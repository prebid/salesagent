"""Adapter instance creation and configuration helpers."""

from __future__ import annotations

from typing import Any

from src.adapters import get_adapter_class
from src.adapters.base import AdServerAdapter
from src.adapters.google_ad_manager import GoogleAdManager
from src.adapters.kevel import Kevel
from src.adapters.mock_ad_server import MockAdServer as MockAdServerAdapter
from src.adapters.triton_digital import TritonDigital
from src.core.database.database_session import get_db_session
from src.core.schemas import Principal


def _mock_adapter_config(*, manual_approval_required: bool) -> dict[str, Any]:
    """Config dict for MockAdServerAdapter.

    Unifies exactly two construction paths: the sandbox short-circuit (below, in
    ``get_adapter``) and the tenant-configured mock branch (``adapter_type ==
    "mock"`` under the ``AdapterConfig`` lookup). ``manual_approval_required`` is the
    one field those two intentionally set differently; naming it as an argument here
    — rather than each branch hand-building its own dict literal — means a field
    added to one automatically reaches the other, where the two config dicts were
    previously typed out separately and could drift on any key besides this one
    without either branch noticing.

    TWO further mock constructions exist in ``get_adapter`` and are NOT unified here:
    the ``if selected_adapter == "mock":`` branch and the final ``else:`` "Default to
    mock for unsupported adapters" branch both instantiate ``MockAdServerAdapter``
    directly from ``adapter_config`` without going through this helper, so neither
    sets ``manual_approval_required`` when they're reached with the bare dict. That
    bare dict is not built by the ``if not selected_adapter:`` fallback — it comes
    from the unconditional ``adapter_config: dict[str, Any] = {"enabled": True}``
    initializer set right after ``with get_db_session() as session:``, before
    ``if config_row:`` even runs; ``if not selected_adapter:`` only re-applies the
    same literal when ``adapter_config`` is falsy at that point. Whether that fallback
    is reachable depends on the SHAPE of ``tenant`` at ``get_adapter``'s entry, not on
    "mock tenant" alone: the ORM branch (``tenant.ad_server or "mock"``) coerces a
    falsy ``ad_server`` to ``"mock"`` immediately, so ``selected_adapter`` is already
    truthy by the time the fallback is checked — dead code there. The dict branch
    (``tenant.get("ad_server", "mock")``) only substitutes the default when the
    ``"ad_server"`` key is ABSENT, not when its value is ``None`` — a tenant row with
    ``ad_server IS NULL`` serializes to a dict carrying ``"ad_server": None``, leaving
    ``selected_adapter = None`` and reaching the fallback for real. Any caller passing
    a dict-shaped tenant is subject to this, not just the ones enumerated today.
    Both non-unified branches are general "no adapter configured" / "unsupported
    adapter type" default logic, not sandbox-specific, so folding them into this
    helper is out of scope here (tracked as #1976).

    One field the sandbox branch sets is deliberately NOT routed through here:
    ``supported_pricing_models``, the constraint profile of the tenant's declared adapter,
    is a ``MockAdServer`` constructor argument rather than a config key. It must NOT reach
    the other construction paths — a tenant that configured the mock as its own ad server
    really may buy all seven models, and pushing the profile into this shared dict would
    hand every mock construction a constraint that only the sandbox substitution needs.
    """
    return {"enabled": True, "manual_approval_required": manual_approval_required}


