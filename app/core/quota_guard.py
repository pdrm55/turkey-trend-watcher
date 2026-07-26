"""
Shared Gemini quota circuit breaker.

Problem this solves: the summarizer (ttw_summarizer) and the FA translation
sweep (ttw_gravity) are separate containers that both call the same Gemini
project quota. When the quota runs out each one independently kept retrying —
5211 rejected calls in 24h from the summarizer alone, none of them producing
anything, all of them keeping the account pinned at its rate limit.

The breaker is stored in Redis so both containers share one view of the quota:
when either one hits a 429, everyone backs off. Exponential cooldown, reset on
the first success.

Usage:
    from app.core.quota_guard import gemini_quota

    if gemini_quota.is_open():
        return None                      # skip — quota is exhausted
    try:
        resp = client.models.generate_content(...)
        gemini_quota.record_success()
    except Exception as e:
        if gemini_quota.record_failure(e): # True when it was a quota error
            ...
"""
import os
import time
import logging

logger = logging.getLogger(__name__)

# Cooldown ladder: 1m → 5m → 15m → 30m (capped). Reset to step 0 on any success.
_COOLDOWN_STEPS = [60, 300, 900, 1800]

_KEY_UNTIL = "ttw:gemini:cooldown_until"
_KEY_STRIKES = "ttw:gemini:strikes"


def _is_quota_error(exc) -> bool:
    """True for rate-limit / quota-exhaustion errors (as opposed to real bugs)."""
    s = str(exc).upper()
    return "429" in s or "RESOURCE_EXHAUSTED" in s or "QUOTA" in s


class _GeminiQuotaGuard:
    def __init__(self):
        self._redis = None
        # In-process fallback, used when Redis is unreachable. Degrades the
        # breaker to per-container instead of shared, which is still far better
        # than no breaker at all.
        self._local_until = 0.0
        self._local_strikes = 0

        try:
            import redis as _redis_lib
            self._redis = _redis_lib.from_url(
                f"redis://{os.getenv('REDIS_HOST', 'ttw_redis')}:6379/0",
                decode_responses=True, socket_connect_timeout=2,
            )
            self._redis.ping()
        except Exception as e:
            logger.warning(f"[quota_guard] Redis unavailable ({e}); using in-process breaker.")
            self._redis = None

    # ── state accessors (Redis with local fallback) ──────────────────────────

    def _get(self, key: str, default: float = 0.0) -> float:
        if self._redis:
            try:
                v = self._redis.get(key)
                return float(v) if v else default
            except Exception:
                pass
        return self._local_until if key == _KEY_UNTIL else self._local_strikes

    def _set(self, key: str, value: float, ttl: int | None = None):
        if self._redis:
            try:
                if ttl:
                    self._redis.setex(key, ttl, value)
                else:
                    self._redis.set(key, value)
                return
            except Exception:
                pass
        if key == _KEY_UNTIL:
            self._local_until = value
        else:
            self._local_strikes = value

    # ── public API ───────────────────────────────────────────────────────────

    def is_open(self) -> bool:
        """True when the breaker is open — callers must NOT call Gemini."""
        return time.time() < self._get(_KEY_UNTIL)

    def seconds_remaining(self) -> int:
        return max(0, int(self._get(_KEY_UNTIL) - time.time()))

    def record_success(self):
        """Reset the ladder. Called after any successful Gemini response."""
        if self._get(_KEY_STRIKES) or self._get(_KEY_UNTIL):
            self._set(_KEY_STRIKES, 0)
            self._set(_KEY_UNTIL, 0)

    def record_failure(self, exc) -> bool:
        """
        Record a failed Gemini call. Opens/extends the breaker only for quota
        errors — a malformed prompt or a server-side 500 should not stop the
        whole fleet. Returns True if this was a quota error.
        """
        if not _is_quota_error(exc):
            return False

        strikes = int(self._get(_KEY_STRIKES))
        step = _COOLDOWN_STEPS[min(strikes, len(_COOLDOWN_STEPS) - 1)]
        until = time.time() + step

        self._set(_KEY_STRIKES, strikes + 1, ttl=86400)
        self._set(_KEY_UNTIL, until, ttl=step + 60)

        logger.warning(
            f"[quota_guard] Gemini quota exhausted (strike {strikes + 1}). "
            f"Pausing all Gemini calls for {step}s."
        )
        return True


gemini_quota = _GeminiQuotaGuard()
