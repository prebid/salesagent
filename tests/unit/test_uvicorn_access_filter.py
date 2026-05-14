"""Unit tests for the uvicorn.access noise filter.

The filter drops access-log lines for the two highest-volume endpoints:

* ``/mcp[/]`` — drops 2xx AND 401. The 401 carve-out targets anonymous
  probe traffic, which generates one access line plus one paired
  ``adcp.server.auth`` structured log per request. The structured log
  is the actual signal; the access line is duplicate noise.
* ``/health`` — drops 2xx only. A 4xx on /health always means a bug.

Other 4xx and all 5xx on /mcp still surface, and every other path is
unaffected.
"""

from __future__ import annotations

import logging

import pytest

from src.core.logging_config import UvicornAccessNoiseFilter


def _make_record(message: str) -> logging.LogRecord:
    """Build a LogRecord matching uvicorn.access's rendered message shape."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=message,
        args=(),
        exc_info=None,
    )


class TestUvicornAccessNoiseFilter:
    """Behavioral contract for the filter."""

    @pytest.fixture
    def filter_(self) -> UvicornAccessNoiseFilter:
        return UvicornAccessNoiseFilter()

    @pytest.mark.parametrize(
        "message",
        [
            # /mcp 2xx — the canonical polling baseline.
            '172.16.7.138:55934 - "GET /mcp HTTP/1.1" 200 OK',
            '172.16.7.138:55934 - "GET /mcp/ HTTP/1.1" 200 OK',
            '172.16.7.138:55934 - "POST /mcp HTTP/1.1" 202 Accepted',
            '172.16.7.138:55934 - "POST /mcp/ HTTP/1.1" 200 OK',
            '172.16.7.138:0 - "HEAD /mcp HTTP/1.1" 200 OK',
            # /mcp 401 — anonymous-probe noise. The auth rejection is also
            # captured in the structured ``adcp.server.auth`` log, so the
            # access line is dupe.
            '172.16.7.138:55934 - "POST /mcp HTTP/1.1" 401 Unauthorized',
            '172.16.7.138:55934 - "POST /mcp/ HTTP/1.1" 401 Unauthorized',
            '172.16.13.250:45036 - "POST /mcp HTTP/1.1" 401 Unauthorized',
            # /health 2xx — Fly's health checks at 15s × two regions.
            '172.19.13.249:48886 - "GET /health HTTP/1.1" 200 OK',
        ],
    )
    def test_drops_noise(self, filter_, message):
        """The high-volume baselines that bury real signal."""
        record = _make_record(message)
        assert filter_.filter(record) is False, f"Expected to drop: {message!r}"

    @pytest.mark.parametrize(
        "message",
        [
            # /mcp non-401 4xx — different failure modes worth seeing.
            '172.16.7.138:55934 - "POST /mcp HTTP/1.1" 403 Forbidden',
            '172.16.7.138:55934 - "GET /mcp HTTP/1.1" 404 Not Found',
            '172.16.7.138:55934 - "POST /mcp HTTP/1.1" 422 Unprocessable Entity',
            # /mcp 5xx — server errors always survive.
            '172.16.7.138:55934 - "POST /mcp HTTP/1.1" 500 Internal Server Error',
            # /health non-2xx — config/platform bugs, must surface.
            '172.16.7.138:55934 - "GET /health HTTP/1.1" 401 Unauthorized',
            '172.16.7.138:55934 - "GET /health HTTP/1.1" 503 Service Unavailable',
            # OAuth discovery surface keeps 401s — that's the start of
            # the OAuth dance and shows the protocol shape on first contact.
            '172.16.13.250:39132 - "GET /.well-known/oauth-protected-resource/mcp/ HTTP/1.1" 401 Unauthorized',
            # Path-prefix boundary: anything with /mcp as a substring but
            # not the literal endpoint must still log. Locks the anchor so
            # a future regex tweak can't broaden the carve-out silently.
            '172.16.7.138:55934 - "GET /mcpattack HTTP/1.1" 401 Unauthorized',
            '172.16.7.138:55934 - "POST /mcp/tools HTTP/1.1" 401 Unauthorized',
            '172.16.7.138:55934 - "GET /mcp-debug HTTP/1.1" 401 Unauthorized',
            # Same boundary check for /health: /healthz, /health/live etc.
            # are different endpoints and must not be swept up.
            '172.16.7.138:55934 - "GET /healthz HTTP/1.1" 200 OK',
            '172.16.7.138:55934 - "GET /health/live HTTP/1.1" 200 OK',
        ],
    )
    def test_keeps_real_signal(self, filter_, message):
        """Non-noise responses must survive — they're the actual signal."""
        record = _make_record(message)
        assert filter_.filter(record) is True, f"Expected to keep: {message!r}"

    @pytest.mark.parametrize(
        "message",
        [
            # Admin UI traffic — must always log so we can debug tenant flows.
            '127.0.0.1:0 - "POST /tenant/abc/products/add HTTP/1.1" 200 OK',
            '127.0.0.1:0 - "GET /admin/ HTTP/1.1" 200 OK',
            # A2A surface lives at /a2a — never suppress.
            '127.0.0.1:0 - "POST /a2a HTTP/1.1" 200 OK',
            # /mcp-suffix path that's not actually /mcp — don't false-positive.
            '127.0.0.1:0 - "GET /mcp-debug HTTP/1.1" 200 OK',
            # Discovery surface — keep these so the OAuth dance is visible.
            '127.0.0.1:0 - "GET /.well-known/agent-card.json HTTP/1.1" 200 OK',
        ],
    )
    def test_keeps_other_paths(self, filter_, message):
        """Only /mcp[/] and /health are suppressed — every other route logs."""
        record = _make_record(message)
        assert filter_.filter(record) is True, f"Expected to keep: {message!r}"

    def test_keeps_query_strings(self, filter_):
        """Query strings on the noisy paths still get suppressed on 2xx."""
        record = _make_record('127.0.0.1:0 - "GET /health?check=1 HTTP/1.1" 200 OK')
        assert filter_.filter(record) is False

    def test_keeps_non_access_log_messages(self, filter_):
        """The filter is only attached to uvicorn.access but if a stray
        non-matching message hits it, we must not drop it."""
        record = _make_record("Started server process [12345]")
        assert filter_.filter(record) is True
