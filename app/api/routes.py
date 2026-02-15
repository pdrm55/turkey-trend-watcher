from flask import Blueprint, jsonify, render_template, request, make_response, abort, Response
from app.database.models import SessionLocal, Trend, RawNews, TrendArrivals, SystemSettings
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
from functools import wraps

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
    db = SessionLocal()
    from app.core.ai_engine import ai_engine 
    try:
        # جستجو بر اساس اسلاگ سئو یا شناسه کلاستر
        trend = db.query(Trend).filter((Trend.slug == identifier) | (Trend.cluster_id == identifier)).first()
        
        if not trend:
            abort(404)
            
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
            
        # دریافت اخبار مرتبط (ممکن است زمان‌بر باشد، در پایتون هندل می‌شود)
        related_ids = ai_engine.get_related_trends(trend.cluster_id, limit=4)
        related_trends = db.query(Trend).filter(
            Trend.cluster_id.in_(related_ids), 
            Trend.is_active == True,
            Trend.id != trend.id
        ).all()
            
        base_url = get_public_url()
        canonical_url = f"{base_url}/trend/{trend.slug if trend.slug else trend.cluster_id}"
        
        date_published = trend.first_seen.isoformat() + "+00:00" if trend.first_seen else None
        date_modified = trend.last_updated.isoformat() + "+00:00" if trend.last_updated else date_published
        
        return render_template(
            'trend_detail.html', 
            trend=trend, 
            news_list=formatted_news,
            related_trends=related_trends,
            canonical_url=canonical_url,
            base_url=base_url,
            date_published=date_published,
            date_modified=date_modified
        )
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
        trend = db.query(Trend).filter((Trend.slug == target_id) | (Trend.cluster_id == target_id)).first()
        if not trend and target_id.isdigit():
            trend = db.query(Trend).filter(Trend.id == int(target_id)).first()
            
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

    # --- منطق کشینگ Redis ---
    cache_key = f"trends_v1_{category}_{list_type}_{offset}_{limit}"
    if redis_client:
        cached_data = redis_client.get(cache_key)
        if cached_data:
            return make_response(cached_data, 200, {"Content-Type": "application/json"})

    db = SessionLocal()
    try:
        query = db.query(Trend).filter(Trend.is_active == True)
        
        if category != 'All':
            query = query.filter(Trend.category == category)

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
                "slug": t.slug,
                "title": t.title or "Analiz Bekleniyor...",
                "summary": t.summary or "Haber detayları işleniyor...",
                "score": round(t.final_tps or t.score, 1),
                "count": t.message_count or 1,
                "category": t.category,
                "first_seen": t.first_seen.isoformat() + 'Z' if t.first_seen else None,
                "last_update": t.last_updated.isoformat() + 'Z' if t.last_updated else None, 
                "source_sample": last_news.source_name if last_news else "Bilinmiyor"
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
    from app.core.ai_engine import ai_engine
    try:
        trend = db.query(Trend).filter((Trend.slug == identifier) | (Trend.cluster_id == identifier)).first()
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
            identifier = trend.slug if trend.slug else trend.cluster_id
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
    """لیست تمام ترندها برای مدیریت"""
    db = SessionLocal()
    try:
        trends = db.query(Trend).order_by(desc(Trend.last_updated)).limit(100).all()
        results = []
        for t in trends:
            results.append({
                "id": t.id,
                "title": t.title or "No Title",
                "tps": round(t.final_tps, 1),
                "is_active": t.is_active,
                "category": t.category,
                "last_updated": t.last_updated.strftime('%H:%M') if t.last_updated else "-"
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