"""Shared error-wrapping for adapter HTTP writes.

Every adapter HTTP write whose failure can reach the buyer must surface a typed
``AdCPAdapterError`` (wire code ``SERVICE_UNAVAILABLE``, recovery ``transient``).
A bare ``requests`` exception escapes the adapter to the transport boundary,
where ``normalize_to_adcp_error`` wraps it in the base ``AdCPError`` ->
``INTERNAL_ERROR`` / recovery ``terminal``. Per the AdCP recovery taxonomy a
buyer agent then escalates to a human instead of retrying a transient outage —
so the same ad-server outage tells the buyer "retry" on one write path and
"don't retry" on another.

This contextmanager is the single source of the
``RequestException -> AdCPAdapterError`` mapping. Adapter write paths wrap their
HTTP call in it so every path reports the same recovery for the same outage.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import requests

from src.core.exceptions import AdCPAdapterError


@contextmanager
def wrap_request_errors() -> Iterator[None]:
    """Re-raise any ``requests`` transport failure as a transient ``AdCPAdapterError``."""
    try:
        yield
    except requests.exceptions.RequestException as e:
        raise AdCPAdapterError(str(e)) from e
