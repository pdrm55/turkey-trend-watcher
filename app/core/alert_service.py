import os
import requests
import logging
import json
from app.config import Config

logger = logging.getLogger(__name__)

class AlertService:
    """
    سرویس مدیریت اعلان‌ها - نسخه ارتقا یافته فاز ۶
    مدیریت هشدارهای ادمین با قابلیت دکمه‌های شیشه‌ای (Inline Keyboard)
    """
    def __init__(self):
        self.bot_token = Config.TELEGRAM_BOT_TOKEN
        self.admin_id = Config.ADMIN_CHAT_ID
        self.channel_id = Config.PUBLIC_CHANNEL_ID
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"

    def _send(self, method, payload):
        """ارسال درخواست به API تلگرام با مدیریت خطا"""
        if not self.bot_token:
            logger.error("خطا: توکن بات تلگرام در تنظیمات یافت نشد.")
            return None
        
        try:
            response = requests.post(f"{self.api_url}/{method}", json=payload, timeout=15)
            result = response.json()
            if not result.get("ok"):
                logger.error(f"Telegram API Error: {result.get('description')}")
            return result
        except Exception as e:
            logger.error(f"اتصال به تلگرام برقرار نشد: {e}")
            return None

    def send_admin_alert(self, title, tps, trajectory, cluster_id):
        """ارسال هشدار تعاملی به ادمین (حاوی دکمه‌های تایید و حذف)"""
        if not self.admin_id: return False
        
        # تعیین ایموجی وضعیت
        icon = "⏫" if trajectory == "up" else "🔥"
        
        msg = (
            f"🚨 <b>سیگنال جدید شناسایی شد</b>\n\n"
            f"📌 <b>موضوع:</b> {title}\n"
            f"{icon} <b>امتیاز:</b> {tps:.1f} TPS\n"
            f"📈 <b>وضعیت حرکت:</b> {trajectory.upper()}\n\n"
            f"<i>مایل به انتشار این خبر هستید؟</i>"
        )

        # دکمه‌های تعاملی برای ادمین
        payload = {
            "chat_id": self.admin_id,
            "text": msg,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "✅ تایید و انتشار", "callback_data": f"pub_{cluster_id}"},
                        {"text": "🗑️ حذف و نادیده گرفتن", "callback_data": f"del_{cluster_id}"}
                    ],
                    [
                        {"text": "📝 مشاهده جزئیات در سایت", "url": f"{Config.BASE_SITE_URL}/trend/{cluster_id}"}
                    ]
                ]
            }
        }
        return self._send("sendMessage", payload)

    def publish_to_channel(self, title, summary, category, url):
        """انتشار خبر در کانال عمومی تلگرام"""
        if not self.channel_id: return False
        
        cat_icons = {
            "Siyaset": "🏛️", "Ekonomi": "💰", "Spor": "⚽", 
            "Teknoloji": "💻", "Sanat": "🎨", "Gündem": "📢"
        }
        icon = cat_icons.get(category, "🔹")
        
        msg = (
            f"{icon} <b>{category.upper()}</b> | {title}\n\n"
            f"{summary[:450]}...\n"
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

alert_service = AlertService()