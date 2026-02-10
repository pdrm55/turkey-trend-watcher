import os
from app.config import Config

def check_setup():
    print("--- Turkey Trend Watcher Setup Check ---")
    
    # 1. بررسی فایل .env
    if not os.path.exists(".env"):
        print("❌ Error: .env file not found!")
        return
    
    # 2. بررسی متغیرهای تلگرام
    if not Config.TELEGRAM_API_ID or not Config.TELEGRAM_API_HASH:
        print("⚠️ Warning: TELEGRAM_API_ID or TELEGRAM_API_HASH is missing in .env")
    else:
        print("✅ Telegram Config: Found")
        
    # 3. بررسی کانفیگ دیتابیس
    print(f"ℹ️  Database URI: postgresql://{Config.DB_USER}:****@{Config.DB_HOST}/{Config.DB_NAME}")
    
    print("\n✅ Setup Complete! You are ready to run 'docker-compose up -d'")

if __name__ == "__main__":
    check_setup()