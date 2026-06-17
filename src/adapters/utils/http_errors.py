"""Shared error-wrapping for adapter HTTP writes.

Every adapter HTTP write whose failure can reach the buyer must surface a typed
``AdCPError`` whose recovery class matches the failure. A bare ``requests``
exception escapes the adapter to the transport boundary, where
``normalize_to_adcp_error`` wraps it in the base ``AdCPError`` ->
``INTERNAL_ERROR`` / recovery ``terminal`` — so the buyer escalates to a human
even for a transient outage that should be retried, or retries forever on a
client error it should fix.

This contextmanager is the single seam for that mapping: it routes a
response-bearing failure (a ``raise_for_status()`` HTTPError) through
``adcp_error_for_http_status`` (the shared status->recovery table: 429/5xx ->
transient, other 4xx -> correctable) and maps a response-less failure (timeout,
connection error) to a transient ``AdCPAdapterError``. Adapter write paths wrap
their HTTP call in it so every path reports the same recovery for the same status.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import requests

from src.core.exceptions import AdCPAdapterError, adcp_error_for_http_status


@contextmanager
def wrap_request_errors() -> Iterator[None]:
    """Re-raise a ``requests`` transport failure as the spec-recovery-correct typed error.

    A response-bearing failure (a ``raise_for_status()`` HTTPError) is mapped by status
    through the shared ``adcp_error_for_http_status`` table (429/5xx -> transient, other
    4xx -> correctable); a response-less failure (timeout, connection error) has no
    status and is a transient ``AdCPAdapterError``.
    """
    try:
        yield
    except requests.exceptions.RequestException as e:
        response = getattr(e, "response", None)
        status = getattr(response, "status_code", None)
        if status is not None:
            raise adcp_error_for_http_status(int(status), str(e)) from e
        raise AdCPAdapterError(str(e)) from e
