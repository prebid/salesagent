"""The account a request names, resolved for the principal that named it. RESOLVER-PRIVATE.

``core/account-ref.json`` lets a request name an account by the seller-assigned
``account_id`` or by its natural key (brand + operator + sandbox). One credential may access
many accounts, so the account is per request and is resolved once, by the resolver
(``src/core/resolved_identity._resolve_identity``), which puts the result on the identity
it builds. ``ruff-boundary.toml`` bans importing this module anywhere else under ``src/``
and ``scripts/``: nothing below the resolver resolves a request's account, the same way
nothing below it resolves a token. The tools that MANAGE accounts as data
(``list_accounts``, ``sync_accounts``) use ``AccountRepository`` directly and never come
here.

Every lookup is tenant-scoped through the repository it is handed, access-checked against
the principal, and status-checked; a refusal is one of the typed account errors.
"""

from __future__ import annotations

import logging

from adcp.types import AccountReference, AccountReferenceById, AccountReferenceByNaturalKey

from src.core.database.models import Account
from src.core.database.repositories.account import AccountRepository
from src.core.errors.details import AccountAmbiguousDetails, AccountSetupDetails, EntityRefDetails
from src.core.exceptions import (
    AdCPAccountAmbiguousError,
    AdCPAccountNotFoundError,
    AdCPAccountPaymentRequiredError,
    AdCPAccountSetupRequiredError,
    AdCPAccountSuspendedError,
    AdCPAuthorizationError,
)
from src.core.helpers.brand_key import brand_key_parts
from src.core.schemas import Principal

logger = logging.getLogger(__name__)


def find_account(repo: AccountRepository, account_ref: AccountReference, principal: Principal) -> Account:
    """The account *account_ref* names that *principal* may use, or a typed refusal.

    Raises:
        AdCPAccountNotFoundError: no account matches, by id or by natural key.
        AdCPAuthorizationError: the principal has no access to the account.
        AdCPAccountAmbiguousError: the natural key matches more than one accessible account.
        AdCPAccountSetupRequiredError, AdCPAccountSuspendedError,
        AdCPAccountPaymentRequiredError: the account's status blocks operations.
    """
    inner = account_ref.root
    if isinstance(inner, AccountReferenceById):
        return _by_id(repo, inner.account_id, principal.principal_id)
    if isinstance(inner, AccountReferenceByNaturalKey):
        return _by_natural_key(repo, inner, principal.principal_id)
    # Unreachable: AccountReference is a closed two-variant union validated by Pydantic
    # upstream. A fresh variant reaching here is an internal contract violation, not a
    # buyer-facing not-found -- raise ValueError, not AdCPSalesAgentError.
    raise ValueError(f"Unsupported AccountReference variant: {type(inner)}")


def _by_id(repo: AccountRepository, account_id: str, principal_id: str) -> Account:
    """Resolve by explicit account_id: lookup, access check, status check."""
    account = repo.get_by_id(account_id)
    if account is None:
        raise AdCPAccountNotFoundError(details=EntityRefDetails(account_id=account_id))
    _require_access(repo, principal_id, account_id)
    _check_status(account_id, account.status)
    return account


def _by_natural_key(repo: AccountRepository, ref: AccountReferenceByNaturalKey, principal_id: str) -> Account:
    """Resolve by natural key: lookup, ambiguity check, access check, status check."""
    brand_domain, brand_id = brand_key_parts(ref.brand)

    # Single query: fetch up to 2 matches for ambiguity detection, scoped to the agent's
    # accessible accounts (#1417) so detection -- and the count disclosed below -- never
    # observe accounts outside this agent's access.
    matches = repo.list_by_natural_key(
        operator=ref.operator,
        brand_domain=brand_domain,
        brand_id=brand_id,
        sandbox=ref.sandbox,
        limit=2,
        principal_id=principal_id,
    )
    if len(matches) > 1:
        # Ambiguity is already established by the limit=2 fast path. Only now, on the rare
        # error path, pay for an exact COUNT so the buyer learns how many accounts collide
        # (the happy path never runs this query). Scoped to the same accessible set as
        # detection.
        total = repo.count_by_natural_key(
            operator=ref.operator,
            brand_domain=brand_domain,
            brand_id=brand_id,
            sandbox=ref.sandbox,
            principal_id=principal_id,
        )
        raise AdCPAccountAmbiguousError(
            details=AccountAmbiguousDetails(match_count=total, brand_domain=brand_domain, operator=ref.operator),
        )

    account = matches[0] if matches else None
    if account is None:
        # OPERATOR-FACING, never on the wire. A natural-key miss is scoped to the calling
        # agent's grants (#1417), so "no such account" and "an account you cannot see" are
        # the same envelope by design — correct for the buyer, and indistinguishable for
        # whoever has to fix a fixture. The typed details stay wire-safe; the tenant and
        # the resolved principal go to the log, which is the only place that can tell a
        # missing row from a missing grant.
        logger.warning(
            "account natural-key miss: tenant=%s principal=%s brand_domain=%s operator=%s sandbox=%s",
            repo.tenant_id,
            principal_id,
            brand_domain,
            ref.operator,
            ref.sandbox,
        )
        raise AdCPAccountNotFoundError(details=EntityRefDetails(brand_domain=brand_domain, operator=ref.operator))
    _require_access(repo, principal_id, account.account_id)
    _check_status(account.account_id, account.status)
    return account


def _require_access(repo: AccountRepository, principal_id: str, account_id: str) -> None:
    """Raise if the principal lacks access to the account."""
    if not repo.has_access(principal_id, account_id):
        raise AdCPAuthorizationError(details=EntityRefDetails(principal_id=principal_id, account_id=account_id))


def _check_status(account_id: str, status: str | None) -> None:
    """Raise if the account's status blocks operations."""
    if status == "pending_approval":
        # BR-UC-002 ext-s grades BOTH the top-level suggestion (POST-F3) and a details
        # payload carrying the setup instructions (POST-F2). `setup_steps` is
        # account-setup-required.json's own property name; the local `setup_instructions`
        # was a synonym for it, so the pin's spelling wins.
        # FIXME(#2099): account_id is not in the pinned shape. It may belong on the error's
        # field pointer or nowhere, rather than in details.
        raise AdCPAccountSetupRequiredError(
            details=AccountSetupDetails(
                setup_steps=["Complete billing configuration before use."],
                account_id=account_id,
            ),
        )
    if status == "suspended":
        raise AdCPAccountSuspendedError(details=EntityRefDetails(account_id=account_id))
    if status == "payment_required":
        raise AdCPAccountPaymentRequiredError(details=EntityRefDetails(account_id=account_id))
