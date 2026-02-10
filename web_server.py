from flask import Flask
from app.api.routes import api_bp
from app.database.models import init_db  # اضافه شدن تابع آماده‌ساز دیتابیس
import os

# تعیین مسیرهای دقیق و مطلق پروژه
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'app/templates')
STATIC_DIR = os.path.join(BASE_DIR, 'app/static')

app = Flask(__name__, 
            template_folder=TEMPLATE_DIR, 
            static_folder=STATIC_DIR,
            static_url_path='/static')

# ثبت مسیرهای API و صفحات
app.register_blueprint(api_bp)

if __name__ == "__main__":
    # --- گام خودکارسازی: هماهنگ‌سازی دیتابیس قبل از شروع سرور ---
    init_db()
    # -----------------------------------------------------

    print(f"🚀 TrendiaTR Web Server starting on port 5000...")
    print(f"📁 Static Directory: {STATIC_DIR}")
    
    # اجرای سرور در حالت دیباگ برای توسعه لوکال
    app.run(host='0.0.0.0', port=5000, debug=True)