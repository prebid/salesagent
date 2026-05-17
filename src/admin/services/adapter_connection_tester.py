"""Adapter connection probe used by the Tenant Management API.

A narrow wrapper that translates the per-adapter health-check API into the
``(success, error)`` tuple the Tenant Management API needs. Heavyweight
permission checks are out of scope here — we just verify that the configured
credentials authenticate.

Tests can monkeypatch :func:`probe_adapter_connection` or
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


def probe_adapter_connection(adapter_type: str, config: dict[str, Any]) -> tuple[bool, str | None]:
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
        A ``(success, error)`` tuple. ``error`` is None on success and a
        human-readable string on failure.
    """
    if adapter_type == "mock":
        return True, None

    if adapter_type == "google_ad_manager":
        return _test_gam(config)

    if adapter_type == "freewheel":
        return _test_freewheel(config)

    if adapter_type == "broadstreet":
        return _test_broadstreet(config)

    if adapter_type == "springserve":
        return _test_springserve(config)

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
    except Exception as exc:  # pragma: no cover - construction-time auth failures are rare
        logger.warning("FreeWheel client construction failed: %s", exc)
        return False, f"FreeWheel client construction failed: {type(exc).__name__}: {exc}"

    # Step 1: bearer validity.
    try:
        client.token_info()
    except FreeWheelAuthError as exc:
        return False, f"FreeWheel auth rejected: {exc}"
    except FreeWheelForbiddenError as exc:
        return False, f"FreeWheel bearer lacks entitlements: {exc}"
    except FreeWheelError as exc:
        return False, f"FreeWheel API error on token_info (status={exc.status_code}): {exc}"
    except Exception as exc:
        logger.warning("FreeWheel token_info() transport failure: %s", exc)
        return False, f"FreeWheel transport failure: {type(exc).__name__}: {exc}"

    # Step 2: publisher binding — does the bearer see inventory?
    try:
        client.inventory.list_sites(per_page=1)
    except FreeWheelForbiddenError as exc:
        # Bearer is valid (step 1 passed) but the publisher account it
        # represents can't read inventory. Either the token is for the
        # wrong publisher or the inventory scope wasn't granted.
        return False, (
            f"FreeWheel bearer cannot read inventory for the configured publisher "
            f"(403): {exc}. Verify the token is for the intended publisher account."
        )
    except FreeWheelError as exc:
        return False, f"FreeWheel API error on list_sites (status={exc.status_code}): {exc}"
    except Exception as exc:
        logger.warning("FreeWheel list_sites() transport failure: %s", exc)
        return False, f"FreeWheel transport failure: {type(exc).__name__}: {exc}"

    return True, None


def _test_broadstreet(config: dict[str, Any]) -> tuple[bool, str | None]:
    """Authentication + network-binding probe for Broadstreet.

    Calls ``GET /networks/{network_id}`` via :meth:`BroadstreetClient.get_network`.
    A single call validates both that the API key is recognised AND that
    it has access to the configured network — Broadstreet's natural
    analog of GAM's ``getCurrentNetwork()``.
    """
    network_id = config.get("network_id")
    api_key = config.get("api_key")
    if not network_id:
        return False, "Broadstreet network_id is required"
    if not api_key:
        return False, "Broadstreet api_key is required"

    try:
        from src.adapters.broadstreet.client import BroadstreetAPIError, BroadstreetClient
    except Exception as exc:  # pragma: no cover - environmental
        logger.exception("Broadstreet imports failed")
        return False, f"Broadstreet client unavailable: {exc}"

    try:
        client = BroadstreetClient(access_token=str(api_key), network_id=str(network_id))
        client.get_network()
    except BroadstreetAPIError as exc:
        # 401/403 → bad key or no access to this network. 404 → wrong network_id.
        status = exc.status_code
        if status in (401, 403):
            return False, f"Broadstreet auth rejected (status={status}): {exc}"
        if status == 404:
            return False, f"Broadstreet network {network_id!r} not found (status=404)"
        return False, f"Broadstreet API error (status={status}): {exc}"
    except Exception as exc:
        logger.warning("Broadstreet get_network() transport failure: %s", exc)
        return False, f"Broadstreet transport failure: {type(exc).__name__}: {exc}"

    return True, None


