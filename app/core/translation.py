"""
Persian (Farsi) translation module.

Storage hierarchy (each layer falls through to the next):
  1. Redis  — hot cache, 24-hour TTL (fast reads)
  2. DB     — fa_title / fa_summary columns in `trends` table (persistent)
  3. Gemini — translation API, result stored in both DB and Redis

DB sessions: translation.py always opens its OWN session for reads and
writes. It never shares the caller's session, so a failed write can
never poison the caller's transaction.

Invalidation: call `clear_fa_cache(trend_id, redis_client)` AND set
trend.fa_title = None / trend.fa_summary = None before DB commit.
The next read will re-translate via Gemini.

Token usage is appended to ai_monitor_data.csv (same schema as summarizer).
"""

import csv
import logging
import os
import time
from datetime import datetime

from app.core.quota_guard import gemini_quota

logger = logging.getLogger(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# ── Model selection (mirrors x_ai_service.py pattern) ────────────────────────
_FALLBACK_MODEL = "gemini-2.5-flash-lite"
_translation_model: str | None = None   # set once on first use
_gemini_client = None

# ── Pricing (kept in sync with summarizer.py) ────────────────────────────────
_MODEL_PRICING: dict = {
    "gemini-2.5-flash-lite": (0.10 / 1_000_000, 0.40 / 1_000_000),
    "gemini-2.5-flash":      (0.15 / 1_000_000, 0.60 / 1_000_000),
    "gemini-2.0-flash-lite": (0.075 / 1_000_000, 0.30 / 1_000_000),
    "gemini-1.5-flash":      (0.075 / 1_000_000, 0.30 / 1_000_000),
}
_DEFAULT_PRICING = (0.10 / 1_000_000, 0.40 / 1_000_000)
_LOG_FILE = "ai_monitor_data.csv"


def _get_model_pricing(model_name: str):
    name_lower = (model_name or "").lower()
    for key, pricing in _MODEL_PRICING.items():
        if key in name_lower:
            return pricing
    return _DEFAULT_PRICING


def _log_usage(trend_id, model: str, in_tok: int, out_tok: int,
               duration: float, call_type: str, status: str) -> None:
    try:
        in_price, out_price = _get_model_pricing(model)
        cost = (in_tok * in_price) + (out_tok * out_price)
        needs_header = not os.path.exists(_LOG_FILE) or os.path.getsize(_LOG_FILE) == 0
        with open(_LOG_FILE, mode="a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if needs_header:
                w.writerow(["timestamp", "trend_id", "model", "input_tokens",
                             "output_tokens", "duration_sec", "category", "status", "cost_usd"])
            w.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                trend_id, model, in_tok, out_tok,
                f"{duration:.2f}", call_type, status, f"{cost:.8f}",
            ])
    except Exception as exc:
        logger.error(f"[translation] log write failed: {exc}")


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None and GOOGLE_API_KEY:
        try:
            from google import genai
            _gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
        except Exception as e:
            logger.error(f"[translation] Gemini init failed: {e}")
    return _gemini_client


def _get_model() -> str:
    """Pick the best available Gemini flash-lite model (cached after first call)."""
    global _translation_model
    if _translation_model:
        return _translation_model

    client = _get_gemini_client()
    if not client:
        _translation_model = _FALLBACK_MODEL
        return _translation_model

    try:
        candidates = []
        for m in client.models.list():
            name = m.name.replace("models/", "")
            nl = name.lower()
            if ("flash" in nl and "lite" in nl
                    and "image" not in nl and "audio" not in nl
                    and "embedding" not in nl):
                candidates.append(name)

        # prefer 2.5-flash-lite, then 2.0-flash-lite, then anything lite
        for priority in ["2.5-flash-lite", "2.0-flash-lite", "flash-lite"]:
            for c in candidates:
                if priority in c.lower():
                    _translation_model = c
                    logger.info(f"[translation] Using model: {c}")
                    return _translation_model

        _translation_model = candidates[0] if candidates else _FALLBACK_MODEL
    except Exception as e:
        logger.warning(f"[translation] Model probe failed, using fallback: {e}")
        _translation_model = _FALLBACK_MODEL

    return _translation_model


