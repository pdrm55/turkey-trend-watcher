import math
import logging
import json
import requests
import os
from datetime import datetime, timezone, timedelta
from sqlalchemy import func
from app.database.models import Trend, RawNews, TrendArrivals
from app.core.ai_engine import ai_engine
from app.core.text_utils import normalize_turkish, JUNK_KEYWORDS
from app.core.alert_service import alert_service
from app.config import Config

# تنظیمات لاگر برای ردیابی دقیق فرآیند امتیازدهی
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# تنظیمات اتصال به مدل محلی Ollama (Qwen 2.5) از کانفیگ خوانده می‌شود
OLLAMA_API_URL = Config.OLLAMA_API_URL
LOCAL_MODEL_NAME = Config.LOCAL_MODEL_NAME

def get_source_tier(source_name: str) -> int:
    """
    تعیین سطح اعتبار منبع بر اساس نام آن (بروزرسانی شده برای فاز ۶).
    اکنون لیست منابع از فایل Config خوانده می‌شود تا مدیریت آن داینامیک باشد.
    """
    if not source_name: return 3
    name = source_name.strip()
    
    # چک کردن منابع لایه ۱
    if any(s.lower() in name.lower() for s in Config.SOURCE_CONFIG["TIER_1_OFFICIAL"]): 
        return 1
    # چک کردن منابع لایه ۲
    if any(s.lower() in name.lower() for s in Config.SOURCE_CONFIG["TIER_2_REPUTABLE"]): 
        return 2
        
    return 3

# --- لیست کلمات کلیدی بحرانی برای تقویت آنی امتیاز (Strategic Boost) ---
CRITICAL_KEYWORDS = {
    "high": ["deprem", "patlama", "istifa", "suikast", "darbe", "saldırı", "acil durum", "infaz", "terör", "faci", "şehit"],
    "medium": ["faiz kararı", "seçim", "gözaltı", "operasyon", "flaş haber", "son dakika", "kararname"]
}

