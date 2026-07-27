"""The retroactive merge search must start where ingest-time merging stops.

merge_worker searched from 0.16 up, on the assumption that ai_engine had already
merged everything below. ai_engine merges below 0.15, and below 0.12 when the
reference document is older than 24h — so 0.12-0.16 belonged to neither: ingest
would not merge it and the merge worker would not look at it.
"""

import inspect
import re


def test_search_starts_at_the_ingest_floor():
    from app.workers import merge_worker

    assert merge_worker.SEARCH_DISTANCE_MIN == 0.12


def test_no_gap_between_ingest_merging_and_retroactive_search():
    """Guards the actual invariant, not just the constant."""
    from app.core import ai_engine
    from app.workers import merge_worker

    src = inspect.getsource(ai_engine.AIEngine.process_news)
    thresholds = {
        float(m) for m in re.findall(r"auto_merge_thresh = ([0-9.]+)", src)
    }
    assert thresholds, "could not find ai_engine's auto-merge thresholds"
    assert merge_worker.SEARCH_DISTANCE_MIN <= min(thresholds), (
        f"merge_worker starts at {merge_worker.SEARCH_DISTANCE_MIN} but ai_engine "
        f"stops merging at {min(thresholds)} — the gap is invisible to both"
    )


def test_keyword_prefilter_still_covers_every_candidate_pair():
    """The two constants are coupled; moving one alone silently drops the filter."""
    from app.workers import merge_worker

    assert (
        merge_worker.SMART_FILTER_DISTANCE_THRESHOLD
        == merge_worker.SEARCH_DISTANCE_MIN
    )


def test_widening_the_band_did_not_remove_gemini_verification():
    """Distance alone must never merge: the wider band is only a wider shortlist."""
    from app.workers import merge_worker

    src = inspect.getsource(merge_worker.run_merge_cycle)
    assert "verify_same_event(" in src
    guard = src[src.index("verify_same_event("):]
    assert "continue" in guard, "a failed verification must skip the pair"


def test_gemini_call_budget_is_still_capped():
    from app.workers import merge_worker

    assert merge_worker.MAX_GEMINI_CALLS_PER_CYCLE == 60
    src = inspect.getsource(merge_worker.run_merge_cycle)
    assert "MAX_GEMINI_CALLS_PER_CYCLE" in src


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