def _build_prompt(texts: list[str]) -> str:
    """Build the translation prompt with formal news-agency tone and logic rules."""
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    return (
        "Sen bir haber ajansının Türkçe'den Farsça'ya çeviri editörüsün.\n"
        "Aşağıdaki Türkçe haber metinlerini akıcı, doğal Farsça haber diline çevir.\n\n"
        "KURALLAR:\n"
        "1. Her satırı sırayla çevir — sadece çeviriyi yaz (numara ve nokta dahil).\n"
        "2. Farsça bir gazetecinin yazacağı gibi yaz: doğal, akıcı ve tarafsız. "
        "Kelimesi kelimesine çeviri YAPMA — cümleyi Farsça'da doğal duyulacak "
        "şekilde yeniden kur. Çeviri kokan kalıplardan kaçın.\n"
        "3. Virgülle ayrılmış sayısal listeleri doğal Farsça cümleye dönüştür.\n"
        "   Örnek: '1 polis, 5 kişi yaralı' → '۱ پلیس و ۵ نفر زخمی شدند'\n"
        "   Örnek: '2 ölü, 3 yaralı' → '۲ نفر کشته و ۳ نفر زخمی شدند'\n"
        "4. Cümle mantığını kontrol et; çeviri dilbilgisel olarak tam ve anlamlı olmalı.\n"
        "5. Türkçe özel isimleri (şehir, kişi, kurum adları) doğal Farsça telaffuzuyla yaz.\n"
        "6. ZORUNLU: Tüm Markdown biçimlendirmesini (###, ##, *, **, emojiler) "
        "AYNEN koru — sadece metin içeriğini çevir.\n"
        "   Örnek: '### ⚡ Özet' → '### ⚡ خلاصه'  (### ve ⚡ olduğu gibi kalır)\n"
        "   Örnek: '* Madde' → '* محتوای ترجمه شده'  (* kaldırılmaz)\n"
        "7. Hiçbir açıklama, not veya ek bilgi ekleme.\n\n"
        f"{numbered}"
    )


def _parse_response(raw: str, texts: list[str]) -> list[str]:
    """
    Parse a numbered Gemini response into a list matching len(texts).

    Each numbered item (1. ... 2. ...) may span multiple lines — we group
    all continuation lines back into the same item. This is critical for
    multi-line markdown summaries where only grouping by number is correct.
    """
    results: list[str] = [""] * len(texts)
    current_idx: int = -1
    current_lines: list[str] = []

    def _flush():
        if current_idx >= 0 and current_idx < len(texts):
            results[current_idx] = "\n".join(current_lines).strip()

    for raw_line in raw.split("\n"):
        stripped = raw_line.strip()
        # Detect a new numbered item: "1. ", "2. ", up to two-digit numbers
        matched = False
        if stripped and stripped[0].isdigit():
            dot = stripped.find(".")
            if 0 < dot <= 3:
                try:
                    num = int(stripped[:dot])
                    if 1 <= num <= len(texts):
                        _flush()
                        current_idx = num - 1
                        current_lines = [stripped[dot + 1:].lstrip()]
                        matched = True
                except ValueError:
                    pass
        if not matched:
            if current_idx >= 0:
                current_lines.append(raw_line.rstrip())

    _flush()

    # Fallback: if only one text and parser found nothing, use full response
    if len(texts) == 1 and not results[0]:
        results[0] = raw.strip()

    # Fill any unparsed slots with originals
    for i, r in enumerate(results):
        if not r:
            results[i] = texts[i]

    return results


def _call_gemini(texts: list[str], trend_ids: list[int],
                 call_type: str) -> list[str] | None:
    """
    Translate a numbered list of Turkish texts to Persian via Gemini.
    Returns list of translated strings on success, None on failure.
    Callers must check for None and must NOT cache the result when None.
    """
    client = _get_gemini_client()
    if not client:
        return None

    # Shared quota breaker. The FA sweep was the heaviest Gemini consumer on the
    # account (81% of logged calls in a day) and kept firing into an exhausted
    # quota, starving the summarizer of the requests it needed.
    if gemini_quota.is_open():
        logger.info(
            f"[translation] Gemini quota cooling down "
            f"({gemini_quota.seconds_remaining()}s left) — skipping {call_type}."
        )
        return None

    model = _get_model()
    prompt = _build_prompt(texts)

    log_id = ",".join(str(i) for i in trend_ids) if trend_ids else "batch"
    t0 = time.time()

    try:
        response = client.models.generate_content(model=model, contents=prompt)
        duration = time.time() - t0
        meta = response.usage_metadata
        in_tok  = (meta.prompt_token_count     or 0) if meta else 0
        out_tok = (meta.candidates_token_count or 0) if meta else 0
        _log_usage(log_id, model, in_tok, out_tok, duration, call_type, "Success")
        gemini_quota.record_success()

        return _parse_response(response.text, texts)

    except Exception as e:
        duration = time.time() - t0
        gemini_quota.record_failure(e)
        logger.error(f"[translation] Gemini call failed ({model}): {e}")
        _log_usage(log_id, model, 0, 0, duration, call_type,
                   f"Error: {type(e).__name__}")
        return None  # ← never cache this; caller falls back to original


