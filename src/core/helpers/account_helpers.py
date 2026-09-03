"""Account resolution helpers.

Bridges AccountReference from request payloads to a validated account id + sandbox mode.
Used across the tool and transport layers wherever an AccountReference needs resolving.

"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, NamedTuple

from adcp.types import AccountReference, AccountReferenceById, AccountReferenceByNaturalKey

if TYPE_CHECKING:
    # Annotation only. uow.py imports sandbox_mode_for_buy from this module at
    # module scope; if this module imported AccountRepository at RUNTIME (not just
    # for typing), that import would need to load the repositories package, whose
    # __init__.py imports FROM repositories.uow — the very module currently
    # importing this one. Runtime import here would therefore cycle back into a
    # partially-initialized uow.py. TYPE_CHECKING-only avoids it entirely.
    from src.core.database.models import MediaBuy
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

logger = logging.getLogger(__name__)


class ResolvedAccount(NamedTuple):
    """A validated account plus the mode that governs its side effects.

    ``sandbox`` is carried alongside the id because it is decided here — at the only
    place the ``Account`` row is in hand — and is needed far downstream (adapter
    selection, response shaping). Returning the id alone silently dropped it, which is
    how sandbox-flagged requests reached the real ad server.

    Per AdCP 3.1.1 ``sandbox.mdx``, sandbox mode is determined SOLELY by the account
    reference. ``Account.sandbox`` is nullable; NULL means live — matching
    ``AccountRepository``'s own ``sandbox IS NULL OR sandbox = False`` filter.
    """

    account_id: str
    sandbox: bool


def resolve_account(
    account_ref: AccountReference,
    identity: ResolvedIdentity,
    repo: AccountRepository,
) -> ResolvedAccount:
    """Resolve an AccountReference to a validated account id + sandbox mode.

    Handles both variants of the AdCP AccountReference union:
    - AccountReferenceById: lookup by explicit account_id, verify agent access
    - AccountReferenceByNaturalKey: lookup by natural key (brand + operator + sandbox)

    Args:
        account_ref: AccountReference from the request payload.
        identity: Resolved identity with principal_id for access checks.
        repo: AccountRepository scoped to the correct tenant.

    Returns:
        ResolvedAccount: validated account_id plus its sandbox mode.

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


def sandbox_mode_for_buy(accounts: AccountRepository, media_buy: MediaBuy | None) -> bool:
    """Sandbox mode of the account owning ``media_buy`` — the one buy-keyed derivation.

    Two kinds of caller need this and only one of them can hold a UoW:
    ``BuyKeyedSandboxMixin.sandbox_mode`` delegates here for UoW holders, and paths that
    hold a raw session (the admin detail route) call it directly. Before this existed
    they open-coded ``_account_is_sandbox(repo, buy.account_id)`` instead, which is the
    same decision expressed twice — and the shape that let five call sites drift into
    three different forms of the same question.

    The None-buy and unresolvable-account policies are ``_account_is_sandbox``'s; see its
    docstring for why those two cases are deliberately not symmetric.
    """
    return _account_is_sandbox(accounts, media_buy.account_id if media_buy is not None else None)


def sandbox_wire_marker(is_sandbox: bool | None) -> bool | None:
    """AdCP 3.1.1 sandbox.mdx: sellers SHOULD include ``sandbox: true`` in success
    responses when processing a sandbox account request. ``None``, not ``False``, when
    not sandbox — the spec asks for the field's presence to signal sandbox, not for an
    explicit live/non-sandbox assertion. Shared so every success-response builder that
    sets this marker makes the same True-vs-None call rather than each re-deciding it.
    """
    return True if is_sandbox else None


