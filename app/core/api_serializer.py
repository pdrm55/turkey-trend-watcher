import os
from sqlalchemy import func
from sqlalchemy.orm import object_session
from app.database.models import Trend, RawNews

BASE_SITE_URL = os.getenv("BASE_SITE_URL", "https://trendiatr.com")

# `trend.news_items` is a plain lazy relationship: touching it loads every row
# in the cluster. The largest cluster on production holds 1567 articles, each
# with a full `content` blob, so a single /trends/<id> call could pull tens of
# megabytes out of Postgres and serialise all of it. Pages are capped instead.
ARTICLES_DEFAULT_LIMIT = 50
ARTICLES_MAX_LIMIT = 200


def clamp_article_limit(value, default=ARTICLES_DEFAULT_LIMIT):
    """Coerce a client-supplied limit into 1..ARTICLES_MAX_LIMIT."""
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, ARTICLES_MAX_LIMIT))


def fetch_articles(trend: Trend, limit: int, offset: int = 0):
    """Return (newest-first page of articles, total article count).

    The count comes from the database, not from the loaded page, so
    `article_count` keeps meaning "how many articles are in this cluster"
    exactly as it did before pagination.
    """
    limit = max(1, min(int(limit), ARTICLES_MAX_LIMIT))
    offset = max(0, int(offset))

    db = object_session(trend)
    if db is None:
        # Detached instance (tests, cached objects): no session to query with.
        items = list(trend.news_items)
        return items[offset:offset + limit], len(items)

    total = (
        db.query(func.count(RawNews.id))
        .filter(RawNews.trend_id == trend.id)
        .scalar()
    ) or 0
    rows = (
        db.query(RawNews)
        .filter(RawNews.trend_id == trend.id)
        .order_by(RawNews.published_at.desc().nullslast(), RawNews.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return rows, total


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


def serialize_trend_full(
    trend: Trend,
    article_limit: int = ARTICLES_DEFAULT_LIMIT,
    article_offset: int = 0,
) -> dict:
    articles, total = fetch_articles(trend, article_limit, article_offset)
    base = serialize_trend_summary(trend)
    base.update({
        "summary": trend.summary,
        "tags": trend.tags or [],
        "entities": trend.entities or [],
        "video_url": _make_absolute(trend.video_path),
        "cluster": {
            # Unchanged meaning: the size of the whole cluster, not of this page.
            "article_count": total,
            "articles": [serialize_article(n) for n in articles],
            "limit": article_limit,
            "offset": article_offset,
            "returned": len(articles),
            "has_more": article_offset + len(articles) < total,
        }
    })
    return base
