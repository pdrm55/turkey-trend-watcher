"""Pin get_public_url against a client-chosen host.

Nginx never sets X-Forwarded-Host, so the header arrives from the client
untouched; and with no default_server on 443, a request carrying an unmatched
Host lands in the first TLS block, making request.host client-controlled too.
get_public_url trusted both, and its result reaches sitemap.xml, the canonical
and og:url tags, the RSS channel link, and the URL published to Telegram. The
SSR HTML is cached in Redis for 600s, so a single poisoned request served every
visitor that followed.

Verified against production before the fix: a request carrying
`X-Forwarded-Host: evil.example` returned `<loc>https://evil.example/</loc>` in
sitemap.xml.

Run: sudo docker exec ttw_api python3 -m pytest tests/test_public_host_allowlist.py -v
  or sudo docker exec ttw_api python3 tests/test_public_host_allowlist.py
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pytest
except ImportError:
    class _PytestStub:
        @staticmethod
        def main(_args):
            return _run_standalone()

    pytest = _PytestStub()

from flask import Flask

from app.config import Config
from app.api.routes import get_public_url, _allowed_public_hosts

_app = Flask(__name__)
BASE = Config.BASE_SITE_URL.rstrip('/')


def _url(host=None, forwarded=None, proto="https"):
    headers = {}
    if forwarded is not None:
        headers['X-Forwarded-Host'] = forwarded
    if proto is not None:
        headers['X-Forwarded-Proto'] = proto
    with _app.test_request_context('/', headers=headers,
                                   environ_overrides={'HTTP_HOST': host or 'trendiatr.com'}):
        return get_public_url()


def test_attacker_host_is_ignored():
    """The reported bug: an arbitrary forwarded host reached the output."""
    assert _url(forwarded='evil.example') == BASE


def test_attacker_host_via_request_host_is_ignored():
    """With no default_server on 443, request.host is attacker-reachable too."""
    assert _url(host='evil.example') == BASE


def test_configured_host_is_honoured():
    from urllib.parse import urlparse
    base_host = urlparse(BASE).netloc
    assert _url(forwarded=base_host) == f"https://{base_host}"


def test_www_variant_is_honoured():
    """nginx serves trendiatr.com and www.trendiatr.com from one block."""
    from urllib.parse import urlparse
    base_host = urlparse(BASE).netloc
    other = base_host[4:] if base_host.startswith('www.') else f"www.{base_host}"
    assert _url(forwarded=other) == f"https://{other}"


def test_only_the_first_forwarded_value_is_read():
    """A proxy chain appends; an attacker appends too, so take the first."""
    from urllib.parse import urlparse
    base_host = urlparse(BASE).netloc
    assert _url(forwarded=f"{base_host}, evil.example") == f"https://{base_host}"
    assert _url(forwarded=f"evil.example, {base_host}") == BASE


def test_host_matching_is_case_insensitive():
    from urllib.parse import urlparse
    base_host = urlparse(BASE).netloc
    assert _url(forwarded=base_host.upper()) == f"https://{base_host.lower()}"


def test_port_suffix_is_not_silently_accepted():
    """host:port is not the configured host; falling back is the safe answer."""
    from urllib.parse import urlparse
    base_host = urlparse(BASE).netloc
    assert _url(forwarded=f"evil.example:443") == BASE
    # a legitimate host with a port is not in the allowlist either, so it falls
    # back rather than emitting a URL nobody configured
    assert _url(forwarded=f"{base_host}:8443") == BASE


def test_empty_headers_fall_back_to_configured_base():
    assert _url(forwarded='') == BASE


def test_allowlist_is_never_empty():
    hosts = _allowed_public_hosts()
    assert hosts, "an empty allowlist would reject every request"
    assert all(h == h.lower() for h in hosts)


def test_no_caller_builds_urls_from_raw_headers():
    """Guards against a bare X-Forwarded-Host read creeping back in."""
    root = os.path.join(os.path.dirname(__file__), '..')
    with open(os.path.join(root, 'app/api/routes.py'), encoding='utf-8') as fh:
        src = fh.read()
    # exactly one read, inside get_public_url
    assert src.count("X-Forwarded-Host") == 1, (
        "X-Forwarded-Host must only be read in get_public_url"
    )


def _run_standalone() -> int:
    failures = []
    ran = 0
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
    print(f"\n{ran - len(failures)}/{ran} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
