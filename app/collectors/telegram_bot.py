import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

# Add project root to sys path for internal module access
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest
from app.config import Config
from app.database.models import SessionLocal, RawNews, Trend, TrendArrivals
from app.core.ai_engine import ai_engine
# نکته مهم فاز ۶.۲: ماژول scoring را از اینجا حذف کردیم چون پردازش آسنکرون شده است
from app.core.scoring import get_source_tier
from app.core.text_utils import slugify_turkish
from app.core.classifier import fast_classify
from app.core.scoring_queue import scoring_queue, ScoringQueue

# Path for the monitored channels list
CHANNELS_FILE = os.path.join(os.path.dirname(__file__), 'channels.txt')
monitored_usernames = set()

async def update_channels_from_file(client):
    """
    Reads channels.txt and joins any new channels automatically.
    This allows adding channels without restarting the bot.
    """
    if not os.path.exists(CHANNELS_FILE):
        return
        
    try:
        with open(CHANNELS_FILE, 'r', encoding='utf-8') as f:
            # Filter comments and empty lines
            file_channels = {l.strip().replace('@', '') for l in f if l.strip() and not l.startswith('#')}
    except Exception as e:
        print(f"⚠️ Error reading channels file: {e}")
        return

    new_channels = file_channels - monitored_usernames
    if not new_channels:
        return

    print(f"\n🔎 Detected {len(new_channels)} new channels in configuration...")
    for ch in new_channels:
        try:
            entity = await client.get_entity(ch)
            try:
                # Attempt to join the channel (no error if already a member)
                await client(JoinChannelRequest(entity))
            except:
                pass
            monitored_usernames.add(ch)
            print(f"   ✅ Successfully monitoring: {ch}")
        except Exception as e:
            print(f"   ❌ Could not resolve entity for {ch}: {e}")

async def file_watcher_loop(client):
    """Background task that watches the channels file for updates every minute"""
    while True:
        await update_channels_from_file(client)
        await asyncio.sleep(60)

def generate_initial_slug(db, text, trend_id=None):
    """
    SEO Logic: Generates a unique, readable slug immediately upon trend creation.
    This prevents UUIDs from appearing in the URLs.
    """
    # Use the first 7 words for a meaningful URL
    words = text.split()[:7]
    base_title = " ".join(words)
    base_slug = slugify_turkish(base_title)
    
    unique_slug = base_slug
    counter = 1
    while True:
        # Check if the slug is already taken by another trend
        existing = db.query(Trend).filter(Trend.slug == unique_slug)
        if trend_id:
            existing = existing.filter(Trend.id != trend_id)
        
        if not existing.first():
            return unique_slug
        
        # If taken, append a counter
        unique_slug = f"{base_slug}-{counter}"
        counter += 1

