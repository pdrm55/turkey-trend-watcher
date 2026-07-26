import sys
import os
from datetime import datetime, timedelta

# اضافه کردن مسیر پروژه برای دسترسی به مدل‌های دیتابیس
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from app.database.models import SessionLocal, Trend, RawNews

def extract_top_trends():
    db = SessionLocal()
    print("🔍 Searching database for Top Trends (Last 30 Days)...\n")
    print("="*80)
    
    try:
        # پیدا کردن ترندهای 30 روز گذشته که کاملا موفق و داغ بوده‌اند
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        
        top_trends = db.query(Trend).filter(
            Trend.first_seen >= thirty_days_ago,
            Trend.final_tps >= 40.0,  # فقط اخبار بسیار مهم
            Trend.is_published == True # اخباری که با موفقیت منتشر شده‌اند
        ).order_by(Trend.final_tps.desc()).limit(15).all()

        if not top_trends:
            print("❌ No high-TPS trends found in the last 30 days.")
            return

        for trend in top_trends:
            print(f"📌 Trend ID: {trend.id} | TPS: {trend.final_tps:.1f} | Category: {trend.category}")
            print(f"📰 Title: {trend.title}")
            print(f"⏱️ First Seen by TrendiaTR: {trend.first_seen}")
            
            # پیدا کردن اولین منبعی که این خبر را منتشر کرد (جرقه اولیه)
            first_news = db.query(RawNews).filter(RawNews.trend_id == trend.id).order_by(RawNews.published_at.asc()).first()
            if first_news:
                print(f"🔥 Sparked By: {first_news.source_name} at {first_news.published_at}")
            
            # پیدا کردن مهم‌ترین خبرگزاری‌های رسمی که بعدا به این ترند پیوستند
            all_sources = db.query(RawNews.source_name).filter(RawNews.trend_id == trend.id).distinct().all()
            source_names = [s[0] for s in all_sources if s[0]]
            print(f"🌐 Involved Sources: {', '.join(source_names[:5])} ...")
            print("-" * 80)

    except Exception as e:
        print(f"❌ Database Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    extract_top_trends()
