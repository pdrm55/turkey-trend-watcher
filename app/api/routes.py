from flask import Blueprint, jsonify, render_template, request, make_response, abort
from app.database.models import SessionLocal, Trend, RawNews
from sqlalchemy import desc
from datetime import datetime, timedelta
from xml.sax.saxutils import escape
import re

api_bp = Blueprint('api', __name__)

# SEO optimized landing page categories
VALID_CATEGORIES = ["Siyaset", "Ekonomi", "Gündem", "Spor", "Teknoloji", "Sanat"]
JUNK_KEYWORDS = ['burç', 'fal ', 'günlük burç', 'astroloji', 'horoskop']

def get_public_url():
    """Calculates public URL considering Nginx proxy headers for SEO"""
    protocol = request.headers.get('X-Forwarded-Proto', 'https')
    host = request.headers.get('X-Forwarded-Host', request.host)
    return f"{protocol}://{host}".rstrip('/')

@api_bp.route('/')
def dashboard():
    """Renders the main dashboard (Home)"""
    return render_template(
        'index.html', 
        active_category="Hepsi",
        page_title="TrendiaTR | Yapay Zeka Haber Analizi",
        page_description="TrendiaTR ile gerçek zamanlı yapay zeka haber analizi ve Türkiye'deki son gelişmeler."
    )

@api_bp.route('/category/<name>')
def category_page(name):
    """Renders category-specific landing pages for SEO indexing"""
    cat_name = name.capitalize()
    if cat_name not in VALID_CATEGORIES:
        abort(404)
    
    seo_meta = {
        "Siyaset": {"title": "Siyaset Haberleri | TrendiaTR", "desc": "Türkiye ve dünya siyasetine dair en son gelişmeler."},
        "Ekonomi": {"title": "Ekonomi ve Borsa Haberleri | TrendiaTR", "desc": "Döviz kurları ve ekonomi dünyasından anlık analizler."},
        "Gündem": {"title": "Gündemdeki Son Dakika Haberleri | TrendiaTR", "desc": "Türkiye gündemindeki en önemli olaylar."},
        "Spor": {"title": "Spor Dünyasından Gelişmeler | TrendiaTR", "desc": "Süper Lig ve transfer haberleri analizi."},
        "Teknoloji": {"title": "Teknoloji ve Bilim Haberleri | TrendiaTR", "desc": "Yapay zeka و teknoloji dünyası özeti."},
        "Sanat": {"title": "Sanat ve Magazin Haberleri | TrendiaTR", "desc": "Popüler kültür ve sanat dünyası haberleri."}
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
    """SSR for news detail pages with related trends and TPS context"""
    db = SessionLocal()
    from app.core.ai_engine import ai_engine 
    try:
        # Search by slug or cluster_id
        trend = db.query(Trend).filter((Trend.slug == identifier) | (Trend.cluster_id == identifier)).first()
        
        if not trend:
            abort(404)
            
        news_items = db.query(RawNews).filter(RawNews.trend_id == trend.id).order_by(desc(RawNews.published_at)).limit(20).all()
        
        formatted_news = []
        for n in news_items:
            link = n.external_id or ""
            if link and not link.startswith('http'):
                link = f"https://{link}"
            formatted_news.append({
                "source": n.source_name,
                "time": n.published_at,
                "content": n.content,
                "link": link
            })
            
        # Get related trends via Vector Search
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

@api_bp.route('/api/trends')
def get_trends():
    """API endpoint for fetching trend lists with TPS metrics"""
    db = SessionLocal()
    try:
        category = request.args.get('category', 'All')
        list_type = request.args.get('type', 'timeline')
        offset = int(request.args.get('offset', 0))
        limit = int(request.args.get('limit', 32))

        query = db.query(Trend).filter(Trend.is_active == True)
        
        if category != 'All':
            query = query.filter(Trend.category == category)

        if list_type == 'hot':
            # Hot trends based on TPS and recency (24h)
            time_threshold = datetime.now() - timedelta(hours=24)
            query = query.filter(Trend.last_updated >= time_threshold)
            # Filter out junk content from hot section
            for word in JUNK_KEYWORDS:
                query = query.filter(~Trend.title.ilike(f'%{word}%'))
            # Priority: final_tps > legacy score > update time
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
        return jsonify(results)
    finally:
        db.close()

@api_bp.route('/sitemap.xml')
def sitemap():
    """Dynamic XML sitemap generator"""
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

@api_bp.route('/api/trends/<identifier>')
def get_trend_details(identifier):
    """API for modal display with TPS metrics and related news"""
    db = SessionLocal()
    from app.core.ai_engine import ai_engine
    try:
        trend = db.query(Trend).filter((Trend.slug == identifier) | (Trend.cluster_id == identifier)).first()
        if not trend: return jsonify({"error": "Trend not found"}), 404
        
        news_items = db.query(RawNews).filter(RawNews.trend_id == trend.id).order_by(desc(RawNews.published_at)).limit(20).all()
        
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

        return jsonify({
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
        })
    finally:
        db.close()

@api_bp.route('/api/stats')
def get_stats():
    """Global system stats for header display"""
    db = SessionLocal()
    try:
        return jsonify({
            "total_news": db.query(RawNews).count(),
            "total_trends": db.query(Trend).filter(Trend.is_active == True).count()
        })
    finally:
        db.close()