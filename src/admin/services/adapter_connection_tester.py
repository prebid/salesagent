"""Adapter connection probe used by the Tenant Management API.

A narrow wrapper that translates the per-adapter health-check API into a
typed :class:`ProbeResult` the Tenant Management API needs. Heavyweight
permission checks are out of scope here — we just verify that the configured
credentials authenticate.

Tests can monkeypatch :func:`probe_adapter_connection` or
:func:`preview_adapter` to bypass real API calls.

Error classification
--------------------

Probes return one of these ``error_code`` values so callers can render
the right remediation copy without parsing English error strings:

- ``network_not_found`` — the configured network/publisher id doesn't exist
  (GAM ``NETWORK_NOT_FOUND``, Broadstreet 404). Almost always a typo.
- ``permission_denied`` — credentials authenticate but lack access to the
  configured network/publisher (GAM ``NOT_ALLOWED`` /
  ``NO_NETWORKS_TO_ACCESS``, 403 on a scope probe, SpringServe/FreeWheel
  inventory 404). The propagation case.
- ``invalid_credentials`` — credentials themselves are bad (GAM
  ``AUTHENTICATION_FAILED``, raw 401 before any scope check).
- ``invalid_config`` — required config field missing (e.g. no
  ``network_code``). No HTTP call is attempted.
- ``upstream_unavailable`` — we reached the vendor but it returned 5xx /
  timed out. Retry-eligible — distinct from ``connection_failed``, which
  means we couldn't reach the vendor at all.
- ``connection_failed`` — fallback for anything not classified above
  (DNS/TLS/network blip, unparseable GAM SOAP fault, unknown reason code).

A ``permission_denied`` (and sometimes ``invalid_credentials``) result also
carries a ``remediation`` hint so UIs can branch on who can fix the problem
without grepping the human-readable message — see :data:`RemediationHint`.

Vendor diagnostics
------------------

The ``details.vendor_fault`` block (when present) carries a unified shape
across adapters::

    {
        "vendor": "gam" | "springserve" | "freewheel" | "broadstreet",
        "phase": str,              # what probe step failed
        "endpoint": str,           # what was called
        "vendor_status": int?,     # HTTP status (REST) / null (SOAP)
        "vendor_message": str?,    # trimmed raw response/exception text
        "<vendor>": dict?,         # vendor-specific extras (e.g. GAM SOAP)
    }

The discriminator lets consumers branch on ``vendor`` and then inspect the
optional nested ``<vendor>`` block for vendor-shaped diagnostics.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)


# Error code constants — typed sub-codes returned by the probe. The
# tenant-management API serves these in two shapes:
#
# - Inside the :class:`ApiError` envelope (provision / PUT failure paths):
#   prefixed as ``adapter_{code}`` — e.g. ``adapter_network_not_found`` —
#   so the envelope's ``error`` field disambiguates adapter-class errors
#   from tenant-class errors (``tenant_not_found``, ``external_org_id_conflict``).
# - Inside ``TestConnectionResponse`` and ``PreviewAdapterResponse``:
#   the bare code — e.g. ``"network_not_found"`` — because the response
#   shape already scopes the value to an adapter probe.
#
# Storefront integrators branch on the bare code in the inner field, and
# either match ``adapter_{code}`` on the envelope or strip the prefix.
# The closed set of values for ``ProbeResult.error_code``. Exposed as
# both a Literal type for static checking + a tuple for runtime iteration
# (e.g. SDK codegen, schema introspection).
AdapterErrorCode = Literal[
    "network_not_found",
    "permission_denied",
    "invalid_credentials",
    "invalid_config",
    "upstream_unavailable",
    "connection_failed",
]

NETWORK_NOT_FOUND: AdapterErrorCode = "network_not_found"
PERMISSION_DENIED: AdapterErrorCode = "permission_denied"
INVALID_CREDENTIALS: AdapterErrorCode = "invalid_credentials"
INVALID_CONFIG: AdapterErrorCode = "invalid_config"
UPSTREAM_UNAVAILABLE: AdapterErrorCode = "upstream_unavailable"
CONNECTION_FAILED: AdapterErrorCode = "connection_failed"

ADAPTER_ERROR_CODES: tuple[AdapterErrorCode, ...] = (
    NETWORK_NOT_FOUND,
    PERMISSION_DENIED,
    INVALID_CREDENTIALS,
    INVALID_CONFIG,
    UPSTREAM_UNAVAILABLE,
    CONNECTION_FAILED,
)


# Hints to UI rendering about WHO can fix the problem. Populated only when
# the assignment is unambiguous; missing means "follow the error_code's
# default guidance". Independent of ``error_code`` — a ``permission_denied``
# can remediate via the vendor (role gap) or the customer (wrong account
# binding); an ``invalid_credentials`` always remediates via token rotation
# but callers may still want the explicit hint.
RemediationHint = Literal[
    "vendor_enables_role",
    "customer_rebinds_account",
    "customer_rotates_token",
]

VENDOR_ENABLES_ROLE: RemediationHint = "vendor_enables_role"
CUSTOMER_REBINDS_ACCOUNT: RemediationHint = "customer_rebinds_account"
CUSTOMER_ROTATES_TOKEN: RemediationHint = "customer_rotates_token"

REMEDIATION_HINTS: tuple[RemediationHint, ...] = (
    VENDOR_ENABLES_ROLE,
    CUSTOMER_REBINDS_ACCOUNT,
    CUSTOMER_ROTATES_TOKEN,
)


# Discriminator for ``vendor_fault.vendor``.
Vendor = Literal["gam", "springserve", "freewheel", "broadstreet"]


@dataclass
class ProbeResult:
    """Outcome of an adapter authentication probe.

    Successful probes carry ``success=True`` and no error fields. Failures
    classify the fault into ``error_code`` (see module docstring), optionally
    a ``remediation`` hint, and optionally a structured ``details`` block so
    callers can render typed diagnostics without parsing the human-readable
    ``error_message``.
    """

    success: bool
    error_code: AdapterErrorCode | None = None
    error_message: str | None = None
    remediation: RemediationHint | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls) -> ProbeResult:
        return cls(success=True)

    @classmethod
    def fail(
        cls,
        error_code: AdapterErrorCode,
        message: str,
        *,
        remediation: RemediationHint | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProbeResult:
        return cls(
            success=False,
            error_code=error_code,
            error_message=message,
            remediation=remediation,
            details=details or {},
        )


@dataclass
class AdapterPreview:
    """Metadata returned by :func:`preview_adapter`.

    Used by the Storefront UI to confirm an adapter grant + auto-fill
    currency/timezone before committing to a tenant. ``ok=False`` is a normal
    flow (bad creds) — callers render this inline; the endpoint does NOT
    return 4xx for that case.

    ``error_code`` mirrors :class:`ProbeResult` so the same typed
    classification is available on the preview path.
    """

    ok: bool
    network_name: str | None = None
    network_code: str | None = None
    currency_code: str | None = None
    time_zone: str | None = None
    inventory_reachable: bool = False
    error: str | None = None
    error_code: AdapterErrorCode | None = None
    remediation: RemediationHint | None = None
    details: dict[str, Any] = field(default_factory=dict)


# Vendor probe phase/endpoint constants. Hoisted to module level so call
# sites don't repeat the strings and a future rename touches one place.
_GAM_PHASE = "get_current_network"
_GAM_ENDPOINT = "NetworkService.getCurrentNetwork"

_SS_PHASE_PROBE = "supply_probe"
_SS_ENDPOINT_SUPPLY = "/supply_tags"

_FW_PHASE_TOKEN_INFO = "token_info"
_FW_ENDPOINT_TOKEN_INFO = "/auth/token/info"
_FW_PHASE_LIST_SITES = "list_sites"
_FW_ENDPOINT_LIST_SITES = "/services/v4/sites"

_BS_PHASE = "get_network"
_BS_ENDPOINT_NETWORK = "/networks/{network_id}"


def _vendor_fault(
    vendor: Vendor,
    phase: str,
    endpoint: str,
    *,
    status: int | None = None,
    message: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the unified ``details["vendor_fault"]`` envelope.

    Returns ``{"vendor_fault": {vendor, phase, endpoint,
    vendor_status?, vendor_message?, <vendor>: {...}?}}``.

    The discriminator ``vendor`` lets consumers branch once before reading
    vendor-shaped fields. The optional nested ``<vendor>`` block (passed
    via ``extra``) carries vendor-specific diagnostics that don't fit the
    common shape — used today for GAM's SOAP service/reason fields.

    Empty/None inner fields are dropped to keep the wire payload tight.
    ``vendor_message`` is trimmed to 200 chars.

    Note on ``vendor_message`` provenance: callers should pass curated text
    (typed-exception messages we authored, or trimmed vendor response bodies).
    Raw ``str(exc)`` from untyped exceptions can include ``requests``-style
    URLs with internal hostnames — those callers should pass
    ``type(exc).__name__`` instead.
    """
    fault: dict[str, Any] = {"vendor": vendor, "phase": phase, "endpoint": endpoint}
    if status is not None:
        fault["vendor_status"] = status
    if message:
        fault["vendor_message"] = message[:200]
    if extra:
        fault[vendor] = extra
    return {"vendor_fault": fault}


