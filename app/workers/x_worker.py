import sys
import os
import time
import logging
from datetime import date
from sqlalchemy import desc, func

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.database.models import SessionLocal, Trend, XDraft, SystemSettings
from app.core.x_ai_service import generate_x_content
from app.core.x_image_gen import generate_x_image
from app.core.tg_notifier import notify_admin_x_draft

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("XWorker")

BASE_SITE_URL = os.getenv("BASE_SITE_URL", "https://trendiatr.com")

# Fix #7: named constant instead of magic string
REPLY_SEPARATOR = "\n\n====REPLY====\n\n"


# Fix #3: shared helper — eliminates duplicate code between Phase 1 and Phase 2
def _build_post_meta(trend, ai_data, tps_val):
    slug_part = trend.slug if trend.slug else trend.id
    link = f"{BASE_SITE_URL}/trend/{slug_part}"
    sep = "&" if "?" in link else "?"
    full_link = f"{link}{sep}utm_source=x&utm_medium=post&utm_campaign=x_studio"
    hashtags = ai_data.get('hashtags', [])
    confidence_val = trend.tps_confidence if trend.tps_confidence is not None else 0.85
    return {
        "full_link": full_link,
        "spread_speed": round(tps_val / 7.5, 1),
        "hash1": hashtags[0] if hashtags else trend.category,
        "hash2": hashtags[1] if len(hashtags) > 1 else "Gündem",
        "power_label": "Kritik" if tps_val >= 80 else "Yüksek" if tps_val >= 50 else "Dikkat Çekici",
        "clean_question": ai_data.get('interaction_question', '').replace("💬", "").strip(),
        "confidence_pct": int(confidence_val * 100),
    }


