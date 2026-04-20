"""Fly.io header normalizer — STUB (L0-08 Red).

Canonical spec: ``flask-to-fastapi-foundation-modules.md`` §11.9.

At L0-08 this module exists as a pass-through stub so the Red test can
fail for a **semantic** reason ("X-Forwarded-For not written") rather
than ``ImportError``. The Green commit replaces the body with the real
``Fly-Forwarded-*`` → ``X-Forwarded-*`` translation per §11.9.A.
"""

from __future__ import annotations

from typing import Any


class FlyHeadersMiddleware:
    """Stub: passes requests through unchanged. Replaced in Green."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        await self.app(scope, receive, send)
