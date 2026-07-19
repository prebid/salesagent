"""Single source of truth for the AdCP protocol version this build speaks.

The advertised AdCP version is derived from the installed ``adcp`` SDK pin
(see docs/adcp-spec-version.md) rather than hardcoded, so bumping the SDK
propagates automatically to everything that advertises or negotiates the
protocol version. A cross-major bump is guarded by
``tests/unit/test_adcp_spec_version.py``.

Spec grounding (v3.1.1):
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

Release pins follow ``docs/reference/versioning.mdx`` exactly: exact matches
win; a stable same-major pin may downshift to the highest stable supported
release not newer than the buyer's pin; sub-min and cross-major pins reject;
and prerelease pins match exactly rather than range-resolving. The deprecated
integer major pin remains a membership check through 3.x. Unpinned legacy
clients are unaffected (no pin → nothing to validate).

The pinned 3.1 migration table grades response ``adcp_version`` echo only as
advisory. This module resolves request pins but does not claim that every
response transport currently emits the selected release; universal response
echo remains an explicit residual for a later protocol-wide response change.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any

import adcp

from src.core.exceptions import AdCPConfigurationError, AdCPValidationError, AdCPVersionUnsupportedError
from src.core.version import get_version

_RELEASE_PIN_RE = re.compile(r"^(?P<major>[0-9]+)\.(?P<minor>[0-9]+)(?:-(?P<prerelease>[a-zA-Z0-9.-]+))?$")
_SEMVER_PRERELEASE_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_BUILD_VERSION_RE = re.compile(
    rf"^(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)"
    rf"(?:-(?P<prerelease>{_SEMVER_PRERELEASE_IDENTIFIER}(?:\.{_SEMVER_PRERELEASE_IDENTIFIER})*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_TEST_POLICY_LEASE_RE = re.compile(r"^[0-9A-Za-z_-]{16,128}$")


@dataclass(frozen=True)
class _ReleasePin:
    """Parsed release-precision AdCP wire value."""

    raw: str
    major: str
    minor: str
    prerelease: str | None


@dataclass(frozen=True)
class _TestingVersionPolicy:
    """Atomic live-server policy used only by secret-gated E2E setup."""

    lease_id: str
    supported_versions: tuple[str, ...]
    build_version: str


_testing_policy_lock = threading.Lock()
_testing_policy: _TestingVersionPolicy | None = None


def _active_testing_policy() -> _TestingVersionPolicy | None:
    """Return the immutable test policy snapshot, never outside test mode."""
    if os.environ.get("ADCP_TESTING") != "true":
        return None
    with _testing_policy_lock:
        return _testing_policy


@cache
def _spec_release_components() -> tuple[int, int, str | None]:
    """Parse ``(major, minor, prerelease)`` from the SDK spec pin, typed-erroring on garbage.

    Both ``adcp_major_version()`` and ``supported_adcp_versions()`` derive from
    this single parse. ``adcp_build_version()`` is intentionally independent:
    it identifies the Sales Agent deployment lineage, not the AdCP spec pin.
    The prerelease is retained so the advertised release-precision wire value
    preserves it (a prerelease pin such as ``"4.2.0-rc.1"`` -> ``"4.2-rc.1"``);
    only PATCH is dropped. The pin (``adcp.get_adcp_spec_version()``, e.g.
    ``"3.1.1"``) is a deploy-time constant, but this runs on the buyer request path
    (``validate_adcp_version_pins`` -> ``_supported_majors`` ->
    ``supported_adcp_versions``). A malformed pin is a broken *seller*
    deployment, not a buyer version mismatch: surface it as a typed
    :class:`AdCPConfigurationError` (500, terminal) — the same contract every
    other server-side failure honors — rather than letting a bare ``ValueError``
    (int cast or tuple-unpack) escape as an untyped 500.
    """
    _raw, major, minor, prerelease = _validate_full_semver(
        adcp.get_adcp_spec_version(),
        subject="AdCP SDK spec version",
    )
    return major, minor, prerelease


def _validate_full_semver(
    raw: Any,
    *,
    subject: str,
) -> tuple[str, int, int, str | None]:
    """Validate a complete semantic version and return its release parts.

    Returns ``(raw, major, minor, prerelease)``. The prerelease segment is
    carried through because the release-precision wire value PRESERVES it
    (a prerelease pin such as ``"4.2.0-rc.1"`` -> ``"4.2-rc.1"``) per
    ``core/version-envelope.json``: only the PATCH component is dropped for the
    wire; the prerelease is not.
    """
    try:
        if not isinstance(raw, str):
            raise TypeError(f"{subject} must be a string")
        match = _BUILD_VERSION_RE.fullmatch(raw)
        if match is None:
            raise ValueError(f"{subject} must be full semantic version")
        return raw, int(match.group("major")), int(match.group("minor")), match.group("prerelease")
    except (TypeError, ValueError) as exc:
        raise AdCPConfigurationError(
            f"{subject} {raw!r} is malformed; expected a full semantic version MAJOR.MINOR.PATCH."
        ) from exc


def _release_precision_wire_value(major: int, minor: int, prerelease: str | None) -> str:
    """Convert a full-semver release to its release-precision wire value.

    Per ``core/version-envelope.json`` (v3.1.1): the wire value is
    ``MAJOR.MINOR`` with the PATCH component dropped but any prerelease segment
    PRESERVED — a prerelease pin ``"4.2.0-rc.1"`` normalizes to ``"4.2-rc.1"``, NOT ``"4.2"``.
    The spec is explicit that full-semver meta-field values are "NOT valid wire
    values". This is the single shared conversion behind ``supported_adcp_versions()``,
    from which advertisement, request validation, capabilities, and the
    VERSION_UNSUPPORTED error details all derive.
    """
    base = f"{major}.{minor}"
    return f"{base}-{prerelease}" if prerelease else base


def adcp_major_version() -> int:
    """AdCP major version this build speaks, from the SDK spec pin.

    The major is the leading component of the SDK's spec version string
    (e.g. ``"3.1.1"`` -> ``3``), so a spec bump is reflected here with
    no code change.
    """
    return _spec_release_components()[0]


def supported_adcp_versions() -> tuple[str, ...]:
    """Release-precision AdCP versions this build speaks, from the SDK spec pin.

    The wire value is release precision per ``core/version-envelope.json``,
    derived from the SDK's full-semver spec pin by dropping the PATCH component
    but PRESERVING any prerelease segment (a prerelease pin ``"4.2.0-rc.1"`` ->
    ``("4.2-rc.1",)``). The spec is explicit that full-semver meta-field
    values are "NOT valid wire values", and that the prerelease must be kept —
    dropping it to a bare ``"3.1"`` made this seller reject a correct
    ``"3.1-beta.3"`` buyer pin with VERSION_UNSUPPORTED. Only the PATCH digit,
    which is never negotiated, is discarded.

    This is the authoritative re-pin list carried in VERSION_UNSUPPORTED error
    details (``supported_versions`` is REQUIRED there with minItems 1).
    """
    testing_policy = _active_testing_policy()
    if testing_policy is not None:
        return testing_policy.supported_versions

    major, minor, prerelease = _spec_release_components()
    return (_release_precision_wire_value(major, minor, prerelease),)


def adcp_build_version() -> str:
    """Full-semver build identifier of the Sales Agent deployment lineage.

    ``supported_versions`` identifies the AdCP spec releases this seller can
    negotiate. ``build_version`` instead identifies the seller implementation
    for incident triage and buyers MUST NOT use it for negotiation, per the
    capabilities and VERSION_UNSUPPORTED schemas.
    """
    testing_policy = _active_testing_policy()
    if testing_policy is not None:
        return testing_policy.build_version
    raw, _major, _minor, _prerelease = _validate_full_semver(
        get_version(),
        subject="Sales Agent build version",
    )
    return raw


def _parse_release_pin(value: Any) -> _ReleasePin | None:
    """Parse a schema-valid release-precision wire value."""
    if not isinstance(value, str):
        return None
    match = _RELEASE_PIN_RE.fullmatch(value)
    if match is None:
        return None
    return _ReleasePin(
        raw=value,
        major=_canonical_numeric_component(match.group("major")),
        minor=_canonical_numeric_component(match.group("minor")),
        prerelease=match.group("prerelease"),
    )


def _canonical_numeric_component(value: str) -> str:
    """Normalize digits without converting attacker-sized input to ``int``."""
    return value.lstrip("0") or "0"


def _numeric_component_key(value: str) -> tuple[int, str]:
    """Comparison key for an arbitrarily large canonical decimal component."""
    return len(value), value


def _parse_supported_releases(raw_supported: tuple[Any, ...]) -> tuple[_ReleasePin, ...]:
    """Validate one complete seller release snapshot."""
    if not raw_supported:
        raise AdCPConfigurationError(
            "The seller advertises no supported AdCP releases; supported_versions must contain at least one value."
        )

    parsed: list[_ReleasePin] = []
    for value in raw_supported:
        release = _parse_release_pin(value)
        if release is None:
            raise AdCPConfigurationError(
                f"Seller-supported AdCP release {value!r} is malformed; expected release precision MAJOR.MINOR."
            )
        parsed.append(release)
    return tuple(parsed)


def _supported_releases() -> tuple[_ReleasePin, ...]:
    """Return validated seller releases or raise a typed configuration error."""
    return _parse_supported_releases(supported_adcp_versions())


def _supported_majors(releases: tuple[_ReleasePin, ...] | None = None) -> list[int]:
    """Major versions covered by ``supported_adcp_versions()``, ascending."""
    releases = releases if releases is not None else _supported_releases()
    try:
        majors = {int(release.major) for release in releases}
    except ValueError as exc:
        raise AdCPConfigurationError(
            "A seller-supported AdCP major is too large to emit as the deprecated supported_majors integer."
        ) from exc
    if any(major < 1 for major in majors):
        raise AdCPConfigurationError("Seller-supported AdCP majors must be positive integers.")
    return sorted(majors)


def _install_testing_version_policy(
    *,
    lease_id: str,
    supported_versions: tuple[Any, ...],
    build_version: Any,
) -> bool:
    """Atomically install a validated E2E policy snapshot for one lease.

    This is a server-side setup seam for the separate-process ``e2e_rest``
    harness, not a buyer-controlled protocol input. The HTTP control route
    independently requires a per-run secret, and this defense-in-depth guard
    prevents direct use outside ``ADCP_TESTING=true``. A different active lease
    is never overwritten, so a stale scenario cannot corrupt another one.

    Returns ``False`` on lease conflict. Invalid candidate snapshots raise
    :class:`AdCPConfigurationError` before the current snapshot is touched.
    """
    if os.environ.get("ADCP_TESTING") != "true":
        raise PermissionError("AdCP version-policy controls are available only in testing mode")
    if not isinstance(lease_id, str) or _TEST_POLICY_LEASE_RE.fullmatch(lease_id) is None:
        raise AdCPConfigurationError("The testing version-policy lease_id is malformed.")

    releases = _parse_supported_releases(supported_versions)
    _supported_majors(releases)
    if not isinstance(build_version, str) or _BUILD_VERSION_RE.fullmatch(build_version) is None:
        raise AdCPConfigurationError("The testing version-policy build_version must be a full semantic version.")

    candidate = _TestingVersionPolicy(
        lease_id=lease_id,
        supported_versions=tuple(release.raw for release in releases),
        build_version=build_version,
    )
    global _testing_policy
    with _testing_policy_lock:
        if _testing_policy is not None and _testing_policy.lease_id != lease_id:
            return False
        _testing_policy = candidate
    return True


def _reset_testing_version_policy(*, lease_id: str) -> bool:
    """Clear the active E2E snapshot only when the caller owns its lease."""
    if os.environ.get("ADCP_TESTING") != "true":
        raise PermissionError("AdCP version-policy controls are available only in testing mode")
    global _testing_policy
    with _testing_policy_lock:
        if _testing_policy is None:
            return True
        if _testing_policy.lease_id != lease_id:
            return False
        _testing_policy = None
    return True


# Cap for human-readable rendering of a buyer-controlled, unbounded wire pin.
# Protocol details retain the complete schema-valid value (see below).
_ECHO_MAX_LEN = 64


def _version_unsupported_error(
    params: Mapping[str, Any],
    *,
    supported_releases: tuple[_ReleasePin, ...] | None = None,
    context: Any = None,
) -> AdCPVersionUnsupportedError:
    """Build the VERSION_UNSUPPORTED error with the spec-required details payload.

    ``details`` follows ``error-details/version-unsupported.json``:
    ``supported_versions`` (REQUIRED), the deprecated ``supported_majors``
    (servers SHOULD emit both through 3.x per the schema), the advisory
    ``build_version``, and the buyer's complete schema-valid pin. Only the
    human-readable message bounds that attacker-controlled value. The request's
    ``context`` object rides on the error so the envelope echoes it back
    (error-compliance storyboard grades
    ``field_present: context`` and an unchanged ``correlation_id`` on error
    responses).
    """
    releases = supported_releases if supported_releases is not None else _supported_releases()
    supported = [release.raw for release in releases]
    # Preserve schema-valid pin values in the protocol details. The wire schema
    # does not impose a maxLength, and truncating a long release at an arbitrary
    # character can remove its required dot and make our own error payload
    # invalid. Only the human-readable message/log rendering is bounded.
    echoed_pins = {field: params[field] for field in ("adcp_version", "adcp_major_version") if field in params}
    rendered_pins = ", ".join(f"{field}={_truncate_echo(value)!r}" for field, value in echoed_pins.items())
    return AdCPVersionUnsupportedError(
        f"AdCP version pin {rendered_pins} cannot be served; this agent supports release(s) {', '.join(supported)}.",
        details={
            **echoed_pins,
            "supported_versions": supported,
            "supported_majors": _supported_majors(releases),
            "build_version": adcp_build_version(),
        },
        suggestion="Re-pin adcp_version to a supported_versions entry and retry the request.",
        context=context if isinstance(context, dict) else None,
    )


def _truncate_echo(value: Any) -> Any:
    """Cap a buyer-controlled pin only for human-readable error/log rendering."""
    if isinstance(value, str) and len(value) > _ECHO_MAX_LEN:
        return value[:_ECHO_MAX_LEN]
    return value


def _version_malformed_error(field: str, echo_value: Any, *, context: Any = None) -> AdCPValidationError:
    """Build the VALIDATION_ERROR for a pin that is present but not parseable.

    ``version-envelope.json`` constrains ``adcp_version`` to the release pattern
    ``^\\d+\\.\\d+(-[a-zA-Z0-9.-]+)?$`` and types ``adcp_major_version`` as an
    integer; a value violating either is a malformed request, not an omitted pin.
    """
    echo = _truncate_echo(echo_value)
    return AdCPValidationError(
        f"AdCP version pin {field}={echo!r} is malformed; expected a release-precision "
        f'version like "3.1" for adcp_version, or an integer for adcp_major_version.',
        field=field,
        details={field: echo},
        suggestion="Send a well-formed adcp_version (MAJOR.MINOR) or omit the field.",
        context=context if isinstance(context, dict) else None,
    )


def _parse_major_pin(value: Any) -> int | None:
    """Parse the deprecated integer pin with its schema bounds."""
    if type(value) is not int or not 1 <= value <= 99:
        return None
    return value


def _resolve_release_pin(
    claimed: _ReleasePin,
    supported: tuple[_ReleasePin, ...],
) -> _ReleasePin | None:
    """Resolve a release pin to the exact or highest eligible seller release."""
    exact = next((release for release in supported if release.raw == claimed.raw), None)
    if exact is not None:
        return exact
    if claimed.prerelease is not None:
        return None

    downshift_candidates = (
        release
        for release in supported
        if release.prerelease is None
        and release.major == claimed.major
        and _numeric_component_key(release.minor) <= _numeric_component_key(claimed.minor)
    )
    return max(
        downshift_candidates,
        key=lambda release: _numeric_component_key(release.minor),
        default=None,
    )


def validate_adcp_version_pins(params: Mapping[str, Any]) -> None:
    """Validate release- and major-precision AdCP request pins.

    AdCP version negotiation (``core/version-envelope.json``): stable release
    pins resolve by exact match or same-major downshift.
    Prerelease pins require an exact match. Sub-min, unmatched prerelease, and
    cross-major claims raise ``VERSION_UNSUPPORTED`` with authoritative seller
    releases. Schema-invalid values raise ``VALIDATION_ERROR``. When both fields
    are present their major components must agree. Only omission activates the
    seller default; an explicit null is malformed.
    """
    has_release_pin = "adcp_version" in params
    has_major_pin = "adcp_major_version" in params
    if not has_release_pin and not has_major_pin:
        return

    request_context = params.get("context")
    claimed_release = _parse_release_pin(params.get("adcp_version")) if has_release_pin else None
    if has_release_pin and claimed_release is None:
        raise _version_malformed_error("adcp_version", params.get("adcp_version"), context=request_context)

    claimed_major = _parse_major_pin(params.get("adcp_major_version")) if has_major_pin else None
    if has_major_pin and claimed_major is None:
        raise _version_malformed_error("adcp_major_version", params.get("adcp_major_version"), context=request_context)

    supported = _supported_releases()
    supported_majors = set(_supported_majors(supported))
    if claimed_release is not None and claimed_major is not None and claimed_release.major != str(claimed_major):
        raise _version_unsupported_error(
            params,
            supported_releases=supported,
            context=request_context,
        )
    if claimed_release is not None and _resolve_release_pin(claimed_release, supported) is None:
        raise _version_unsupported_error(
            params,
            supported_releases=supported,
            context=request_context,
        )
    if claimed_major is not None and claimed_major not in supported_majors:
        raise _version_unsupported_error(
            params,
            supported_releases=supported,
            context=request_context,
        )
