import os
import logging
from flask import Flask
from app.api.routes import api_bp
from app.database.models import init_db

# تنظیمات لاگر برای مانیتورینگ متمرکز سیستم
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("TrendiaTR-Web")

def create_app():
    """
    ساختار کارخانه‌ای (Factory Pattern) برای ایجاد اپلیکیشن Flask.
    این ساختار برای اجرای بهینه توسط Gunicorn و مدیریت چندین ورکر ضروری است.
    """
    
    # حفظ منطق مسیردهی صریح از نسخه قبلی برای اطمینان از بارگذاری صحیح قالب‌ها و فایل‌های استاتیک
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    TEMPLATE_DIR = os.path.join(BASE_DIR, 'app/templates')
    STATIC_DIR = os.path.join(BASE_DIR, 'app/static')

    app = Flask(__name__, 
                template_folder=TEMPLATE_DIR, 
                static_folder=STATIC_DIR,
                static_url_path='/static')

    # ثبت بلوپرینت اصلی API و مسیرهای مسیریابی (Routing)
    app.register_blueprint(api_bp)

    # اطمینان از آماده‌سازی دیتابیس در بدو ورود به اپلیکیشن
    with app.app_context():
        try:
            # فراخوانی تابع هماهنگ‌سازی دیتابیس (برگرفته از منطق اصلی فایل قبلی)
            init_db()
            logger.info("✅ Database schemas verified and synchronized.")
        except Exception as e:
            logger.error(f"❌ Database Initialization Error: {e}")

    return app

# ایجاد آبجکت اصلی اپلیکیشن جهت استفاده Gunicorn (Entry Point)
app = create_app()

if __name__ == "__main__":
    # اجرای مستقیم برای دیباگ و توسعه لوکال (در محیط عملیاتی Docker از Gunicorn استفاده می‌شود)
    logger.info("🚀 Starting TrendiaTR Web Server in Debug Mode...")
    
    # خواندن پورت از متغیرهای محیطی یا استفاده از پورت پیش‌فرض ۵۰۰۰
    port = int(os.getenv("PORT", 5000))
    
    # در حالت اجرای مستقیم، Debug فعال می‌ماند (مشابه نسخه قبلی شما)
    app.run(host='0.0.0.0', port=port, debug=True)