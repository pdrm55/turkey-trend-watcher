"""Query-shape fixes: N+1 loops, unbounded scans, and wildcard injection."""

import inspect


def test_comment_votes_are_fetched_in_one_query():
    """The loop issued one lookup per comment, up to 50 per request."""
    from app.api import routes

    src = inspect.getsource(routes.get_comments)
    assert "CommentVote.comment_id.in_(" in src
    body = src[src.index("for c in comments:"):]
    assert "db.query(CommentVote)" not in body, "no per-comment query may remain"


def test_stats_endpoint_is_cached():
    """COUNT(*) over 494k raw_news rows ran on every header render (~172ms)."""
    from app.api import routes

    src = inspect.getsource(routes.get_stats)
    assert "STATS_CACHE_KEY" in src
    assert routes.STATS_CACHE_TTL > 0


def test_like_pattern_escapes_user_wildcards():
    from app.api.routes import _like_pattern

    # A bare "%" used to match every row in the table.
    assert _like_pattern("%") == "%\\%%"
    assert _like_pattern("_") == "%\\_%"
    assert _like_pattern("a%b_c") == "%a\\%b\\_c%"
    # A backslash must be escaped first, or it would escape our own escapes.
    assert _like_pattern("a\\b") == "%a\\\\b%"


def test_like_pattern_leaves_ordinary_search_terms_alone():
    from app.api.routes import _like_pattern

    assert _like_pattern("deprem") == "%deprem%"
    assert _like_pattern("Ankara 2026") == "%Ankara 2026%"


def test_admin_listing_uses_the_escaped_pattern_and_bounded_ints():
    from app.api import routes

    src = inspect.getsource(routes.admin_get_trends)
    assert "_like_pattern(q)" in src
    assert "escape=" in src
    assert "int(request.args" not in src, "unbounded int() raised a 500 on bad input"
    assert "_safe_int(" in src


def test_gc_video_cleanup_is_batched_and_not_n_plus_one():
    from app.workers import gravity_worker

    src = inspect.getsource(gravity_worker.cleanup_inactive_media)
    assert "GC_VIDEO_BATCH" in src
    loop = src[src.index("for trend in inactive_video_trends:"):]
    assert "db.query(RawNews).filter(RawNews.trend_id == trend.id).all()" not in loop
    assert "tuple_(" in src, "the per-trend update must be one bulk statement"


def test_scoring_does_not_load_content_for_every_row():
    """`content` is a large text column; only three rows can ever be the ref doc."""
    from app.core import scoring

    src = inspect.getsource(scoring.TPSCalculator.run_tps_cycle)
    aggregate_query = src[src.index("news_items"):src.index("has_editorial")]
    assert "RawNews.content" not in aggregate_query
    assert "RawNews.source_type" in aggregate_query
    # The columns get_confidence_score reads must still be selected.
    for column in ("source_tier", "source_name", "source_type"):
        assert f"RawNews.{column}" in aggregate_query


def test_scoring_ref_doc_fallback_still_takes_the_oldest_with_content():
    """The old code walked reversed(news_items), i.e. oldest first."""
    from app.core import scoring

    src = inspect.getsource(scoring.TPSCalculator.run_tps_cycle)
    assert "published_at.asc()" in src
    assert 'RawNews.content != ""' in src


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