async def main():
    """Main Telegram Bot entry point"""
    if not Config.TELEGRAM_API_ID: 
        print("❌ Error: TELEGRAM_API_ID not set in .env")
        return

    print("📡 Connecting to Telegram using stored session...")
    # 'ttw_session' is the SQLite file storing the authentication token
    # connection_retries=None → retry forever instead of giving up after 5 attempts
    # (a transient blip used to crash the whole worker). auto_reconnect keeps the
    # session alive across short network drops.
    client = TelegramClient(
        'ttw_session',
        Config.TELEGRAM_API_ID,
        Config.TELEGRAM_API_HASH,
        connection_retries=None,
        retry_delay=5,
        auto_reconnect=True,
    )
    
    try:
        # Interactive start for initial login (phone and code required)
        await client.start(phone=Config.TELEGRAM_PHONE)
        print("✅ Telegram Authenticated Successfully!")
    except EOFError:
        print("❌ Auth Error: Interactive terminal required for initial login.")
        return
    except Exception as e:
        print(f"❌ Telegram Connection Error: {e}")
        return

    # Initialize file monitoring task
    asyncio.create_task(file_watcher_loop(client))

    @client.on(events.NewMessage())
    async def new_message_handler(event):
        """Processes every incoming message from monitored sources"""
        if not event.message.message:
            return
        
        try:
            chat = await event.get_chat()
            if not chat: return
            
            # Identify the source username or title
            ch_id = getattr(chat, 'username', None) or getattr(chat, 'title', None)
            if not ch_id: return
            
            # Security filter: Only process messages from the monitored list
            if ch_id not in monitored_usernames:
                return

            db = SessionLocal()
            try:
                raw_text = event.message.message
                # Junk filter for very short messages
                if len(raw_text.strip()) < 20:
                    return

                # Construct unique link for source tracking
                unique_id = f"https://t.me/{ch_id}/{event.message.id}"
                
                video_path_db = None
                if getattr(event.message, 'video', None):
                    try:
                        now_utc = datetime.now(timezone.utc)
                        year, month, day = now_utc.strftime("%Y"), now_utc.strftime("%m"), now_utc.strftime("%d")
                        # Ensure path is correct for the docker container
                        vid_folder = os.path.join("/app/app/static/media/videos", year, month, day)
                        os.makedirs(vid_folder, exist_ok=True)
                        
                        vid_filename = f"vid_{uuid.uuid4().hex}.mp4"
                        final_vid_full_path = os.path.join(vid_folder, vid_filename)
                        
                        print(f"📥 Downloading video from {ch_id}...")
                        await client.download_media(event.message.video, file=final_vid_full_path)
                        video_path_db = f"media/videos/{year}/{month}/{day}/{vid_filename}"
                    except Exception as e:
                        print(f"⚠️ Failed to download video: {e}")

                # --- Step 1: AI Clustering ---
                cluster_id, is_duplicate = ai_engine.process_news(raw_text, ch_id, unique_id)
                if not cluster_id: return

                msg_time = datetime.now(timezone.utc).replace(tzinfo=None)

                # --- Step 2: Trend Management & SEO Slugging ---
                trend = db.query(Trend).filter(Trend.cluster_id == cluster_id).first()
                
                if trend:
                    trend.message_count += 1
                    trend.last_updated = msg_time
                    trend.needs_scoring = True # ASYNC TRIGGER: پرچم‌گذاری برای محاسبه در ورکر پس‌زمینه
                    if video_path_db and not trend.video_path:
                        trend.video_path = video_path_db
                    action = "📈 Signal Added"
                else:
                    # New trend detected: Create initial headline and SEO slug immediately
                    initial_title = raw_text[:70].strip() + "..."
                    
                    # Layer 0: Instant Classification
                    initial_category = fast_classify(raw_text)
                    
                    trend = Trend(
                        cluster_id=cluster_id,
                        message_count=1,
                        title=initial_title,
                        slug=generate_initial_slug(db, raw_text), # SEO-First logic
                        category=initial_category,
                        first_seen=msg_time,
                        last_updated=msg_time,
                        needs_scoring=True, # ASYNC TRIGGER: پرچم‌گذاری برای محاسبه اولیه
                        video_path=video_path_db
                    )
                    db.add(trend)
                    db.flush() # Secure the trend.id
                    action = f"✨ Trend Created [{initial_category}]"
                
                # --- Step 3: Raw News Persistence ---
                source_tier = get_source_tier(ch_id)
                news_item = RawNews(
                    source_type="telegram",
                    source_name=ch_id,
                    source_tier=source_tier,
                    external_id=unique_id,
                    content=raw_text,
                    published_at=msg_time,
                    trend_id=trend.id,
                    video_path=video_path_db
                )
                db.add(news_item)
                db.flush()

                # --- Step 4: Record Arrival for Velocity Calculation ---
                arrival = TrendArrivals(
                    trend_id=trend.id,
                    raw_news_id=news_item.id,
                    timestamp=msg_time
                )
                db.add(arrival)
                db.commit()

                queue_priority = ScoringQueue.BREAKING if source_tier <= 2 else ScoringQueue.NORMAL
                if not scoring_queue.enqueue(trend.id, queue_priority):
                    print(f"⚠️ Queue enqueue skipped for trend {trend.id} (Telegram).")

                # فاز ۶.۲: حذف کامل فراخوانی مستقیم scoring برای افزایش سرعت دریافت
                print(f"{action}: [{ch_id}] (Tier {source_tier}) | Queued for Scoring.")

            except Exception as e:
                db.rollback()
                print(f"❌ Telegram DB Error: {e}")
            finally:
                db.close()

        except Exception as e:
            print(f"❌ Event Loop Error: {e}")

    # Keep the client running. Wrap in a reconnect loop so a dropped/refused
    # connection reconnects in-process instead of crashing the worker (the
    # 2026-06-16 outage: telethon gave up after 5 failures and run_until_disconnected
    # raised ConnectionError, killing the process for ~a day).
    while True:
        try:
            await client.run_until_disconnected()
            print("⚠️ Telegram disconnected cleanly; reconnecting in 10s...")
            await asyncio.sleep(10)
        except Exception as e:
            print(f"⚠️ Telegram connection lost ({e}); reconnecting in 15s...")
            await asyncio.sleep(15)
        try:
            if not client.is_connected():
                await client.connect()
            if await client.is_user_authorized():
                print("✅ Telegram reconnected.")
            else:
                print("❌ Session no longer authorized — needs re-login; exiting for restart.")
                return
        except Exception as e:
            print(f"❌ Reconnect attempt failed ({e}); will retry.")

if __name__ == "__main__":
    asyncio.run(main())