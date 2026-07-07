"""Single source of truth for the AdCP protocol version this build speaks.

The advertised AdCP version is derived from the installed ``adcp`` SDK pin
(see docs/adcp-spec-version.md) rather than hardcoded, so bumping the SDK
propagates automatically to everything that advertises or negotiates the
protocol version. A cross-major bump is guarded by
``tests/unit/test_adcp_spec_version.py``.

Spec grounding (v3.1.0-beta.3):
    - ``static/schemas/source/core/version-envelope.json`` — buyers pin either
      ``adcp_version`` (release-precision string, e.g. ``"4.0"``) or the
      deprecated ``adcp_major_version`` (int). The seller "validates against
      its supported_versions and returns VERSION_UNSUPPORTED on cross-major
      mismatch, or downshifts to the highest supported release within the same
      major".
    - ``static/schemas/source/error-details/version-unsupported.json`` — the
      VERSION_UNSUPPORTED details payload REQUIRES ``supported_versions[]``
      (minItems 1), echoes the buyer's pin (version envelope via allOf), and
      may carry the deprecated ``supported_majors[]`` plus the advisory
      ``build_version`` (which buyers MUST NOT use for negotiation).

Pre-3.0 majors are deliberately NOT rejected: the ``src/core/version_compat``
layer (``needs_v2_compat``) still serves pre-3.0 buyers with v2-compat
serialization, so a pre-3.0 pin is a version this seller honors — rejecting it
would break that deployed compat contract. Only majors ABOVE the native major
(protocol releases this build cannot speak) trigger VERSION_UNSUPPORTED.
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


def supported_adcp_versions() -> tuple[str, ...]:
    """Release-precision AdCP versions this build speaks, from the SDK spec pin.

    The wire value is ``MAJOR.MINOR`` (release precision per
    ``core/version-envelope.json``), derived from the SDK's full-semver spec
    pin (e.g. ``"3.1.0-beta.3"`` -> ``("3.1",)``). Pre-release/patch segments
    are spec-text revisions, not separately negotiated releases, so they are
    not advertised as distinct entries.

    This is the authoritative re-pin list carried in VERSION_UNSUPPORTED error
    details (``supported_versions`` is REQUIRED there with minItems 1).
    """
    major, minor = adcp.get_adcp_spec_version().split(".", 2)[:2]
    return (f"{major}.{minor}",)


def adcp_build_version() -> str:
    """Full-semver build identifier of this deployment's AdCP spec pin.

    Advisory only (incident triage) — buyers MUST NOT use it for negotiation,
    per ``error-details/version-unsupported.json``.
    """
    return adcp.get_adcp_spec_version()


def _supported_majors() -> list[int]:
    """Major versions covered by ``supported_adcp_versions()``, ascending."""
    return sorted({int(v.split(".", 1)[0]) for v in supported_adcp_versions()})


def _version_unsupported_error(echo_field: str, echo_value: Any, claimed_major: int) -> AdCPVersionUnsupportedError:
    """Build the VERSION_UNSUPPORTED error with the spec-required details payload.

    ``details`` follows ``error-details/version-unsupported.json``:
    ``supported_versions`` (REQUIRED), the deprecated ``supported_majors``
    (servers SHOULD emit both through 3.x per the schema), the advisory
    ``build_version``, and the buyer's echoed pin.
    """
    supported = supported_adcp_versions()
    return AdCPVersionUnsupportedError(
        f"AdCP major version {claimed_major} is not supported; this agent speaks major {max(_supported_majors())}.",
        details={
            echo_field: echo_value,
            "supported_versions": list(supported),
            "supported_majors": _supported_majors(),
            "build_version": adcp_build_version(),
        },
        suggestion="Re-pin adcp_version to a supported_versions entry and retry the request.",
    )


def _claimed_major(value: Any) -> int | None:
    """Parse the major component from a version pin (int or release string).

    Returns None when the value carries no parseable major. An unparseable pin
    cannot be negotiated; because the negotiation fields are stripped from the
    request before any schema sees them, it is tolerated rather than rejected
    (masking a malformed value as VERSION_UNSUPPORTED would misreport it as a
    version mismatch).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.split(".", 1)[0]
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate_adcp_version_pins(params: Mapping[str, Any]) -> None:
    """Reject a request that pins an AdCP version this build cannot speak.

    AdCP version negotiation (``core/version-envelope.json``): SDK clients pin
    ``adcp_version`` (release string, e.g. ``"4.0"``) and/or the deprecated
    ``adcp_major_version`` (int) on every request. A pin whose major is ABOVE
    the native major is a protocol this build cannot serve — raise
    :class:`AdCPVersionUnsupportedError` (wire code ``VERSION_UNSUPPORTED``)
    carrying the spec-required ``supported_versions`` details. Absent means no
    version claim, so there is nothing to reject.

    Same-major pins downshift to the release this build serves (no error), and
    pre-3.0 majors are honored via the version_compat layer — see the module
    docstring for the spec grounding of both.
    """
    highest_supported = max(_supported_majors())
    for field in ("adcp_version", "adcp_major_version"):
        claimed = params.get(field)
        if claimed is None:
            continue
        claimed_major = _claimed_major(claimed)
        if claimed_major is not None and claimed_major > highest_supported:
            raise _version_unsupported_error(field, claimed, claimed_major)