def run_worker():
    logger.info("🚀 X (Twitter) Draft Worker Started")

    while True:
        db = SessionLocal()
        try:
            # 1. Check Auto-Pilot Status
            status_setting = db.query(SystemSettings).filter_by(key="x_auto_pilot_status").first()
            if not status_setting or status_setting.value != "True":
                # Fix #6: removed duplicate db.close() — finally block handles it
                time.sleep(15)
                continue

            # 2. Load all thresholds from SystemSettings
            threshold_setting = db.query(SystemSettings).filter_by(key="x_publish_threshold").first()
            try:
                confirm_threshold = float(threshold_setting.value) if threshold_setting else 70.0
            except ValueError:
                confirm_threshold = 70.0

            # Fix #2: radar_threshold now reads from SystemSettings (was hardcoded 50.0)
            radar_setting = db.query(SystemSettings).filter_by(key="x_radar_threshold").first()
            try:
                radar_threshold = float(radar_setting.value) if radar_setting else 50.0
            except ValueError:
                radar_threshold = 50.0

            # Fix #1: daily rate limit guard
            daily_limit_setting = db.query(SystemSettings).filter_by(key="x_daily_limit").first()
            try:
                daily_limit = int(daily_limit_setting.value) if daily_limit_setting else 15
            except ValueError:
                daily_limit = 15

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
            # CLEANUP: Remove Dead Radars (TPS < 10)
            # ==========================================
            dead_radars = db.query(Trend).filter(
                Trend.radar_phase_triggered == True,
                Trend.final_tps < 10.0
            ).all()
            if dead_radars:
                for dr in dead_radars:
                    dr.radar_phase_triggered = False
                db.commit()
                logger.info(f"🧹 Cleaned up {len(dead_radars)} dead radars (TPS dropped below 10).")

            # ==========================================
            # PHASE 1: RADAR (Early Signals)
            # ==========================================
            radar_draft_subquery = db.query(XDraft.trend_id).filter(XDraft.draft_type == 'radar')

            radar_candidates = db.query(Trend).filter(
                Trend.is_active == True,
                Trend.final_tps >= radar_threshold,
                Trend.final_tps < confirm_threshold,
                ~Trend.id.in_(radar_draft_subquery)
            ).order_by(desc(Trend.final_tps)).limit(2).all()

            for trend in radar_candidates:
                try:
                    # Fix #5: skip trends without a summary — context too weak for good AI output
                    if not trend.summary:
                        logger.info(f"⏭️ Skipping trend {trend.id} — no summary yet")
                        continue

                    logger.info(f"📡 Processing Phase 1 (Radar) for Trend {trend.id}: {trend.title}")

                    ai_data = generate_x_content(trend.title, trend.summary, trend.category, phase_type='radar')
                    if not ai_data:
                        continue

                    tps_val = round(trend.final_tps, 1)
                    image_path = generate_x_image(trend.id, trend.title, ai_data['image_short_text'], tps_val)
                    if not image_path:
                        continue

                    # Fix #3: use shared helper
                    meta = _build_post_meta(trend, ai_data, tps_val)
                    conf_label = "⏳ İnceleniyor - İlk Sinyaller"

                    main_tweet = (
                        f"💬 Bu neden trend?\n\n"
                        f"{ai_data['ai_summary']}\n\n"
                        f"🛡️ Güven Endeksi: %{meta['confidence_pct']} ({conf_label})\n"
                        f"📈 Gündem Gücü: {meta['power_label']} (Normalden {meta['spread_speed']}x daha hızlı yayılıyor)\n\n"
                        f"💬 {meta['clean_question']}\n\n"
                        f"#{meta['hash1']} #{meta['hash2']} #TrendiaTR"
                    )
                    reply_tweet = f"Olayın detayları araştırılıyor... Gelişmeler için: 👇 🔗\n{meta['full_link']}"
                    # Fix #7: use named constant
                    caption = f"{main_tweet}{REPLY_SEPARATOR}{reply_tweet}"

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
                    time.sleep(5)

                except Exception as e:
                    logger.error(f"Error in Phase 1 (Radar) for trend {trend.id}: {e}")
                    continue

            # ==========================================
            # PHASE 2: CONFIRMED (High Score)
            # ==========================================
            confirmed_draft_subquery = db.query(XDraft.trend_id).filter(XDraft.draft_type == 'confirmed')

            confirmed_candidates = db.query(Trend).filter(
                Trend.is_active == True,
                Trend.final_tps >= confirm_threshold,
                ~Trend.id.in_(confirmed_draft_subquery)
            ).order_by(desc(Trend.final_tps)).limit(2).all()

            for trend in confirmed_candidates:
                try:
                    # Fix #5: skip trends without a summary
                    if not trend.summary:
                        logger.info(f"⏭️ Skipping trend {trend.id} — no summary yet")
                        continue

                    logger.info(f"🚨 Processing Phase 2 (Confirmed) for Trend {trend.id}: {trend.title}")

                    ai_data = generate_x_content(trend.title, trend.summary, trend.category, phase_type='confirmed')
                    if not ai_data:
                        continue

                    tps_val = round(trend.final_tps, 1)
                    image_path = generate_x_image(trend.id, trend.title, ai_data['image_short_text'], tps_val)
                    if not image_path:
                        continue

                    # Fix #3: use shared helper
                    meta = _build_post_meta(trend, ai_data, tps_val)
                    confidence_pct = meta['confidence_pct']
                    if confidence_pct >= 90:
                        conf_label = "Teyitli Kaynaklar"
                    elif confidence_pct >= 75:
                        conf_label = "Güvenilir Veri"
                    else:
                        conf_label = "Gelişmekte Olan Haber"

                    main_tweet = (
                        f"🚨 DOĞRULANDI (Sistem Güncellemesi):\n\n"
                        f"{ai_data['ai_summary']}\n\n"
                        f"🛡️ Güven Endeksi: %{confidence_pct} ({conf_label})\n"
                        f"📈 Gündem Gücü: {meta['power_label']} (Normalden {meta['spread_speed']}x daha hızlı yayılıyor)\n\n"
                        f"💬 {meta['clean_question']}\n\n"
                        f"#{meta['hash1']} #{meta['hash2']} #TrendiaTR"
                    )
                    reply_tweet = f"Olayın tüm detayları, resmi açıklamalar ve güncel gelişmeler için: 👇 🔗\n{meta['full_link']}"
                    # Fix #7: use named constant
                    caption = f"{main_tweet}{REPLY_SEPARATOR}{reply_tweet}"

                    # Fix #4: query the actual sent radar tweet_id for thread linking
                    sent_radar = db.query(XDraft).filter(
                        XDraft.trend_id == trend.id,
                        XDraft.draft_type == 'radar',
                        XDraft.status == 'sent',
                        XDraft.tweet_id.isnot(None)
                    ).order_by(XDraft.sent_at.desc()).first()

                    draft = XDraft(
                        trend_id=trend.id,
                        hook_text=ai_data['ai_summary'][:50],
                        long_caption=caption,
                        image_short_text=ai_data['image_short_text'],
                        tps_score=tps_val,
                        image_path=image_path,
                        status='draft',
                        draft_type='confirmed',
                        reply_to_tweet_id=sent_radar.tweet_id if sent_radar else None
                    )
                    db.add(draft)
                    trend.radar_phase_triggered = False
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