# ---------------------------------------------------------------------------
# GAM SOAP fault classification
# ---------------------------------------------------------------------------

# Matches the suds/googleads stringification of a single error entry, e.g.
# ``[AuthenticationError.NETWORK_NOT_FOUND @ ; trigger:'<bad-code>']``.
_GAM_FAULT_RE = re.compile(
    r"\[(?P<service>\w+)\.(?P<reason>\w+)\s*@\s*(?P<field>[^;\]]*)"
    r"(?:;\s*trigger:'(?P<trigger>[^']*)')?\]"
)

# Map GAM ``AuthenticationError`` reason codes to ``(error_code, remediation)``.
# Everything else (or no match) falls back to ``CONNECTION_FAILED``.
_GAM_REASON_CLASSIFICATION: dict[str, tuple[AdapterErrorCode, RemediationHint | None]] = {
    # Network ID is a typo — operator can fix; no vendor involvement.
    "NETWORK_NOT_FOUND": (NETWORK_NOT_FOUND, None),
    # Service account isn't a member of this network — rebind to a network
    # the account has access to, or grant the account access in GAM admin.
    "NOT_ALLOWED": (PERMISSION_DENIED, CUSTOMER_REBINDS_ACCOUNT),
    "NO_NETWORKS_TO_ACCESS": (PERMISSION_DENIED, CUSTOMER_REBINDS_ACCOUNT),
    # Bad credentials — the code itself is unambiguous, but we surface the
    # rotation hint anyway so the same UI affordance lights up across codes.
    "AUTHENTICATION_FAILED": (INVALID_CREDENTIALS, CUSTOMER_ROTATES_TOKEN),
    "GOOGLE_ACCOUNT_AUTHENTICATION_FAILED": (INVALID_CREDENTIALS, CUSTOMER_ROTATES_TOKEN),
}


def _build_gam_extra(match: re.Match[str]) -> dict[str, Any]:
    """Extract GAM SOAP-specific fields from a regex match for the
    ``vendor_fault.gam`` nested block."""
    extra: dict[str, Any] = {
        "service": match.group("service"),
        "reason": match.group("reason"),
    }
    field_path = (match.group("field") or "").strip()
    if field_path:
        extra["field_path"] = field_path
    trigger = match.group("trigger")
    if trigger is not None:
        extra["trigger"] = trigger
    return extra


def _classify_gam_message(
    message: str,
) -> tuple[AdapterErrorCode, RemediationHint | None, dict[str, Any]]:
    """Inspect a GAM error message and produce ``(error_code, remediation, gam_extra)``.

    GAM SOAP faults can include multiple error entries — a generic
    wrapper like ``[ServerError.SOAP_FAULT @ ...]`` often precedes the
    diagnostic ``[AuthenticationError.NETWORK_NOT_FOUND @ ...]``. We
    scan all entries and prefer the first one whose ``reason`` maps to
    a typed sub-code; if none classify, we fall back to the first
    parseable entry for diagnostics with code ``CONNECTION_FAILED``.
    If no fault entry is parseable at all, returns ``(CONNECTION_FAILED, None, {})``.
    """
    matches = list(_GAM_FAULT_RE.finditer(message or ""))
    if not matches:
        return CONNECTION_FAILED, None, {}

    for match in matches:
        reason = match.group("reason")
        if reason in _GAM_REASON_CLASSIFICATION:
            code, remediation = _GAM_REASON_CLASSIFICATION[reason]
            return code, remediation, _build_gam_extra(match)

    # No classifiable reason — emit gam_extra for diagnostics anyway, no
    # remediation hint (we don't know what to suggest).
    return CONNECTION_FAILED, None, _build_gam_extra(matches[0])


