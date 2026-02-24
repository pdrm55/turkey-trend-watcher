import os
import requests
import logging

# Configure module logger
logger = logging.getLogger(__name__)

def notify_admin_x_draft(trend_title, tps_score, generation_type="Auto"):
    """
    Sends a Telegram notification to the admin when a new X Draft is generated.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("ADMIN_CHAT_ID")
    base_url = os.getenv("BASE_SITE_URL", "https://trendiatr.com")
    
    if not bot_token or not chat_id:
        logger.warning("⚠️ Telegram notification skipped: Missing config.")
        return

    message = (
        f"🐦 <b>Yeni 𝕏 Draft Hazır!</b>\n\n"
        f"📌 {trend_title}\n"
        f"🔥 TPS: {tps_score}\n"
        f"⚙️ Type: {generation_type}\n\n"
        f"👉 <a href='{base_url}/admin/x-studio'>X Studio'yu Aç</a>"
    )

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code != 200:
            logger.error(f"❌ Telegram API Error: {response.text}")
    except Exception as e:
        logger.error(f"❌ Failed to send Telegram notification: {e}")