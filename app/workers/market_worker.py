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

# Add project root to sys path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.config import Config
from app.database.models import SessionLocal, MarketAsset, MarketHistory

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("MarketWorker")

class MarketWorker:
    def __init__(self):
        self.redis_client = None
        try:
            self.redis_client = redis.from_url(Config.REDIS_URL, decode_responses=True)
            logger.info("✅ Redis Connected for Market Data.")
        except Exception as e:
            logger.error(f"❌ Redis Connection Error: {e}")

    def fetch_bigpara_data(self):
        """Scrapes currency and gold data from BigPara"""
        url = "https://bigpara.hurriyet.com.tr/doviz/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        data = {}
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                logger.error(f"BigPara HTTP Error: {response.status_code}")
                return {}

            soup = BeautifulSoup(response.content, "html.parser")
            
            # Mapping BigPara labels to our symbols
            # Note: Selectors might need adjustment if BigPara changes layout
            # This is a generic robust selector strategy
            items = soup.select(".dovizBar .dbItem")
            
            for item in items:
                try:
                    label = item.select_one(".dbType").text.strip()
                    price_str = item.select_one(".dbValue").text.strip().replace(',', '.')
                    change_str = item.select_one(".dbChange").text.strip().replace('%', '').replace(',', '.')
                    
                    price = float(price_str)
                    change = float(change_str)
                    
                    symbol = None
                    if "DOLAR" in label: symbol = "USDTRY"
                    elif "EURO" in label: symbol = "EURTRY"
                    elif "GRAM ALTIN" in label: symbol = "GRAM-ALTIN"
                    
                    if symbol:
                        data[symbol] = {"price": price, "change": change}
                        
                except Exception as e:
                    continue
                    
            # Fallback for ONS if not in header bar
            # (Implementation simplified for reliability, can be expanded)
            data["ONS"] = {"price": 0.0, "change": 0.0} # Placeholder if not scraped
            
            return data
        except Exception as e:
            logger.error(f"BigPara Scraping Error: {e}")
            return {}

    def fetch_bist100(self):
        """Fetches BIST 100 data using yfinance"""
        try:
            ticker = yf.Ticker("XU100.IS")
            hist = ticker.history(period="2d")
            
            if len(hist) < 2:
                # If market just opened or data is scarce, use regular info
                info = ticker.info
                price = info.get('regularMarketPrice', 0)
                prev_close = info.get('previousClose', price)
            else:
                price = hist['Close'].iloc[-1]
                prev_close = hist['Close'].iloc[-2]
            
            change = ((price - prev_close) / prev_close) * 100
            
            return {"BIST100": {"price": price, "change": change}}
        except Exception as e:
            logger.error(f"YFinance Error: {e}")
            return {}

    def update_cache(self, data):
        """Updates Redis cache with latest market data"""
        if not self.redis_client or not data: return
        try:
            self.redis_client.setex("market_ticker", 120, json.dumps(data))
            logger.info("✅ Redis Cache Updated")
        except Exception as e:
            logger.error(f"Redis Update Error: {e}")

    def save_history(self, data):
        """Saves snapshot to PostgreSQL history table"""
        if not data: return
        
        db = SessionLocal()
        try:
            assets = db.query(MarketAsset).filter(MarketAsset.is_active == True).all()
            asset_map = {a.symbol: a.id for a in assets}
            
            for symbol, values in data.items():
                if symbol in asset_map:
                    history = MarketHistory(
                        asset_id=asset_map[symbol],
                        price=values['price'],
                        change_rate=values['change'],
                        timestamp=datetime.now(timezone.utc).replace(tzinfo=None)
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
        history_counter = 0
        
        while True:
            # 1. Fetch Data
            market_data = self.fetch_bigpara_data()
            bist_data = self.fetch_bist100()
            market_data.update(bist_data)
            
            # 2. Update Cache (Every cycle)
            self.update_cache(market_data)
            
            # 3. Save History (Every 15 cycles -> 15 mins)
            if history_counter >= 15:
                self.save_history(market_data)
                history_counter = 0
            else:
                history_counter += 1
            
            time.sleep(60)

if __name__ == "__main__":
    worker = MarketWorker()
    worker.run()