from flask import Flask
from app.api.routes import api_bp
import os

# تعیین مسیرهای دقیق و مطلق پروژه
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'app/templates')
STATIC_DIR = os.path.join(BASE_DIR, 'app/static')

# تعریف اپلیکیشن با تعیین دقیق پوشه‌های قالب و استاتیک
# Flask به صورت خودکار روت /static/ را برای static_folder فعال می‌کند
app = Flask(__name__, 
            template_folder=TEMPLATE_DIR, 
            static_folder=STATIC_DIR,
            static_url_path='/static')

# ثبت مسیرهای API و صفحات
app.register_blueprint(api_bp)

if __name__ == "__main__":
    print(f"🚀 TrendiaTR Web Server starting...")
    print(f"📁 Static Directory: {STATIC_DIR}")
    
    # اجرای سرور
    app.run(host='0.0.0.0', port=5000, debug=True)
