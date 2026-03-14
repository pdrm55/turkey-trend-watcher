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

    def send_admin_alert(self, title, tps, trajectory, cluster_id):
        """
        ارسال هشدار به ادمین در صورت شناسایی ترند صعودی
        این پیام شامل دکمه‌های شیشه‌ای (Inline Keyboard) است.
        """
        if not self.admin_id:
            return False
            
        trend_emoji = "🔥" if trajectory == "soaring" else "📈"
        msg = (
            f"⚠️ <b>هشدار ترند جدید</b> {trend_emoji}\n\n"
            f"📌 <b>موضوع:</b> {title}\n"
            f"📊 <b>امتیاز (TPS):</b> {tps:.1f}\n"
            f"🚀 <b>وضعیت:</b> {trajectory.upper()}\n"
        )
        
        # ساخت کیبورد شیشه‌ای برای تایید یا رد سریع ترند توسط ادمین
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ تایید انتشار", "callback_data": f"approve_{cluster_id}"},
                    {"text": "❌ رد ترند", "callback_data": f"reject_{cluster_id}"}
                ],
                [
                    {"text": "🔍 مشاهده جزئیات", "callback_data": f"details_{cluster_id}"}
                ]
            ]
        }
        
        payload = {
            "chat_id": self.admin_id,
            "text": msg,
            "parse_mode": "HTML",
            "reply_markup": reply_markup
        }
        
        return self._send("sendMessage", payload)

    def _format_for_telegram(self, text):
        """
        تبدیل مارک‌داون وب به HTML تلگرام طبق منطق:
        - تبدیل تیترهای خاص به ایموجی + بولد
        - تبدیل ستاره به <b> و نقل‌قول به <i>
        - پاکسازی علامت‌های #
        """
        if not text:
            return ""

        # ۱. جایگزینی هدرهای اختصاصی
        text = text.replace("### ⚡️ Özet", "⚡️ <b>Özet</b>")
        text = text.replace("### ⚡ Özet", "⚡️ <b>Özet</b>")
        text = text.replace("### 🤖 Yapay Zeka Analizi", "🤖 <b>Yapay Zeka Analizi</b>")
        
        # ۲. تبدیل سایر تیترهای مارک‌داون به استایل تلگرامی
        text = re.sub(r'###\s+(.*)', r'🔹 <b>\1</b>', text)
        
        # ۳. تبدیل فرمت‌های متنی
        text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
        text = re.sub(r'^>\s+(.*)', r'<i>\1</i>', text, flags=re.MULTILINE)
        
        # ۴. پاکسازی نهایی (حذف هشتگ‌های باقی‌مانده)
        text = text.replace("#", "")
        
        return text.strip()

    def publish_to_channel(self, title, summary, category, url, image_path=None):
        """
        ارسال خبر به کانال با مدیریت هوشمند طول متن و تگ‌های HTML
        """
        if not self.channel_id:
            return False
        
        cat_icons = {
            "Siyaset": "🏛️", "Ekonomi": "💰", "Spor": "⚽", 
            "Teknoloji": "💻", "Sanat": "🎨", "Gündem": "📢"
        }
        icon = cat_icons.get(category, "🔹")
        
        # ساخت بخش‌های پیام
        formatted_summary = self._format_for_telegram(summary)
        header = f"{icon} <b>{category.upper()}</b> | {title}"
        footer = f"🚀 <a href='{url}'>Haberin Tamamını Oku</a>"
        
        # محدودیت کاراکتر (۱۰۲۴ برای عکس، ۴۰۹۶ برای متن)
        limit = 950 if image_path else 4000
        
        # بررسی طول پیام و برش هوشمند
        if len(header + formatted_summary + footer) > limit:
            max_summary_len = limit - len(header) - len(footer) - 20
            truncated = formatted_summary[:max_summary_len]
            
            # جلوگیری از شکستن تگ‌های HTML (بستن تگ‌های باز)
            open_tags = re.findall(r'<(b|i)>', truncated)
            close_tags = re.findall(r'</(b|i)>', truncated)
            if len(open_tags) > len(close_tags):
                # تگ باز وجود دارد، آن را می‌بندیم یا از محل تگ باز برش می‌دهیم
                last_tag_pos = truncated.rfind("<b") if "b" in open_tags[-1] else truncated.rfind("<i")
                truncated = truncated[:last_tag_pos]
            
            formatted_summary = truncated.strip() + "..."

        # ترکیب نهایی با رعایت فاصله‌گذاری (Double Newline)
        full_msg = f"{header}\n\n{formatted_summary}\n\n{footer}"
        
        reply_markup = {
            "inline_keyboard": [[
                {"text": "🚀 Haberin Tamamını Oku", "url": url}
            ]]
        }

        if image_path:
            # پاکسازی مسیر تصویر
            clean_path = image_path.lstrip('/')
            full_image_url = f"{Config.BASE_SITE_URL}/static/{clean_path}"
            
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

    def send_system_status(self, db_status="OK", scraper_status="ACTIVE", error_count=0):
        """
        گزارش وضعیت روزانه یا دوره‌ای سیستم به ادمین.
        """
        if not self.admin_id:
            return False
            
        status_icon = "✅" if error_count == 0 else "⚠️"
        msg = (
            f"⚙️ <b>وضعیت سیستم</b> {status_icon}\n\n"
            f"🗄 <b>دیتابیس:</b> {db_status}\n"
            f"🕷 <b>ربات جمع‌آوری:</b> {scraper_status}\n"
            f"❌ <b>خطاهای اخیر:</b> {error_count}\n"
        )
        
        payload = {
            "chat_id": self.admin_id,
            "text": msg,
            "parse_mode": "HTML"
        }
        return self._send("sendMessage", payload)

# نمونه‌سازی واحد برای استفاده در کل اپلیکیشن
alert_service = AlertService()