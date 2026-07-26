import sys
import os
import re
import time
import random
import feedparser
from datetime import datetime, timezone, timedelta
from dateutil import parser as date_parser

# Add project root to sys path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.database.models import SessionLocal, RawNews, Trend, TrendArrivals
from app.core.ai_engine import ai_engine
from app.config import Config
# نکته مهم: ماژول scoring کامل حذف نشد، فقط get_source_tier نگه داشته شد، محاسبه‌گر TPS حذف شد
from app.core.scoring import get_source_tier
from app.core.text_utils import slugify_turkish
from app.core.classifier import fast_classify
from app.core.scoring_queue import scoring_queue, ScoringQueue

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
    """Loads source name and URL pairs from rss_sources.txt (supports both old and new 5-column format)."""
    sources = {}
    if not os.path.exists(RSS_FILE):
        return {}

    try:
        with open(RSS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 2:
                    sources[parts[0]] = parts[1]
    except Exception as e:
        print(f"⚠️ Error loading RSS sources: {e}")

    return sources


def load_rss_sources_tiered(filepath: str = RSS_FILE) -> dict:
    """Returns RSS sources grouped by polling speed tier.

    Returns dict with keys 'breaking', 'fast', 'standard', each a list of (name, url) tuples.
    Sources with category='afet' are always promoted to breaking tier.
    Old 2-column format lines fall back to 'standard'.
    """
    groups: dict = {"breaking": [], "fast": [], "standard": []}
    speed_map = {"1": "breaking", "2": "fast", "3": "standard"}

    if not os.path.exists(filepath):
        return groups

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 5:
                    name, url, _tier, category, speed = parts[0], parts[1], parts[2], parts[3], parts[4]
                    group = "breaking" if category == "afet" else speed_map.get(speed, "standard")
                elif len(parts) >= 2:
                    name, url = parts[0], parts[1]
                    group = "standard"
                else:
                    continue
                groups[group].append((name, url))
    except Exception as e:
        print(f"⚠️ Error loading tiered RSS sources: {e}")

    return groups

def fetch_and_process_rss(sources_override: dict = None):
    """Executes a single cycle of RSS fetching, clustering, and queuing for scoring.

    sources_override: optional dict of {name: url} to poll instead of the full source file.
    """
    db = SessionLocal()
    try:
        return _run_rss_cycle(db, sources_override)
    finally:
        # The close() used to sit after an unguarded commit at the end of the body,
        # so any commit failure (slug collision, deadlock) propagated to main() and
        # leaked the session plus its pooled connection. QueuePool is 5+10; enough
        # of those and the worker wedges — this showed up as `idle in transaction`.
        db.close()


def _run_rss_cycle(db, sources_override: dict = None):
    rss_feeds = sources_override if sources_override is not None else load_rss_sources()
    current_time_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    
    print(f"🔄 RSS Cycle Started: Checking {len(rss_feeds)} feeds...")
    
    new_trends_count = 0
    signal_updates_count = 0
    batch_size = max(1, getattr(Config, "INGEST_WRITE_BATCH_SIZE", 25))
    pending_writes = 0
    pending_queue_jobs = []

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

                # Extract Media from RSS (multi-stage image extraction)
                media_url = None
                if 'media_content' in entry and len(entry.media_content) > 0:
                    media_url = entry.media_content[0].get('url')
                elif 'links' in entry:
                    for link_item in entry.links:
                        if link_item.get('type', '').startswith('image/'):
                            media_url = link_item.get('href')
                            break
                # Stage 3: <image href="..."> inside entry (some Atom feeds)
                if not media_url:
                    img_obj = getattr(entry, 'image', None)
                    if img_obj and isinstance(img_obj, dict):
                        media_url = img_obj.get('href') or img_obj.get('url')
                # Stage 4: first enclosure that is an image
                if not media_url:
                    for enc in getattr(entry, 'enclosures', []):
                        if enc.get('type', '').startswith('image/') or enc.get('href', '').lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                            media_url = enc.get('href') or enc.get('url')
                            break
                # Stage 5: first <img src> in the summary HTML
                if not media_url and summary:
                    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary, re.IGNORECASE)
                    if m:
                        media_url = m.group(1)
                
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
                    try:
                        db.flush()
                        new_trends_count += 1
                    except Exception:
                        # Race condition: session state after rollback caused a missed lookup.
                        # The trend already exists in DB — fetch and update it instead.
                        db.rollback()
                        trend = db.query(Trend).filter(Trend.cluster_id == cluster_id).first()
                        if not trend:
                            continue
                        trend.message_count += 1
                        trend.last_updated = max(trend.last_updated, actual_pub_time)
                        trend.needs_scoring = True
                        signal_updates_count += 1
                
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
                pending_writes += 1

                queue_priority = ScoringQueue.BREAKING if source_tier <= 2 else ScoringQueue.NORMAL
                pending_queue_jobs.append((trend.id, queue_priority))

                if pending_writes >= batch_size:
                    db.commit()
                    for trend_id, priority in pending_queue_jobs:
                        if not scoring_queue.enqueue(trend_id, priority):
                            print(f"⚠️ Queue enqueue skipped for trend {trend_id} (RSS).")
                    pending_queue_jobs.clear()
                    pending_writes = 0

                # فاز ۶.۲: حذف محاسبه همزمان TPS. ورکر پس‌زمینه این کار را انجام می‌دهد.

            # Commit at the feed boundary so a later feed's failure cannot roll this
            # one back. The rollback below wraps the whole feed loop, and pending
            # writes used to span up to `batch_size` items across several feeds —
            # one bad feed discarded all of them. Their vectors were already in
            # ChromaDB, leaving cluster_ids with no Trend row.
            if pending_writes > 0:
                db.commit()
                for trend_id, priority in pending_queue_jobs:
                    if not scoring_queue.enqueue(trend_id, priority):
                        print(f"⚠️ Queue enqueue skipped for trend {trend_id} (RSS).")
                pending_queue_jobs.clear()
                pending_writes = 0

        except Exception as e:
            db.rollback()
            pending_queue_jobs.clear()
            pending_writes = 0
            print(f"   ❌ Error processing feed {source_name}: {e}")

    if pending_writes > 0:
        db.commit()
        for trend_id, priority in pending_queue_jobs:
            if not scoring_queue.enqueue(trend_id, priority):
                print(f"⚠️ Queue enqueue skipped for trend {trend_id} (RSS).")

    print(f"✅ RSS Cycle Finished: {new_trends_count} New Trends, {signal_updates_count} Signal Updates.")
    return new_trends_count, signal_updates_count

