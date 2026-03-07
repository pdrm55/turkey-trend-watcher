import os
import logging
from flask import Flask, Blueprint, request, jsonify
from app.api.routes import api_bp
from app.database.models import init_db, SessionLocal, Comment, CommentVote, Trend
from app.core.ai_engine import ai_engine
from sqlalchemy import or_, desc

# تنظیمات لاگر برای مانیتورینگ متمرکز سیستم
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("TrendiaTR-Web")

# Blueprint for Comments API
comments_bp = Blueprint('comments_bp', __name__, url_prefix='/api/comments')

@comments_bp.route('/<identifier>', methods=['GET'])
def get_comments(identifier):
    session_id = request.args.get('session_id', '')
    sort_by = request.args.get('sort', 'popular') # popular or newest
    
    with SessionLocal() as db:
        # Resolve identifier to trend_id
        trend = db.query(Trend).filter(or_(Trend.slug == identifier, Trend.cluster_id == identifier)).first()
        if not trend and identifier.isdigit():
            trend = db.query(Trend).filter(Trend.id == int(identifier)).first()
            
        if not trend:
            return jsonify({"error": "Trend not found"}), 404
            
        # Fetch comments: approved OR (shadow_banned/pending but belongs to this session)
        query = db.query(Comment).filter(
            Comment.trend_id == trend.id,
            or_(
                Comment.status == 'approved',
                Comment.session_id == session_id
            )
        )
        
        if sort_by == 'newest':
            query = query.order_by(desc(Comment.created_at))
        else:
            # Popular: likes - dislikes
            query = query.order_by(desc(Comment.likes - Comment.dislikes), desc(Comment.created_at))
            
        comments = query.all()
        
        # Get user's votes for these comments
        user_votes = {}
        if session_id:
            votes = db.query(CommentVote).filter(
                CommentVote.session_id == session_id,
                CommentVote.comment_id.in_([c.id for c in comments])
            ).all()
            user_votes = {v.comment_id: v.vote_type for v in votes}
            
        result = []
        for c in comments:
            result.append({
                "id": c.id,
                "user_name": c.user_name,
                "content": c.content,
                "likes": c.likes,
                "dislikes": c.dislikes,
                "created_at": c.created_at.isoformat(),
                "user_vote": user_votes.get(c.id, 0),
                "is_own": c.session_id == session_id,
                "status": c.status
            })
            
        return jsonify(result)

@comments_bp.route('/<identifier>', methods=['POST'])
def post_comment(identifier):
    data = request.json
    user_name = data.get('user_name', '').strip()
    content = data.get('content', '').strip()
    session_id = data.get('session_id', '').strip()
    
    if not user_name or not content or not session_id:
        return jsonify({"error": "Missing required fields"}), 400
        
    with SessionLocal() as db:
        trend = db.query(Trend).filter(or_(Trend.slug == identifier, Trend.cluster_id == identifier)).first()
        if not trend and identifier.isdigit():
            trend = db.query(Trend).filter(Trend.id == int(identifier)).first()
            
        if not trend:
            return jsonify({"error": "Trend not found"}), 404
            
        # Rate limiting check (e.g., max 2 comments per 5 mins)
        # Simplified for now: just check last comment time
        last_comment = db.query(Comment).filter(Comment.session_id == session_id).order_by(desc(Comment.created_at)).first()
        
        # AI Auto-Moderation
        ai_status = ai_engine.moderate_comment(content)
        
        new_comment = Comment(
            trend_id=trend.id,
            user_name=user_name[:100],
            session_id=session_id,
            content=content,
            status=ai_status
        )
        db.add(new_comment)
        db.commit()
        
        return jsonify({"status": "success", "moderation_status": ai_status})

@comments_bp.route('/vote/<int:comment_id>', methods=['POST'])
def vote_comment(comment_id):
    data = request.json
    session_id = data.get('session_id', '').strip()
    vote_type = data.get('vote_type') # 1 or -1
    
    if not session_id or vote_type not in [1, -1]:
        return jsonify({"error": "Invalid data"}), 400
        
    with SessionLocal() as db:
        comment = db.query(Comment).filter(Comment.id == comment_id).first()
        if not comment:
            return jsonify({"error": "Comment not found"}), 404
            
        existing_vote = db.query(CommentVote).filter(
            CommentVote.comment_id == comment_id,
            CommentVote.session_id == session_id
        ).first()
        
        if existing_vote:
            if existing_vote.vote_type == vote_type:
                # Toggle off
                db.delete(existing_vote)
                if vote_type == 1: comment.likes -= 1
                else: comment.dislikes -= 1
            else:
                # Switch vote
                existing_vote.vote_type = vote_type
                if vote_type == 1:
                    comment.likes += 1
                    comment.dislikes -= 1
                else:
                    comment.likes -= 1
                    comment.dislikes += 1
        else:
            # New vote
            new_vote = CommentVote(comment_id=comment_id, session_id=session_id, vote_type=vote_type)
            db.add(new_vote)
            if vote_type == 1: comment.likes += 1
            else: comment.dislikes += 1
            
        db.commit()
        return jsonify({"status": "success", "likes": comment.likes, "dislikes": comment.dislikes})

