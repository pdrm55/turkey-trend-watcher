"""The B2B trend detail used to serialise every article in a cluster.

`trend.news_items` is a lazy relationship, so `len(trend.news_items)` and the
list comprehension over it both loaded the whole cluster — 1567 rows for the
largest trend on production, each carrying a full `content` blob. Both
/api/v1/trends/<id> and /api/v1/trends/<id>/media now page through the
articles instead.
"""

import inspect
from datetime import datetime, timedelta

from app.core import api_serializer
from app.core.api_serializer import (
    ARTICLES_DEFAULT_LIMIT,
    ARTICLES_MAX_LIMIT,
    clamp_article_limit,
    fetch_articles,
    serialize_trend_full,
)
from app.database.models import RawNews, Trend


def _detached_trend(article_count):
    """A Trend with no session, so fetch_articles takes the in-memory path."""
    trend = Trend(id=1, title="deprem", slug="1-deprem")
    base = datetime(2026, 7, 27, 12, 0, 0)
    trend.news_items = [
        RawNews(
            id=i,
            content=f"article {i}",
            source_name="AA",
            source_type="rss",
            source_tier=1,
            published_at=base - timedelta(minutes=i),
        )
        for i in range(article_count)
    ]
    # serialize_trend_summary reads these; they are DB-side defaults.
    for attr, value in (
        ("final_tps", 0.0), ("tps_signal", 0.0), ("tps_confidence", 0.0),
        ("message_count", article_count), ("category", "gundem"),
        ("trajectory", "steady"), ("first_seen", base), ("last_updated", base),
        ("cover_image", None), ("video_path", None), ("summary", None),
        ("tags", None), ("entities", None),
    ):
        setattr(trend, attr, value)
    return trend


def test_clamp_rejects_oversized_limits():
    assert clamp_article_limit(10_000) == ARTICLES_MAX_LIMIT
    assert clamp_article_limit(ARTICLES_MAX_LIMIT + 1) == ARTICLES_MAX_LIMIT


def test_clamp_rejects_zero_and_negative_limits():
    assert clamp_article_limit(0) == 1
    assert clamp_article_limit(-5) == 1


def test_clamp_falls_back_on_garbage_instead_of_raising_500():
    assert clamp_article_limit("abc") == ARTICLES_DEFAULT_LIMIT
    assert clamp_article_limit(None) == ARTICLES_DEFAULT_LIMIT


def test_clamp_passes_through_reasonable_values():
    assert clamp_article_limit("25") == 25
    assert clamp_article_limit(ARTICLES_MAX_LIMIT) == ARTICLES_MAX_LIMIT


def test_fetch_articles_caps_a_large_cluster():
    trend = _detached_trend(1567)
    page, total = fetch_articles(trend, ARTICLES_DEFAULT_LIMIT)
    assert len(page) == ARTICLES_DEFAULT_LIMIT
    assert total == 1567, "the total must stay the whole cluster, not the page"


def test_fetch_articles_offset_walks_the_cluster():
    trend = _detached_trend(10)
    first, _ = fetch_articles(trend, 4, 0)
    second, _ = fetch_articles(trend, 4, 4)
    assert [n.id for n in first] == [0, 1, 2, 3]
    assert [n.id for n in second] == [4, 5, 6, 7]


def test_article_count_still_means_the_whole_cluster():
    """Existing clients read cluster.article_count; its meaning must not shift."""
    payload = serialize_trend_full(_detached_trend(300))
    assert payload["cluster"]["article_count"] == 300
    assert payload["cluster"]["returned"] == ARTICLES_DEFAULT_LIMIT
    assert payload["cluster"]["has_more"] is True


def test_has_more_is_false_once_the_cluster_fits_in_one_page():
    payload = serialize_trend_full(_detached_trend(3))
    assert payload["cluster"]["returned"] == 3
    assert payload["cluster"]["has_more"] is False


def test_full_serialisation_still_carries_the_summary_fields():
    """Pagination must not drop anything else from the response contract."""
    payload = serialize_trend_full(_detached_trend(2))
    for key in ("id", "title", "tps_score", "article_count", "url", "summary",
                "tags", "entities", "cluster"):
        assert key in payload


def test_no_endpoint_iterates_the_unbounded_relationship():
    from app.api import api_v1

    for fn in (api_v1.get_trend, api_v1.get_trend_media):
        src = inspect.getsource(fn)
        assert "trend.news_items" not in src, \
            f"{fn.__name__} must page via fetch_articles, not the relationship"

    src = inspect.getsource(api_serializer.serialize_trend_full)
    assert "len(trend.news_items)" not in src


def test_db_backed_path_orders_newest_first_and_counts_separately():
    src = inspect.getsource(fetch_articles)
    assert "published_at.desc()" in src
    assert "func.count(" in src, "the total must be a COUNT, not len() of the page"
    assert ".limit(limit)" in src


def _run_standalone():
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            passed += 1
            print(f"  PASS {name}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL {name}: {exc}")
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
