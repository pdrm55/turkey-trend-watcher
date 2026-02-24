import sys
import os
import time
import logging
from sqlalchemy import desc

# Add project root to sys path for internal imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.database.models import SessionLocal, Trend, XDraft, SystemSettings
from app.core.x_ai_service import generate_x_content
from app.core.x_image_gen import generate_x_image
from app.core.tg_notifier import notify_admin_x_draft

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("XWorker")

BASE_SITE_URL = os.getenv("BASE_SITE_URL", "https://trendiatr.com")

def run_worker():
    logger.info("🚀 X (Twitter) Draft Worker Started")
    
    while True:
        db = SessionLocal()
        try:
            # 1. Check Auto-Pilot Status
            status_setting = db.query(SystemSettings).filter_by(key="x_auto_pilot_status").first()
            if not status_setting or status_setting.value != "True":
                db.close()
                time.sleep(15)
                continue

            # 2. Get Threshold
            threshold_setting = db.query(SystemSettings).filter_by(key="x_publish_threshold").first()
            try:
                threshold = float(threshold_setting.value) if threshold_setting else 70.0
            except ValueError:
                threshold = 70.0

            # 3. Find Candidates
            # Subquery to exclude existing drafts
            existing_draft_ids = db.query(XDraft.trend_id)
            
            candidates = db.query(Trend).filter(
                Trend.is_active == True,
                Trend.final_tps >= threshold,
                ~Trend.id.in_(existing_draft_ids)
            ).order_by(desc(Trend.final_tps)).limit(3).all()

            if not candidates:
                db.close()
                time.sleep(15)
                continue

            logger.info(f"Found {len(candidates)} trends eligible for X Drafts (Threshold: {threshold})")

            for trend in candidates:
                try:
                    logger.info(f"Processing Trend {trend.id}: {trend.title}")
                    
                    # Generate Content
                    context_text = trend.summary if trend.summary else trend.title
                    ai_data = generate_x_content(trend.title, context_text, trend.category)
                    
                    if not ai_data:
                        logger.warning(f"Failed to generate AI content for Trend {trend.id}")
                        continue

                    # Generate Image
                    tps_val = round(trend.final_tps, 1)
                    image_path = generate_x_image(trend.id, trend.title, ai_data['image_short_text'], tps_val)
                    
                    if not image_path:
                        logger.warning(f"Failed to generate image for Trend {trend.id}")
                        continue

                    # Construct Caption
                    slug_part = trend.slug if trend.slug else trend.id
                    full_link = f"{BASE_SITE_URL}/trend/{slug_part}"
                    
                    # Add UTM parameters for tracking
                    utm_params = "utm_source=x&utm_medium=post&utm_campaign=x_studio"
                    separator = "&" if "?" in full_link else "?"
                    full_link = f"{full_link}{separator}{utm_params}"
                    
                    caption = (
                        f"{ai_data['hook_text']}\n\n"
                        f"🤖 {ai_data['short_teaser']}\n\n"
                        f"Detaylar: 👇 🔗\n"
                        f"{full_link}\n\n"
                        f"#{trend.category} #TrendiaTR"
                    )

                    # Save Draft
                    draft = XDraft(
                        trend_id=trend.id,
                        hook_text=ai_data['hook_text'],
                        long_caption=caption,
                        image_short_text=ai_data['image_short_text'],
                        tps_score=tps_val,
                        image_path=image_path,
                        status='draft'
                    )
                    db.add(draft)
                    db.commit()
                    logger.info(f"✅ Created X Draft for Trend {trend.id}")
                    notify_admin_x_draft(trend.title, tps_val, "Auto-Pilot")
                    
                    # Rate limit between generations
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