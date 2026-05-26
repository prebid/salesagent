"""Buyer-advertiser routing chain.

Resolves the GAM advertiser id for an inline ``AccountReference``
(operator + brand + sandbox) plus the calling agent's ``principal_id``
by walking a most-specific-wins precedence chain. Agent-tagged rules
beat agent-agnostic rules at every brand-specificity tier:

    sandbox carve-out (early return)
        -> agent + operator + brand_house + brand_id    (exact, agent-specific)
        -> agent + operator + brand_house + NULL        (house, agent-specific)
        -> agent + operator + NULL + NULL               (operator, agent-specific)
        -> NULL  + operator + brand_house + brand_id    (exact, any agent)
        -> NULL  + operator + brand_house + NULL        (house, any agent)
        -> NULL  + operator + NULL + NULL               (operator, any agent)
        -> Tenant.default_gam_advertiser_id
        -> raise TENANT_NOT_ACTIVATED

The function returns a (advertiser_id, resolved_via) tuple. ``resolved_via``
is stamped on Account rows at first-creation so /recent-buyers can color-
code matches vs fall-throughs without re-running the chain. The stamp
collapses agent-specific and agent-agnostic matches into the same value
(``exact``/``house``/``operator``) — the matched rule's ``principal_id``
is the source of truth for callers who need to surface that distinction.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from typing import Literal

from adcp.types.generated_poc.core.account_ref import AccountReference2
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import (
    Account,
    AdapterConfig,
    AdvertiserRoutingRule,
    AgentAccountAccess,
    Principal,
    Tenant,
)
from src.core.exceptions import AdCPError, RecoveryHint
from src.core.helpers.account_provisioning import (
    _account_advertiser_id,
    _set_account_advertiser_mapping,
    gam_create_advertiser_companyservice,
)

logger = logging.getLogger(__name__)


ResolvedVia = Literal["account", "sandbox", "exact", "house", "operator", "default"]


class AdCPTenantNotActivated(AdCPError):
    """Raised when the routing chain falls through with no default
    advertiser set on the tenant.

    Implicit-activation contract (Sprint 1.8 Q3): no separate
    ``POST /activate`` — the buyer-protocol error path IS the
    tenant-activated check. Storefront's homepage checklist drives
    off ``Tenant.default_gam_advertiser_id`` being non-null.

    Recovery is ``terminal``: the buyer cannot self-recover —
    the publisher must finish onboarding (set a default advertiser
    or add a routing rule) before any buy on this tenant can succeed.
    """

    error_code = "TENANT_NOT_ACTIVATED"
    recovery: RecoveryHint = "terminal"

    # Back-compat: legacy callers read ``exc.code``; surface the same
    # value as ``error_code`` so existing code paths keep working.
    @property
    def code(self) -> str:
        return self.error_code


# ---------------------------------------------------------------------------
# Sandbox advertiser cache
# ---------------------------------------------------------------------------


_SANDBOX_NAME_TEMPLATE = "Sandbox (auto-created by salesagent — do not bill)"


def ensure_sandbox_advertiser(session: Session, tenant_id: str, *, dry_run: bool = False) -> str:
    """Return the per-tenant sandbox GAM advertiser id, creating it lazily.

    Sprint 1.6 deferred the sandbox advertiser cache to a follow-up; this
    helper closes that loop. The id is cached on
    ``AdapterConfig.gam_sandbox_advertiser_id`` so subsequent sandbox
    calls hit the cache without re-creating.

    The sandbox advertiser is a real GAM company (CompanyService.createCompanies),
    just keyed off a special name template so reports can filter it out. We
    don't bill against it, don't count it in inventory caps, don't pollute
    revenue rollups.

    ``dry_run=True`` returns a synthetic id for dev/test environments — no
    GAM API call. Caller is responsible for committing the session
    (the caller's UoW manages transactionality).
    """
    adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first()
    if adapter is None:
        raise AdCPError(
            details={"code": "ADAPTER_NOT_CONFIGURED"},
            message=(
                f"Tenant {tenant_id!r} has no AdapterConfig — cannot resolve "
                f"sandbox advertiser. Provision adapter first."
            ),
        )

    if adapter.gam_sandbox_advertiser_id:
        return adapter.gam_sandbox_advertiser_id

    if adapter.adapter_type != "google_ad_manager":
        # Mock / non-GAM adapters: synthesize a stable sandbox id so
        # downstream code paths don't branch on adapter type.
        synthetic = f"sandbox-{tenant_id}"
        adapter.gam_sandbox_advertiser_id = synthetic
        return synthetic

    if dry_run:
        synthetic = f"sandbox-dry-run-{secrets.token_hex(4)}"
        adapter.gam_sandbox_advertiser_id = synthetic
        return synthetic

    network_code = adapter.gam_network_code
    if not network_code:
        raise AdCPError(
            details={"code": "ADAPTER_NOT_CONFIGURED"},
            message=(f"Tenant {tenant_id!r} GAM adapter has no network_code — cannot create sandbox advertiser."),
        )

    config = {
        "network_code": network_code,
        "service_account_json": adapter.gam_service_account_json,
        "refresh_token": adapter.gam_refresh_token,
    }
    new_id = gam_create_advertiser_companyservice(
        network_code=str(network_code),
        config=config,
        name=_SANDBOX_NAME_TEMPLATE,
        dry_run=False,
    )
    adapter.gam_sandbox_advertiser_id = new_id
    logger.info("[ROUTING] cached sandbox advertiser %s for tenant %s", new_id, tenant_id)
    return new_id


# ---------------------------------------------------------------------------
# Routing chain
# ---------------------------------------------------------------------------


def _find_rule(
    session: Session,
    tenant_id: str,
    operator_domain: str,
    brand_house: str | None,
    brand_id: str | None,
    *,
    principal_id: str | None = None,
) -> AdvertiserRoutingRule | None:
    """Find a routing rule matching the natural key exactly.

    NULL participates in the match — passing ``brand_house=None`` matches
    only rows where brand_house IS NULL (operator-wildcard rule), not rows
    with a populated brand_house. Same for ``principal_id``: passing
    ``None`` matches agent-agnostic rules; passing a value matches only
    rules tagged for that specific agent.
    """
    stmt = select(AdvertiserRoutingRule).where(
        AdvertiserRoutingRule.tenant_id == tenant_id,
        AdvertiserRoutingRule.operator_domain == operator_domain,
    )
    stmt = (
        stmt.where(AdvertiserRoutingRule.principal_id.is_(None))
        if principal_id is None
        else stmt.where(AdvertiserRoutingRule.principal_id == principal_id)
    )
    stmt = (
        stmt.where(AdvertiserRoutingRule.brand_house.is_(None))
        if brand_house is None
        else stmt.where(AdvertiserRoutingRule.brand_house == brand_house)
    )
    stmt = (
        stmt.where(AdvertiserRoutingRule.brand_id.is_(None))
        if brand_id is None
        else stmt.where(AdvertiserRoutingRule.brand_id == brand_id)
    )
    return session.scalars(stmt).first()


def resolve_advertiser_for_buy(
    session: Session,
    tenant_id: str,
    account_ref: AccountReference2,
    *,
    principal_id: str | None = None,
    dry_run: bool = False,
) -> tuple[str, ResolvedVia]:
    """Resolve the GAM advertiser id for an inline AccountReference.

    Returns ``(advertiser_id, resolved_via)``. ``resolved_via`` is one of
    ``"sandbox" | "exact" | "house" | "operator" | "default"``; callers
    persist it on the Account row so /recent-buyers can render match
    quality without re-walking the chain. Agent-specific and
    agent-agnostic matches collapse to the same stamp — the matched
    rule's ``principal_id`` is the source of truth for callers who care.

    ``principal_id`` is the calling buyer agent (from auth context). When
    set, agent-tagged rules are tried first at every brand-specificity
    tier; agent-agnostic rules (``principal_id IS NULL``) are tried as a
    fallback. When unset (default), only agent-agnostic rules match —
    preserves Sprint 1.8 behavior for callers that don't pass an agent.

    Caller is responsible for transaction management — this function only
    reads (and mutates ``AdapterConfig.gam_sandbox_advertiser_id`` lazily
    via ``ensure_sandbox_advertiser``).

    Raises:
        :class:`AdCPTenantNotActivated`: routing chain fell through with
        no default advertiser set on the tenant.
    """
    # Sandbox carve-out (Q4): never consult routing rules or default.
    if account_ref.sandbox:
        sandbox_id = ensure_sandbox_advertiser(session, tenant_id, dry_run=dry_run)
        return sandbox_id, "sandbox"

    operator = account_ref.operator
    brand_house = account_ref.brand.domain
    brand_id_raw = account_ref.brand.brand_id
    brand_id: str | None
    if brand_id_raw is None:
        brand_id = None
    elif hasattr(brand_id_raw, "root"):
        brand_id = str(brand_id_raw.root)
    else:
        brand_id = str(brand_id_raw)

    # Agent-tagged tier walks first (1-3); agent-agnostic tier (4-6) is
    # the fallback. When principal_id is None the agent-tagged tier is
    # skipped entirely and we match Sprint 1.8 behavior exactly.
    agent_tiers: list[str | None] = [principal_id, None] if principal_id is not None else [None]

    for agent in agent_tiers:
        # 1/4. Exact match (agent + operator + brand_house + brand_id).
        if brand_id is not None:
            rule = _find_rule(session, tenant_id, operator, brand_house, brand_id, principal_id=agent)
            if rule is not None:
                return rule.gam_advertiser_id, "exact"

        # 2/5. House wildcard (agent + operator + brand_house + null).
        rule = _find_rule(session, tenant_id, operator, brand_house, None, principal_id=agent)
        if rule is not None:
            return rule.gam_advertiser_id, "house"

        # 3/6. Operator wildcard (agent + operator + null + null).
        rule = _find_rule(session, tenant_id, operator, None, None, principal_id=agent)
        if rule is not None:
            return rule.gam_advertiser_id, "operator"

    # 7. Tenant default.
    tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
    if tenant is None:
        raise AdCPError(
            details={"code": "TENANT_NOT_FOUND"},
            message=f"Tenant {tenant_id!r} not found while resolving routing chain.",
        )
    if tenant.default_gam_advertiser_id:
        return tenant.default_gam_advertiser_id, "default"

    # 8. No fallback — implicit activation gate.
    raise AdCPTenantNotActivated(
        message=(
            f"Tenant {tenant_id!r} has no default_gam_advertiser_id and no "
            f"matching routing rule for (principal={principal_id!r}, "
            f"operator={operator!r}, brand_house={brand_house!r}, "
            f"brand_id={brand_id!r}). Publisher must set a default "
            f"advertiser before this tenant can buy media."
        ),
        details={
            "principal_id": principal_id,
            "operator": operator,
            "brand_house": brand_house,
            "brand_id": brand_id,
            "tenant_id": tenant_id,
            "suggestion": "Ask the publisher to set a default advertiser or add an advertiser routing rule.",
        },
    )


# ---------------------------------------------------------------------------
# Account auto-creation (first-buy from an unmapped triple)
# ---------------------------------------------------------------------------


def create_account_from_routing(
    session: Session,
    tenant_id: str,
    account_ref: AccountReference2,
    *,
    principal_id: str | None = None,
    dry_run: bool = False,
) -> Account:
    """Create an Account row for an unmapped (operator, brand, sandbox)
    triple, with the advertiser id resolved by the routing chain and
    ``resolved_via`` stamped.

    Used by the natural-key resolver in ``account_helpers.resolve_account``
    when no existing Account matches. Caller commits the session — this
    function only adds the row to the unit-of-work.

    The resulting Account has:
    - ``status="active"`` (advertiser is mapped, no pending_provision)
    - ``platform_mappings.google_ad_manager.advertiser_id`` set
    - ``resolved_via`` set to the chain step that picked the advertiser
    - ``billing="agent"`` if principal_id passed (sprint 1.6 split),
      else ``"operator"``
    """
    advertiser_id, resolved_via = resolve_advertiser_for_buy(
        session, tenant_id, account_ref, principal_id=principal_id, dry_run=dry_run
    )

    brand_id_raw = account_ref.brand.brand_id
    brand_id: str | None
    if brand_id_raw is None:
        brand_id = None
    elif hasattr(brand_id_raw, "root"):
        brand_id = str(brand_id_raw.root)
    else:
        brand_id = str(brand_id_raw)

    brand_dict: dict[str, str] = {"domain": account_ref.brand.domain}
    if brand_id is not None:
        brand_dict["brand_id"] = brand_id

    name = f"{account_ref.operator} × {account_ref.brand.domain}"
    if account_ref.sandbox:
        name = f"{name} (sandbox)"
    elif principal_id:
        name = f"{name} ({principal_id})"

    billing = "agent" if principal_id else "operator"

    account = Account(
        tenant_id=tenant_id,
        account_id=f"acct_{secrets.token_hex(6)}",
        name=name,
        status="active",
        operator=account_ref.operator,
        brand=brand_dict,
        billing=billing,
        sandbox=bool(account_ref.sandbox),
        principal_id=principal_id if billing == "agent" else None,
        platform_mappings={},
        resolved_via=resolved_via,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    _set_account_advertiser_mapping(
        account,
        advertiser_id,
        advertiser_name=None,
        source=f"auto:routing-chain:{resolved_via}",
    )
    session.add(account)

    # Grant the buyer agent access to the new Account so subsequent
    # AccountRepository.has_access() checks succeed without a manual
    # provisioning step. Operator-billed accounts (no principal_id)
    # skip this — access is governed by tenant-wide auth instead.
    #
    # Defense-in-depth: only grant if the Principal row exists. On
    # embedded tenants Principals are provisioned out-of-band by the
    # platform via the Tenant Management API (initial_principal on
    # /provision, or POST /principals); on open instances they're
    # created in the admin UI. Skipping the FK insert when the row is
    # missing avoids killing the buy flow if provisioning is racy or
    # incomplete; the access check downstream surfaces as a cleaner
    # authorization error than an FK violation.
    if principal_id:
        principal_exists = session.scalars(
            select(Principal.principal_id).filter_by(tenant_id=tenant_id, principal_id=principal_id)
        ).first()
        if principal_exists:
            session.add(
                AgentAccountAccess(
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    account_id=account.account_id,
                )
            )
        else:
            logger.warning(
                "[ROUTING] skipping AgentAccountAccess grant for tenant=%s "
                "principal=%s (Principal row not found — auth bypass "
                "should have created it)",
                tenant_id,
                principal_id,
            )

    logger.info(
        "[ROUTING] auto-created Account %s for tenant %s via %s -> advertiser %s",
        account.account_id,
        tenant_id,
        resolved_via,
        advertiser_id,
    )
    return account


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


__all__ = [
    "AdCPTenantNotActivated",
    "ResolvedVia",
    "create_account_from_routing",
    "ensure_sandbox_advertiser",
    "resolve_advertiser_for_buy",
    # Re-exported so callers reading existing Accounts don't need a second import.
    "_account_advertiser_id",
]