class TPSCalculator:
    """
    موتور محاسباتی TPS 2.1 (نسخه جامع فاز ۶.۲ - آسنکرون)
    این کلاس مسئولیت ترکیب سیگنال‌های سرعت، شتاب، معنایی، تازگی و اعتبار منبع را بر عهده دارد.
    """
    def __init__(self, db_session):
        self.db = db_session

    def get_criticality_boost(self, text: str) -> float:
        """
        تحلیل متن برای شناسایی کلمات کلیدی بحرانی.
        اگر خبر حاوی کلمات کلیدی 'بسیار حساس' باشد، امتیاز نهایی تقویت می‌شود.
        """
        if not text: return 1.0
        text_norm = normalize_turkish(text.lower())
        
        if any(word in text_norm for word in CRITICAL_KEYWORDS["high"]):
            return 1.6  # ۶۰ درصد تقویت برای اخبار حیاتی (زلزله، انفجار و غیره)
        elif any(word in text_norm for word in CRITICAL_KEYWORDS["medium"]):
            return 1.25 # ۲۵ درصد تقویت برای اخبار مهم سیاسی/اقتصادی
        return 1.0

    def calculate_velocity(self, trend_id: int) -> float:
        """
        محاسبه سرعت انتشار (Propagation Velocity - V)
        وزن در فرمول: ۳۵٪
        فرمول: 35 * log2(1 + سیگنال بر دقیقه)
        """
        arrivals = self.db.query(TrendArrivals).filter(TrendArrivals.trend_id == trend_id).order_by(TrendArrivals.timestamp).all()
        if not arrivals: return 0.0
        
        source_count = len(arrivals)
        if source_count <= 1: return 15.0 # امتیاز پایه برای اولین حضور
        
        first_time = arrivals[0].timestamp
        last_time = arrivals[-1].timestamp
        
        # محاسبه فاصله زمانی به دقیقه (حداقل ۱ دقیقه لحاظ می‌شود)
        duration_mins = max(1.0, (last_time - first_time).total_seconds() / 60.0)
        
        velocity = 35 * math.log2(1 + (source_count / duration_mins))
        return min(100.0, velocity)

    def calculate_acceleration(self, trend_id: int) -> str:
        """
        تشخیص شتاب انفجاری (Acceleration - فاز ۶)
        مقایسه بازه زمانی ورود ۳ خبر اخیر نسبت به میانگین کل کلاستر.
        """
        # دریافت ۱۵ ورود اخیر برای تحلیل شتاب
        arrivals = self.db.query(TrendArrivals).filter(TrendArrivals.trend_id == trend_id).order_by(TrendArrivals.timestamp.desc()).limit(15).all()
        
        if len(arrivals) < 5: return "steady"
        
        # بازه زمانی بین ۳ خبر آخر (ثانیه)
        recent_gap = (arrivals[0].timestamp - arrivals[2].timestamp).total_seconds()
        
        # میانگین بازه زمانی کل کلاستر موجود در لیست
        total_count = len(arrivals)
        avg_gap = (arrivals[0].timestamp - arrivals[-1].timestamp).total_seconds() / total_count
        
        # اگر بازه اخیر کمتر از ۴۰٪ میانگین باشد، یعنی خبر با شتاب بالایی در حال پخش است
        if recent_gap < (avg_gap * 0.4):
            return "up" # وضعیت صعودی شدید (Explosive)
        return "steady"

    def analyze_semantic_and_entity(self, text: str):
        """
        استخراج امتیاز نهاد (E) و حساسیت (S) با استفاده از مدل محلی Qwen.
        E: تاثیر اشخاص یا سازمان‌های درگیر (رهبران کشور vs افراد ناشناس)
        S: میزان بحرانی بودن واقعه از نظر معنایی
        """
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
            "model": LOCAL_MODEL_NAME, "prompt": prompt, "stream": False, "format": "json",
            "options": {"temperature": 0.0, "num_ctx": 2048}
        }
        try:
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=12)
            result_data = json.loads(response.json()['response'])
            return (
                result_data.get("entity_score", 30),
                result_data.get("criticality_score", 30),
                result_data.get("is_opinion", False)
            )
        except Exception as e:
            logger.error(f"⚠️ Local LLM Scoring Error: {e}")
            return 30, 30, False

    def calculate_novelty(self, text: str) -> float:
        """
        محاسبه امتیاز تازگی (Novelty - N)
        وزن در فرمول: ۱۵٪
        مقایسه بردار خبر با پایگاه داده برداری برای تشخیص تکراری بودن.
        """
        try:
            vector = ai_engine.get_embedding(text)
            # جستجو در ChromaDB برای یافتن نزدیک‌ترین شباهت
            results = ai_engine.collection.query(
                query_embeddings=[vector],
                n_results=1,
                include=["distances"]
            )
            
            if not results['distances'] or not results['distances'][0]:
                return 100.0 # کاملاً جدید
            
            # تبدیل فاصله کسینوسی به شباهت
            max_similarity = 1.0 - results['distances'][0][0]
            
            if max_similarity > 0.88: return 0.0 # احتمالاً خبر تکراری است
            return 100 * (1.0 - max_similarity)
        except Exception as e:
            logger.error(f"Novelty Calculation Error: {e}")
            return 50.0

    def get_confidence_score(self, trend_id: int) -> float:
        """
        محاسبه ضریب اطمینان (Confidence Score)
        ترکیبی از اعتبار منبع (Tier) و تنوع خبرگزاری‌ها.
        """
        news_items = self.db.query(RawNews).filter(RawNews.trend_id == trend_id).all()
        if not news_items: return 0.5
        
        # ۱. تعیین بهترین سطح منبع در کلاستر بر اساس تنظیمات فاز ۶
        best_tier = min([n.source_tier for n in news_items])
        
        # نقشه وزنی Tierها از Config خوانده می‌شود
        tier_weights = Config.SOURCE_CONFIG["WEIGHTS"]
        base_confidence = tier_weights.get(best_tier, 0.75)
        
        # ۲. ضریب تنوع منابع (Diversity Multiplier)
        unique_source_names = set([n.source_name for n in news_items])
        source_count = len(unique_source_names)
        
        diversity_multiplier = 1.0
        if source_count >= 5: diversity_multiplier = 1.35
        elif source_count >= 3: diversity_multiplier = 1.25
        elif source_count >= 2: diversity_multiplier = 1.15
            
        final_conf = base_confidence * diversity_multiplier
        
        # محدودسازی سقف ضریب اطمینان
        return min(1.5, final_conf)

    def determine_trajectory(self, current_tps, previous_tps):
        """
        تعیین روند حرکت ترند (Trajectory).
        مقایسه امتیاز فعلی با دوره قبل برای نمایش در UI.
        """
        if previous_tps <= 0: return "up"
        
        # محاسبه درصد تغییرات
        change_ratio = (current_tps - previous_tps) / previous_tps
        
        if change_ratio > 0.06: return "up"      # رشد بیش از ۶ درصد
        if change_ratio < -0.06: return "down"   # افت بیش از ۶ درصد
        return "steady"                          # نوسان جزئی

    def run_tps_cycle(self, trend_id: int):
        """
        اجرای چرخه کامل و جامع امتیازدهی پیشرفته (Advanced TPS 2.1 - Async Ready)
        این متد توسط ورکر محاسباتی (Gravity Worker) فراخوانی می‌شود، نه اسکرپرها.
        """
        trend = self.db.query(Trend).get(trend_id)
        if not trend: return None
        
        # دریافت سند مرجع (Reference Document) برای تحلیل معنایی
        ref_doc = ai_engine.get_cluster_reference_doc(trend.cluster_id)
        if not ref_doc:
            # اگر سند مرجع یافت نشد، از اولین خبر موجود استفاده کن
            first_news = self.db.query(RawNews).filter(RawNews.trend_id == trend.id).first()
            if first_news: ref_doc = first_news.content
            else: return None
        
        # ۱. استخراج سیگنال‌های خام (V, E, S, N) و شتاب (فاز ۶)
        v = self.calculate_velocity(trend_id)
        accel = self.calculate_acceleration(trend_id) # متد جدید فاز ۶
        e, s, is_opinion = self.analyze_semantic_and_entity(ref_doc)
        n = self.calculate_novelty(ref_doc) # بازگردانده شده طبق درخواست
        
        # ۲. اعمال ضریب تقویت استراتژیک (Criticality Boost)
        c_boost = self.get_criticality_boost(ref_doc)
        
        # محاسبه امتیاز سیگنال نهایی با وزن‌دهی استاندارد
        # Formula: Signal = (0.35V + 0.25E + 0.25S + 0.15N) * Boost
        signal_score = ((0.35 * v) + (0.25 * e) + (0.25 * s) + (0.15 * n)) * c_boost
        
        # ۳. محاسبه ضریب اعتماد منابع بر اساس Tiers فاز ۶
        confidence = self.get_confidence_score(trend_id) # بازگردانده شده
        
        # ۴. محاسبه نهایی TPS و اعمال فیلترهای ایمنی
        final_tps = min(100.0, signal_score * confidence)
        
        # اعمال جریمه (Penalty) برای محتوای زرد یا نظرات شخصی
        normalized_title = normalize_turkish(trend.title or ref_doc[:100])
        if any(junk in normalized_title for junk in JUNK_KEYWORDS):
            final_tps = min(12.0, final_tps) # اخبار زرد هرگز ترند نمی‌شوند
        
        if is_opinion:
            final_tps *= 0.55 # اخبار تحلیلی/شخصی وزن کمتری در بخش "داغ" دارند
            
        # ۵. بروزرسانی روند حرکت و شتاب (Trajectory)
        # اگر شتاب انفجاری (accel='up') باشد، اولویت با آن است، وگرنه روند معمولی محاسبه می‌شود
        trend.trajectory = accel if accel == "up" else self.determine_trajectory(final_tps, trend.final_tps)
        
        # --- فاز ۶.۲: مدیریت هشدار آسنکرون ---
        # فقط به ادمین اطلاع می‌دهد. انتشار خودکار (Auto-Pilot) توسط Summarizer انجام می‌شود.
        if final_tps >= Config.THRESHOLD_ADMIN_ALERT and trend.previous_tps < Config.THRESHOLD_ADMIN_ALERT:
            alert_service.send_admin_alert(
                title=trend.title or ref_doc[:60],
                tps=final_tps,
                trajectory=trend.trajectory,
                cluster_id=trend.cluster_id
            )

        # ۶. ذخیره‌سازی داده‌ها در آبجکت دیتابیس
        trend.previous_tps = trend.final_tps
        trend.tps_signal = signal_score
        trend.tps_confidence = confidence
        trend.final_tps = final_tps
        trend.score = final_tps # همگام‌سازی برای کدهای قدیمی
        trend.last_updated = datetime.now(timezone.utc).replace(tzinfo=None)
        
        try:
            self.db.commit()
            logger.info(f"✅ [Async TPS] Trend {trend_id} Scored: {final_tps:.2f} | Accel: {trend.trajectory}")
            return final_tps
        except Exception as ex:
            self.db.rollback()
            logger.error(f"❌ Error during DB commit in Scoring: {ex}")
            return None