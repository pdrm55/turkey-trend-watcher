import math
import logging
import json
import time as _time
from datetime import datetime, timezone, timedelta
from app.database.models import Trend, RawNews, TrendArrivals, TrendScoreHistory
from app.core.ai_engine import ai_engine
from app.core.text_utils import normalize_turkish, JUNK_KEYWORDS
from app.config import Config
from app.core.http_resilience import request_with_retry
from app.core.observability import traced_span, emit_metric

# Fix 1: per-trend Ollama result cache {trend_id: (e, s, is_opinion, expires_at)}
_LLM_CACHE: dict = {}
_LLM_CACHE_TTL = 900       # 15 minutes
_LLM_CACHE_MAX_SIZE = 500  # Fix 5: evict expired entries when cache hits this size

# Fix 6: precomputed tier sets — O(1) lookup instead of O(n) Config-list iteration
_TIER1_SET = frozenset(s.lower() for s in Config.SOURCE_CONFIG["TIER_1_OFFICIAL"])
_TIER2_SET = frozenset(s.lower() for s in Config.SOURCE_CONFIG["TIER_2_REPUTABLE"])

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OLLAMA_API_URL = Config.OLLAMA_API_URL
LOCAL_MODEL_NAME = Config.LOCAL_MODEL_NAME


def get_source_tier(source_name: str) -> int:
    """
    تعیین سطح اعتبار منبع بر اساس نام آن.
    Fix 6: از frozenset‌های پیش‌محاسبه‌شده در زمان بارگذاری ماژول استفاده می‌کند — O(1).
    """
    if not source_name:
        return 3
    name = source_name.strip().lower()
    if any(s in name for s in _TIER1_SET):
        return 1
    if any(s in name for s in _TIER2_SET):
        return 2
    return 3


# --- لیست کلمات کلیدی بحرانی برای تقویت آنی امتیاز (Strategic Boost) ---
CRITICAL_KEYWORDS = {
    "high":        ["deprem", "patlama", "istifa", "suikast", "darbe", "saldırı",
                    "acil durum", "infaz", "terör", "faci", "şehit"],
    "sports_gold": ["tarihi zafer", "tarihi galibiyet", "rekor", "şampiyon",
                    "mağlup etti", "tur atladı"],
    "medium":      ["faiz kararı", "seçim", "gözaltı", "operasyon", "flaş haber",
                    "son dakika", "kararname", "çeyrek final", "yarı final", "final"],
}


