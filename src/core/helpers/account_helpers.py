"""Account resolution helpers.

Bridges AccountReference from request payloads to validated account_id strings.
Used by _create_media_buy_impl and _sync_creatives_impl.

beads: salesagent-8n4
"""

from __future__ import annotations

from adcp.types import AccountReference, AccountReferenceById, AccountReferenceByNaturalKey

from src.core.database.repositories.account import AccountRepository
from src.core.exceptions import (
    AdCPAccountAmbiguousError,
    AdCPAccountNotFoundError,
    AdCPAccountPaymentRequiredError,
    AdCPAccountSetupRequiredError,
    AdCPAccountSuspendedError,
    AdCPAuthorizationError,
)
from src.core.resolved_identity import ResolvedIdentity


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
    """
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
    """Raise if the agent's principal lacks access to the account."""
    principal_id = identity.principal_id
    if principal_id and not repo.has_access(principal_id, account_id):
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
    # agent's accessible accounts (salesagent-ym1c) so detection — and the count
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
