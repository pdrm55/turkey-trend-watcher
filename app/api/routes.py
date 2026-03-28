from flask import Blueprint, jsonify, render_template, request, make_response, abort, Response, redirect, send_from_directory, current_app
import os
from app.database.models import SessionLocal, Trend, RawNews, TrendArrivals, SystemSettings, MarketAsset, MarketHistory, XDraft, Comment, CommentVote
from sqlalchemy import desc, func, or_, and_
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
import uuid
from PIL import Image
from werkzeug.utils import secure_filename

# --- بازگرداندن ایمپورت‌ها به سطح ماژول برای بهبود سرعت پاسخگویی ---
# استفاده از Lazy Loading باعث کندی شدید در اولین درخواست می‌شد.
# لینوکس با مکانیزم Copy-on-Write حافظه را بین ورکرها به اشتراک می‌گذارد، پس مصرف رم بهینه می‌ماند.
from app.core.ai_engine import ai_engine 
from app.core.x_ai_service import generate_x_content, generate_x_thread
from app.core.x_image_gen import generate_x_image
from app.core.tg_notifier import notify_admin_x_draft
from app.workers.summarizer import generate_summary_with_gemini
from app.core.alert_service import alert_service

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
    try:
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

            if n.source_type == 'editorial':
                link = get_public_url()
            else:
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
        
        comments_count = db.query(Comment).filter(Comment.trend_id == trend.id, Comment.status == 'approved').count()
            
        base_url = get_public_url()
        canonical_url = f"{base_url}/trend/{trend.id}-{trend.slug}" if trend.slug else f"{base_url}/trend/{trend.cluster_id}"
        
        date_published = trend.first_seen.isoformat() + "+00:00" if trend.first_seen else None
        date_modified = trend.last_updated.isoformat() + "+00:00" if trend.last_updated else date_published
        
        # --- GEO: Generate Dynamic FAQ Schema ---
        faq_items = [
            {
                "@type": "Question",
                "name": f"{trend.title} olayının arka planı ve özeti nedir?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": trend.summary[:400] + "..." if trend.summary else "Detaylar analiz ediliyor."
                }
            }
        ]
        
        if trend.summary and "🤖 Yapay Zeka Analizi" in trend.summary:
            analysis_text = trend.summary.split("🤖 Yapay Zeka Analizi")[-1].replace("#", "").strip()
            faq_items.append({
                "@type": "Question",
                "name": "Yapay zeka bu haberi ve olası etkilerini nasıl yorumluyor?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": analysis_text
                }
            })
            
        faq_schema = json.dumps({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": faq_items
        }, ensure_ascii=False)
        # --------------------------------------

        html_content = render_template(
            'trend_detail.html', 
            trend=trend, 
            news_list=formatted_news,
            related_trends=related_trends,
            canonical_url=canonical_url,
            base_url=base_url,
            date_published=date_published,
            date_modified=date_modified,
            comments_count=comments_count,
            faq_schema=faq_schema  # Injected Schema
        )
        
        # 2. Save to Redis Cache (10 minutes TTL)
        if redis_client:
            redis_client.setex(cache_key, 600, html_content)
            
        return html_content
    finally:
        db.close()

# --- Comment System Routes ---

@api_bp.route('/api/comments/<identifier>', methods=['GET'])
def get_comments(identifier):
    """Get comments for a specific trend"""
    sort_by = request.args.get('sort', 'popular')
    session_id = request.args.get('session_id')
    
    db = SessionLocal()
    try:
        trend = resolve_trend_smart(db, identifier)
        if not trend: return jsonify({"error": "Trend not found"}), 404
        trend_id = trend.id

        query = db.query(Comment).filter(
            Comment.trend_id == trend_id,
            Comment.status.in_(['approved', 'pending']) # Show pending to everyone? Usually only approved. Let's stick to approved + own pending
        )
        
        # Logic: Show approved comments OR comments belonging to this session (even if pending/rejected)
        if session_id:
            query = db.query(Comment).filter(
                Comment.trend_id == trend_id,
                or_(
                    Comment.status == 'approved',
                    and_(Comment.session_id == session_id, Comment.status != 'deleted')
                )
            )
        else:
            query = query.filter(Comment.status == 'approved')
            
        if sort_by == 'newest':
            comments = query.order_by(desc(Comment.created_at)).limit(50).all()
        else: # popular
            comments = query.order_by(desc(Comment.likes - Comment.dislikes), desc(Comment.created_at)).limit(50).all()
            
        results = []
        for c in comments:
            # Check user vote
            user_vote = 0
            if session_id:
                vote = db.query(CommentVote).filter(
                    CommentVote.comment_id == c.id,
                    CommentVote.session_id == session_id
                ).first()
                if vote: user_vote = vote.vote_type
                
            results.append({
                "id": c.id,
                "user_name": c.user_name,
                "content": c.content,
                "likes": c.likes,
                "dislikes": c.dislikes,
                "created_at": c.created_at.isoformat(),
                "status": c.status,
                "user_vote": user_vote
            })
            
        return jsonify(results)
    finally:
        db.close()

