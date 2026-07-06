"""Single source of truth for the AdCP protocol version this build speaks.

The advertised AdCP major version is derived from the installed ``adcp`` SDK
pin (see docs/adcp-spec-version.md) rather than hardcoded, so bumping the SDK
propagates automatically to everything that advertises or negotiates the
protocol version. A cross-major bump is guarded by
``tests/unit/test_adcp_spec_version.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from typing import Any

import adcp

from src.core.exceptions import AdCPVersionUnsupportedError


@cache
def adcp_major_version() -> int:
    """AdCP major version this build speaks, from the SDK spec pin.

    The major is the leading component of the SDK's spec version string
    (e.g. ``"3.1.0-beta.3"`` -> ``3``), so a spec bump is reflected here with
    no code change.
    """
    return int(adcp.get_adcp_spec_version().split(".", 1)[0])


def validate_adcp_major_version(params: Mapping[str, Any]) -> None:
    """Reject a request that pins an unsupported AdCP major version.

    AdCP version negotiation: SDK clients send ``adcp_major_version`` (int) on
    every request. If present and not the major this build speaks, raise
    :class:`AdCPVersionUnsupportedError` (wire code ``VERSION_UNSUPPORTED``) per
    the spec's version-negotiation contract. Absent means no version claim, so
    there is nothing to reject. A non-integer value is left for schema
    validation to report rather than masked as an unsupported version.
    """
    claimed = params.get("adcp_major_version")
    if claimed is None:
        return
    try:
        claimed_major = int(claimed)
    except (TypeError, ValueError):
        return
    supported = adcp_major_version()
    if claimed_major != supported:
        raise AdCPVersionUnsupportedError(
            f"AdCP major version {claimed_major} is not supported; this agent speaks major {supported}."
        )
