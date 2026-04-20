"""STUB (L0-06 Red) — CSRFOriginMiddleware scaffolding so tests fail SEMANTICALLY.

Green commit replaces this with the real pure-ASGI Origin-validation middleware
per .claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md §11.7.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _extract_header(scope: dict, name: str) -> str | None:
    """Stub — Green reads headers from ASGI scope."""
    return None


def _origin_of(url: str) -> str | None:
    """Stub — Green RFC-6454 serializes scheme://host[:port]."""
    return None


class CSRFOriginMiddleware:
    """Stub CSRFOriginMiddleware — Red behavior: raises on __call__.

    Constructor accepts the same kwargs as Green so test harness can build it;
    ``__call__`` raises ``NotImplementedError`` so every Origin-scenario test
    fails semantically (500 / exception) rather than bypassing validation.
    """

    def __init__(
        self,
        app: Any,
        *,
        allowed_origins: Iterable[str] = (),
        allowed_origin_suffixes: Iterable[str] = (),
    ) -> None:
        self.app = app
        self.allowed_origins: frozenset[str] = frozenset(allowed_origins)
        self.allowed_suffixes: tuple[str, ...] = tuple(allowed_origin_suffixes)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        raise NotImplementedError("L0-06 Red stub — Green replaces")