@api_bp.route('/api/comments/<identifier>', methods=['POST'])
def post_comment(identifier):
    """Post a new comment"""
    data = request.json or {}
    user_name = data.get('user_name')
    content = data.get('content')
    session_id = data.get('session_id')
    
    if not all([user_name, content, session_id]):
        return jsonify({"error": "Missing fields"}), 400
        
    db = SessionLocal()
    try:
        trend = resolve_trend_smart(db, identifier)
        if not trend: return jsonify({"error": "Trend not found"}), 404
        trend_id = trend.id

        # AI Moderation
        moderation_status = ai_engine.moderate_comment(content)
        
        comment = Comment(
            trend_id=trend_id,
            user_name=user_name,
            session_id=session_id,
            content=content,
            status=moderation_status
        )
        db.add(comment)
        db.commit()
        
        return jsonify({"status": "success", "moderation_status": moderation_status, "id": comment.id})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@api_bp.route('/api/comments/vote/<int:comment_id>', methods=['POST'])
def vote_comment(comment_id):
    """Vote on a comment (like/dislike)"""
    data = request.json or {}
    session_id = data.get('session_id')
    vote_type = int(data.get('vote_type', 0)) # 1 or -1
    
    if not session_id or vote_type not in [1, -1]:
        return jsonify({"error": "Invalid data"}), 400
        
    db = SessionLocal()
    try:
        comment = db.query(Comment).filter(Comment.id == comment_id).first()
        if not comment: return jsonify({"error": "Comment not found"}), 404
        
        existing_vote = db.query(CommentVote).filter(
            CommentVote.comment_id == comment_id,
            CommentVote.session_id == session_id
        ).first()
        
        if existing_vote:
            # If clicking same vote, remove it (toggle off)
            if existing_vote.vote_type == vote_type:
                if vote_type == 1: comment.likes -= 1
                else: comment.dislikes -= 1
                db.delete(existing_vote)
            else:
                # Change vote
                if vote_type == 1:
                    comment.likes += 1
                    comment.dislikes -= 1
                else:
                    comment.likes -= 1
                    comment.dislikes += 1
                existing_vote.vote_type = vote_type
        else:
            # New vote
            new_vote = CommentVote(comment_id=comment_id, session_id=session_id, vote_type=vote_type)
            db.add(new_vote)
            if vote_type == 1: comment.likes += 1
            else: comment.dislikes += 1
            
        db.commit()
        return jsonify({"status": "success", "likes": comment.likes, "dislikes": comment.dislikes})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@api_bp.route('/api/admin/comments', methods=['GET'])
@requires_auth
def admin_list_comments():
    """Admin: List comments for moderation"""
    status = request.args.get('status', 'all')
    
    db = SessionLocal()
    try:
        query = db.query(Comment, Trend.title).join(Trend, Comment.trend_id == Trend.id)
        
        if status != 'all':
            query = query.filter(Comment.status == status)
            
        comments = query.order_by(desc(Comment.created_at)).limit(100).all()
        
        results = []
        for c, trend_title in comments:
            results.append({
                "id": c.id,
                "trend_title": trend_title,
                "user_name": c.user_name,
                "session_id": c.session_id,
                "content": c.content,
                "status": c.status,
                "created_at": c.created_at.isoformat()
            })
        return jsonify(results)
    finally:
        db.close()

