import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest
from app.config import Config
from app.database.models import SessionLocal, RawNews, Trend
from app.core.ai_engine import AIEngine

CHANNELS_FILE = os.path.join(os.path.dirname(__file__), 'channels.txt')
monitored_usernames = set()

async def update_channels_from_file(client):
    if not os.path.exists(CHANNELS_FILE): return
    try:
        with open(CHANNELS_FILE, 'r', encoding='utf-8') as f:
            file_channels = {l.strip().replace('@', '') for l in f if l.strip() and not l.startswith('#')}
    except: return

    new_channels = file_channels - monitored_usernames
    if not new_channels: return

    print(f"\n🔎 Detected {len(new_channels)} new channels...")
    for ch in new_channels:
        try:
            entity = await client.get_entity(ch)
            try: await client(JoinChannelRequest(entity))
            except: pass
            monitored_usernames.add(ch)
            print(f"   ✅ Monitoring: {ch}")
        except: pass

async def file_watcher_loop(client):
    while True:
        await update_channels_from_file(client)
        await asyncio.sleep(60)

async def main():
    if not Config.TELEGRAM_API_ID: 
        print("❌ Error: TELEGRAM_API_ID not set!")
        return

    # ۱. اول اتصال تلگرام و دریافت کد (قبل از سنگین شدن برنامه با هوش مصنوعی)
    print("📡 Connecting to Telegram...")
    client = TelegramClient('ttw_session', Config.TELEGRAM_API_ID, Config.TELEGRAM_API_HASH)
    
    try:
        await client.start(phone=Config.TELEGRAM_PHONE)
        print("✅ Telegram Authenticated Successfully!")
    except Exception as e:
        print(f"❌ Telegram Auth Error: {e}")
        return

    # ۲. حالا که لاگین شدیم، مغز هوش مصنوعی را لود می‌کنیم
    print("🧠 Initializing AI Brain (Loading Models)...")
    ai_engine = AIEngine()
    print("✅ System Fully Active!")

    asyncio.create_task(file_watcher_loop(client))

    @client.on(events.NewMessage())
    async def new_message_handler(event):
        if not event.message.message: return
        chat = await event.get_chat()
        if not chat or (not chat.username and not chat.title): return
        
        ch_id = chat.username or chat.title
        is_monitored = (chat.username and chat.username in monitored_usernames) or (chat.title and chat.title in monitored_usernames)
        if not is_monitored: return

        db = SessionLocal()
        try:
            raw_text = event.message.message
            if len(raw_text.strip()) < 15: return

            unique_id = f"https://t.me/{ch_id}/{event.message.id}"
            
            cluster_id, is_duplicate = ai_engine.process_news(raw_text, ch_id, unique_id)
            if not cluster_id: return

            msg_time = datetime.now(timezone.utc).replace(tzinfo=None)

            trend = db.query(Trend).filter(Trend.cluster_id == cluster_id).first()
            
            if trend:
                trend.message_count += 1
                trend.last_updated = msg_time
                trend.score += 10
                action = "📈 Updated"
            else:
                trend = Trend(
                    cluster_id=cluster_id,
                    message_count=1,
                    score=10.0,
                    title=raw_text[:50] + "...",
                    first_seen=msg_time,
                    last_updated=msg_time
                )
                db.add(trend)
                action = "✨ New Trend"
            
            db.commit()
            
            news_item = RawNews(
                source_type="telegram",
                source_name=ch_id,
                external_id=unique_id,
                content=raw_text,
                published_at=msg_time,
                trend_id=trend.id
            )
            db.add(news_item)
            db.commit()
            
            print(f"{action}: [{ch_id}] {raw_text[:30]}...")

        except Exception as e:
            db.rollback()
            print(f"Error: {e}")
        finally:
            db.close()

    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())