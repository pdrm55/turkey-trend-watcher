from flask import Blueprint, jsonify, render_template, request, make_response, abort
from app.database.models import SessionLocal, Trend, RawNews
from sqlalchemy import desc
from datetime import datetime, timedelta
from xml.sax.saxutils import escape
import re

api_bp = Blueprint('api', __name__)

# SEO uyumlu landing page'ler için geçerli kategori listesi
VALID_CATEGORIES = ["Siyaset", "Ekonomi", "Gündem", "Spor", "Teknoloji", "Sanat"]
JUNK_KEYWORDS = ['burç', 'fal ', 'günlük burç', 'astroloji', 'horoskop']

def get_public_url():
    """SEO için Nginx proxy header'larını dikkate alarak genel URL'yi hesaplar"""
    protocol = request.headers.get('X-Forwarded-Proto', 'https')
    host = request.headers.get('X-Forwarded-Host', request.host)
    return f"{protocol}://{host}".rstrip('/')

@api_bp.route('/')
def dashboard():
    """Ana sayfayı oluşturur (varsayılan mod)"""
    return render_template(
        'index.html', 
        active_category="Hepsi",
        page_title="TrendiaTR | Yapay Zeka Haber Analizi",
        page_description="TrendiaTR ile gerçek zamanlı yapay zeka haber analizi ve Türkiye'deki son gelişmeler."
    )

# --- SEO AŞAMA 5: Kategori Sayfaları İçin Özel Rota (Landing Pages) ---
@api_bp.route('/category/<name>')
def category_page(name):
    """Google için belirli bir kategoriye odaklanarak ana sayfayı oluşturur"""
    # Kategori adını normalleştirme (İlk harf büyük)
    cat_name = name.capitalize()
    if cat_name not in VALID_CATEGORIES:
        abort(404)
    
    # SEO için her kategoriye özel meta etiketleri
    seo_meta = {
        "Siyaset": {"title": "Siyaset Haberleri | TrendiaTR", "desc": "Türkiye ve dünya siyasetine dair en son gelişmeler ve yapay zeka analizleri."},
        "Ekonomi": {"title": "Ekonomi ve Borsa Haberleri | TrendiaTR", "desc": "Döviz kurları, borsa istanbul ve ekonomi dünyasından anlık yapay zeka analizli haberler."},
        "Gündem": {"title": "Gündemdeki Son Dakika Haberleri | TrendiaTR", "desc": "Türkiye gündemini meşgul eden en önemli olaylar ve gerçek zamanlı özetler."},
        "Spor": {"title": "Spor Dünyasından Gelişmeler | TrendiaTR", "desc": "Süper Lig, transfer haberleri ve spor dünyasındaki önemli olayların analizi."},
        "Teknoloji": {"title": "Teknoloji ve Bilim Haberleri | TrendiaTR", "desc": "Yapay zeka, teknoloji dünyası ve bilimsel gelişmelerin özeti."},
        "Sanat": {"title": "Sanat ve Magazin Haberleri | TrendiaTR", "desc": "Sanat dünyası, etkinlikler ve popüler kültürün en son haberleri."}
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
    """İlgili haberlerle birlikte sunucu tarafı oluşturma (SSR) (SEO Aşama 4)"""
    db = SessionLocal()
    from app.core.ai_engine import ai_engine 
    try:
        # Slug veya cluster_id üzerinden arama
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
            
        # SSR için ilgili haberleri bulma
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
    """Ana sayfa için trend listesini alan API"""
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
            time_threshold = datetime.now() - timedelta(hours=24)
            query = query.filter(Trend.last_updated >= time_threshold)
            for word in JUNK_KEYWORDS:
                query = query.filter(~Trend.title.ilike(f'%{word}%'))
            trends = query.order_by(desc(Trend.score), desc(Trend.last_updated)).limit(8).all()
        else:
            trends = query.order_by(desc(Trend.first_seen)).offset(offset).limit(limit).all()
            
        results = []
        for t in trends:
            last_news = db.query(RawNews).filter(RawNews.trend_id == t.id).order_by(desc(RawNews.published_at)).first()
            results.append({
                "id": t.cluster_id,
                "slug": t.slug,
                "title": t.title or "Haber Başlığı Yok",
                "summary": t.summary or "Generating...",
                "score": t.score or 0,
                "count": t.message_count or 1,
                "category": t.category,
                "first_seen": t.first_seen.isoformat() + 'Z' if t.first_seen else None,
                "last_update": t.last_updated.isoformat() + 'Z' if t.last_updated else None, 
                "source_sample": last_news.source_name if last_news else "Unknown"
            })
        return jsonify(results)
    finally:
        db.close()

@api_bp.route('/sitemap.xml')
def sitemap():
    """Dinamik site haritası oluşturma"""
    db = SessionLocal()
    try:
        base_url = get_public_url()
        trends = db.query(Trend).filter(Trend.is_active == True).order_by(desc(Trend.last_updated)).limit(3000).all()
        
        xml_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        ]
        xml_lines.append(f'  <url><loc>{base_url}/</loc><changefreq>always</changefreq><priority>1.0</priority></url>')
        
        # Kategori sayfalarını site haritasına ekleme
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
    """Ana sayfa modalı için bir trendin ayrıntılarını alan API (ilgili haberlerle birlikte)"""
    db = SessionLocal()
    from app.core.ai_engine import ai_engine
    try:
        trend = db.query(Trend).filter((Trend.slug == identifier) | (Trend.cluster_id == identifier)).first()
        if not trend: return jsonify({"error": "Trend not found"}), 404
        
        news_items = db.query(RawNews).filter(RawNews.trend_id == trend.id).order_by(desc(RawNews.published_at)).limit(20).all()
        
        # Modal için ilgili haberleri alma
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
    """Başlıkta görüntülemek için genel sistem istatistikleri"""
    db = SessionLocal()
    try:
        return jsonify({
            "total_news": db.query(RawNews).count(),
            "total_trends": db.query(Trend).filter(Trend.is_active == True).count()
        })
    finally:
        db.close()
