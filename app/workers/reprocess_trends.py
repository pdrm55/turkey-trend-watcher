import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.database.models import SessionLocal, Trend

def force_reset_all_trends():
    db = SessionLocal()
    try:
        # دریافت همه ترندها بدون هیچ شرطی
        all_trends = db.query(Trend).all()
        count = len(all_trends)
        
        print(f"🔄 Found {count} total trends in database.")
        
        if count == 0:
            print("✅ Database is empty. Nothing to reset.")
            return

        print("⏳ Forcing reset on ALL trends (Cleaning titles, summaries, categories, SCORES)...")
        
        for trend in all_trends:
            trend.summary = None 
            trend.title = None 
            trend.category = None 
            
            # ریست کردن امتیاز طبق درخواست شما
            # نکته: با این کار، ترندها از بخش HOT خارج می‌شوند تا دوباره امتیاز جمع کنند
            trend.score = 0 
            
            # تعداد پیام‌ها را نگه می‌داریم چون یک واقعیت آماری است (تعداد خبرهای لینک شده)
            # trend.message_count = 0 
            
        db.commit()
        print(f"✅ Successfully reset {count} trends.")
        print("👉 Now run 'python3 app/workers/summarizer.py' to re-process with fresh logic.")

    except Exception as e:
        print(f"❌ Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    confirm = input("⚠️  This will reset SCORE, TITLES, and SUMMARIES. Are you sure? (y/n): ")
    if confirm.lower() == 'y':
        force_reset_all_trends()
    else:
        print("Cancelled.")