@api_bp.route('/api/admin/comments/<int:comment_id>/status', methods=['POST'])
@requires_auth
def admin_update_comment_status(comment_id):
    """Admin: Approve/Reject/ShadowBan comment"""
    data = request.json or {}
    new_status = data.get('status')
    
    if new_status not in ['approved', 'rejected', 'shadow_banned', 'pending']:
        return jsonify({"error": "Invalid status"}), 400
        
    db = SessionLocal()
    try:
        comment = db.query(Comment).filter(Comment.id == comment_id).first()
        if not comment: return jsonify({"error": "Comment not found"}), 404
        
        comment.status = new_status
        db.commit()
        return jsonify({"status": "success"})
    finally:
        db.close()

@api_bp.route('/api/admin/comments/<int:comment_id>', methods=['DELETE'])
@requires_auth
def admin_delete_comment(comment_id):
    """Admin: Delete a comment"""
    db = SessionLocal()
    try:
        comment = db.query(Comment).filter(Comment.id == comment_id).first()
        if not comment: return jsonify({"error": "Comment not found"}), 404
        
        db.delete(comment)
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@api_bp.route('/admin/editorial')
@requires_auth
def editorial_panel():
    """Render the dedicated Editorial News creation page"""
    return render_template('editorial.html')

@api_bp.route('/api/admin/news/draft', methods=['POST'])
@requires_auth
def generate_manual_news_draft():
    """Send raw text to Gemini to generate headline, summary, and category"""
    data = request.json or {}
    content = data.get('content')
    
    if not content or len(content) < 50:
        return jsonify({"error": "Text is too short (Requires at least 50 characters)"}), 400
        
    try:
        ai_data, in_tok, out_tok, duration = generate_summary_with_gemini(content)
        
        if not ai_data:
            return jsonify({"error": "AI did not respond properly (AI Error)"}), 500
            
        return jsonify({
            "status": "success",
            "title": ai_data.get("headline", ""),
                "summary": ai_data.get("summary", ""),
                "category": ai_data.get("category", "Gündem"),
                "telegram_caption": ai_data.get("telegram_caption", ""),
                "entities": ai_data.get("entities", {})
        })
    except Exception as e:
        logger.error(f"Manual Draft Error: {e}")
        return jsonify({"error": str(e)}), 500

