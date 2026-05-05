import os
from dotenv import load_dotenv

# بارگذاری متغیرها از فایل .env
load_dotenv()

class Config:
    """
    تنظیمات مرکزی TrendiaTR - نسخه کامل فاز ۶
    شامل تنظیمات دیتابیس، تلگرام، هوش مصنوعی و اعتبار منابع.
    """
    
    # --- تنظیمات دیتابیس (PostgreSQL) ---
    DB_USER = os.getenv("POSTGRES_USER", "admin")
    DB_PASS = os.getenv("POSTGRES_PASSWORD", "secretpassword")
    DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
    DB_PORT = os.getenv("POSTGRES_PORT", "5433")
    DB_NAME = os.getenv("POSTGRES_DB", "trend_watcher_db")
    
    SQLALCHEMY_DATABASE_URI = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- تنظیمات تلگرام (Core) ---
    TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
    TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
    TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")

    # --- تنظیمات بات مدیریت و اطلاع‌رسانی ---
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
    PUBLIC_CHANNEL_ID = os.getenv("PUBLIC_CHANNEL_ID")

    # --- تنظیمات زیرساخت (Redis & URLs) ---
    REDIS_URL = f"redis://{os.getenv('REDIS_HOST', 'localhost')}:6379/0"
    BASE_SITE_URL = os.getenv("BASE_SITE_URL", "https://trendiatr.com")

    # --- تنظیمات Discovery Latency (Breaking-News Mode) ---
    # RSS polling: faster base cycle with adaptive burst support in worker
    RSS_POLL_INTERVAL_SECONDS = int(os.getenv("RSS_POLL_INTERVAL_SECONDS", "180"))
    RSS_MIN_POLL_INTERVAL_SECONDS = int(os.getenv("RSS_MIN_POLL_INTERVAL_SECONDS", "45"))
    RSS_MAX_POLL_INTERVAL_SECONDS = int(os.getenv("RSS_MAX_POLL_INTERVAL_SECONDS", "600"))
    RSS_POLL_JITTER_RATIO = float(os.getenv("RSS_POLL_JITTER_RATIO", "0.15"))
    RSS_STARTUP_STAGGER_SECONDS = int(os.getenv("RSS_STARTUP_STAGGER_SECONDS", "20"))
    RSS_PRIME_START_HOUR = int(os.getenv("RSS_PRIME_START_HOUR", "7"))
    RSS_PRIME_END_HOUR = int(os.getenv("RSS_PRIME_END_HOUR", "23"))
    RSS_PRIME_INTERVAL_SECONDS = int(os.getenv("RSS_PRIME_INTERVAL_SECONDS", "90"))

    # Social/X trend polling: shorter base cycle with adaptive slowdown on quiet periods
    SOCIAL_POLL_INTERVAL_SECONDS = int(os.getenv("SOCIAL_POLL_INTERVAL_SECONDS", "300"))
    SOCIAL_MIN_POLL_INTERVAL_SECONDS = int(os.getenv("SOCIAL_MIN_POLL_INTERVAL_SECONDS", "120"))
    SOCIAL_MAX_POLL_INTERVAL_SECONDS = int(os.getenv("SOCIAL_MAX_POLL_INTERVAL_SECONDS", "1800"))
    SOCIAL_POLL_JITTER_RATIO = float(os.getenv("SOCIAL_POLL_JITTER_RATIO", "0.15"))
    SOCIAL_STARTUP_STAGGER_SECONDS = int(os.getenv("SOCIAL_STARTUP_STAGGER_SECONDS", "30"))
    SOCIAL_PRIME_START_HOUR = int(os.getenv("SOCIAL_PRIME_START_HOUR", "8"))
    SOCIAL_PRIME_END_HOUR = int(os.getenv("SOCIAL_PRIME_END_HOUR", "23"))
    SOCIAL_PRIME_INTERVAL_SECONDS = int(os.getenv("SOCIAL_PRIME_INTERVAL_SECONDS", "180"))

    # --- Async Scoring Queue (Priority + Backpressure) ---
    SCORING_QUEUE_MAX_SIZE = int(os.getenv("SCORING_QUEUE_MAX_SIZE", "5000"))
    SCORING_QUEUE_BATCH_SIZE = int(os.getenv("SCORING_QUEUE_BATCH_SIZE", "50"))
    SCORING_QUEUE_MAX_RETRIES = int(os.getenv("SCORING_QUEUE_MAX_RETRIES", "2"))

    # --- Shared HTTP Resilience & Observability ---
    HTTP_RETRY_ATTEMPTS = int(os.getenv("HTTP_RETRY_ATTEMPTS", "3"))
    HTTP_BACKOFF_BASE_SECONDS = float(os.getenv("HTTP_BACKOFF_BASE_SECONDS", "0.7"))
    HTTP_BACKOFF_MAX_SECONDS = float(os.getenv("HTTP_BACKOFF_MAX_SECONDS", "8"))
    HTTP_BACKOFF_JITTER_SECONDS = float(os.getenv("HTTP_BACKOFF_JITTER_SECONDS", "0.3"))

    # --- تنظیمات هوش مصنوعی محلی (Ollama) ---
    OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://ttw_ollama:11434/api/generate")
    LOCAL_MODEL_NAME = "qwen2.5:1.5b"

    # --- فاز ۶: نگاشت استراتژیک منابع (Source Authority) ---
    SOURCE_CONFIG = {
        "TIER_1_OFFICIAL": ["AA", "Anadolu Ajansı", "TRT", "DHA", "IHA", "ANKA"],
        "TIER_2_REPUTABLE": ["Sözcü", "Hürriyet", "Habertürk", "Cumhuriyet", "Milliyet", "T24"],
        "WEIGHTS": {
            1: 1.25,  # افزایش وزن برای خبرگزاری‌های رسمی (Tier 1)
            2: 1.00,  # وزن استاندارد برای روزنامه‌های معتبر (Tier 2)
            3: 0.75   # کاهش وزن برای منابع ناشناس یا کانال‌های تلگرامی (Tier 3)
        }
    }

    # --- آستانه‌های امتیازدهی (TPS Thresholds) ---
    THRESHOLD_ADMIN_ALERT = 20.0    # ارسال هشدار به ادمین برای بررسی
    THRESHOLD_AUTO_PUBLISH = 35.0   # انتشار خودکار در صورت عدم واکنش ادمین یا امتیاز بسیار بالا