import sys
import os
import time
import math
import logging
from datetime import datetime, timezone

# اضافه کردن مسیر ریشه پروژه به sys.path برای دسترسی به ماژول‌های داخلی
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.database.models import SessionLocal, Trend
from app.core.scoring import TPSCalculator

# تنظیمات لاگینگ
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ProcessorWorker")

# --- تنظیمات میرایی داینامیک بر اساس دسته‌بندی (Dynamic Decay Configuration) ---
CATEGORY_DECAY_FACTORS = {
    "Siyaset": 0.98,    # سیاست: بسیار ماندگار (فقط ۲٪ کاهش در هر ساعت)
    "Ekonomi": 0.97,    # اقتصاد: ماندگار (۳٪ کاهش در هر ساعت)
    "Teknoloji": 0.94,  # تکنولوژی: میان‌رده
    "Gündem": 0.92,     # عمومی/حوادث: میرایی نسبتاً سریع
    "Spor": 0.85,       # ورزش: میرایی بسیار سریع (۱۵٪ کاهش در هر ساعت)
    "Sanat": 0.88,      # هنر و مجله: میرایی سریع
    "Default": 0.93     # نرخ پیش‌فرض برای دسته‌های ناشناخته
}

MIN_TPS_THRESHOLD = 3.0
DECAY_CHECK_INTERVAL = 1800  # هر ۳۰ دقیقه برای Gravity
SCORING_CHECK_INTERVAL = 5   # هر ۵ ثانیه برای امتیازدهی اخبار جدید (Async)

def process_pending_scores():
    """
    وظیفه ۱ (جدید در فاز ۶.۲): پردازش صف اخبار جدید و محاسبه امتیاز TPS.
    این تابع جایگزین محاسبات همزمان در اسکرپرها شده است.
    """
    db = SessionLocal()
    tps_engine = TPSCalculator(db)
    
    try:
        # دریافت ترندهایی که نیاز به امتیازدهی دارند (تا ۵۰ مورد در هر چرخه)
        pending_trends = db.query(Trend).filter(
            Trend.needs_scoring == True,
            Trend.is_active == True
        ).limit(50).all()

        if not pending_trends:
            return False # کار خاصی انجام نشد

        count = len(pending_trends)
        logger.info(f"🚀 [Async Scoring] Found {count} trends needing update...")

        for trend in pending_trends:
            try:
                # اجرای چرخه کامل امتیازدهی (Velocity, Acceleration, LLM)
                new_score = tps_engine.run_tps_cycle(trend.id)
                
                # پس از محاسبه موفق، پرچم را پایین بیاور
                if new_score is not None:
                    trend.needs_scoring = False
                    
            except Exception as inner_e:
                logger.error(f"❌ Error scoring trend {trend.id}: {inner_e}")
        
        db.commit()
        return True # کار انجام شد (برای مدیریت زمان خواب)

    except Exception as e:
        logger.error(f"❌ Async Scoring Loop Error: {e}")
        return False
    finally:
        db.close()

def apply_gravity_decay():
    """
    وظیفه ۲: اعمال نرخ میرایی هوشمند (Gravity 2.0).
    """
    db = SessionLocal()
    try:
        active_trends = db.query(Trend).filter(
            Trend.is_active == True,
            Trend.final_tps > MIN_TPS_THRESHOLD
        ).all()

        if not active_trends:
            return

        logger.info(f"📉 [Gravity] Starting decay cycle for {len(active_trends)} trends...")
        
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        decay_count = 0
        deactivated_count = 0

        for trend in active_trends:
            time_diff = now - trend.last_updated
            hours_passed = time_diff.total_seconds() / 3600.0

            if hours_passed >= 1.0:
                category = trend.category if trend.category else "Default"
                decay_factor = CATEGORY_DECAY_FACTORS.get(category, CATEGORY_DECAY_FACTORS["Default"])
                
                old_score = trend.final_tps
                new_score = old_score * math.pow(decay_factor, hours_passed)
                
                trend.final_tps = new_score
                trend.score = new_score
                
                if new_score < 2.0:
                    trend.is_active = False
                    deactivated_count += 1
                
                decay_count += 1

        db.commit()
        logger.info(f"✅ [Gravity] Cycle done. Decayed: {decay_count} | Archived: {deactivated_count}")

    except Exception as e:
        db.rollback()
        logger.error(f"❌ [Gravity] Error: {e}")
    finally:
        db.close()

def main():
    """
    حلقه اصلی "Worker محاسباتی".
    هم امتیازدهی سریع (Async Scoring) و هم تضعیف کند (Gravity) را مدیریت می‌کند.
    """
    logger.info("🪐 TrendiaTR Calculation Worker (Async Scoring + Gravity 2.0) Started.")
    
    last_decay_time = time.time()
    
    while True:
        try:
            # ۱. اولویت بالا: امتیازدهی به اخبار جدید
            did_work = process_pending_scores()
            
            # ۲. اولویت پایین: بررسی زمان اجرای Gravity
            current_time = time.time()
            if current_time - last_decay_time > DECAY_CHECK_INTERVAL:
                apply_gravity_decay()
                last_decay_time = current_time
            
            # مدیریت هوشمند خواب: اگر کار بود فقط ۱ ثانیه، اگر نبود ۵ ثانیه صبر کن
            sleep_time = 1 if did_work else SCORING_CHECK_INTERVAL
            time.sleep(sleep_time)
            
        except KeyboardInterrupt:
            logger.info("🛑 Service stopped manually.")
            break
        except Exception as e:
            logger.error(f"❌ Critical Worker Loop Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()