import asyncio
import os
import sys
import logging
import uuid
import requests
import io
from datetime import datetime
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageStat
from telethon import TelegramClient
from telethon.errors import FloodWaitError

# Add project root to sys path for internal imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.config import Config
from app.database.models import SessionLocal, RawNews, Trend

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ImageWorker")

# Constants
MEDIA_ROOT = "/app/app/static/media"
WATERMARK_TEXT = "TrendiaTR"
TARGET_WIDTH = 800

class ImageProcessor:
    def __init__(self):
        # Initialize Telethon Client (Connects ONCE at startup)
        self.client = TelegramClient(
            'ttw_session', 
            Config.TELEGRAM_API_ID, 
            Config.TELEGRAM_API_HASH
        )

    async def start(self):
        """Connect to Telegram"""
        logger.info("🔌 Connecting to Telegram...")
        await self.client.start()
        logger.info("✅ Telegram Client Connected for Image Worker")

    def get_luminance(self, image):
        """Calculate average luminance of an image (0-255)"""
        # Convert to grayscale to calculate brightness
        greyscale_image = image.convert('L')
        stat = ImageStat.Stat(greyscale_image)
        return stat.mean[0]

    def process_image_data(self, image_data):
        """Resize, Crop, Watermark, and Convert to WebP"""
        try:
            img = Image.open(io.BytesIO(image_data))
            
            # 1. Crop bottom 50px (Remove source captions/watermarks)
            w, h = img.size
            if h > 100: # Only crop if height is sufficient
                img = img.crop((0, 0, w, h - 50))
            
            # 2. Resize (Maintain Aspect Ratio)
            w, h = img.size
            aspect_ratio = h / w
            new_h = int(TARGET_WIDTH * aspect_ratio)
            img = img.resize((TARGET_WIDTH, new_h), Image.Resampling.LANCZOS)
            
            # 3. Smart Watermark
            # Position: Bottom Right with some padding
            font_size = 20
            padding = 15
            
            # Try to load a bold font, fallback to default if missing
            try:
                # Common path in many Linux containers
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
            except:
                font = ImageFont.load_default()

            # Calculate text size
            if hasattr(font, 'getbbox'):
                bbox = font.getbbox(WATERMARK_TEXT)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
            else:
                text_w, text_h = font.getsize(WATERMARK_TEXT)

            x = TARGET_WIDTH - text_w - padding
            y = new_h - text_h - padding
            
            # Analyze background luminance at watermark position
            # Crop the area where text will be placed
            watermark_area = img.crop((x, y, x + text_w, y + text_h))
            lum = self.get_luminance(watermark_area)
            
            # If Dark Background (L < 128) -> White Text
            # If Light Background (L >= 128) -> Black Text
            text_color = (255, 255, 255) if lum < 128 else (0, 0, 0)
            
            draw = ImageDraw.Draw(img)
            draw.text((x, y), WATERMARK_TEXT, font=font, fill=text_color)
            
            # 4. Convert to WebP
            output = io.BytesIO()
            img.save(output, format="WEBP", quality=80)
            return output.getvalue(), TARGET_WIDTH, new_h
            
        except Exception as e:
            logger.error(f"Image Processing Error: {e}")
            return None, 0, 0

    async def download_from_telegram(self, external_id):
        """Download media from Telegram message"""
        try:
            # Format: https://t.me/username/123
            parts = external_id.split('/')
            if len(parts) < 2: return None
            
            msg_id = int(parts[-1])
            username = parts[-2]
            
            # Get message
            message = await self.client.get_messages(username, ids=msg_id)
            if not message or not message.media:
                return None
                
            # Download to memory
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
        """Scrape OG Image from URL"""
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (TrendiaTR Bot)'}
            # Fetch page
            resp = requests.get(external_id, headers=headers, timeout=10)
            if resp.status_code != 200: return None
            
            soup = BeautifulSoup(resp.content, 'html.parser')
            og_image = soup.find("meta", property="og:image")
            
            if not og_image or not og_image.get("content"):
                return None
                
            img_url = og_image["content"]
            
            # Download image stream
            img_resp = requests.get(img_url, headers=headers, timeout=10)
            if img_resp.status_code == 200:
                return img_resp.content
            return None
            
        except Exception as e:
            logger.error(f"RSS Download Error ({external_id}): {e}")
            return None

    def save_file(self, image_data):
        """Save WebP to disk with date-based structure"""
        now = datetime.now()
        year, month, day = now.strftime("%Y"), now.strftime("%m"), now.strftime("%d")
        
        folder_path = os.path.join(MEDIA_ROOT, year, month, day)
        os.makedirs(folder_path, exist_ok=True)
        
        filename = f"{uuid.uuid4()}.webp"
        full_path = os.path.join(folder_path, filename)
        
        with open(full_path, "wb") as f:
            f.write(image_data)
            
        # Return relative path for DB
        return f"media/{year}/{month}/{day}/{filename}"

    async def run(self):
        await self.start()
        logger.info("🚀 Image Worker Loop Started")
        
        while True:
            db = SessionLocal()
            try:
                # Fetch pending items (Limit 10 per cycle)
                pending_news = db.query(RawNews).filter(RawNews.media_status == 0).limit(10).all()
                
                if not pending_news:
                    db.close()
                    await asyncio.sleep(10)
                    continue
                
                logger.info(f"📸 Processing {len(pending_news)} images...")
                
                for news in pending_news:
                    image_data = None
                    
                    # 1. Download Logic
                    if news.source_type == 'telegram':
                        image_data = await self.download_from_telegram(news.external_id)
                    elif news.source_type == 'rss':
                        # Run blocking RSS download in executor
                        loop = asyncio.get_event_loop()
                        image_data = await loop.run_in_executor(None, self.download_from_rss, news.external_id)
                    
                    if not image_data:
                        news.media_status = -1 # Error or No Image
                        db.commit()
                        continue
                        
                    # 2. Processing Logic
                    processed_data, w, h = self.process_image_data(image_data)
                    
                    if not processed_data:
                        news.media_status = -1
                        db.commit()
                        continue
                        
                    # 3. Storage
                    rel_path = self.save_file(processed_data)
                    
                    # 4. Database Updates
                    news.media_path = rel_path
                    news.media_status = 2 # Ready
                    news.media_meta = {"width": w, "height": h, "size": len(processed_data)}
                    
                    # 5. Promotion Logic (Best Image Strategy)
                    if news.trend_id:
                        trend = db.query(Trend).filter(Trend.id == news.trend_id).first()
                        if trend:
                            # Rule 1: Empty cover
                            if not trend.cover_image:
                                trend.cover_image = rel_path
                                logger.info(f"🖼️ Set initial cover for Trend {trend.id}")
                            
                            # Rule 2: Tier Upgrade (Official source overrides)
                            elif news.source_tier == 1:
                                trend.cover_image = rel_path
                                logger.info(f"🖼️ Upgraded cover for Trend {trend.id} (Tier 1 Source)")
                    
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