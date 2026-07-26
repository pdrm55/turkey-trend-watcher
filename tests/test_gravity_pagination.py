"""Pin the keyset pagination in gravity_worker's decay cycle.

The decay loop pages over `is_active == True` while setting `is_active = False`
inside the same loop and committing before advancing. With LIMIT/OFFSET every
archived row shifted the remaining rows down, so the next page started past
trends that had never been looked at — they silently skipped that decay cycle.
A cycle archiving 30 of a 100-row page skipped the next 30 active trends.

These tests model both paging strategies against an in-memory table so the
failure is reproducible without a database. Nothing here imports the worker;
the point is to pin the algorithm the worker now uses, and to keep a runnable
demonstration of why OFFSET was wrong.

Run: python3 -m pytest tests/test_gravity_pagination.py -v
  or python3 tests/test_gravity_pagination.py
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pytest
except ImportError:
    class _ParamMark:
        @staticmethod
        def parametrize(argname, values):
            def deco(fn):
                fn._params = (argname, values)
                return fn
            return deco

    class _PytestStub:
        mark = _ParamMark()

        @staticmethod
        def main(_args):
            return _run_standalone()

    pytest = _PytestStub()

BATCH = 10


class _Trend:
    def __init__(self, tid, archive):
        self.id = tid
        self.is_active = True
        self.archive = archive  # would this trend be archived when visited?


def _rows(n, archive_every):
    return [_Trend(i, archive=(i % archive_every == 0)) for i in range(1, n + 1)]


def _sweep_offset(rows):
    """The old strategy: LIMIT/OFFSET over a predicate the loop mutates."""
    visited = []
    offset = 0
    while True:
        page = [r for r in rows if r.is_active][offset:offset + BATCH]
        if not page:
            break
        for r in page:
            visited.append(r.id)
            if r.archive:
                r.is_active = False
        if len(page) < BATCH:
            break
        offset += BATCH
    return visited


def _sweep_keyset(rows):
    """The current strategy: seek on id, immune to rows leaving the predicate."""
    visited = []
    last_id = 0
    while True:
        page = [r for r in rows if r.is_active and r.id > last_id][:BATCH]
        if not page:
            break
        for r in page:
            visited.append(r.id)
            if r.archive:
                r.is_active = False
        last_id = page[-1].id
        if len(page) < BATCH:
            break
    return visited


def test_keyset_visits_every_trend():
    rows = _rows(100, archive_every=3)
    visited = _sweep_keyset(rows)
    assert sorted(visited) == list(range(1, 101))
    assert len(visited) == len(set(visited)), "no trend may be visited twice"


def test_offset_strategy_skips_trends():
    """Demonstrates the bug the keyset version fixes."""
    rows = _rows(100, archive_every=3)
    visited = _sweep_offset(rows)
    missed = set(range(1, 101)) - set(visited)
    assert missed, "expected the offset sweep to skip trends"
    assert len(visited) < 100


def test_keyset_beats_offset_on_the_same_input():
    a, b = _rows(100, archive_every=3), _rows(100, archive_every=3)
    assert len(_sweep_keyset(a)) > len(_sweep_offset(b))


@pytest.mark.parametrize("archive_every", [1, 2, 3, 5, 7, 1000])
def test_keyset_is_complete_at_any_archive_rate(archive_every):
    rows = _rows(97, archive_every=archive_every)
    assert sorted(_sweep_keyset(rows)) == list(range(1, 98))


def test_keyset_handles_archive_everything():
    """Worst case for OFFSET: every visited row leaves the predicate."""
    rows = _rows(50, archive_every=1)
    assert sorted(_sweep_keyset(rows)) == list(range(1, 51))


def test_keyset_terminates_on_empty_input():
    assert _sweep_keyset([]) == []


def _run_standalone() -> int:
    failures = []
    ran = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        argname, values = getattr(fn, "_params", (None, [None]))
        for value in values:
            ran += 1
            label = f"{name}[{value}]" if argname else name
            try:
                fn(value) if argname else fn()
                print(f"PASS  {label}")
            except AssertionError as e:
                failures.append(label)
                print(f"FAIL  {label}: {e}")

    print(f"\n{ran - len(failures)}/{ran} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
