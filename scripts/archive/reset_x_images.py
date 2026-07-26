import os
import sys

# اضافه کردن مسیر اصلی پروژه
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app.database.models import SessionLocal, RawNews, Trend

def reset_x_images():
    db = SessionLocal()
    try:
        # پیدا کردن تمام خبرهای توییتر که وضعیت عکسشان 0 نیست (چه دانلود شده، چه خطا داده)
        x_news = db.query(RawNews).filter(
            RawNews.source_type == 'x',
            RawNews.media_status != 0
        ).all()

        if not x_news:
            print("✅ هیچ عکسی مربوط به X-Trend برای ریست کردن یافت نشد.")
            return

        print(f"🔄 پیدا شدن {len(x_news)} خبر توییتر. شروع عملیات پاک‌سازی...")
        
        reset_count = 0
        
        for news in x_news:
            # ۱. پاک کردن فایل فیزیکی از روی هارد
            if news.media_path:
                # آدرس مطلق در داخل کانتینر
                file_path = os.path.join('/app/app/static', news.media_path)
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        print(f"🗑️ فایل پاک شد: {file_path}")
                    except Exception as e:
                        print(f"⚠️ خطا در پاک کردن فایل {file_path}: {e}")
            
            # ۲. پاک کردن عکس کاور ترند (اگر این عکس برای ترند انتخاب شده بود)
            if news.trend_id:
                trend = db.query(Trend).filter(Trend.id == news.trend_id).first()
                if trend and trend.cover_image == news.media_path:
                    trend.cover_image = None
                    print(f"🧹 عکس کاور ترند {trend.id} حذف شد.")
            
            # ۳. ریست کردن وضعیت خبر برای دانلود مجدد
            news.media_status = 0
            news.media_path = None
            news.media_url = None
            news.media_meta = None
            
            reset_count += 1

        db.commit()
        print(f"🎉 عملیات موفقیت‌آمیز! تعداد {reset_count} عکس توییتر ریست شد.")
        print("⏳ ورکر تصویر (Image Worker) به زودی با الگوریتم جدید برای آن‌ها عکس دانلود می‌کند.")

    except Exception as e:
        print(f"❌ خطای دیتابیس: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    reset_x_images()