def get_adapter(
    principal: Principal,
    dry_run: bool = False,
    testing_context: Any = None,
    tenant: Any = None,
    *,
    # REQUIRED, no default. A default is what makes "forgot to decide" indistinguishable
    # from "decided live" — and the wrong one books a real campaign for a sandbox buyer.
    # The structural guard covers src/ only, so a default here silently un-guards every
    # caller outside it. Same reasoning as the required `sanitize` on
    # reject_unsafe_outbound_webhook_url: when the two values fail asymmetrically, the
    # safe move is to make omission impossible rather than to pick a side.
    sandbox: bool,
) -> MockAdServerAdapter | GoogleAdManager | Kevel | TritonDigital:
    """Get the appropriate adapter instance for the selected adapter type.

    Args:
        principal: The authenticated principal
        dry_run: Whether to run in dry-run mode
        testing_context: Optional test context for simulations
        tenant: Tenant context (from identity.tenant). Falls back to ContextVar if not provided.
        sandbox: True when the resolved account is a sandbox account
            (``identity.sandbox``). Forces the mock adapter so no real ad-platform call
            is ever made. AdCP 3.1.1 ``sandbox.mdx`` §Seller implementation: sandbox
            requests "MUST NOT make real ad platform API calls (no real orders, line
            items, etc.)" and "MUST NOT charge real money or create real billing records".
            Keyword-only and explicit at every call site — enforced by
            ``tests/unit/test_architecture_get_adapter_sandbox.py`` so a new call site
            cannot silently default to the live adapter. The substitution covers who
            EXECUTES, not what the tenant may buy: the returned mock still answers
            ``get_supported_pricing_models()`` with the tenant's DECLARED adapter's models,
            because the same spec section requires sandbox to "validate inputs the same way
            as production".
    """
    import logging

    logger = logging.getLogger(__name__)

    if tenant is None:
        # Fallback for callers that haven't been updated yet (e.g., async approval handlers)
        from src.core.config_loader import get_current_tenant

        tenant = get_current_tenant()

    # Extract tenant_id and ad_server from tenant (supports both ORM model and dict)
    if isinstance(tenant, dict):
        tenant_id = tenant["tenant_id"]
        selected_adapter = tenant.get("ad_server", "mock")
    else:
        # ORM model (Tenant) — use attribute access
        tenant_id = tenant.tenant_id
        selected_adapter = tenant.ad_server or "mock"
    logger.info(f"[ADAPTER_SELECT] Initial selected_adapter from tenant.ad_server: {selected_adapter}")

    if sandbox:
        # Short-circuit BEFORE the AdapterConfig lookup: a sandbox account must never reach a
        # real adapter — not its API, not even its credentials. The mock adapter is the
        # simulator the spec asks for ("MUST return realistic response shapes with simulated
        # data"). Adapters are configured per-tenant, so this is the only layer that can tell
        # a sandbox request from a live one.
        #
        # The substitution covers EXECUTION only. Substituting the mock's own constraint
        # DECLARATION as well would make the sandbox more permissive than production — the
        # mock simulates all seven pricing models, so a cpcv buy a GAM tenant rejects would
        # be accepted here — and AdCP 3.1.1 sandbox.mdx §Seller implementation requires
        # sandbox to "validate inputs the same way as production" and to "Apply normal input
        # validation (sandbox does not bypass validation)". What a tenant may buy is a
        # property of the ad server it declared, so read that declaration off the declared
        # adapter's CLASS — no instance, no credentials — and hand it to the simulator.
        #
        # An ad_server this process cannot resolve to an ad-server adapter — unregistered,
        # or registered as something else entirely like "creative_engine" — is not an error
        # here: the live path's final else-branch runs those on the mock too, so declaring
        # anything narrower would make sandbox stricter than the production it mirrors.
        try:
            declared_adapter_class = get_adapter_class(selected_adapter or "mock")
        except ValueError:
            declared_adapter_class = MockAdServerAdapter
        if not issubclass(declared_adapter_class, AdServerAdapter):
            declared_adapter_class = MockAdServerAdapter
        logger.info("[ADAPTER_SELECT] sandbox account — forcing mock adapter, no real ad-platform calls")
        return MockAdServerAdapter(
            # manual_approval_required=False is deliberate, not inherited. The tenant's
            # own mock branch below defaults this to True "for safety" from
            # AdapterConfig; this branch cannot read that row (the whole point of
            # short-circuiting above is to touch no adapter config), so the two mock
            # adapters would differ on a field _create_media_buy_impl actually reads —
            # a sandbox buy auto-executing where the tenant's mock would queue for
            # approval. A simulator that parks the buyer's request awaiting a human
            # defeats what the sandbox is for, and the approval gate exists to protect
            # real spend, which this adapter cannot incur. Named here rather than left
            # to whatever _mock_adapter_config's caller happens to pass, so the
            # divergence is a decision the next reader can see, instead of one produced
            # by an absent key — a test asserting only "the mock was selected" cannot
            # tell those apart.
            _mock_adapter_config(manual_approval_required=False),
            principal,
            dry_run,
            tenant_id=tenant_id,
            strategy_context=testing_context,
            supported_pricing_models=declared_adapter_class.supported_pricing_models,
        )

    # Get adapter config via repository
    from src.core.database.repositories.adapter_config import AdapterConfigRepository

    targeting_config: dict[str, Any] | None = None
    naming_templates: tuple[str | None, str | None] | None = None

    with get_db_session() as session:
        repo = AdapterConfigRepository(session, tenant_id)
        config_row = repo.find_by_tenant()

        adapter_config: dict[str, Any] = {"enabled": True}
        if config_row:
            adapter_type = config_row.adapter_type
            logger.info(f"[ADAPTER_SELECT] adapter_type from AdapterConfig: {adapter_type}")
            # Use adapter_type from AdapterConfig as the source of truth
            if adapter_type:
                selected_adapter = adapter_type
                logger.info(f"[ADAPTER_SELECT] Using AdapterConfig.adapter_type: {selected_adapter}")
            if adapter_type == "mock":
                # Default to True (require approval) for safety
                adapter_config = _mock_adapter_config(
                    manual_approval_required=(
                        config_row.mock_manual_approval_required
                        if config_row.mock_manual_approval_required is not None
                        else True
                    )
                )
            elif adapter_type == "google_ad_manager":
                adapter_config = repo.get_gam_config(config_row)
                targeting_config = repo.get_gam_targeting_config(config_row)
                naming_templates = repo.get_gam_naming_templates(config_row)

                # Get advertiser_id from principal's platform_mappings (per-principal, not tenant-level)
                # Support both old format (nested under "google_ad_manager") and new format (root "gam_advertiser_id")
                advertiser_id: str | None = None
                if principal.platform_mappings:
                    # Try nested format first
                    gam_mappings = principal.platform_mappings.get("google_ad_manager", {})
                    advertiser_id = gam_mappings.get("advertiser_id")
                    logger.info(
                        f"[ADAPTER_CONFIG] principal_id={principal.principal_id}, platform_mappings={principal.platform_mappings}, gam_mappings={gam_mappings}, advertiser_id={advertiser_id}"
                    )

                    # Fall back to root-level format if nested not found
                    if not advertiser_id:
                        advertiser_id = principal.platform_mappings.get("gam_advertiser_id")
                        logger.info(f"[ADAPTER_CONFIG] Fell back to root-level gam_advertiser_id: {advertiser_id}")

                    adapter_config["company_id"] = advertiser_id
                    logger.info(f"[ADAPTER_CONFIG] Set adapter_config['company_id']={advertiser_id}")
                else:
                    adapter_config["company_id"] = None
                    logger.info("[ADAPTER_CONFIG] principal.platform_mappings is None/empty, set company_id=None")
            elif adapter_type == "kevel":
                adapter_config["network_id"] = config_row.kevel_network_id or ""
                adapter_config["api_key"] = config_row.kevel_api_key or ""
                # Default to True (require approval) for safety
                adapter_config["manual_approval_required"] = (
                    config_row.kevel_manual_approval_required
                    if config_row.kevel_manual_approval_required is not None
                    else True
                )
            elif adapter_type == "triton":
                adapter_config["station_id"] = config_row.triton_station_id or ""
                adapter_config["api_key"] = config_row.triton_api_key or ""

    if not selected_adapter:
        # Default to mock if no adapter specified
        selected_adapter = "mock"
        if not adapter_config:
            adapter_config = {"enabled": True}

    # Create the appropriate adapter instance with tenant_id and testing context
    logger.info(f"[ADAPTER_SELECT] FINAL selected_adapter: {selected_adapter}")
    if selected_adapter == "mock":
        logger.info("[ADAPTER_SELECT] Instantiating MockAdServerAdapter")
        return MockAdServerAdapter(
            adapter_config, principal, dry_run, tenant_id=tenant_id, strategy_context=testing_context
        )
    elif selected_adapter == "google_ad_manager":
        # network_code is required for GoogleAdManager
        network_code = adapter_config.get("network_code")
        if not network_code or not isinstance(network_code, str):
            raise ValueError("network_code is required for GoogleAdManager adapter")

        logger.info("[ADAPTER_SELECT] Instantiating GoogleAdManager")
        logger.info(
            f"[ADAPTER_SELECT] GAM params: network_code={adapter_config.get('network_code')}, advertiser_id={adapter_config.get('company_id')}, trafficker_id={adapter_config.get('trafficker_id')}, dry_run={dry_run}"
        )
        return GoogleAdManager(
            adapter_config,
            principal,
            network_code=network_code,
            advertiser_id=adapter_config.get("company_id"),
            trafficker_id=adapter_config.get("trafficker_id"),
            dry_run=dry_run,
            tenant_id=tenant_id,
            targeting_config=targeting_config,
            naming_templates=naming_templates,
        )
    elif selected_adapter == "kevel":
        return Kevel(adapter_config, principal, dry_run, tenant_id=tenant_id)
    elif selected_adapter in ["triton", "triton_digital"]:
        return TritonDigital(adapter_config, principal, dry_run, tenant_id=tenant_id)
    else:
        # Default to mock for unsupported adapters
        return MockAdServerAdapter(
            adapter_config, principal, dry_run, tenant_id=tenant_id, strategy_context=testing_context
        )


def adapter_for_mode(
    principal: Principal,
    *,
    tenant: Any,
    testing_ctx: Any,
    sandbox: bool,
) -> MockAdServerAdapter | GoogleAdManager | Kevel | TritonDigital:
    """get_adapter() with the dry_run-from-testing_ctx boilerplate factored out.

    Buy-keyed callers that build one adapter per sandbox/live partition
    (get_media_buy_delivery, get_media_buy_deliveries) previously typed out this
    exact five-argument call at each site; a change to how dry_run derives from
    testing_ctx had to land at both by hand. sandbox is the caller's already-
    resolved mode (BuyKeyedSandboxMixin.sandbox_mode / partition_by_sandbox_mode) —
    this wrapper does not derive it.
    """
    return get_adapter(
        principal,
        dry_run=testing_ctx.dry_run if testing_ctx else False,
        testing_context=testing_ctx,
        tenant=tenant,
        sandbox=sandbox,
    )
