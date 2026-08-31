"""Account resolution + seller billing-policy helpers.

Two responsibilities, both keyed off the seller's account model:

* ``resolve_account`` bridges an ``AccountReference`` from a request payload to a
  validated ``account_id`` string (used by ``_create_media_buy_impl``,
  ``_sync_creatives_impl``, and ``_sync_governance_impl``).
* ``resolve_supported_billing`` / ``SELLER_ACCOUNT_BILLING`` are the SINGLE source of
  truth for the account-billable parties this seller accepts — consumed by BOTH the
  get_adcp_capabilities ``account.supported_billing`` honesty declaration and the
  sync_accounts billing gate, so declared == accepted (#1329).

"""

from __future__ import annotations

import logging

from adcp.types import AccountReference, AccountReferenceById, AccountReferenceByNaturalKey
from adcp.types.generated_poc.enums.billing_party import BillingParty

from src.core.database.repositories.account import AccountRepository
from src.core.exceptions import (
    AdCPAccountAmbiguousError,
    AdCPAccountNotFoundError,
    AdCPAccountPaymentRequiredError,
    AdCPAccountSetupRequiredError,
    AdCPAccountSuspendedError,
    AdCPAuthorizationError,
    AdCPConfigurationError,
)
from src.core.resolved_identity import ResolvedIdentity
from src.core.tenant_context import TenantContext, TenantLike

logger = logging.getLogger(__name__)

# The billing parties this seller can bill at the ACCOUNT level. The accounts.billing
# CHECK constraint (ck_accounts_billing) permits only these, so this is the honest,
# hand-maintained mirror of that constraint. A tenant's ``supported_billing`` may ALSO
# carry media-buy-level parties (e.g. ``advertiser``, used by create_media_buy) that are
# not account-billable — those are intersected out for the account context.
SELLER_ACCOUNT_BILLING: list[BillingParty] = [BillingParty.operator, BillingParty.agent]
_PERMITTED_ACCOUNT_BILLING: frozenset[str] = frozenset(b.value for b in SELLER_ACCOUNT_BILLING)


def resolve_supported_billing(tenant: TenantLike | None) -> list[BillingParty]:
    """The account-billable parties this seller accepts — the SINGLE source of truth.

    Consumed by BOTH the get_adcp_capabilities ``account.supported_billing`` honesty
    declaration (what the seller advertises) and the sync_accounts ``billing``
    enforcement (what it accepts), so declared == accepted (#1329). The 3.1.1
    ``account.supported_billing`` contract is "the buyer must pass one of these values in
    sync_accounts", so the two MUST agree.

    Resolution:

    * unset (``None`` / absent) → the default ``SELLER_ACCOUNT_BILLING`` ({operator,
      agent}). The accounts.billing ck constraint allows only these, so this is the honest
      accepted set — not "accept everything" (which would let a ``advertiser`` account pass
      validation and then fail the persist constraint).
    * a configured list with ≥1 account-billable party → that intersection (a tenant may
      narrow, and any non-account party such as ``advertiser`` is dropped for this
      context, never advertised as account-billable).
    * ANY explicitly configured list that yields NO account-billable party — an empty
      list ``[]`` (the pinned 3.1.1 ``account.supported_billing`` is ``minItems: 1``, so an
      empty declaration is not spec-expressible), a list naming only non-account parties
      (``["advertiser"]``), or a typo (``["bogus"]``) → raise ``AdCPConfigurationError``
      (TERMINAL). This is a SELLER misconfiguration the buyer cannot fix; emitting it as a
      buyer-correctable ``VALIDATION_ERROR`` (which is what a bare ``ValueError`` maps to)
      would tell the buyer to retry something only the operator can change, and letting
      ``[]`` through would surface downstream as a confusing ``account.supported_billing``
      ``minItems`` 400 on the capabilities wire. The operator diagnostic (the configured
      list + the internal constraint) goes to the LOG; the buyer message stays generic and
      discloses neither the tenant config nor the constraint identifier (#1329).
    """
    # Read supported_billing through the one path each carrier supports (#1329): a TenantContext
    # exposes the typed attribute; a LazyTenantContext (the proxy the MCP/A2A bridge actually
    # builds — NOT a TenantContext subclass, so this is the arm that fires on the live path) and a
    # legacy dict both expose ``.get``. The ``TenantLike`` union now NAMES the LazyTenantContext
    # that crosses the seam, so the signature describes what flows in production instead of
    # claiming a TenantContext does.
    if tenant is None:
        configured = None
    elif isinstance(tenant, TenantContext):
        configured = tenant.supported_billing
    else:
        configured = tenant.get("supported_billing")
    if configured is None:
        return list(SELLER_ACCOUNT_BILLING)
    resolved = [BillingParty(v) for v in configured if v in _PERMITTED_ACCOUNT_BILLING]
    if not resolved:
        logger.error(
            "tenant supported_billing configuration declares no account-billable party "
            "(type=%s, item_count=%s); accounts.billing accepts only %s "
            "(ck_accounts_billing) — fix the tenant's supported_billing configuration",
            type(configured).__name__,
            len(configured) if hasattr(configured, "__len__") else "unknown",
            sorted(_PERMITTED_ACCOUNT_BILLING),
        )
        raise AdCPConfigurationError("Seller account-billing configuration is invalid; contact the seller.")
    return resolved