# ── DB helpers (own session — never share with caller) ───────────────────────

def _db_get_fa_title(trend_id: int) -> str | None:
    try:
        from app.database.models import SessionLocal, Trend
        with SessionLocal() as s:
            row = s.query(Trend.fa_title).filter(
                Trend.id == trend_id, Trend.fa_title.isnot(None)
            ).first()
            return row.fa_title if row and row.fa_title else None
    except Exception as e:
        logger.warning(f"[translation] DB fa_title read failed: {e}")
        return None


def _db_get_fa_summary(trend_id: int) -> str | None:
    try:
        from app.database.models import SessionLocal, Trend
        with SessionLocal() as s:
            row = s.query(Trend.fa_summary).filter(
                Trend.id == trend_id, Trend.fa_summary.isnot(None)
            ).first()
            return row.fa_summary if row and row.fa_summary else None
    except Exception as e:
        logger.warning(f"[translation] DB fa_summary read failed: {e}")
        return None


def _db_save_fa_title(trend_id: int, fa_title: str) -> None:
    try:
        from app.database.models import SessionLocal, Trend
        with SessionLocal() as s:
            s.query(Trend).filter(Trend.id == trend_id).update(
                {"fa_title": fa_title}, synchronize_session=False
            )
            s.commit()
    except Exception as e:
        logger.error(f"[translation] DB fa_title save failed for {trend_id}: {e}")


def _db_save_fa_summary(trend_id: int, fa_summary: str) -> None:
    try:
        from app.database.models import SessionLocal, Trend
        with SessionLocal() as s:
            s.query(Trend).filter(Trend.id == trend_id).update(
                {"fa_summary": fa_summary}, synchronize_session=False
            )
            s.commit()
    except Exception as e:
        logger.error(f"[translation] DB fa_summary save failed for {trend_id}: {e}")


def _db_batch_get_fa_titles(ids: list[int]) -> dict[int, str]:
    try:
        from app.database.models import SessionLocal, Trend
        with SessionLocal() as s:
            rows = s.query(Trend.id, Trend.fa_title).filter(
                Trend.id.in_(ids), Trend.fa_title.isnot(None)
            ).all()
            return {r.id: r.fa_title for r in rows if r.fa_title}
    except Exception as e:
        logger.warning(f"[translation] DB batch fa_title read failed: {e}")
        return {}


def _db_batch_save_fa_titles(updates: list[tuple[int, str]]) -> None:
    if not updates:
        return
    try:
        from app.database.models import SessionLocal, Trend
        with SessionLocal() as s:
            for tid, fa_title in updates:
                s.query(Trend).filter(Trend.id == tid).update(
                    {"fa_title": fa_title}, synchronize_session=False
                )
            s.commit()
    except Exception as e:
        logger.error(f"[translation] DB batch fa_title save failed: {e}")


def _redis_key_title(trend_id: int) -> str:
    return f"fa:title:{trend_id}"


def _redis_key_summary(trend_id: int) -> str:
    return f"fa:summary:{trend_id}"


# ── Summarizer integration ────────────────────────────────────────────────────

def translate_for_summarizer(trend_id: int, title: str, summary: str,
                              redis_client) -> tuple[str, str]:
    """
    Called by summarizer.py immediately after generating new Turkish content.
    Translates title + summary in ONE Gemini call for efficiency.

    Returns (fa_title, fa_summary).
    - On success: saves to Redis; caller sets trend.fa_title/fa_summary on ORM.
    - On failure: returns ("", "") so caller sets NULL; sweep cycle will retry.
    DB persistence is the caller's responsibility (via ORM commit).
    """
    texts = []
    if title:
        texts.append(title)
    if summary:
        texts.append(summary)

    if not texts:
        return "", ""

    call_type = "FA-Auto-Title" if not summary else "FA-Auto"
    result = _call_gemini(texts, [trend_id], call_type)

    fa_title, fa_summary = "", ""

    if result:
        idx = 0
        if title and idx < len(result):
            t = result[idx]
            if _has_persian(t):
                fa_title = t
                if redis_client:
                    redis_client.setex(_redis_key_title(trend_id), 86400, fa_title)
            idx += 1
        if summary and idx < len(result):
            s = result[idx]
            if _has_persian(s):
                fa_summary = s
                if redis_client:
                    redis_client.setex(_redis_key_summary(trend_id), 86400, fa_summary)

    return fa_title, fa_summary