def _test_springserve(config: dict[str, Any]) -> tuple[bool, str | None]:
    """Authentication + scope probe for SpringServe.

    Two-step probe mirroring the FreeWheel pattern:

    1. ``GET /auth/check`` via the transport's token cache — the first
       authenticated call mints (or validates) the bearer. Email-grant
       credentials hit ``POST /auth`` here; bad password surfaces as
       :class:`SpringServeAuthError`.
    2. ``GET /supply/tags?per_page=1`` — proves the bearer is scoped to
       a publisher account with supply inventory. Analogous to FreeWheel's
       ``list_sites`` probe and GAM's ``getCurrentNetwork``.
    """
    email = config.get("email")
    password = config.get("password")
    api_token = config.get("api_token")
    if not ((email and password) or api_token):
        return False, "SpringServe config requires either (email + password) or api_token"

    try:
        from src.adapters.springserve._transport import (
            SpringServeAuthError,
            SpringServeError,
            SpringServeForbiddenError,
        )
        from src.adapters.springserve.client import SpringServeClient
    except Exception as exc:  # pragma: no cover - environmental
        logger.exception("SpringServe imports failed")
        return False, f"SpringServe client unavailable: {exc}"

    try:
        client = SpringServeClient(api_token=api_token, email=email, password=password)
    except Exception as exc:  # pragma: no cover - construction-time failures are rare
        logger.warning("SpringServe client construction failed: %s", exc)
        return False, f"SpringServe client construction failed: {type(exc).__name__}: {exc}"

    # Single call exercises both auth (token mint, if password grant) and
    # scope (a 403 here means the bearer is valid but can't see supply
    # inventory for the configured account). client.probe() returns
    # (status_code, body) without raising on non-2xx — auth/mint
    # failures still raise, which we surface separately.
    try:
        status, body = client.probe("GET", "/supply/tags?per_page=1")
    except SpringServeAuthError as exc:
        return False, f"SpringServe auth rejected: {exc}"
    except SpringServeForbiddenError as exc:
        return False, f"SpringServe bearer lacks entitlements: {exc}"
    except SpringServeError as exc:
        return False, f"SpringServe API error on auth (status={exc.status_code}): {exc}"
    except Exception as exc:
        logger.warning("SpringServe probe transport failure: %s", exc)
        return False, f"SpringServe transport failure: {type(exc).__name__}: {exc}"

    if status == 200:
        return True, None
    if status in (401, 403):
        return False, (
            f"SpringServe bearer cannot read supply inventory (status={status}). "
            f"Verify the token is for the intended publisher account."
        )
    return False, f"SpringServe supply probe returned status={status}: {body[:200]}"


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

    if adapter_type == "freewheel":
        return _preview_freewheel(config)

    if adapter_type == "broadstreet":
        return _preview_broadstreet(config)

    if adapter_type == "springserve":
        return _preview_springserve(config)

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
        )

    try:
        from src.adapters.freewheel._transport import FreeWheelAuthError, FreeWheelError
        from src.adapters.freewheel.client import FreeWheelClient
        from src.adapters.freewheel.schemas import FREEWHEEL_HOSTS
    except Exception as exc:  # pragma: no cover - environmental
        logger.exception("FreeWheel imports failed")
        return AdapterPreview(ok=False, error=f"FreeWheel client unavailable: {exc}")

    environment = config.get("environment", "production")
    base_url = FREEWHEEL_HOSTS.get(environment, FREEWHEEL_HOSTS["production"])

    try:
        client = FreeWheelClient(api_token=api_token, username=username, password=password, base_url=base_url)
        token_info = client.token_info()
    except FreeWheelAuthError as exc:
        return AdapterPreview(ok=False, error=f"FreeWheel auth rejected: {exc}")
    except FreeWheelError as exc:
        return AdapterPreview(ok=False, error=f"FreeWheel API error (status={exc.status_code}): {exc}")
    except Exception as exc:
        logger.warning("FreeWheel token_info() failed: %s", exc)
        return AdapterPreview(ok=False, error=f"FreeWheel transport failure: {type(exc).__name__}: {exc}")

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
        return AdapterPreview(ok=False, error="Broadstreet network_id is required")
    if not api_key:
        return AdapterPreview(ok=False, error="Broadstreet api_key is required")

    try:
        from src.adapters.broadstreet.client import BroadstreetAPIError, BroadstreetClient
    except Exception as exc:  # pragma: no cover - environmental
        logger.exception("Broadstreet imports failed")
        return AdapterPreview(ok=False, error=f"Broadstreet client unavailable: {exc}")

    try:
        client = BroadstreetClient(access_token=str(api_key), network_id=str(network_id))
        network = client.get_network()
    except BroadstreetAPIError as exc:
        status = exc.status_code
        if status in (401, 403):
            return AdapterPreview(ok=False, error=f"Broadstreet auth rejected (status={status}): {exc}")
        if status == 404:
            return AdapterPreview(ok=False, error=f"Broadstreet network {network_id!r} not found")
        return AdapterPreview(ok=False, error=f"Broadstreet API error (status={status}): {exc}")
    except Exception as exc:
        logger.warning("Broadstreet get_network() failed: %s", exc)
        return AdapterPreview(ok=False, error=f"Broadstreet transport failure: {type(exc).__name__}: {exc}")

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
        return AdapterPreview(ok=False, error=f"SpringServe client unavailable: {exc}")

    try:
        client = SpringServeClient(api_token=api_token, email=email, password=password)
        status, body = client.probe("GET", "/supply/tags?per_page=1")
    except SpringServeAuthError as exc:
        return AdapterPreview(ok=False, error=f"SpringServe auth rejected: {exc}")
    except SpringServeForbiddenError as exc:
        return AdapterPreview(ok=False, error=f"SpringServe bearer lacks entitlements: {exc}")
    except SpringServeError as exc:
        return AdapterPreview(ok=False, error=f"SpringServe API error (status={exc.status_code}): {exc}")
    except Exception as exc:
        logger.warning("SpringServe probe failed: %s", exc)
        return AdapterPreview(ok=False, error=f"SpringServe transport failure: {type(exc).__name__}: {exc}")

    if status == 200:
        return AdapterPreview(
            ok=True,
            network_name=email if email else None,
            network_code=None,
            currency_code=None,
            time_zone=None,
            inventory_reachable=True,
        )
    if status in (401, 403):
        return AdapterPreview(
            ok=False,
            error=f"SpringServe bearer cannot read supply inventory (status={status})",
        )
    return AdapterPreview(ok=False, error=f"SpringServe supply probe returned status={status}: {body[:200]}")