class TPSCalculator:
    """
    موتور محاسباتی TPS 2.2 (بهینه‌سازی کوئری — فاز ۶.۳)
    تعداد کوئری در هر سیکل: از ۱۱ به ۵ کاهش یافت.
    داده‌های مشترک (RawNews، TrendArrivals، Trend) یکبار در run_tps_cycle فچ شده
    و به متدهای داخلی پاس داده می‌شوند.
    """

    def __init__(self, db_session):
        self.db = db_session

    # ── کمکی ────────────────────────────────────────────────────────────────

    def get_criticality_boost(self, text: str) -> float:
        if not text:
            return 1.0
        text_norm = normalize_turkish(text.lower())
        if any(word in text_norm for word in CRITICAL_KEYWORDS["high"]):
            return 1.6
        if any(word in text_norm for word in CRITICAL_KEYWORDS["sports_gold"]):
            return 1.6
        if any(word in text_norm for word in CRITICAL_KEYWORDS["medium"]):
            return 1.25
        return 1.0

    def determine_trajectory(self, current_tps: float, previous_tps: float) -> str:
        if previous_tps <= 0:
            return "up"
        change_ratio = (current_tps - previous_tps) / previous_tps
        if change_ratio > 0.06:
            return "up"
        if change_ratio < -0.06:
            return "down"
        return "steady"

    # ── سیگنال‌های خام ──────────────────────────────────────────────────────

    def calculate_velocity(
        self,
        trend_id: int,
        arrivals: list,
        trend: Trend,
        has_editorial: bool = False,
    ) -> float:
        """
        محاسبه سرعت انتشار (V) — وزن ۳۵٪ در فرمول TPS.

        Fix 1+2: arrivals و trend از run_tps_cycle پاس داده می‌شوند؛
        هیچ کوئری DB داخلی وجود ندارد.
        arrivals در ترتیب DESC (جدیدترین اول) هستند.
        Fix 7: Batching Guard با max() نرم — بدون جهش ناگهانی ۲→۵.
        Fix 6: entities با isinstance چک می‌شود تا از TypeError جلوگیری شود.
        """
        if not arrivals:
            return 0.0

        if has_editorial:
            return 100.0

        source_count = len(arrivals)

        # Cross-Validation Velocity Shock
        entities = trend.entities if trend and isinstance(trend.entities, dict) else {}
        is_cross_validated = entities.get("cross_validated", False)

        if source_count <= 1:
            return 42.0 if is_cross_validated else 15.0

        # arrivals[0]=newest, arrivals[-1]=oldest
        first_time = arrivals[-1].timestamp
        last_time  = arrivals[0].timestamp
        raw_duration = (last_time - first_time).total_seconds() / 60.0

        # Fix 7: smooth clamp — نه jump سخت از <2 به 5
        if raw_duration < 2.0:
            duration_mins = 5.0
        else:
            duration_mins = max(1.0, raw_duration)

        if trend and trend.category == "Spor":
            duration_mins *= 0.6

        velocity = 35 * math.log2(1 + (source_count / duration_mins))

        if source_count > 20:
            velocity += 40
        elif source_count > 10:
            velocity += 20

        return min(100.0, velocity)

    def calculate_acceleration(self, arrivals: list) -> str:
        """
        تشخیص شتاب انفجاری (Acceleration).

        Fix 2: arrivals از run_tps_cycle پاس داده می‌شود — بدون کوئری مجدد.
        arrivals در ترتیب DESC هستند؛ [0]=جدیدترین.
        """
        if len(arrivals) < 5:
            return "steady"

        recent_gap = (arrivals[0].timestamp - arrivals[2].timestamp).total_seconds()
        avg_gap    = (arrivals[0].timestamp - arrivals[-1].timestamp).total_seconds() / len(arrivals)

        if recent_gap < (avg_gap * 0.4):
            return "up"
        return "steady"

    def analyze_semantic_and_entity(self, text: str, trend_id: int = None):
        """
        استخراج امتیاز نهاد (E) و حساسیت (S) با Qwen محلی.

        Fix 1: کش ۱۵ دقیقه‌ای به ازای هر trend_id.
        Fix 5: قبل از نوشتن entry جدید، entryهای منقضی‌شده پاکسازی می‌شوند
               تا cache از _LLM_CACHE_MAX_SIZE تجاوز نکند.
        """
        if trend_id is not None:
            cached = _LLM_CACHE.get(trend_id)
            if cached and _time.monotonic() < cached[3]:
                return cached[0], cached[1], cached[2]

        prompt = f"""
        Analyze this Turkish news for Trend Potential Score (TPS).
        Text: "{text[:800]}"

        Task:
        1. Entity Impact (E): Is this about National leaders, Regional figures, or Unknowns? (Range: 20-100)
        2. Semantic Criticality (S): Is this High (Major/Dangerous), Medium, or Low? (Range: 20-100)
        3. Opinion Check: Is this a personal commentary/blog post or objective news?

        Return JSON ONLY:
        {{"entity_score": 20-100, "criticality_score": 20-100, "is_opinion": true/false}}
        """
        payload = {
            "model": LOCAL_MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0, "num_ctx": 2048},
        }
        try:
            with traced_span("scoring.llm_call", model=LOCAL_MODEL_NAME):
                result = ai_engine._ollama_post(payload, timeout=12)
            if result is None:
                # Never attempted — lock contention or an open circuit. E and S are
                # 25% of the signal each, and the old code returned 30/30 here, which
                # run_tps_cycle then persisted as a genuine score. Under contention
                # the pipeline was writing invented numbers to the database. Prefer a
                # stale cached reading; failing that, say so and let the caller defer.
                stale = _LLM_CACHE.get(trend_id) if trend_id is not None else None
                if stale:
                    emit_metric("scoring.llm.contention_stale", 1, model=LOCAL_MODEL_NAME)
                    return stale[0], stale[1], stale[2]
                emit_metric("scoring.llm.contention_defer", 1, model=LOCAL_MODEL_NAME)
                return None, None, False
            if not result:
                emit_metric("scoring.llm.failure", 1, model=LOCAL_MODEL_NAME)
                return 30, 30, False
            result_data = json.loads(result.get("response", "{}"))
            e_score = result_data.get("entity_score", 30)
            s_score = result_data.get("criticality_score", 30)
            opinion = result_data.get("is_opinion", False)
            emit_metric("scoring.llm.success", 1, model=LOCAL_MODEL_NAME)

            if trend_id is not None:
                # Fix 5: پاکسازی entryهای منقضی اگر cache پر شده
                if len(_LLM_CACHE) >= _LLM_CACHE_MAX_SIZE:
                    now = _time.monotonic()
                    expired_keys = [k for k, v in _LLM_CACHE.items() if now >= v[3]]
                    for k in expired_keys:
                        del _LLM_CACHE[k]
                _LLM_CACHE[trend_id] = (e_score, s_score, opinion, _time.monotonic() + _LLM_CACHE_TTL)

            return e_score, s_score, opinion
        except Exception as e:
            logger.error(f"⚠️ Local LLM Scoring Error: {e}")
            emit_metric("scoring.llm.failure", 1, model=LOCAL_MODEL_NAME)
            return 30, 30, False

    def calculate_novelty(
        self, text: str, message_count: int = 1, cluster_id: str = None
    ) -> float:
        """
        محاسبه امتیاز تازگی (N) — وزن ۱۵٪ در فرمول TPS.

        Fix 3: اگر ترند قبلاً کلاستر شده (message_count > 1)، ChromaDB پرسیده نمی‌شود.
        ترند کلاستر‌شده به‌تعریف novel نیست — مقدار ثابت ۲۰.۰ برگردانده می‌شود.
        این بهینه‌سازی ~۱ ChromaDB call در >۸۰٪ سیکل‌ها حذف می‌کند.

        `cluster_id` excludes the trend's own vectors from the search. Without it
        this signal was structurally dead: process_news() writes the document to
        Chroma (ai_engine.py:469) *before* the Trend row exists, so the nearest
        neighbour was always the trend's own just-inserted vector. Measured on
        live data: unfiltered similarity 0.9534 (> 0.88 → N=0.0) versus 0.8484
        against other clusters (→ N=15.2). Every trend scored 0 on 15% of TPS.
        """
        if message_count > 1:
            return 20.0

        try:
            vector  = ai_engine.get_embedding(text, is_query=True)
            query_args = {
                "query_embeddings": [vector],
                "n_results": 1,
                "include": ["distances"],
            }
            if cluster_id:
                query_args["where"] = {"cluster_id": {"$ne": cluster_id}}
            results = ai_engine.collection.query(**query_args)
            if not results["distances"] or not results["distances"][0]:
                return 100.0
            max_similarity = 1.0 - results["distances"][0][0]
            if max_similarity > 0.88:
                return 0.0
            return 100 * (1.0 - max_similarity)
        except Exception as e:
            logger.error(f"Novelty Calculation Error: {e}")
            return 50.0

    def get_confidence_score(
        self, news_items: list, has_editorial: bool
    ) -> float:
        """
        محاسبه ضریب اطمینان — ترکیب اعتبار منبع (Tier) و تنوع خبرگزاری‌ها.

        Fix 1+2: news_items و has_editorial از run_tps_cycle پاس داده می‌شوند؛
        هیچ کوئری DB داخلی وجود ندارد.
        """
        if not news_items:
            return 0.5

        if has_editorial:
            return 1.0

        best_tier       = min(n.source_tier for n in news_items)
        tier_weights    = Config.SOURCE_CONFIG["WEIGHTS"]
        base_confidence = tier_weights.get(best_tier, 0.75)

        unique_source_names = {n.source_name for n in news_items}
        source_count = len(unique_source_names)

        if source_count == 1:
            diversity_multiplier = 0.75
        elif source_count == 2:
            diversity_multiplier = 0.85
        else:
            diversity_multiplier = 1.0

        final_conf = base_confidence * diversity_multiplier

        if source_count == 1:
            final_conf = min(0.75, final_conf)

        if all(n.source_type == "x" for n in news_items):
            final_conf = min(0.4, final_conf)

        return min(1.0, final_conf)

    # ── چرخه اصلی ──────────────────────────────────────────────────────────

    def run_tps_cycle(self, trend_id: int):
        """
        اجرای چرخه کامل امتیازدهی TPS 2.2.

        بهینه‌سازی کوئری — کوئری‌های DB در این متد:
          1. Trend        (1 کوئری)
          2. RawNews all  (1 کوئری — جایگزین ۴ کوئری جداگانه قبلی)
          3. TrendArrivals limit 50  (1 کوئری — مشترک بین velocity و acceleration)
          4. ChromaDB get_cluster_reference_doc  (فقط در صورت نیاز)
          5. TrendScoreHistory last record  (1 کوئری)
        مجموع: ۵ کوئری (قبلاً ۱۱ کوئری + ۲ ChromaDB)
        """
        trend = self.db.query(Trend).filter(Trend.id == trend_id).first()
        if not trend:
            return None

        # ── کوئری ۲: تمام RawNews یکجا ─────────────────────────────────────
        # جایگزین ۴ کوئری جداگانه: ref_doc (limit 3)، editorial check ×۲، x-signal+unique
        news_items    = (
            self.db.query(RawNews)
            .filter(RawNews.trend_id == trend_id)
            .order_by(RawNews.published_at.desc())
            .all()
        )
        has_editorial = any(n.source_type == "editorial" for n in news_items)
        has_x_signal  = any(n.source_type == "x"         for n in news_items)
        unique_sources = {n.source_name for n in news_items}

        # ref_doc: بهترین خبر از news_items — بدون کوئری جداگانه
        ref_doc = None
        if trend.message_count > 5 and news_items:
            ref_doc = max(news_items[:3], key=lambda x: len(x.content or "")).content or None
        if not ref_doc:
            ref_result = ai_engine.get_cluster_reference_doc(trend.cluster_id)
            ref_doc = ref_result[0] if isinstance(ref_result, tuple) else ref_result
        if not ref_doc and news_items:
            ref_doc = next((n.content for n in reversed(news_items) if n.content), None)
        if not ref_doc:
            return None

        # ── کوئری ۳: TrendArrivals یکجا (مشترک بین velocity و acceleration) ─
        arrivals = (
            self.db.query(TrendArrivals)
            .filter(TrendArrivals.trend_id == trend_id)
            .order_by(TrendArrivals.timestamp.desc())
            .limit(50)
            .all()
        )

        # ── ۱. سیگنال‌های خام ───────────────────────────────────────────────
        v     = self.calculate_velocity(trend_id, arrivals, trend, has_editorial)
        accel = self.calculate_acceleration(arrivals)
        e, s, is_opinion = self.analyze_semantic_and_entity(ref_doc, trend_id=trend_id)
        if e is None:
            # Ollama was busy and no cached reading exists. Returning None leaves
            # needs_scoring set (gravity_worker.py:181) so the trend is retried
            # instead of being stamped with a made-up score.
            logger.warning(f"⏸️ Trend {trend_id}: E/S unavailable (Ollama busy) — deferring scoring")
            return None
        # Fix 3: برای ترند کلاستر‌شده ChromaDB پرسیده نمی‌شود
        n = self.calculate_novelty(
            ref_doc, message_count=trend.message_count, cluster_id=trend.cluster_id
        )

        # ── ۲. ضریب تقویت استراتژیک ─────────────────────────────────────────
        c_boost      = self.get_criticality_boost(ref_doc)
        signal_score = ((0.35 * v) + (0.25 * e) + (0.25 * s) + (0.15 * n)) * c_boost

        # Fix 3: Social Pulse Synergy — سقف boost ترکیبی ×۲.۰
        if has_x_signal:
            trend.has_social_signal = True
            x_boost = min(1.5, 2.0 / max(c_boost, 1.0))
            signal_score *= x_boost
        else:
            trend.has_social_signal = False

        # ── ۳. ضریب اطمینان — news_items پاس می‌شود (بدون کوئری مجدد) ─────
        confidence = self.get_confidence_score(news_items=news_items, has_editorial=has_editorial)

        # ── ۴. TPS نهایی + فیلترهای ایمنی ──────────────────────────────────
        final_tps = min(100.0, signal_score * confidence)

        if len(unique_sources) == 1:
            final_tps *= 0.8

        normalized_title = normalize_turkish(trend.title or ref_doc[:100])
        if any(junk in normalized_title for junk in JUNK_KEYWORDS):
            final_tps = min(12.0, final_tps)

        if is_opinion:
            final_tps *= 0.55

        # ── ۵. Trajectory ────────────────────────────────────────────────────
        trend.trajectory = accel if accel == "up" else self.determine_trajectory(final_tps, trend.final_tps)

        # ── ۶. ذخیره‌سازی ────────────────────────────────────────────────────
        trend.previous_tps  = trend.final_tps
        trend.tps_signal    = signal_score
        trend.tps_confidence = confidence
        trend.final_tps     = final_tps
        trend.score         = final_tps
        # Fix 2: last_updated را لمس نمی‌کنیم — ingest workers آن را تنظیم می‌کنند

        # ── کوئری ۵: آخرین رکورد تاریخچه (برای تصمیم لاگ) ──────────────────
        last_history = (
            self.db.query(TrendScoreHistory)
            .filter(TrendScoreHistory.trend_id == trend_id)
            .order_by(TrendScoreHistory.timestamp.desc())
            .first()
        )
        should_log = not last_history
        if last_history:
            last_score = last_history.tps_score if last_history.tps_score is not None else 0.0
            age        = datetime.now(timezone.utc).replace(tzinfo=None) - last_history.timestamp
            if abs(final_tps - last_score) >= 1.0 or age > timedelta(minutes=10):
                should_log = True

        if should_log:
            self.db.add(TrendScoreHistory(
                trend_id=trend_id,
                tps_score=final_tps,
                event_type="scoring",
            ))

        try:
            self.db.commit()
            logger.info(f"✅ [Async TPS] Trend {trend_id} Scored: {final_tps:.2f} | Accel: {trend.trajectory}")
            return final_tps
        except Exception as ex:
            self.db.rollback()
            logger.error(f"❌ Error during DB commit in Scoring: {ex}")
            return None
