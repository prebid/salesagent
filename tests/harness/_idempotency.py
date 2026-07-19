"""Shared idempotency-key defaults for mutating-request test harnesses."""

from __future__ import annotations

import uuid
from typing import Any

# Pass this sentinel when a test must put no idempotency_key on the wire.
OMIT_IDEMPOTENCY_KEY: Any = object()


def fresh_idempotency_key(prefix: str = "test-key") -> str:
    """Return a fresh spec-valid key with an optional diagnostic prefix."""
    return f"{prefix}-{uuid.uuid4().hex}"


def ensure_idempotency_key(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Supply a fresh valid key unless the test explicitly controls omission/value."""
    if kwargs.get("idempotency_key") is OMIT_IDEMPOTENCY_KEY:
        kwargs.pop("idempotency_key")
    else:
        kwargs.setdefault("idempotency_key", fresh_idempotency_key())
    return kwargs
