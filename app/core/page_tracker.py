"""
Redis-based page view tracker.
Tracks HTML page views with HyperLogLog unique visitor estimation.
Keys are TTL-bounded so Redis memory stays bounded.
"""

import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import List

logger = logging.getLogger("PageTracker")

_PREFIX = "ttw:pv"
_DAILY_TTL = 60 * 60 * 24 * 35   # 35 days
_HOURLY_TTL = 60 * 60 * 24 * 3   # 3 days
_HLL_TTL    = 60 * 60 * 24 * 35  # 35 days (HyperLogLog for unique visitors)

_SKIP_PREFIXES = ("/static/", "/api/", "/admin/", "/sitemap", "/robots", "/favicon")


def _hash_ip(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


class PageTracker:
    def __init__(self):
        self._redis = None  # None = not yet tried; False = unavailable

    def _get_redis(self):
        if self._redis is not None:
            return self._redis
        try:
            import redis as _redis
            from app.config import Config
            client = _redis.from_url(Config.REDIS_URL, socket_connect_timeout=1, decode_responses=True)
            client.ping()
            self._redis = client
        except Exception as e:
            logger.debug(f"PageTracker Redis unavailable: {e}")
            self._redis = False
        return self._redis

    def track(self, path: str, ip: str) -> None:
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            return
        r = self._get_redis()
        if not r:
            return
        try:
            now = datetime.now(timezone.utc)
            date_str  = now.strftime("%Y-%m-%d")
            hour_str  = now.strftime("%Y-%m-%d:%H")
            ip_hash   = _hash_ip(ip or "unknown")

            pipe = r.pipeline(transaction=False)
            pipe.incr(f"{_PREFIX}:daily:{date_str}")
            pipe.expire(f"{_PREFIX}:daily:{date_str}", _DAILY_TTL)
            pipe.incr(f"{_PREFIX}:hourly:{hour_str}")
            pipe.expire(f"{_PREFIX}:hourly:{hour_str}", _HOURLY_TTL)
            pipe.pfadd(f"{_PREFIX}:uniq:daily:{date_str}", ip_hash)
            pipe.expire(f"{_PREFIX}:uniq:daily:{date_str}", _HLL_TTL)
            pipe.execute()
        except Exception as e:
            logger.debug(f"PageTracker.track error: {e}")

    def get_today_stats(self) -> dict:
        r = self._get_redis()
        if not r:
            return {"views": 0, "unique": 0}
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            pipe = r.pipeline(transaction=False)
            pipe.get(f"{_PREFIX}:daily:{today}")
            pipe.pfcount(f"{_PREFIX}:uniq:daily:{today}")
            values = pipe.execute()
            return {"views": int(values[0] or 0), "unique": int(values[1] or 0)}
        except Exception as e:
            logger.debug(f"PageTracker.get_today_stats error: {e}")
            return {"views": 0, "unique": 0}

    def get_daily_stats(self, days: int = 30) -> List[dict]:
        """Returns [{'date': str, 'views': int, 'unique': int}] for last N days."""
        r = self._get_redis()
        if not r:
            return []
        try:
            now   = datetime.now(timezone.utc)
            dates = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
            pipe  = r.pipeline(transaction=False)
            for d in dates:
                pipe.get(f"{_PREFIX}:daily:{d}")
                pipe.pfcount(f"{_PREFIX}:uniq:daily:{d}")
            values = pipe.execute()
            results = []
            for i, d in enumerate(dates):
                results.append({
                    "date":   d,
                    "views":  int(values[i * 2]     or 0),
                    "unique": int(values[i * 2 + 1] or 0),
                })
            return results
        except Exception as e:
            logger.debug(f"PageTracker.get_daily_stats error: {e}")
            return []

    def get_hourly_stats(self, hours: int = 48) -> List[dict]:
        """Returns [{'label': str, 'views': int}] for last N hours."""
        r = self._get_redis()
        if not r:
            return []
        try:
            now    = datetime.now(timezone.utc)
            labels = []
            pipe   = r.pipeline(transaction=False)
            for i in range(hours - 1, -1, -1):
                t = now - timedelta(hours=i)
                labels.append(t.strftime("%m/%d %H:00"))
                pipe.get(f"{_PREFIX}:hourly:{t.strftime('%Y-%m-%d:%H')}")
            values = pipe.execute()
            return [{"label": labels[i], "views": int(v or 0)} for i, v in enumerate(values)]
        except Exception as e:
            logger.debug(f"PageTracker.get_hourly_stats error: {e}")
            return []


page_tracker = PageTracker()
