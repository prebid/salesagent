"""Account resolution helpers.

Bridges AccountReference from request payloads to validated account_id strings.
Used by _create_media_buy_impl and _sync_creatives_impl.

beads: salesagent-8n4
"""

from __future__ import annotations

from adcp.types.generated_poc.core.account_ref import (
    AccountReference,
    AccountReference1,
    AccountReference2,
)

from src.core.database.repositories.account import AccountRepository
from src.core.exceptions import (
    AdCPAccountAmbiguousError,
    AdCPAccountNotFoundError,
    AdCPAccountPaymentRequiredError,
    AdCPAccountSetupRequiredError,
    AdCPAccountSuspendedError,
    AdCPAuthorizationError,
    AdCPNotFoundError,
)
from src.core.resolved_identity import ResolvedIdentity


def resolve_account(
    account_ref: AccountReference,
    identity: ResolvedIdentity,
    repo: AccountRepository,
) -> str:
    """Resolve an AccountReference to a validated account_id.

    Handles both variants of the AdCP AccountReference union:
    - AccountReference1: lookup by explicit account_id, verify agent access
    - AccountReference2: lookup by natural key (brand + operator + sandbox)

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

    if isinstance(inner, AccountReference1):
        return _resolve_by_id(inner.account_id, identity, repo)

    if isinstance(inner, AccountReference2):
        return _resolve_by_natural_key(inner, identity, repo)

    raise AdCPNotFoundError(f"Unsupported AccountReference variant: {type(inner)}")


def _check_account_status(account_id: str, status: str | None) -> None:
    """Raise if account status blocks operations."""
    if status == "pending_approval":
        raise AdCPAccountSetupRequiredError(
            f"Account '{account_id}' requires setup.",
            details={"suggestion": "Complete billing configuration before use."},
        )
    if status == "suspended":
        raise AdCPAccountSuspendedError(
            f"Account '{account_id}' is suspended.",
            details={"suggestion": "Contact your account manager."},
        )
    if status == "payment_required":
        raise AdCPAccountPaymentRequiredError(
            f"Account '{account_id}' has outstanding payment.",
            details={"suggestion": "Resolve payment before use."},
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
            details={"suggestion": "Use list_accounts to find valid account IDs."},
        )

    principal_id = identity.principal_id
    if principal_id and not repo.has_access(principal_id, account_id):
        raise AdCPAuthorizationError(
            f"Agent '{principal_id}' does not have access to account '{account_id}'.",
            details={"suggestion": "Use list_accounts to find accounts accessible to this agent."},
        )

    _check_account_status(account_id, account.status)

    return account.account_id


def _resolve_by_natural_key(
    ref: AccountReference2,
    identity: ResolvedIdentity,
    repo: AccountRepository,
) -> str:
    """Resolve by natural key (brand + operator + sandbox) — lookup + ambiguity check + access check + status check."""
    brand_domain = ref.brand.domain
    brand_id = None
    if ref.brand.brand_id is not None:
        brand_id = str(ref.brand.brand_id.root) if hasattr(ref.brand.brand_id, "root") else str(ref.brand.brand_id)

    # Single query: fetch up to 2 matches for ambiguity detection
    matches = repo.list_by_natural_key(
        operator=ref.operator,
        brand_domain=brand_domain,
        brand_id=brand_id,
        sandbox=ref.sandbox,
        limit=2,
    )
    if len(matches) > 1:
        raise AdCPAccountAmbiguousError(
            f"Natural key matches multiple accounts for brand '{brand_domain}', operator '{ref.operator}'.",
            details={"suggestion": "Use explicit account_id instead of brand+operator to avoid ambiguity."},
        )

    account = matches[0] if matches else None
    if account is None:
        raise AdCPAccountNotFoundError(
            f"Account not found for brand '{brand_domain}', operator '{ref.operator}'.",
            details={"suggestion": "Use list_accounts to find valid accounts."},
        )

    # Access check — parity with _resolve_by_id (lines 100-102)
    principal_id = identity.principal_id
    if principal_id and not repo.has_access(principal_id, account.account_id):
        raise AdCPAuthorizationError(
            f"Agent '{principal_id}' does not have access to account '{account.account_id}'.",
            details={"suggestion": "Use list_accounts to find accounts accessible to this agent."},
        )

    _check_account_status(account.account_id, account.status)

    return account.account_id
