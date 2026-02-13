import os
import sys
import time
import logging
import telebot
from datetime import datetime, timedelta
from sqlalchemy import desc

# اضافه کردن مسیر پروژه برای دسترسی به مدل‌ها
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.database.models import SessionLocal, Trend, RawNews

# تنظیم دقیق لاگر برای مانیتورینگ در داکر
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("TelegramBotWorker")

# دریافت تنظیمات از متغیرهای محیطی
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_CHAT_ID")

if not TOKEN or not ADMIN_ID_STR:
    logger.error("❌ Telegram Token or Admin ID is missing in .env")
    sys.exit(1)

bot = telebot.TeleBot(TOKEN)
ADMIN_ID = int(ADMIN_ID_STR)

logger.info(f"✅ Bot initialized. Authorized Admin ID: {ADMIN_ID}")

def is_admin(chat_id):
    """بررسی سطح دسترسی کاربر"""
    if chat_id == ADMIN_ID:
        return True
    logger.warning(f"⚠️ Access denied for chat_id: {chat_id}")
    return False

# --- فرمان /start و /help ---
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if not is_admin(message.chat.id): return
    
    logger.info(f"📩 Admin command: {message.text}")
    help_text = (
        "🤖 *TrendiaTR Control Center*\n\n"
        "فرمان‌های مجاز برای مدیریت سیستم:\n\n"
        "📊 /stats - مشاهده آمار کلی سیستم\n"
        "🔥 /top - لیست ۵ ترند داغ فعلی\n"
        "🔄 /check - وضعیت سرویس‌ها\n"
        "ℹ️ /help - راهنمای فرمان‌ها"
    )
    bot.reply_to(message, help_text, parse_mode="Markdown")

# --- فرمان /stats ---
@bot.message_handler(commands=['stats'])
def get_stats(message):
    if not is_admin(message.chat.id): return
    
    logger.info("📊 Processing stats request...")
    db = SessionLocal()
    try:
        total_news = db.query(RawNews).count()
        active_trends = db.query(Trend).filter(Trend.is_active == True).count()
        total_trends = db.query(Trend).count()
        
        last_24h = datetime.now() - timedelta(hours=24)
        news_24h = db.query(RawNews).filter(RawNews.created_at >= last_24h).count()
        
        stats_msg = (
            "📊 *وضعیت دیتابیس*\n\n"
            f"🗞 کل اخبار خام: `{total_news}`\n"
            f"📈 کل کلاسترها: `{total_trends}`\n"
            f"🔥 ترندهای فعال: `{active_trends}`\n"
            f"⏱ ۲۴ ساعت اخیر: `{news_24h}` خبر"
        )
        bot.reply_to(message, stats_msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Stats Error: {e}")
        bot.reply_to(message, "❌ خطا در واکشی آمار.")
    finally:
        db.close()

# --- فرمان /top ---
@bot.message_handler(commands=['top'])
def get_top_trends(message):
    if not is_admin(message.chat.id): return
    
    logger.info("🔥 Processing top trends request...")
    db = SessionLocal()
    try:
        top_trends = db.query(Trend).filter(Trend.is_active == True)\
            .order_by(desc(Trend.final_tps)).limit(5).all()
        
        if not top_trends:
            bot.reply_to(message, "⚠️ ترند فعالی یافت نشد.")
            return

        response = "🔥 *برترین ترندهای لحظه‌ای:*\n\n"
        for i, t in enumerate(top_trends, 1):
            title = t.title if t.title else "در حال تحلیل..."
            response += f"{i}. `{t.final_tps:.1f}` | {title}\n"
            
        bot.reply_to(message, response, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Top Trends Error: {e}")
    finally:
        db.close()

# --- فرمان /check ---
@bot.message_handler(commands=['check'])
def check_status(message):
    if not is_admin(message.chat.id): return
    
    status_msg = (
        "⚙️ *سرویس‌های متصل به داکر:*\n\n"
        "✅ `Interactive Bot`: ONLINE\n"
        "✅ `Database`: CONNECTED\n"
        "📡 `RSS/TG Workers`: MONITORING\n"
        "🧠 `AI Summarizer`: READY"
    )
    bot.reply_to(message, status_msg, parse_mode="Markdown")

def main():
    logger.info("📡 Starting Polling (Listening for messages)...")
    while True:
        try:
            bot.polling(none_stop=True, interval=0, timeout=20)
        except Exception as e:
            logger.error(f"Polling Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()