"""Pin the /api/trends cache key against an attacker-shaped keyspace.

The key was built by interpolating the raw query string:

    cache_key = f"trends_v2_{category}_{list_type}_{offset}_{limit}_{q}_{date_str}"

Every distinct `?q=` minted another Redis key holding a full response body. This
Redis runs with `maxmemory 0` and `maxmemory-policy noeviction` — verified on the
server — so nothing reclaims those keys under pressure; the keyspace simply grows
until the host runs out of memory. `offset` and `limit` widened it further, and
being passed through bare `int()` they also turned `?offset=abc` into a 500 and
`?limit=1000000` into a million-row query whose result was then cached.

Validation now gates caching rather than functionality: anything outside the
bounded set is still served, just computed fresh.

Run: sudo docker exec ttw_api python3 -m pytest tests/test_listing_cache_key.py -v
  or sudo docker exec ttw_api python3 tests/test_listing_cache_key.py
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pytest
except ImportError:
    class _PytestStub:
        @staticmethod
        def main(_args):
            return _run_standalone()

    pytest = _PytestStub()

from app.api.routes import (
    CACHEABLE_LIMITS,
    MAX_CACHEABLE_OFFSET,
    MAX_LISTING_LIMIT,
    MAX_LISTING_OFFSET,
    _listing_cache_key,
    _safe_int,
)


def _key(category='All', list_type='timeline', offset=0, limit=32, q='', date_str=''):
    return _listing_cache_key(category, list_type, offset, limit, q, date_str)


def test_search_queries_are_never_cached():
    """The attack: one Redis key per distinct search term, none reclaimable."""
    assert _key(q='anything') is None
    assert _key(q='a' * 100) is None


def test_distinct_searches_cannot_mint_distinct_keys():
    keys = {_key(q=f"term-{i}") for i in range(500)}
    assert keys == {None}, "search must contribute no keys at all"


def test_ordinary_browsing_is_still_cached():
    """The frontend requests limit=32 with offsets in steps of 32."""
    assert _key() == "trends_v2_All_timeline_0_32__"
    assert _key(offset=32) is not None
    assert _key(offset=64, category='Siyaset') is not None
    assert _key(list_type='hot') is not None


def test_unknown_category_is_served_but_not_cached():
    """Validation must gate caching, not results — a new category still works."""
    assert _key(category='Kuantum') is None


def test_known_minor_categories_are_cached():
    """The classifier already emits these; they are ordinary traffic."""
    for cat in ('Sağlık', 'Eğitim', 'Bilim', 'Afet'):
        assert _key(category=cat) is not None, cat


def test_unknown_list_type_is_not_cached():
    assert _key(list_type='../../etc') is None


def test_offset_walking_is_bounded():
    """An attacker stepping offset one at a time must not mint a key each time."""
    keys = {_key(offset=i) for i in range(1, 200)}
    assert keys == {None}, "only whole-page offsets may be cached"


def test_deep_offsets_are_not_cached():
    assert _key(offset=MAX_CACHEABLE_OFFSET + 32) is None


def test_odd_limits_are_not_cached():
    assert 33 not in CACHEABLE_LIMITS
    assert _key(limit=33, offset=0) is None


def test_invalid_date_is_not_cached():
    assert _key(date_str='not-a-date') is None
    assert _key(date_str='2026-07-26') is not None


def test_key_keeps_its_prefix_for_invalidation():
    """invalidate_trend_caches scans trends_v2_*; the prefix must not drift."""
    assert _key().startswith("trends_v2_")


def test_safe_int_does_not_raise_on_garbage():
    """`int(request.args.get('offset'))` used to 500 on any non-numeric value."""
    assert _safe_int('abc', default=0, low=0, high=100) == 0
    assert _safe_int(None, default=7, low=0, high=100) == 7
    assert _safe_int('', default=3, low=0, high=100) == 3


def test_safe_int_clamps_runaway_values():
    assert _safe_int('1000000', default=32, low=1, high=MAX_LISTING_LIMIT) == MAX_LISTING_LIMIT
    assert _safe_int('-5', default=0, low=0, high=MAX_LISTING_OFFSET) == 0
    assert _safe_int('99999999', default=0, low=0, high=MAX_LISTING_OFFSET) == MAX_LISTING_OFFSET


def test_safe_int_passes_ordinary_values_through():
    assert _safe_int('64', default=32, low=1, high=MAX_LISTING_LIMIT) == 64


def _run_standalone() -> int:
    failures = []
    ran = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        ran += 1
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failures.append(name)
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            failures.append(name)
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{ran - len(failures)}/{ran} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
