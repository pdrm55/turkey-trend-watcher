import sys
import os
import time
import json
import logging
import redis
import yfinance as yf
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
                    MarketAsset(symbol="GOLD-USD", name="Altın (Ounce/USD)", asset_type="gold"),
                    MarketAsset(symbol="BTC-USD", name="Bitcoin (USD)", asset_type="crypto"),
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

    def fetch_all_market_data(self):
        """Fetches all market data using yfinance for stability."""
        # Mapping: YFinance Symbol -> DB Symbol
        tickers_map = {
            "USDTRY=X": "USDTRY",
            "EURTRY=X": "EURTRY",
            "GC=F": "GOLD-USD",
            "BTC-USD": "BTC-USD",
            "XU100.IS": "BIST100"
        }
        
        data = {}
        try:
            # Fetch all tickers at once
            tickers = yf.Tickers(" ".join(tickers_map.keys()))
            
            for yf_symbol, db_symbol in tickers_map.items():
                try:
                    ticker = tickers.tickers[yf_symbol]
                    
                    # Try to get history for accurate price and change
                    hist = ticker.history(period="2d")
                    
                    if not hist.empty:
                        price = hist['Close'].iloc[-1]
                        if len(hist) > 1:
                            prev_close = hist['Close'].iloc[-2]
                        else:
                            prev_close = ticker.info.get('regularMarketPreviousClose', price)
                        
                        change = ((price - prev_close) / prev_close) * 100
                        
                        data[db_symbol] = {
                            "price": safe_float(price),
                            "change": safe_float(change)
                        }
                except Exception as e:
                    logger.error(f"Error processing {yf_symbol}: {e}")
                    continue
            
            return data
        except Exception as e:
            logger.error(f"YFinance Batch Error: {e}")
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
                market_data = self.fetch_all_market_data()
                
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