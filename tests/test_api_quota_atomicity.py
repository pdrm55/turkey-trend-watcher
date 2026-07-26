"""Pin the B2B quota counter against concurrent requests.

`require_api_key` used to load the APIClient row, compare `calls_used` against
`monthly_limit` in Python, then write `calls_used + 1` back. Two requests
arriving together both read N and both wrote N+1, so billed calls were lost, and
the limit check ran against a value that was already stale by the time it was
enforced — the monthly cap could be walked past with parallel requests.

Check and increment are now one UPDATE whose WHERE clause carries the limit, so
Postgres serialises them on the row.

Needs a live database. Run:
    sudo docker exec ttw_api python3 -m pytest tests/test_api_quota_atomicity.py -v
 or sudo docker exec ttw_api python3 tests/test_api_quota_atomicity.py
"""
import os
import sys
import threading

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pytest
except ImportError:
    class _PytestStub:
        @staticmethod
        def main(_args):
            return _run_standalone()

    pytest = _PytestStub()

from app.core.api_auth import generate_api_key, _SQL_CONSUME_CALL, _SQL_RESET_WINDOW
from app.database.models import SessionLocal, APIClient, utc_now
from datetime import timedelta

CONCURRENCY = 12


def _make_client(limit):
    db = SessionLocal()
    try:
        c = APIClient(
            name="Quota Race Test", email="quota@test.com", api_key=generate_api_key(),
            plan="pro", tps_threshold=0.0, monthly_limit=limit,
            calls_used=0, calls_reset_at=utc_now(), is_active=True,
        )
        db.add(c)
        db.commit()
        return c.id
    finally:
        db.close()


def _drop_client(cid):
    db = SessionLocal()
    try:
        db.query(APIClient).filter_by(id=cid).delete()
        db.commit()
    finally:
        db.close()


def _consume(cid, results, idx):
    """One request's worth of quota accounting, on its own session."""
    db = SessionLocal()
    try:
        row = db.execute(_SQL_CONSUME_CALL, {"id": cid, "now": utc_now()}).first()
        db.commit()
        results[idx] = row[0] if row else None
    finally:
        db.close()


def _run_concurrently(cid, n):
    results = [object()] * n
    threads = [threading.Thread(target=_consume, args=(cid, results, i)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_no_increments_are_lost():
    cid = _make_client(limit=1000)
    try:
        _run_concurrently(cid, CONCURRENCY)
        db = SessionLocal()
        try:
            used = db.query(APIClient).get(cid).calls_used
        finally:
            db.close()
        assert used == CONCURRENCY, f"expected {CONCURRENCY} billed calls, counted {used}"
    finally:
        _drop_client(cid)


def test_every_concurrent_caller_sees_a_distinct_count():
    """A lost update would show up as two callers reporting the same number."""
    cid = _make_client(limit=1000)
    try:
        results = _run_concurrently(cid, CONCURRENCY)
        counts = [r for r in results if isinstance(r, int)]
        assert len(counts) == CONCURRENCY
        assert sorted(counts) == list(range(1, CONCURRENCY + 1))
    finally:
        _drop_client(cid)


def test_limit_cannot_be_exceeded_under_concurrency():
    """The whole point: the cap is enforced inside the statement."""
    limit = 5
    cid = _make_client(limit=limit)
    try:
        results = _run_concurrently(cid, CONCURRENCY)
        allowed = [r for r in results if isinstance(r, int)]
        refused = [r for r in results if r is None]
        assert len(allowed) == limit, f"allowed {len(allowed)} calls past a limit of {limit}"
        assert len(refused) == CONCURRENCY - limit

        db = SessionLocal()
        try:
            assert db.query(APIClient).get(cid).calls_used == limit
        finally:
            db.close()
    finally:
        _drop_client(cid)


def test_zero_or_null_limit_means_unlimited():
    cid = _make_client(limit=0)
    try:
        results = _run_concurrently(cid, CONCURRENCY)
        assert all(isinstance(r, int) for r in results), "limit 0 must not throttle"
    finally:
        _drop_client(cid)


def test_window_reset_is_conditional():
    """A reset that is not due must leave the counter alone."""
    cid = _make_client(limit=1000)
    try:
        _run_concurrently(cid, 3)
        db = SessionLocal()
        try:
            now = utc_now()
            db.execute(_SQL_RESET_WINDOW, {"id": cid, "now": now, "cutoff": now - timedelta(days=30)})
            db.commit()
            assert db.query(APIClient).get(cid).calls_used == 3, "reset fired early"

            # Now age the window past 30 days and confirm it does fire.
            c = db.query(APIClient).get(cid)
            c.calls_reset_at = now - timedelta(days=31)
            db.commit()
            db.execute(_SQL_RESET_WINDOW, {"id": cid, "now": now, "cutoff": now - timedelta(days=30)})
            db.commit()
            db.refresh(c)
            assert c.calls_used == 0, "reset did not fire when due"
        finally:
            db.close()
    finally:
        _drop_client(cid)


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