def sweep_untranslated(redis_client, batch_size: int = 8) -> int:
    """
    Scan active trends with missing translations and translate them.
    Intended to be called periodically (e.g., every 30 min in gravity_worker).
    Returns the number of trends translated in this run.
    """
    try:
        from app.database.models import SessionLocal, Trend
        from sqlalchemy import or_, and_
    except ImportError as e:
        logger.error(f"[translation] sweep import error: {e}")
        return 0

    db = SessionLocal()
    try:
        trends = (
            db.query(Trend.id, Trend.title, Trend.summary, Trend.fa_title, Trend.fa_summary)
            .filter(
                Trend.is_active == True,
                Trend.summary.isnot(None),  # only translate if Turkish summary exists
                or_(Trend.fa_title.is_(None), Trend.fa_summary.is_(None))
            )
            .order_by(Trend.final_tps.desc())   # highest TPS first
            .limit(batch_size)
            .all()
        )
    finally:
        db.close()

    if not trends:
        return 0

    translated = 0
    for row in trends:
        # Build texts to translate (only missing ones)
        texts = []
        need_title   = not row.fa_title   and row.title
        need_summary = not row.fa_summary and row.summary
        if need_title:
            texts.append(row.title)
        if need_summary:
            texts.append(row.summary)
        if not texts:
            continue

        call_type = "FA-Sweep"
        result = _call_gemini(texts, [row.id], call_type)
        if result is None:
            continue

        fa_title   = row.fa_title   or ""
        fa_summary = row.fa_summary or ""
        idx = 0
        if need_title and idx < len(result):
            t = result[idx]
            if _has_persian(t):
                fa_title = t
            idx += 1
        if need_summary and idx < len(result):
            s = result[idx]
            if _has_persian(s):
                fa_summary = s

        # Persist to DB and Redis
        db2 = SessionLocal()
        try:
            db2.query(Trend).filter(Trend.id == row.id).update(
                {"fa_title": fa_title or None, "fa_summary": fa_summary or None},
                synchronize_session=False
            )
            db2.commit()
        except Exception as e:
            db2.rollback()
            logger.error(f"[translation] sweep DB save failed for {row.id}: {e}")
        finally:
            db2.close()

        if fa_title and redis_client:
            redis_client.setex(_redis_key_title(row.id), 86400, fa_title)
        if fa_summary and redis_client:
            redis_client.setex(_redis_key_summary(row.id), 86400, fa_summary)

        translated += 1
        logger.info(f"[translation] sweep translated trend {row.id}")

    return translated


# ── Public invalidation ───────────────────────────────────────────────────────

def clear_fa_cache(trend_id: int, redis_client) -> None:
    """Remove Redis translation cache for a trend. Call when title/summary changes."""
    if redis_client:
        try:
            redis_client.delete(_redis_key_title(trend_id))
            redis_client.delete(_redis_key_summary(trend_id))
        except Exception as e:
            logger.warning(f"[translation] Redis clear failed for {trend_id}: {e}")


# ── Public translation API ────────────────────────────────────────────────────

def _has_persian(text: str) -> bool:
    """Return True if text contains Persian/Arabic Unicode characters (U+0600–U+06FF).
    Turkish uses Latin script; any real Persian translation will contain these chars."""
    return any('؀' <= c <= 'ۿ' for c in (text or ""))


def _is_untranslated(cached: str, original: str) -> bool:  # noqa: ARG001
    """Return True if cached value is NOT a real Persian translation.
    Checks for Persian script presence — reliable because Turkish is Latin-based."""
    return not _has_persian(cached)


