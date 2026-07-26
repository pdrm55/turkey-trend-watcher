"""Verify the image worker can no longer be frozen by a single hung item.

Reproduces the 2026-06-09 failure mode (a _process_one_inner that never returns)
and asserts the new safeguards bound it:
  * _process_one enforces _ITEM_TIMEOUT and marks the item failed
  * the run()-loop gather pattern (return_exceptions=True) survives a raising task
Run inside a container: python3 tests/test_image_worker_timeout.py
"""
import asyncio
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.workers import image_processor as ip
from app.workers.image_processor import ImageProcessor


def make_worker():
    # Build without __init__ so we don't open a real Telegram client.
    w = ImageProcessor.__new__(ImageProcessor)
    w._sem = asyncio.Semaphore(8)
    w._last_heartbeat = 0.0
    w._marked = []
    # Stub the DB-writing failure marker so the test needs no DB.
    w._mark_item_failed = lambda nid: w._marked.append(nid)
    return w


async def test_hung_item_is_bounded():
    ip._ITEM_TIMEOUT = 1  # shrink for the test
    w = make_worker()

    async def _hang(news_id):
        await asyncio.sleep(60)  # simulate a telethon call that never returns
    w._process_one_inner = _hang

    start = asyncio.get_event_loop().time()
    await asyncio.wait_for(w._process_one(123), timeout=10)  # must finish well under 10s
    elapsed = asyncio.get_event_loop().time() - start

    assert elapsed < 5, f"hung item not bounded (took {elapsed:.1f}s)"
    assert w._marked == [123], f"failed item not marked: {w._marked}"
    print(f"✅ hung item bounded in {elapsed:.2f}s and marked failed")


async def test_gather_survives_raising_task():
    """The exact pattern used in run(): one task raises, batch still completes."""
    ip._ITEM_TIMEOUT = 5
    w = make_worker()

    processed = []

    async def _inner(news_id):
        if news_id == 2:
            raise RuntimeError("boom")
        processed.append(news_id)
    w._process_one_inner = _inner

    news_ids = [1, 2, 3]
    results = await asyncio.gather(
        *[w._process_one(nid) for nid in news_ids],
        return_exceptions=True,
    )
    # _process_one swallows inner errors via wait_for? No — only TimeoutError.
    # A RuntimeError propagates out of _process_one, but return_exceptions captures it.
    errs = [r for r in results if isinstance(r, Exception)]
    assert sorted(processed) == [1, 3], f"good items not processed: {processed}"
    assert len(errs) == 1 and isinstance(errs[0], RuntimeError), f"unexpected: {results}"
    print(f"✅ gather survived raising task; good items processed: {sorted(processed)}")


async def test_watchdog_fires_on_stall():
    ip._WATCHDOG_TIMEOUT = 1
    w = make_worker()
    w._last_heartbeat = asyncio.get_event_loop().time() - 100  # already stalled

    # Stub the watchdog's 60s cadence sleep so the test runs fast.
    real_sleep = asyncio.sleep

    async def fast_sleep(secs):
        await real_sleep(0.05)
    ip.asyncio.sleep = fast_sleep

    exited = {}
    orig_exit = os._exit
    os._exit = lambda code: exited.setdefault("code", code)
    try:
        task = asyncio.create_task(w._watchdog())
        await real_sleep(0.3)  # allow several fast watchdog ticks
        task.cancel()
    finally:
        os._exit = orig_exit
        ip.asyncio.sleep = real_sleep
    assert exited.get("code") == 1, "watchdog did not force-exit on stall"
    print("✅ watchdog force-exits on stalled heartbeat")


def test_uses_dedicated_executor():
    """The worker must own a bounded ThreadPoolExecutor (not the shared default
    pool) so slow downloads can't starve the loop, and use tuple HTTP timeouts."""
    from concurrent.futures import ThreadPoolExecutor

    # Patch TelegramClient so __init__ doesn't touch the real session file.
    orig_client = ip.TelegramClient
    ip.TelegramClient = lambda *a, **k: object()
    try:
        w = ImageProcessor()
    finally:
        ip.TelegramClient = orig_client

    assert isinstance(w._executor, ThreadPoolExecutor), "worker has no dedicated executor"
    assert w._executor._max_workers == 8, f"unexpected pool size: {w._executor._max_workers}"
    assert isinstance(ip._HTTP_TIMEOUT, tuple) and len(ip._HTTP_TIMEOUT) == 2, \
        f"_HTTP_TIMEOUT must be a (connect, read) tuple, got {ip._HTTP_TIMEOUT!r}"
    w._executor.shutdown(wait=False)
    print(f"✅ dedicated bounded executor (max_workers={w._executor._max_workers}) "
          f"and tuple HTTP timeout {ip._HTTP_TIMEOUT}")


async def main():
    await test_hung_item_is_bounded()
    await test_gather_survives_raising_task()
    await test_watchdog_fires_on_stall()
    test_uses_dedicated_executor()
    print("\n🎉 All image-worker robustness tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
