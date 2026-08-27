"""AdCP protocol version negotiation (#1592 C4, salesagent-rldj).

Single source of truth for the versions/majors this seller advertises and
accepts, derived from the pinned SDK spec version -- never a literal/
hardcoded list duplicated across response-construction sites (mirrors
``resolve_supported_billing()`` in ``src/core/billing_policy.py``).

Do NOT confuse with ``src/core/version_compat.py`` -- that module handles the
unrelated v2/v3 wire-payload compatibility shims, not protocol version
negotiation.
"""

from __future__ import annotations

import adcp

#: adcp.get_adcp_spec_version() returns the full pinned semver (e.g. "3.1.1",
#: MAJOR.MINOR.PATCH). The v3.1.1 SupportedVersion wire type requires RELEASE
#: precision (MAJOR.MINOR only, pattern ^\d+\.\d+(-...)?$) -- passing the raw
#: 3-part string through would violate that pattern and risk the SDK model
#: rejecting construction. Strip to the first two dot-separated components.
_FULL_SPEC_VERSION: str = adcp.get_adcp_spec_version()
_RELEASE_VERSION: str = ".".join(_FULL_SPEC_VERSION.split(".")[:2])

SUPPORTED_ADCP_VERSIONS: list[str] = [_RELEASE_VERSION]
SUPPORTED_ADCP_MAJORS: list[int] = [int(_RELEASE_VERSION.split(".")[0])]


def negotiate_adcp_version(adcp_version: str | None, adcp_major_version: int | None) -> None:
    """Reject a buyer's version/major pin outside what this seller supports.

    No-op (silent accept) when the caller sent no pin at all, or when the pin
    matches a supported release. Raises ``AdCPVersionUnsupportedError`` ->
    wire code ``VERSION_UNSUPPORTED`` otherwise.
    """
    if adcp_version is None and adcp_major_version is None:
        return
    if adcp_version is not None and adcp_version in SUPPORTED_ADCP_VERSIONS:
        return
    if adcp_major_version is not None and adcp_major_version in SUPPORTED_ADCP_MAJORS:
        return

    from src.core.exceptions import AdCPVersionUnsupportedError
    from src.core.version import get_version

    # build_version is ADVISORY ONLY (incident-triage aid) -- buyers MUST NOT
    # use it for negotiation (v3.1.1 error-details/version-unsupported.json).
    # The actual negotiation outcome above depends solely on SUPPORTED_ADCP_VERSIONS.
    raise AdCPVersionUnsupportedError(
        f"adcp_version={adcp_version!r} / adcp_major_version={adcp_major_version!r} "
        f"is not supported; this seller speaks {SUPPORTED_ADCP_VERSIONS}",
        details={"supported_versions": list(SUPPORTED_ADCP_VERSIONS), "build_version": get_version()},
        suggestion=f"Re-pin adcp_version to one of {SUPPORTED_ADCP_VERSIONS} and retry",
    )