# Admin Comments API
admin_comments_bp = Blueprint('admin_comments_bp', __name__, url_prefix='/api/admin/comments')

@admin_comments_bp.route('', methods=['GET'])
def get_admin_comments():
    status_filter = request.args.get('status', 'all')
    with SessionLocal() as db:
        query = db.query(Comment, Trend.title).join(Trend, Comment.trend_id == Trend.id)
        if status_filter != 'all':
            query = query.filter(Comment.status == status_filter)
            
        comments = query.order_by(desc(Comment.created_at)).limit(100).all()
        
        result = []
        for c, t_title in comments:
            result.append({
                "id": c.id,
                "trend_title": t_title,
                "user_name": c.user_name,
                "content": c.content,
                "status": c.status,
                "created_at": c.created_at.isoformat(),
                "session_id": c.session_id
            })
        return jsonify(result)

@admin_comments_bp.route('/<int:comment_id>/status', methods=['POST'])
def update_comment_status(comment_id):
    data = request.json
    new_status = data.get('status')
    if new_status not in ['approved', 'pending', 'rejected', 'shadow_banned']:
        return jsonify({"error": "Invalid status"}), 400
        
    with SessionLocal() as db:
        comment = db.query(Comment).filter(Comment.id == comment_id).first()
        if not comment:
            return jsonify({"error": "Not found"}), 404
            
        comment.status = new_status
        
        # If shadow banning, ban all comments from this session
        if new_status == 'shadow_banned':
            db.query(Comment).filter(Comment.session_id == comment.session_id).update({"status": "shadow_banned"})
            
        db.commit()
        return jsonify({"status": "success"})

def create_app():
    """
    ساختار کارخانه‌ای (Factory Pattern) برای ایجاد اپلیکیشن Flask.
    این ساختار برای اجرای بهینه توسط Gunicorn و مدیریت چندین ورکر ضروری است.
    """
    
    # حفظ منطق مسیردهی صریح از نسخه قبلی برای اطمینان از بارگذاری صحیح قالب‌ها و فایل‌های استاتیک
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    TEMPLATE_DIR = os.path.join(BASE_DIR, 'app/templates')
    STATIC_DIR = os.path.join(BASE_DIR, 'app/static')

    app = Flask(__name__, 
                template_folder=TEMPLATE_DIR, 
                static_folder=STATIC_DIR,
                static_url_path='/static')

    # افزایش محدودیت حجم آپلود به 50 مگابایت برای رفع ارور 413
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

    # ثبت بلوپرینت اصلی API و مسیرهای مسیریابی (Routing)
    app.register_blueprint(api_bp)
    app.register_blueprint(comments_bp)
    app.register_blueprint(admin_comments_bp)

    # اطمینان از آماده‌سازی دیتابیس در بدو ورود به اپلیکیشن
    with app.app_context():
        try:
            # فراخوانی تابع هماهنگ‌سازی دیتابیس (برگرفته از منطق اصلی فایل قبلی)
            init_db()
            logger.info("✅ Database schemas verified and synchronized.")
        except Exception as e:
            logger.error(f"❌ Database Initialization Error: {e}")

    return app

# ایجاد آبجکت اصلی اپلیکیشن جهت استفاده Gunicorn (Entry Point)
app = create_app()

if __name__ == "__main__":
    # اجرای مستقیم برای دیباگ و توسعه لوکال (در محیط عملیاتی Docker از Gunicorn استفاده می‌شود)
    logger.info("🚀 Starting TrendiaTR Web Server in Debug Mode...")
    
    # خواندن پورت از متغیرهای محیطی یا استفاده از پورت پیش‌فرض ۵۰۰۰
    port = int(os.getenv("PORT", 5000))
    
    # در حالت اجرای مستقیم، Debug فعال می‌ماند (مشابه نسخه قبلی شما)
    app.run(host='0.0.0.0', port=port, debug=True)