@api_bp.route('/api/admin/news/publish', methods=['POST'])
@requires_auth
def publish_manual_news():
    """Save editorial news, handle image processing (WebP/800px), inject into ChromaDB, and apply high TPS"""
    content = request.form.get('content')
    title = request.form.get('title')
    summary = request.form.get('summary')
    category = request.form.get('category')
    telegram_caption = request.form.get('telegram_caption')
    entities_str = request.form.get('entities')
    
    # Advanced Image Processing (Matches image_processor.py standards)
    image_url = None
    if 'image' in request.files:
        file = request.files['image']
        if file and file.filename:
            try:
                img = Image.open(file)
                
                # 1. Convert to RGB safely
                if img.mode != 'RGB':
                    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                        background = Image.new('RGB', img.size, (255, 255, 255))
                        background.paste(img, mask=img.convert('RGBA').split()[3])
                        img = background
                    else:
                        img = img.convert('RGB')
                
                # 2. Resize to TARGET_WIDTH = 800
                TARGET_WIDTH = 800
                w, h = img.size
                aspect_ratio = h / w
                new_h = int(TARGET_WIDTH * aspect_ratio)
                img = img.resize((TARGET_WIDTH, new_h), Image.Resampling.LANCZOS)
                
                # 3. Setup YYYY/MM/DD folder structure
                now = datetime.utcnow()
                year, month, day = now.strftime("%Y"), now.strftime("%m"), now.strftime("%d")
                
                # Navigate to app/static/media
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                folder_path = os.path.join(base_dir, 'static', 'media', year, month, day)
                os.makedirs(folder_path, exist_ok=True)
                
                # 4. Save as WebP
                filename = f"{uuid.uuid4().hex}.webp"
                full_path = os.path.join(folder_path, filename)
                img.save(full_path, format="WEBP", quality=80)
                
                image_url = f"media/{year}/{month}/{day}/{filename}"
            except Exception as e:
                logger.error(f"Image processing error in Editorial: {e}")
                return jsonify({"error": "Image processing failed"}), 500
            
    # Advanced Video Processing (Phase 1)
    video_url = None
    if 'video' in request.files:
        video_file = request.files['video']
        if video_file and video_file.filename:
            try:
                # Setup YYYY/MM/DD folder structure under media/videos
                now = datetime.utcnow()
                year, month, day = now.strftime("%Y"), now.strftime("%m"), now.strftime("%d")
                
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                folder_path = os.path.join(base_dir, 'static', 'media', 'videos', year, month, day)
                os.makedirs(folder_path, exist_ok=True)
                
                # Generate unique filename retaining original extension or fallback to .mp4
                ext = os.path.splitext(secure_filename(video_file.filename))[1]
                if not ext: ext = ".mp4"
                filename = f"vid_{uuid.uuid4().hex}{ext}"
                full_path = os.path.join(folder_path, filename)
                
                video_file.save(full_path)
                video_url = f"media/videos/{year}/{month}/{day}/{filename}"
            except Exception as e:
                logger.error(f"Video processing error in Editorial: {e}")
                return jsonify({"error": "Video processing failed"}), 500

    db = SessionLocal()
    try:
        from app.core.text_utils import slugify_turkish
        
        threshold_setting = db.query(SystemSettings).filter_by(key="x_publish_threshold").first()
        base_tps = float(threshold_setting.value) if threshold_setting else 60.0
        
        external_id = f"trendiatr_ed_{uuid.uuid4().hex[:8]}"
        target_trend_id = request.form.get('target_trend_id')
        
        trend = None
        
        # 1. Handle Forced Merging or AI Clustering
        if target_trend_id and target_trend_id.strip().isdigit():
            trend = db.query(Trend).filter(Trend.id == int(target_trend_id)).first()
            if not trend:
                return jsonify({"error": "Belirtilen Trend ID bulunamadı."}), 404
            cluster_id = trend.cluster_id
            
            # Inject into ChromaDB silently to train the cluster
            try:
                vector = ai_engine.get_embedding(content)
                ai_engine.collection.add(
                    documents=[content],
                    embeddings=[vector],
                    metadatas=[{"source": "TrendiaTR", "cluster_id": cluster_id, "external_id": external_id, "timestamp": datetime.utcnow().timestamp(), "is_reference": False}],
                    ids=[str(uuid.uuid4())]
                )
            except: pass
        else:
            cluster_id, is_duplicate = ai_engine.process_news(content, "TrendiaTR", external_id)
            if not cluster_id:
                return jsonify({"error": "Vector database processing error"}), 500
            if is_duplicate:
                trend = db.query(Trend).filter(Trend.cluster_id == cluster_id).first()
                
        # 2. Create new Trend if it doesn't exist
        if not trend:
            trend = Trend(cluster_id=cluster_id, first_seen=datetime.utcnow())
            db.add(trend)
            db.flush() 
            
        # 3. Apply Editorial Updates
        trend.title = title
        trend.summary = summary
        trend.category = category
        if image_url:
            trend.cover_image = image_url
        if video_url:
            trend.video_path = video_url
            
        # NEW: Save Telegram caption in the JSON column
        entities_dict = {}
        if entities_str:
            try:
                entities_dict = json.loads(entities_str)
            except json.JSONDecodeError:
                pass
        if telegram_caption:
            entities_dict["telegram_caption"] = telegram_caption
        trend.entities = entities_dict
            
        # 4. Generate SEO Slug if missing
        if not trend.slug and title:
            base_slug = slugify_turkish(title)
            unique_slug = base_slug
            counter = 1
            while db.query(Trend).filter(Trend.slug == unique_slug, Trend.id != trend.id).first():
                unique_slug = f"{base_slug}-{counter}"
                counter += 1
            trend.slug = unique_slug
            
        # 5. Apply high TPS
        trend.final_tps = max(trend.final_tps or 0.0, base_tps)
        trend.previous_tps = base_tps
        trend.score = trend.final_tps
        trend.trajectory = "up"
        trend.is_active = True
        trend.has_social_signal = True 
        trend.last_updated = datetime.utcnow()

        raw_news = RawNews(
            source_type="editorial",
            source_name="TrendiaTR",
            source_tier=1, 
            external_id=external_id,
            content=content,
            published_at=datetime.utcnow(),
            trend_id=trend.id
        )
        if image_url:
            raw_news.media_status = 2
            raw_news.media_path = image_url
        if video_url:
            raw_news.video_path = video_url
            
        db.add(raw_news)
        db.flush()

        arrival = TrendArrivals(trend_id=trend.id, raw_news_id=raw_news.id, timestamp=datetime.utcnow())
        db.add(arrival)
        
        # 6. Publish to Telegram immediately
        target_url = f"{get_public_url()}/trend/{trend.slug if trend.slug else trend.cluster_id}"
        utm_params = "utm_source=telegram&utm_medium=channel&utm_campaign=hot_trends"
        separator = "&" if "?" in target_url else "?"
        target_url = f"{target_url}{separator}{utm_params}"
        
        # STRICT ENFORCEMENT: Extract dedicated telegram caption
        tg_caption = None
        if isinstance(trend.entities, dict):
            tg_caption = trend.entities.get("telegram_caption")
            
        if not tg_caption:
            db.rollback()
            return jsonify({"error": "Telegram Caption (Özel Özet) eksik! Lütfen içeriğin AI tarafından tam oluşturulduğundan emin olun."}), 400

        success = alert_service.publish_to_channel(
            title=trend.title,
            summary=tg_caption, # MUST use the dedicated caption here!
            category=trend.category,
            url=target_url,
            image_path=trend.cover_image,
            video_path=trend.video_path
        )
        
        if success:
            trend.is_published = True

        db.commit()
        return jsonify({"status": "success", "trend_id": trend.id, "tps": trend.final_tps})
        
    except Exception as e:
        db.rollback()
        logger.error(f"Publish Editorial News Error: {e}")
        return jsonify({"error": str(e)}), 500
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

            # 1. Calculate Confidence (Güven Endeksi)
            confidence_val = getattr(trend, 'tps_confidence', 0.85)
            if confidence_val is None:
                confidence_val = 0.85
            confidence_pct = int(confidence_val * 100)
            
            if confidence_pct >= 90:
                conf_label = "Teyitli Kaynaklar"
            elif confidence_pct >= 75:
                conf_label = "Güvenilir Veri"
            else:
                conf_label = "Gelişmekte Olan Haber"

            # 2. Calculate Trend Power (Gündem Gücü)
            if tps_val >= 80:
                power_label = "Kritik"
            elif tps_val >= 50:
                power_label = "Yüksek"
            else:
                power_label = "Dikkat Çekici"

            # Sanitize the question to prevent double emojis
            clean_question = ai_data.get('interaction_question', '').replace("💬", "").strip()

            main_tweet = (
                f"{ai_data['ai_summary']}\n\n"
                f"🛡️ Güven Endeksi: %{confidence_pct} ({conf_label})\n"
                f"📈 Gündem Gücü: {power_label} (Normalden {spread_speed}x daha hızlı yayılıyor)\n\n"
                f"💬 {clean_question}\n\n"
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
                "created_at": d.created_at.isoformat(),
                "reply_to_tweet_id": d.reply_to_tweet_id
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
            
            # If we discard a radar draft, we must also remove it from the "Active Radars" UI
            if draft.draft_type == 'radar':
                trend = db.query(Trend).filter(Trend.id == draft.trend_id).first()
                if trend:
                    trend.radar_phase_triggered = False
                    trend.radar_tweet_id = None
                    
            # Delete the physical image file to save space, just like in 'mark_sent'
            if draft.image_path:
                try:
                    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    file_path = os.path.join(base_dir, draft.image_path.lstrip('/'))
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except Exception as e:
                    logger.error(f"Failed to delete discarded image: {e}")
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
    phase_type = data.get('phase_type', 'standard')
    
    if not trend_id:
        return jsonify({"error": "trend_id is required"}), 400
        
    db = SessionLocal()
    try:
        trend = db.query(Trend).filter(Trend.id == trend_id).first()
        if not trend:
            return jsonify({"error": "Trend not found"}), 404
            
        # Check if draft exists and REPLACE it to avoid UniqueConstraint errors
        existing = db.query(XDraft).filter(XDraft.trend_id == trend.id, XDraft.draft_type == phase_type).first()
        if existing:
            # Delete physical image to save space
            if existing.image_path:
                try:
                    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    file_path = os.path.join(base_dir, existing.image_path.lstrip('/'))
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except Exception as e:
                    logger.warning(f"Could not delete old image: {e}")
            
            # Remove old draft from DB
            db.delete(existing)
            db.commit()

        # Generate Content
        context_text = trend.summary if trend.summary else trend.title
        ai_data = generate_x_content(trend.title, context_text, trend.category, phase_type=phase_type)
        
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

        # 1. Calculate Confidence (Güven Endeksi)
        confidence_val = getattr(trend, 'tps_confidence', 0.85)
        if confidence_val is None:
            confidence_val = 0.85
        confidence_pct = int(confidence_val * 100)
        
        if phase_type == 'radar':
            conf_label = "⏳ İnceleniyor - İlk Sinyaller"
        elif confidence_pct >= 90:
            conf_label = "Teyitli Kaynaklar"
        elif confidence_pct >= 75:
            conf_label = "Güvenilir Veri"
        else:
            conf_label = "Gelişmekte Olan Haber"

        # 2. Calculate Trend Power (Gündem Gücü)
        if tps_val >= 80:
            power_label = "Kritik"
        elif tps_val >= 50:
            power_label = "Yüksek"
        else:
            power_label = "Dikkat Çekici"

        # Sanitize the question to prevent double emojis
        clean_question = ai_data.get('interaction_question', '').replace("💬", "").strip()

        main_tweet = (
            f"{ai_data['ai_summary']}\n\n"
            f"🛡️ Güven Endeksi: %{confidence_pct} ({conf_label})\n"
            f"📈 Gündem Gücü: {power_label} (Normalden {spread_speed}x daha hızlı yayılıyor)\n\n"
            f"💬 {clean_question}\n\n"
            f"#{hash1} #{hash2} #TrendiaTR"
        )
        
        if phase_type == 'radar':
            main_tweet = "💬 Bu neden trend?\n\n" + main_tweet
            trend.radar_phase_triggered = True
        elif phase_type == 'confirmed':
            main_tweet = "🚨 DOĞRULANDI (Sistem Güncellemesi):\n\n" + main_tweet
            trend.radar_phase_triggered = False

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
            status='draft',
            draft_type=phase_type
        )
        
        if phase_type == 'confirmed' and trend.radar_tweet_id:
            draft.reply_to_tweet_id = trend.radar_tweet_id

        db.add(draft)
        db.commit()
        
        notify_admin_x_draft(trend.title, tps_val, f"Manual ID ({phase_type.capitalize()})")
        
        return jsonify({"status": "success", "draft_id": draft.id})
    except Exception as e:
        db.rollback()
        logger.error(f"Manual X Draft Generation Error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@api_bp.route('/api/admin/x-drafts/generate-thread-by-id', methods=['POST'])
@requires_auth
def generate_x_thread_by_id():
    data = request.json or {}
    trend_id = data.get('trend_id')
    
    if not trend_id:
        return jsonify({"error": "trend_id is required"}), 400
        
    db = SessionLocal()
    try:
        trend = db.query(Trend).filter(Trend.id == trend_id).first()
        if not trend:
            return jsonify({"error": "Trend not found"}), 404
            
        # Check if draft exists and REPLACE it to avoid UniqueConstraint errors
        existing = db.query(XDraft).filter(XDraft.trend_id == trend.id).first()
        if existing:
            # Delete physical image to save space
            if existing.image_path:
                try:
                    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    file_path = os.path.join(base_dir, existing.image_path.lstrip('/'))
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except Exception as e:
                    logger.warning(f"Could not delete old image: {e}")
            
            # Remove old draft from DB
            db.delete(existing)
            db.commit()

        # Generate Content
        context_text = trend.summary if trend.summary else trend.title
        ai_data = generate_x_thread(trend.title, context_text, trend.category)
        
        if not ai_data:
            return jsonify({"error": "AI thread generation failed"}), 500
            
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

        # 1. Calculate Confidence (Güven Endeksi)
        confidence_val = getattr(trend, 'tps_confidence', 0.85)
        if confidence_val is None:
            confidence_val = 0.85
        confidence_pct = int(confidence_val * 100)
        
        if confidence_pct >= 90:
            conf_label = "Teyitli Kaynaklar"
        elif confidence_pct >= 75:
            conf_label = "Güvenilir Veri"
        else:
            conf_label = "Gelişmekte Olan Haber"

        # 2. Calculate Trend Power (Gündem Gücü)
        if tps_val >= 80:
            power_label = "Kritik"
        elif tps_val >= 50:
            power_label = "Yüksek"
        else:
            power_label = "Dikkat Çekici"

        part1 = (
            f"{ai_data['tweet_1_hook']}\n\n"
            f"🛡️ Güven Endeksi: %{confidence_pct} ({conf_label})\n"
            f"📈 Gündem Gücü: {power_label} (Normalden {spread_speed}x daha hızlı yayılıyor)\n\n"
            f"Devamı ↓ 🧵"
        )
        part2 = ai_data['tweet_2_facts']
        part3 = ai_data['tweet_3_ai_insight']
        
        # Sanitize the question to prevent double emojis
        clean_question = ai_data.get('tweet_4_cta', '').replace("💬", "").strip()
        part4 = f"💬 {clean_question}\n\n🔗 Olayın tüm detayları ve harita: 👇\n{full_link}"

        caption = f"{part1}\n\n====THREAD====\n\n{part2}\n\n====THREAD====\n\n{part3}\n\n====THREAD====\n\n{part4}"
        
        # Close the radar phase since a thread is being generated
        trend.radar_phase_triggered = False

        # Save Draft
        draft = XDraft(
            trend_id=trend.id,
            hook_text=ai_data['tweet_1_hook'][:50],
            long_caption=caption, # Storing full thread in long_caption column
            image_short_text=ai_data['image_short_text'],
            tps_score=tps_val,
            image_path=image_path,
            status='draft'
        )
        db.add(draft)
        db.commit()
        
        notify_admin_x_draft(trend.title, tps_val, "Thread Mode")
        
        return jsonify({"status": "success", "draft_id": draft.id})
    except Exception as e:
        db.rollback()
        logger.error(f"Manual X Thread Generation Error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@api_bp.route('/api/admin/x-radars/active', methods=['GET'])
@requires_auth
def get_active_radars():
    db = SessionLocal()
    try:
        radars = db.query(Trend).filter(Trend.radar_phase_triggered == True).order_by(desc(Trend.last_updated)).all()
        results = []
        for r in radars:
            results.append({
                "id": r.id,
                "title": r.title,
                "final_tps": round(r.final_tps, 1) if r.final_tps else 0.0,
                "radar_tweet_id": r.radar_tweet_id
            })
        return jsonify(results)
    finally:
        db.close()

@api_bp.route('/api/admin/x-radars/<int:trend_id>/set-tweet-id', methods=['POST'])
@requires_auth
def set_radar_tweet_id(trend_id):
    data = request.json or {}
    tweet_id = data.get('tweet_id')
    
    if not tweet_id:
        return jsonify({"error": "tweet_id is required"}), 400
        
    db = SessionLocal()
    try:
        trend = db.query(Trend).filter(Trend.id == trend_id).first()
        if not trend:
            return jsonify({"error": "Trend not found"}), 404
            
        trend.radar_tweet_id = str(tweet_id)
        db.commit()
        return jsonify({"status": "success", "radar_tweet_id": trend.radar_tweet_id})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@api_bp.route('/api/admin/x-radars/<int:trend_id>', methods=['DELETE'])
@requires_auth
def cancel_active_radar(trend_id):
    """Cancels an active radar mission by resetting its flags"""
    db = SessionLocal()
    try:
        trend = db.query(Trend).filter(Trend.id == trend_id).first()
        if not trend:
            return jsonify({"error": "Trend not found"}), 404
            
        trend.radar_phase_triggered = False
        trend.radar_tweet_id = None
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        db.rollback()
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
    cache_key = f"trends_v2_{category}_{list_type}_{offset}_{limit}_{q}_{date_str}"
    if redis_client:
        cached_data = redis_client.get(cache_key)
        if cached_data:
            return make_response(cached_data, 200, {"Content-Type": "application/json"})

    db = SessionLocal()
    try:
        query = db.query(Trend).filter(Trend.is_active == True)
        
        # Only show trends that have a summary or are highly scored (waiting for summary)
        query = query.filter(or_(
            Trend.summary.isnot(None), 
            Trend.final_tps >= 15.0
        ))
        
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
            comments_count = db.query(Comment).filter(Comment.trend_id == t.id, Comment.status == 'approved').count()
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
                "image": t.cover_image,
                "video_path": t.video_path,
                "comments_count": comments_count
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
    cache_key = f"detail_v2_{identifier}"
    if redis_client:
        cached_data = redis_client.get(cache_key)
        if cached_data:
            return make_response(cached_data, 200, {"Content-Type": "application/json"})

    db = SessionLocal()
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
        
        comments_count = db.query(Comment).filter(Comment.trend_id == trend.id, Comment.status == 'approved').count()

        formatted_news = []
        for n in news_items:
            if n.source_type == 'editorial':
                link = get_public_url()
            else:
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
            "video_path": trend.video_path,
            "comments_count": comments_count,
            "tags": trend.tags or [],
            "entities": trend.entities or {},
            "news_list": formatted_news,
            "related_trends": [{
                "title": r.title,
                "category": r.category,
                "slug": r.slug or r.cluster_id,
                "date": r.last_updated.strftime('%d.%m.%Y') if r.last_updated else "",
                "relation_type": (
                    "Ana Olay" if r.first_seen < trend.first_seen else 
                    "Yeni Gelişme" if r.first_seen > trend.first_seen else 
                    "İlgili"
                )
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

@api_bp.route('/api/admin/trends/merge', methods=['POST'])
@requires_auth
def admin_merge_trends():
    """Merges a source trend into a target trend completely with cache fixes and velocity spikes"""
    data = request.json or {}
    source_id = data.get('source_id')
    target_id = data.get('target_id')
    
    if not source_id or not target_id or int(source_id) == int(target_id):
        return jsonify({"error": "Geçersiz ID'ler (Aynı haber birleştirilemez)"}), 400
        
    db = SessionLocal()
    try:
        source_trend = db.query(Trend).filter(Trend.id == int(source_id)).first()
        target_trend = db.query(Trend).filter(Trend.id == int(target_id)).first()
        
        if not source_trend or not target_trend:
            return jsonify({"error": "Trend bulunamadı"}), 404
            
        # 1. Transfer RawNews
        db.query(RawNews).filter(RawNews.trend_id == source_trend.id).update({"trend_id": target_trend.id})
        
        # Transfer TrendArrivals AND update timestamp to NOW to trigger a Velocity/Acceleration spike!
        db.query(TrendArrivals).filter(TrendArrivals.trend_id == source_trend.id).update({
            "trend_id": target_trend.id,
            "timestamp": datetime.utcnow()
        })
        
        # 2. Inherit Image (Fallback to source image if target has none)
        if not target_trend.cover_image and source_trend.cover_image:
            target_trend.cover_image = source_trend.cover_image
            
        # 3. Update Counts and Trigger AI/Scoring
        target_trend.message_count += source_trend.message_count
        target_trend.needs_scoring = True
        target_trend.last_updated = datetime.utcnow()
        
        # 4. Deactivate Source Trend
        source_trend.is_active = False
        
        # 5. Update ChromaDB Vectors
        ai_engine.merge_clusters(source_trend.cluster_id, target_trend.cluster_id)
        
        db.commit()
        
        # 6. Clear Redis Cache (Aggressive Invalidation)
        if redis_client:
            # Clear specific trend pages
            for t in [source_trend, target_trend]:
                keys_to_delete = [
                    f"ssr_trend_{t.id}", 
                    f"ssr_trend_{t.slug}", 
                    f"ssr_trend_{t.id}-{t.slug}",
                    f"detail_v1_{t.id}", 
                    f"detail_v1_{t.slug}", 
                    f"detail_v1_{t.id}-{t.slug}"
                ]
                for k in keys_to_delete:
                    redis_client.delete(k)
            
            # Clear homepage and category list caches so the UI updates instantly
            for key in redis_client.scan_iter("trends_v1_*"):
                redis_client.delete(key)
                
        return jsonify({"status": "success", "target_id": target_trend.id})
    except Exception as e:
        db.rollback()
        logger.error(f"Merge API Error: {e}")
        return jsonify({"error": str(e)}), 500
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