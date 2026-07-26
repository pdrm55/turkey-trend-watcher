"""The uncertain-zone LLM gate is disabled by default.

Measured in production before disabling it: 189 asks across 85 clustered
articles produced zero merges, while lock timeouts, circuit skips and read
timeouts were all zero — so every one of those asks reached Ollama and came
back "no". Probing the function directly showed why: it returns False even
when both texts are byte-identical, and the best alternative prompt still
answered "no" for three realistic Turkish near-duplicate pairs. The gate was a
constant-false function consuming roughly 70% of Ollama capacity.
"""

import inspect

from app.config import Config
from app.core import ai_engine as ai_engine_module


def test_gate_is_off_by_default():
    assert Config.CLUSTER_LLM_VERIFY is False


def test_gate_can_be_re_enabled_by_env(monkeypatch):
    """Re-enabling must stay possible for when a capable model is available."""
    import importlib

    monkeypatch.setenv("CLUSTER_LLM_VERIFY", "1")
    import app.config

    reloaded = importlib.reload(app.config)
    assert reloaded.Config.CLUSTER_LLM_VERIFY is True
    monkeypatch.delenv("CLUSTER_LLM_VERIFY")
    importlib.reload(app.config)


def test_uncertain_branch_is_guarded_by_the_flag():
    src = inspect.getsource(ai_engine_module.AIEngine.process_news)
    assert "Config.CLUSTER_LLM_VERIFY" in src, "uncertain-zone ask must be gated"
    # The auto-merge branch is threshold-only and must stay unconditional.
    assert "if distance < auto_merge_thresh:" in src


def test_auto_merge_path_does_not_depend_on_the_llm():
    """Disabling the gate must not touch the 75% of articles that auto-merge."""
    src = inspect.getsource(ai_engine_module.AIEngine.process_news)
    auto = src.index("if distance < auto_merge_thresh:")
    uncertain = src.index("if distance < uncertain_thresh")
    assert auto < uncertain, "auto-merge must still be evaluated first"
    between = src[auto:uncertain]
    assert "ask_local_llm" not in between


def test_instrumentation_still_reports():
    """CLUSTER_STATS is how the follow-up threshold decision gets its data."""
    src = inspect.getsource(ai_engine_module.AIEngine.process_news)
    assert "CLUSTER_STATS" in src


def _run_standalone():
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        if "monkeypatch" in inspect.signature(fn).parameters:
            print(f"  SKIP {name} (needs pytest fixture)")
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