def probe_adapter_connection(adapter_type: str, config: dict[str, Any]) -> ProbeResult:
    """Probe the adapter's authentication path.

    Args:
        adapter_type: One of ``"google_ad_manager"``, ``"freewheel"``,
            ``"broadstreet"``, ``"springserve"``, or ``"mock"``.
        config: Adapter-specific configuration. For GAM this includes
            ``network_code`` and one of ``service_account_json`` /
            ``refresh_token``. For FreeWheel this includes
            ``environment`` and one of (``username``, ``password``) /
            ``api_token``. For Broadstreet, ``network_id`` + ``api_key``.
            For SpringServe, one of (``email``, ``password``) /
            ``api_token``.

    Returns:
        A :class:`ProbeResult`. On failure, ``error_code`` classifies the
        fault into one of the four typed sub-codes (see module docstring).
    """
    if adapter_type == "mock":
        return ProbeResult.ok()

    if adapter_type == "google_ad_manager":
        return _test_gam(config)

    if adapter_type == "freewheel":
        return _test_freewheel(config)

    if adapter_type == "broadstreet":
        return _test_broadstreet(config)

    if adapter_type == "springserve":
        return _test_springserve(config)

    return ProbeResult.fail(CONNECTION_FAILED, f"Unsupported adapter_type: {adapter_type!r}")


def _test_gam(config: dict[str, Any]) -> ProbeResult:
    """Authentication probe for Google Ad Manager."""
    network_code = config.get("network_code")
    if not network_code:
        return ProbeResult.fail(INVALID_CONFIG, "GAM network_code is required")

    try:
        # Local import: keeps googleads off the import path for non-GAM tests.
        from src.adapters.gam.client import GAMClientManager
        from src.adapters.gam.utils.health_check import HealthStatus
    except Exception as exc:  # pragma: no cover - import-time failures are environmental
        logger.exception("GAM imports failed")
        return ProbeResult.fail(CONNECTION_FAILED, f"GAM client unavailable: {exc}")

    def _gam_details(message: str, gam_extra: dict[str, Any]) -> dict[str, Any]:
        return _vendor_fault(
            "gam",
            _GAM_PHASE,
            _GAM_ENDPOINT,
            message=message,
            extra=gam_extra or None,
        )

    try:
        manager = GAMClientManager(config=config, network_code=str(network_code))
        result = manager.test_connection()
    except Exception as exc:
        logger.warning("GAM test_connection raised: %s", exc)
        message = str(exc)
        code, remediation, gam_extra = _classify_gam_message(message)
        return ProbeResult.fail(
            code,
            f"GAM connection probe failed: {message}",
            remediation=remediation,
            details=_gam_details(message, gam_extra),
        )

    if result.status == HealthStatus.HEALTHY:
        return ProbeResult.ok()

    message = result.message or "GAM connection probe returned non-healthy status"
    code, remediation, gam_extra = _classify_gam_message(message)
    return ProbeResult.fail(code, message, remediation=remediation, details=_gam_details(message, gam_extra))


