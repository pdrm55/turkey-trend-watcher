from flask import Blueprint, jsonify, render_template, request, make_response, abort, Response, redirect, send_from_directory, current_app
import os
from app.database.models import SessionLocal, Trend, RawNews, TrendArrivals, SystemSettings, MarketAsset, MarketHistory, XDraft
from sqlalchemy import desc, func
from datetime import datetime, timedelta
from xml.sax.saxutils import escape
from app.config import Config
from bs4 import BeautifulSoup
import re
import redis
import json
import logging
import time
import requests
from functools import wraps

# --- اصلاح حیاتی: انتقال ایمپورت به سطح ماژول ---
# این کار باعث می‌شود مدل هوش مصنوعی فقط یک‌بار (زمان روشن شدن سرور) لود شود
# و نه هر بار که کاربر روی لینک کلیک می‌کند.
from app.core.ai_engine import ai_engine 
from app.core.x_ai_service import generate_x_content
from app.core.x_image_gen import generate_x_image
from app.core.tg_notifier import notify_admin_x_draft

# تنظیمات لاگر برای مانیتورینگ وضعیت کش
logger = logging.getLogger(__name__)

api_bp = Blueprint('api', __name__)

# اتصال به کلاینت Redis برای مدیریت لایه کش
try:
    redis_client = redis.from_url(Config.REDIS_URL, decode_responses=True)
    logger.info("✅ Redis Cache Layer Connected successfully.")
except Exception as e:
    redis_client = None
    logger.error(f"❌ Redis Connection Failed: {e}")

# دسته‌بندی‌های مجاز برای سئو
VALID_CATEGORIES = ["Siyaset", "Ekonomi", "Gündem", "Spor", "Teknoloji", "Sanat"]
JUNK_KEYWORDS = ['burç', 'fal ', 'günlük burç', 'astroloji', 'horoskop']

# In-memory cache for trend history (Simple Dictionary)
trend_history_cache = {}

def get_public_url():
    """محاسبه URL عمومی با در نظر گرفتن پروکسی Nginx برای سئو"""
    protocol = request.headers.get('X-Forwarded-Proto', 'https')
    host = request.headers.get('X-Forwarded-Host', request.host)
    return f"{protocol}://{host}".rstrip('/')

# --- Basic Auth Helper ---
def check_auth(username, password):
    """بررسی نام کاربری و رمز عبور برای پنل ادمین"""
    # در محیط واقعی باید از متغیرهای محیطی خوانده شود
    return username == 'admin' and password == 'trendia2026'

