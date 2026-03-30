"""Adapter instance creation and configuration helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from adcp import AgentConfig


class _HasAgentFields(Protocol):
    """Structural type for objects with agent config fields (CreativeAgent, SignalsAgent)."""

    name: str
    agent_url: str
    auth: dict[str, Any] | None
    auth_header: str | None
    timeout: int


def build_agent_config(agent: _HasAgentFields) -> AgentConfig:
    """Build an adcp AgentConfig from any object with standard agent fields.

    Shared by CreativeAgentRegistry and SignalsAgentRegistry to avoid
    duplicating the auth-extraction and config-building logic.
    """
    from adcp import AgentConfig as _AgentConfig
    from adcp import Protocol as AdcpProtocol

    auth_type = "token"
    auth_token = None
    if agent.auth:
        auth_type = agent.auth.get("type", "token")
        auth_token = agent.auth.get("credentials")

    return _AgentConfig(
        id=agent.name,
        agent_uri=str(agent.agent_url),
        protocol=AdcpProtocol.MCP,
        auth_token=auth_token,
        auth_type=auth_type,
        auth_header=agent.auth_header or "x-adcp-auth",
        timeout=float(agent.timeout),
    )


from src.adapters.google_ad_manager import GoogleAdManager
from src.adapters.kevel import Kevel
from src.adapters.mock_ad_server import MockAdServer as MockAdServerAdapter
from src.adapters.triton_digital import TritonDigital
from src.core.database.database_session import get_db_session
from src.core.schemas import Principal


def get_adapter(
    principal: Principal, dry_run: bool = False, testing_context: Any = None, tenant: Any = None
) -> MockAdServerAdapter | GoogleAdManager | Kevel | TritonDigital:
    """Get the appropriate adapter instance for the selected adapter type.

    Args:
        principal: The authenticated principal
        dry_run: Whether to run in dry-run mode
        testing_context: Optional test context for simulations
        tenant: Tenant context (from identity.tenant). Falls back to ContextVar if not provided.
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
                adapter_config["dry_run"] = config_row.mock_dry_run or False
                # Default to True (require approval) for safety
                adapter_config["manual_approval_required"] = (
                    config_row.mock_manual_approval_required
                    if config_row.mock_manual_approval_required is not None
                    else True
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