def _test_freewheel(config: dict[str, Any]) -> ProbeResult:
    """Authentication + publisher-binding probe for FreeWheel Publisher API.

    Two calls, sequentially:

    1. ``/auth/token/info`` — proves the bearer is recognised by
       FreeWheel's gateway. Surfaces 401 (revoked/expired) and 403 (no
       entitlements) cleanly.
    2. ``GET /services/v4/sites?per_page=1`` — proves the bearer is
       scoped to a publisher account with inventory. Without this, a
       valid-but-wrong-publisher token would provision successfully and
       only fail at first inventory sync — the asymmetry GAM avoids via
       ``getCurrentNetwork()``. A 403 here is the diagnostic signal that
       the token works but the publisher binding is wrong.
    """
    username = config.get("username")
    password = config.get("password")
    api_token = config.get("api_token")
    if not ((username and password) or api_token):
        return ProbeResult.fail(
            INVALID_CONFIG,
            "FreeWheel config requires either (username + password) or api_token",
        )

    try:
        from src.adapters.freewheel._transport import (
            FreeWheelAuthError,
            FreeWheelError,
            FreeWheelForbiddenError,
            FreeWheelNotFoundError,
        )
        from src.adapters.freewheel.client import FreeWheelClient
        from src.adapters.freewheel.schemas import FREEWHEEL_HOSTS
    except Exception as exc:  # pragma: no cover - environmental
        logger.exception("FreeWheel imports failed")
        return ProbeResult.fail(CONNECTION_FAILED, f"FreeWheel client unavailable: {exc}")

    environment = config.get("environment", "production")
    base_url = FREEWHEEL_HOSTS.get(environment, FREEWHEEL_HOSTS["production"])

    try:
        client = FreeWheelClient(
            api_token=api_token,
            username=username,
            password=password,
            base_url=base_url,
        )
    except Exception as exc:  # pragma: no cover - construction-time auth failures are rare
        logger.warning("FreeWheel client construction failed: %s", exc)
        return ProbeResult.fail(
            CONNECTION_FAILED,
            f"FreeWheel client construction failed: {type(exc).__name__}: {exc}",
        )

    def _fw_details(phase: str, endpoint: str, exc: Exception) -> dict[str, Any]:
        # Typed FreeWheel exceptions carry status_code; bare Exception
        # paths (e.g. requests.ConnectionError) do not — and we deliberately
        # drop str(exc) for those to avoid leaking internal hostnames the
        # transport puts into the message (see _vendor_fault docstring).
        if isinstance(exc, FreeWheelError):
            return _vendor_fault("freewheel", phase, endpoint, status=exc.status_code, message=str(exc))
        return _vendor_fault("freewheel", phase, endpoint, message=type(exc).__name__)

    def _fw_5xx_or_connection(exc: FreeWheelError) -> AdapterErrorCode:
        # 5xx means we reached the vendor and got an error — retry-eligible.
        # No status_code means we never got a response — connection-level.
        return UPSTREAM_UNAVAILABLE if (exc.status_code or 0) >= 500 else CONNECTION_FAILED

    # Step 1: bearer validity.
    try:
        client.token_info()
    except FreeWheelAuthError as exc:
        return ProbeResult.fail(
            INVALID_CREDENTIALS,
            f"FreeWheel auth rejected: {exc}",
            remediation=CUSTOMER_ROTATES_TOKEN,
            details=_fw_details(_FW_PHASE_TOKEN_INFO, _FW_ENDPOINT_TOKEN_INFO, exc),
        )
    except FreeWheelForbiddenError as exc:
        # Bearer recognized but the entitlement set is wrong — the vendor
        # must enable the right scopes on this token's account.
        return ProbeResult.fail(
            PERMISSION_DENIED,
            f"FreeWheel bearer lacks entitlements: {exc}",
            remediation=VENDOR_ENABLES_ROLE,
            details=_fw_details(_FW_PHASE_TOKEN_INFO, _FW_ENDPOINT_TOKEN_INFO, exc),
        )
    except FreeWheelError as exc:
        # Includes FreeWheelNotFoundError on token_info: 404 here means the
        # auth gateway host is misconfigured (route doesn't exist), not a
        # role gap — keep as CONNECTION_FAILED. See list_sites for the
        # symmetric 404-as-role-gap case. 5xx splits to upstream_unavailable.
        return ProbeResult.fail(
            _fw_5xx_or_connection(exc),
            f"FreeWheel API error on token_info (status={exc.status_code}): {exc}",
            details=_fw_details(_FW_PHASE_TOKEN_INFO, _FW_ENDPOINT_TOKEN_INFO, exc),
        )
    except Exception as exc:
        logger.warning("FreeWheel token_info() transport failure: %s", exc)
        return ProbeResult.fail(
            CONNECTION_FAILED,
            f"FreeWheel transport failure: {type(exc).__name__}",
            details=_fw_details(_FW_PHASE_TOKEN_INFO, _FW_ENDPOINT_TOKEN_INFO, exc),
        )

    # Step 2: publisher binding — does the bearer see inventory?
    try:
        client.inventory.list_sites(per_page=1)
    except FreeWheelForbiddenError as exc:
        # Bearer is valid (step 1 passed) but the publisher account it
        # represents can't read inventory. Either the token is for the
        # wrong publisher or the inventory scope wasn't granted — most
        # commonly fixed by getting a token for the right publisher.
        return ProbeResult.fail(
            PERMISSION_DENIED,
            (
                f"FreeWheel bearer cannot read inventory for the configured publisher "
                f"(403): {exc}. Verify the token is for the intended publisher account."
            ),
            remediation=CUSTOMER_REBINDS_ACCOUNT,
            details=_fw_details(_FW_PHASE_LIST_SITES, _FW_ENDPOINT_LIST_SITES, exc),
        )
    except FreeWheelNotFoundError as exc:
        # 404 on inventory listing means the bearer authenticated but the
        # configured publisher account has no accessible inventory route —
        # the role/scope-gap signature. Only the vendor can enable it.
        return ProbeResult.fail(
            PERMISSION_DENIED,
            (
                f"FreeWheel inventory endpoint returned 404 — bearer authenticated but "
                f"the configured publisher has no accessible inventory route: {exc}. "
                "Verify the token is provisioned for the intended publisher account."
            ),
            remediation=VENDOR_ENABLES_ROLE,
            details=_fw_details(_FW_PHASE_LIST_SITES, _FW_ENDPOINT_LIST_SITES, exc),
        )
    except FreeWheelError as exc:
        return ProbeResult.fail(
            _fw_5xx_or_connection(exc),
            f"FreeWheel API error on list_sites (status={exc.status_code}): {exc}",
            details=_fw_details(_FW_PHASE_LIST_SITES, _FW_ENDPOINT_LIST_SITES, exc),
        )
    except Exception as exc:
        logger.warning("FreeWheel list_sites() transport failure: %s", exc)
        return ProbeResult.fail(
            CONNECTION_FAILED,
            f"FreeWheel transport failure: {type(exc).__name__}",
            details=_fw_details(_FW_PHASE_LIST_SITES, _FW_ENDPOINT_LIST_SITES, exc),
        )

    return ProbeResult.ok()


def _test_broadstreet(config: dict[str, Any]) -> ProbeResult:
    """Authentication + network-binding probe for Broadstreet.

    Calls ``GET /networks/{network_id}`` via :meth:`BroadstreetClient.get_network`.
    A single call validates both that the API key is recognised AND that
    it has access to the configured network — Broadstreet's natural
    analog of GAM's ``getCurrentNetwork()``.
    """
    network_id = config.get("network_id")
    api_key = config.get("api_key")
    if not network_id:
        return ProbeResult.fail(INVALID_CONFIG, "Broadstreet network_id is required")
    if not api_key:
        return ProbeResult.fail(INVALID_CONFIG, "Broadstreet api_key is required")

    try:
        from src.adapters.broadstreet.client import BroadstreetAPIError, BroadstreetClient
    except Exception as exc:  # pragma: no cover - environmental
        logger.exception("Broadstreet imports failed")
        return ProbeResult.fail(CONNECTION_FAILED, f"Broadstreet client unavailable: {exc}")

    def _bs_details(exc: Exception, status: int | None = None) -> dict[str, Any]:
        if isinstance(exc, BroadstreetAPIError):
            return _vendor_fault(
                "broadstreet",
                _BS_PHASE,
                _BS_ENDPOINT_NETWORK,
                status=exc.status_code,
                message=str(exc),
            )
        return _vendor_fault("broadstreet", _BS_PHASE, _BS_ENDPOINT_NETWORK, message=type(exc).__name__)

    try:
        client = BroadstreetClient(access_token=str(api_key), network_id=str(network_id))
        client.get_network()
    except BroadstreetAPIError as exc:
        # 401 → bad key; 403 → no access to this network; 404 → wrong network_id;
        # 5xx → vendor down, retry-eligible.
        status = exc.status_code
        if status == 401:
            return ProbeResult.fail(
                INVALID_CREDENTIALS,
                f"Broadstreet auth rejected (status=401): {exc}",
                remediation=CUSTOMER_ROTATES_TOKEN,
                details=_bs_details(exc),
            )
        if status == 403:
            return ProbeResult.fail(
                PERMISSION_DENIED,
                f"Broadstreet network access denied (status=403): {exc}",
                remediation=CUSTOMER_REBINDS_ACCOUNT,
                details=_bs_details(exc),
            )
        if status == 404:
            return ProbeResult.fail(
                NETWORK_NOT_FOUND,
                f"Broadstreet network {network_id!r} not found (status=404)",
                details=_bs_details(exc),
            )
        if status and status >= 500:
            return ProbeResult.fail(
                UPSTREAM_UNAVAILABLE,
                f"Broadstreet API unavailable (status={status}): {exc}",
                details=_bs_details(exc),
            )
        return ProbeResult.fail(
            CONNECTION_FAILED,
            f"Broadstreet API error (status={status}): {exc}",
            details=_bs_details(exc),
        )
    except Exception as exc:
        logger.warning("Broadstreet get_network() transport failure: %s", exc)
        return ProbeResult.fail(
            CONNECTION_FAILED,
            f"Broadstreet transport failure: {type(exc).__name__}",
            details=_bs_details(exc),
        )

    return ProbeResult.ok()


