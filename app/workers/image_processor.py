import asyncio
import os
import sys
import logging
import uuid
import requests
import io
import re
import random
import urllib.parse
import time
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageStat
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from rapidfuzz import fuzz
from sqlalchemy import desc, and_, or_, cast, String, func, text as sa_text

# اضافه کردن ریشه پروژه به مسیر برای ایمپورت‌های داخلی
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.config import Config
from app.database.models import SessionLocal, RawNews, Trend, EntityImageCache

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

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
]

# Fix: after this many Bing failures for a single item, stop retrying it.
# Tracked in media_meta->bing_tries; self-healing skips items at or above this limit.
_MAX_BING_TRIES = 3

_GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
_google_imagen_client = None

CATEGORY_COLORS = {
    "Siyaset":   ((30,  64, 175), "🏛️"),
    "Ekonomi":   ((5,  150, 105), "📈"),
    "Teknoloji": ((124, 58, 237), "💻"),
    "Gündem":    ((220, 38,  38), "📰"),
    "Spor":      ((245, 158, 11), "⚽"),
    "Sanat":     ((219, 39, 119), "🎨"),
    "Deprem":    ((185, 28,  28), "🌍"),
}

class ImageProcessor:
    def __init__(self):
        # مقداردهی کلاینت تلگرام (فقط یک بار متصل می‌شود)
        self.client = TelegramClient(
            '/app/ttw_image',
            Config.TELEGRAM_API_ID,
            Config.TELEGRAM_API_HASH
        )
        # Limit concurrent DB sessions to stay within SQLAlchemy pool size (5+10=15)
        self._sem = asyncio.Semaphore(8)

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

            if img.mode != 'RGB':
                if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                    # Create a white background for transparent images before converting to RGB
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    background.paste(img, mask=img.convert('RGBA').split()[3])
                    img = background
                else:
                    img = img.convert('RGB')

            # ۱. حذف ۵۰ پیکسل پایین (برای پاک‌سازی واترمارک‌های منبع اصلی)
            w, h = img.size
            if h > 400:
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

            if getattr(message, 'video', None) or getattr(message, 'document', None):
                # Use Telegram's built-in video thumbnail (pre-generated by Telegram servers)
                media_obj = message.video or message.document
                thumbs = getattr(media_obj, 'thumbs', None)
                if thumbs:
                    try:
                        await self.client.download_media(message, file=buffer, thumb=-1)
                        if buffer.tell() > 0:
                            buffer.seek(0)
                            logger.info(f"🎞️ Got Telegram built-in thumbnail for video ({external_id})")
                            return buffer.getvalue()
                    except Exception as e:
                        logger.warning(f"⚠️ Telegram thumbnail download failed: {e}")
                logger.info(f"📹 Video has no thumbnail — will fall back to Bing ({external_id})")
                return None
            else:
                await self.client.download_media(message, file=buffer)
            return buffer.getvalue()

        except FloodWaitError as e:
            logger.warning(f"FloodWait: Sleeping {e.seconds}s")
            await asyncio.sleep(e.seconds)
            return None
        except Exception as e:
            logger.error(f"Telegram Download Error ({external_id}): {e}")
            return None

    def download_from_rss(self, external_id, pre_extracted_media_url=None):
        """استخراج تصویر از لینک خبرگزاری (اولویت با لینک مستقیم RSS)"""
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

            img_url = pre_extracted_media_url

            # اگر لینک مستقیم نداشتیم، صفحه را اسکرپ می‌کنیم
            if not img_url:
                resp = requests.get(external_id, headers=headers, timeout=10)
                if resp.status_code != 200: return None, None

                soup = BeautifulSoup(resp.content, 'html.parser')
                og_image = soup.find("meta", property="og:image") or \
                           soup.find("meta", attrs={"name": "og:image"}) or \
                           soup.find("meta", property="twitter:image")

                if not og_image or not og_image.get("content"):
                    return None, None

                img_url = og_image["content"]

                # مدیریت لینک‌های نسبی
                if img_url.startswith('/'):
                    from urllib.parse import urljoin
                    img_url = urljoin(external_id, img_url)

            img_resp = requests.get(img_url, headers=headers, timeout=10)
            if img_resp.status_code == 200:
                return img_resp.content, img_url
            return None, None

        except Exception as e:
            logger.error(f"RSS Download Error ({external_id}): {e}")
            return None, None

    def download_from_bing_images(self, query):
        """جستجوی فوق‌پیشرفته در بینگ با جعل مکان (TR) و دور زدن محدودیت‌های دانلود"""
        try:
            import json
            # Fix 4: Reduced sleep from (1.5, 3.0) to (0.8, 1.5) for faster image sourcing
            time.sleep(random.uniform(0.8, 1.5))

            # 🧹 Aggressive Query Cleaning: Remove phrases that trigger "Viral Social" results
            noise = ['Sosyal Medya Trendi', 'İlgili Haber Başlıkları', '𝕏', '📰', '#']
            clean_query = query
            for n in noise: clean_query = clean_query.replace(n, ' ')
            clean_query = re.sub(r'[^\w\s]', ' ', clean_query)
            clean_query = " ".join(clean_query.split()[:6]).strip()

            if not clean_query: return None, None

            # 📍 Force Turkey Geolocation + 🖼️ Wide Photos Only
            encoded_query = urllib.parse.quote_plus(clean_query + " haber")
            url = f"https://www.bing.com/images/search?q={encoded_query}&cc=TR&setmkt=tr-TR&setlang=tr&qft=+filterui:photo-photo+filterui:aspect-wide"

            # 🕵️ Stealth Headers to look like a local user
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept-Language": "tr-TR,tr;q=0.9",
                "Referer": "https://www.bing.com/",
                "Cookie": "SRCHHPGUSR=ADLT=OFF&NRSLT=-1&CW=1366&CH=768&DPR=1&UTC=180&WLS=2&SRCHLANG=tr" # Force TR region cookie
            }

            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, 'html.parser')
                elements = soup.find_all('a', class_='iusc')

                candidates = []
                for i, el in enumerate(elements[:8]): # Check top 8 results
                    try:
                        m_data = json.loads(el.get('m', '{}'))
                        img_url = m_data.get('murl')
                        title = m_data.get('t', '')
                        desc = m_data.get('desc', '')

                        if not img_url or not img_url.startswith('http'):
                            continue

                        # 🛡️ Blacklist low-quality/viral domains
                        bad_domains = ['tiktok', 'instagram', 'pinterest', 'facebook', 'meme', 'emoji', 'tenor', 'giphy']
                        if any(x in img_url.lower() for x in bad_domains):
                            continue

                        combined_text = f"{title} {desc}".strip()
                        score = fuzz.token_set_ratio(clean_query.lower(), combined_text.lower())
                        candidates.append({"url": img_url, "score": score})
                    except:
                        continue

                # Sort by score descending
                candidates.sort(key=lambda x: x["score"], reverse=True)

                for rank, candidate in enumerate(candidates[:3]):
                    if candidate["score"] < 40:
                        logger.warning(f"⚠️ Best match score ({candidate['score']}) below threshold (40). Aborting.")
                        break

                    try:
                        img_url = candidate["url"]
                        # 📥 Download with Bing as Referer (Crucial for bypassing Hotlink Protection)
                        img_resp = requests.get(img_url, headers={"User-Agent": headers["User-Agent"], "Referer": "https://www.bing.com/"}, timeout=7)

                        if img_resp.status_code == 200:
                            content_type = img_resp.headers.get('Content-Type', '')
                            if 'image' in content_type:
                                logger.info(f"🎯 Success! Downloaded Rank #{rank+1} (Score: {candidate['score']}) from {img_url[:40]}...")
                                return img_resp.content, img_url
                        else:
                            logger.warning(f"⚠️ Failed to download Rank #{rank+1} (HTTP {img_resp.status_code})")
                    except Exception as e:
                        logger.error(f"Download error for candidate: {e}")
                        continue

            return None, None
        except Exception as e:
            logger.error(f"Bing Extraction Error: {e}")
            return None, None

    def download_from_wikipedia(self, entity_name: str):
        """Stage 3: fetch a thumbnail from the Wikipedia pageimages API for a named entity."""
        try:
            encoded = urllib.parse.quote(entity_name)
            url = (
                f"https://en.wikipedia.org/w/api.php"
                f"?action=query&titles={encoded}&prop=pageimages"
                f"&format=json&pithumbsize=800"
            )
            resp = requests.get(url, headers={"User-Agent": "TrendiaTR/1.0 (trendia.tr)"}, timeout=8)
            if resp.status_code != 200:
                return None
            data = resp.json()
            pages = data.get("query", {}).get("pages", {})
            for page_id, page in pages.items():
                if page_id == "-1":
                    continue
                thumb_url = page.get("thumbnail", {}).get("source")
                if thumb_url:
                    img_resp = requests.get(thumb_url, headers={"User-Agent": "TrendiaTR/1.0"}, timeout=8)
                    if img_resp.status_code == 200:
                        return img_resp.content
            return None
        except Exception as e:
            logger.error(f"Wikipedia Download Error ({entity_name}): {e}")
            return None

    def generate_from_imagen(self, trend_title: str, category: str = "Gündem"):
        """Stage 4: generate a contextual illustration with Gemini Imagen 4 Fast."""
        try:
            if not _GOOGLE_API_KEY:
                logger.warning("⚠️ GOOGLE_API_KEY not set — skipping Imagen 4.")
                return None
            from google import genai as _genai
            from google.genai import types as _types
            global _google_imagen_client
            if _google_imagen_client is None:
                _google_imagen_client = _genai.Client(api_key=_GOOGLE_API_KEY)
            prompt = (
                f"Professional Turkish news editorial photograph, high quality, photojournalism style: "
                f"{trend_title}. Category: {category}. "
                f"Realistic, dramatic lighting, no text, no watermarks, no logos."
            )
            response = _google_imagen_client.models.generate_images(
                model="imagen-4.0-fast-generate-001",
                prompt=prompt,
                config=_types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio="16:9",
                ),
            )
            if response.generated_images:
                return response.generated_images[0].image.image_bytes
            return None
        except Exception as e:
            logger.error(f"Imagen 4 Generation Error ({trend_title[:30]}): {e}")
            return None

    def generate_pil_placeholder(self, trend_title: str, category: str = "Gündem"):
        """Stage 5: create a branded category card using PIL — always succeeds when a title exists."""
        try:
            bg_color, _ = CATEGORY_COLORS.get(category, ((37, 99, 235), "📰"))
            w, h = 800, 450
            img = Image.new("RGB", (w, h), bg_color)
            draw = ImageDraw.Draw(img)

            # Dark gradient overlay at the bottom for text readability
            for y in range(200, h):
                fade = int((y - 200) / (h - 200) * 160)
                draw.line(
                    [(0, y), (w, y)],
                    fill=(max(0, bg_color[0] - fade), max(0, bg_color[1] - fade), max(0, bg_color[2] - fade)),
                )

            try:
                font_bold  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
                font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
            except Exception:
                font_bold = font_small = ImageFont.load_default()

            # Category label (top-left)
            draw.text((30, 28), category.upper(), font=font_small, fill=(255, 255, 255))
            # Brand label (top-right)
            try:
                bw = font_small.getbbox(WATERMARK_TEXT)[2]
            except Exception:
                bw = len(WATERMARK_TEXT) * 11
            draw.text((w - bw - 20, 28), WATERMARK_TEXT, font=font_small, fill=(255, 255, 255))

            # Trend title — word-wrapped, max 3 lines
            words = trend_title.split()
            lines, cur = [], []
            for word in words:
                test = " ".join(cur + [word])
                try:
                    tw = font_bold.getbbox(test)[2]
                except Exception:
                    tw = len(test) * 18
                if tw > w - 60 and cur:
                    lines.append(" ".join(cur))
                    cur = [word]
                else:
                    cur.append(word)
            if cur:
                lines.append(" ".join(cur))

            y_text = h - len(lines[:3]) * 44 - 30
            for line in lines[:3]:
                draw.text((30, y_text), line, font=font_bold, fill=(255, 255, 255))
                y_text += 44

            output = io.BytesIO()
            img.save(output, format="WEBP", quality=82)
            return output.getvalue()
        except Exception as e:
            logger.error(f"PIL Placeholder Error: {e}")
            return None

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

    async def _process_one(self, news_id: int):
        """Process a single news item's image. Uses its own DB session for parallel safety."""
        async with self._sem:
            await self._process_one_inner(news_id)

    async def _process_one_inner(self, news_id: int):
        db = SessionLocal()
        try:
            news = db.query(RawNews).filter(RawNews.id == news_id).first()
            if not news:
                return

            image_data = None
            source_url = None
            source_label = None   # overrides source_name in watermark; e.g. "AI Görseli"
            skip_processing = False  # True for PIL placeholder (already formatted as WebP)
            active_entity_name = None
            trend = None

            # Stage 1: Source media (Telegram download / RSS og:image)
            if news.source_type == 'telegram':
                image_data = await self.download_from_telegram(news.external_id)
                source_url = news.external_id

                # SMART FALLBACK: If no media in Telegram, check text for links
                if not image_data and news.content:
                    urls = re.findall(r'(https?://[^\s]+)', news.content)
                    if urls:
                        fallback_url = urls[0].rstrip('.,!?\'"')
                        loop = asyncio.get_event_loop()
                        image_data, _ = await loop.run_in_executor(None, self.download_from_rss, fallback_url, None)
                        if image_data:
                            source_url = fallback_url
                            logger.info(f"🔗 Fallback successful: Extracted image from link {source_url}")
            elif news.source_type == 'rss':
                loop = asyncio.get_event_loop()
                image_data, source_url = await loop.run_in_executor(None, self.download_from_rss, news.external_id, news.media_url)

            # Stages 2–5: Bing → Wikipedia → Imagen 4 → PIL Placeholder
            if not image_data:
                search_query = None
                active_entity_name = None

                if news.trend_id:
                    trend = db.query(Trend).filter(Trend.id == news.trend_id).first()
                    if trend:
                        if trend.entities and isinstance(trend.entities, dict):
                            ai_image_query = trend.entities.get('image_search_query')
                            if ai_image_query and len(ai_image_query) > 2:
                                search_query = ai_image_query
                            else:
                                people = trend.entities.get('people', [])
                                orgs = trend.entities.get('organizations', [])
                                if people or orgs:
                                    search_query = " ".join(people[:1] + orgs[:1])

                            # Fallback: entities exist but no usable query — use trend title
                            if not search_query and trend.title:
                                search_query = ' '.join(trend.title.split()[:6])
                                logger.info(
                                    f"🔍 Trend {trend.id}: entities empty/incomplete — "
                                    f"using title as Bing query: '{search_query}'"
                                )

                            active_entity_name = search_query

                            # CACHE CHECK
                            if active_entity_name:
                                cached_entity = db.query(EntityImageCache).filter(
                                    EntityImageCache.entity_name == active_entity_name
                                ).first()
                                if cached_entity:
                                    logger.info(f"⚡ CACHE HIT! Using verified image for entity: {active_entity_name}")
                                    news.media_path = cached_entity.local_path
                                    news.media_url = cached_entity.image_url
                                    news.media_status = 2
                                    news.media_meta = {"cached": True}
                                    if not trend.cover_image or news.source_tier == 1:
                                        trend.cover_image = cached_entity.local_path
                                        logger.info(f"🖼️ Set/Upgraded cover for Trend {trend.id} from Cache")
                                    db.commit()
                                    return
                        else:
                            # Fix 1: entities is None or {} — use title as Bing query fallback
                            if trend.title:
                                search_query = trend.title[:80]
                                logger.info(
                                    f"🔍 Trend {trend.id} has no/empty entities — "
                                    f"using title as Bing query."
                                )

                # Stage 2: Bing image search
                if search_query:
                    _meta = news.media_meta if isinstance(news.media_meta, dict) else {}
                    bing_tries = _meta.get('bing_tries', 0)
                    if bing_tries < _MAX_BING_TRIES:
                        search_query = search_query.split('📰')[0].split('|')[0].split('-')[0].strip()
                        logger.info(f"🔍 [Stage 2] Bing search (try {bing_tries + 1}/{_MAX_BING_TRIES}): '{search_query[:40]}'")
                        loop = asyncio.get_event_loop()
                        image_data, fallback_url = await loop.run_in_executor(
                            None, self.download_from_bing_images, search_query
                        )
                        if image_data:
                            source_url = fallback_url
                            logger.info("✅ [Stage 2] Bing image downloaded.")
                        else:
                            _meta['bing_tries'] = bing_tries + 1
                            news.media_meta = _meta
                    else:
                        logger.info(f"⏭️ [Stage 2] Bing budget exhausted ({bing_tries}), proceeding to next stage.")

                # Stage 3: Wikipedia (named entity thumbnail)
                if not image_data and trend and trend.entities and isinstance(trend.entities, dict):
                    people = trend.entities.get('people', [])
                    orgs = trend.entities.get('organizations', [])
                    loop = asyncio.get_event_loop()
                    for entity in (people[:1] + orgs[:1]):
                        if entity:
                            logger.info(f"📖 [Stage 3] Wikipedia search for: {entity}")
                            wiki_data = await loop.run_in_executor(None, self.download_from_wikipedia, entity)
                            if wiki_data:
                                image_data = wiki_data
                                source_url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(entity)}"
                                source_label = "Wikipedia"
                                logger.info(f"✅ [Stage 3] Wikipedia image found for: {entity}")
                                break

                # Stage 4: Gemini Imagen 4 Fast
                if not image_data and trend and trend.title:
                    logger.info(f"🤖 [Stage 4] Imagen 4 generating for: {trend.title[:40]}")
                    loop = asyncio.get_event_loop()
                    ai_data = await loop.run_in_executor(
                        None, self.generate_from_imagen, trend.title, trend.category or "Gündem"
                    )
                    if ai_data:
                        image_data = ai_data
                        source_url = "ai_generated"
                        source_label = "AI Görseli"
                        logger.info("✅ [Stage 4] Imagen 4 image generated.")

                # Stage 5: PIL placeholder (last resort — always succeeds when a title exists)
                if not image_data and trend and trend.title:
                    logger.info(f"🎨 [Stage 5] PIL placeholder for: {trend.title[:40]}")
                    placeholder_data = self.generate_pil_placeholder(trend.title, trend.category or "Gündem")
                    if placeholder_data:
                        image_data = placeholder_data
                        source_url = "placeholder"
                        source_label = "TrendiaTR"
                        skip_processing = True
                        logger.info("✅ [Stage 5] PIL placeholder created.")

            if not image_data:
                # Truly no image possible (no trend_id, no title) — permanent fail
                _meta = news.media_meta if isinstance(news.media_meta, dict) else {}
                if 'bing_tries' not in _meta:
                    _meta['bing_tries'] = _MAX_BING_TRIES
                    news.media_meta = _meta
                news.media_status = -1
                db.commit()
                return

            # ۲. منطق پردازش تصویر
            current_source = source_label if source_label else (news.source_name if news.source_name else "TrendiaTR")
            if skip_processing:
                # PIL placeholder is already a fully formatted WebP at 800×450
                processed_data, w, h = image_data, 800, 450
            else:
                processed_data, w, h = self.process_image_data(image_data, current_source)

            if not processed_data:
                news.media_status = -1
                db.commit()
                return

            # ۳. ذخیره‌سازی فیزیکی
            rel_path = self.save_file(processed_data, news.id)

            # ۴. بروزرسانی دیتابیس
            news.media_path = rel_path
            news.media_url = source_url
            news.media_status = 2  # Ready
            news.media_meta = {"width": w, "height": h, "size": len(processed_data)}

            # CACHE SAVE (real photos only — Bing or Wikipedia, not AI/placeholder)
            if active_entity_name and source_label not in ("AI Görseli",) and not skip_processing:
                existing_cache = db.query(EntityImageCache).filter(
                    EntityImageCache.entity_name == active_entity_name
                ).first()
                if not existing_cache:
                    new_cache = EntityImageCache(
                        entity_name=active_entity_name,
                        image_url=source_url,
                        local_path=rel_path
                    )
                    db.add(new_cache)
                    logger.info(f"💾 Cached new verified image for entity: {active_entity_name}")

            # ۵. منطق انتخاب بهترین تصویر برای ترند (Promotion Logic)
            if news.trend_id:
                if trend is None:
                    trend = db.query(Trend).filter(Trend.id == news.trend_id).first()
                if trend:
                    if not trend.cover_image:
                        trend.cover_image = rel_path
                        logger.info(f"🖼️ Set initial cover for Trend {trend.id}")
                    elif news.source_tier == 1:
                        trend.cover_image = rel_path
                        logger.info(f"🖼️ Upgraded cover for Trend {trend.id} (Tier 1 Source)")

            db.commit()

        except Exception as e:
            logger.error(f"Error processing news {news_id}: {e}")
            try:
                news_row = db.query(RawNews).filter(RawNews.id == news_id).first()
                if news_row:
                    news_row.media_status = -1
                    db.commit()
            except Exception:
                db.rollback()
        finally:
            db.close()

    async def run(self):
        await self.start()
        logger.info("🚀 Image Worker Loop Started (Time Limit: 48h active)")

        last_retry_time = 0
        last_video_heal_time = 0

        while True:
            news_ids = []
            db = SessionLocal()
            try:
                current_time = time.time()

                # --- Fix 4: Fast video self-healing (every 60s) ---
                # Re-queue items that have a Telegram video path but no processed image yet.
                if current_time - last_video_heal_time > 60:
                    video_pending = db.execute(sa_text("""
                        UPDATE raw_news
                        SET media_status = 0
                        WHERE video_path IS NOT NULL
                          AND media_path IS NULL
                          AND media_status NOT IN (0, 2)
                    """)).rowcount
                    if video_pending > 0:
                        db.commit()
                        logger.info(f"🎞️ Video heal: re-queued {video_pending} video items for thumbnail retry.")
                    last_video_heal_time = current_time

                # --- Periodic Retry for Missing Images (Self-Healing) ---
                # Run this check every 15 minutes (900 seconds)
                if current_time - last_retry_time > 900:
                    logger.info("🔄 Checking for missing images (Self-Healing)...")

                    heal_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)
                    # Fix: skip items that have exhausted their Bing retry budget
                    # (bing_tries >= _MAX_BING_TRIES stored in media_meta JSON).
                    # Items without bing_tries in meta are old/legacy — re-queue them once.
                    retry_ids = [row[0] for row in db.execute(sa_text("""
                        SELECT id FROM raw_news
                        WHERE media_status = -1
                          AND created_at >= :cutoff
                          AND COALESCE((media_meta->>'bing_tries')::int, 0) < :max_tries
                        ORDER BY created_at DESC
                        LIMIT 50
                    """), {"cutoff": heal_cutoff, "max_tries": _MAX_BING_TRIES}).fetchall()]

                    if retry_ids:
                        db.execute(sa_text(
                            "UPDATE raw_news SET media_status = 0 WHERE id = ANY(:ids)"
                        ), {"ids": retry_ids})
                        db.commit()
                        logger.info(f"♻️ Re-queued {len(retry_ids)} failed items for retry (budget remaining).")

                    # Re-queue Bing-exhausted items whose trend still has no cover — stages 3-5 can fill them
                    stage3plus_ids = [row[0] for row in db.execute(sa_text("""
                        SELECT rn.id
                        FROM raw_news rn
                        JOIN trends t ON t.id = rn.trend_id
                        WHERE rn.media_status = -1
                          AND rn.created_at >= :cutoff
                          AND t.cover_image IS NULL
                          AND t.is_active = TRUE
                          AND COALESCE((rn.media_meta->>'bing_tries')::int, 0) >= :max_tries
                        ORDER BY rn.created_at DESC
                        LIMIT 30
                    """), {"cutoff": heal_cutoff, "max_tries": _MAX_BING_TRIES}).fetchall()]
                    if stage3plus_ids:
                        db.execute(sa_text(
                            "UPDATE raw_news SET media_status = 0 WHERE id = ANY(:ids)"
                        ), {"ids": stage3plus_ids})
                        db.commit()
                        logger.info(f"♻️ Re-queued {len(stage3plus_ids)} Bing-exhausted items for stage 3-5 pipeline.")

                    # Fix 2: Backfill cover_image for trends missing a cover (e.g. after cluster merges)
                    no_cover_trends = db.query(Trend).filter(
                        Trend.is_active == True,
                        Trend.cover_image == None
                    ).all()
                    backfilled = 0
                    for trend in no_cover_trends:
                        best_news = db.query(RawNews).filter(
                            RawNews.trend_id == trend.id,
                            RawNews.media_status == 2,
                            RawNews.media_path.isnot(None)
                        ).order_by(RawNews.source_tier.asc(), RawNews.published_at.desc()).first()
                        if best_news:
                            trend.cover_image = best_news.media_path
                            backfilled += 1
                    if backfilled > 0:
                        db.commit()
                        logger.info(f"🖼️ Backfilled cover_image for {backfilled} trends missing a cover.")

                    last_retry_time = current_time

                # محاسبه زمان قطع ۴۸ ساعت اخیر
                cutoff_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)

                # --- Priority Query: two-slot batch ---
                # Slot A (16): one item per coverless trend — fastest path first
                slot_a_ids = [row[0] for row in db.execute(sa_text("""
                    SELECT DISTINCT ON (rn.trend_id) rn.id
                    FROM raw_news rn
                    JOIN trends t ON rn.trend_id = t.id
                    WHERE t.cover_image IS NULL
                      AND t.is_active = TRUE
                      AND rn.media_status IN (0, -2)
                      AND rn.created_at >= :cutoff
                    ORDER BY rn.trend_id,
                             CASE WHEN rn.media_url IS NOT NULL THEN 0 ELSE 1 END,
                             rn.source_tier ASC,
                             rn.created_at DESC
                    LIMIT 16
                """), {"cutoff": cutoff_time}).fetchall()]

                # Slot B (4): remaining pending items not already in slot A
                slot_b_ids = [row[0] for row in db.execute(sa_text("""
                    SELECT rn.id
                    FROM raw_news rn
                    WHERE rn.media_status IN (0, -2)
                      AND rn.created_at >= :cutoff
                      AND rn.id != ALL(:exclude)
                    ORDER BY rn.created_at DESC
                    LIMIT 4
                """), {"cutoff": cutoff_time, "exclude": slot_a_ids or [-1]}).fetchall()]

                news_ids = slot_a_ids + slot_b_ids
                if slot_a_ids:
                    logger.info(
                        f"🎯 Priority batch: {len(slot_a_ids)} coverless-trend items + "
                        f"{len(slot_b_ids)} normal items"
                    )

            except Exception as e:
                logger.error(f"Loop Error: {e}")
                db.rollback()
            finally:
                db.close()

            if not news_ids:
                await asyncio.sleep(15)
                continue

            logger.info(f"📸 Processing {len(news_ids)} actionable images in parallel...")
            # Fix 3: parallel processing — each _process_one has its own DB session
            await asyncio.gather(*[self._process_one(nid) for nid in news_ids])

            await asyncio.sleep(10)

if __name__ == "__main__":
    worker = ImageProcessor()
    asyncio.run(worker.run())
