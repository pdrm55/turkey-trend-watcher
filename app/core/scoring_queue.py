import logging
from typing import Optional, Tuple

import redis

from app.config import Config

logger = logging.getLogger("ScoringQueue")


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

            # sAdd returns 1 only for first insertion => dedup pending jobs.
            if self._redis.sadd(self._pending, tid) == 0:
                return True

            self._redis.lpush(self._lane(priority), tid)
            return True
        except Exception as exc:
            logger.error(f"❌ Queue enqueue error for trend {tid}: {exc}")
            return False

    def pop(self) -> Optional[Tuple[int, str]]:
        """Pop one trend id (breaking first, then normal) and return its lane."""
        if not self._redis:
            return None

        try:
            tid = self._redis.rpop(self._q_breaking)
            lane = self.BREAKING
            if tid is None:
                tid = self._redis.rpop(self._q_normal)
                lane = self.NORMAL
            if tid is None:
                return None

            self._redis.srem(self._pending, tid)
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

            # Dedup set removal already happened on pop; push directly.
            self._redis.lpush(self._lane(priority), tid)
            self._redis.sadd(self._pending, tid)
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

    def size(self) -> int:
        if not self._redis:
            return 0
        try:
            return int(self._redis.scard(self._pending))
        except Exception:
            return 0


scoring_queue = ScoringQueue()

