import asyncio
import os
import sys
import logging
import uuid
import requests
import io
import re
import random
import threading
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

_OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://ttw_ollama:11434/api/generate")

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

            # ۲. Center-crop to 16:9 then resize to 800×450
            TARGET_HEIGHT = 450
            w, h = img.size
            target_ratio = TARGET_WIDTH / TARGET_HEIGHT  # 16:9
            src_ratio = w / h
            if src_ratio > target_ratio:
                # wider than 16:9 — crop left/right
                new_w = int(h * target_ratio)
                x0 = (w - new_w) // 2
                img = img.crop((x0, 0, x0 + new_w, h))
            elif src_ratio < target_ratio:
                # taller than 16:9 — crop top/bottom
                new_h_crop = int(w / target_ratio)
                y0 = (h - new_h_crop) // 2
                img = img.crop((0, y0, w, y0 + new_h_crop))
            img = img.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS)
            new_h = TARGET_HEIGHT

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
            noise = [
                'Sosyal Medya Trendi', 'İlgili Haber Başlıkları', '𝕏', '📰', '#',
                'flaş karar', 'flas karar', 'son dakika', 'şok gelişme', 'bakın ne oldu',
            ]
            clean_query = query
            for n in noise: clean_query = clean_query.replace(n, ' ').replace(n.upper(), ' ')
            clean_query = re.sub(r'[^\w\s]', ' ', clean_query)
            clean_query = " ".join(clean_query.split()[:6]).strip()

            if not clean_query: return None, None

            # 📍 Force Turkey Geolocation (all aspect ratios for wider image pool)
            encoded_query = urllib.parse.quote_plus(clean_query + " haber")
            url = f"https://www.bing.com/images/search?q={encoded_query}&cc=TR&setmkt=tr-TR&setlang=tr"

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
                for i, el in enumerate(elements[:12]): # Check top 12 results
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
                    if candidate["score"] < 35:
                        logger.warning(f"⚠️ Candidate score ({candidate['score']}) below threshold (35). Skipping.")
                        continue

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

    def generate_visual_query_with_ollama(self, trend_title: str) -> str:
        """Use local Ollama (qwen2.5) to extract 2-4 visual keywords from a Turkish trend title."""
        try:
            prompt = (
                "Sen bir görsel arama uzmanısın. Aşağıdaki Türkçe haber başlığından "
                "görsel arama için en iyi 2-4 anahtar kelimeyi çıkar. "
                "Sadece isim, kişi adı, kurum adı veya yer adı gibi görsel açıdan zengin kelimeleri seç. "
                "Clickbait ifadeleri (flaş, son dakika, bakın ne oldu, şok, açıkladı, final yapıyor) "
                "ve fiilleri ÇIKART. "
                "SADECE kelimeleri boşlukla ayırarak yaz. Açıklama, tırnak, madde işareti veya markdown YAZMA.\n\n"
                f"Başlık: {trend_title}"
            )
            payload = {
                "model": "qwen2.5",
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "top_p": 0.9},
            }
            resp = requests.post(_OLLAMA_API_URL, json=payload, timeout=8)
            if resp.status_code == 200:
                result = resp.json().get("response", "").strip()
                result = re.sub(r'[^\w\s]', ' ', result)
                result = " ".join(result.split()[:6])
                if result:
                    logger.info(f"🧠 Ollama visual query: '{result}' ← '{trend_title[:40]}'")
                    return result
        except Exception as e:
            logger.warning(f"Ollama query generation failed ({e}), falling back to title words.")
        return " ".join(trend_title.split()[:4])

    def download_from_duckduckgo_images(self, query: str):
        """Stage 3: DuckDuckGo image search (Bing-backed, native structured JSON)."""
        try:
            encoded = urllib.parse.quote_plus(query)
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept-Language": "tr-TR,tr;q=0.9",
            }
            # Step 1: acquire vdir session token
            token_resp = requests.get(
                f"https://duckduckgo.com/?q={encoded}&iax=images&ia=images",
                headers=headers, timeout=8
            )
            vdir = None
            if token_resp.status_code == 200:
                m = re.search(r"vdir\s*=\s*['\"]?([0-9a-fA-F-]+)['\"]?", token_resp.text)
                if m:
                    vdir = m.group(1)
            if not vdir:
                logger.warning("⚠️ [DDG] Could not extract vdir token.")
                return None, None

            # Step 2: fetch image results JSON
            results_resp = requests.get(
                f"https://duckduckgo.com/i.js?q={encoded}&o=json&vdir={vdir}",
                headers=headers, timeout=8
            )
            if results_resp.status_code != 200:
                return None, None

            bad_domains = ['tiktok', 'instagram', 'pinterest', 'facebook', 'meme', 'tenor', 'giphy']
            for item in results_resp.json().get("results", [])[:5]:
                img_url = item.get("image") or item.get("url")
                if not img_url or not img_url.startswith("http"):
                    continue
                if any(d in img_url.lower() for d in bad_domains):
                    continue
                try:
                    img_resp = requests.get(
                        img_url,
                        headers={"User-Agent": headers["User-Agent"], "Referer": "https://duckduckgo.com/"},
                        timeout=7
                    )
                    if img_resp.status_code == 200 and "image" in img_resp.headers.get("Content-Type", ""):
                        logger.info(f"🦆 [DDG] Downloaded image from {img_url[:50]}")
                        return img_resp.content, img_url
                except Exception as e:
                    logger.warning(f"⚠️ [DDG] Download failed ({e})")
                    continue
            return None, None
        except Exception as e:
            logger.error(f"DuckDuckGo Search Error: {e}")
            return None, None

    def generate_pil_placeholder(self, trend_title: str = "", category: str = "Gündem") -> bytes:
        """Stage 5: Branded breaking-news card — TrendiaTR visual identity."""
        try:
            FONT_BLACK   = "/usr/share/fonts/truetype/noto/NotoSans-Black.ttf"
            FONT_BOLD    = "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
            FONT_REGULAR = "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"

            w, h = 800, 450
            RED_DARK   = (100,  8,  8)
            RED_BRIGHT = (210, 38, 38)

            # ── 1. Background gradient (dark left → brighter centre-right) ──
            img = Image.new("RGB", (w, h), RED_DARK)
            draw = ImageDraw.Draw(img)

            for y in range(h):
                for x in range(0, w, 2):
                    fx = x / w
                    fy = y / h
                    t = fx * 0.55 + (1 - abs(fy - 0.5) * 2) * 0.45
                    t = max(0.0, min(1.0, t))
                    r = int(RED_DARK[0] + (RED_BRIGHT[0] - RED_DARK[0]) * t)
                    g = int(RED_DARK[1] + (RED_BRIGHT[1] - RED_DARK[1]) * t)
                    b = int(RED_DARK[2] + (RED_BRIGHT[2] - RED_DARK[2]) * t)
                    draw.line([(x, y), (x + 1, y)], fill=(r, g, b))

            # ── 2. Diagonal lines ──
            for i in range(-h, w + h, 38):
                draw.line([(i, 0), (i + h, h)], fill=(160, 25, 25), width=1)

            # ── 3. Fonts ──
            def _load_font(path, size):
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    return ImageFont.load_default()

            f_tt      = _load_font(FONT_BLACK,   46)
            f_brand   = _load_font(FONT_BLACK,   54)
            f_sondak  = _load_font(FONT_BLACK,  118)
            f_sub     = _load_font(FONT_REGULAR,  25)
            f_footer  = _load_font(FONT_BOLD,    19)

            # ── 4. TT logo square with glow ──
            logo_size = 76
            logo_x, logo_y = 36, 28

            for g_off in [8, 5, 3, 1]:
                fade = 20 + g_off * 6
                draw.rounded_rectangle(
                    [(logo_x - g_off, logo_y - g_off),
                     (logo_x + logo_size + g_off, logo_y + logo_size + g_off)],
                    radius=22 + g_off,
                    fill=(min(255, 100 + fade), min(255, 30 + fade // 3), min(255, 30 + fade // 3))
                )

            draw.rounded_rectangle(
                [(logo_x, logo_y), (logo_x + logo_size, logo_y + logo_size)],
                radius=18, fill=(220, 38, 38)
            )

            tt_bbox = f_tt.getbbox("TT")
            tt_w = tt_bbox[2] - tt_bbox[0]
            tt_h = tt_bbox[3] - tt_bbox[1]
            tt_x = logo_x + (logo_size - tt_w) // 2 - tt_bbox[0]
            tt_y = logo_y + (logo_size - tt_h) // 2 - tt_bbox[1]
            draw.text((tt_x, tt_y), "TT", font=f_tt, fill=(255, 255, 255))

            # ── 5. "TrendiaTR" brand ──
            brand_x = logo_x + logo_size + 18
            brand_y = logo_y + (logo_size - 54) // 2

            draw.text((brand_x + 3, brand_y + 3), "Trendia", font=f_brand, fill=(40, 0, 0))
            draw.text((brand_x, brand_y), "Trendia", font=f_brand, fill=(255, 255, 255))

            trendia_w = f_brand.getbbox("Trendia")[2] - f_brand.getbbox("Trendia")[0]
            tr_x = brand_x + trendia_w

            draw.text((tr_x + 3, brand_y + 3), "TR", font=f_brand, fill=(80, 0, 0))
            draw.text((tr_x, brand_y), "TR", font=f_brand, fill=(255, 100, 100))

            # ── 6. SONDAKİKA — centred, huge, with shadow ──
            sdk_text = "SONDAKİKA"
            sdk_bbox = f_sondak.getbbox(sdk_text)
            sdk_w = sdk_bbox[2] - sdk_bbox[0]
            sdk_x = (w - sdk_w) // 2
            sdk_y = 148

            for sx, sy in [(5, 5), (4, 4), (3, 3)]:
                draw.text((sdk_x + sx, sdk_y + sy), sdk_text, font=f_sondak, fill=(40, 0, 0))
            draw.text((sdk_x, sdk_y), sdk_text, font=f_sondak, fill=(230, 55, 55))

            # ── 7. Subtitle ──
            sub_text = "Yapay Zeka Destekli Gerçek Zamanlı Analiz"
            sub_bbox = f_sub.getbbox(sub_text)
            sub_w = sub_bbox[2] - sub_bbox[0]
            sub_x = (w - sub_w) // 2
            draw.text((sub_x, sdk_y + 152), sub_text, font=f_sub, fill=(210, 160, 160))

            # ── 8. Footer bar ──
            footer_y = h - 42
            draw.rectangle([(0, footer_y), (w, h)], fill=(15, 5, 5))
            draw.line([(0, footer_y), (w, footer_y)], fill=(180, 35, 35), width=2)

            footer_bar_h = h - footer_y

            left_text = "TrendiaTR Yapay Zeka Haber Analizi"
            left_bbox = f_footer.getbbox(left_text)
            left_h = left_bbox[3] - left_bbox[1]
            left_y = footer_y + (footer_bar_h - left_h) // 2 - left_bbox[1]
            draw.text((28, left_y), left_text, font=f_footer, fill=(255, 255, 255))

            right_text = "trendiatr.com"
            right_bbox = f_footer.getbbox(right_text)
            right_w = right_bbox[2] - right_bbox[0]
            right_h = right_bbox[3] - right_bbox[1]
            right_y = footer_y + (footer_bar_h - right_h) // 2 - right_bbox[1]
            draw.text((w - right_w - 28, right_y), right_text, font=f_footer, fill=(255, 255, 255))

            # ── 9. Diamond accent (bottom-right) ──
            dx, dy = w - 48, h - 68
            draw.polygon(
                [(dx, dy - 11), (dx + 11, dy), (dx, dy + 11), (dx - 11, dy)],
                fill=(190, 55, 55)
            )

            output = io.BytesIO()
            img.save(output, format="WEBP", quality=90)
            return output.getvalue()

        except Exception as e:
            logger.error(f"PIL Placeholder Generation Failed: {e}")
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

            # Stages 2–5: Bing → DuckDuckGo → Wikipedia → PIL Placeholder
            if not image_data:
                search_query = None
                active_entity_name = None
                loop = asyncio.get_event_loop()

                if news.trend_id:
                    trend = db.query(Trend).filter(Trend.id == news.trend_id).first()
                    if trend:
                        # Build search query from stored entities first
                        if trend.entities and isinstance(trend.entities, dict):
                            ai_image_query = trend.entities.get('image_search_query')
                            if ai_image_query and len(ai_image_query) > 2:
                                search_query = ai_image_query
                            else:
                                people = trend.entities.get('people', [])
                                orgs = trend.entities.get('organizations', [])
                                if people or orgs:
                                    search_query = " ".join(people[:2] + orgs[:1])

                        # No good query yet — ask Ollama for visual keywords
                        if not search_query and trend.title:
                            search_query = await loop.run_in_executor(
                                None, self.generate_visual_query_with_ollama, trend.title
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

                # Stage 2: Bing image search
                if search_query:
                    _meta = news.media_meta if isinstance(news.media_meta, dict) else {}
                    bing_tries = _meta.get('bing_tries', 0)
                    if bing_tries < _MAX_BING_TRIES:
                        clean_sq = search_query.split('📰')[0].split('|')[0].split('-')[0].strip()
                        logger.info(f"🔍 [Stage 2] Bing search (try {bing_tries + 1}/{_MAX_BING_TRIES}): '{clean_sq[:40]}'")
                        image_data, fallback_url = await loop.run_in_executor(
                            None, self.download_from_bing_images, clean_sq
                        )
                        if image_data:
                            source_url = fallback_url
                            logger.info("✅ [Stage 2] Bing image downloaded.")
                        else:
                            _meta['bing_tries'] = bing_tries + 1
                            news.media_meta = _meta
                    else:
                        logger.info(f"⏭️ [Stage 2] Bing budget exhausted ({bing_tries}), skipping to Stage 3.")

                # Stage 3: DuckDuckGo image search
                if not image_data and search_query:
                    logger.info(f"🦆 [Stage 3] DuckDuckGo search: '{search_query[:40]}'")
                    image_data, fallback_url = await loop.run_in_executor(
                        None, self.download_from_duckduckgo_images, search_query
                    )
                    if image_data:
                        source_url = fallback_url
                        logger.info("✅ [Stage 3] DuckDuckGo image downloaded.")

                # Stage 4: Wikipedia (up to 3 named entities)
                if not image_data and trend and trend.entities and isinstance(trend.entities, dict):
                    people = trend.entities.get('people', [])
                    orgs = trend.entities.get('organizations', [])
                    for entity in (people[:2] + orgs[:1])[:3]:
                        if not entity:
                            continue
                        logger.info(f"📖 [Stage 4] Wikipedia search for: {entity}")
                        wiki_data = await loop.run_in_executor(None, self.download_from_wikipedia, entity)
                        if wiki_data:
                            image_data = wiki_data
                            source_url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(entity)}"
                            source_label = "Wikipedia"
                            logger.info(f"✅ [Stage 4] Wikipedia image found for: {entity}")
                            break

                # Stage 5: PIL placeholder (final safety net)
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
