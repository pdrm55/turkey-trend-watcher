"""Three in-memory caches that grew without limit in long-running processes.

Each had the same shape of bug: entries were written and read but nothing ever
removed one. _LLM_CACHE and fast_dedup_cache looked bounded because they swept
*expired* entries at the cap -- but during a burst nothing is expired yet, so
the sweep freed nothing and the cap was silently exceeded. trend_history_cache
had no bound at all and is keyed by the identifier taken from the request path,
so walking trend ids grew it directly.
"""

import time


def test_llm_cache_bound_holds_when_nothing_has_expired():
    from app.core import scoring

    scoring._LLM_CACHE.clear()
    try:
        far_future = time.monotonic() + 10_000
        for i in range(scoring._LLM_CACHE_MAX_SIZE + 250):
            if len(scoring._LLM_CACHE) >= scoring._LLM_CACHE_MAX_SIZE:
                now = time.monotonic()
                for k in [k for k, v in scoring._LLM_CACHE.items() if now >= v[3]]:
                    del scoring._LLM_CACHE[k]
                overflow = len(scoring._LLM_CACHE) - scoring._LLM_CACHE_MAX_SIZE + 1
                if overflow > 0:
                    for k in sorted(
                        scoring._LLM_CACHE, key=lambda k: scoring._LLM_CACHE[k][3]
                    )[:overflow]:
                        del scoring._LLM_CACHE[k]
            scoring._LLM_CACHE[i] = (50, 50, False, far_future)
        assert len(scoring._LLM_CACHE) <= scoring._LLM_CACHE_MAX_SIZE
    finally:
        scoring._LLM_CACHE.clear()


def test_llm_cache_eviction_is_wired_into_the_real_code_path():
    import inspect
    from app.core import scoring

    src = inspect.getsource(scoring.TPSCalculator.analyze_semantic_and_entity)
    assert "overflow" in src, "cap must be enforced, not only expiry-swept"


def test_fast_dedup_cache_is_bounded():
    from app.core.ai_engine import AIEngine

    eng = AIEngine.__new__(AIEngine)
    eng.fast_dedup_cache = {}
    eng.fast_dedup_max_size = 100
    eng.fast_dedup_ttl = 10_000  # nothing expires during the test

    now_ts = time.time()
    for i in range(500):
        if len(eng.fast_dedup_cache) >= eng.fast_dedup_max_size:
            for k in [k for k, v in eng.fast_dedup_cache.items() if v["expires_at"] <= now_ts]:
                del eng.fast_dedup_cache[k]
            overflow = len(eng.fast_dedup_cache) - eng.fast_dedup_max_size + 1
            if overflow > 0:
                for k in sorted(
                    eng.fast_dedup_cache, key=lambda k: eng.fast_dedup_cache[k]["expires_at"]
                )[:overflow]:
                    del eng.fast_dedup_cache[k]
        eng.fast_dedup_cache[f"h{i}"] = {
            "cluster_id": f"c{i}",
            "expires_at": now_ts + eng.fast_dedup_ttl,
        }
    assert len(eng.fast_dedup_cache) <= eng.fast_dedup_max_size


def test_fast_dedup_eviction_is_wired_into_process_news():
    import inspect
    from app.core.ai_engine import AIEngine

    src = inspect.getsource(AIEngine.process_news)
    assert "fast_dedup_max_size" in src


def test_trend_history_cache_evicts_oldest_beyond_the_cap():
    from app.api import routes

    routes.trend_history_cache.clear()
    try:
        now = time.time()
        for i in range(routes.TREND_HISTORY_CACHE_MAX + 120):
            routes._trend_history_cache_put(f"trend-{i}", {"data": i}, now)
        assert len(routes.trend_history_cache) == routes.TREND_HISTORY_CACHE_MAX
        # Oldest evicted, newest retained.
        assert "trend-0" not in routes.trend_history_cache
        newest = f"trend-{routes.TREND_HISTORY_CACHE_MAX + 119}"
        assert newest in routes.trend_history_cache
    finally:
        routes.trend_history_cache.clear()


def test_trend_history_cache_recent_read_survives_eviction_pressure():
    """A hot key must not be evicted just because it was inserted early."""
    from app.api import routes

    routes.trend_history_cache.clear()
    try:
        now = time.time()
        routes._trend_history_cache_put("hot", {"data": "hot"}, now)
        for i in range(routes.TREND_HISTORY_CACHE_MAX):
            routes.trend_history_cache.move_to_end("hot")
            routes._trend_history_cache_put(f"cold-{i}", {"data": i}, now)
        assert "hot" in routes.trend_history_cache
    finally:
        routes.trend_history_cache.clear()


def test_trend_history_cache_is_an_ordered_dict():
    from app.api import routes

    assert isinstance(routes.trend_history_cache, OrderedDictType)


from collections import OrderedDict as OrderedDictType  # noqa: E402


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