def _test_springserve(config: dict[str, Any]) -> ProbeResult:
    """Authentication + scope probe for SpringServe.

    Two-step probe mirroring the FreeWheel pattern:

    1. ``GET /supply_tags?per_page=1`` via the transport's token cache — the first
       authenticated call mints (or validates) the bearer. Email-grant
       credentials hit ``POST /auth`` here; bad password surfaces as
       :class:`SpringServeAuthError`.
    2. The supply-tags response proves the bearer is scoped to
       a publisher account with supply inventory. Analogous to FreeWheel's
       ``list_sites`` probe and GAM's ``getCurrentNetwork``.
    """
    email = config.get("email")
    password = config.get("password")
    api_token = config.get("api_token")
    if not ((email and password) or api_token):
        return ProbeResult.fail(
            INVALID_CONFIG,
            "SpringServe config requires either (email + password) or api_token",
        )

    try:
        from src.adapters.springserve._transport import (
            SpringServeAuthError,
            SpringServeError,
            SpringServeForbiddenError,
        )
        from src.adapters.springserve.client import SpringServeClient
    except Exception as exc:  # pragma: no cover - environmental
        logger.exception("SpringServe imports failed")
        return ProbeResult.fail(CONNECTION_FAILED, f"SpringServe client unavailable: {exc}")

    try:
        client = SpringServeClient(api_token=api_token, email=email, password=password)
    except Exception as exc:  # pragma: no cover - construction-time failures are rare
        logger.warning("SpringServe client construction failed: %s", exc)
        return ProbeResult.fail(
            CONNECTION_FAILED,
            f"SpringServe client construction failed: {type(exc).__name__}",
        )

    def _ss_details(exc: Exception) -> dict[str, Any]:
        # All probe paths target the supply endpoint; an internal token
        # mint (password grant) is an implementation detail of probe() and
        # we don't label exceptions as ``auth_mint`` because in the
        # pre-minted-token case no mint happens — labeling the endpoint
        # ``/auth`` would lie. The error_code on the envelope (e.g.
        # INVALID_CREDENTIALS) already tells consumers what was wrong;
        # phase/endpoint just tell them what was called.
        if isinstance(exc, SpringServeError):
            return _vendor_fault(
                "springserve",
                _SS_PHASE_PROBE,
                _SS_ENDPOINT_SUPPLY,
                status=exc.status_code,
                message=str(exc),
            )
        # Untyped exception (e.g. requests.ConnectionError) — drop str(exc)
        # because it can include the full URL with internal hostnames.
        return _vendor_fault(
            "springserve",
            _SS_PHASE_PROBE,
            _SS_ENDPOINT_SUPPLY,
            message=type(exc).__name__,
        )

    def _ss_status_details(status: int, body: str | None) -> dict[str, Any]:
        return _vendor_fault(
            "springserve",
            _SS_PHASE_PROBE,
            _SS_ENDPOINT_SUPPLY,
            status=status,
            message=body,
        )

    def _ss_5xx_or_connection(exc: SpringServeError) -> AdapterErrorCode:
        return UPSTREAM_UNAVAILABLE if (exc.status_code or 0) >= 500 else CONNECTION_FAILED

    # Single call exercises both auth (token mint, if password grant) and
    # scope (a 403 here means the bearer is valid but can't see supply
    # inventory for the configured account). client.probe() returns
    # (status_code, body) without raising on non-2xx — auth/mint
    # failures still raise, which we surface separately.
    try:
        status, body = client.probe("GET", f"{_SS_ENDPOINT_SUPPLY}?per_page=1")
    except SpringServeAuthError as exc:
        return ProbeResult.fail(
            INVALID_CREDENTIALS,
            f"SpringServe auth rejected: {exc}",
            remediation=CUSTOMER_ROTATES_TOKEN,
            details=_ss_details(exc),
        )
    except SpringServeForbiddenError as exc:
        # Bearer recognized but entitlements are wrong — typically the
        # token is for the wrong account; rotate to one with supply scope.
        return ProbeResult.fail(
            PERMISSION_DENIED,
            f"SpringServe bearer lacks entitlements: {exc}",
            remediation=CUSTOMER_REBINDS_ACCOUNT,
            details=_ss_details(exc),
        )
    except SpringServeError as exc:
        return ProbeResult.fail(
            _ss_5xx_or_connection(exc),
            f"SpringServe API error on supply probe (status={exc.status_code}): {exc}",
            details=_ss_details(exc),
        )
    except Exception as exc:
        logger.warning("SpringServe probe transport failure: %s", exc)
        return ProbeResult.fail(
            CONNECTION_FAILED,
            f"SpringServe transport failure: {type(exc).__name__}",
            details=_ss_details(exc),
        )

    if status == 200:
        return ProbeResult.ok()
    if status == 401:
        return ProbeResult.fail(
            INVALID_CREDENTIALS,
            "SpringServe bearer rejected on supply inventory probe (status=401)",
            remediation=CUSTOMER_ROTATES_TOKEN,
            details=_ss_status_details(status, body),
        )
    if status == 403:
        return ProbeResult.fail(
            PERMISSION_DENIED,
            (
                "SpringServe bearer cannot read supply inventory (status=403). "
                "Verify the token is for the intended publisher account."
            ),
            remediation=CUSTOMER_REBINDS_ACCOUNT,
            details=_ss_status_details(status, body),
        )
    if status == 404:
        # Authenticated successfully (we got past mint) but the supply
        # endpoint returned 404 — the signature of "account exists but
        # lacks the supply API role". Only the vendor can enable it.
        return ProbeResult.fail(
            PERMISSION_DENIED,
            (
                "SpringServe supply endpoint returned 404 — the bearer is valid but "
                "the account lacks the supply API role. Contact your SpringServe "
                "representative to enable supply API access for this account."
            ),
            remediation=VENDOR_ENABLES_ROLE,
            details=_ss_status_details(status, body),
        )
    if status >= 500:
        return ProbeResult.fail(
            UPSTREAM_UNAVAILABLE,
            f"SpringServe supply probe unavailable (status={status}): {body[:200]}",
            details=_ss_status_details(status, body),
        )
    return ProbeResult.fail(
        CONNECTION_FAILED,
        f"SpringServe supply probe returned status={status}: {body[:200]}",
        details=_ss_status_details(status, body),
    )


