import sys
import os
import time
import logging
from datetime import date
from sqlalchemy import desc, func

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.database.models import SessionLocal, Trend, XDraft, SystemSettings
from app.core.x_ai_service import generate_x_content, _detect_content_type
from app.core.x_image_gen import generate_x_image
from app.core.tg_notifier import notify_admin_x_draft

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("XWorker")

BASE_SITE_URL = os.getenv("BASE_SITE_URL", "https://trendiatr.com")
REPLY_SEPARATOR = "\n\n====REPLY====\n\n"


def run_worker():
    logger.info("🚀 X (Twitter) Draft Worker Started")

    while True:
        db = SessionLocal()
        try:
            # 1. Check Auto-Pilot Status
            status_setting = db.query(SystemSettings).filter_by(key="x_auto_pilot_status").first()
            if not status_setting or status_setting.value != "True":
                time.sleep(15)
                continue

            # 2. Load thresholds from SystemSettings
            threshold_setting = db.query(SystemSettings).filter_by(key="x_publish_threshold").first()
            try:
                confirm_threshold = float(threshold_setting.value) if threshold_setting else 70.0
            except ValueError:
                confirm_threshold = 70.0

            daily_limit_setting = db.query(SystemSettings).filter_by(key="x_daily_limit").first()
            try:
                daily_limit = int(daily_limit_setting.value) if daily_limit_setting else 15
            except ValueError:
                daily_limit = 15

            # 3. Daily rate limit guard
            today = date.today()
            sent_today = db.query(XDraft).filter(
                XDraft.status == 'sent',
                func.date(XDraft.sent_at) == today
            ).count()
            drafts_today = db.query(XDraft).filter(
                XDraft.status == 'draft',
                func.date(XDraft.created_at) == today
            ).count()
            if sent_today + drafts_today >= daily_limit:
                logger.info(
                    f"📊 Daily limit reached ({sent_today} sent + {drafts_today} draft"
                    f" = {sent_today + drafts_today}/{daily_limit}). Sleeping."
                )
                time.sleep(60)
                continue

            # ==========================================
            # SINGLE PHASE: Draft high-scoring trends
            # ==========================================
            confirmed_draft_subquery = db.query(XDraft.trend_id).filter(XDraft.draft_type == 'confirmed')

            candidates = db.query(Trend).filter(
                Trend.is_active == True,
                Trend.final_tps >= confirm_threshold,
                ~Trend.id.in_(confirmed_draft_subquery)
            ).order_by(desc(Trend.final_tps)).limit(2).all()

            for trend in candidates:
                try:
                    if not trend.summary:
                        logger.info(f"⏭️ Skipping trend {trend.id} — no summary yet")
                        continue

                    content_type = _detect_content_type(trend.category).upper()
                    logger.info(f"✍️ Processing [{content_type}] Trend {trend.id}: {trend.title}")

                    ai_data = generate_x_content(trend.title, trend.summary, trend.category)
                    if not ai_data:
                        continue

                    tps_val = round(trend.final_tps, 1)
                    image_path = generate_x_image(trend.id, trend.title, ai_data['image_short_text'], tps_val)
                    if not image_path:
                        continue

                    slug_part = trend.slug if trend.slug else trend.id
                    link = f"{BASE_SITE_URL}/trend/{slug_part}?utm_source=x&utm_medium=post&utm_campaign=x_studio"

                    main_tweet = ai_data['full_tweet']
                    reply_tweet = f"{ai_data['reply_hook']}\n{link}"
                    caption = f"{main_tweet}{REPLY_SEPARATOR}{reply_tweet}"

                    draft = XDraft(
                        trend_id=trend.id,
                        hook_text=ai_data['full_tweet'][:50],
                        long_caption=caption,
                        image_short_text=ai_data['image_short_text'],
                        tps_score=tps_val,
                        image_path=image_path,
                        status='draft',
                        draft_type='confirmed',
                        reply_to_tweet_id=None  # single-phase: no prior radar tweet
                    )
                    db.add(draft)
                    db.commit()
                    logger.info(f"✅ Created [{content_type}] draft for Trend {trend.id}")
                    notify_admin_x_draft(trend.title, tps_val, f"Auto [{content_type}]")
                    time.sleep(5)

                except Exception as e:
                    logger.error(f"Error processing trend {trend.id}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Worker Loop Error: {e}")
            if db:
                db.rollback()
        finally:
            db.close()

        time.sleep(15)


if __name__ == "__main__":
    run_worker()
