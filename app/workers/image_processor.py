import asyncio
import os
import sys
import logging
import uuid
import requests
import io
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageStat
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from sqlalchemy import desc, and_

# اضافه کردن ریشه پروژه به مسیر برای ایمپورت‌های داخلی
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.config import Config
from app.database.models import SessionLocal, RawNews, Trend

# تنظیمات لاگر
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ImageWorker")

# ثوابت سیستم
MEDIA_ROOT = "/app/app/static/media"
WATERMARK_TEXT = "TrendiaTR"
TARGET_WIDTH = 800

class ImageProcessor:
    def __init__(self):
        # مقداردهی کلاینت تلگرام (فقط یک بار متصل می‌شود)
        self.client = TelegramClient(
            'ttw_image', 
            Config.TELEGRAM_API_ID, 
            Config.TELEGRAM_API_HASH
        )

    async def start(self):
        """اتصال به تلگرام"""
        logger.info("🔌 Connecting to Telegram...")
        await self.client.start()
        logger.info("✅ Telegram Client Connected for Image Worker")

    def get_luminance(self, image):
        """محاسبه میانگین روشنایی تصویر (0-255)"""
        greyscale_image = image.convert('L')
        stat = ImageStat.Stat(greyscale_image)
        return stat.mean[0]

    def process_image_data(self, image_data, source_name="Bilinmiyor"):
        """تغییر سایز، برش، واترمارک دوگانه (برند + منبع) و تبدیل به WebP"""
        try:
            img = Image.open(io.BytesIO(image_data))
            
            # ۱. حذف ۵۰ پیکسل پایین (برای پاک‌سازی واترمارک‌های منبع اصلی)
            w, h = img.size
            if h > 100: 
                img = img.crop((0, 0, w, h - 50))
            
            # ۲. تغییر سایز با حفظ نسبت ابعاد (عرض ثابت ۸۰۰)
            w, h = img.size
            aspect_ratio = h / w
            new_h = int(TARGET_WIDTH * aspect_ratio)
            img = img.resize((TARGET_WIDTH, new_h), Image.Resampling.LANCZOS)
            
            # ۳. تنظیمات واترمارک
            draw = ImageDraw.Draw(img)
            font_size = 20
            padding = 15
            
            try:
                # مسیر فونت در کانتینر لینوکسی
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
            except:
                font = ImageFont.load_default()

            # تابع کمکی برای درج متن با کنتراست هوشمند
            def draw_smart_text(text, align='left'):
                # محاسبه ابعاد متن
                if hasattr(font, 'getbbox'):
                    bbox = font.getbbox(text)
                    text_w = bbox[2] - bbox[0]
                    text_h = bbox[3] - bbox[1]
                else:
                    text_w, text_h = font.getsize(text)

                # تعیین موقعیت
                if align == 'right':
                    # پایین سمت راست (نام منبع)
                    x = TARGET_WIDTH - text_w - padding
                else:
                    # پایین سمت چپ (برند)
                    x = padding
                
                y = new_h - text_h - padding
                
                # تحلیل روشنایی دقیقاً در محل درج متن
                box = (max(0, x-5), max(0, y-5), min(TARGET_WIDTH, x + text_w + 5), min(new_h, y + text_h + 5))
                watermark_area = img.crop(box)
                lum = self.get_luminance(watermark_area)
                
                # کنتراست خودکار
                text_color = (255, 255, 255) if lum < 128 else (0, 0, 0)
                shadow_color = (0, 0, 0) if lum < 128 else (255, 255, 255)
                
                # رسم سایه و متن
                draw.text((x+1, y+1), text, font=font, fill=shadow_color)
                draw.text((x, y), text, font=font, fill=text_color)

            # درج نام برند در سمت چپ
            draw_smart_text(WATERMARK_TEXT, align='left')

            # درج نام منبع در سمت راست
            display_source = f"Kaynak: {source_name}"
            if len(display_source) > 30:
                display_source = display_source[:27] + "..."
            draw_smart_text(display_source, align='right')
            
            # ۴. خروجی به فرمت WebP
            output = io.BytesIO()
            img.save(output, format="WEBP", quality=80)
            return output.getvalue(), TARGET_WIDTH, new_h
            
        except Exception as e:
            logger.error(f"Image Processing Error: {e}")
            return None, 0, 0

    async def download_from_telegram(self, external_id):
        """دانلود تصویر از پیام تلگرام"""
        try:
            parts = external_id.split('/')
            if len(parts) < 2: return None
            
            msg_id = int(parts[-1])
            username = parts[-2]
            
            message = await self.client.get_messages(username, ids=msg_id)
            if not message or not message.media:
                return None
                
            buffer = io.BytesIO()
            await self.client.download_media(message, file=buffer)
            return buffer.getvalue()
            
        except FloodWaitError as e:
            logger.warning(f"FloodWait: Sleeping {e.seconds}s")
            await asyncio.sleep(e.seconds)
            return None
        except Exception as e:
            logger.error(f"Telegram Download Error ({external_id}): {e}")
            return None

    def download_from_rss(self, external_id):
        """استخراج og:image از لینک خبرگزاری"""
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
            resp = requests.get(external_id, headers=headers, timeout=10)
            if resp.status_code != 200: return None, None
            
            soup = BeautifulSoup(resp.content, 'html.parser')
            og_image = soup.find("meta", property="og:image")
            
            if not og_image or not og_image.get("content"):
                return None, None
                
            img_url = og_image["content"]
            img_resp = requests.get(img_url, headers=headers, timeout=10)
            if img_resp.status_code == 200:
                return img_resp.content, img_url
            return None, None
            
        except Exception as e:
            logger.error(f"RSS Download Error ({external_id}): {e}")
            return None, None

    def save_file(self, image_data, news_id):
        """ذخیره فایل در ساختار پوشه‌بندی تاریخ‌محور"""
        now = datetime.now()
        year, month, day = now.strftime("%Y"), now.strftime("%m"), now.strftime("%d")
        
        folder_path = os.path.join(MEDIA_ROOT, year, month, day)
        os.makedirs(folder_path, exist_ok=True)
        
        filename = f"{uuid.uuid4()}.webp"
        full_path = os.path.join(folder_path, filename)
        
        logger.info(f"💾 Attempting to save processed image for News {news_id} to {full_path}")
        
        with open(full_path, "wb") as f:
            f.write(image_data)
            
        return f"media/{year}/{month}/{day}/{filename}"

    async def run(self):
        await self.start()
        logger.info("🚀 Image Worker Loop Started (Time Limit: 48h active)")
        
        while True:
            db = SessionLocal()
            try:
                # محاسبه زمان قطع ۴۸ ساعت اخیر
                cutoff_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)
                
                # دریافت موارد در انتظار با فیلتر زمانی ۴۸ ساعت
                pending_news = db.query(RawNews).filter(
                    RawNews.media_status == 0,
                    RawNews.created_at >= cutoff_time
                ).order_by(desc(RawNews.created_at)).limit(10).all()
                
                if not pending_news:
                    db.close()
                    await asyncio.sleep(15)
                    continue
                
                logger.info(f"📸 Processing {len(pending_news)} actionable images...")
                
                for news in pending_news:
                    try:
                        image_data = None
                        source_url = None
                        
                        # ۱. منطق دانلود
                        if news.source_type == 'telegram':
                            image_data = await self.download_from_telegram(news.external_id)
                            source_url = news.external_id
                        elif news.source_type == 'rss':
                            loop = asyncio.get_event_loop()
                            image_data, source_url = await loop.run_in_executor(None, self.download_from_rss, news.external_id)
                        
                        if not image_data:
                            news.media_status = -1 
                            db.commit()
                            continue
                            
                        # ۲. منطق پردازش تصویر (با نام منبع)
                        current_source = news.source_name if news.source_name else "TrendiaTR"
                        processed_data, w, h = self.process_image_data(image_data, current_source)
                        
                        if not processed_data:
                            news.media_status = -1
                            db.commit()
                            continue
                            
                        # ۳. ذخیره‌سازی فیزیکی
                        rel_path = self.save_file(processed_data, news.id)
                        
                        # ۴. بروزرسانی دیتابیس
                        news.media_path = rel_path
                        news.media_url = source_url
                        news.media_status = 2 # Ready
                        news.media_meta = {"width": w, "height": h, "size": len(processed_data)}
                        
                        # ۵. منطق انتخاب بهترین تصویر برای ترند (Promotion Logic)
                        if news.trend_id:
                            trend = db.query(Trend).filter(Trend.id == news.trend_id).first()
                            if trend:
                                # قانون ۱: اگر ترند هنوز عکس ندارد، این اولین عکس شاخص شود
                                if not trend.cover_image:
                                    trend.cover_image = rel_path
                                    logger.info(f"🖼️ Set initial cover for Trend {trend.id}")
                                
                                # قانون ۲: ارتقای منبع (خبرگزاری‌های رسمی Tier 1 جایگزین تلگرام می‌شوند)
                                elif news.source_tier == 1:
                                    trend.cover_image = rel_path
                                    logger.info(f"🖼️ Upgraded cover for Trend {trend.id} (Tier 1 Source)")
                        
                        db.commit()
                    except Exception as e:
                        logger.error(f"Error processing news {news.id}: {e}")
                        news.media_status = -1
                        db.commit()
                    
            except Exception as e:
                logger.error(f"Loop Error: {e}")
                db.rollback()
            finally:
                db.close()
                
            await asyncio.sleep(10)

if __name__ == "__main__":
    worker = ImageProcessor()
    asyncio.run(worker.run())