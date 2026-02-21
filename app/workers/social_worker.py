import sys
import os
import time
import logging
import requests
from datetime import datetime, timezone
from ntscraper import Nitter

# Add project root to sys path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.config import Config
from app.database.models import SessionLocal, RawNews, Trend, TrendArrivals
from app.core.text_utils import JUNK_KEYWORDS, slugify_turkish
from app.core.ai_engine import ai_engine
from app.core.classifier import fast_classify

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("SocialWorker")

class SocialWorker:
    def __init__(self):
        self.scraper = Nitter(log_level=1, skip_instance_check=False)
        self.error_count = 0
        self.admin_chat_id = getattr(Config, 'ADMIN_CHAT_ID', None)
        self.bot_token = getattr(Config, 'TELEGRAM_BOT_TOKEN', None)

    def send_admin_alert(self, message):
        """Sends critical alerts to the admin via Telegram."""
        if not self.bot_token or not self.admin_chat_id:
            logger.warning("⚠️ Admin alert skipped: Missing Telegram config.")
            return

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.admin_chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            logger.error(f"❌ Failed to send admin alert: {e}")

    def generate_initial_slug(self, db, text, trend_id=None):
        """Generates a unique slug for new trends."""
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

    def fetch_trends(self):
        """Fetches top trends for Turkey using Nitter."""
        try:
            trends_data = self.scraper.get_trends("Turkey")
            
            if not trends_data or 'trends' not in trends_data:
                logger.warning("⚠️ No trends returned from Nitter.")
                return []

            valid_trends = []
            for trend in trends_data['trends']:
                name = trend['name']
                
                # Filter junk
                if len(name) < 2: continue
                if any(junk in name.lower() for junk in JUNK_KEYWORDS):
                    continue
                
                valid_trends.append(trend)
            
            return valid_trends

        except Exception as e:
            logger.error(f"❌ Error fetching trends: {e}")
            return None

    def run(self):
        logger.info("🚀 X-Watcher (Social Worker) Started")
        
        while True:
            try:
                trends = self.fetch_trends()
                
                if trends is None or len(trends) == 0:
                    self.error_count += 1
                    logger.warning(f"⚠️ Fetch failed or empty. Error count: {self.error_count}")
                    
                    if self.error_count >= 3:
                        self.send_admin_alert("🚨 X-Watcher Worker Failed! Consecutive errors reached threshold. Please check Nitter instances or X policies.")
                        self.error_count = 0 
                else:
                    self.error_count = 0
                    
                    # Process trends
                    db = SessionLocal()
                    current_time_utc = datetime.now(timezone.utc).replace(tzinfo=None)
                    
                    try:
                        new_count = 0
                        for item in trends:
                            name = item['name']
                            url = item['url']
                            
                            # Check if exists
                            existing = db.query(RawNews).filter(RawNews.external_id == url).first()
                            if existing: continue

                            # AI Clustering
                            cluster_id, _ = ai_engine.process_news(name, "X-Trend", url)
                            if not cluster_id: continue

                            # Trend Management
                            trend = db.query(Trend).filter(Trend.cluster_id == cluster_id).first()
                            
                            if trend:
                                trend.message_count += 1
                                trend.last_updated = max(trend.last_updated, current_time_utc)
                                trend.needs_scoring = True
                            else:
                                initial_category = fast_classify(name)
                                trend = Trend(
                                    cluster_id=cluster_id,
                                    message_count=1,
                                    title=name,
                                    slug=self.generate_initial_slug(db, name),
                                    category=initial_category,
                                    first_seen=current_time_utc,
                                    last_updated=current_time_utc,
                                    needs_scoring=True
                                )
                                db.add(trend)
                                db.flush()
                                new_count += 1

                            # Raw News
                            news_item = RawNews(
                                source_type="x",
                                source_name="X-Trend",
                                source_tier=3,
                                external_id=url,
                                content=name,
                                published_at=current_time_utc,
                                trend_id=trend.id,
                                media_status=0
                            )
                            db.add(news_item)
                            db.flush()
                            
                            # Arrival
                            arrival = TrendArrivals(
                                trend_id=trend.id,
                                raw_news_id=news_item.id,
                                timestamp=current_time_utc
                            )
                            db.add(arrival)
                            db.commit()
                            
                        logger.info(f"✅ Processed {len(trends)} X trends. New: {new_count}")
                        
                    except Exception as e:
                        db.rollback()
                        logger.error(f"❌ DB Error in SocialWorker: {e}")
                    finally:
                        db.close()

            except Exception as e:
                logger.error(f"❌ Critical Worker Loop Error: {e}")
                self.error_count += 1
            
            # Sleep for 30 minutes
            time.sleep(1800)

if __name__ == "__main__":
    worker = SocialWorker()
    worker.run()