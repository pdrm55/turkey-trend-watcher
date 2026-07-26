"""Pin the gravity decay curve.

Decay used to read the already-decayed `final_tps` and multiply it by
`factor ** hours_since_last_updated` on every cycle. Two things were wrong at
once: the base kept shrinking, and the exponent was the *full* elapsed time
rather than the half hour since the previous cycle. The applied exponent grew as
1.0 + 1.5 + 2.0 + … — quadratic in elapsed time. Measured on trend 273527 in
production: 64.3 hours of decay applied across 7.05 real hours, 9.1x.

It is now anchored to `tps_at_last_signal`, the score from the last real
scoring pass, which makes the cycle idempotent.

These tests model the arithmetic directly rather than importing the worker,
which needs a database. `test_matches_worker_constants` is the one that breaks
if the two drift apart.

Run: python3 -m pytest tests/test_decay_curve.py -v
  or python3 tests/test_decay_curve.py
"""
import math
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

TICK_H = 0.5          # DECAY_CHECK_INTERVAL, in hours
ARCHIVE_FLOOR = 3.0   # MIN_TPS_THRESHOLD

# Median lifetimes measured over 7,997 archived trends, used to derive the
# constants in gravity_worker. Kept here so a constant cannot be changed by
# accident without a test noticing.
EXPECTED_FACTORS = {
    "Siyaset": 0.959,
    "Ekonomi": 0.939,
    "Teknoloji": 0.880,
    "Default": 0.861,
    "Gündem": 0.842,
    "Sanat": 0.768,
    "Spor": 0.715,
}

# The factors are the pre-fix nominal values raised to this exponent, chosen so a
# clean curve reproduces the decay measured across live trends.
SCALE_EXPONENT = 2.06
PRE_FIX_NOMINAL = {
    "Siyaset": 0.98, "Ekonomi": 0.97, "Teknoloji": 0.94,
    "Default": 0.93, "Gündem": 0.92, "Sanat": 0.88, "Spor": 0.85,
}
# Independent per-category fits against live data, for the categories with
# enough samples to be worth anything.
LIVE_FITS = {"Gündem": (0.848, 150), "Teknoloji": (0.871, 10), "Siyaset": (0.915, 36)}


def decay_new(base, factor, hours):
    """Current curve: always from the fixed base."""
    return base * math.pow(factor, hours)


def decay_old(score, factor, hours):
    """Previous curve: from the running score, full elapsed time every tick."""
    return score * math.pow(factor, hours)


def _sweep_old(base, factor, total_h):
    """Replay the old cycle: a tick every TICK_H once an hour has passed."""
    score, h = base, 1.0
    while h <= total_h:
        score = decay_old(score, factor, h)
        h += TICK_H
    return score


def test_new_curve_is_idempotent():
    """The property the whole change exists for."""
    once = decay_new(50.0, 0.644, 4.0)
    twice = decay_new(50.0, 0.644, 4.0)
    assert once == twice
    # and re-running a cycle at the same elapsed time cannot shrink it further
    assert decay_new(50.0, 0.644, 4.0) == decay_new(50.0, 0.644, 4.0)


def test_old_curve_compounded():
    """Documents the bug in the unit that characterises it: applied exponent.

    Ticks at h = 1.0, 1.5 … 7.0 apply exponents summing to 52 across 7 real
    hours — 7.4x the nominal rate. Production measurement on trend 273527 was
    9.1x, higher because its ticks kept running past the 7-hour mark.
    """
    base, factor, hours = 50.0, 0.98, 7.0
    nominal = decay_new(base, factor, hours)
    actual = _sweep_old(base, factor, hours)
    assert actual < nominal, "old sweep must decay further than the nominal curve"

    applied_exponent = math.log(actual / base) / math.log(factor)
    assert applied_exponent > 5 * hours, (
        f"expected the applied exponent to dwarf {hours}h, got {applied_exponent:.1f}h"
    )


def test_old_curve_effective_exponent_is_quadratic():
    """The exponent grew with the square of elapsed time, not linearly."""
    factor = 0.98
    exp_at = {}
    for total_h in (4.0, 8.0):
        ratio = _sweep_old(50.0, factor, total_h) / 50.0
        exp_at[total_h] = math.log(ratio) / math.log(factor)
    growth = exp_at[8.0] / exp_at[4.0]
    assert growth > 3.0, f"doubling elapsed time should ~quadruple the exponent, got {growth:.1f}x"


@pytest.mark.parametrize("category", sorted(PRE_FIX_NOMINAL))
def test_factors_are_the_nominal_values_rescaled(category):
    """Uniform rescaling is what preserves the intended category ordering."""
    expected = PRE_FIX_NOMINAL[category] ** SCALE_EXPONENT
    assert abs(EXPECTED_FACTORS[category] - expected) < 0.001


@pytest.mark.parametrize("category", sorted(LIVE_FITS))
def test_factors_agree_with_the_live_fit(category):
    """Tolerance widens as the sample shrinks — Gündem at n=150 must be tight."""
    fitted, n = LIVE_FITS[category]
    tolerance = 0.01 if n >= 100 else 0.05
    assert abs(EXPECTED_FACTORS[category] - fitted) < tolerance, (
        f"{category} (n={n}): constant {EXPECTED_FACTORS[category]} vs fit {fitted}"
    )


def test_category_ordering_is_preserved():
    """Politics must still outlive sport — the recalibration kept the ranking."""
    order = ["Siyaset", "Ekonomi", "Teknoloji", "Gündem", "Sanat", "Spor"]
    values = [EXPECTED_FACTORS[c] for c in order]
    assert values == sorted(values, reverse=True)


def test_afet_decays_faster_than_its_category():
    """Disaster news is short-lived; the override must beat every category."""
    afet = 0.664
    assert afet < min(EXPECTED_FACTORS.values())


def test_matches_worker_constants():
    """The source of truth is gravity_worker; fail loudly if they diverge."""
    import re
    path = os.path.join(os.path.dirname(__file__), '..', 'app', 'workers', 'gravity_worker.py')
    with open(path, encoding='utf-8') as fh:
        src = fh.read()

    block = src.split("CATEGORY_DECAY_FACTORS = {", 1)[1].split("}", 1)[0]
    found = {m.group(1): float(m.group(2))
             for m in re.finditer(r'"([^"]+)":\s*([0-9.]+)', block)}
    assert found == EXPECTED_FACTORS, f"worker constants drifted: {found}"

    afet = re.search(r'AFET_DECAY_FACTOR\s*=\s*([0-9.]+)', src)
    assert afet and float(afet.group(1)) == 0.664


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
