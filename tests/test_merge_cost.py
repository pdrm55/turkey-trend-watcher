"""The merge cycle used to pay for the same answers every hour.

It ran from scratch on a 60-minute timer with no memory: a pair of clusters
ruled "different event" at 10:00 was sent to Gemini again at 11:00, and every
hour after that for as long as both trends stayed active. The same cycle also
re-encoded the reference document of every active trend on CPU — 830 of them on
the last run — although those documents rarely change.
"""

import inspect

from app.workers import merge_worker as mw


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.setex_calls = 0

    def ping(self):
        return True

    def get(self, k):
        return self.store.get(k)

    def setex(self, k, ttl, v):
        self.setex_calls += 1
        self.store[k] = v
        return True


def _with_fake_redis(monkeypatch=None):
    fake = FakeRedis()
    mw._cache_conn._client = fake
    return fake


# --- verdict key -------------------------------------------------------------

def test_verdict_key_is_order_independent():
    """(A,B) and (B,A) are the same pair and must not be judged twice."""
    a = mw._verdict_key("cluster-a", "cluster-b", 3, 40)
    b = mw._verdict_key("cluster-b", "cluster-a", 40, 3)
    assert a == b


def test_verdict_key_survives_one_more_article():
    """Otherwise every new article would repay for the same verdict."""
    before = mw._verdict_key("a", "b", 3, 10)
    after = mw._verdict_key("a", "b", 4, 10)
    assert before == after


def test_verdict_key_changes_once_a_cluster_really_grows():
    """An umbrella story only recognisable later must get a second look."""
    small = mw._verdict_key("a", "b", 3, 10)
    grown = mw._verdict_key("a", "b", 3, 40)
    assert small != grown


# --- cache behaviour ---------------------------------------------------------

def test_a_rejected_pair_is_remembered():
    fake = _with_fake_redis()
    key = mw._verdict_key("a", "b", 1, 1)
    assert mw._cached_not_same(key) is False
    mw._remember_not_same(key)
    assert mw._cached_not_same(key) is True


def test_cache_miss_when_redis_is_down():
    """Failing open costs money; failing closed would suppress real merges."""
    mw._cache_conn._client = None
    mw._cache_conn._next_attempt = float("inf")
    assert mw._cached_not_same("merge:verdict:whatever") is False


def test_embedding_is_encoded_once_then_served_from_cache():
    fake = _with_fake_redis()
    calls = []

    original = mw.ai_engine.get_embedding
    mw.ai_engine.get_embedding = lambda text, is_query=False: (calls.append(text) or [0.1, 0.2])
    try:
        first, cached_first = mw._embed_ref_doc("Ankara'da deprem oldu")
        second, cached_second = mw._embed_ref_doc("Ankara'da deprem oldu")
    finally:
        mw.ai_engine.get_embedding = original

    assert len(calls) == 1, "the second cycle must not re-encode the same document"
    assert cached_first is False and cached_second is True
    assert first == second


def test_a_changed_reference_document_is_re_encoded():
    fake = _with_fake_redis()
    calls = []
    original = mw.ai_engine.get_embedding
    mw.ai_engine.get_embedding = lambda text, is_query=False: (calls.append(text) or [0.3])
    try:
        mw._embed_ref_doc("first version")
        mw._embed_ref_doc("second version")
    finally:
        mw.ai_engine.get_embedding = original
    assert len(calls) == 2


def test_embedding_cache_keeps_the_query_side_vector():
    """The 0.12/0.40 thresholds are calibrated on query-vs-passage distances."""
    src = inspect.getsource(mw._embed_ref_doc)
    assert "is_query=True" in src


# --- API failures must not be cached ----------------------------------------

def test_api_failure_returns_none_not_false():
    """False means "different event" and gets cached for a week; an outage must not."""
    src = inspect.getsource(mw.verify_same_event)
    tail = src[src.index("except Exception"):]
    assert "return None" in tail
    assert "return False" not in tail


def test_only_a_real_negative_verdict_is_written():
    src = inspect.getsource(mw.run_merge_cycle)
    assert "if same is None:" in src
    none_branch = src[src.index("if same is None:"):src.index("if not same:")]
    assert "_remember_not_same" not in none_branch, \
        "an unanswerable pair must leave no verdict behind"


# --- silent truncation -------------------------------------------------------

def test_budget_exhaustion_is_reported_not_silent():
    src = inspect.getsource(mw.run_merge_cycle)
    assert "budget_stopped_at" in src
    assert "unexamined" in src


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