def translate_title(trend_id: int, title: str, redis_client, db=None) -> str:
    """
    Translate a single title Turkish→Persian.
    Lookup: Redis → DB → Gemini. Result saved to Redis + DB.
    Stale Redis entries that equal the original are treated as cache misses.
    """
    if not title:
        return ""

    # 1. Redis — skip if it's the unchanged original (stale failed-translation)
    rkey = _redis_key_title(trend_id)
    if redis_client:
        cached = redis_client.get(rkey)
        if cached and not _is_untranslated(cached, title):
            return cached
        if cached:
            redis_client.delete(rkey)  # clear stale entry

    # 2. DB — same stale check
    db_val = _db_get_fa_title(trend_id)
    if db_val and not _is_untranslated(db_val, title):
        if redis_client:
            redis_client.setex(rkey, 86400, db_val)
        return db_val

    # 3. Gemini — only cache on success (result is None on failure)
    result = _call_gemini([title], [trend_id], "FA-Title")
    if result is None:
        return title  # Gemini failed: return original, do NOT cache

    fa_title = result[0] if result[0] else title
    if redis_client:
        redis_client.setex(rkey, 86400, fa_title)
    _db_save_fa_title(trend_id, fa_title)
    return fa_title


def translate_summary(trend_id: int, summary: str, redis_client, db=None) -> str:
    """
    Translate a single summary Turkish→Persian.
    Lookup: Redis → DB → Gemini. Result saved to Redis + DB.
    Stale Redis entries that equal the original are treated as cache misses.
    """
    if not summary:
        return ""

    # 1. Redis — skip stale (original stored by old failed attempt)
    rkey = _redis_key_summary(trend_id)
    if redis_client:
        cached = redis_client.get(rkey)
        if cached and not _is_untranslated(cached, summary):
            return cached
        if cached:
            redis_client.delete(rkey)  # clear stale entry

    # 2. DB — same stale check
    db_val = _db_get_fa_summary(trend_id)
    if db_val and not _is_untranslated(db_val, summary):
        if redis_client:
            redis_client.setex(rkey, 86400, db_val)
        return db_val

    # 3. Gemini — only cache on success (result is None on failure)
    result = _call_gemini([summary], [trend_id], "FA-Summary")
    if result is None:
        return summary  # Gemini failed: return original, do NOT cache

    fa_summary = result[0] if result[0] else summary
    if redis_client:
        redis_client.setex(rkey, 86400, fa_summary)
    _db_save_fa_summary(trend_id, fa_summary)
    return fa_summary


def translate_titles(trends: list[dict], redis_client, db=None) -> dict[int, str]:
    """
    Batch-translate trend titles Turkish→Persian.
    Returns {trend_id: fa_title}.
    Lookup: Redis → DB → (single batch) Gemini.
    db parameter accepted but ignored (translation uses its own session).
    """
    result: dict[int, str] = {}
    need_db:     list[dict] = []
    need_gemini: list[dict] = []

    # 1. Redis pass — skip stale entries (original stored by old failed attempt)
    for t in trends:
        tid = t["id"]
        title = t.get("title") or ""
        if not title:
            result[tid] = ""
            continue
        if redis_client:
            cached = redis_client.get(_redis_key_title(tid))
            if cached and not _is_untranslated(cached, title):
                result[tid] = cached
                continue
            if cached:
                redis_client.delete(_redis_key_title(tid))  # clear stale
        need_db.append({"id": tid, "title": title})

    if not need_db:
        return result

    # 2. DB pass (batch query) — skip stale entries too
    db_map = _db_batch_get_fa_titles([m["id"] for m in need_db])
    for item in need_db:
        tid = item["id"]
        db_val = db_map.get(tid)
        if db_val and not _is_untranslated(db_val, item["title"]):
            result[tid] = db_val
            if redis_client:
                redis_client.setex(_redis_key_title(tid), 86400, db_val)
        else:
            need_gemini.append(item)

    if not need_gemini:
        return result

    # 3. Gemini pass (one call for all missing)
    raw_titles = [m["title"] for m in need_gemini]
    ids = [m["id"] for m in need_gemini]
    translated = _call_gemini(raw_titles, ids, "FA-Batch")

    if translated is None:
        # Gemini failed — return originals, do NOT cache anything
        for item in need_gemini:
            result[item["id"]] = item["title"]
        return result

    db_updates: list[tuple[int, str]] = []
    for item, fa_title in zip(need_gemini, translated):
        tid = item["id"]
        fa_title = fa_title if fa_title else item["title"]
        result[tid] = fa_title
        if redis_client:
            redis_client.setex(_redis_key_title(tid), 86400, fa_title)
        db_updates.append((tid, fa_title))

    _db_batch_save_fa_titles(db_updates)
    return result
