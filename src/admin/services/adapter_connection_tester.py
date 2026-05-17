"""Adapter connection probe used by the Tenant Management API.

A narrow wrapper that translates the per-adapter health-check API into the
``(success, error)`` tuple the Tenant Management API needs. Heavyweight
permission checks are out of scope here — we just verify that the configured
credentials authenticate.

Tests can monkeypatch :func:`test_adapter_connection` or
:func:`preview_adapter` to bypass real API calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AdapterPreview:
    """Metadata returned by :func:`preview_adapter`.

    Used by the Storefront UI to confirm an adapter grant + auto-fill
    currency/timezone before committing to a tenant. ``ok=False`` is a normal
    flow (bad creds) — callers render this inline; the endpoint does NOT
    return 4xx for that case.
    """

    ok: bool
    network_name: str | None = None
    network_code: str | None = None
    currency_code: str | None = None
    time_zone: str | None = None
    inventory_reachable: bool = False
    error: str | None = None


def test_adapter_connection(adapter_type: str, config: dict[str, Any]) -> tuple[bool, str | None]:
    """Probe the adapter's authentication path.

    Args:
        adapter_type: One of ``"google_ad_manager"``, ``"freewheel"``, or
            ``"mock"``.
        config: Adapter-specific configuration. For GAM this includes
            ``network_code`` and one of ``service_account_json`` /
            ``refresh_token``. For FreeWheel this includes
            ``environment`` and one of (``username``, ``password``) /
            ``api_token``.

    Returns:
        A ``(success, error)`` tuple. ``error`` is None on success and a
        human-readable string on failure.
    """
    if adapter_type == "mock":
        return True, None

    if adapter_type == "google_ad_manager":
        return _test_gam(config)

    if adapter_type == "freewheel":
        return _test_freewheel(config)

    return False, f"Unsupported adapter_type: {adapter_type!r}"


def _test_gam(config: dict[str, Any]) -> tuple[bool, str | None]:
    """Authentication probe for Google Ad Manager."""
    network_code = config.get("network_code")
    if not network_code:
        return False, "GAM network_code is required"

    try:
        # Local import: keeps googleads off the import path for non-GAM tests.
        from src.adapters.gam.client import GAMClientManager
        from src.adapters.gam.utils.health_check import HealthStatus
    except Exception as exc:  # pragma: no cover - import-time failures are environmental
        logger.exception("GAM imports failed")
        return False, f"GAM client unavailable: {exc}"

    try:
        manager = GAMClientManager(config=config, network_code=str(network_code))
        result = manager.test_connection()
    except Exception as exc:
        logger.warning("GAM test_connection raised: %s", exc)
        return False, f"GAM connection probe failed: {exc}"

    if result.status == HealthStatus.HEALTHY:
        return True, None
    return False, result.message or "GAM connection probe returned non-healthy status"


def _test_freewheel(config: dict[str, Any]) -> tuple[bool, str | None]:
    """Authentication probe for FreeWheel Publisher API.

    Hits ``/auth/token/info`` — a 200 proves the bearer (or password
    grant) is recognised by FreeWheel's gateway. Same probe FreeWheel's
    own ``check_permissions()`` matrix uses for the required
    ``auth_token_info`` scope, so success here means everything else in
    that matrix has a real chance.
    """
    username = config.get("username")
    password = config.get("password")
    api_token = config.get("api_token")
    if not ((username and password) or api_token):
        return False, "FreeWheel config requires either (username + password) or api_token"

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
        return False, f"FreeWheel client unavailable: {exc}"

    environment = config.get("environment", "production")
    base_url = FREEWHEEL_HOSTS.get(environment, FREEWHEEL_HOSTS["production"])

    try:
        client = FreeWheelClient(
            api_token=api_token,
            username=username,
            password=password,
            base_url=base_url,
        )
        client.token_info()
    except FreeWheelAuthError as exc:
        return False, f"FreeWheel auth rejected: {exc}"
    except FreeWheelForbiddenError as exc:
        # Bearer is valid but lacks the entitlements to introspect itself.
        # Treat as a credential problem — the configured key isn't usable.
        return False, f"FreeWheel bearer lacks entitlements: {exc}"
    except FreeWheelError as exc:
        # Other FreeWheel-side error (4xx validation, 5xx server). Surface
        # the status code so the host product can distinguish transient
        # infra failures from bad credentials.
        return False, f"FreeWheel API error (status={exc.status_code}): {exc}"
    except Exception as exc:
        # Network / transport failure (DNS, TLS, timeout, JSON decode).
        # Not a credentials problem; the host product may want to retry.
        logger.warning("FreeWheel token_info() transport failure: %s", exc)
        return False, f"FreeWheel transport failure: {type(exc).__name__}: {exc}"

    return True, None


def preview_adapter(adapter_type: str, config: dict[str, Any]) -> AdapterPreview:
    """Probe the adapter and return network metadata for Storefront preview.

    On bad creds returns ``AdapterPreview(ok=False, error=...)`` rather than
    raising — the endpoint surfaces this as 200 so the UI can render inline.
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

    return AdapterPreview(ok=False, error=f"Unsupported adapter_type: {adapter_type!r}")


def _preview_gam(config: dict[str, Any]) -> AdapterPreview:
    """GAM preview: connection test + ``getCurrentNetwork()`` metadata."""
    network_code = config.get("network_code")
    if not network_code:
        return AdapterPreview(ok=False, error="GAM network_code is required")

    try:
        from src.adapters.gam.client import GAMClientManager
        from src.adapters.gam.utils.health_check import HealthStatus
    except Exception as exc:  # pragma: no cover - environmental
        logger.exception("GAM imports failed")
        return AdapterPreview(ok=False, error=f"GAM client unavailable: {exc}")

    try:
        manager = GAMClientManager(config=config, network_code=str(network_code))
        result = manager.test_connection()
    except Exception as exc:
        logger.warning("GAM test_connection raised: %s", exc)
        return AdapterPreview(ok=False, error=f"GAM connection probe failed: {exc}")

    if result.status != HealthStatus.HEALTHY:
        return AdapterPreview(
            ok=False,
            error=result.message or "GAM connection probe returned non-healthy status",
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