def _account_is_sandbox(accounts: AccountRepository, account_id: str | None) -> bool:
    """Sandbox mode of an account, for paths that have no ResolvedIdentity.

    Deferred execution (the approval executor, creative push, admin routes) runs after
    the request identity is gone but must apply the same sandbox semantics — an approved
    sandbox buy dispatching to the real ad server is the same defect as an inline one.

    Takes the caller's ``AccountRepository`` rather than opening its own session: these
    call sites already hold one, and a second connection inside the adapter-dispatch path
    would be both a wasted round-trip and a real-DB dependency in unit-tested code.

    Two "no account" cases, deliberately treated differently:

    - ``account_id is None`` → False (live). Legacy buys predate account references; live
      is their only meaningful mode, and the operator's approval gate stays in control.
    - a non-null ``account_id`` that does not resolve → **raises**. Returning False there
      would mean "use the real ad server" for a stale, corrupt, or wrongly-scoped
      reference — fail-OPEN with respect to external side effects, which is the exact
      failure this module exists to prevent. When the mode cannot be established, the
      only safe answer is to refuse dispatch.
    """
    if not account_id:
        return False

    account = accounts.get_by_id(account_id)
    if account is None:
        # DELIBERATE DEVIATION, recorded rather than silent.
        #
        # ACCOUNT_NOT_FOUND is a buyer-directed code: AdCP 3.1.1
        # `dist/docs/3.1.1/building/by-layer/L2/accounts-and-agents.mdx:464` pairs it
        # with "account_id doesn't exist or agent lacks access" and the remedy "Check
        # account reference, re-run sync_accounts". Neither half applies HERE. This path
        # is reached from buy-keyed operations (update, performance, delivery, admin)
        # where the buyer supplied NO account reference at all — the id came off the
        # seller's own `media_buys.account_id`, so a dangling value is seller-side data
        # integrity, not a buyer mistake, and no buyer action can repair it.
        # CONFIGURATION_ERROR is the declared class for that ("the buyer cannot fix it,
        # retrying will not help, reporting to the seller's operator is the only
        # remediation").
        #
        # ACCOUNT_NOT_FOUND is kept anyway: recovery is `terminal` under both candidates,
        # so no buyer's autonomous behaviour changes, and switching would move the wire
        # status from 404 to 5xx for a condition whose reachability is very low — the
        # composite FK cannot orphan a row, and `sync_accounts --delete_missing` only
        # closes accounts. Carrying the spec's canonical suggestion verbatim is the
        # consistent choice once the code is the spec's: a buyer parsing
        # ACCOUNT_NOT_FOUND gets the remedy the spec documents for it, rather than a
        # second, site-specific phrasing of the same code.
        #
        # The MESSAGE stays in the same terse register as this file's other
        # ACCOUNT_NOT_FOUND raises (`_resolve_by_id`, `_resolve_by_natural_key`) — a
        # buyer parsing the wire sees one family, not three. The seller-side nature
        # this comment documents is for the next reader of this file, not the wire;
        # the log line below (ERROR, with a stack) is the operator-facing signal.
        #
        # Logged at ERROR with a stack rather than left to the boundary. As a typed
        # AdCPError this reaches `record_boundary_error`, which logs typed errors at
        # WARNING with no traceback — correct for buyer mistakes, wrong for a
        # data-integrity fault that no buyer caused and only an operator can fix.
        logger.error(
            "Dangling account reference: media buy row points at account_id=%r in tenant %r, "
            "which does not resolve. Refusing to dispatch because sandbox mode cannot be "
            "established; this is seller-side data integrity, not a buyer error.",
            account_id,
            accounts.tenant_id,
            stack_info=True,
        )
        raise AdCPAccountNotFoundError(
            f"Account '{account_id}' not found.",
            suggestion="Check account reference, re-run sync_accounts.",
        )
    return bool(account.sandbox)


def partition_by_sandbox_mode[T](
    accounts: AccountRepository,
    items: Iterable[T],
    account_id_of: Callable[[T], str | None],
) -> tuple[list[T], list[T]]:
    """Split items into ``(sandbox_items, live_items)`` by their account's mode.

    Multi-buy operations (get_media_buys snapshots, get_media_buy_delivery) can span
    both modes in a single response, which no identity-level boolean can represent.
    Callers run each partition against its own adapter and merge the results by
    ``media_buy_id``; an empty partition means its adapter is never constructed.

    Resolution happens for every non-null account BEFORE either adapter call, so an
    unresolved account raises rather than silently landing in the live partition.
    Modes are cached per account id — a snapshot of 200 buys on one account costs one
    lookup, not 200.
    """
    sandbox_items: list[T] = []
    live_items: list[T] = []
    mode_cache: dict[str | None, bool] = {}

    for item in items:
        account_id = account_id_of(item)
        if account_id not in mode_cache:
            mode_cache[account_id] = _account_is_sandbox(accounts, account_id)
        (sandbox_items if mode_cache[account_id] else live_items).append(item)

    return sandbox_items, live_items


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
) -> ResolvedAccount:
    """Resolve by explicit account_id — lookup + access check + status check."""
    account = repo.get_by_id(account_id)
    if account is None:
        raise AdCPAccountNotFoundError(
            f"Account '{account_id}' not found.",
            suggestion="Use list_accounts to find valid account IDs.",
        )

    _require_account_access(identity, account_id, repo)

    _check_account_status(account_id, account.status)

    return ResolvedAccount(account.account_id, bool(account.sandbox))


def _resolve_by_natural_key(
    ref: AccountReferenceByNaturalKey,
    identity: ResolvedIdentity,
    repo: AccountRepository,
) -> ResolvedAccount:
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

    return ResolvedAccount(account.account_id, bool(account.sandbox))