def preview_adapter(adapter_type: str, config: dict[str, Any]) -> AdapterPreview:
    """Probe the adapter and return network metadata for Storefront preview.

    On bad creds returns ``AdapterPreview(ok=False, error=..., error_code=...)``
    rather than raising — the endpoint surfaces this as 200 so the UI can
    render inline. The same typed ``error_code`` produced by
    :func:`probe_adapter_connection` is included on the preview path.
    """
    if adapter_type == "mock":
        return AdapterPreview(
            ok=True,
            network_name="Mock Network",
            network_code=str(config.get("network_code") or "mock-network"),
            currency_code="USD",
            time_zone="UTC",
            inventory_reachable=True,
        )

    if adapter_type == "google_ad_manager":
        return _preview_gam(config)

    if adapter_type == "freewheel":
        return _preview_freewheel(config)

    if adapter_type == "broadstreet":
        return _preview_broadstreet(config)

    if adapter_type == "springserve":
        return _preview_springserve(config)

    return AdapterPreview(
        ok=False,
        error=f"Unsupported adapter_type: {adapter_type!r}",
        error_code=CONNECTION_FAILED,
    )


def _preview_gam(config: dict[str, Any]) -> AdapterPreview:
    """GAM preview: connection test + ``getCurrentNetwork()`` metadata."""
    network_code = config.get("network_code")
    if not network_code:
        return AdapterPreview(
            ok=False,
            error="GAM network_code is required",
            error_code=INVALID_CONFIG,
        )

    try:
        from src.adapters.gam.client import GAMClientManager
        from src.adapters.gam.utils.health_check import HealthStatus
    except Exception as exc:  # pragma: no cover - environmental
        logger.exception("GAM imports failed")
        return AdapterPreview(ok=False, error=f"GAM client unavailable: {exc}", error_code=CONNECTION_FAILED)

    def _gam_preview_details(message: str, gam_extra: dict[str, Any]) -> dict[str, Any]:
        return _vendor_fault(
            "gam",
            _GAM_PHASE,
            _GAM_ENDPOINT,
            message=message,
            extra=gam_extra or None,
        )

    try:
        manager = GAMClientManager(config=config, network_code=str(network_code))
        result = manager.test_connection()
    except Exception as exc:
        logger.warning("GAM test_connection raised: %s", exc)
        message = str(exc)
        code, remediation, gam_extra = _classify_gam_message(message)
        return AdapterPreview(
            ok=False,
            error=f"GAM connection probe failed: {message}",
            error_code=code,
            remediation=remediation,
            details=_gam_preview_details(message, gam_extra),
        )

    if result.status != HealthStatus.HEALTHY:
        message = result.message or "GAM connection probe returned non-healthy status"
        code, remediation, gam_extra = _classify_gam_message(message)
        return AdapterPreview(
            ok=False,
            error=message,
            error_code=code,
            remediation=remediation,
            details=_gam_preview_details(message, gam_extra),
        )

    # Fetch network metadata via getCurrentNetwork(). One extra call after auth proven.
    try:
        client = manager.get_client()
        network = client.GetService("NetworkService").getCurrentNetwork()
    except Exception as exc:
        # Connection works but metadata fetch failed — still ok=true with sparse fields.
        logger.warning("GAM getCurrentNetwork() failed after auth ok: %s", exc)
        return AdapterPreview(
            ok=True,
            network_code=str(network_code),
            inventory_reachable=False,
            error=f"network metadata unavailable: {exc}",
        )

    return AdapterPreview(
        ok=True,
        network_name=getattr(network, "displayName", None),
        network_code=str(getattr(network, "networkCode", network_code)),
        currency_code=getattr(network, "currencyCode", None),
        time_zone=getattr(network, "timeZone", None),
        inventory_reachable=True,
    )


