"""Pin migration-step isolation in init_db.

All the ALTER TABLEs used to share one connection and one commit. In Postgres a
failing DDL aborts the surrounding transaction, so one bad statement did not just
fail itself — every later step in the block was skipped and the whole batch
rolled back. init_db caught the exception, printed it, and returned normally, so
web_server logged "Database schemas verified and synchronized" and the app served
a half-migrated schema.

Each step now runs in its own transaction, failures are collected rather than
raised mid-way, and init_db raises at the end if anything did not land.

Needs a live database. Run:
    sudo docker exec ttw_api python3 -m pytest tests/test_migration_isolation.py -v
 or sudo docker exec ttw_api python3 tests/test_migration_isolation.py
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

from sqlalchemy import text
from app.database.models import _Migrations, engine

_TMP = "ttw_migration_isolation_probe"


def _drop_probe():
    with engine.connect() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {_TMP}"))
        conn.commit()


def _columns():
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = :t"
        ), {"t": _TMP}).fetchall()
    return {r[0] for r in rows}


def setup_function(_=None):
    _drop_probe()
    with engine.connect() as conn:
        conn.execute(text(f"CREATE TABLE {_TMP} (id SERIAL PRIMARY KEY)"))
        conn.commit()


def teardown_function(_=None):
    _drop_probe()


def test_a_failing_step_does_not_block_later_steps():
    """The actual bug: one bad DDL used to take every following one with it."""
    m = _Migrations(engine)
    m.run("good_before", f"ALTER TABLE {_TMP} ADD COLUMN before_col INTEGER")
    m.run("broken", f"ALTER TABLE {_TMP} ADD COLUMN bad_col NOSUCHTYPE")
    m.run("good_after", f"ALTER TABLE {_TMP} ADD COLUMN after_col INTEGER")

    cols = _columns()
    assert "before_col" in cols, "step before the failure must survive"
    assert "after_col" in cols, "step after the failure must still run"
    assert "bad_col" not in cols
    assert [label for label, _ in m.failures] == ["broken"]


def test_failure_detail_is_recorded():
    m = _Migrations(engine)
    m.run("broken", f"ALTER TABLE {_TMP} ADD COLUMN bad_col NOSUCHTYPE")
    assert len(m.failures) == 1
    label, detail = m.failures[0]
    assert label == "broken"
    assert detail, "the driver message must be kept, not discarded"


def test_multi_statement_step_is_all_or_nothing():
    """A step is one transaction: a later statement failing rolls back the earlier."""
    m = _Migrations(engine)
    m.run(
        "atomic_step",
        f"ALTER TABLE {_TMP} ADD COLUMN part_one INTEGER",
        f"ALTER TABLE {_TMP} ADD COLUMN part_two NOSUCHTYPE",
    )
    cols = _columns()
    assert "part_one" not in cols, "the whole step must roll back together"
    assert len(m.failures) == 1


def test_successful_step_commits():
    m = _Migrations(engine)
    assert m.run("ok", f"ALTER TABLE {_TMP} ADD COLUMN ok_col INTEGER") is True
    assert "ok_col" in _columns()
    assert m.failures == []


def test_run_returns_false_on_failure():
    m = _Migrations(engine)
    assert m.run("bad", f"ALTER TABLE {_TMP} ADD COLUMN x NOSUCHTYPE") is False


def _run_standalone() -> int:
    failures = []
    ran = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        ran += 1
        setup_function()
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failures.append(name)
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            failures.append(name)
            print(f"ERROR {name}: {type(e).__name__}: {e}")
        finally:
            teardown_function()
    print(f"\n{ran - len(failures)}/{ran} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
