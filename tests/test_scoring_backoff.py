"""Backoff for trends that repeatedly fail to score.

Production symptom this covers: 19 trends sat with needs_scoring = true for up
to seven hours. The DB fallback scan re-read them every five-second cycle, they
filled the batch ahead of newly arrived trends, and each attempt logged a
deferral — 78 per minute at peak. The scan had neither ordering nor backoff, so
nothing could ever push those trends out of the way.
"""

import time

from app.core.scoring_queue import ScoringQueue


class FakeRedis:
    """Minimal GET/SET/DEL with TTL bookkeeping, enough for the backoff paths."""

    def __init__(self):
        self.store = {}
        self.ttls = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def delete(self, key):
        self.store.pop(key, None)
        self.ttls.pop(key, None)


def _queue():
    q = ScoringQueue.__new__(ScoringQueue)
    q._redis = FakeRedis()
    q.max_retries = 2
    return q


def test_first_failure_starts_the_shortest_backoff():
    q = _queue()
    assert q.note_deferral(1) == ScoringQueue._BACKOFF_STEPS[0]
    assert q.in_backoff(1) is True


def test_backoff_escalates_and_then_caps():
    q = _queue()
    seen = [q.note_deferral(7) for _ in range(8)]
    assert seen[: len(ScoringQueue._BACKOFF_STEPS)] == list(ScoringQueue._BACKOFF_STEPS)
    # A trend that keeps failing must not escalate past the last step, otherwise
    # it would eventually stop being retried at all.
    assert set(seen[len(ScoringQueue._BACKOFF_STEPS) :]) == {ScoringQueue._BACKOFF_STEPS[-1]}


def test_expired_window_lets_the_trend_through_again():
    q = _queue()
    q.note_deferral(2)
    # Rewrite ready_at into the past rather than sleeping 60 real seconds.
    attempts = q._redis.get(q._backoff_key(2)).split(":", 1)[0]
    q._redis.set(q._backoff_key(2), f"{attempts}:{time.time() - 1}")
    assert q.in_backoff(2) is False


def test_escalation_survives_the_wait():
    """The key outlives the delay it encodes, so attempt count is not reset."""
    q = _queue()
    q.note_deferral(3)
    attempts = q._redis.get(q._backoff_key(3)).split(":", 1)[0]
    q._redis.set(q._backoff_key(3), f"{attempts}:{time.time() - 1}")
    assert q.note_deferral(3) == ScoringQueue._BACKOFF_STEPS[1]


def test_key_ttl_exceeds_the_longest_delay():
    assert ScoringQueue._BACKOFF_KEY_TTL > ScoringQueue._BACKOFF_STEPS[-1]


def test_success_clears_the_backoff():
    q = _queue()
    q.note_deferral(4)
    q.clear_backoff(4)
    assert q.in_backoff(4) is False
    assert q.note_deferral(4) == ScoringQueue._BACKOFF_STEPS[0]


def test_untouched_trend_is_never_in_backoff():
    assert _queue().in_backoff(999) is False


def test_unreadable_state_fails_open():
    """A backoff read must never be what stops a trend from being scored."""
    q = _queue()
    q._redis.set(q._backoff_key(5), "garbage-without-a-colon")
    assert q.in_backoff(5) is False


def test_no_redis_does_not_block_scoring():
    q = ScoringQueue.__new__(ScoringQueue)
    q._redis = None
    assert q.in_backoff(6) is False
    assert q.note_deferral(6) == ScoringQueue._BACKOFF_STEPS[0]
    q.clear_backoff(6)


def test_scoring_llm_budget_exceeds_measured_latency():
    """Guards the actual root cause: the budget was below real p90 latency.

    Measured on the production box while saturated: median 6.5s, p90 13.4s,
    max 25.0s. The old 12s budget timed out on the slowest ~15% of calls, and
    three consecutive timeouts opened the shared circuit breaker for 120s.
    """
    import inspect
    from app.core import scoring

    src = inspect.getsource(scoring.TPSCalculator.analyze_semantic_and_entity)
    assert "timeout=12" not in src, "scoring budget must clear measured p90 (13.4s)"
    assert "timeout=25" in src


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
