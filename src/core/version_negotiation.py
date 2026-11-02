"""AdCP protocol version negotiation (#1592 C4, salesagent-rldj).

Single source of truth for the versions/majors this seller advertises and
accepts, derived from the pinned SDK spec version -- never a literal/
hardcoded list duplicated across response-construction sites (mirrors
``resolve_supported_billing()`` in ``src/core/billing_policy.py``).

"""

from __future__ import annotations

import adcp

#: adcp.get_adcp_spec_version() returns the full pinned semver (e.g. "3.1.1",
#: MAJOR.MINOR.PATCH). The v3.1.1 SupportedVersion wire type requires RELEASE
#: precision (MAJOR.MINOR only, pattern ^\d+\.\d+(-...)?$) -- passing the raw
#: 3-part string through would violate that pattern and risk the SDK model
#: rejecting construction. Strip to the first two dot-separated components.
_FULL_SPEC_VERSION: str = adcp.get_adcp_spec_version()

#: The release THIS BUILD SERVES, which is what a response echoes. Named separately from the
#: advertisement below because they answer different questions -- "what did you serve?" versus
#: "what do you speak?" -- and reading the served release as ``SUPPORTED_ADCP_VERSIONS[0]``
#: would encode the accident that this seller currently speaks exactly one release.
SERVED_ADCP_VERSION: str = ".".join(_FULL_SPEC_VERSION.split(".")[:2])


def _major_of(release: str) -> int:
    """The major number a release-precision version names: ``"3.1"`` -> ``3``."""
    return int(release.split(".", 1)[0])


SUPPORTED_ADCP_VERSIONS: list[str] = [SERVED_ADCP_VERSION]
SUPPORTED_ADCP_MAJORS: list[int] = [_major_of(SERVED_ADCP_VERSION)]


def _is_unsupported(adcp_version: str | None, adcp_major_version: int | None) -> bool:
    """Whether this pair of pins names something this seller does not speak.

    Each pin the buyer SENDS is a constraint, and they are judged INDEPENDENTLY -- an
    unsupported release is a rejection whatever the major says, and the reverse. Reading
    them as alternatives ("either one matching is enough") accepted a request pinning
    ``adcp_version: "99.0"`` alongside a supported major, which is a release this build
    cannot serve. An absent pin constrains nothing.
    """
    if adcp_version is not None and adcp_version not in SUPPORTED_ADCP_VERSIONS:
        return True
    if adcp_major_version is not None and adcp_major_version not in SUPPORTED_ADCP_MAJORS:
        return True
    # Both pins are supported on their own. They can still CONTRADICT each other -- a buyer
    # asking for release "3.1" under major 4 names no release that exists. Unreachable while
    # this seller speaks a single release (a supported release and a supported major then
    # always agree), and reachable the moment the supported set grows, which is exactly when
    # nobody would think to add the check.
    return adcp_version is not None and adcp_major_version is not None and _major_of(adcp_version) != adcp_major_version


def negotiate_adcp_version(adcp_version: str | None, adcp_major_version: int | None) -> None:
    """Reject a buyer's version/major pin outside what this seller supports.

    No-op (silent accept) when the caller sent no pin at all, or when every pin it did send
    is one this seller speaks. Raises ``AdCPVersionUnsupportedError`` -> wire code
    ``VERSION_UNSUPPORTED`` otherwise.

    ``recovery`` is DERIVED from the pinned ``enums/error-code.json``, which puts
    ``VERSION_UNSUPPORTED`` at ``correctable`` -- the buyer re-pins and retries. The
    universal error-compliance storyboard's ``expected:`` prose says ``fatal``, but that
    block is narrative: its graded ``validations:`` check the code and the echoed context,
    never the recovery. The metadata says ``correctable`` in every published bundle through
    3.2.0-rc.1, so the prose is an upstream contradiction rather than a coming change.
    """
    if not _is_unsupported(adcp_version, adcp_major_version):
        return

    from src.core.errors.details import VersionUnsupportedDetails
    from src.core.exceptions import AdCPVersionUnsupportedError
    from src.core.version import get_version

    # build_version is ADVISORY ONLY (incident-triage aid) -- buyers MUST NOT
    # use it for negotiation (v3.1.1 error-details/version-unsupported.json).
    # The actual negotiation outcome above depends solely on SUPPORTED_ADCP_VERSIONS.
    raise AdCPVersionUnsupportedError(
        details=VersionUnsupportedDetails(
            supported_versions=list(SUPPORTED_ADCP_VERSIONS),
            # A buyer rejected on a MAJOR pin needs the majors to retry against; without
            # this the answer names only releases, which is not what they sent.
            supported_majors=list(SUPPORTED_ADCP_MAJORS),
            build_version=get_version(),
            adcp_version=adcp_version,
            adcp_major_version=adcp_major_version,
        ),
    )
