"""Pin the atomicity guarantees of ScoringQueue.

The queue used to do `sadd` then `lpush` as two round-trips. A crash or Redis
error in the gap left the id in the pending set with nothing in any lane, and
because `enqueue` returns early on `sadd == 0`, that trend could never be queued
again. The orphans also inflated `scard(pending)` — the backpressure counter —
so once the drift reached `max_size` every normal-lane job was dropped silently
and permanently. `pop` had the mirror-image window between `rpop` and `srem`.

Both paths are now single Lua scripts, which Redis executes atomically.

Needs a live Redis, so it runs inside a container against a test keyspace —
the production keys are never touched. Run:
    sudo docker exec ttw_gravity python3 -m pytest tests/test_scoring_queue_atomicity.py -v
 or sudo docker exec ttw_gravity python3 tests/test_scoring_queue_atomicity.py
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

        @staticmethod
        def skip(reason):
            raise RuntimeError(f"SKIP: {reason}")

    pytest = _PytestStub()

from app.core.scoring_queue import ScoringQueue

_TEST_NS = "test:scoring_queue_atomicity"


def _queue():
    """A ScoringQueue pointed at a scratch keyspace, emptied before each use."""
    q = ScoringQueue()
    if not q.enabled:
        raise RuntimeError("Redis unavailable — run this inside a container")
    q._q_breaking = f"{_TEST_NS}:breaking"
    q._q_normal = f"{_TEST_NS}:normal"
    q._pending = f"{_TEST_NS}:pending"
    q._retries = f"{_TEST_NS}:retries"
    q._redis.delete(q._q_breaking, q._q_normal, q._pending, q._retries)
    return q


def _state(q):
    return (
        q._redis.scard(q._pending),
        q._redis.llen(q._q_breaking),
        q._redis.llen(q._q_normal),
    )


def test_enqueue_is_deduped_without_orphaning():
    q = _queue()
    assert q.enqueue(101) is True
    assert q.enqueue(101) is True, "duplicate enqueue still reports success"
    assert _state(q) == (1, 0, 1), "second enqueue must not push a second copy"


def test_pending_never_outlives_the_lane_entry():
    """The invariant the old two-step code could violate."""
    q = _queue()
    q.enqueue(202)
    assert _state(q) == (1, 0, 1)
    q.pop()
    assert _state(q) == (0, 0, 0), "pop must clear the pending set with the lane"


def test_requeue_possible_after_pop():
    """The symptom of an orphan: a trend that can never be queued again."""
    q = _queue()
    q.enqueue(303)
    q.pop()
    q.enqueue(303)
    assert _state(q) == (1, 0, 1), "trend must be queueable again after being popped"


def test_breaking_lane_pops_first():
    q = _queue()
    q.enqueue(1, ScoringQueue.NORMAL)
    q.enqueue(2, ScoringQueue.BREAKING)
    tid, lane = q.pop()
    assert (tid, lane) == (2, ScoringQueue.BREAKING)
    tid, lane = q.pop()
    assert (tid, lane) == (1, ScoringQueue.NORMAL)


def test_pop_on_empty_queue():
    q = _queue()
    assert q.pop() is None


def test_pop_returns_int_id():
    q = _queue()
    q.enqueue(404)
    tid, _ = q.pop()
    assert isinstance(tid, int) and tid == 404


def test_retry_restores_both_structures():
    q = _queue()
    q.enqueue(505)
    q.pop()
    assert q.retry_or_drop(505) is True
    assert _state(q) == (1, 0, 1), "retry must restore pending and lane together"


def test_retry_gives_up_after_max_retries():
    q = _queue()
    for _ in range(q.max_retries):
        assert q.retry_or_drop(606) is True
        q.pop()
    assert q.retry_or_drop(606) is False, "must drop once retries are exhausted"


def test_size_tracks_pending():
    q = _queue()
    assert q.size() == 0
    q.enqueue(707)
    q.enqueue(808)
    assert q.size() == 2
    q.pop()
    assert q.size() == 1


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

    q = ScoringQueue()
    if q.enabled:
        q._redis.delete(
            f"{_TEST_NS}:breaking", f"{_TEST_NS}:normal",
            f"{_TEST_NS}:pending", f"{_TEST_NS}:retries",
        )
    print(f"\n{ran - len(failures)}/{ran} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
