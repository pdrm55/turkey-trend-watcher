import sys
import os
import time
import feedparser
from datetime import datetime, timezone

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.database.models import SessionLocal, RawNews, Trend
from app.core.ai_engine import AIEngine

RSS_FILE = os.path.join(os.path.dirname(__file__), 'rss_sources.txt')

def load_rss_sources():
    sources = {}
    if not os.path.exists(RSS_FILE):
        return {}
    try:
        with open(RSS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'): continue
                parts = line.split(',', 1)
                if len(parts) == 2:
                    sources[parts[0].strip()] = parts[1].strip()
    except Exception as e:
        print(f"❌ Error reading RSS file: {e}")
    return sources

def fetch_and_process_rss(ai_engine):
    db = SessionLocal()
    rss_feeds = load_rss_sources()
    print(f"🔄 RSS Cycle Started at {datetime.now().strftime('%H:%M:%S')}...")
    
    new_count = 0
    updated_count = 0
    
    # زمان واحد برای کل این سیکل (برای هماهنگی)
    # استفاده از زمان حال سیستم (تبدیل شده به UTC برای دیتابیس)
    current_time_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    
    for source_name, url in rss_feeds.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title = entry.get('title', '')
                summary = entry.get('summary', '') or entry.get('description', '')
                link = entry.get('link', '')
                full_text = f"{title}. {summary}"
                
                if len(full_text) < 20: continue

                # --- تغییر استراتژیک: استفاده از زمان دریافت ---
                # زمان خبرگزاری را نادیده می‌گیریم تا مشکل تایم‌زون و آینده حل شود
                pub_date = current_time_utc

                exists = db.query(RawNews).filter(RawNews.external_id == link).first()
                if exists: continue

                cluster_id, is_duplicate = ai_engine.process_news(full_text, source_name, link)
                if not cluster_id: continue

                trend = db.query(Trend).filter(Trend.cluster_id == cluster_id).first()
                
                if trend:
                    trend.message_count += 1
                    # همیشه زمان آپدیت را به زمان حال تغییر می‌دهیم
                    trend.last_updated = pub_date
                    trend.score += 5
                    updated_count += 1
                else:
                    trend = Trend(
                        cluster_id=cluster_id,
                        message_count=1,
                        score=5.0,
                        title=title[:100],
                        first_seen=pub_date,
                        last_updated=pub_date
                    )
                    db.add(trend)
                    new_count += 1
                
                db.commit()

                news_item = RawNews(
                    source_type="rss",
                    source_name=source_name,
                    external_id=link,
                    content=full_text,
                    published_at=pub_date,
                    trend_id=trend.id
                )
                db.add(news_item)
                db.commit()
                
        except Exception as e:
            # print(f"   ❌ Error fetching {source_name}: {e}")
            db.rollback()

    db.close()
    print(f"✅ RSS Cycle Finished: {new_count} New, {updated_count} Updates.")

def main():
    print("🧠 Initializing AI Brain for RSS Worker...")
    ai_engine = AIEngine()
    while True:
        try:
            fetch_and_process_rss(ai_engine)
        except Exception as e:
            print(f"CRITICAL ERROR: {e}")
        print("💤 Sleeping for 10 minutes...")
        time.sleep(600)

if __name__ == "__main__":
    main()