"""Redis handles must survive Redis being down at import time.

Every client in this codebase connected once at module import and latched a
permanent None/False on failure. Containers start concurrently under docker
compose, so a worker that wins the race against ttw_redis lost its client for
the entire life of the process, with no error after the first: the decay
schedule was never persisted, the Persian translation sweep never ran, and the
cross-container Ollama lock silently degraded to a per-process semaphore while
still believing it serialised across containers.
"""

import time

from app.core.redis_connector import RedisConnector


class FakeClientFactory:
    """Stands in for redis.from_url, failing the first `fail_times` attempts."""

    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.attempts = 0

    def __call__(self):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise ConnectionError("connection refused")
        return _Pingable()


class _Pingable:
    def ping(self):
        return True


def _connector(factory, **kw):
    return RedisConnector(
        "redis://fake:6379/0", name="test", client_factory=factory, **kw
    )


def test_first_failure_returns_none_without_raising():
    conn = _connector(FakeClientFactory(fail_times=1))
    assert conn.get() is None


def test_it_reconnects_after_the_cooldown():
    """The whole point: a failure at startup must not be permanent."""
    factory = FakeClientFactory(fail_times=1)
    conn = _connector(factory, retry_interval=0)
    assert conn.get() is None
    assert conn.get() is not None, "must retry once the cooldown has passed"
    assert factory.attempts == 2


def test_cooldown_prevents_hammering_a_down_server():
    factory = FakeClientFactory(fail_times=99)
    conn = _connector(factory, retry_interval=300)
    for _ in range(10):
        assert conn.get() is None
    assert factory.attempts == 1, "must not retry before the cooldown expires"


def test_successful_client_is_reused_not_reconnected():
    factory = FakeClientFactory()
    conn = _connector(factory, retry_interval=0)
    first = conn.get()
    for _ in range(5):
        assert conn.get() is first
    assert factory.attempts == 1


def test_drop_forces_a_reconnect_on_the_next_call():
    factory = FakeClientFactory()
    conn = _connector(factory, retry_interval=0)
    first = conn.get()
    conn.drop(RuntimeError("connection reset"))
    second = conn.get()
    assert factory.attempts == 2
    assert second is not first


def test_drop_also_respects_the_cooldown():
    factory = FakeClientFactory()
    conn = _connector(factory, retry_interval=300)
    conn.get()
    conn.drop(RuntimeError("connection reset"))
    assert conn.get() is None, "a dropped connection must back off too"


def test_no_module_latches_a_dead_redis_handle():
    """Guards the original bug pattern from coming back."""
    import inspect

    from app.core import ai_engine
    from app.workers import gravity_worker, summarizer

    for mod in (ai_engine, gravity_worker, summarizer):
        src = inspect.getsource(mod)
        assert "_redis_fa = None" not in src
        assert "self._redis = False" not in src


def _run_standalone():
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
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
