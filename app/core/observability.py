import logging
import time
from contextlib import contextmanager


logger = logging.getLogger("Observability")


def emit_metric(name: str, value: float, **labels):
    label_text = " ".join([f"{k}={v}" for k, v in labels.items()]) if labels else ""
    logger.info(f"📈 metric name={name} value={value} {label_text}".strip())


@contextmanager
def traced_span(span_name: str, **labels):
    start = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = (time.perf_counter() - start) * 1000.0
        emit_metric("span.duration_ms", round(duration_ms, 2), span=span_name, **labels)

