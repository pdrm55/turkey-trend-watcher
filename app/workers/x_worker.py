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

            # 2. Define Thresholds
            threshold_setting = db.query(SystemSettings).filter_by(key="x_publish_threshold").first()
            try:
                confirm_threshold = float(threshold_setting.value) if threshold_setting else 70.0
            except ValueError:
                confirm_threshold = 70.0
                
            radar_threshold = 30.0

            # ==========================================
            # PHASE 1: RADAR (Early Signals)
            # ==========================================
            # Subquery to check for existing radar drafts (draft, sent, or discarded)
            radar_draft_subquery = db.query(XDraft.trend_id).filter(XDraft.draft_type == 'radar')
            
            radar_candidates = db.query(Trend).filter(
                Trend.is_active == True,
                Trend.final_tps >= radar_threshold,
                Trend.final_tps < confirm_threshold,
                ~Trend.id.in_(radar_draft_subquery)
            ).order_by(desc(Trend.final_tps)).limit(2).all()

            for trend in radar_candidates:
                try:
                    logger.info(f"📡 Processing Phase 1 (Radar) for Trend {trend.id}: {trend.title}")
                    
                    # Generate Content
                    context_text = trend.summary if trend.summary else trend.title
                    ai_data = generate_x_content(trend.title, context_text, trend.category)
                    
                    if not ai_data: continue

                    # Generate Image
                    tps_val = round(trend.final_tps, 1)
                    image_path = generate_x_image(trend.id, trend.title, ai_data['image_short_text'], tps_val)
                    if not image_path: continue

                    # Construct Caption
                    slug_part = trend.slug if trend.slug else trend.id
                    full_link = f"{BASE_SITE_URL}/trend/{slug_part}"
                    
                    utm_params = "utm_source=x&utm_medium=post&utm_campaign=x_studio"
                    separator = "&" if "?" in full_link else "?"
                    full_link = f"{full_link}{separator}{utm_params}"
                    
                    spread_speed = round(tps_val / 7.5, 1)
                    hashtags = ai_data.get('hashtags', [])
                    hash1 = hashtags[0] if len(hashtags) > 0 else trend.category
                    hash2 = hashtags[1] if len(hashtags) > 1 else "Gündem"

                    # Force Confidence Label for Phase 1
                    conf_label = "⏳ İnceleniyor - İlk Sinyaller"
                    confidence_val = getattr(trend, 'tps_confidence', 0.85)
                    confidence_pct = int((confidence_val if confidence_val is not None else 0.85) * 100)

                    # 2. Calculate Trend Power (Gündem Gücü)
                    power_label = "Kritik" if tps_val >= 80 else "Yüksek" if tps_val >= 50 else "Dikkat Çekici"

                    # Sanitize the question to prevent double emojis
                    clean_question = ai_data.get('interaction_question', '').replace("💬", "").strip()

                    main_tweet = (
                        f"📡 **Sinyal Algılandı: #{hash1}**\n"
                        f"💬 **Bu neden trend?** Son dakikalarda X Türkiye'de ani bir etkileşim patlaması yaşanıyor.\n\n"
                        f"{ai_data['ai_summary']}\n\n"
                        f"🛡️ Güven Endeksi: %{confidence_pct} ({conf_label})\n"
                        f"📈 Gündem Gücü: {power_label} (Normalden {spread_speed}x daha hızlı yayılıyor)\n\n"
                        f"💬 {clean_question}\n\n"
                        f"#{hash1} #{hash2} #TrendiaTR"
                    )
                    
                    reply_tweet = f"Olayın detayları araştırılıyor... Gelişmeler için: 👇 🔗\n{full_link}"
                    
                    caption = f"{main_tweet}\n\n====REPLY====\n\n{reply_tweet}"

                    # Save Draft
                    draft = XDraft(
                        trend_id=trend.id,
                        hook_text=ai_data['ai_summary'][:50],
                        long_caption=caption,
                        image_short_text=ai_data['image_short_text'],
                        tps_score=tps_val,
                        image_path=image_path,
                        status='draft',
                        draft_type='radar'
                    )
                    db.add(draft)
                    trend.radar_phase_triggered = True
                    db.commit()
                    logger.info(f"✅ Created Phase 1 (Radar) Draft for Trend {trend.id}")
                    notify_admin_x_draft(trend.title, tps_val, "Radar (Phase 1)")
                    
                    # Rate limit between generations
                    time.sleep(5)

                except Exception as e:
                    logger.error(f"Error in Phase 1 (Radar) for trend {trend.id}: {e}")
                    continue

            # ==========================================
            # PHASE 2: CONFIRMED (High Score)
            # ==========================================
            # Subquery to check for existing confirmed drafts
            confirmed_draft_subquery = db.query(XDraft.trend_id).filter(XDraft.draft_type == 'confirmed')
            
            confirmed_candidates = db.query(Trend).filter(
                Trend.is_active == True,
                Trend.final_tps >= confirm_threshold,
                ~Trend.id.in_(confirmed_draft_subquery)
            ).order_by(desc(Trend.final_tps)).limit(2).all()

            for trend in confirmed_candidates:
                try:
                    logger.info(f"🚨 Processing Phase 2 (Confirmed) for Trend {trend.id}: {trend.title}")
                    
                    context_text = trend.summary if trend.summary else trend.title
                    ai_data = generate_x_content(trend.title, context_text, trend.category)
                    
                    if not ai_data: continue

                    tps_val = round(trend.final_tps, 1)
                    image_path = generate_x_image(trend.id, trend.title, ai_data['image_short_text'], tps_val)
                    if not image_path: continue

                    slug_part = trend.slug if trend.slug else trend.id
                    full_link = f"{BASE_SITE_URL}/trend/{slug_part}"
                    
                    utm_params = "utm_source=x&utm_medium=post&utm_campaign=x_studio"
                    separator = "&" if "?" in full_link else "?"
                    full_link = f"{full_link}{separator}{utm_params}"
                    
                    spread_speed = round(tps_val / 7.5, 1)
                    hashtags = ai_data.get('hashtags', [])
                    hash1 = hashtags[0] if len(hashtags) > 0 else trend.category
                    hash2 = hashtags[1] if len(hashtags) > 1 else "Gündem"

                    # 1. Standard Confidence Label for Phase 2
                    confidence_val = getattr(trend, 'tps_confidence', 0.85)
                    confidence_pct = int((confidence_val if confidence_val is not None else 0.85) * 100)
                    
                    if confidence_pct >= 90:
                        conf_label = "Teyitli Kaynaklar"
                    elif confidence_pct >= 75:
                        conf_label = "Güvenilir Veri"
                    else:
                        conf_label = "Gelişmekte Olan Haber"

                    # 2. Calculate Trend Power
                    power_label = "Kritik" if tps_val >= 80 else "Yüksek" if tps_val >= 50 else "Dikkat Çekici"

                    clean_question = ai_data.get('interaction_question', '').replace("💬", "").strip()

                    main_tweet = (
                        f"🚨 **DOĞRULANDI (Sistem Güncellemesi):**\n\n"
                        f"{ai_data['ai_summary']}\n\n"
                        f"🛡️ Güven Endeksi: %{confidence_pct} ({conf_label})\n"
                        f"📈 Gündem Gücü: {power_label} (Normalden {spread_speed}x daha hızlı yayılıyor)\n\n"
                        f"💬 {clean_question}\n\n"
                        f"#{hash1} #{hash2} #TrendiaTR"
                    )
                    
                    reply_tweet = f"Olayın tüm detayları, resmi açıklamalar ve güncel gelişmeler için: 👇 🔗\n{full_link}"
                    
                    caption = f"{main_tweet}\n\n====REPLY====\n\n{reply_tweet}"

                    # Save Draft (Reply to Radar Phase if it exists)
                    draft = XDraft(
                        trend_id=trend.id,
                        hook_text=ai_data['ai_summary'][:50],
                        long_caption=caption,
                        image_short_text=ai_data['image_short_text'],
                        tps_score=tps_val,
                        image_path=image_path,
                        status='draft',
                        draft_type='confirmed',
                        reply_to_tweet_id=trend.radar_tweet_id
                    )
                    db.add(draft)
                    db.commit()
                    logger.info(f"✅ Created Phase 2 (Confirmed) Draft for Trend {trend.id}")
                    notify_admin_x_draft(trend.title, tps_val, "Confirmed (Phase 2)")
                    
                    time.sleep(5)

                except Exception as e:
                    logger.error(f"Error in Phase 2 (Confirmed) for trend {trend.id}: {e}")
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