def resolve_account(
    account_ref: AccountReference,
    identity: ResolvedIdentity,
    repo: AccountRepository,
) -> str:
    """Resolve an AccountReference to a validated account_id.

    Handles both variants of the AdCP AccountReference union:
    - AccountReferenceById: lookup by explicit account_id, verify agent access
    - AccountReferenceByNaturalKey: lookup by natural key (brand + operator + sandbox)

    Args:
        account_ref: AccountReference from the request payload.
        identity: Resolved identity with principal_id for access checks.
        repo: AccountRepository scoped to the correct tenant.

    Returns:
        Validated account_id string.

    Raises:
        AdCPAccountNotFoundError: Account not found by ID or natural key.
        AdCPAuthorizationError: Agent doesn't have access to the account.
        AdCPAccountAmbiguousError: Natural key matches multiple accounts.
        AdCPAccountSetupRequiredError: Account requires setup before use.
        AdCPAccountSuspendedError: Account is suspended.
        AdCPAccountPaymentRequiredError: Account has outstanding payment.
        AdCPAuthenticationError: No authenticated principal_id in the identity.
    """
    # Self-defending entry guard: reject a falsy principal_id up front so neither
    # variant runs a scoped query before rejection. The natural-key path skips the
    # access-scope join on a None principal and could otherwise disclose a
    # tenant-wide match count; require_principal_id raises AUTH_REQUIRED first (#1417).
    from src.core.auth import require_principal_id

    require_principal_id(identity)

    inner = account_ref.root

    if isinstance(inner, AccountReferenceById):
        return _resolve_by_id(inner.account_id, identity, repo)

    if isinstance(inner, AccountReferenceByNaturalKey):
        return _resolve_by_natural_key(inner, identity, repo)

    # Unreachable: AccountReference is a closed two-variant union validated by
    # Pydantic upstream. A fresh variant reaching here is an internal contract
    # violation, not a buyer-facing not-found — raise ValueError, not AdCPError.
    raise ValueError(f"Unsupported AccountReference variant: {type(inner)}")


def _check_account_status(account_id: str, status: str | None) -> None:
    """Raise if account status blocks operations."""
    if status == "pending_approval":
        # BR-UC-002 ext-s grades BOTH the top-level suggestion (POST-F3) and a
        # details payload carrying the setup instructions (POST-F2).
        setup_instructions = "Complete billing configuration before use."
        raise AdCPAccountSetupRequiredError(
            f"Account '{account_id}' requires setup.",
            suggestion=setup_instructions,
            details={"setup_instructions": setup_instructions},
        )
    if status == "suspended":
        raise AdCPAccountSuspendedError(
            f"Account '{account_id}' is suspended.",
            suggestion="Contact your account manager.",
        )
    if status == "payment_required":
        raise AdCPAccountPaymentRequiredError(
            f"Account '{account_id}' has outstanding payment.",
            suggestion="Resolve payment before use.",
        )


def _require_account_access(identity: ResolvedIdentity, account_id: str, repo: AccountRepository) -> None:
    """Raise if the agent's principal lacks access to the account.

    Self-defending: a falsy principal_id is rejected as AUTH_REQUIRED via
    require_principal_id, independent of any caller-side guard, so the access
    check can never be silently skipped by an empty/None principal (#1417).
    """
    from src.core.auth import require_principal_id

    principal_id = require_principal_id(identity)
    if not repo.has_access(principal_id, account_id):
        raise AdCPAuthorizationError(
            f"Agent '{principal_id}' does not have access to account '{account_id}'.",
            suggestion="Use list_accounts to find accounts accessible to this agent.",
        )


def _resolve_by_id(
    account_id: str,
    identity: ResolvedIdentity,
    repo: AccountRepository,
) -> str:
    """Resolve by explicit account_id — lookup + access check + status check."""
    account = repo.get_by_id(account_id)
    if account is None:
        raise AdCPAccountNotFoundError(
            f"Account '{account_id}' not found.",
            suggestion="Use list_accounts to find valid account IDs.",
        )

    _require_account_access(identity, account_id, repo)

    _check_account_status(account_id, account.status)

    return account.account_id


def _resolve_by_natural_key(
    ref: AccountReferenceByNaturalKey,
    identity: ResolvedIdentity,
    repo: AccountRepository,
) -> str:
    """Resolve by natural key (brand + operator + sandbox) — lookup + ambiguity check + access check + status check."""
    brand_domain = ref.brand.domain
    brand_id = None
    if ref.brand.brand_id is not None:
        brand_id = str(ref.brand.brand_id.root)

    # Single query: fetch up to 2 matches for ambiguity detection, scoped to the
    # agent's accessible accounts (#1417) so detection — and the count
    # disclosed below — never observe accounts outside this agent's access.
    principal_id = identity.principal_id
    matches = repo.list_by_natural_key(
        operator=ref.operator,
        brand_domain=brand_domain,
        brand_id=brand_id,
        sandbox=ref.sandbox,
        limit=2,
        principal_id=principal_id,
    )
    if len(matches) > 1:
        # Ambiguity is already established by the limit=2 fast path. Only now —
        # on the rare error path — pay for an exact COUNT so the buyer learns how
        # many accounts collide (the happy path never runs this query). Scoped to
        # the same accessible set as detection.
        total = repo.count_by_natural_key(
            operator=ref.operator,
            brand_domain=brand_domain,
            brand_id=brand_id,
            sandbox=ref.sandbox,
            principal_id=principal_id,
        )
        raise AdCPAccountAmbiguousError(
            f"Natural key matches {total} accounts for brand '{brand_domain}', operator '{ref.operator}'.",
            suggestion="Use explicit account_id instead of brand+operator to avoid ambiguity.",
        )

    account = matches[0] if matches else None
    if account is None:
        raise AdCPAccountNotFoundError(
            f"Account not found for brand '{brand_domain}', operator '{ref.operator}'.",
            suggestion="Use list_accounts to find valid accounts.",
        )

    _require_account_access(identity, account.account_id, repo)

    _check_account_status(account.account_id, account.status)

    return account.account_id
