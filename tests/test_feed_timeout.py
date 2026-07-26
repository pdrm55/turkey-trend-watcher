"""Prove feed fetching cannot hang forever.

`feedparser.parse(url)` fetches through urllib, which honours only the global
socket timeout. This project never calls socket.setdefaulttimeout, so that value
is None: a single unresponsive feed server could stall the RSS worker's entire
cycle across all 50 feeds, with no upper bound. Both call sites now go through
`parse_feed`, which fetches under an explicit timeout first.

The tests run a local HTTP server that deliberately stalls, so the failure being
prevented is reproduced rather than assumed.

Run: python3 -m pytest tests/test_feed_timeout.py -v
  or python3 tests/test_feed_timeout.py
"""
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pytest
except ImportError:
    class _PytestStub:
        @staticmethod
        def main(_args):
            return _run_standalone()

    pytest = _PytestStub()

FEED_BODY = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>probe</title>
<item><title>Probe headline</title><link>http://example.invalid/a</link></item>
</channel></rss>"""

STALL_SECONDS = 30


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/stall"):
            time.sleep(STALL_SECONDS)      # never answers within any sane timeout
            return
        if self.path.startswith("/boom"):
            self.send_response(500)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml")
        self.send_header("Content-Length", str(len(FEED_BODY)))
        self.end_headers()
        self.wfile.write(FEED_BODY)

    def log_message(self, *_args):
        pass


_server = None
_base = None


def setup_module(_=None):
    global _server, _base
    _server = HTTPServer(("127.0.0.1", 0), _Handler)
    _base = f"http://127.0.0.1:{_server.server_address[1]}"
    threading.Thread(target=_server.serve_forever, daemon=True).start()


def teardown_module(_=None):
    if _server:
        _server.shutdown()


def test_healthy_feed_parses():
    from app.core.http_resilience import parse_feed
    feed = parse_feed(f"{_base}/ok", timeout=5)
    assert [e.title for e in feed.entries] == ["Probe headline"]


def test_stalled_server_raises_instead_of_hanging():
    """The point of the change: bounded failure, not an unbounded stall."""
    from app.core.http_resilience import parse_feed
    started = time.monotonic()
    try:
        parse_feed(f"{_base}/stall", timeout=2)
        raised = False
    except Exception:
        raised = True
    elapsed = time.monotonic() - started

    assert raised, "a stalled server must surface as an exception"
    assert elapsed < STALL_SECONDS, (
        f"call took {elapsed:.1f}s — it waited on the server instead of timing out"
    )


def test_timeout_is_bounded_by_retries():
    """Retries multiply the timeout, so the ceiling must still be finite."""
    from app.core.http_resilience import parse_feed
    started = time.monotonic()
    try:
        parse_feed(f"{_base}/stall", timeout=1)
    except Exception:
        pass
    elapsed = time.monotonic() - started
    assert elapsed < 20, f"worst case {elapsed:.1f}s is too long for one bad feed"


def test_error_status_is_raised():
    from app.core.http_resilience import parse_feed
    try:
        parse_feed(f"{_base}/boom", timeout=3)
        raised = False
    except Exception:
        raised = True
    assert raised, "an HTTP error must not be parsed as an empty feed"


def test_callers_use_the_helper():
    """Guards against a bare feedparser.parse(url) creeping back in."""
    root = os.path.join(os.path.dirname(__file__), '..')
    for rel in ("app/collectors/rss_fetcher.py", "app/workers/social_worker.py"):
        with open(os.path.join(root, rel), encoding='utf-8') as fh:
            src = fh.read()
        assert "feedparser.parse(" not in src, f"{rel} bypasses the timeout helper"
        assert "parse_feed(" in src, f"{rel} should fetch through parse_feed"


def _run_standalone() -> int:
    setup_module()
    failures = []
    ran = 0
    try:
        for name, fn in sorted(globals().items()):
            if not name.startswith("test_") or not callable(fn):
                continue
            ran += 1
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as e:
                failures.append(name)
                print(f"FAIL  {name}: {e}")
            except Exception as e:
                failures.append(name)
                print(f"ERROR {name}: {type(e).__name__}: {e}")
    finally:
        teardown_module()
    print(f"\n{ran - len(failures)}/{ran} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