def authenticate():
    return Response('Login Required', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

def resolve_trend_smart(db, identifier):
    """
    جستجوی هوشمند ترند با پشتیبانی از فرمت ID-Slug برای جلوگیری از لینک‌های شکسته.
    اولویت: 1. ID استخراج شده از ابتدای رشته 2. شناسه عددی خالص 3. اسلاگ یا کلاستر ID
    """
    # 1. تلاش برای استخراج ID از فرمت "123-slug-name"
    match = re.match(r'^(\d+)-', identifier)
    if match:
        try:
            trend_id = int(match.group(1))
            trend = db.query(Trend).filter(Trend.id == trend_id).first()
            if trend: return trend
        except: pass

    # 2. جستجوی استاندارد (ID خالص، Slug یا Cluster ID)
    if identifier.isdigit():
        return db.query(Trend).filter(Trend.id == int(identifier)).first()
    
    return db.query(Trend).filter((Trend.slug == identifier) | (Trend.cluster_id == identifier)).first()

@api_bp.route('/robots.txt')
def robots_txt():
    """Serve robots.txt for SEO crawlers"""
    return send_from_directory(current_app.static_folder, 'robots.txt')

@api_bp.route('/')
def dashboard():
    """رندر کردن داشبورد اصلی (Home)"""
    return render_template(
        'index.html', 
        active_category="Hepsi",
        page_title="TrendiaTR | Yapay Zeka Haber Analizi",
        page_description="TrendiaTR ile gerçek zamanlı yapay zeka haber analizi ve Türkiye'deki son gelişmeler."
    )

@api_bp.route('/category/<name>')
def category_page(name):
    """رندر صفحات لندینگ دسته‌بندی‌ها برای ایندکس سئو"""
    cat_name = name.capitalize()
    if cat_name not in VALID_CATEGORIES:
        abort(404)
    
    seo_meta = {
        "Siyaset": {"title": "Siyaset Haberleri | TrendiaTR", "desc": "Türkiye ve dünya siyasetine dair en son gelişmeler."},
        "Ekonomi": {"title": "Ekonomi ve Borsa Haberleri | TrendiaTR", "desc": "Döviz kurları og اقتصاد دنیا."},
        "Gündem": {"title": "Gündemdeki Son Dakika Haberleri | TrendiaTR", "desc": "Türkiye gündemindeki en önemli olaylar."},
        "Spor": {"title": "Spor Dünyasından Gelişmeler | TrendiaTR", "desc": "Süper Lig ve transfer haberleri analizi."},
        "Teknoloji": {"title": "Teknoloji ve Bilim Haberleri | TrendiaTR", "desc": "Yapay zeka و تکنولوژی."},
        "Sanat": {"title": "Sanat ve Magazin Haberleri | TrendiaTR", "desc": "Popüler kültür ve sanat dünyası خبرلری."}
    }
    
    current_meta = seo_meta.get(cat_name, {"title": f"{cat_name} Haberleri", "desc": "TrendiaTR Haber Analizi"})
    
    return render_template(
        'index.html', 
        active_category=cat_name,
        page_title=current_meta["title"],
        page_description=current_meta["desc"]
    )

@api_bp.route('/trend/<identifier>')
def render_trend_page(identifier):
    """رندر سمت سرور (SSR) برای صفحات جزئیات ترند"""
    
    # 1. Check Redis Cache for SSR HTML
    cache_key = f"ssr_trend_{identifier}"
    if redis_client:
        cached_html = redis_client.get(cache_key)
        if cached_html:
            return cached_html

    db = SessionLocal()
    # ایمپورت ai_engine از اینجا حذف شد و به بالا منتقل گردید
    try:
        # جستجو بر اساس اسلاگ سئو یا شناسه کلاستر
        trend = resolve_trend_smart(db, identifier)
        
        if not trend:
            abort(404)
            
        # ریدایرکت کانونیکال برای سئو (اگر اسلاگ تغییر کرده باشد، به آدرس جدید هدایت می‌کند)
        if trend.slug:
            canonical_slug = f"{trend.id}-{trend.slug}"
            # اگر درخواست با ID شروع شده ولی اسلاگش قدیمی است، ریدایرکت کن
            if (re.match(r'^(\d+)-', identifier) or identifier.isdigit()) and identifier != canonical_slug:
                return redirect(f"/trend/{canonical_slug}", code=301)
            
        news_items = db.query(RawNews).filter(RawNews.trend_id == trend.id).order_by(desc(RawNews.published_at)).limit(20).all()
        
        formatted_news = []
        for n in news_items:
            # پاک‌سازی تگ‌های HTML با BeautifulSoup
            clean_content = n.content
            try:
                soup = BeautifulSoup(n.content, "html.parser")
                # حذف تگ‌های مزاحم اگر وجود داشت
                for script in soup(["script", "style"]):
                    script.extract()
                clean_content = soup.get_text()
                # حذف فضاهای خالی اضافه
                clean_content = " ".join(clean_content.split())
            except Exception as e:
                logger.error(f"Error cleaning HTML: {e}")
                clean_content = n.content

            link = n.external_id or ""
            if link and not link.startswith('http'):
                link = f"https://{link}"
            
            formatted_news.append({
                "source": n.source_name,
                "time": n.published_at,
                "content": clean_content,
                "link": link
            })
            
        # دریافت اخبار مرتبط (بدون لودینگ مجدد مدل)
        related_ids = ai_engine.get_related_trends(trend.cluster_id, limit=4)
        related_trends = db.query(Trend).filter(
            Trend.cluster_id.in_(related_ids), 
            Trend.is_active == True,
            Trend.id != trend.id
        ).all()
            
        base_url = get_public_url()
        canonical_url = f"{base_url}/trend/{trend.id}-{trend.slug}" if trend.slug else f"{base_url}/trend/{trend.cluster_id}"
        
        date_published = trend.first_seen.isoformat() + "+00:00" if trend.first_seen else None
        date_modified = trend.last_updated.isoformat() + "+00:00" if trend.last_updated else date_published
        
        html_content = render_template(
            'trend_detail.html', 
            trend=trend, 
            news_list=formatted_news,
            related_trends=related_trends,
            canonical_url=canonical_url,
            base_url=base_url,
            date_published=date_published,
            date_modified=date_modified
        )
        
        # 2. Save to Redis Cache (10 minutes TTL)
        if redis_client:
            redis_client.setex(cache_key, 600, html_content)
            
        return html_content
    finally:
        db.close()

@api_bp.route('/api/admin/x-drafts/generate', methods=['POST'])
@requires_auth
def generate_x_drafts():
    data = request.json or {}
    min_tps = float(data.get('min_tps', 50.0))
    num_drafts = int(data.get('num_drafts', 5))
    
    db = SessionLocal()
    try:
        # Subquery to find trends that already have drafts
        
        candidates = db.query(Trend).filter(
            Trend.is_active == True,
            Trend.final_tps >= min_tps,
            ~Trend.id.in_(db.query(XDraft.trend_id))
        ).order_by(desc(Trend.final_tps)).limit(num_drafts).all()
        
        generated_count = 0
        
        for trend in candidates:
            # 1. Generate Content
            # Ensure summary exists, fallback to title if needed
            context_text = trend.summary if trend.summary else trend.title
            ai_data = generate_x_content(trend.title, context_text, trend.category)
            
            if not ai_data:
                continue
                
            # 2. Generate Image
            tps_val = round(trend.final_tps, 1)
            image_path = generate_x_image(trend.id, trend.title, ai_data['image_short_text'], tps_val)
            
            if not image_path:
                continue
                
            # 3. Construct Caption
            public_url = get_public_url()
            slug_part = trend.slug if trend.slug else trend.id
            full_link = f"{public_url}/trend/{slug_part}"
            
            spread_speed = round(tps_val / 7.5, 1)
            hashtags = ai_data.get('hashtags', [])
            hash1 = hashtags[0] if len(hashtags) > 0 else trend.category
            hash2 = hashtags[1] if len(hashtags) > 1 else "Gündem"

            main_tweet = (
                f"🤖 AI Özeti: {ai_data['ai_summary']}\n\n"
                f"📊 TPS: {tps_val} | Yayılım Hızı: {spread_speed}x\n\n"
                f"💬 {ai_data['interaction_question']}\n\n"
                f"#{hash1} #{hash2} #TrendiaTR"
            )
            
            reply_tweet = f"Olayın tüm detayları, resmi açıklamalar ve güncel gelişmeler için: 👇 🔗\n{full_link}"
            
            caption = f"{main_tweet}\n\n====REPLY====\n\n{reply_tweet}"
            
            # 4. Save Draft
            draft = XDraft(
                trend_id=trend.id,
                hook_text=ai_data['ai_summary'][:50],
                long_caption=caption,
                image_short_text=ai_data['image_short_text'],
                tps_score=tps_val,
                image_path=image_path,
                status='draft'
            )
            db.add(draft)
            generated_count += 1
            
        db.commit()
        if generated_count > 0:
            notify_admin_x_draft(f"{generated_count} adet yeni draft oluşturuldu.", min_tps, "Batch Panel")
        return jsonify({"status": "success", "generated": generated_count})
    except Exception as e:
        db.rollback()
        logger.error(f"X Draft Generation Error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@api_bp.route('/api/admin/x-drafts', methods=['GET'])
@requires_auth
def list_x_drafts():
    db = SessionLocal()
    try:
        drafts = db.query(XDraft, Trend.title).join(Trend, XDraft.trend_id == Trend.id).filter(
            XDraft.status == 'draft'
        ).order_by(desc(XDraft.created_at)).all()
        
        results = []
        for d, title in drafts:
            results.append({
                "draft_id": d.id,
                "trend_id": d.trend_id,
                "trend_title": title,
                "caption": d.long_caption,
                "image_path": d.image_path,
                "tps_score": d.tps_score,
                "created_at": d.created_at.isoformat()
            })
            
        return jsonify(results)
    finally:
        db.close()

@api_bp.route('/api/admin/x-drafts/<int:draft_id>/action', methods=['POST'])
@requires_auth
def action_x_draft(draft_id):
    data = request.json or {}
    action = data.get('action')
    
    db = SessionLocal()
    try:
        draft = db.query(XDraft).filter(XDraft.id == draft_id).first()
        if not draft:
            return jsonify({"error": "Draft not found"}), 404
            
        if action == 'mark_sent':
            draft.status = 'sent'
            draft.sent_at = datetime.utcnow()
            
            # Delete the physical image file to save space
            if draft.image_path:
                try:
                    # app/api/routes.py -> app/api -> app/
                    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    file_path = os.path.join(base_dir, draft.image_path.lstrip('/'))
                    
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        logger.info(f"🗑️ Deleted X Draft image: {file_path}")
                    else:
                        logger.warning(f"⚠️ Image file not found for deletion: {file_path}")
                except Exception as e:
                    logger.error(f"❌ Failed to delete image file: {e}")

        elif action == 'discard':
            draft.status = 'discarded'
        else:
            return jsonify({"error": "Invalid action"}), 400
            
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@api_bp.route('/admin/x-studio')
@requires_auth
def x_studio_dashboard():
    db = SessionLocal()
    try:
        auto_pilot = db.query(SystemSettings).filter_by(key="x_auto_pilot_status").first()
        threshold = db.query(SystemSettings).filter_by(key="x_publish_threshold").first()
        
        return render_template(
            'x_studio.html',
            auto_pilot=auto_pilot.value if auto_pilot else "False",
            threshold=threshold.value if threshold else "70.0"
        )
    finally:
        db.close()

@api_bp.route('/api/admin/x-settings', methods=['POST'])
@requires_auth
def update_x_settings():
    data = request.json or {}
    auto_pilot = str(data.get('auto_pilot', False)) # "True" or "False"
    threshold = str(data.get('threshold', 70.0))
    
    db = SessionLocal()
    try:
        # Update Auto-Pilot Status
        ap_setting = db.query(SystemSettings).filter_by(key="x_auto_pilot_status").first()
        if not ap_setting:
            ap_setting = SystemSettings(key="x_auto_pilot_status", value=auto_pilot)
            db.add(ap_setting)
        else:
            ap_setting.value = auto_pilot
            
        # Update Threshold
        th_setting = db.query(SystemSettings).filter_by(key="x_publish_threshold").first()
        if not th_setting:
            th_setting = SystemSettings(key="x_publish_threshold", value=threshold)
            db.add(th_setting)
        else:
            th_setting.value = threshold
            
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@api_bp.route('/api/admin/x-drafts/generate-by-id', methods=['POST'])
@requires_auth
def generate_x_draft_by_id():
    data = request.json or {}
    trend_id = data.get('trend_id')
    
    if not trend_id:
        return jsonify({"error": "trend_id is required"}), 400
        
    db = SessionLocal()
    try:
        trend = db.query(Trend).filter(Trend.id == trend_id).first()
        if not trend:
            return jsonify({"error": "Trend not found"}), 404
            
        # Check if draft exists
        existing = db.query(XDraft).filter(XDraft.trend_id == trend.id).first()
        if existing:
            return jsonify({"error": "Draft already exists for this trend"}), 400
            
        # Generate Content
        context_text = trend.summary if trend.summary else trend.title
        ai_data = generate_x_content(trend.title, context_text, trend.category)
        
        if not ai_data:
            return jsonify({"error": "AI content generation failed"}), 500
            
        # Generate Image
        tps_val = round(trend.final_tps, 1)
        image_path = generate_x_image(trend.id, trend.title, ai_data['image_short_text'], tps_val)
        
        if not image_path:
            return jsonify({"error": "Image generation failed"}), 500
            
        # Construct Caption
        public_url = get_public_url()
        slug_part = trend.slug if trend.slug else trend.id
        full_link = f"{public_url}/trend/{slug_part}"
        
        spread_speed = round(tps_val / 7.5, 1)
        hashtags = ai_data.get('hashtags', [])
        hash1 = hashtags[0] if len(hashtags) > 0 else trend.category
        hash2 = hashtags[1] if len(hashtags) > 1 else "Gündem"

        main_tweet = (
            f"🤖 AI Özeti: {ai_data['ai_summary']}\n\n"
            f"📊 TPS: {tps_val} | Yayılım Hızı: {spread_speed}x\n\n"
            f"💬 {ai_data['interaction_question']}\n\n"
            f"#{hash1} #{hash2} #TrendiaTR"
        )
        
        reply_tweet = f"Olayın tüm detayları, resmi açıklamalar ve güncel gelişmeler için: 👇 🔗\n{full_link}"
        
        caption = f"{main_tweet}\n\n====REPLY====\n\n{reply_tweet}"
        
        # Save Draft
        draft = XDraft(
            trend_id=trend.id,
            hook_text=ai_data['ai_summary'][:50],
            long_caption=caption, # Storing full caption in long_caption column
            image_short_text=ai_data['image_short_text'],
            tps_score=tps_val,
            image_path=image_path,
            status='draft'
        )
        db.add(draft)
        db.commit()
        
        notify_admin_x_draft(trend.title, tps_val, "Manual ID")
        
        return jsonify({"status": "success", "draft_id": draft.id})
    except Exception as e:
        db.rollback()
        logger.error(f"Manual X Draft Generation Error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@api_bp.route('/api/trends/<int:trend_id>/history')
@api_bp.route('/api/trends/<identifier>/history')
def get_trend_history(identifier=None, trend_id=None):
    """API endpoint for trend history chart data (TPS/Signal Growth)"""
    
    # Database Indexing Suggestion:
    # Run this SQL to optimize performance:
    # CREATE INDEX idx_trend_arrivals_trend_ts ON trend_arrivals (trend_id, timestamp);

    target_id = str(trend_id) if trend_id is not None else identifier

    # 1. Check In-Memory Cache (60s TTL)
    current_time = time.time()
    if target_id in trend_history_cache:
        cached_data, timestamp = trend_history_cache[target_id]
        if current_time - timestamp < 60:
            return jsonify(cached_data)

    db = SessionLocal()
    try:
        # Resolve trend by slug, cluster_id, or ID
        trend = resolve_trend_smart(db, target_id)
        if not trend:
            abort(404)

        # 2. Time-Series Aggregation (5-minute buckets)
        cutoff_time = datetime.utcnow() - timedelta(hours=48)
        
        # Bucket by 5 minutes (300 seconds) using epoch math (Postgres compatible)
        time_bucket = func.to_timestamp(func.floor(func.extract('epoch', TrendArrivals.timestamp) / 300) * 300)
        
        results = db.query(
            time_bucket.label('bucket'),
            func.count(TrendArrivals.id).label('count')
        ).filter(
            TrendArrivals.trend_id == trend.id,
            TrendArrivals.timestamp >= cutoff_time
        ).group_by(
            'bucket'
        ).order_by(
            'bucket'
        ).all()
        
        labels = []
        data = []
        cumulative_signal = 0
        
        for bucket, count in results:
            if not bucket: continue
            cumulative_signal += count
            labels.append(bucket.strftime('%H:%M'))
            data.append(cumulative_signal)
            
        response_data = {"labels": labels, "data": data}
        
        # 3. Update Cache
        trend_history_cache[target_id] = (response_data, current_time)
            
        return jsonify(response_data)
    finally:
        db.close()

@api_bp.route('/api/trends')
def get_trends():
    """API لیست ترندها با قابلیت کشینگ هوشمند (فاز ۶)"""
    category = request.args.get('category', 'All')
    list_type = request.args.get('type', 'timeline')
    offset = int(request.args.get('offset', 0))
    limit = int(request.args.get('limit', 32))
    
    # Search & Filter Params
    q = request.args.get('q', '').strip()
    date_str = request.args.get('date', '')

    # --- منطق کشینگ Redis ---
    cache_key = f"trends_v1_{category}_{list_type}_{offset}_{limit}_{q}_{date_str}"
    if redis_client:
        cached_data = redis_client.get(cache_key)
        if cached_data:
            return make_response(cached_data, 200, {"Content-Type": "application/json"})

    db = SessionLocal()
    try:
        query = db.query(Trend).filter(Trend.is_active == True)
        
        if category != 'All':
            query = query.filter(Trend.category == category)

        # --- Advanced Search Logic ---
        if q:
            # PostgreSQL Full Text Search (Turkish)
            query = query.filter(
                func.to_tsvector('turkish', Trend.title).op('@@')(func.plainto_tsquery('turkish', q))
            )
        
        # --- Date Filter ---
        if date_str:
            try:
                filter_date = datetime.strptime(date_str, '%Y-%m-%d')
                query = query.filter(Trend.last_updated >= filter_date)
            except ValueError:
                pass # Ignore invalid date format

        if list_type == 'hot':
            # ترندهای داغ بر اساس امتیاز TPS در ۲۴ ساعت اخیر
            time_threshold = datetime.now() - timedelta(hours=24)
            query = query.filter(Trend.last_updated >= time_threshold)
            for word in JUNK_KEYWORDS:
                query = query.filter(~Trend.title.ilike(f'%{word}%'))
            trends = query.order_by(desc(Trend.final_tps), desc(Trend.last_updated)).limit(8).all()
        else:
            trends = query.order_by(desc(Trend.first_seen)).offset(offset).limit(limit).all()
            
        results = []
        for t in trends:
            last_news = db.query(RawNews).filter(RawNews.trend_id == t.id).order_by(desc(RawNews.published_at)).first()
            results.append({
                "id": t.cluster_id,
                "trend_id": t.id, # شناسه عددی برای ساخت لینک‌های پایدار
                "slug": t.slug,
                "title": t.title or "Analiz Bekleniyor...",
                "summary": t.summary or "Haber detayları işleniyor...",
                "score": round(t.final_tps or t.score, 1),
                "count": t.message_count or 1,
                "category": t.category,
                "first_seen": t.first_seen.isoformat() + 'Z' if t.first_seen else None,
                "last_update": t.last_updated.isoformat() + 'Z' if t.last_updated else None, 
                "source_sample": last_news.source_name if last_news else "Bilinmiyor",
                "image": t.cover_image
            })
        
        response_json = json.dumps(results)
        # ذخیره در کش برای ۱۲۰ ثانیه (برای حفظ تازگی اخبار صفحه اصلی)
        if redis_client:
            redis_client.setex(cache_key, 120, response_json)
            
        return jsonify(results)
    finally:
        db.close()

@api_bp.route('/api/trends/<identifier>')
def get_trend_details(identifier):
    """API جزئیات ترند برای مودال با کشینگ طولانی‌تر (فاز ۶)"""
    
    # کلید اختصاصی برای هر کلاستر
    cache_key = f"detail_v1_{identifier}"
    if redis_client:
        cached_data = redis_client.get(cache_key)
        if cached_data:
            return make_response(cached_data, 200, {"Content-Type": "application/json"})

    db = SessionLocal()
    # ایمپورت ai_engine از اینجا حذف شد
    try:
        trend = resolve_trend_smart(db, identifier)
        if not trend: return jsonify({"error": "Trend not found"}), 404
        
        # واکشی اخبار مربوطه
        news_items = db.query(RawNews).filter(RawNews.trend_id == trend.id).order_by(desc(RawNews.published_at)).limit(20).all()
        
        # جستجوی برداری (بخش سنگین)
        related_ids = ai_engine.get_related_trends(trend.cluster_id, limit=4)
        related_data = db.query(Trend).filter(
            Trend.cluster_id.in_(related_ids), 
            Trend.is_active == True, 
            Trend.id != trend.id
        ).all()

        formatted_news = []
        for n in news_items:
            link = n.external_id or ""
            if link and not link.startswith('http'):
                link = f"https://{link}"
            formatted_news.append({
                "source": n.source_name, 
                "time": n.published_at.isoformat() + 'Z', 
                "content": n.content, 
                "link": link
            })

        result = {
            "title": trend.title,
            "category": trend.category,
            "tps_score": round(trend.final_tps, 1),
            "summary": trend.summary or "Generating summary...",
            "image": trend.cover_image,
            "tags": trend.tags or [],
            "entities": trend.entities or {},
            "news_list": formatted_news,
            "related_trends": [{
                "title": r.title,
                "category": r.category,
                "slug": r.slug or r.cluster_id,
                "date": r.last_updated.strftime('%d.%m.%Y') if r.last_updated else ""
            } for r in related_data]
        }

        response_json = json.dumps(result)
        # ذخیره در کش برای ۶۰۰ ثانیه (۱۰ دقیقه) چون جزئیات خبر کمتر تغییر می‌کند
        if redis_client:
            redis_client.setex(cache_key, 600, response_json)

        return jsonify(result)
    finally:
        db.close()

@api_bp.route('/sitemap.xml')
def sitemap():
    """تولید داینامیک نقشه سایت (XML Sitemap)"""
    db = SessionLocal()
    try:
        base_url = get_public_url()
        trends = db.query(Trend).filter(Trend.is_active == True).order_by(desc(Trend.last_updated)).limit(3000).all()
        
        xml_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        ]
        xml_lines.append(f'  <url><loc>{base_url}/</loc><changefreq>always</changefreq><priority>1.0</priority></url>')
        
        for cat in VALID_CATEGORIES:
            xml_lines.append(f'  <url><loc>{base_url}/category/{cat.lower()}</loc><changefreq>daily</changefreq><priority>0.9</priority></url>')

        for trend in trends:
            if not trend.last_updated: continue
            identifier = f"{trend.id}-{trend.slug}" if trend.slug else trend.cluster_id
            if not identifier: continue
            
            lastmod = trend.last_updated.strftime('%Y-%m-%d')
            loc = f"{base_url}/trend/{identifier}"
            xml_lines.append(f'  <url><loc>{escape(loc)}</loc><lastmod>{lastmod}</lastmod><changefreq>daily</changefreq><priority>0.8</priority></url>')
        
        xml_lines.append('</urlset>')
        response = make_response('\n'.join(xml_lines))
        response.headers['Content-Type'] = 'application/xml; charset=utf-8'
        return response
    finally:
        db.close()

@api_bp.route('/api/stats')
def get_stats():
    """آمار کلی سیستم برای نمایش در هدر"""
    db = SessionLocal()
    try:
        return jsonify({
            "total_news": db.query(RawNews).count(),
            "total_trends": db.query(Trend).filter(Trend.is_active == True).count()
        })
    finally:
        db.close()

@api_bp.route('/api/market/live')
def get_live_market_data():
    """API endpoint for live market ticker data"""
    # 1. Try Redis
    if redis_client:
        try:
            cached_data = redis_client.get("market_ticker")
            if cached_data:
                response = make_response(cached_data)
                response.headers['Content-Type'] = 'application/json'
                response.headers['Cache-Control'] = 'public, max-age=30'
                return response
        except Exception as e:
            logger.error(f"Redis error in market endpoint: {e}")

    # 2. Fallback to DB
    db = SessionLocal()
    try:
        assets = db.query(MarketAsset).filter(MarketAsset.is_active == True).all()
        data = {}
        
        for asset in assets:
            latest = db.query(MarketHistory)\
                .filter(MarketHistory.asset_id == asset.id)\
                .order_by(desc(MarketHistory.timestamp))\
                .first()
            
            if latest:
                data[asset.symbol] = {"price": latest.price, "change": latest.change_rate}
        
        response = jsonify(data)
        response.headers['Cache-Control'] = 'public, max-age=30'
        return response
    except Exception as e:
        logger.error(f"DB error in market endpoint: {e}")
        return jsonify({})
    finally:
        db.close()

@api_bp.route('/api/contact', methods=['POST'])
def submit_contact_form():
    """Handle contact form submissions via Telegram"""
    data = request.json or {}
    name = data.get('name')
    email = data.get('email')
    message = data.get('message')
    
    if not all([name, email, message]):
        return jsonify({'error': 'Lütfen tüm alanları doldurun.'}), 400
        
    try:
        text = f"📩 <b>Yeni İletişim Mesajı (TrendiaTR)</b>\n\n👤 <b>İsim:</b> {name}\n📧 <b>E-posta:</b> {email}\n\n📝 <b>Mesaj:</b>\n{message}"
        
        telegram_url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': Config.ADMIN_CHAT_ID,
            'text': text,
            'parse_mode': 'HTML'
        }
        
        response = requests.post(telegram_url, json=payload, timeout=10)
        
        if response.status_code == 200:
            return jsonify({'status': 'success', 'message': 'Mesajınız başarıyla gönderildi.'})
        else:
            logger.error(f"Telegram API Error: {response.text}")
            return jsonify({'error': 'Mesaj gönderilemedi.'}), 500
            
    except Exception as e:
        logger.error(f"Contact Form Error: {e}")
        return jsonify({'error': 'Internal Server Error'}), 500

# --- Admin Panel Routes ---

@api_bp.route('/admin')
@requires_auth
def admin_panel():
    """رندر کردن پنل مدیریت"""
    db = SessionLocal()
    try:
        # دریافت تنظیمات فعلی
        threshold_setting = db.query(SystemSettings).filter_by(key="auto_publish_threshold").first()
        current_threshold = threshold_setting.value if threshold_setting else "35.0"
        
        # بررسی وضعیت ورکرها (بر اساس آخرین فعالیت ترندها)
        last_trend_update = db.query(func.max(Trend.last_updated)).scalar()
        worker_status = "Active" if last_trend_update and (datetime.utcnow() - last_trend_update).total_seconds() < 600 else "Idle/Offline"
        
        return render_template('admin.html', current_threshold=current_threshold, worker_status=worker_status)
    finally:
        db.close()

@api_bp.route('/api/admin/settings', methods=['POST'])
@requires_auth
def update_settings():
    """بروزرسانی تنظیمات سیستم"""
    data = request.json
    new_threshold = data.get('threshold')
    
    db = SessionLocal()
    try:
        setting = db.query(SystemSettings).filter_by(key="auto_publish_threshold").first()
        if not setting:
            setting = SystemSettings(key="auto_publish_threshold", value=str(new_threshold))
            db.add(setting)
        else:
            setting.value = str(new_threshold)
        db.commit()
        return jsonify({"status": "success", "new_value": setting.value})
    finally:
        db.close()

@api_bp.route('/api/admin/trends')
@requires_auth
def admin_get_trends():
    """لیست تمام ترندها برای مدیریت با فیلتر و صفحه‌بندی"""
    offset = int(request.args.get('offset', 0))
    limit = int(request.args.get('limit', 50))
    q = request.args.get('q', '').strip()
    category = request.args.get('category', 'All')
    date_str = request.args.get('date', '')

    db = SessionLocal()
    try:
        query = db.query(Trend)

        if category != 'All':
            query = query.filter(Trend.category == category)
        
        if q:
            query = query.filter(Trend.title.ilike(f'%{q}%'))
            
        if date_str:
            try:
                filter_date = datetime.strptime(date_str, '%Y-%m-%d')
                query = query.filter(Trend.last_updated >= filter_date, Trend.last_updated < filter_date + timedelta(days=1))
            except ValueError:
                pass

        trends = query.order_by(desc(Trend.last_updated)).offset(offset).limit(limit).all()
        results = []
        for t in trends:
            results.append({
                "id": t.id,
                "title": t.title or "No Title",
                "summary": t.summary or "",
                "tps": round(t.final_tps, 1),
                "is_active": t.is_active,
                "category": t.category,
                "last_updated": t.last_updated.strftime('%Y-%m-%d %H:%M') if t.last_updated else "-"
            })
        return jsonify(results)
    finally:
        db.close()

@api_bp.route('/api/admin/trends/<int:trend_id>/action', methods=['POST'])
@requires_auth
def admin_trend_action(trend_id):
    """انجام عملیات روی ترندها (حذف/انتشار)"""
    action = request.json.get('action')
    db = SessionLocal()
    try:
        trend = db.query(Trend).filter(Trend.id == trend_id).first()
        if not trend: return jsonify({"error": "Trend not found"}), 404
        
        if action == 'toggle_active':
            trend.is_active = not trend.is_active
        elif action == 'force_publish':
            # فقط فلگ را ست می‌کنیم، ورکر سامرایزر یا آلرت سرویس باید هندل کند
            # اما اینجا برای سادگی فرض می‌کنیم انتشار دستی از طریق بات انجام می‌شود
            pass # نیاز به ایمپورت alert_service در routes دارد که فعلا انجام نمی‌دهیم تا پیچیده نشود
            
        db.commit()
        return jsonify({"status": "success", "is_active": trend.is_active})
    finally:
        db.close()

@api_bp.route('/api/admin/trends/<int:trend_id>/update', methods=['POST'])
@requires_auth
def admin_update_trend(trend_id):
    """Update trend title and category manually"""
    data = request.json
    new_title = data.get('title')
    new_category = data.get('category')
    new_summary = data.get('summary')
    
    db = SessionLocal()
    try:
        trend = db.query(Trend).filter(Trend.id == trend_id).first()
        if not trend:
            return jsonify({"error": "Trend not found"}), 404
            
        if new_title:
            trend.title = new_title
        if new_category:
            trend.category = new_category
        if new_summary is not None:
            trend.summary = new_summary.strip()
            
        trend.last_updated = datetime.utcnow()
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()