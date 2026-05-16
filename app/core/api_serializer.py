import os
from app.database.models import Trend, RawNews

BASE_SITE_URL = os.getenv("BASE_SITE_URL", "https://trendiatr.com")


def _make_absolute(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith("http"):
        return path
    return f"{BASE_SITE_URL}{path}"


def serialize_media(news: RawNews) -> list[dict]:
    media = []
    if news.media_url and news.media_status == 2:
        entry = {
            "type": "image",
            "url": _make_absolute(news.media_path) or news.media_url,
            "source_url": news.media_url,
        }
        if news.media_meta and isinstance(news.media_meta, dict):
            entry["width"] = news.media_meta.get("width")
            entry["height"] = news.media_meta.get("height")
        media.append(entry)
    elif news.media_url:
        media.append({
            "type": "image",
            "url": news.media_url,
            "source_url": news.media_url,
            "status": "pending_download" if news.media_status == 0 else "processing"
        })

    if news.video_path:
        media.append({
            "type": "video",
            "url": _make_absolute(news.video_path),
        })

    return media


def serialize_article(news: RawNews) -> dict:
    return {
        "id": news.id,
        "title": (news.content or "")[:120].strip() if news.content else None,
        "content_preview": (news.content or "")[:500].strip() if news.content else None,
        "source_name": news.source_name,
        "source_type": news.source_type,
        "source_tier": news.source_tier,
        "published_at": news.published_at.isoformat() + "Z" if news.published_at else None,
        "media": serialize_media(news),
    }


def serialize_trend_summary(trend: Trend) -> dict:
    return {
        "id": trend.id,
        "title": trend.title,
        "category": trend.category,
        "tps_score": round(trend.final_tps, 2),
        "tps_signal": round(trend.tps_signal, 2),
        "tps_confidence": round(trend.tps_confidence, 2),
        "trajectory": trend.trajectory,
        "article_count": trend.message_count,
        "first_seen": trend.first_seen.isoformat() + "Z" if trend.first_seen else None,
        "last_updated": trend.last_updated.isoformat() + "Z" if trend.last_updated else None,
        "url": f"{BASE_SITE_URL}/trend/{trend.slug or trend.id}",
        "cover_image": _make_absolute(trend.cover_image),
        "has_video": bool(trend.video_path),
    }


def serialize_trend_full(trend: Trend) -> dict:
    base = serialize_trend_summary(trend)
    base.update({
        "summary": trend.summary,
        "tags": trend.tags or [],
        "entities": trend.entities or [],
        "video_url": _make_absolute(trend.video_path),
        "cluster": {
            "article_count": len(trend.news_items),
            "articles": [serialize_article(n) for n in trend.news_items]
        }
    })
    return base
