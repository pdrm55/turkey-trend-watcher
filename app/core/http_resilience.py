import random
import time
import logging
from typing import Optional

import requests

from app.config import Config
from app.core.observability import emit_metric


logger = logging.getLogger("HttpResilience")


def request_with_retry(
    method: str,
    url: str,
    *,
    timeout: int = 10,
    attempts: Optional[int] = None,
    retry_on_status=(429, 500, 502, 503, 504),
    metric_name: str = "http.request",
    **kwargs
):
    max_attempts = max(1, attempts or getattr(Config, "HTTP_RETRY_ATTEMPTS", 3))
    backoff_base = max(0.1, getattr(Config, "HTTP_BACKOFF_BASE_SECONDS", 0.7))
    backoff_max = max(backoff_base, getattr(Config, "HTTP_BACKOFF_MAX_SECONDS", 8))
    jitter = max(0.0, getattr(Config, "HTTP_BACKOFF_JITTER_SECONDS", 0.3))

    last_exc = None
    for attempt in range(1, max_attempts + 1):
        started = time.perf_counter()
        try:
            resp = requests.request(method=method.upper(), url=url, timeout=timeout, **kwargs)
            latency_ms = (time.perf_counter() - started) * 1000.0
            emit_metric(metric_name, round(latency_ms, 2), status=resp.status_code, attempt=attempt)

            if resp.status_code in retry_on_status and attempt < max_attempts:
                sleep_s = min(backoff_max, backoff_base * (2 ** (attempt - 1))) + random.uniform(0, jitter)
                time.sleep(sleep_s)
                continue
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            latency_ms = (time.perf_counter() - started) * 1000.0
            emit_metric(metric_name, round(latency_ms, 2), status="exception", attempt=attempt)
            if attempt >= max_attempts:
                break
            sleep_s = min(backoff_max, backoff_base * (2 ** (attempt - 1))) + random.uniform(0, jitter)
            time.sleep(sleep_s)

    if last_exc:
        raise last_exc
    raise RuntimeError(f"HTTP request failed after retries: {method} {url}")


FEED_HEADERS = {
    # Six Turkish outlets (sozcu, ensonhaber, evrensel, gazeteduvar, technopat,
    # indyturk) answer 403 to a bare python-requests agent and 200 to a browser
    # one. feedparser sent its own agent, so this only became visible once the
    # fetch moved here.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


def parse_feed(url: str, *, timeout: int = 15, metric_name: str = "feed.fetch"):
    """Fetch a feed body under a timeout, then hand the bytes to feedparser.

    `feedparser.parse(url)` does its own fetch through urllib, which honours only
    the global socket timeout — never set in this project, so it is None. A single
    unresponsive feed server could stall a worker's whole cycle across every other
    feed, with no upper bound. Fetching first also gets the retry and backoff
    behaviour the rest of the project's HTTP already has.

    Raises whatever request_with_retry raises; both callers already treat a failed
    feed as "skip this source and continue".
    """
    import feedparser  # local: keeps this module importable without the collector deps

    resp = request_with_retry(
        "GET", url, timeout=timeout, metric_name=metric_name, headers=FEED_HEADERS
    )
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)

    # raise_for_status only catches feeds that fail loudly. Five sources answered
    # 200 with an HTML page or an empty body — indistinguishable from "no news
    # right now" to every caller, so they went unnoticed for as long as they had
    # been dead. A zero-entry response is not necessarily broken, so this warns
    # rather than raising; a source warning on every cycle is a dead source.
    if not parsed.entries:
        logger.warning("Feed returned no entries: %s (%d bytes)", url, len(resp.content))
        emit_metric("feed.empty", 1, url=url)

    return parsed

