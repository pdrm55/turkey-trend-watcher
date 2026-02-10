import os
from dotenv import load_dotenv

# بارگذاری متغیرها از فایل .env
load_dotenv()

class Config:
    """تنظیمات کلی برنامه"""
    
    # تنظیمات دیتابیس
    DB_USER = os.getenv("POSTGRES_USER", "admin")
    DB_PASS = os.getenv("POSTGRES_PASSWORD", "secretpassword")
    DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
    DB_PORT = os.getenv("POSTGRES_PORT", "5433")  # پیش‌فرض را روی 5433 گذاشتیم
    DB_NAME = os.getenv("POSTGRES_DB", "trend_watcher_db")
    
    # اصلاحیه مهم: اضافه کردن پورت به آدرس اتصال
    SQLALCHEMY_DATABASE_URI = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # تنظیمات تلگرام
    TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
    TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
    TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")

    # تنظیمات ردیس
    REDIS_URL = f"redis://{os.getenv('REDIS_HOST', 'localhost')}:6379/0"