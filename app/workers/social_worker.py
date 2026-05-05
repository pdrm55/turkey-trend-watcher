import sys
import os
import time
import random
import logging
import requests
import urllib.parse
import feedparser
import re
from datetime import datetime, timezone
from datetime import timedelta
from bs4 import BeautifulSoup

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

    def fetch_google_context(self, keyword):
        """Fetches background news context for a Twitter trend using Google News."""
        try:
            encoded_query = urllib.parse.quote_plus(keyword)
            rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=tr&gl=TR&ceid=TR:tr"
            feed = feedparser.parse(rss_url)
            if feed.entries:
                # Regex to remove trailing " - Source" or " | Source" from Google News titles
                clean_title = lambda t: re.sub(r'\s+[-|]\s+[^-|]+$', '', t).strip()
                
                best_title = clean_title(feed.entries[0].title)
                
                # 1. For UI (RawNews content saved in DB)
                headlines_ui = [f"𝕏 Sosyal Medya Trendi: {keyword}", "İlgili Haber Başlıkları:"]

                # 2. For AI (ChromaDB Vectorization - PURE NEWS ONLY)
                ai_text_parts = [best_title]

                for entry in feed.entries[:3]:
                    ct = clean_title(entry.title)
                    headlines_ui.append(f"📰 {ct}")
                    if ct != best_title:
                        ai_text_parts.append(ct)
                
                ui_content = "\n".join(headlines_ui)
                ai_content = " ".join(ai_text_parts) # Pure text ensures exact matching with RSS

                return best_title, ui_content, ai_content
        except Exception as e:
            logger.error(f"Google Context Error: {e}")
        
        # Fallback if Google fails
        fallback_ui = f"𝕏 Sosyal Medya Trendi: {keyword}"
        fallback_ai = f"{keyword} hakkında güncel gelişmeler ve paylaşımlar"
        return keyword, fallback_ui, fallback_ai

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
        """Fetches top trends for Turkey using multiple fallback aggregators."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
        }
        valid_trends = []
        
        try:
            # --- Attempt 1: GetDayTrends (Often has lower Cloudflare protection) ---
            logger.info("📡 Attempt 1: Fetching from GetDayTrends...")
            resp1 = requests.get("https://getdaytrends.com/turkey/", headers=headers, timeout=15)
            if resp1.status_code == 200:
                soup = BeautifulSoup(resp1.text, 'html.parser')
                # Primary selector for getdaytrends
                trend_elements = soup.select('table tbody tr td.main a') or soup.select('a.string')
                
                for el in trend_elements:
                    name = el.text.strip()
                    if len(name) > 2 and not any(junk in name.lower() for junk in JUNK_KEYWORDS):
                        valid_trends.append({'name': name, 'url': f"https://x.com/search?q={name.replace('#', '%23')}"})
            else:
                logger.warning(f"⚠️ GetDayTrends failed with status code: {resp1.status_code}")

            # --- Attempt 2: Trends24 (Fallback) ---
            if not valid_trends:
                logger.info("📡 Attempt 2: Falling back to Trends24...")
                resp2 = requests.get("https://trends24.in/turkey/", headers=headers, timeout=15)
                if resp2.status_code == 200:
                    soup = BeautifulSoup(resp2.text, 'html.parser')
                    trend_elements = soup.select('.trend-card:nth-of-type(1) li a') or soup.select('.trend-card__list li a')
                    
                    for el in trend_elements:
                        name = el.text.strip()
                        if len(name) > 2 and not any(junk in name.lower() for junk in JUNK_KEYWORDS):
                            valid_trends.append({'name': name, 'url': f"https://x.com/search?q={name.replace('#', '%23')}"})
                else:
                    logger.warning(f"⚠️ Trends24 failed with status code: {resp2.status_code}")

            if not valid_trends:
                logger.error("❌ Both aggregators failed or returned empty lists. Cloudflare block is likely.")
                
            return valid_trends

        except Exception as e:
            logger.error(f"❌ Error fetching trends from aggregators: {e}")
            return []

    def run(self):
        logger.info("🚀 X-Watcher (Social Worker) Started")
        base_interval = max(30, getattr(Config, "SOCIAL_POLL_INTERVAL_SECONDS", 300))
        min_interval = max(30, getattr(Config, "SOCIAL_MIN_POLL_INTERVAL_SECONDS", 120))
        max_interval = max(base_interval, getattr(Config, "SOCIAL_MAX_POLL_INTERVAL_SECONDS", 1800))
        next_sleep = max(min_interval, min(base_interval, max_interval))
        jitter_ratio = min(0.5, max(0.0, getattr(Config, "SOCIAL_POLL_JITTER_RATIO", 0.15)))
        startup_stagger = max(0, getattr(Config, "SOCIAL_STARTUP_STAGGER_SECONDS", 30))
        prime_start = max(0, min(23, getattr(Config, "SOCIAL_PRIME_START_HOUR", 8)))
        prime_end = max(0, min(23, getattr(Config, "SOCIAL_PRIME_END_HOUR", 23)))
        prime_interval = max(min_interval, min(max_interval, getattr(Config, "SOCIAL_PRIME_INTERVAL_SECONDS", 180)))
        logger.info(
            f"⚙️ Social polling config -> base={base_interval}s, prime={prime_interval}s, min={min_interval}s, max={max_interval}s, jitter={jitter_ratio}"
        )
        if startup_stagger > 0:
            initial_delay = random.uniform(0, startup_stagger)
            logger.info(f"⏳ Social startup stagger sleep: {initial_delay:.1f}s")
            time.sleep(initial_delay)
        
        while True:
            tr_hour = datetime.now(timezone(timedelta(hours=3))).hour
            in_prime_hours = prime_start <= tr_hour <= prime_end
            dynamic_base = prime_interval if in_prime_hours else base_interval
            try:
                trends = self.fetch_trends()
                
                if trends is None or len(trends) == 0:
                    self.error_count += 1
                    logger.warning(f"⚠️ Fetch failed or empty. Error count: {self.error_count}")
                    next_sleep = min(max_interval, max(dynamic_base, int(next_sleep * 1.5)))
                    
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
                            
                            # --- NEW: Fetch Real-World Context ---
                            best_title, enriched_content, ai_clustering_text = self.fetch_google_context(name)

                            # --- NEW: Semantic Cross-Validation ---
                            is_validated = ai_engine.verify_cross_trend(name, best_title)
                            trend_entities = {"cross_validated": True} if is_validated else {}

                            # Check if exists
                            existing = db.query(RawNews).filter(RawNews.external_id == url).first()
                            if existing: continue

                            # 🧠 CRITICAL FIX: Send pure AI text to ChromaDB for perfect matching
                            cluster_id, _ = ai_engine.process_news(ai_clustering_text, "X-Trend", url)
                            if not cluster_id: continue

                            # Trend Management
                            trend = db.query(Trend).filter(Trend.cluster_id == cluster_id).first()
                            
                            if trend:
                                trend.message_count += 1
                                trend.last_updated = max(trend.last_updated, current_time_utc)
                                trend.needs_scoring = True
                                # Add validation flag if it wasn't there
                                if is_validated:
                                    current_entities = dict(trend.entities or {})
                                    current_entities["cross_validated"] = True
                                    trend.entities = current_entities
                            else:
                                # Use the pure AI text for initial classification to avoid bias
                                initial_category = fast_classify(ai_clustering_text)
                                trend = Trend(
                                    cluster_id=cluster_id,
                                    message_count=1,
                                    title=best_title, # <-- CRITICAL FIX: Use real headline, not just the keyword
                                    slug=self.generate_initial_slug(db, best_title), # Use real headline for SEO
                                    category=initial_category,
                                    first_seen=current_time_utc,
                                    last_updated=current_time_utc,
                                    needs_scoring=True,
                                    entities=trend_entities # <--- Inject the golden label here
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
                                content=enriched_content, # 🎨 UI gets the pretty formatted text
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
                        if new_count > 0:
                            next_sleep = min_interval
                        else:
                            next_sleep = min(max_interval, max(dynamic_base, int(next_sleep * 1.25)))
                        
                    except Exception as e:
                        db.rollback()
                        logger.error(f"❌ DB Error in SocialWorker: {e}")
                        next_sleep = min(max_interval, max(dynamic_base, int(next_sleep * 1.5)))
                    finally:
                        db.close()

            except Exception as e:
                logger.error(f"❌ Critical Worker Loop Error: {e}")
                self.error_count += 1
                next_sleep = min(max_interval, max(dynamic_base, int(next_sleep * 1.5)))
            
            jitter_window = next_sleep * jitter_ratio
            jittered_sleep = next_sleep + random.uniform(-jitter_window, jitter_window)
            jittered_sleep = max(min_interval, min(max_interval, int(jittered_sleep)))
            logger.info(
                f"🕒 Social next cycle in {jittered_sleep}s (target={next_sleep}s, tr_hour={tr_hour}, prime={in_prime_hours})"
            )
            time.sleep(jittered_sleep)

if __name__ == "__main__":
    worker = SocialWorker()
    worker.run()