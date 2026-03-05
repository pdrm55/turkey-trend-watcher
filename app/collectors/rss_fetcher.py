import sys
import os
import time
import feedparser
from datetime import datetime, timezone
from dateutil import parser as date_parser

# Add project root to sys path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.database.models import SessionLocal, RawNews, Trend, TrendArrivals
from app.core.ai_engine import ai_engine
# نکته مهم: ماژول scoring کامل حذف نشد، فقط get_source_tier نگه داشته شد، محاسبه‌گر TPS حذف شد
from app.core.scoring import get_source_tier
from app.core.text_utils import slugify_turkish
from app.core.classifier import fast_classify

# Path for RSS sources configuration
RSS_FILE = os.path.join(os.path.dirname(__file__), 'rss_sources.txt')

def generate_initial_slug(db, text, trend_id=None):
    """
    SEO Logic: Generates a human-readable URL slug immediately for new trends.
    This ensures that search engines index meaningful keywords instead of IDs.
    """
    if not text:
        return "haber-detayi"
        
    words = text.split()[:7]
    base_title = " ".join(words)
    base_slug = slugify_turkish(base_title)
    
    unique_slug = base_slug
    counter = 1
    while True:
        existing = db.query(Trend).filter(Trend.slug == unique_slug)
        if trend_id:
            existing = existing.filter(Trend.id != trend_id)
        
        if not existing.first():
            return unique_slug
        
        unique_slug = f"{base_slug}-{counter}"
        counter += 1

def load_rss_sources():
    """Loads source name and URL pairs from rss_sources.txt"""
    sources = {}
    if not os.path.exists(RSS_FILE):
        return {}
        
    try:
        with open(RSS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                # Format: SourceName, URL
                parts = line.split(',', 1)
                if len(parts) == 2:
                    sources[parts[0].strip()] = parts[1].strip()
    except Exception as e:
        print(f"⚠️ Error loading RSS sources: {e}")
        
    return sources

def fetch_and_process_rss():
    """Executes a single cycle of RSS fetching, clustering, and queuing for scoring"""
    db = SessionLocal()
    rss_feeds = load_rss_sources()
    current_time_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    
    print(f"🔄 RSS Cycle Started: Checking {len(rss_feeds)} feeds...")
    
    new_trends_count = 0
    signal_updates_count = 0

    for source_name, url in rss_feeds.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title = entry.get('title', '')
                summary = entry.get('summary', '') or entry.get('description', '')
                link = entry.get('link', '')
                
                # Extract actual publication time
                pub_date_str = entry.get('published') or entry.get('updated')
                actual_pub_time = current_time_utc
                
                if pub_date_str:
                    try:
                        from datetime import timedelta
                        parsed_date = date_parser.parse(pub_date_str)
                        
                        # If naive (no timezone info), assume it's Turkey time (UTC+3)
                        if parsed_date.tzinfo is None:
                            tr_tz = timezone(timedelta(hours=3))
                            parsed_date = parsed_date.replace(tzinfo=tr_tz)
                            
                        actual_pub_time = parsed_date.astimezone(timezone.utc).replace(tzinfo=None)
                        
                        # SANITY CHECK: Never allow future timestamps from misconfigured RSS feeds
                        if actual_pub_time > current_time_utc:
                            actual_pub_time = current_time_utc
                    except:
                        actual_pub_time = current_time_utc

                # Extract Media from RSS (Two-Stage Image Extraction)
                media_url = None
                if 'media_content' in entry and len(entry.media_content) > 0:
                    media_url = entry.media_content[0].get('url')
                elif 'links' in entry:
                    for link_item in entry.links:
                        if link_item.get('type', '').startswith('image/'):
                            media_url = link_item.get('href')
                            break
                
                full_text = f"{title}. {summary}"
                if len(full_text) < 30:
                    continue
                
                # Avoid processing the exact same link twice
                existing_news = db.query(RawNews).filter(RawNews.external_id == link).first()
                if existing_news:
                    continue

                # --- Step 1: AI Brain Clustering ---
                cluster_id, _ = ai_engine.process_news(full_text, source_name, link)
                if not cluster_id:
                    continue

                # --- Step 2: Trend Management ---
                trend = db.query(Trend).filter(Trend.cluster_id == cluster_id).first()
                
                if trend:
                    trend.message_count += 1
                    trend.last_updated = max(trend.last_updated, actual_pub_time)
                    trend.needs_scoring = True # ASYNC TRIGGER: در صف امتیازدهی قرار گرفت
                    signal_updates_count += 1
                else:
                    # New Trend from RSS: Create instant SEO slug
                    initial_category = fast_classify(full_text)
                    trend = Trend(
                        cluster_id=cluster_id,
                        message_count=1,
                        title=title[:120].strip(),
                        slug=generate_initial_slug(db, title), # SEO-First
                        category=initial_category,
                        first_seen=actual_pub_time,
                        last_updated=actual_pub_time,
                        needs_scoring=True # ASYNC TRIGGER: در صف امتیازدهی قرار گرفت
                    )
                    db.add(trend)
                    db.flush()
                    new_trends_count += 1
                
                # --- Step 3: Raw Data and Reliability ---
                source_tier = get_source_tier(source_name)
                news_item = RawNews(
                    source_type="rss",
                    source_name=source_name,
                    source_tier=source_tier,
                    external_id=link,
                    content=full_text,
                    published_at=actual_pub_time,
                    trend_id=trend.id,
                    media_url=media_url,
                    media_status=0
                )
                db.add(news_item)
                db.flush()

                # --- Step 4: Record Velocity History ---
                arrival = TrendArrivals(
                    trend_id=trend.id,
                    raw_news_id=news_item.id,
                    timestamp=actual_pub_time
                )
                db.add(arrival)
                db.commit()

                # فاز ۶.۲: حذف محاسبه همزمان TPS. ورکر پس‌زمینه این کار را انجام می‌دهد.
                
        except Exception as e:
            db.rollback()
            print(f"   ❌ Error processing feed {source_name}: {e}")

    print(f"✅ RSS Cycle Finished: {new_trends_count} New Trends, {signal_updates_count} Signal Updates.")
    db.close()

def main():
    """Main worker loop for the RSS Engine"""
    print("🧠 TrendiaTR RSS Fetcher Active (Async Mode).")
    while True:
        try:
            fetch_and_process_rss()
        except Exception as e:
            print(f"❌ Critical Error in RSS Loop: {e}")
        
        # Sleep for 10 minutes between cycles to respect source rate limits
        time.sleep(600)

if __name__ == "__main__":
    main()