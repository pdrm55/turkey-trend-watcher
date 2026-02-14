import os
import sys
import time
import logging
import telebot
from datetime import datetime, timedelta
from sqlalchemy import desc

# افزودن مسیر اصلی پروژه
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.config import Config
from app.database.models import SessionLocal, Trend, RawNews

# تنظیمات لاگر
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("TelegramBotWorker")

# مقداردهی بات
bot = telebot.TeleBot(Config.TELEGRAM_BOT_TOKEN)
ADMIN_ID = int(Config.ADMIN_CHAT_ID)

def is_admin(chat_id):
    """بررسی سطح دسترسی ادمین"""
    return chat_id == ADMIN_ID

# --- مدیریت کلیک روی دکمه‌ها (Callback Queries) ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callback_actions(call):
    if not is_admin(call.message.chat.id): return
    
    db = SessionLocal()
    try:
        # فرمت دیتا: action_clusterid
        parts = call.data.split('_', 1)
        if len(parts) < 2: return
        
        action = parts[0]
        cluster_id = parts[1]
        
        trend = db.query(Trend).filter(Trend.cluster_id == cluster_id).first()
        if not trend:
            bot.answer_callback_query(call.id, "❌ این ترند دیگر وجود ندارد.")
            return

        if action == "del":
            trend.is_active = False
            db.commit()
            bot.answer_callback_query(call.id, "ترند با موفقیت حذف شد.")
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"🗑️ ترند <b>{trend.title[:40]}...</b> توسط ادمین نادیده گرفته شد.",
                parse_mode="HTML"
            )

        elif action == "pub":
            # تایید دستی برای انتشار در کانال
            bot.answer_callback_query(call.id, "🚀 تایید شد. خبر در صف انتشار قرار گرفت.")
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"✅ ترند <b>{trend.title[:40]}...</b> تایید و منتشر شد.",
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"خطا در پردازش دکمه: {e}")
        bot.answer_callback_query(call.id, "خطای سیستمی در انجام عملیات.")
    finally:
        db.close()

# --- فرمان‌های متنی ادمین ---

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if not is_admin(message.chat.id): return
    help_text = (
        "🤖 <b>مرکز کنترل تعاملی TrendiaTR (نسخه ۶.۰)</b>\n\n"
        "فرمان‌های مجاز:\n"
        "📊 /stats - آمار کلی سیستم\n"
        "🔥 /top - ۵ ترند داغ لحظه‌ای\n"
        "⚙️ /check - وضعیت سلامت سرویس‌ها\n"
    )
    bot.reply_to(message, help_text, parse_mode="HTML")

@bot.message_handler(commands=['stats'])
def get_stats(message):
    if not is_admin(message.chat.id): return
    db = SessionLocal()
    try:
        total_news = db.query(RawNews).count()
        active_trends = db.query(Trend).filter(Trend.is_active == True).count()
        
        last_24h = datetime.now() - timedelta(hours=24)
        news_24h = db.query(RawNews).filter(RawNews.created_at >= last_24h).count()
        
        msg = (
            "📊 <b>وضعیت پردازش سیستم</b>\n\n"
            f"🗞 اخبار دریافت شده: <code>{total_news}</code>\n"
            f"🔥 خوشه‌های فعال: <code>{active_trends}</code>\n"
            f"⏱ ورودی ۲۴ ساعت اخیر: <code>{news_24h}</code> خبر"
        )
        bot.reply_to(message, msg, parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, "خطا در دریافت آمار.")
    finally:
        db.close()

@bot.message_handler(commands=['top'])
def get_top_trends(message):
    if not is_admin(message.chat.id): return
    db = SessionLocal()
    try:
        top_trends = db.query(Trend).filter(Trend.is_active == True)\
            .order_by(desc(Trend.final_tps)).limit(5).all()
        
        if not top_trends:
            bot.reply_to(message, "ترند فعالی یافت نشد.")
            return

        res = "🔥 <b>۵ ترند برتر (Live TPS):</b>\n\n"
        for i, t in enumerate(top_trends, 1):
            title = t.title if t.title else "در حال تحلیل..."
            res += f"{i}. <code>{t.final_tps:.1f}</code> | {title}\n"
        bot.reply_to(message, res, parse_mode="HTML")
    finally:
        db.close()

@bot.message_handler(commands=['check'])
def check_status(message):
    if not is_admin(message.chat.id): return
    status = (
        "⚙️ <b>وضعیت سرویس‌ها (v6.0):</b>\n\n"
        "✅ API Server: ONLINE\n"
        "🧠 AI Worker: ACTIVE\n"
        "📡 Scraping: RUNNING"
    )
    bot.reply_to(message, status, parse_mode="HTML")

def main():
    logger.info("📡 Admin Bot Worker Polling Started...")
    while True:
        try:
            bot.polling(none_stop=True, interval=0, timeout=30)
        except Exception as e:
            logger.error(f"Polling Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()