def _preview_freewheel(config: dict[str, Any]) -> AdapterPreview:
    """FreeWheel preview: token validation + identity metadata.

    Auth via ``token_info`` returns ``{user_id, user_name, ...}`` fields
    the Storefront UI can render as "you're connected as <user_name>".
    ``inventory_reachable`` set by attempting one ``list_sites`` page —
    same probe as :func:`_test_freewheel`, surfaced as a flag instead
    of a hard 4xx so the preview is inline.
    """
    username = config.get("username")
    password = config.get("password")
    api_token = config.get("api_token")
    if not ((username and password) or api_token):
        return AdapterPreview(
            ok=False,
            error="FreeWheel config requires either (username + password) or api_token",
            error_code=INVALID_CONFIG,
        )

    try:
        from src.adapters.freewheel._transport import (
            FreeWheelAuthError,
            FreeWheelError,
            FreeWheelForbiddenError,
        )
        from src.adapters.freewheel.client import FreeWheelClient
        from src.adapters.freewheel.schemas import FREEWHEEL_HOSTS
    except Exception as exc:  # pragma: no cover - environmental
        logger.exception("FreeWheel imports failed")
        return AdapterPreview(
            ok=False,
            error=f"FreeWheel client unavailable: {exc}",
            error_code=CONNECTION_FAILED,
        )

    environment = config.get("environment", "production")
    base_url = FREEWHEEL_HOSTS.get(environment, FREEWHEEL_HOSTS["production"])

    def _fw_preview_details(exc: Exception) -> dict[str, Any]:
        if isinstance(exc, FreeWheelError):
            return _vendor_fault(
                "freewheel",
                _FW_PHASE_TOKEN_INFO,
                _FW_ENDPOINT_TOKEN_INFO,
                status=exc.status_code,
                message=str(exc),
            )
        return _vendor_fault(
            "freewheel",
            _FW_PHASE_TOKEN_INFO,
            _FW_ENDPOINT_TOKEN_INFO,
            message=type(exc).__name__,
        )

    try:
        client = FreeWheelClient(api_token=api_token, username=username, password=password, base_url=base_url)
        token_info = client.token_info()
    except FreeWheelAuthError as exc:
        return AdapterPreview(
            ok=False,
            error=f"FreeWheel auth rejected: {exc}",
            error_code=INVALID_CREDENTIALS,
            remediation=CUSTOMER_ROTATES_TOKEN,
            details=_fw_preview_details(exc),
        )
    except FreeWheelForbiddenError as exc:
        return AdapterPreview(
            ok=False,
            error=f"FreeWheel bearer lacks entitlements: {exc}",
            error_code=PERMISSION_DENIED,
            remediation=VENDOR_ENABLES_ROLE,
            details=_fw_preview_details(exc),
        )
    except FreeWheelError as exc:
        code: AdapterErrorCode = UPSTREAM_UNAVAILABLE if (exc.status_code or 0) >= 500 else CONNECTION_FAILED
        return AdapterPreview(
            ok=False,
            error=f"FreeWheel API error (status={exc.status_code}): {exc}",
            error_code=code,
            details=_fw_preview_details(exc),
        )
    except Exception as exc:
        logger.warning("FreeWheel token_info() failed: %s", exc)
        return AdapterPreview(
            ok=False,
            error=f"FreeWheel transport failure: {type(exc).__name__}",
            error_code=CONNECTION_FAILED,
            details=_fw_preview_details(exc),
        )

    # token_info shape: {"user_id": ..., "user_name": ..., "scope": ...}.
    # FreeWheel doesn't expose a single "network" entity; we use user_name
    # as the human-readable label so the Storefront UI shows "you're
    # connected as <user_name>".
    network_name = token_info.get("user_name") if isinstance(token_info, dict) else None

    # Probe inventory reachability — non-fatal. A 200 here proves the
    # token has the publisher binding we need at provision time.
    inventory_reachable = False
    try:
        client.inventory.list_sites(per_page=1)
        inventory_reachable = True
    except Exception as exc:  # noqa: BLE001 — preview path is best-effort
        logger.debug("FreeWheel inventory preview probe failed: %s", exc)

    return AdapterPreview(
        ok=True,
        network_name=network_name,
        network_code=None,  # FreeWheel publisher accounts don't have a network_code
        currency_code=None,  # Not exposed by token_info; would need a separate call
        time_zone=None,
        inventory_reachable=inventory_reachable,
    )


def _preview_broadstreet(config: dict[str, Any]) -> AdapterPreview:
    """Broadstreet preview: ``get_network()`` returns network metadata
    (name, id) in one call. Validates auth + network binding too — same
    probe as :func:`_test_broadstreet`, surfaced with network metadata.
    """
    network_id = config.get("network_id")
    api_key = config.get("api_key")
    if not network_id:
        return AdapterPreview(
            ok=False,
            error="Broadstreet network_id is required",
            error_code=INVALID_CONFIG,
        )
    if not api_key:
        return AdapterPreview(
            ok=False,
            error="Broadstreet api_key is required",
            error_code=INVALID_CONFIG,
        )

    try:
        from src.adapters.broadstreet.client import BroadstreetAPIError, BroadstreetClient
    except Exception as exc:  # pragma: no cover - environmental
        logger.exception("Broadstreet imports failed")
        return AdapterPreview(
            ok=False,
            error=f"Broadstreet client unavailable: {exc}",
            error_code=CONNECTION_FAILED,
        )

    def _bs_preview_details(exc: Exception) -> dict[str, Any]:
        if isinstance(exc, BroadstreetAPIError):
            return _vendor_fault(
                "broadstreet",
                _BS_PHASE,
                _BS_ENDPOINT_NETWORK,
                status=exc.status_code,
                message=str(exc),
            )
        return _vendor_fault("broadstreet", _BS_PHASE, _BS_ENDPOINT_NETWORK, message=type(exc).__name__)

    try:
        client = BroadstreetClient(access_token=str(api_key), network_id=str(network_id))
        network = client.get_network()
    except BroadstreetAPIError as exc:
        status = exc.status_code
        if status == 401:
            return AdapterPreview(
                ok=False,
                error=f"Broadstreet auth rejected (status=401): {exc}",
                error_code=INVALID_CREDENTIALS,
                remediation=CUSTOMER_ROTATES_TOKEN,
                details=_bs_preview_details(exc),
            )
        if status == 403:
            return AdapterPreview(
                ok=False,
                error=f"Broadstreet network access denied (status=403): {exc}",
                error_code=PERMISSION_DENIED,
                remediation=CUSTOMER_REBINDS_ACCOUNT,
                details=_bs_preview_details(exc),
            )
        if status == 404:
            return AdapterPreview(
                ok=False,
                error=f"Broadstreet network {network_id!r} not found",
                error_code=NETWORK_NOT_FOUND,
                details=_bs_preview_details(exc),
            )
        if status and status >= 500:
            return AdapterPreview(
                ok=False,
                error=f"Broadstreet API unavailable (status={status}): {exc}",
                error_code=UPSTREAM_UNAVAILABLE,
                details=_bs_preview_details(exc),
            )
        return AdapterPreview(
            ok=False,
            error=f"Broadstreet API error (status={status}): {exc}",
            error_code=CONNECTION_FAILED,
            details=_bs_preview_details(exc),
        )
    except Exception as exc:
        logger.warning("Broadstreet get_network() failed: %s", exc)
        return AdapterPreview(
            ok=False,
            error=f"Broadstreet transport failure: {type(exc).__name__}",
            error_code=CONNECTION_FAILED,
            details=_bs_preview_details(exc),
        )

    # Broadstreet network responses use camelCase keys per the v0 API; the
    # client returns the unwrapped network dict.
    name = None
    if isinstance(network, dict):
        name = network.get("name") or network.get("Name")

    return AdapterPreview(
        ok=True,
        network_name=name,
        network_code=str(network_id),
        currency_code=None,  # Broadstreet doesn't surface currency at the network level
        time_zone=None,
        # Broadstreet inventory sync isn't implemented (#448) — declared
        # False on the capability flag, so we don't probe inventory here
        # either. Network access proven by get_network() returning 200.
        inventory_reachable=False,
    )


