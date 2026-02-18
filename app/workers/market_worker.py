import sys
import os
import time
import json
import logging
import requests
import redis
import yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime, timezone

# اضافه کردن مسیر ریشه پروژه به سیستم
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.config import Config
from app.database.models import SessionLocal, MarketAsset, MarketHistory

# تنظیمات لاگ سیستم
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("MarketWorker")

def safe_float(value):
    """تبدیل ایمن مقادیر به float پایتون و مدیریت تایپ‌های Numpy و رشته‌های ترکی."""
    if value is None:
        return 0.0
    try:
        if isinstance(value, str):
            # جایگزینی کاما با نقطه برای فرمت‌های قیمت ترکی
            value = value.replace('.', '').replace(',', '.').replace('%', '').strip()
        return float(value)
    except Exception:
        return 0.0

class MarketWorker:
    def __init__(self):
        self.redis_client = None
        self.last_history_save = 0
        try:
            self.redis_client = redis.from_url(Config.REDIS_URL, decode_responses=True)
            logger.info("✅ Redis Connected for Market Data.")
        except Exception as e:
            logger.error(f"❌ Redis Connection Error: {e}")

    def seed_assets(self):
        """ایجاد ردیف‌های اولیه در دیتابیس اگر جدول خالی باشد."""
        db = SessionLocal()
        try:
            if db.query(MarketAsset).count() == 0:
                logger.info("🌱 Seeding initial market assets...")
                assets = [
                    MarketAsset(symbol="USDTRY", name="Dolar", asset_type="currency"),
                    MarketAsset(symbol="EURTRY", name="Euro", asset_type="currency"),
                    MarketAsset(symbol="GRAM-ALTIN", name="Gram Altın", asset_type="gold"),
                    MarketAsset(symbol="BIST100", name="Borsa İstanbul", asset_type="stock")
                ]
                db.add_all(assets)
                db.commit()
                logger.info("✅ Seeding complete.")
        except Exception as e:
            logger.error(f"❌ Seeding Error: {e}")
            db.rollback()
        finally:
            db.close()

    def fetch_bigpara_data(self):
        """استخراج داده‌های ارز و طلا از BigPara"""
        url = "https://bigpara.hurriyet.com.tr/doviz/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        data = {}
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                return {}

            soup = BeautifulSoup(response.content, "html.parser")
            
            # استخراج از باکس‌های قیمت (Kur Boxes)
            items = soup.select(".kurBox")
            for item in items:
                try:
                    label = item.select_one(".kurTitle, .kurBoxTitle").text.strip().upper()
                    price = safe_float(item.select_one(".value").text)
                    change_tag = item.select_one(".change")
                    change = safe_float(change_tag.text) if change_tag else 0.0
                    
                    symbol = None
                    if "DOLAR" in label: symbol = "USDTRY"
                    elif "EURO" in label: symbol = "EURTRY"
                    elif "ALTIN" in label and "GRAM" in label: symbol = "GRAM-ALTIN"
                    
                    if symbol:
                        data[symbol] = {"price": price, "change": change}
                except:
                    continue
            
            return data
        except Exception as e:
            logger.error(f"BigPara Scraping Error: {e}")
            return {}

    def fetch_bist100(self):
        """دریافت شاخص BIST 100 با استفاده از yfinance"""
        try:
            ticker = yf.Ticker("XU100.IS")
            # دریافت دیتای ۲ روز اخیر برای محاسبه درصد تغییر دقیق
            hist = ticker.history(period="2d")
            
            if hist.empty:
                return {}
            
            price = hist['Close'].iloc[-1]
            if len(hist) > 1:
                prev_close = hist['Close'].iloc[-2]
                change = ((price - prev_close) / prev_close) * 100
            else:
                change = 0.0
            
            return {"BIST100": {"price": safe_float(price), "change": safe_float(change)}}
        except Exception as e:
            logger.error(f"YFinance Error: {e}")
            return {}

    def update_cache(self, data):
        """آپدیت کش Redis برای نمایش در Ticker سایت"""
        if not self.redis_client or not data: return
        try:
            self.redis_client.setex("market_ticker", 300, json.dumps(data))
            logger.info("✅ Redis Cache Updated")
        except Exception as e:
            logger.error(f"Redis Update Error: {e}")

    def save_history(self, data):
        """ذخیره وضعیت در جدول تاریخچه دیتابیس"""
        if not data: return
        
        db = SessionLocal()
        try:
            assets = db.query(MarketAsset).filter(MarketAsset.is_active == True).all()
            asset_map = {a.symbol: a.id for a in assets}
            
            timestamp = datetime.now(timezone.utc).replace(tzinfo=None)
            
            for symbol, values in data.items():
                if symbol in asset_map:
                    history = MarketHistory(
                        asset_id=asset_map[symbol],
                        price=safe_float(values['price']),
                        change_rate=safe_float(values['change']),
                        timestamp=timestamp
                    )
                    db.add(history)
            
            db.commit()
            logger.info("💾 Market History Saved to DB")
        except Exception as e:
            logger.error(f"DB Save Error: {e}")
            db.rollback()
        finally:
            db.close()

    def run(self):
        logger.info("🚀 Market Worker Started")
        # اطمینان از وجود دارایی‌ها در دیتابیس
        self.seed_assets()
        
        last_history_time = 0
        
        while True:
            try:
                # ۱. جمع‌آوری داده‌ها
                market_data = self.fetch_bigpara_data()
                bist_data = self.fetch_bist100()
                market_data.update(bist_data)
                
                if market_data:
                    # ۲. آپدیت کش (در هر چرخه)
                    self.update_cache(market_data)
                    
                    # ۳. ذخیره تاریخچه (هر ۱۵ دقیقه)
                    current_time = time.time()
                    if current_time - last_history_time >= 900: # 900 seconds = 15 mins
                        self.save_history(market_data)
                        last_history_time = current_time
                
            except Exception as e:
                logger.error(f"Loop error: {e}")
            
            # انتظار ۶۰ ثانیه برای دور بعدی
            time.sleep(60)

if __name__ == "__main__":
    worker = MarketWorker()
    worker.run()