import sys
import os
import time
import math
import logging
from datetime import datetime, timezone, timedelta

# اضافه کردن مسیر ریشه پروژه به sys.path برای دسترسی به ماژول‌های داخلی
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.database.models import SessionLocal, Trend, RawNews, TrendScoreHistory
from app.core.scoring import TPSCalculator
from app.core.scoring_queue import scoring_queue
from app.core.translation import sweep_untranslated
from app.config import Config

# Redis client for FA translation sweep
try:
    import redis as _redis_lib
    _redis_fa = _redis_lib.from_url(
        f"redis://{os.getenv('REDIS_HOST', 'ttw_redis')}:6379/0",
        decode_responses=True, socket_connect_timeout=2
    )
    _redis_fa.ping()
except Exception:
    _redis_fa = None

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

# Fix 4: disaster/emergency keywords — classifier never emits "afet", so we check titles directly
_AFET_KEYWORDS = frozenset({
    'deprem', 'yangın', 'yangin', 'sel', 'patlama', 'heyelan',
    'fırtına', 'firtina', 'tsunami', 'kasırga', 'kasirga', 'tufan',
})
AFET_DECAY_FACTOR = 0.82  # faster decay: emergency news loses relevance quickly

# Fix 6: process active trends in chunks to avoid loading thousands of rows at once
GRAVITY_BATCH_SIZE = 100

MIN_TPS_THRESHOLD = 3.0
DECAY_CHECK_INTERVAL = 1800   # هر ۳۰ دقیقه برای Gravity
SCORING_CHECK_INTERVAL = 5    # هر ۵ ثانیه برای امتیازدهی اخبار جدید (Async)
GC_CHECK_INTERVAL = 21600     # هر ۶ ساعت برای پاکسازی فایل‌های مدیا (Garbage Collection)
FA_SWEEP_INTERVAL = 1800      # هر ۳۰ دقیقه: ترجمه ترندهایی که fa_title/fa_summary ندارند
FA_SWEEP_BATCH = 8            # تعداد ترند در هر دور sweep
QUEUE_METRICS_LOG_INTERVAL = 60

def _is_afet_trend(title: str) -> bool:
    """Return True if the trend title contains any disaster/emergency keyword."""
    if not title:
        return False
    lower = title.lower()
    return any(kw in lower for kw in _AFET_KEYWORDS)


def cleanup_inactive_media():
    """
    Garbage Collection: Delete physical video files of inactive trends to save disk space.
    """
    db = SessionLocal()
    try:
        inactive_video_trends = db.query(Trend).filter(
            Trend.is_active == False,
            Trend.video_path.isnot(None)
        ).all()

        if not inactive_video_trends:
            return

        logger.info(f"🧹 [GC] Found {len(inactive_video_trends)} inactive trends with videos. Starting cleanup...")
        
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cleaned_count = 0

        for trend in inactive_video_trends:
            if trend.video_path:
                file_path = os.path.join(base_dir, 'static', trend.video_path.lstrip('/'))
                
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        logger.info(f"🗑️ [GC] Deleted video file: {file_path}")
                    else:
                        logger.warning(f"⚠️ [GC] Video file not found: {file_path}")
                except Exception as e:
                    logger.error(f"❌ [GC] Failed to delete file {file_path}: {e}")

                # Set RawNews video_path to None as well
                raw_news_items = db.query(RawNews).filter(RawNews.trend_id == trend.id).all()
                for news in raw_news_items:
                    if news.video_path == trend.video_path:
                        news.video_path = None

                trend.video_path = None
                cleaned_count += 1

        db.commit()
        logger.info(f"✅ [GC] Cleanup finished. Removed videos from {cleaned_count} trends.")

        # Fix 7: prune TrendScoreHistory rows older than 48 hours
        try:
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)
            deleted_rows = db.query(TrendScoreHistory).filter(
                TrendScoreHistory.timestamp < cutoff
            ).delete(synchronize_session=False)
            db.commit()
            if deleted_rows:
                logger.info(f"🗑️ [GC] Pruned {deleted_rows} stale TrendScoreHistory rows (>48h).")
        except Exception as e:
            db.rollback()
            logger.error(f"❌ [GC] TrendScoreHistory prune error: {e}")

    except Exception as e:
        db.rollback()
        logger.error(f"❌ [GC] Database error during cleanup: {e}")
    finally:
        db.close()