def _preview_springserve(config: dict[str, Any]) -> AdapterPreview:
    """SpringServe preview: token mint + supply scope probe in one call.

    Same probe as :func:`_test_springserve` but surfaced as a preview
    flag instead of a hard 4xx. SpringServe's auth API doesn't return
    metadata equivalent to GAM's network info; the only thing we can
    confirm is that the bearer is valid and has supply access.
    """
    email = config.get("email")
    password = config.get("password")
    api_token = config.get("api_token")
    if not ((email and password) or api_token):
        return AdapterPreview(
            ok=False,
            error="SpringServe config requires either (email + password) or api_token",
            error_code=INVALID_CONFIG,
        )

    try:
        from src.adapters.springserve._transport import (
            SpringServeAuthError,
            SpringServeError,
            SpringServeForbiddenError,
        )
        from src.adapters.springserve.client import SpringServeClient
    except Exception as exc:  # pragma: no cover - environmental
        logger.exception("SpringServe imports failed")
        return AdapterPreview(
            ok=False,
            error=f"SpringServe client unavailable: {exc}",
            error_code=CONNECTION_FAILED,
        )

    def _ss_preview_details(exc: Exception) -> dict[str, Any]:
        if isinstance(exc, SpringServeError):
            return _vendor_fault(
                "springserve",
                _SS_PHASE_PROBE,
                _SS_ENDPOINT_SUPPLY,
                status=exc.status_code,
                message=str(exc),
            )
        return _vendor_fault(
            "springserve",
            _SS_PHASE_PROBE,
            _SS_ENDPOINT_SUPPLY,
            message=type(exc).__name__,
        )

    def _ss_preview_status_details(status: int, body: str | None) -> dict[str, Any]:
        return _vendor_fault(
            "springserve",
            _SS_PHASE_PROBE,
            _SS_ENDPOINT_SUPPLY,
            status=status,
            message=body,
        )

    try:
        client = SpringServeClient(api_token=api_token, email=email, password=password)
        status, body = client.probe("GET", f"{_SS_ENDPOINT_SUPPLY}?per_page=1")
    except SpringServeAuthError as exc:
        return AdapterPreview(
            ok=False,
            error=f"SpringServe auth rejected: {exc}",
            error_code=INVALID_CREDENTIALS,
            remediation=CUSTOMER_ROTATES_TOKEN,
            details=_ss_preview_details(exc),
        )
    except SpringServeForbiddenError as exc:
        return AdapterPreview(
            ok=False,
            error=f"SpringServe bearer lacks entitlements: {exc}",
            error_code=PERMISSION_DENIED,
            remediation=CUSTOMER_REBINDS_ACCOUNT,
            details=_ss_preview_details(exc),
        )
    except SpringServeError as exc:
        code: AdapterErrorCode = UPSTREAM_UNAVAILABLE if (exc.status_code or 0) >= 500 else CONNECTION_FAILED
        return AdapterPreview(
            ok=False,
            error=f"SpringServe API error (status={exc.status_code}): {exc}",
            error_code=code,
            details=_ss_preview_details(exc),
        )
    except Exception as exc:
        logger.warning("SpringServe probe failed: %s", exc)
        return AdapterPreview(
            ok=False,
            error=f"SpringServe transport failure: {type(exc).__name__}",
            error_code=CONNECTION_FAILED,
            details=_ss_preview_details(exc),
        )

    if status == 200:
        return AdapterPreview(
            ok=True,
            network_name=email if email else None,
            network_code=None,
            currency_code=None,
            time_zone=None,
            inventory_reachable=True,
        )
    if status == 401:
        return AdapterPreview(
            ok=False,
            error="SpringServe bearer rejected (status=401)",
            error_code=INVALID_CREDENTIALS,
            remediation=CUSTOMER_ROTATES_TOKEN,
            details=_ss_preview_status_details(status, body),
        )
    if status == 403:
        return AdapterPreview(
            ok=False,
            error="SpringServe bearer cannot read supply inventory (status=403)",
            error_code=PERMISSION_DENIED,
            remediation=CUSTOMER_REBINDS_ACCOUNT,
            details=_ss_preview_status_details(status, body),
        )
    if status == 404:
        return AdapterPreview(
            ok=False,
            error=(
                "SpringServe supply endpoint returned 404 — bearer is valid but the "
                "account lacks the supply API role. Contact your SpringServe "
                "representative to enable supply API access."
            ),
            error_code=PERMISSION_DENIED,
            remediation=VENDOR_ENABLES_ROLE,
            details=_ss_preview_status_details(status, body),
        )
    if status >= 500:
        return AdapterPreview(
            ok=False,
            error=f"SpringServe supply probe unavailable (status={status}): {body[:200]}",
            error_code=UPSTREAM_UNAVAILABLE,
            details=_ss_preview_status_details(status, body),
        )
    return AdapterPreview(
        ok=False,
        error=f"SpringServe supply probe returned status={status}: {body[:200]}",
        error_code=CONNECTION_FAILED,
        details=_ss_preview_status_details(status, body),
    )
