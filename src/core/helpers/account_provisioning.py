"""Sprint 1.6 piece C: Account → GAM advertiser resolution at first-buy.

Sits between ``_create_media_buy_impl`` and ``get_adapter()`` and answers
"which GAM advertiser_id should this media buy attach to?" — consulting
the Account row instead of (or before) the legacy
``Principal.platform_mappings.gam_advertiser_id`` lookup.

Branching matches ``docs/design/sync-accounts-advertiser-mapping.md``:

- ``Account.status == "active"`` + ``platform_mappings.google_ad_manager.advertiser_id``
  set → return it. (Pre-mapped via Tenant Mgmt API or auto-provisioned
  earlier.)
- ``Account.status == "pending_provision"`` + ``Tenant.auto_provision_advertisers=True``
  → call ``gam_create_advertiser_companyservice()`` to mint a new GAM
  advertiser, persist it on the Account row, flip status to ``active``,
  return.
- ``Account.status == "pending_provision"`` + ``auto_provision_advertisers=False``
  → return :class:`AdCPAccountNotProvisioned`. Caller raises; publisher
  ops maps manually via the Admin UI / Tenant Mgmt API.
- Sandbox accounts → not handled here yet (sprint 1.6 follow-up). The
  sandbox advertiser cache lives on ``AdapterConfig.gam_sandbox_advertiser_id``;
  resolver returns ``None`` and the caller falls back to legacy resolution
  for sandbox=True today. Updating sandbox to use the per-tenant cached
  advertiser is a separate landable.
- ``identity.account_id is None`` (legacy buyers without ``account`` in
  the request) → return ``None``. Caller uses the legacy
  Principal.platform_mappings path. **Backward-compatible — existing
  open-instance buyers see no behavior change.**
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Account, Tenant
from src.core.exceptions import AdCPError
from src.core.resolved_identity import ResolvedIdentity

logger = logging.getLogger(__name__)


_ACCOUNT_GAM_KEY = "google_ad_manager"


class AdCPAccountNotProvisioned(AdCPError):
    """Raised when a buyer references an Account in ``pending_provision``
    status on a tenant where ``auto_provision_advertisers=False``.

    The publisher must map a GAM advertiser to the Account via the Admin
    UI or ``POST /api/v1/tenant-management/tenants/{tid}/accounts`` before
    this account can buy media.
    """

    code = "ACCOUNT_NOT_PROVISIONED"


def _account_advertiser_id(account: Account) -> str | None:
    mappings = account.platform_mappings or {}
    return (mappings.get(_ACCOUNT_GAM_KEY) or {}).get("advertiser_id")


def _set_account_advertiser_mapping(
    account: Account, advertiser_id: str, advertiser_name: str | None, source: str
) -> None:
    """Stamp ``platform_mappings.google_ad_manager.advertiser_id`` on the
    Account row. Caller must be inside a UoW / committed session."""
    mappings = dict(account.platform_mappings or {})
    gam_block = dict(mappings.get(_ACCOUNT_GAM_KEY) or {})
    gam_block["advertiser_id"] = advertiser_id
    if advertiser_name is not None:
        gam_block["advertiser_name"] = advertiser_name
    gam_block["provisioned_at"] = datetime.now(UTC).isoformat()
    gam_block["provisioned_by"] = source
    mappings[_ACCOUNT_GAM_KEY] = gam_block
    account.platform_mappings = mappings


def gam_create_advertiser_companyservice(
    network_code: str,
    config: dict[str, Any],
    name: str,
    *,
    dry_run: bool = False,
) -> str:
    """Create a GAM advertiser via ``CompanyService.createCompanies``.

    On a name-collision (existing company with the same name and
    ``type='ADVERTISER'``), looks up the existing id and returns it
    instead of failing — the salesagent's view is "logical advertiser
    keyed by the natural name template," and a re-run shouldn't error
    just because someone provisioned the same one before.

    Returns the GAM advertiser id as a string. ``dry_run=True`` skips
    the live API call and returns a synthetic id so dev / sandbox
    environments don't burn real GAM company rows.
    """
    if dry_run:
        synthetic = f"dryrun_{abs(hash(name)) % 10**10}"
        logger.info(f"[gam_create_advertiser] dry_run: would create {name!r}, returning {synthetic!r}")
        return synthetic

    from src.adapters.gam.client import GAMClientManager

    manager = GAMClientManager(config=config, network_code=network_code)
    client = manager.get_client()
    company_service = client.GetService("CompanyService")
    try:
        result = company_service.createCompanies([{"name": name, "type": "ADVERTISER"}])
        if not result:
            raise RuntimeError(f"GAM CompanyService.createCompanies returned empty for {name!r}")
        new_id = str(result[0]["id"])
        logger.info(f"[gam_create_advertiser] created GAM advertiser {new_id} ({name!r})")
        return new_id
    except Exception as exc:
        # Name-collision attach. GAM raises an UNIQUE_NAME error inside an
        # ApplicationException; rather than parse the SOAP fault we just
        # query for the existing company by name and return its id. If THAT
        # fails too, re-raise — caller treats this as a hard provisioning
        # error.
        if "UNIQUE_NAME" not in str(exc) and "already exists" not in str(exc).lower():
            raise

        from googleads import ad_manager

        statement_builder = ad_manager.StatementBuilder()
        statement_builder.Where("type = :type AND name = :name").WithBindVariable(
            "type", "ADVERTISER"
        ).WithBindVariable("name", name)
        existing = company_service.getCompaniesByStatement(statement_builder.ToStatement())
        if existing and getattr(existing, "results", None):
            attach_id = str(existing.results[0]["id"])
            logger.warning(f"[gam_create_advertiser] name collision on {name!r} — attaching existing id {attach_id}")
            return attach_id
        raise


def resolve_account_advertiser(
    identity: ResolvedIdentity,
    *,
    adapter_config: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> str | None:
    """Resolve the GAM advertiser id for ``identity.account_id``.

    Returns:
        - The advertiser id (string) when the Account is mapped (now or
          via auto-provision).
        - ``None`` when there's no Account context (legacy buyers) or
          the Account is sandbox/non-GAM — caller falls back to legacy
          ``Principal.platform_mappings`` resolution.

    Raises:
        :class:`AdCPAccountNotProvisioned` when an Account is in
        ``pending_provision`` status on a tenant that doesn't auto-provision.
    """
    account_id = identity.account_id
    if account_id is None:
        return None

    tenant_id = identity.tenant_id
    if tenant_id is None:
        return None

    with get_db_session() as session:
        account = session.scalars(select(Account).filter_by(tenant_id=tenant_id, account_id=account_id)).first()
        if account is None:
            # Account ref didn't resolve to a row — that's a buyer-side
            # ref-validation failure that's surfaced upstream of here.
            # Returning None lets the caller fall through; if there's no
            # principal mapping either, the adapter raises its own error.
            return None

        # Sandbox accounts: deferred to a follow-up. Returning None means
        # the legacy Principal path takes over for now.
        if account.sandbox:
            return None

        # Active + already mapped → fast path.
        if account.status == "active":
            return _account_advertiser_id(account)

        # Pending provision: branch on tenant policy.
        if account.status == "pending_provision":
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if tenant is None:
                return None

            if not tenant.auto_provision_advertisers:
                raise AdCPAccountNotProvisioned(
                    f"Account {account_id!r} (operator={account.operator!r}, "
                    f"brand={account.brand}) has no GAM advertiser mapped. Publisher must map "
                    f"manually via the Admin UI before this account can buy media. "
                    f"Tenant {tenant_id!r} has auto_provision_advertisers=False."
                )

            # Auto-provision path. Use the same naming template the design
            # doc proposes; embeds operator + brand + (agent for billing=agent)
            # so collisions across the publisher's network are unlikely.
            name = _build_advertiser_name(account)
            adapter_config = adapter_config or {}
            network_code = adapter_config.get("network_code")
            if not network_code and tenant.ad_server == "google_ad_manager":
                # Fall back to AdapterConfig if caller didn't pass it.
                from src.adapters.gam import AdapterConfig as GamConfig  # type: ignore[attr-defined] # noqa: F401
                from src.core.database.models import AdapterConfig as AdapterConfigModel

                config_row = session.scalars(select(AdapterConfigModel).filter_by(tenant_id=tenant_id)).first()
                if config_row is None or not config_row.gam_network_code:
                    raise AdCPAccountNotProvisioned(
                        f"Account {account_id!r} requires GAM advertiser provisioning but "
                        f"tenant {tenant_id!r} has no AdapterConfig.gam_network_code configured."
                    )
                network_code = config_row.gam_network_code
                adapter_config = {
                    "network_code": network_code,
                    "service_account_json": config_row.gam_service_account_json,
                    "refresh_token": config_row.gam_refresh_token,
                }

            new_advertiser_id = gam_create_advertiser_companyservice(
                network_code=str(network_code),
                config=adapter_config,
                name=name,
                dry_run=dry_run,
            )

            _set_account_advertiser_mapping(
                account, new_advertiser_id, advertiser_name=name, source="auto:create_media_buy"
            )
            account.status = "active"
            account.updated_at = datetime.now(UTC)
            session.commit()
            return new_advertiser_id

    return None


def _build_advertiser_name(account: Account) -> str:
    """Naming template per the design doc § Storage."""
    operator = account.operator or "unknown"
    brand = account.brand
    domain = (
        brand.get("domain")
        if isinstance(brand, dict)
        else getattr(brand, "domain", None) if brand is not None else None
    )
    domain = domain or "unknown"
    if account.billing == "agent" and account.principal_id:
        return f"{operator} × {domain} ({account.principal_id})"
    return f"{operator} × {domain}"
