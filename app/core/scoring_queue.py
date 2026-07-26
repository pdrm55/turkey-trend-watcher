import logging
import time
from typing import Optional, Tuple

import redis

from app.config import Config

logger = logging.getLogger("ScoringQueue")

# KEYS[1]=pending set, KEYS[2]=target lane, ARGV[1]=trend id.
# Returns 1 when the job was pushed, 0 when it was already pending.
_LUA_ENQUEUE = """
if redis.call('SADD', KEYS[1], ARGV[1]) == 0 then
  return 0
end
redis.call('LPUSH', KEYS[2], ARGV[1])
return 1
"""

# KEYS[1]=breaking lane, KEYS[2]=normal lane, KEYS[3]=pending set.
# Returns {id, lane} or nil. The SREM must land with the RPOP, otherwise a crash
# in between leaves the id pending forever while nothing holds it in a lane.
_LUA_POP = """
local tid = redis.call('RPOP', KEYS[1])
local lane = 'breaking'
if not tid then
  tid = redis.call('RPOP', KEYS[2])
  lane = 'normal'
end
if not tid then
  return nil
end
redis.call('SREM', KEYS[3], tid)
return {tid, lane}
"""


class ScoringQueue:
    """
    Redis-backed priority queue for async TPS scoring.
    - Priority lanes: breaking > normal
    - Dedup via pending set
    - Soft backpressure using max pending size
    """

    BREAKING = "breaking"
    NORMAL = "normal"

    def __init__(self):
        self.max_size = max(100, getattr(Config, "SCORING_QUEUE_MAX_SIZE", 5000))
        self.max_retries = max(0, getattr(Config, "SCORING_QUEUE_MAX_RETRIES", 2))
        self._redis = None
        try:
            self._redis = redis.from_url(Config.REDIS_URL, decode_responses=True)
        except Exception as exc:
            logger.error(f"❌ Queue Redis connection error: {exc}")

        self._q_breaking = "queue:scoring:breaking"
        self._q_normal = "queue:scoring:normal"
        self._pending = "queue:scoring:pending"
        self._retries = "queue:scoring:retries"

        self._enqueue_script = None
        self._pop_script = None
        if self._redis is not None:
            # Lua runs atomically inside Redis, which a MULTI pipeline cannot do
            # here: the LPUSH has to be conditional on the SADD result.
            self._enqueue_script = self._redis.register_script(_LUA_ENQUEUE)
            self._pop_script = self._redis.register_script(_LUA_POP)

    @property
    def enabled(self) -> bool:
        return self._redis is not None

    def _lane(self, priority: str) -> str:
        return self._q_breaking if priority == self.BREAKING else self._q_normal

    def enqueue(self, trend_id: int, priority: str = NORMAL) -> bool:
        """Enqueue trend id if not already pending. Returns True on successful push."""
        if not self._redis:
            return False

        tid = str(trend_id)
        try:
            pending_size = self._redis.scard(self._pending)
            if pending_size >= self.max_size and priority != self.BREAKING:
                # Soft drop for normal lane under pressure; DB fallback still exists.
                logger.warning(
                    f"⚠️ Queue backpressure active (size={pending_size}/{self.max_size}), dropped normal trend {tid}"
                )
                return False

            # SADD-then-LPUSH must be atomic. A crash between the two used to leave
            # the id in `pending` with nothing in a lane; every later enqueue then
            # hit `sadd == 0` and returned early, so the trend became permanently
            # unqueueable. The orphans also inflated scard(_pending) — the
            # backpressure counter — and once that drift reached max_size every
            # normal-lane job was silently dropped for good.
            self._enqueue_script(
                keys=[self._pending, self._lane(priority)], args=[tid]
            )
            return True
        except Exception as exc:
            logger.error(f"❌ Queue enqueue error for trend {tid}: {exc}")
            return False

    def pop(self) -> Optional[Tuple[int, str]]:
        """Pop one trend id (breaking first, then normal) and return its lane."""
        if not self._redis:
            return None

        try:
            result = self._pop_script(
                keys=[self._q_breaking, self._q_normal, self._pending]
            )
            if not result:
                return None
            tid, lane = result[0], result[1]
            return int(tid), lane
        except Exception as exc:
            logger.error(f"❌ Queue pop error: {exc}")
            return None

    def retry_or_drop(self, trend_id: int, priority: str = NORMAL) -> bool:
        """
        Requeue failed jobs up to max_retries.
        Returns True when job is requeued, False when dropped.
        """
        if not self._redis:
            return False

        tid = str(trend_id)
        try:
            current_retry = int(self._redis.hincrby(self._retries, tid, 1))
            if current_retry > self.max_retries:
                self._redis.hdel(self._retries, tid)
                logger.error(
                    f"🛑 Queue drop trend {tid} after retries exceeded ({current_retry - 1}/{self.max_retries})"
                )
                return False

            # Dedup set removal already happened on pop, so this re-adds and pushes
            # through the same atomic path as a fresh enqueue.
            self._enqueue_script(keys=[self._pending, self._lane(priority)], args=[tid])
            logger.warning(f"♻️ Queue retry trend {tid} ({current_retry}/{self.max_retries})")
            return True
        except Exception as exc:
            logger.error(f"❌ Queue retry error for trend {tid}: {exc}")
            return False

    def clear_retry(self, trend_id: int) -> None:
        if not self._redis:
            return
        try:
            self._redis.hdel(self._retries, str(trend_id))
        except Exception:
            return

    # ── Deferral backoff ────────────────────────────────────────────────────
    # The DB fallback scan in gravity_worker re-reads `needs_scoring = true`
    # every cycle, so a trend that cannot be scored right now comes back every
    # five seconds forever. In production that left 19 trends looping for up to
    # seven hours, and because the scan is a plain LIMIT they also sat in front
    # of every newly arrived trend. Backing a failing trend off exponentially
    # both sheds the pointless load and unblocks the queue behind it.
    #
    # State lives in one key per trend: "<attempts>:<ready_at>", carrying a TTL
    # well past the delay it encodes, so escalation survives the wait and the
    # keys still expire on their own for trends that get deleted.
    _BACKOFF_STEPS = (60, 180, 600, 1800, 3600)
    _BACKOFF_KEY_TTL = 7200

    def _backoff_key(self, trend_id: int) -> str:
        return f"queue:scoring:backoff:{trend_id}"

    def note_deferral(self, trend_id: int) -> int:
        """Record a failed scoring attempt. Returns the backoff seconds applied."""
        attempts = 0
        delay = self._BACKOFF_STEPS[0]
        if not self._redis:
            return delay
        try:
            raw = self._redis.get(self._backoff_key(trend_id))
            if raw:
                attempts = int(raw.split(":", 1)[0])
            delay = self._BACKOFF_STEPS[min(attempts, len(self._BACKOFF_STEPS) - 1)]
            self._redis.set(
                self._backoff_key(trend_id),
                f"{attempts + 1}:{time.time() + delay}",
                ex=self._BACKOFF_KEY_TTL,
            )
        except Exception as exc:
            logger.error(f"❌ Queue backoff write error for trend {trend_id}: {exc}")
        return delay

    def in_backoff(self, trend_id: int) -> bool:
        """True while a previously failed trend is still inside its backoff window."""
        if not self._redis:
            return False
        try:
            raw = self._redis.get(self._backoff_key(trend_id))
            if not raw:
                return False
            return time.time() < float(raw.split(":", 1)[1])
        except Exception:
            # Never let a backoff read stop scoring — failing open just means the
            # trend is attempted, which is the old behaviour.
            return False

    def clear_backoff(self, trend_id: int) -> None:
        if not self._redis:
            return
        try:
            self._redis.delete(self._backoff_key(trend_id))
        except Exception:
            return

    def size(self) -> int:
        if not self._redis:
            return 0
        try:
            return int(self._redis.scard(self._pending))
        except Exception:
            return 0


scoring_queue = ScoringQueue()