def main():
    """Main worker loop for the RSS Engine — tiered polling (breaking/fast/standard)."""
    print("🧠 TrendiaTR RSS Fetcher Active (Tiered Mode).")

    TIER_BASE_INTERVALS = {
        "breaking": int(os.getenv("RSS_BREAKING_INTERVAL_SECONDS", "60")),
        "fast":     int(os.getenv("RSS_FAST_INTERVAL_SECONDS",     "300")),
        "standard": int(os.getenv("RSS_STANDARD_INTERVAL_SECONDS", "900")),
    }
    jitter_ratio   = min(0.5, max(0.0, getattr(Config, "RSS_POLL_JITTER_RATIO", 0.15)))
    startup_stagger = max(0, getattr(Config, "RSS_STARTUP_STAGGER_SECONDS", 20))
    prime_start    = max(0, min(23, getattr(Config, "RSS_PRIME_START_HOUR", 7)))
    prime_end      = max(0, min(23, getattr(Config, "RSS_PRIME_END_HOUR", 23)))

    tiered = load_rss_sources_tiered()
    total  = sum(len(v) for v in tiered.values())
    print(
        f"⚙️ Loaded {total} sources — "
        f"breaking={len(tiered['breaking'])}, fast={len(tiered['fast'])}, standard={len(tiered['standard'])}"
    )
    print(
        f"⚙️ Poll intervals — "
        f"breaking={TIER_BASE_INTERVALS['breaking']}s, "
        f"fast={TIER_BASE_INTERVALS['fast']}s, "
        f"standard={TIER_BASE_INTERVALS['standard']}s, jitter={jitter_ratio}"
    )

    if startup_stagger > 0:
        initial_delay = random.uniform(0, startup_stagger)
        print(f"⏳ RSS startup stagger: {initial_delay:.1f}s")
        time.sleep(initial_delay)

    # next_run[tier] = wall-clock time when the tier is next due; 0 → run immediately
    next_run = {tier: 0.0 for tier in TIER_BASE_INTERVALS}
    # adaptive interval per tier (seconds); starts at base
    cur_interval = {tier: float(iv) for tier, iv in TIER_BASE_INTERVALS.items()}

    while True:
        now = time.time()
        tr_hour = datetime.now(timezone(timedelta(hours=3))).hour
        in_prime_hours = prime_start <= tr_hour <= prime_end

        for tier, base_interval in TIER_BASE_INTERVALS.items():
            if now < next_run[tier]:
                continue

            sources = tiered.get(tier, [])
            if not sources:
                next_run[tier] = now + base_interval
                continue

            sources_dict = {name: url for name, url in sources}
            try:
                new_t, sig_t = fetch_and_process_rss(sources_dict)

                # In prime hours, halve the effective interval for fast + breaking tiers
                effective_base = (base_interval // 2) if (in_prime_hours and tier in ("breaking", "fast")) else base_interval

                if (new_t + sig_t) > 0:
                    cur_interval[tier] = float(effective_base)
                else:
                    # Back off gradually when nothing new was found
                    cur_interval[tier] = min(base_interval * 3, cur_interval[tier] * 1.25)
            except Exception as e:
                print(f"❌ Critical error in [{tier}] tier RSS fetch: {e}")
                cur_interval[tier] = min(base_interval * 3, cur_interval[tier] * 1.5)

            jitter_window = cur_interval[tier] * jitter_ratio
            sleep_secs = cur_interval[tier] + random.uniform(-jitter_window, jitter_window)
            sleep_secs = max(float(TIER_BASE_INTERVALS["breaking"]), sleep_secs)
            next_run[tier] = now + sleep_secs
            print(
                f"🕒 [{tier}] next run in {sleep_secs:.0f}s "
                f"(tr_hour={tr_hour}, prime={in_prime_hours})"
            )

        # Tight loop: check tiers every 5 seconds
        time.sleep(5)

if __name__ == "__main__":
    main()