"""A Redis handle that can come back after the server does.

Every Redis client in this codebase was built the same way: connect once at
import time, and on failure latch a permanent None (or False). Containers start
concurrently under docker compose, so a worker that races ahead of ttw_redis
loses its client for the entire lifetime of the process — no error after the
first one, just a feature that quietly never runs again. In gravity_worker that
meant the decay schedule was never persisted and the Persian translation sweep
never ran; in ai_engine it meant the cross-container Ollama lock silently
degraded to a per-process semaphore.

This keeps the same graceful degradation — callers still get None and carry on —
but retries on a cooldown instead of giving up forever.
"""

import logging
import time

logger = logging.getLogger("RedisConnector")

DEFAULT_RETRY_INTERVAL = 30


class RedisConnector:
    def __init__(self, url: str, *, name: str, retry_interval: int = DEFAULT_RETRY_INTERVAL,
                 decode_responses: bool = True, client_factory=None):
        self._url = url
        self._name = name
        self._retry_interval = retry_interval
        self._decode_responses = decode_responses
        # Injectable so tests can drive the failure path without a live server
        # and without patching the global redis module.
        self._client_factory = client_factory
        self._client = None
        self._next_attempt = 0.0
        self._announced = False

    def _connect(self):
        if self._client_factory is not None:
            return self._client_factory()
        import redis as _redis_lib

        return _redis_lib.from_url(
            self._url,
            decode_responses=self._decode_responses,
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    def get(self):
        """Return a connected client, or None while Redis is unreachable."""
        if self._client is not None:
            return self._client
        now = time.monotonic()
        if now < self._next_attempt:
            return None
        try:
            client = self._connect()
            client.ping()
        except Exception as exc:
            self._next_attempt = now + self._retry_interval
            # Only the first failure of a streak is worth a log line; after that
            # it repeats every retry_interval for as long as Redis is down.
            if self._announced:
                logger.debug("Redis (%s) still unavailable: %s", self._name, exc)
            else:
                logger.warning("⚠️ Redis (%s) unavailable, will retry: %s", self._name, exc)
                self._announced = True
            return None

        self._client = client
        if self._announced:
            logger.info("✅ Redis (%s) reconnected.", self._name)
            self._announced = False
        return client

    def drop(self, exc=None):
        """Discard the current client so the next get() reconnects.

        Call this when an operation fails: redis-py can hand back a client whose
        connection died, and without dropping it every later call fails too.
        """
        if self._client is not None:
            logger.warning("⚠️ Redis (%s) connection dropped: %s", self._name, exc)
        self._client = None
        self._next_attempt = time.monotonic() + self._retry_interval
        self._announced = True
