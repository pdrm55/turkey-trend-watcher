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

