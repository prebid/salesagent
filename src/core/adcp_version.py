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

A pin is validated by MEMBERSHIP in the supported major set (per
``get_adcp_capabilities.mdx``: "the seller validates against its
``major_versions`` and returns ``VERSION_UNSUPPORTED`` if not in range") —
majors below the native major are rejected exactly like majors above it,
because this build serves 3.x-shaped responses only: the legacy
``src/core/version_compat`` layer covers a single tool on a subset of
transports and is not a genuine cross-protocol 2.x contract, so advertising
``major_versions: [3]`` while accepting a 2-pin would be self-inconsistent.
Unpinned legacy clients are unaffected (no pin → nothing to validate).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from functools import cache
from typing import Any

import adcp

from src.core.exceptions import AdCPConfigurationError, AdCPVersionUnsupportedError

logger = logging.getLogger(__name__)


@cache
def _spec_major_minor() -> tuple[int, int]:
    """Parse ``(major, minor)`` from the SDK spec pin, typed-erroring on garbage.

    Both ``adcp_major_version()`` and ``supported_adcp_versions()`` derive from
    this single parse so they agree on the SDK version and fail the same way.
    The pin (``adcp.get_adcp_spec_version()``, e.g. ``"3.1.0-beta.3"``) is a
    deploy-time constant, but this runs on the buyer request path
    (``validate_adcp_version_pins`` -> ``_supported_majors`` ->
    ``supported_adcp_versions``). A malformed pin is a broken *seller*
    deployment, not a buyer version mismatch: surface it as a typed
    :class:`AdCPConfigurationError` (500, terminal) — the same contract every
    other server-side failure honors — rather than letting a bare ``ValueError``
    (int cast or tuple-unpack) escape as an untyped 500.
    """
    raw = adcp.get_adcp_spec_version()
    parts = raw.split(".")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError) as exc:
        raise AdCPConfigurationError(
            f"AdCP SDK spec version {raw!r} is malformed; expected MAJOR.MINOR(.PATCH...)."
        ) from exc


def adcp_major_version() -> int:
    """AdCP major version this build speaks, from the SDK spec pin.

    The major is the leading component of the SDK's spec version string
    (e.g. ``"3.1.0-beta.3"`` -> ``3``), so a spec bump is reflected here with
    no code change.
    """
    return _spec_major_minor()[0]


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
    major, minor = _spec_major_minor()
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


# Cap on the echoed pin value: it is buyer-controlled and unbounded on the
# wire, and it is reflected into the error details and boundary logs.
_ECHO_MAX_LEN = 64


def _version_unsupported_error(
    echo_field: str,
    echo_value: Any,
    claimed_major: int,
    *,
    context: Any = None,
) -> AdCPVersionUnsupportedError:
    """Build the VERSION_UNSUPPORTED error with the spec-required details payload.

    ``details`` follows ``error-details/version-unsupported.json``:
    ``supported_versions`` (REQUIRED), the deprecated ``supported_majors``
    (servers SHOULD emit both through 3.x per the schema), the advisory
    ``build_version``, and the buyer's echoed pin (truncated — the raw value is
    attacker-sized). The request's ``context`` object rides on the error so the
    envelope echoes it back (error-compliance storyboard grades
    ``field_present: context`` and an unchanged ``correlation_id`` on error
    responses).
    """
    supported = supported_adcp_versions()
    if isinstance(echo_value, str) and len(echo_value) > _ECHO_MAX_LEN:
        echo_value = echo_value[:_ECHO_MAX_LEN]
    return AdCPVersionUnsupportedError(
        f"AdCP major version {claimed_major} is not supported; "
        f"this agent speaks major(s) {', '.join(str(m) for m in _supported_majors())}.",
        details={
            echo_field: echo_value,
            "supported_versions": list(supported),
            "supported_majors": _supported_majors(),
            "build_version": adcp_build_version(),
        },
        suggestion="Re-pin adcp_version to a supported_versions entry and retry the request.",
        context=context if isinstance(context, dict) else None,
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
    """Reject a request that pins an AdCP major version this build does not speak.

    AdCP version negotiation (``core/version-envelope.json``): SDK clients pin
    ``adcp_version`` (release string, e.g. ``"4.0"``) and/or the deprecated
    ``adcp_major_version`` (int) on every request. A pin whose major is NOT IN
    the supported major set — above or below — is a protocol this build does
    not serve: raise :class:`AdCPVersionUnsupportedError` (wire code
    ``VERSION_UNSUPPORTED``) carrying the spec-required ``supported_versions``
    details and echoing the request's ``context``. Absent means no version
    claim, so there is nothing to reject.

    Same-major pins downshift to the release this build serves (no error) —
    see the module docstring for the spec grounding.
    """
    supported_majors = set(_supported_majors())
    request_context = params.get("context")
    for field in ("adcp_version", "adcp_major_version"):
        claimed = params.get(field)
        if claimed is None:
            continue
        claimed_major = _claimed_major(claimed)
        if claimed_major is None:
            # Schema-invalid pin (e.g. "banana"): no negotiable major, so it is
            # tolerated and stripped with the negotiation envelope rather than
            # misreported as VERSION_UNSUPPORTED. Log it — a silently-dropped
            # malformed pin is otherwise invisible at triage time (#1546).
            logger.debug("Tolerating unparseable AdCP version pin %s=%r (no negotiable major)", field, claimed)
            continue
        if claimed_major not in supported_majors:
            raise _version_unsupported_error(field, claimed, claimed_major, context=request_context)
