"""_do_merge must not leave ChromaDB and Postgres disagreeing.

ChromaDB is not part of the Postgres transaction, so a merge touches two stores
that can fail independently:

  * ChromaDB move fails, Postgres commits  -> trends merged in Postgres while the
    vectors stay under the dead source cluster.
  * ChromaDB move succeeds, Postgres fails -> source trend stays active with all
    its rows but none of its vectors, so it is invisible to later merge cycles
    and new matching news clusters to the target instead.

merge_clusters used to swallow its own exception and return None either way, so
_do_merge could not tell the two apart and committed regardless.
"""

import sys
import types


class FakeCollection:
    def __init__(self, vectors, fail_update_on=None):
        # vectors: {vector_id: metadata}
        self.vectors = vectors
        self.fail_update_on = fail_update_on or set()
        self.update_calls = 0

    def get(self, where=None, include=None):
        cid = (where or {}).get("cluster_id")
        ids, metas = [], []
        for vid, meta in self.vectors.items():
            if cid is None or meta.get("cluster_id") == cid:
                ids.append(vid)
                metas.append(dict(meta))
        return {"ids": ids, "metadatas": metas}

    def update(self, ids=None, metadatas=None):
        self.update_calls += 1
        if self.update_calls in self.fail_update_on:
            raise RuntimeError("chroma unavailable")
        for vid, meta in zip(ids, metadatas):
            self.vectors[vid] = dict(meta)


def _engine(collection):
    """Build an AIEngine shell with only what the two methods touch."""
    from app.core.ai_engine import AIEngine

    eng = AIEngine.__new__(AIEngine)
    eng.collection = collection
    return eng


def _vectors():
    return {
        "v1": {"cluster_id": "SRC", "is_reference": True},
        "v2": {"cluster_id": "SRC", "is_reference": False},
        "v3": {"cluster_id": "OTHER", "is_reference": True},
    }


def test_successful_move_returns_original_metadata():
    col = FakeCollection(_vectors())
    original = _engine(col).merge_clusters("SRC", "DST")
    assert original is not None
    assert {vid for vid, _ in original} == {"v1", "v2"}
    # Captured state must be the pre-move state, not the post-move state.
    assert dict(original)["v1"]["cluster_id"] == "SRC"
    assert dict(original)["v1"]["is_reference"] is True
    assert col.vectors["v1"]["cluster_id"] == "DST"
    assert col.vectors["v3"]["cluster_id"] == "OTHER", "untouched clusters must not move"


def test_failed_move_returns_none_not_empty():
    """None means failure; [] means nothing to move. The caller acts differently."""
    col = FakeCollection(_vectors(), fail_update_on={1})
    assert _engine(col).merge_clusters("SRC", "DST") is None


def test_empty_source_is_success_not_failure():
    col = FakeCollection(_vectors())
    assert _engine(col).merge_clusters("NO_SUCH_CLUSTER", "DST") == []


def test_restore_puts_metadata_back_exactly():
    col = FakeCollection(_vectors())
    eng = _engine(col)
    original = eng.merge_clusters("SRC", "DST")
    assert col.vectors["v1"]["cluster_id"] == "DST"
    assert col.vectors["v1"]["is_reference"] is False, "move demotes references"

    assert eng.restore_vector_metadata(original) is True
    assert col.vectors["v1"]["cluster_id"] == "SRC"
    assert col.vectors["v1"]["is_reference"] is True, "rollback must restore the flag"
    assert col.vectors["v2"]["cluster_id"] == "SRC"


def test_restore_reports_failure_rather_than_hiding_it():
    col = FakeCollection(_vectors(), fail_update_on={2})
    eng = _engine(col)
    original = eng.merge_clusters("SRC", "DST")
    assert eng.restore_vector_metadata(original) is False


def test_restore_of_nothing_is_a_no_op():
    col = FakeCollection(_vectors())
    assert _engine(col).restore_vector_metadata([]) is True
    assert col.update_calls == 0


def test_do_merge_aborts_before_touching_postgres_when_chroma_fails():
    """The whole point: no Postgres write may happen after a failed move."""
    import inspect
    from app.workers import merge_worker

    src = inspect.getsource(merge_worker._do_merge)
    abort = src.index("moved_vectors is None")
    first_db_write = src.index("db.query(RawNews)")
    assert abort < first_db_write, "abort must come before any Postgres mutation"
    assert "return False" in src[abort:first_db_write]


def test_do_merge_compensates_chroma_when_postgres_rolls_back():
    import inspect
    from app.workers import merge_worker

    src = inspect.getsource(merge_worker._do_merge)
    rollback = src.index("db.rollback()")
    assert "restore_vector_metadata(moved_vectors)" in src[rollback:]


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