def process_pending_scores():
    """
    وظیفه ۱ (جدید در فاز ۶.۲): پردازش صف اخبار جدید و محاسبه امتیاز TPS.
    این تابع جایگزین محاسبات همزمان در اسکرپرها شده است.
    """
    db = SessionLocal()
    tps_engine = TPSCalculator(db)
    
    try:
        batch_size = max(1, getattr(Config, "SCORING_QUEUE_BATCH_SIZE", 50))
        pending_trends = []

        # Priority path: consume trend ids from Redis queue first.
        queue_priority_by_id = {}
        if scoring_queue.enabled:
            for _ in range(batch_size):
                queue_item = scoring_queue.pop()
                if queue_item is None:
                    break
                trend_id, lane = queue_item
                queue_priority_by_id[trend_id] = lane

            queued_ids = list(queue_priority_by_id.keys())
            if queued_ids:
                pending_trends = db.query(Trend).filter(
                    Trend.id.in_(queued_ids),
                    Trend.is_active == True
                ).all()

        # Fallback path: old DB flag scan to avoid dropped jobs during outages.
        if not pending_trends:
            pending_trends = db.query(Trend).filter(
                Trend.needs_scoring == True,
                Trend.is_active == True
            ).limit(batch_size).all()

        if not pending_trends:
            return False # کار خاصی انجام نشد

        count = len(pending_trends)
        queue_size = scoring_queue.size() if scoring_queue.enabled else 0
        logger.info(f"🚀 [Async Scoring] Found {count} trends needing update (queue_size={queue_size})...")

        for trend in pending_trends:
            try:
                # اجرای چرخه کامل امتیازدهی (Velocity, Acceleration, LLM)
                new_score = tps_engine.run_tps_cycle(trend.id)
                
                # پس از محاسبه موفق، پرچم را پایین بیاور
                if new_score is not None:
                    trend.needs_scoring = False
                    if scoring_queue.enabled:
                        scoring_queue.clear_retry(trend.id)
                else:
                    if scoring_queue.enabled and trend.id in queue_priority_by_id:
                        scoring_queue.retry_or_drop(trend.id, queue_priority_by_id[trend.id])
                    
            except Exception as inner_e:
                logger.error(f"❌ Error scoring trend {trend.id}: {inner_e}")
                if scoring_queue.enabled and trend.id in queue_priority_by_id:
                    scoring_queue.retry_or_drop(trend.id, queue_priority_by_id[trend.id])
        
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
    Fix 6: processes trends in batches of GRAVITY_BATCH_SIZE to avoid loading
    all rows into memory at once.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        decay_count = 0
        deactivated_count = 0
        last_id = 0
        total_seen = 0

        while True:
            # Fix 6: chunked pagination — never load all active trends at once.
            # Keyset, not LIMIT/OFFSET: the loop below sets is_active = False and
            # commits before advancing, and the query filters on is_active, so every
            # archived trend shifted the remaining rows down by one. A cycle that
            # archived 30 of a 100-row page then skipped the next 30 active trends
            # entirely — they silently never decayed. Seeking on id has no such
            # interaction with the predicate.
            batch = (
                db.query(Trend)
                .filter(Trend.is_active == True, Trend.id > last_id)
                .order_by(Trend.id)
                .limit(GRAVITY_BATCH_SIZE)
                .all()
            )
            if not batch:
                break

            if last_id == 0:
                logger.info(f"📉 [Gravity] Starting decay cycle (batch_size={GRAVITY_BATCH_SIZE})...")

            for trend in batch:
                time_diff = now - trend.last_updated
                hours_passed = time_diff.total_seconds() / 3600.0

                if hours_passed >= 1.0:
                    # Fast cleanup for orphaned/noise trends
                    if trend.final_tps < 3.0:
                        trend.is_active = False
                        deactivated_count += 1
                        continue

                    category = trend.category if trend.category else "Default"
                    decay_factor = CATEGORY_DECAY_FACTORS.get(category, CATEGORY_DECAY_FACTORS["Default"])

                    # Fix 4: disaster news (deprem, yangın, sel…) decays faster
                    # The classifier never emits "afet" — check the title directly.
                    if _is_afet_trend(trend.title):
                        decay_factor = min(decay_factor, AFET_DECAY_FACTOR)

                    old_score = trend.final_tps
                    new_score = old_score * math.pow(decay_factor, hours_passed)

                    trend.final_tps = new_score
                    trend.score = new_score

                    if new_score < 2.0:
                        trend.is_active = False
                        deactivated_count += 1

                    # --- Smart Score History Logging (Gravity) ---
                    history_entry = TrendScoreHistory(
                        trend_id=trend.id,
                        tps_score=new_score,
                        timestamp=now,
                        event_type='gravity'
                    )
                    db.add(history_entry)
                    decay_count += 1

            total_seen += len(batch)
            # Advance before committing: after the commit the archived rows no
            # longer satisfy the filter, and batch[-1].id is the only cursor that
            # survives that.
            last_id = batch[-1].id
            db.commit()

            if len(batch) < GRAVITY_BATCH_SIZE:
                break

        logger.info(
            f"✅ [Gravity] Cycle done. Processed: {total_seen} | "
            f"Decayed: {decay_count} | Archived: {deactivated_count}"
        )

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
    last_gc_time = time.time()
    last_fa_sweep_time = time.time() - FA_SWEEP_INTERVAL  # run first sweep soon after start
    last_queue_metrics_time = time.time()

    while True:
        try:
            # ۱. اولویت بالا: امتیازدهی به اخبار جدید
            did_work = process_pending_scores()

            # ۲. اولویت پایین: بررسی زمان اجرای Gravity
            current_time = time.time()
            if current_time - last_decay_time > DECAY_CHECK_INTERVAL:
                apply_gravity_decay()
                last_decay_time = current_time

            # ۳. Garbage Collection: Media Cleanup
            if current_time - last_gc_time > GC_CHECK_INTERVAL:
                cleanup_inactive_media()
                last_gc_time = current_time

            # ۴. FA Translation Sweep: ترجمه ترندهای بدون fa_title/fa_summary
            if current_time - last_fa_sweep_time > FA_SWEEP_INTERVAL:
                try:
                    count = sweep_untranslated(_redis_fa, batch_size=FA_SWEEP_BATCH)
                    if count:
                        logger.info(f"🇮🇷 [FA Sweep] Translated {count} trend(s)")
                except Exception as sweep_err:
                    logger.error(f"❌ [FA Sweep] Error: {sweep_err}")
                last_fa_sweep_time = current_time

            if scoring_queue.enabled and (current_time - last_queue_metrics_time > QUEUE_METRICS_LOG_INTERVAL):
                logger.info(f"📊 [Queue Metrics] scoring_pending={scoring_queue.size()}")
                last_queue_metrics_time = current_time
            
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