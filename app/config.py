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