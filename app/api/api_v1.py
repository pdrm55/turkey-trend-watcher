import os
from flask import Blueprint, jsonify, request, g
from sqlalchemy import desc, text
from app.database.models import SessionLocal, Trend, APIClient
from app.core.api_auth import require_api_key
from app.core.api_serializer import serialize_trend_summary, serialize_trend_full, serialize_media, _make_absolute

bp = Blueprint('api_v1', __name__, url_prefix='/api/v1')


# --------------------------------------------------
# GET /api/v1/health  (no auth — for uptime monitors)
# --------------------------------------------------
@bp.route('/health', methods=['GET'])
def health():
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return jsonify({"status": "ok", "service": "TrendiaTR B2B API", "version": "1.0"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 503
    finally:
        db.close()


# --------------------------------------------------
# GET /api/v1/trends
# --------------------------------------------------
@bp.route('/trends', methods=['GET'])
@require_api_key
def list_trends():
    """
    List active trends above the client's TPS threshold.

    Query params:
        min_tps     float   Override threshold (cannot go below client's plan threshold)
        category    str     Filter by category (case-insensitive)
        trajectory  str     Filter: up | down | steady
        limit       int     Max results (default 20, max 100)
        offset      int     Pagination offset (default 0)
    """
    client = g.api_client
    plan_threshold = client['tps_threshold']

    try:
        requested_tps = float(request.args.get('min_tps', plan_threshold))
    except (ValueError, TypeError):
        return jsonify({"error": "invalid_param", "message": "min_tps must be a number"}), 400

    min_tps = max(requested_tps, plan_threshold)

    try:
        limit = min(int(request.args.get('limit', 20)), 100)
        offset = max(int(request.args.get('offset', 0)), 0)
    except (ValueError, TypeError):
        return jsonify({"error": "invalid_param", "message": "limit and offset must be integers"}), 400

    category_filter = request.args.get('category', '').strip()
    trajectory_filter = request.args.get('trajectory', '').strip().lower()

    db = SessionLocal()
    try:
        query = db.query(Trend).filter(
            Trend.is_active == True,
            Trend.final_tps >= min_tps,
            Trend.title.isnot(None)
        )
        if category_filter:
            query = query.filter(Trend.category.ilike(f"%{category_filter}%"))
        if trajectory_filter in ('up', 'down', 'steady'):
            query = query.filter(Trend.trajectory == trajectory_filter)

        total = query.count()
        trends = query.order_by(desc(Trend.final_tps)).limit(limit).offset(offset).all()

        return jsonify({
            "data": [serialize_trend_summary(t) for t in trends],
            "meta": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "min_tps_applied": min_tps,
                "plan_threshold": plan_threshold,
            }
        })
    finally:
        db.close()


# --------------------------------------------------
# GET /api/v1/trends/<id>
# --------------------------------------------------
@bp.route('/trends/<int:trend_id>', methods=['GET'])
@require_api_key
def get_trend(trend_id):
    """Full trend detail including all articles, media, and cluster data."""
    client = g.api_client
    db = SessionLocal()
    try:
        trend = db.query(Trend).filter(
            Trend.id == trend_id,
            Trend.is_active == True,
            Trend.final_tps >= client['tps_threshold']
        ).first()

        if not trend:
            return jsonify({
                "error": "not_found",
                "message": "Trend not found or below your plan's TPS threshold."
            }), 404

        return jsonify({"data": serialize_trend_full(trend)})
    finally:
        db.close()


# --------------------------------------------------
# GET /api/v1/trends/<id>/media
# --------------------------------------------------
@bp.route('/trends/<int:trend_id>/media', methods=['GET'])
@require_api_key
def get_trend_media(trend_id):
    """All media (images + videos) from a trend's article cluster."""
    client = g.api_client
    db = SessionLocal()
    try:
        trend = db.query(Trend).filter(
            Trend.id == trend_id,
            Trend.is_active == True,
            Trend.final_tps >= client['tps_threshold']
        ).first()

        if not trend:
            return jsonify({"error": "not_found", "message": "Trend not found."}), 404

        media_items = []

        if trend.cover_image:
            media_items.append({
                "source": "trend_cover",
                "type": "image",
                "url": _make_absolute(trend.cover_image)
            })
        if trend.video_path:
            media_items.append({
                "source": "trend_video",
                "type": "video",
                "url": _make_absolute(trend.video_path)
            })

        for article in trend.news_items:
            for m in serialize_media(article):
                m["source"] = "article"
                m["article_id"] = article.id
                m["source_name"] = article.source_name
                media_items.append(m)

        return jsonify({
            "trend_id": trend_id,
            "trend_title": trend.title,
            "media_count": len(media_items),
            "data": media_items
        })
    finally:
        db.close()


# --------------------------------------------------
# GET /api/v1/usage
# --------------------------------------------------
@bp.route('/usage', methods=['GET'])
@require_api_key
def get_usage():
    """Return current API usage stats for the authenticated client."""
    client = g.api_client
    db = SessionLocal()
    try:
        db_client = db.get(APIClient, client['id'])
        return jsonify({
            "plan": db_client.plan,
            "tps_threshold": db_client.tps_threshold,
            "monthly_limit": db_client.monthly_limit,
            "calls_used": db_client.calls_used,
            "calls_remaining": max(0, db_client.monthly_limit - db_client.calls_used),
            "resets_at": db_client.calls_reset_at.isoformat() + "Z" if db_client.calls_reset_at else None,
            "last_seen_at": db_client.last_seen_at.isoformat() + "Z" if db_client.last_seen_at else None
        })
    finally:
        db.close()
