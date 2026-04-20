"""L0-14 STUB (Red commit) — real implementation lands in Green commit.

Canonical spec: flask-to-fastapi-foundation-modules.md §11.11; Critical
Invariant #3 (Accept-aware AdCPError handler).
"""

from __future__ import annotations

from typing import Literal

from starlette.requests import Request


def _response_mode(request: Request) -> Literal["html", "json"]:
    """STUB — always returns 'json' (trivially wrong for admin HTML browsers)."""
    return "json"
