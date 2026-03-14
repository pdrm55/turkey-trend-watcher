import os
import requests
import re
import logging
import json
from app.config import Config

# تنظیمات لاگر
logger = logging.getLogger(__name__)

class AlertService:
    """
    سرویس مدیریت اعلان‌ها - نسخه کامل فاز ۶ (بدون حذفیات)
    وظیفه: مدیریت ارسال پیام به ادمین و انتشار در کانال عمومی.
    """
    def __init__(self):
        # دریافت اطلاعات از کانفیگ مرکزی
        self.bot_token = Config.TELEGRAM_BOT_TOKEN
        self.admin_id = Config.ADMIN_CHAT_ID
        self.channel_id = Config.PUBLIC_CHANNEL_ID
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"

    def _send(self, method, payload):
        """متد داخلی برای ارسال درخواست به API تلگرام"""
        if not self.bot_token:
            logger.error("خطا: TELEGRAM_BOT_TOKEN در فایل .env یافت نشد.")
            return None
        
        try:
            response = requests.post(f"{self.api_url}/{method}", json=payload, timeout=15)
            result = response.json()
            if not result.get("ok"):
                logger.error(f"خطای تلگرام: {result.get('description')}")
            return result
        except Exception as e:
            logger.error(f"عدم توانایی در اتصال به تلگرام: {e}")
            return None

    def _format_for_telegram(self, text):
        """Converts Markdown to Telegram HTML and handles specific headers."""
        if not text: return ""

        # 1. Header Replacements
        text = text.replace("### ⚡ Özet", "⚡ <b>Özet</b>")
        text = text.replace("### 🤖 Yapay Zeka Analizi", "🤖 <b>Analiz:</b>")
        
        # 2. Generic Sub-headers (### Title -> 🔹 Title)
        text = re.sub(r'###\s*(.+)', r'🔹 <b>\1</b>', text)

        # 3. Formatting Replacements
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)  # **bold** -> <b>bold</b>
        text = re.sub(r'^>\s*(.+)', r'<i>\1</i>', text, flags=re.MULTILINE)  # > quote -> <i>quote</i>
        
        # 4. Cleanup
        text = text.replace("###", "") # Remove leftover markdown headers
        return text.strip()

    def send_admin_alert(self, title, tps, trajectory, cluster_id):
        """
        ارسال هشدار به ادمین.
        تغییر فاز ۶: انتشار خودکار است، لذا دکمه‌های تایید غیرفعال (مخفی) شدند.
        دکمه مشاهده در سایت برای بررسی سریع ادمین باقی مانده است.
        """
        if not self.admin_id: return False
        
        icon = "⏫" if trajectory == "up" else "🔥"
        msg = (
            f"🚨 <b>سیگنال جدید شناسایی شد</b>\n\n"
            f"📌 <b>موضوع:</b> {title}\n"
            f"{icon} <b>امتیاز:</b> {tps:.1f} TPS\n"
            f"📈 <b>وضعیت:</b> {trajectory.upper()}\n\n"
            f"✅ <i>این خبر طبق تنظیمات جدید، به صورت خودکار منتشر می‌شود.</i>"
        )

        # ساخت دکمه‌های شیشه‌ای
        payload = {
            "chat_id": self.admin_id,
            "text": msg,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    # دکمه‌های تایید/حذف برای استفاده در آینده (در صورت نیاز به فعال‌سازی مجدد) کامنت شدند
                    # [
                    #     {"text": "✅ تایید دستی", "callback_data": f"pub_{cluster_id}"},
                    #     {"text": "🗑️ حذف ترند", "callback_data": f"del_{cluster_id}"}
                    # ],
                    [
                        {"text": "📝 مشاهده در سایت", "url": f"{Config.BASE_SITE_URL}/trend/{cluster_id}"}
                    ]
                ]
            }
        }
        return self._send("sendMessage", payload)

    def publish_to_channel(self, title, summary, category, url, image_path=None):
        """انتشار خبر در کانال عمومی تلگرام (اتوماسیون کامل)"""
        if not self.channel_id: return False
        
        # Icons
        cat_icons = {
            "Siyaset": "🏛️", 
            "Ekonomi": "💰", 
            "Spor": "⚽", 
            "Teknoloji": "💻", 
            "Sanat": "🎨", 
            "Gündem": "📢"
        }
        icon = cat_icons.get(category, "🔹")
        
        # Format content
        formatted_summary = self._format_for_telegram(summary)
        
        # Define Header
        header = f"{icon} <b>{category.upper()}</b> | <b>{title}</b>\n\n"
        
        # Smart Truncation Logic (Caption limit: 1024, Text limit: 4096)
        # We target ~950 for captions to be safe with HTML tags
        limit = 950 if image_path else 4000
        
        full_msg = header + formatted_summary
        
        if len(full_msg) > limit:
            # Truncate while trying to preserve line integrity
            available_chars = limit - len(header) - 5
            lines = formatted_summary.split('\n')
            truncated_body = ""
            
            for line in lines:
                if len(truncated_body) + len(line) + 2 < available_chars:
                    truncated_body += line + "\n"
                else:
                    truncated_body += "..."
                    break
            full_msg = header + truncated_body.strip()
            
        
        reply_markup = {
            "inline_keyboard": [[
                {"text": "🚀 Haberin Tamamını Oku", "url": url}
            ]]
        }

        if image_path:
            full_image_url = f"{Config.BASE_SITE_URL}/static/{image_path}"
            payload = {
                "chat_id": self.channel_id,
                "photo": full_image_url,
                "caption": full_msg,
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
            return self._send("sendPhoto", payload)
        else:
            payload = {
                "chat_id": self.channel_id,
                "text": full_msg,
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
            return self._send("sendMessage", payload)

# نمونه‌سازی واحد برای استفاده در کل اپلیکیشن
alert_service = AlertService()