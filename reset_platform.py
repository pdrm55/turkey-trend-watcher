import sys
import os
import shutil
import feedparser
from datetime import datetime

# افزودن مسیر اصلی پروژه به پایتون
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

try:
    from app.database.models import SessionLocal, RawNews, Trend
    from app.config import Config
    from sqlalchemy import text
except ImportError as e:
    print(f"❌ Import Error: {e}")
    print("Make sure you are running this from the project root or the imports are correct.")
    sys.exit(1)

def reset_and_prime():
    print("⚠️  WARNING: This will WIPE ALL DATA (Trends, News, Clusters).")
    confirm = input("Type 'yes' to confirm: ")
    if confirm.lower() != 'yes':
        print("Operation cancelled.")
        return

    db = SessionLocal()
    
    # 1. پاکسازی دیتابیس SQL
    print("\n🗑️  Step 1: Cleaning SQL Database...")
    try:
        # استفاده از دستورات SQL مستقیم برای سرعت و اطمینان
        # نکته: اگر از SQLite استفاده می‌کنید دستورات متفاوت است، اما ORM معمولا هندل می‌کند
        num_news = db.query(RawNews).delete()
        num_trends = db.query(Trend).delete()
        db.commit()
        print(f"   ✅ Deleted {num_news} news items and {num_trends} trends.")
    except Exception as e:
        print(f"   ❌ Error cleaning SQL: {e}")
        db.rollback()

    # 2. پاکسازی دیتابیس وکتوری (ChromaDB)
    print("\n🗑️  Step 2: Cleaning Vector Database (ChromaDB)...")
    chroma_path = os.path.join(os.getcwd(), "chroma_db_data")
    if os.path.exists(chroma_path):
        try:
            shutil.rmtree(chroma_path)
            print("   ✅ ChromaDB folder removed.")
        except Exception as e:
            print(f"   ❌ Error removing ChromaDB: {e}")
    else:
        print("   ℹ️  ChromaDB folder not found (already clean).")

    # 3. پر کردن اولیه (Mark as Read)
    print("\n🛡️  Step 3: Priming DB with current RSS items (Skipping AI processing)...")
    
    # لیست فیدها را از کانفیگ می‌گیریم
    # فرض بر این است که Config.RSS_FEEDS یک لیست از دیکشنری یا رشته است
    feeds = getattr(Config, 'RSS_FEEDS', [])
    if not feeds:
        # اگر در کانفیگ نبود، یک لیست نمونه یا خالی در نظر می‌گیریم
        print("   ⚠️ No feeds found in Config.RSS_FEEDS.")
    
    total_ignored = 0
    
    for feed_source in feeds:
        # استخراج URL بسته به ساختار کانفیگ شما
        url = feed_source.get('url') if isinstance(feed_source, dict) else feed_source
        
        print(f"   Reading {url}...", end='', flush=True)
        try:
            feed = feedparser.parse(url)
            batch_count = 0
            
            for entry in feed.entries:
                # ذخیره فقط لینک و تایتل (بدون ارسال به AI)
                # این باعث می‌شود rss_fetcher در اجرای بعدی این‌ها را تکراری تشخیص دهد
                
                # بررسی تکراری بودن (محض احتیاط)
                exists = db.query(RawNews).filter(RawNews.link == entry.link).first()
                if not exists:
                    news_item = RawNews(
                        source=url,
                        link=entry.link,
                        title=entry.title[:255], # محدودیت طول تایتل
                        content="IGNORED_OLD_DATA", # محتوا مهم نیست چون قرار نیست پردازش شود
                        published_date=datetime.now(),
                        trend_id=None # ترند ندارد، پس Summarizer هم کاری با آن ندارد
                    )
                    db.add(news_item)
                    batch_count += 1
            
            db.commit()
            total_ignored += batch_count
            print(f" Done ({batch_count} items ignored)")
            
        except Exception as e:
            print(f" Failed ({e})")

    db.close()
    print("\n" + "="*50)
    print(f"✅ SYSTEM RESET COMPLETE.")
    print(f"🙈 {total_ignored} old news items marked as 'read'.")
    print("🚀 You can now start 'rss_fetcher.py'. It will only process NEW incoming news.")
    print("="*50)

if __name__ == "__main__":
    reset_and_prime()