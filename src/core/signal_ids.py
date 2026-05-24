"""Helpers for AdCP-safe signal identifiers."""

from __future__ import annotations

import re

_ADCP_SIGNAL_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def adcp_safe_signal_id(signal_id: str) -> str:
    """Project a stored signal_id to AdCP's wire-safe identifier pattern."""
    safe = _ADCP_SIGNAL_ID_UNSAFE.sub("_", signal_id)
    return safe or "signal"
