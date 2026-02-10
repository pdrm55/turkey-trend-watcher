import sys
import os
from sqlalchemy import create_engine, text, inspect

# اضافه کردن مسیر پروژه برای دسترسی به تنظیمات و مدل‌ها
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))
from app.config import Config

def update_database():
    print("🔍 در حال اتصال به دیتابیس برای به‌روزرسانی ساختار...")
    
    # ایجاد اتصال به دیتابیس پستگرس
    try:
        engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
        inspector = inspect(engine)
        
        # دریافت لیست ستون‌های فعلی جدول trends
        columns = [c['name'] for c in inspector.get_columns('trends')]
        print(f"✅ اتصال برقرار شد. ستون‌های فعلی: {len(columns)}")
    except Exception as e:
        print(f"❌ خطا در اتصال به دیتابیس: {e}")
        return

    with engine.connect() as conn:
        # شروع یک تراکنش (Transaction)
        trans = conn.begin()
        try:
            # ۱. اضافه کردن ستون title_fa (برای مانیتورینگ فارسی)
            if 'title_fa' not in columns:
                print("➕ در حال اضافه کردن ستون 'title_fa'...")
                conn.execute(text("ALTER TABLE trends ADD COLUMN title_fa VARCHAR(255)"))
            
            # ۲. اضافه کردن ستون summary_fa (برای مانیتورینگ فارسی)
            if 'summary_fa' not in columns:
                print("➕ در حال اضافه کردن ستون 'summary_fa'...")
                conn.execute(text("ALTER TABLE trends ADD COLUMN summary_fa TEXT"))

            # ۳. اضافه کردن ستون slug (بسیار مهم برای سئو و URLهای خوانا)
            if 'slug' not in columns:
                print("➕ در حال اضافه کردن ستون 'slug' برای سئو...")
                # ابتدا ستون را می‌سازیم
                conn.execute(text("ALTER TABLE trends ADD COLUMN slug VARCHAR(255)"))
                # یک ایندکس یونیک برای سرعت و جلوگیری از تکرار می‌سازیم
                conn.execute(text("CREATE UNIQUE INDEX idx_trends_slug ON trends (slug)"))
            else:
                print("✅ ستون 'slug' از قبل وجود دارد.")

            trans.commit()
            print("✨ تمام تغییرات با موفقیت در دیتابیس اعمال شد.")
            
        except Exception as e:
            trans.rollback()
            print(f"❌ خطا در هنگام به‌روزرسانی ساختار دیتابیس: {e}")

if __name__ == "__main__":
    update_database()
