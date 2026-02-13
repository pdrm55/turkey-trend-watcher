import os
import requests
import logging

# تنظیمات لاگر
logger = logging.getLogger(__name__)

class AlertService:
    """
    سرویس اطلاع‌رسانی مرکزی (فاز ۵.۱)
    وظیفه: مدیریت ارسال پیام به ادمین و انتشار در کانال عمومی.
    """
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.admin_id = os.getenv("ADMIN_CHAT_ID")
        self.channel_id = os.getenv("PUBLIC_CHANNEL_ID")
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"

    def _send(self, method, payload):
        """متد کمکی برای ارسال درخواست به API تلگرام"""
        if not self.bot_token:
            logger.error("Telegram Bot Token is missing in .env")
            return False
        
        try:
            response = requests.post(f"{self.api_url}/{method}", json=payload, timeout=15)
            result = response.json()
            if not result.get("ok"):
                logger.error(f"Telegram API Error: {result.get('description')}")
            return result.get("ok", False)
        except Exception as e:
            logger.error(f"Failed to connect to Telegram: {e}")
            return False

    def send_admin_alert(self, title, tps, trajectory):
        """ارسال هشدار به چت خصوصی ادمین (فاز ۵.۲)"""
        if not self.admin_id: return False
        
        icon = "🚀" if trajectory == "up" else "🔥"
        msg = (
            f"🚨 <b>TrendiaTR Detection</b>\n\n"
            f"📌 <b>Topic:</b> {title}\n"
            f"{icon} <b>Score:</b> {tps:.1f} TPS\n"
            f"📈 <b>Status:</b> Acceleration detected!"
        )
        return self._send("sendMessage", {
            "chat_id": self.admin_id, 
            "text": msg, 
            "parse_mode": "HTML"
        })

    def publish_to_channel(self, title, summary, category, url):
        """انتشار خودکار در کانال عمومی (فاز ۵.۳)"""
        if not self.channel_id: return False
        
        # انتخاب ایموجی بر اساس دسته‌بندی
        cat_icons = {
            "Siyaset": "🏛️", "Ekonomi": "💰", "Spor": "⚽", 
            "Teknoloji": "💻", "Sanat": "🎨", "Gündem": "📢"
        }
        icon = cat_icons.get(category, "🔹")
        
        msg = (
            f"{icon} <b>{category.upper()}</b> | {title}\n\n"
            f"{summary[:400]}...\n"
        )
        
        payload = {
            "chat_id": self.channel_id,
            "text": msg,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "🚀 Haberin Tamamını Oku", "url": url}
                ]]
            }
        }
        return self._send("sendMessage", payload)

# نمونه‌سازی تکی (Singleton)
alert_service = AlertService()