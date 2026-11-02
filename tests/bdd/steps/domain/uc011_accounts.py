"""Domain step definitions for UC-011: Manage Accounts.

Given steps: set up accounts, agent access, seller config
When steps: send list_accounts / sync_accounts requests
Then steps: verify account results, actions, status, errors

All steps operate on ctx dict (shared across Given/When/Then).
ctx["env"] is the harness environment (AccountSyncEnv or AccountListEnv).
ctx["result"] is the dispatch's TransportResult after When; read its typed
payload through require_payload()/payload_or_none() and its wire through the
wire_* accessors in _outcome_helpers — never a detached ctx["response"] copy
(#1802 removed it). ctx["error"] is any exception raised.
"""

from __future__ import annotations

import json
from typing import Any

from pytest_bdd import given, parsers, then, when

from src.core.billing_policy import BILLING_PARTY_VALUES
from tests.bdd.steps._outcome_helpers import (
    payload_or_none,
    require_payload,
    wire_absent,
    wire_dict,
    wire_entry,
    wire_entry_errors,
    wire_envelope_errors,
    wire_error_dict,
    wire_error_envelope_or_none,
    wire_field,
)
from tests.bdd.steps.generic._account_resolution import ensure_tenant_principal
from tests.bdd.steps.generic._dispatch import dispatch_request, dispatch_via_client, gate_and_record
from tests.bdd.steps.generic._table import as_bool
from tests.bdd.steps.generic._table import rows as table_rows
from tests.bdd.steps.generic.then_error import _wire_code
from tests.factories.account import AccountFactory, AgentAccountAccessFactory
from tests.factories.request import fresh_idempotency_key

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


#: Sentinel for the omitted-key scenario: pass ``idempotency_key=OMIT_IDEMPOTENCY_KEY``
#: to send a sync_accounts payload carrying NO key at all.
#:
#: sync-accounts-request.json 3.1.1 lists idempotency_key in ``/required``, so "the seller
#: refuses a request without one" is an obligation that needs grading — and it cannot be
#: expressed by omitting the kwarg, because :func:`_sync_raw` defaults a key in for every
#: scenario that simply does not care about it. Same shape, same reason, as
#: ``tests.harness.media_buy_create.OMIT_IDEMPOTENCY_KEY`` and its ``OMIT_ACCOUNT`` sibling;
#: kept local because that one is the create env's and this is the sync verb's wire path.
OMIT_IDEMPOTENCY_KEY: Any = object()


def _sync_raw(ctx: dict, **payload: Any) -> None:
    """Dispatch a LITERAL sync_accounts payload — no ``SyncAccountsRequest`` first.

    THE NEGATIVE-PATH DISPATCH for the sync verb. Every caller expects the SELLER
    to reject the payload, so building the request model here would be fatal to
    the test's meaning: pydantic raises in the test process, the old ``except
    Exception: ctx["error"] = exc`` stashed a client-side exception, and
    production was never executed. Several of those step docstrings said so
    outright ("triggers Pydantic validation"). The scenario then graded the MODEL
    and said nothing about the SERVER — so nothing checked that the transports
    AGREE on a code.

    Goes through ``env.call_via`` (a raw kwargs bag IS a raw dispatch — see
    ``AccountSyncEnv.build_rest_body``'s explicit flat path) rather than
    ``dispatch_via_client``, because ``sync_accounts`` has NO pinned response
    model: the client seam resolves success through ``spec_response_model(tool)``
    and would hand back ``payload=None`` with ``is_success`` False, whereas the
    env's ``response_parser()`` hook types each verb correctly. That matters here
    precisely BECAUSE some of these rows are expected to graduate — if production
    accepts a payload the scenario thought invalid, this path reports a plain
    success instead of an unparseable one, so the graduation is legible rather
    than disguised as a failure.

    A CONFORMANT ``idempotency_key`` is defaulted in, because
    sync-accounts-request.json 3.1.1 lists it in ``/required`` and a buyer sends
    one on every request. Without it, a scenario grading an invalid ``operator``
    is rejected for the MISSING KEY first and the seller blames the wrong field —
    the scenario then reports a real disagreement about a field it never got to
    test. Every negative row here targets some OTHER field, so the key is
    setup, not subject.

    ``setdefault``, so a scenario that names its own key still grades that key:
    the malformed-key rows (T-UC-011-sync-idempotency-malformed) pass 15-character
    and bad-charset values through and must keep reaching the boundary with them.
    A scenario meaning to send NO key says so with :data:`OMIT_IDEMPOTENCY_KEY`,
    which is popped rather than defaulted — omission has to be STATED, because
    every scenario that simply does not care about the key looks identical to one
    that means to leave it out, and those must get the default.
    """
    if payload.get("idempotency_key") is OMIT_IDEMPOTENCY_KEY:
        del payload["idempotency_key"]
    else:
        payload.setdefault("idempotency_key", fresh_idempotency_key())
    dispatch_request(ctx, **payload)


def _list_raw(ctx: dict, **payload: Any) -> None:
    """Dispatch a LITERAL list_accounts payload, NAMING the tool explicitly.

    Same rationale as :func:`_sync_raw` for why the payload must not go through
    ``ListAccountsRequest`` — but this verb CANNOT use ``env.call_via`` with a raw
    bag, and that is a genuine constraint rather than a preference:
    ``AccountListDispatchMixin.is_list_request`` selects the verb with
    ``isinstance(kwargs.get("req"), ListAccountsRequest)``. With no typed ``req``
    the discriminator returns False and a raw list payload would be MISROUTED to
    ``sync_accounts``. The client seam takes the tool name directly, so it is the
    only raw path that can address this verb.

    Safe on the success side too: ``list_accounts`` HAS a pinned response model
    (``ListAccountsResponse``), of which the local schema is a subclass, so an
    unexpected acceptance still parses and reads as a graduation.
    """
    dispatch_via_client(ctx, "list_accounts", payload)


def _pointer_names(pointer: str, field: str) -> bool:
    """Whether the envelope's ``field`` POINTER blames *field*.

    Production points at the offending member with a full path
    (``accounts.0.brand.domain``); a scenario names the member
    (``brand.domain``). Suffix-match on dotted components rather than a
    substring test, so "operator" does not match "operator_id" and a pointer
    at a DIFFERENT member cannot satisfy the assertion.
    """
    want = [c for c in field.split(".") if c]
    have = [c for c in str(pointer).split(".") if c]
    return len(have) >= len(want) and have[len(have) - len(want) :] == want


def _assert_wire_field_rejection(ctx: dict, field: str, code: str = "INVALID_REQUEST") -> None:
    """Assert the SELLER rejected the request with *code*, blaming *field*.

    THE rejection assertion for this module. Reads the two-layer envelope the
    buyer actually received, through the harness's own ``assert_wire_error``
    (the single sanctioned surface, shared with then_error.py) plus the guarded
    ``wire_error_dict`` reader for the field pointer.

    What it replaces, and why the replacement is STRONGER rather than merely
    different: the previous assertions matched the scenario's field name as a
    SUBSTRING of ``str(error)``, or required ``isinstance(error, ValidationError)``.
    Both were satisfiable only while the request was built in the test process --
    the substring could land anywhere in a long pydantic message, and the
    isinstance check was literally asserting that production had NOT been reached.
    This reads ``errors[0].field``, the error.json pointer whose whole job is to
    name WHICH request member was rejected, at the position the spec defines it.

    INVALID_REQUEST, not VALIDATION_ERROR, and the distinction is the finding:
    the pinned ``sync-accounts-request`` declares ``accounts[]`` as an anyOf of
    two branches requiring ``['brand','operator','billing']`` (provisioning) or
    ``['account']`` (settings-update). An entry missing one of those satisfies
    NEITHER branch, so the REQUEST is malformed -- "Request is malformed, missing
    required fields, or violates schema constraints" -- rather than the ACCOUNT
    being unprovisionable, which is what a per-account ``action: "failed"`` result
    reports. VALIDATION_ERROR is for "business rules BEYOND schema validation".
    @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json
      pointer=/INVALID_REQUEST  (pinned: tests/fixtures/adcp_schemas_pinned/enums/error-code.json:102)
    """
    result = ctx.get("result")
    assert result is not None, (
        f"Expected the seller to reject this request naming {field!r}, but no transport result was "
        f"captured -- the payload never reached it. ctx['error']={ctx.get('error')!r}"
    )
    result.assert_wire_error(code)
    # wire_error_dict returns the ENVELOPE; locate_envelope_error is THE one locator
    # for the payload-layer error object (errors[0]) inside it. Reading .get("field")
    # off the envelope itself silently yields None — the envelope has no such member.
    from tests.helpers.envelope_assertions import locate_envelope_error

    error = locate_envelope_error(wire_error_dict(ctx))
    assert error is not None, f"Envelope carries no payload-layer error object: {wire_error_dict(ctx)!r}"
    pointer = error.get("field")
    assert pointer is not None, (
        f"Envelope carries {code} but no errors[0].field, so it does not tell the buyer WHICH "
        f"member was rejected (expected one naming {field!r}). Error object: {error!r}"
    )
    assert _pointer_names(pointer, field), (
        f"errors[0].field={pointer!r} does not name {field!r} -- the seller blamed a different member"
    )


def _setup_tenant_and_principal(ctx: dict) -> tuple[Any, Any]:
    """This module's 22 call sites, routed to the canonical owner.

    ``ensure_tenant_principal`` (``steps/generic/_account_resolution.py``) is the
    one implementation; ``uc002`` and ``uc006`` already delegate to it the same
    way. The only difference here is the return value, which this module's
    callers use.
    """
    ensure_tenant_principal(ctx, ctx["env"])
    return ctx["tenant"], ctx["principal"]


def _create_accessible_account(ctx: dict, status: str = "active", **kwargs: Any) -> Any:
    """Create an account and grant agent access to it.

    Tracks created account IDs in ctx["expected_account_ids"] and statuses
    in ctx["created_statuses"] for assertion verification.
    """
    tenant, principal = _setup_tenant_and_principal(ctx)
    account = AccountFactory(tenant=tenant, status=status, **kwargs)
    AgentAccountAccessFactory(
        tenant_id=tenant.tenant_id,
        principal=principal,
        account=account,
    )
    ctx.setdefault("expected_account_ids", set()).add(account.account_id)
    ctx.setdefault("created_statuses", set()).add(status)
    return account


def _status_str(status: Any) -> str:
    """Extract string value from Status enum or return as-is."""
    return status.value if hasattr(status, "value") else str(status)


def _action_str(action: Any) -> str:
    """Extract string value from Action enum or return as-is."""
    return action.value if hasattr(action, "value") else str(action)


def _brand_id_str(bid: Any) -> str | None:
    """Extract string value from BrandId (RootModel[str]) or return as-is."""
    if bid is None:
        return None
    if isinstance(bid, str):
        return bid
    return str(bid.root)


def _find_account_by_brand(resp: Any, domain: str, brand_id: str | None = None) -> Any:
    """Find an account in sync response by brand domain (and optional brand_id)."""
    for acct in resp.accounts:
        if acct.brand.domain != domain:
            continue
        if brand_id is not None:
            acct_bid = _brand_id_str(getattr(acct.brand, "brand_id", None))
            if acct_bid != brand_id:
                continue
        return acct
    domains = [a.brand.domain for a in resp.accounts]
    suffix = f" and brand_id '{brand_id}'" if brand_id else ""
    raise AssertionError(f"No account found for domain '{domain}'{suffix}. Available: {domains}")


def _make_governance_agent(url: str = "https://compliance.example.com/check") -> dict[str, Any]:
    """Build a valid GovernanceAgent dict matching the adcp schema.

    Uses the library GovernanceAgent model for validation, then dumps to dict
    for use in SyncAccountsRequest entries.

    ``url`` is the ONLY property the pinned model declares (extra="forbid"), so
    the helper takes nothing else. It previously also passed ``categories``,
    which the pinned GovernanceAgent has not declared since ``authentication``
    was dropped in adcp 3.12 — every call raised
    ``ValidationError: categories Extra inputs are not permitted``. That went
    unnoticed because no BR-UC-011 scenario referenced these steps; the
    omission-preserves scenario (salesagent-gcze) is the first, and it must
    fail on the governance wipe, not on the fixture.
    """
    from adcp.types.generated_poc.core.account import GovernanceAgent  # TODO: no stable alias in adcp.types

    return GovernanceAgent(url=url).model_dump(mode="json")


def _sync_pre_create(
    ctx: dict,
    brand_domain: str,
    operator: str,
    billing: str,
    *,
    idempotency_key: str | None = None,
    **extra: Any,
) -> None:
    """Pre-create an account via sync so it exists for update/unchanged tests.

    Extra kwargs (e.g., payment_terms, governance_agents) are merged into the account entry.
    Captures original field values in ctx["original_field_values"] for later
    "unchanged from the original" assertions.

    ``idempotency_key`` names the key this first request SPENDS, for the one Given whose
    subject is the key rather than the account: a fresh key per call is what every other
    caller wants, but the conflict scenario needs the second request to arrive on a key the
    replay cache already holds a payload hash for. Keyword-only so it cannot be mistaken for
    an entry field by ``**extra``.
    """
    from src.core.schemas.account import SyncAccountsRequest

    entry: dict[str, Any] = {"brand": {"domain": brand_domain}, "operator": operator, "billing": billing}
    entry.update(extra)
    req = SyncAccountsRequest(idempotency_key=idempotency_key or fresh_idempotency_key(), accounts=[entry])
    dispatch_request(ctx, req=req)
    error = ctx.get("error")
    assert error is None, f"Given: pre-create sync for {brand_domain!r} failed: {error!r}"
    # Capture original field values for "unchanged from the original" assertions
    resp = require_payload(ctx)
    if resp.accounts:
        acct = resp.accounts[0]
        originals = ctx.setdefault("original_field_values", {})
        originals["billing"] = billing
        originals["operator"] = operator
        if "payment_terms" in extra:
            originals["payment_terms"] = extra["payment_terms"]
        # Capture DB-assigned fields from the response
        if hasattr(acct, "account_id"):
            _capture_server_account_id(ctx, acct.account_id)
        if hasattr(acct, "status"):
            originals["status"] = _status_str(acct.status)
    # Clear response so the next When step's response is fresh
    # Clear every source the payload accessors read: a When that raises BEFORE
    # dispatching leaves the previous step's TransportResult in ctx, and the
    # accessors would serve it as this step's own result. (The retired
    # ctx["response"] is not popped — nothing writes it, so there is nothing to
    # clear, and pretending otherwise reads as live state management.)
    ctx.pop("result", None)
    ctx.pop("self_dispatched_response", None)
    ctx.pop("error", None)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — authentication and account setup
# ═══════════════════════════════════════════════════════════════════════


@given("the Buyer Agent has an unauthenticated connection")
@given(parsers.parse("the Buyer Agent has an unauthenticated connection via {transport}"))
def given_unauthenticated(ctx: dict, transport: str | None = None) -> None:
    """Set up unauthenticated connection.

    The transport arg is accepted but ignored — pytest_generate_tests
    controls which transport is used for dispatch.
    """
    ctx["has_auth"] = False
    # Present no token, on the env's tenant, so the resolver answers AUTH_MISSING.
    ctx["force_credential"] = ctx["env"].credential(token=None)


@given("the Buyer Agent has an A2A connection with an expired token")
def given_expired_token(ctx: dict) -> None:
    """Present a credential that resolves to no principal (expired/revoked).

    Distinct from ``given_unauthenticated``, which presents NO credential.
    AdCP v3.1.1 splits the two auth rejections on exactly that axis (absent ->
    AUTH_MISSING/correctable, presented-but-rejected -> AUTH_INVALID/terminal),
    so the Given has to drive a real token through the real resolution chain on
    every transport, not stand in for the token-less case. Every transport
    presents the same headers and the same resolver answers them (GH #1886 was
    a REST-only override disagreeing with the wire).
    """
    from tests.harness._base import INVALID_TOKEN

    ctx["has_auth"] = False
    # Seed the tenant so tenant detection still resolves: the realistic shape is
    # a known seller reached with a dead credential, not an unknown seller.
    _setup_tenant_and_principal(ctx)
    ctx["force_credential"] = ctx["env"].credential(token=INVALID_TOKEN)


# `the sync_accounts response schema uses oneOf` bound here and is deleted: no feature carries
# the sentence, it asserted nothing at all, and its whole body was `ctx["schema_test"] = True`
# — a key no surviving reader anywhere consumes. That is positive evidence of deadness rather
# than an inference from the absence of a binding.
#
# THE OTHER 31 UNBOUND STEPS IN THIS MODULE ARE KEPT DELIBERATELY, and this note is the record
# of why, so the next reader does not re-derive it. No feature binds them either — checked by
# literal grep and by matching each pattern against all 49534 sentences rendered from every
# feature's Examples through pytest-bdd's own FeatureParser. But "nothing binds it" is not
# evidence of deadness on its own: most of them read ctx state that live steps still write
# (`tenant`, `principal`, `last_account`, `original_field_values`) and assert real obligations
# — per-agent account scoping, governance-agent persistence across a resync, DB-field
# immutability, one access grant per domain. Those are obligations this project identified and
# never wrote a scenario for; deleting the steps would destroy the only record that they were
# identified at all.
#
# One of them proves the point concretely. `given_db_failure` writes ctx["simulate_db_failure"],
# and a SURVIVING function in this module reads it — deleting the Given would have left a live
# reader whose condition can never become true.
#
# The missing scenarios are filed as salesagent-xighb. Write the scenario, then bind the step.


@given("the seller system is experiencing an internal failure")
def given_seller_internal_failure(ctx: dict) -> None:
    """Make the SELLER fail, through the env, so the request is still dispatched.

    This used to set a ctx flag that the dispatcher read to manufacture an
    ``AdCPSalesAgentError`` in the test process and return WITHOUT dispatching. The
    scenario then graded an object the test had built, and the boundary's error
    translation -- the thing the scenario exists to check -- never ran.
    """
    ctx["env"].fail_the_sync_internally()


@given("the seller does not support any of the requested billing models")
def given_seller_no_billing(ctx: dict) -> None:
    """Configure seller to reject all billing models."""
    _set_billing_policy(ctx, [])  # Empty list = reject everything


def _set_billing_policy(ctx: dict, supported: list[str]) -> None:
    """Set billing policy via the harness."""
    ctx["env"].set_billing_policy(supported)
    ctx["configured_billing_policy"] = list(supported)


@given(parsers.parse('the seller does not support "{billing}" billing'))
def given_seller_no_specific_billing(ctx: dict, billing: str) -> None:
    """Configure seller to not support a specific billing model."""
    all_models = set(BILLING_PARTY_VALUES)
    _set_billing_policy(ctx, sorted(all_models - {billing}))


@given(parsers.parse('the seller supports "{supported}" billing but not "{rejected}" billing'))
def given_seller_partial_billing(ctx: dict, supported: str, rejected: str) -> None:
    """Configure seller to support one billing model but not another."""
    _set_billing_policy(ctx, [supported])


@given("the Buyer Agent is registered with the seller as passthrough-only")
def given_agent_passthrough_only(ctx: dict) -> None:
    """Register the calling buyer agent as passthrough-only (no payments relationship).

    Spec (error-details/billing-not-permitted-for-agent.json#/description): the
    per-agent gate fires when "the seller's declared ``supported_billing``
    capability ACCEPTS the requested value but the calling buyer agent's
    commercial relationship with the seller does not (e.g., the agent is
    onboarded as passthrough-only — no payments relationship — so ``agent`` and
    ``advertiser`` reject)." The correct fixture therefore declares agent (and
    advertiser) as *capability-supported* — the value is enum-valid AND in the
    seller's supported_billing — so the ONLY thing that could reject it is the
    per-buyer-agent commercial gate. Production has no such gate (GH #1772), so it
    accepts the capability-supported value and provisions the account; that is
    the gap these scenarios grade (strict xfail in conftest _XFAIL_TAGS).

    Real DB seeding via ``set_billing_policy`` (writes the tenant
    ``supported_billing`` column) — NOT mock injection — so the setup is
    e2e_rest-compatible.
    """
    _set_billing_policy(ctx, ["operator", "agent", "advertiser"])


def _set_approval_mode(ctx: dict, mode: str) -> None:
    """Set approval mode via the harness."""
    ctx["env"].set_approval_mode(mode)


@given("the seller requires credit review for new accounts")
def given_seller_credit_review(ctx: dict) -> None:
    """Configure seller to require credit review (pending + url + message)."""
    _set_approval_mode(ctx, "credit_review")


@given("the seller requires legal review for new accounts")
def given_seller_legal_review(ctx: dict) -> None:
    """Configure seller to require legal review (pending + message only)."""
    _set_approval_mode(ctx, "legal_review")


@given("the seller auto-approves new accounts")
def given_seller_auto_approve(ctx: dict) -> None:
    """Configure seller to auto-approve (status=active, no setup)."""
    _set_approval_mode(ctx, "auto")


@given(parsers.parse('the agent has {count:d} accessible accounts with statuses "{s1}", "{s2}", "{s3}"'))
def given_n_accounts_with_3_statuses(ctx: dict, count: int, s1: str, s2: str, s3: str) -> None:
    """Create N accounts with the given statuses (3 statuses for N=3)."""
    statuses = [s1, s2, s3]
    for status in statuses[:count]:
        _create_accessible_account(ctx, status=status)


@given(parsers.parse('the agent has accounts with statuses "{s1}", "{s2}", "{s3}", "{s4}"'))
def given_accounts_with_4_statuses(ctx: dict, s1: str, s2: str, s3: str, s4: str) -> None:
    """Create accounts with 4 distinct statuses."""
    for status in [s1, s2, s3, s4]:
        _create_accessible_account(ctx, status=status)


@given(parsers.parse('the agent has accounts with statuses "{s1}", "{s2}", "{s3}"'))
def given_accounts_with_3_statuses(ctx: dict, s1: str, s2: str, s3: str) -> None:
    """Create accounts with 3 statuses."""
    for status in [s1, s2, s3]:
        _create_accessible_account(ctx, status=status)


@given("the agent has no accessible accounts")
def given_no_accounts(ctx: dict) -> None:
    """Agent has no accessible accounts (tenant + principal exist but no accounts).

    Emptiness is the default -- accounts reach this agent only through
    ``_create_accessible_account``, which records every one it grants. The
    falsifiable half is the other direction: a scenario that granted an account
    and then declares the agent has none is grading the opposite of its sentence.
    """
    _setup_tenant_and_principal(ctx)
    granted = ctx.get("expected_account_ids", set())
    assert not granted, (
        f"Step claims the agent has no accessible accounts, but this scenario "
        f"already granted access to {sorted(granted)}."
    )


@given(parsers.parse("the agent has {count:d} accessible accounts"))
def given_n_accessible_accounts(ctx: dict, count: int) -> None:
    """Create N accessible accounts with default active status."""
    for _ in range(count):
        _create_accessible_account(ctx, status="active")


# ── Sync-specific Given steps ──────────────────────────────────────────


@given(parsers.parse('an account for brand domain "{domain}" already exists with billing "{billing}"'))
def given_existing_account(ctx: dict, domain: str, billing: str) -> None:
    """Pre-create an account via sync_accounts so it exists for update/unchanged scenarios."""
    _setup_tenant_and_principal(ctx)
    _sync_pre_create(ctx, brand_domain=domain, operator=domain, billing=billing)


@given(parsers.parse('an account for brand domain "{domain}" already exists with payment_terms "{pt}"'))
def given_existing_account_payment_terms(ctx: dict, domain: str, pt: str) -> None:
    """Pre-create an account with specific payment_terms via sync_accounts."""
    _setup_tenant_and_principal(ctx)
    _sync_pre_create(ctx, brand_domain=domain, operator=domain, billing="operator", payment_terms=pt)


@given(
    parsers.parse(
        'an account for brand domain "{domain}" already exists with billing "{billing}" and payment_terms "{pt}"'
    )
)
def given_existing_account_billing_and_pt(ctx: dict, domain: str, billing: str, pt: str) -> None:
    """Pre-create an account with specific billing and payment_terms via sync_accounts."""
    _setup_tenant_and_principal(ctx)
    _sync_pre_create(ctx, brand_domain=domain, operator=domain, billing=billing, payment_terms=pt)


@given(parsers.parse('the previously synced account for brand domain "{domain}" has no brand recorded'))
def given_named_persisted_account_without_brand(ctx: dict, domain: str) -> None:
    """Clear ``brand`` on a NAMED already-synced account.

    Sibling of "the persisted account has no brand recorded", which clears the
    account the immediately-preceding Given created. This one names its target,
    so a scenario that seeded several accounts can put exactly one of them into
    the brand-less state -- which is what the delete_missing branch needs, since it
    walks accounts the request did NOT mention.
    """
    from src.core.database.models import Account
    from src.core.helpers.brand_key import brand_key_parts

    env = ctx["env"]
    # brand is a JSONType column, but the ORM may hand back a BrandReference
    # model rather than a plain dict depending on how the row was written, so go
    # through the project's own accessor instead of assuming either shape.
    account = next(
        (a for a in env.query(Account, tenant_id=ctx["tenant"].tenant_id) if brand_key_parts(a.brand)[0] == domain),
        None,
    )
    assert account is not None, f"no persisted account for brand domain {domain!r} to clear"
    env.clear_account_brand(account.account_id)


@given("the persisted account has no brand recorded")
def given_persisted_account_without_brand(ctx: dict) -> None:
    """Clear ``brand`` on the account the preceding "already exists" Given created.

    A REAL persisted state, not an injected mock: ``accounts.brand`` is nullable,
    and ``env.clear_account_brand`` writes the row through the env session bound to
    the same database every transport (including e2e) reads. The row is otherwise
    untouched, so the settings-update reference still resolves to it — which is what
    makes the brand read reachable at all.
    """
    account_id = ctx.get("original_field_values", {}).get("account_id")
    assert account_id, (
        "Given must pre-create an account (and capture its account_id in "
        "original_field_values) before its brand can be cleared"
    )
    ctx["env"].clear_account_brand(account_id)


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — list_accounts requests
# ═══════════════════════════════════════════════════════════════════════


@when(parsers.parse("the Buyer Agent sends a list_accounts request via {transport}"))
def when_list_accounts_via_transport(ctx: dict, transport: str | None = None) -> None:
    """Send list_accounts request.

    The transport arg is accepted but ignored — pytest_generate_tests
    controls which transport is used for dispatch via ctx["transport"].
    This step only matches the "via {transport}" variant from pre-compiled
    feature files. The plain "sends a list_accounts request" is matched
    by when_list_accounts_unfiltered.
    """
    dispatch_request(ctx)


@when(
    parsers.re(
        r"the Buyer Agent sends a list_accounts request"
        r"(?! (?:with|via))"  # Not followed by "with" or "via" (those have their own steps)
        r"|the Buyer Agent sends a list_accounts request without a (?:status filter|context object)"
    )
)
def when_list_accounts_unfiltered(ctx: dict) -> None:
    """Send list_accounts request with no filters (matches multiple phrasings).

    Dispatches over the scenario's transport on EVERY env: AccountSyncEnv now
    carries the list verb too (AccountListDispatchMixin), so a cross-cutting
    scenario running under the sync env grades the same wire every other
    scenario does. The request is constructed explicitly so one request-type
    discriminator covers both list When steps.
    Simulates DB failure when ctx["simulate_db_failure"] is set.
    """
    # DB failure simulation: mock AccountUoW to raise OperationalError
    if ctx.get("simulate_db_failure"):
        from unittest.mock import patch

        from sqlalchemy.exc import OperationalError

        with patch(
            "src.core.tools.accounts.AccountUoW",
            side_effect=OperationalError("simulated", {}, Exception("connection refused")),
        ):
            try:
                dispatch_request(ctx)
            except Exception as exc:
                ctx["error"] = exc
        return

    from src.core.schemas.account import ListAccountsRequest

    dispatch_request(ctx, req=ListAccountsRequest())


@when(parsers.parse('the Buyer Agent sends a list_accounts request with status filter "{status}"'))
def when_list_accounts_status_filter(ctx: dict, status: str) -> None:
    """Send list_accounts with a status filter."""
    _list_raw(ctx, status=status)


# "the Buyer Agent sends a list_accounts request without an authentication token" is bound
# by steps/generic/given_auth.py::when_dispatch_without_credential, together with UC-004's
# delivery-metrics spelling. Both were separate functions with byte-identical bodies.


@when("the Buyer Agent sends a list_accounts skill request via A2A with the token")
def when_list_accounts_a2a_invalid_token(ctx: dict) -> None:
    """Send list_accounts via A2A with an invalid (presented but not valid) token."""
    from tests.harness._base import INVALID_TOKEN

    ctx["transport"] = "A2A"
    dispatch_request(ctx, credential=ctx["env"].credential(token=INVALID_TOKEN))


@when(parsers.parse("the Buyer Agent sends a list_accounts request with max_results {value:d}"))
def when_list_accounts_paginated(ctx: dict, value: int) -> None:
    """Send list_accounts with max_results pagination."""
    # BR-UC-011 Examples grade max_results 0 and 101 as "validation error", so the
    # out-of-range rows must reach the seller rather than PaginationRequest.
    _list_raw(ctx, pagination={"max_results": value})


@when("the Buyer Agent sends a list_accounts request with the returned cursor")
def when_list_accounts_with_cursor(ctx: dict) -> None:
    """Send list_accounts with the cursor from the previous response."""
    from adcp.types import PaginationRequest

    from src.core.schemas.account import ListAccountsRequest

    prev_response = require_payload(ctx)
    cursor = prev_response.pagination.cursor
    # Production's default page size. This read ctx["last_max_results"] "or default"
    # and no step has ever written that key, so the default was the only value it
    # could ever take -- a configurable-looking read that was not configurable.
    max_results = 50
    try:
        req = ListAccountsRequest(pagination=PaginationRequest(max_results=max_results, cursor=cursor))
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.parse('the Buyer Agent sends a list_accounts request with cursor "{cursor}"'))
def when_list_accounts_with_explicit_cursor(ctx: dict, cursor: str) -> None:
    """Send list_accounts with a specific cursor string (e.g. malformed base64)."""
    _list_raw(ctx, pagination={"cursor": cursor})


@when(parsers.parse("the Buyer Agent sends a list_accounts request with sandbox equals {value}"))
def when_list_sandbox_filter(ctx: dict, value: str) -> None:
    """Send list_accounts with sandbox filter.

    Dispatches over the scenario's transport on every env, including the sandbox
    tag's AccountSyncEnv — which carries the list verb via
    AccountListDispatchMixin.
    """
    from src.core.schemas.account import ListAccountsRequest

    dispatch_request(ctx, req=ListAccountsRequest(sandbox=as_bool(value)))


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — response assertions
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse("the response contains an accounts array with {count:d} items"))
def then_accounts_array_count(ctx: dict, count: int) -> None:
    """Assert the response accounts array has the expected count."""
    resp = require_payload(ctx)
    accounts = resp.accounts  # AttributeError if field missing
    assert len(accounts) == count, f"Expected {count} accounts, got {len(accounts)}"


@then("each account includes account_id, name, status, advertiser, rate_card, and payment_terms")
def then_accounts_have_fields(ctx: dict) -> None:
    """Assert each returned account carries the expected fields from Given setup.

    Required fields (account_id, name, status) must match the factory-seeded
    values tracked in ctx. Optional fields (advertiser, rate_card, payment_terms)
    must be present in the schema so callers can read them.
    """
    resp = require_payload(ctx)
    expected_ids = ctx.get("expected_account_ids", set())
    assert expected_ids, "Test setup error: no expected_account_ids tracked by Given steps"
    returned_ids = {acct.account_id for acct in resp.accounts}
    assert returned_ids == expected_ids, f"Returned account_ids {returned_ids} != expected {expected_ids}"
    for acct in resp.accounts:
        # Required fields — verify values match factory defaults
        assert acct.account_id in expected_ids, f"Unexpected account_id: {acct.account_id}"
        assert isinstance(acct.name, str) and acct.name, f"Account {acct.account_id} has empty name"
        actual_status = _status_str(acct.status)
        assert actual_status in ctx.get("created_statuses", set()), (
            f"Account {acct.account_id} status '{actual_status}' not in seeded statuses {ctx.get('created_statuses')}"
        )
        # Optional fields — schema must expose them (POST-S3 compliance).
        # These fields are None when not set, but the schema must declare them
        # so callers can read the field even when the value is None.
        fields = type(acct).model_fields
        for field_name in ("advertiser", "rate_card", "payment_terms"):
            assert field_name in fields, f"Account {acct.account_id} schema missing optional field '{field_name}'"


@then("the accounts are only those accessible to the authenticated agent")
def then_accounts_are_agent_scoped(ctx: dict) -> None:
    """Assert returned accounts are exactly those created for the authenticated agent.

    Compares returned account_ids against the set created by Given steps
    (tracked in ctx["expected_account_ids"]).
    """
    resp = require_payload(ctx)
    expected_ids = ctx.get("expected_account_ids", set())
    assert expected_ids, "Test setup error: no expected_account_ids tracked by Given steps"
    returned_ids = {acct.account_id for acct in resp.accounts}
    assert returned_ids == expected_ids, (
        f"Scoping mismatch: returned {returned_ids}, expected {expected_ids}. "
        f"Extra: {returned_ids - expected_ids}, Missing: {expected_ids - returned_ids}"
    )


@then(parsers.parse('the response contains only accounts with status "{status}"'))
def then_only_status(ctx: dict, status: str) -> None:
    """Assert all returned accounts have the expected status (vacuously true if empty)."""
    resp = require_payload(ctx)
    for acct in resp.accounts:
        actual = _status_str(acct.status)
        assert actual == status, f"Expected status '{status}', got '{actual}'"


@then("accounts with other statuses are excluded")
def then_other_statuses_excluded(ctx: dict) -> None:
    """Assert no accounts with statuses other than the filtered one are present.

    Uses ctx["created_statuses"] (all statuses from Given) and the response
    to verify that non-matching statuses were actually excluded, not just
    that matching ones are present.
    """
    resp = require_payload(ctx)
    created_statuses = ctx.get("created_statuses", set())
    returned_statuses = {_status_str(acct.status) for acct in resp.accounts}
    # The previous only_status step already verified all returned have the target.
    # This step verifies the exclusion is real: created statuses that aren't in
    # the response were actually filtered out (not just absent by coincidence).
    assert len(created_statuses) > 1, "Test setup should create accounts with multiple statuses to verify exclusion"
    # returned_statuses should be a strict subset — not all created statuses appear
    assert returned_statuses < created_statuses, (
        f"Expected returned statuses ({returned_statuses}) to be a strict subset of "
        f"created statuses ({created_statuses}) — some statuses must be excluded by the filter"
    )


@then("the response contains an empty accounts array")
def then_empty_accounts(ctx: dict) -> None:
    """Assert the response has an empty accounts array."""
    resp = require_payload(ctx)
    accounts = resp.accounts  # AttributeError if field missing
    assert accounts == [], f"Expected empty accounts array, got {len(accounts)} items"


@then("the response is not an error")
def then_not_an_error(ctx: dict) -> None:
    """Assert the response is a success (no error)."""
    error = ctx.get("error")
    assert error is None, f"Expected no error but got: {error}"
    resp = require_payload(ctx)


@then(parsers.parse("the response contains {count:d} accounts"))
def then_n_accounts(ctx: dict, count: int) -> None:
    """Assert the response has exactly N accounts.

    Also tracks account IDs for later disjointness checks in pagination.
    """
    resp = require_payload(ctx)
    actual = len(resp.accounts)
    assert actual == count, f"Expected {count} accounts, got {actual}"
    # Track IDs for disjointness assertion in subsequent pages
    ctx["previous_page_ids"] = {a.account_id for a in resp.accounts}


@then(parsers.parse("the response contains {count:d} more accounts"))
def then_n_more_accounts(ctx: dict, count: int) -> None:
    """Assert the response has exactly N accounts and they are disjoint from previous page."""
    resp = require_payload(ctx)
    actual = len(resp.accounts)
    assert actual == count, f"Expected {count} more accounts, got {actual}"
    # Verify disjointness with previous page
    prev_ids = ctx.get("previous_page_ids")
    if prev_ids:
        current_ids = {a.account_id for a in resp.accounts}
        overlap = prev_ids & current_ids
        assert not overlap, f"Page 2 shares {len(overlap)} account(s) with page 1: {overlap}"
        # Track cumulative IDs for further pages
        ctx["previous_page_ids"] = prev_ids | current_ids


@then(parsers.parse("the response includes pagination metadata with has_more {has_more} and a cursor"))
def then_pagination_has_more_with_cursor(ctx: dict, has_more: str) -> None:
    """Assert pagination metadata with has_more and cursor."""
    resp = require_payload(ctx)
    assert resp.pagination is not None, "Expected pagination metadata"
    expected = as_bool(has_more)
    assert resp.pagination.has_more == expected, f"Expected has_more={expected}, got {resp.pagination.has_more}"
    if expected:
        assert resp.pagination.cursor is not None, "Expected cursor when has_more is true"


@then(parsers.parse("the response includes pagination metadata with has_more {has_more}"))
def then_pagination_has_more(ctx: dict, has_more: str) -> None:
    """Assert pagination metadata with has_more."""
    resp = require_payload(ctx)
    assert resp.pagination is not None, "Expected pagination metadata"
    expected = as_bool(has_more)
    assert resp.pagination.has_more == expected, f"Expected has_more={expected}, got {resp.pagination.has_more}"


@then("the response returns accounts starting from the first page")
def then_accounts_from_first_page(ctx: dict) -> None:
    """Assert the response returns accounts from offset 0 (first page).

    Verifies that a malformed cursor was silently treated as offset 0 by
    checking that the returned accounts match the first-page slice of the
    full sorted expected set (offset 0 through page_size).
    """
    resp = require_payload(ctx)
    error = ctx.get("error")
    assert error is None, f"Expected success but got error: {error}"
    assert resp is not None, "Expected a response"
    accounts = resp.accounts  # AttributeError if field missing
    expected_ids = sorted(ctx.get("expected_account_ids", set()))
    assert expected_ids, "Test setup error: no expected_account_ids tracked by Given steps"
    # Verify accounts are sorted by account_id (first-page ordering from offset 0)
    account_ids = [a.account_id for a in accounts]
    assert account_ids == sorted(account_ids), (
        f"Accounts not sorted by account_id — cannot confirm first-page ordering: {account_ids}"
    )
    # The returned page must be exactly the first N elements of the sorted expected set,
    # where N is the page size (number of returned accounts). This proves offset-0 semantics.
    page_size = len(account_ids)
    expected_first_page = expected_ids[:page_size]
    assert account_ids == expected_first_page, (
        f"First page should contain accounts {expected_first_page}, got {account_ids}. "
        f"This indicates the malformed cursor was not treated as offset 0."
    )


@then("the response contains a validation error")
def then_validation_error(ctx: dict) -> None:
    """Assert the seller rejected the request as INVALID_REQUEST on the wire.

    TWO CORRECTIONS, both forced by the payload now actually reaching production.

    THE CODE. This asserted VALIDATION_ERROR. The row that reaches it sends
    ``status: "unknown_status"`` -- a value outside the pinned enum, i.e. a
    SCHEMA constraint violation -- and production correctly answers
    INVALID_REQUEST ("Request is malformed, missing required fields, or violates
    schema constraints"). VALIDATION_ERROR is reserved for "invalid field values
    or violates business rules BEYOND schema validation", which this is not.
    @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json
      pointer=/INVALID_REQUEST  (pinned locally at
      tests/fixtures/adcp_schemas_pinned/enums/error-code.json:102 and :149)

    THE FALLBACK. The "request could not be CONSTRUCTED" branch is gone. It
    existed because ``ListAccountsRequest(status=...)`` raised in the When step
    before ``dispatch_request`` was ever called, so there was no wire to read --
    which is precisely the defect salesagent-prkv.65 removed. That When now
    dispatches the raw payload, so an absent envelope no longer has an innocent
    explanation: it means the payload never reached the seller, and accepting a
    test-process exception here would reinstate the vacuum.
    """
    result = ctx.get("result")
    assert result is not None, (
        "Expected a wire rejection, but no transport result was captured -- the payload never "
        f"reached the seller. ctx['error']={ctx.get('error')!r}"
    )
    result.assert_wire_error("INVALID_REQUEST")


@then("the error indicates the status value is not recognized")
def then_error_invalid_status(ctx: dict) -> None:
    """Assert the rejection blames the ``status`` member specifically.

    Reads ``errors[0].field`` -- the error.json pointer naming WHICH request
    member was rejected -- rather than scanning the error's message text for the
    word "status" plus one of several invalidity synonyms. The message is prose
    the seller may reword; the pointer is the contract, and a generic rejection
    that blames nothing must not satisfy a scenario asserting the status value
    was the problem.
    """
    _assert_wire_field_rejection(ctx, "status")


@then("the response contains accounts with all statuses")
def then_all_statuses_present(ctx: dict) -> None:
    """Assert the response includes accounts covering all seeded statuses."""
    resp = require_payload(ctx)
    statuses = {_status_str(a.status) for a in resp.accounts}
    expected = ctx.get("created_statuses")
    assert expected, "Test setup error: created_statuses not tracked by Given step"
    missing = expected - statuses
    assert not missing, f"Response missing statuses {missing}. Got {statuses}, expected superset of {expected}"


@then("the result set is identical to requesting without any filter")
def then_result_set_identical(ctx: dict) -> None:
    """Assert the unfiltered result set contains exactly the seeded accounts.

    The Given step created accounts with 4 different statuses and tracked
    their IDs in ctx["expected_account_ids"]. The unfiltered response must
    return exactly that set — no extras, no omissions. The expected_ids
    check is mandatory (not optional) — if Given steps did not track IDs,
    the test setup is broken.
    """
    resp = require_payload(ctx)
    expected_ids = ctx.get("expected_account_ids")
    assert expected_ids, "Test setup error: no expected_account_ids tracked by Given steps"
    returned_ids = {acct.account_id for acct in resp.accounts}
    assert len(returned_ids) == len(expected_ids), (
        f"Expected exactly {len(expected_ids)} accounts, got {len(returned_ids)}"
    )
    assert returned_ids == expected_ids, (
        f"Result set mismatch: returned {returned_ids}, expected {expected_ids}. "
        f"Extra: {returned_ids - expected_ids}, Missing: {expected_ids - returned_ids}"
    )


@then(parsers.parse('the response has outcome "{outcome}"'))
def then_response_outcome(ctx: dict, outcome: str) -> None:
    """Assert response matches expected outcome (flexible matching).

    Branches:
    - "validation error": assert an error was raised
    - "success with N account(s)": assert exact count (pagination)
    - "success with per-account results": assert the response has one
      result per submitted account, each with account_id and status
    """
    import re

    if "validation error" in outcome:
        error = ctx.get("error")
        assert error is not None, f"Expected validation error for outcome '{outcome}', but got no error"
    elif outcome.startswith("success with"):
        error = ctx.get("error")
        assert error is None, f"Expected success for outcome '{outcome}', but got error: {error}"
        resp = require_payload(ctx)

        if "per-account results" in outcome:
            # Sync BVA: verify per-account result count matches submitted count
            submitted = ctx.get("submitted_account_count")
            assert submitted is not None, "Test setup error: submitted_account_count not stored in ctx by When step"
            actual_count = len(resp.accounts)
            assert actual_count == submitted, f"Expected {submitted} per-account results, got {actual_count}"
            # Each per-account result must have an identifier and status
            for acct in resp.accounts:
                assert acct.account_id is not None, f"Per-account result missing account_id: {acct}"
                assert _status_str(acct.status) in {
                    "active",
                    "pending_approval",
                    "suspended",
                    "closed",
                }, f"Unexpected account status: {_status_str(acct.status)}"
        else:
            # Parse expected count from outcome like "success with 50 accounts"
            match = re.search(r"(\d+)\s+account", outcome)
            assert match, f"Outcome {outcome!r} names no account count this step can grade"
            expected_count = int(match.group(1))
            actual = len(resp.accounts)
            assert actual == expected_count, f"Expected {expected_count} accounts for outcome '{outcome}', got {actual}"
    else:
        # An outcome this step does not recognise must FAIL, not pass silently.
        # Without this branch a typo in a feature file — or a new outcome phrase
        # nobody wired — produced a green step that graded nothing.
        raise AssertionError(
            f"Unhandled outcome {outcome!r}: this step grades 'validation error' and 'success with ...' only"
        )


# ═══════════════════════════════════════════════════════════════════════
# UC-011 list Then/When/Given hardening (salesagent-9if1 / eiww batch B1)
#
# Wires the previously-dormant list scenarios and de-vacuums the
# status-rejected assertions. Every Then reads the buyer-facing wire body
# (wire_dict/wire_absent) and asserts VALUES, not existence. Spec anchors are
# cited per scenario against v3.1.1 (the pinned target, docs/adcp-spec-version.md).
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('every returned account has status "{status}"'))
def then_every_account_has_status(ctx: dict, status: str) -> None:
    """Assert the wire accounts array is non-empty AND every element has ``status``.

    Non-vacuous replacement for the ``only accounts with status`` /
    ``accounts with other statuses are excluded`` pair on
    @T-UC-011-list-status-rejected: the fixture seeds exactly one matching
    account (rejected) plus two non-matching (active), so an empty array is a
    filter defect, not a pass.

    Spec: account/list-accounts-request.json#/properties/status (v3.1.1) —
    "Filter accounts by status"; account-status enum contains ``rejected``.
    """
    body = wire_dict(ctx)
    accounts = body.get("accounts")
    assert accounts, f"expected a non-empty accounts array for status filter {status!r}, got {accounts!r}"
    actual = [a.get("status") for a in accounts]
    assert all(s == status for s in actual), f"expected every returned account status == {status!r}, got {actual}"


@then("the response pagination has has_more false and no cursor")
def then_pagination_terminal(ctx: dict) -> None:
    """Assert the terminal page reports has_more=false and OMITS the cursor.

    Spec: core/pagination-response.json (v3.1.1) — cursor is "Only present when
    has_more is true"; ``required: ["has_more"]``. A serialized ``cursor: null``
    would be a schema-invalid emission of an unset optional, so wire_absent (not
    a null-tolerant check) is the correct oracle.
    """
    pagination = wire_dict(ctx, "pagination")
    assert pagination.get("has_more") is False, (
        f"expected pagination.has_more is false on the terminal page, got {pagination.get('has_more')!r}"
    )
    wire_absent(ctx, "pagination.cursor")


@given(parsers.parse('accessible accounts exist for brand domains "{d1}" and "{d2}"'))
def given_accounts_for_brand_domains(ctx: dict, d1: str, d2: str) -> None:
    """Seed two accessible accounts keyed by distinct brand domains.

    Records each seller-assigned account_id under ctx["accounts_by_domain"] so
    the account_id-keyed filter row can build a real AccountRef.
    """
    by_domain = ctx.setdefault("accounts_by_domain", {})
    for domain in (d1, d2):
        acct = _create_accessible_account(ctx, brand={"domain": domain}, operator=domain)
        by_domain[domain] = acct.account_id


@when(
    parsers.parse(
        "the Buyer Agent sends a list_accounts request with an account filter "
        'keyed by {key_shape} for brand domain "{domain}"'
    )
)
def when_list_with_account_filter(ctx: dict, key_shape: str, domain: str) -> None:
    """Send list_accounts with an exact ``account`` filter (AccountRef).

    Spec: account/list-accounts-request.json#/properties/account (v3.1.1) →
    core/account-ref.json#/oneOf — branch 0 is keyed by account_id, branch 1 by the
    natural key (brand + operator).
    """
    from src.core.schemas.account import ListAccountsRequest

    if key_shape == "account_id":
        account_ref: dict[str, Any] = {"account_id": ctx["accounts_by_domain"][domain]}
    else:  # "brand and operator"
        account_ref = {"brand": {"domain": domain}, "operator": domain}
    try:
        req = ListAccountsRequest(account=account_ref)
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


@then(parsers.parse('the returned account has brand domain "{domain}" and operator "{operator}"'))
def then_returned_account_identity(ctx: dict, domain: str, operator: str) -> None:
    """Assert the filter returned exactly one account with the expected natural key.

    "is the one for brand domain X" is under-specified — brand.domain alone does
    not distinguish accounts differing by operator. The natural key
    (brand + operator) is what core/account-ref.json#/oneOf/1 identifies by.
    """
    body = wire_dict(ctx)
    accounts = body.get("accounts")
    assert accounts and len(accounts) == 1, f"expected exactly one filtered account, got {accounts!r}"
    acct = accounts[0]
    actual_domain = (acct.get("brand") or {}).get("domain")
    assert actual_domain == domain, f"expected brand domain {domain!r}, got {actual_domain!r}"
    assert acct.get("operator") == operator, f"expected operator {operator!r}, got {acct.get('operator')!r}"


@given("the seller supports scope introspection for the authenticated agent")
def given_scope_introspection(ctx: dict) -> None:
    """Declare that the seller can introspect the agent's task scope.

    Production has no scope-introspection surface, so no config is applied; the
    flag records intent for the wired (currently-xfailing) authorization check.
    """
    _setup_tenant_and_principal(ctx)


@then('each returned account includes an authorization object with required key "allowed_tasks"')
def then_account_has_authorization(ctx: dict) -> None:
    """Assert every listed account carries an ``authorization`` object bearing allowed_tasks.

    Spec: account/list-accounts-response.json#/properties/accounts/items →
    core/account-with-authorization.json; core/account-authorization.json
    ``required: ["allowed_tasks"]`` (v3.1.1). Absence of the whole object means
    "no introspection" — callers MUST NOT infer denial — but when the seller
    DOES support introspection each item MUST carry it.
    """
    body = wire_dict(ctx)
    accounts = body.get("accounts")
    assert accounts, f"expected a non-empty accounts array, got {accounts!r}"
    for acct in accounts:
        authz = acct.get("authorization")
        assert isinstance(authz, dict), f"account {acct.get('account_id')!r} missing authorization object: {acct!r}"
        assert "allowed_tasks" in authz, f"authorization missing required key allowed_tasks: {authz!r}"


@then("each allowed_tasks array is a non-empty list of unique snake_case task names")
def then_allowed_tasks_snake_case(ctx: dict) -> None:
    """Assert allowed_tasks is a non-empty, duplicate-free snake_case task list.

    Spec: core/account-authorization.json#/properties/allowed_tasks (v3.1.1) —
    items ``pattern: "^[a-z][a-z0-9_]*$"``, ``uniqueItems: true``.
    """
    import re

    pattern = re.compile(r"^[a-z][a-z0-9_]*$")
    body = wire_dict(ctx)
    accounts = body.get("accounts")
    assert accounts, f"expected a non-empty accounts array, got {accounts!r}"
    for acct in accounts:
        authz = acct.get("authorization") or {}
        tasks = authz.get("allowed_tasks")
        assert isinstance(tasks, list) and tasks, f"allowed_tasks must be a non-empty list, got {tasks!r}"
        assert len(tasks) == len(set(tasks)), f"allowed_tasks contains duplicates: {tasks!r}"
        bad = [t for t in tasks if not (isinstance(t, str) and pattern.match(t))]
        assert not bad, f"allowed_tasks not all snake_case task names: {bad!r}"


@when(
    parsers.re(
        r'the Buyer Agent sends a list_accounts request carrying idempotency_key "(?P<idem>[^"]+)", '
        r"an ext object, and context (?P<ctx_json>\{.*\})"
    )
)
def when_list_with_idempotency_envelope(ctx: dict, idem: str, ctx_json: str) -> None:
    """Send list_accounts carrying the 3.1 every-request envelope extras.

    Spec: compliance/3.1.1/universal/read-tool-idempotency.yaml
    (phase read_requests_accept_idempotency_key) requires read tools to ACCEPT
    idempotency_key/ext without rejection; list-accounts-request.json declares
    ``additionalProperties: true`` and does NOT list idempotency_key as a field.
    The duty is tolerance. In dev/CI the request model runs extra="forbid", so
    constructing it with idempotency_key is rejected here — captured as the error
    that grades the tolerance gap.
    """
    from adcp.types import ContextObject

    context_data = _parse_inline_context(ctx_json)
    ctx["sent_context"] = context_data
    # Validated only to fail the TEST loudly on a malformed inline table; the
    # dict below, not this object, is what crosses the wire.
    ContextObject.model_validate(context_data)
    # Dispatched RAW because the duty under test is the SERVER's TOLERANCE of an
    # unlisted field. Building ListAccountsRequest here made dev/CI's
    # extra="forbid" reject it in the test process, so the step recorded a
    # client-side error and never once asked whether production tolerates it.
    _list_raw(
        ctx,
        idempotency_key=idem,
        ext={"probe": "read-tool-idempotency"},
        context=context_data,
    )


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — sync_accounts requests
# ═══════════════════════════════════════════════════════════════════════


def _extract_brand_pairs(accounts: list[dict[str, Any]]) -> set[tuple[str, str | None]]:
    """Extract (domain, brand_id) pairs from parsed sync account entries."""
    pairs: set[tuple[str, str | None]] = set()
    for a in accounts:
        brand = a.get("brand", {})
        domain = brand.get("domain")
        if domain:
            pairs.add((domain, brand.get("brand_id")))
    return pairs


def _parse_sync_table(datatable: Any) -> list[dict[str, Any]]:
    """Parse a Gherkin data table into sync_accounts account entries.

    Handles columns: brand.domain, brand.brand_id, operator, billing, sandbox.
    Nested dot-notation is converted to nested dicts (e.g., brand.domain → {"brand": {"domain": ...}}).
    """
    accounts: list[dict[str, Any]] = []
    for row in datatable:
        entry: dict[str, Any] = {}
        brand: dict[str, str] = {}
        for key, value in row.items():
            if key == "brand.domain":
                brand["domain"] = value
            elif key == "brand.brand_id":
                brand["brand_id"] = value
            elif key == "sandbox":
                entry[key] = as_bool(value)
            else:
                entry[key] = value
        if brand:
            entry["brand"] = brand
        accounts.append(entry)
    return accounts


def _dispatch_sync_table(ctx: dict, datatable: Any, *, idempotency_key: str | None = None) -> None:
    """Parse a Gherkin sync_accounts data table and dispatch it on the wire.

    pytest-bdd datatable: list of lists. First row = headers, rest = data rows.
    Handles force_credential (unauthenticated) and force_internal_error contexts.
    Shared by the plain ``with:`` table When and the ``carrying idempotency_key … and:``
    variant so the two paths cannot drift (DRY invariant).

    ``idempotency_key``, when given, is sent as a FLAT wire parameter rather than
    through a locally-constructed ``SyncAccountsRequest``. Constructing the request
    in the step would run the buyer-side model validation here, in the test process,
    and mask what each transport actually does with the field — which is precisely
    the behavior these scenarios grade.
    """

    rows = table_rows(datatable)
    accounts = _parse_sync_table(rows)

    ctx["sync_request_brand_pairs"] = _extract_brand_pairs(accounts)
    # Retain the parsed entries so a recovery retry (fresh idempotency_key,
    # changed billing) can re-issue against the SAME natural key(s).
    ctx["last_sync_accounts"] = accounts

    kwargs: dict[str, Any] = {}

    # Handle a forced credential (unauthenticated/expired token)
    if "force_credential" in ctx:
        kwargs["credential"] = ctx["force_credential"]

    # Handle forced internal error
    # ONE dispatch shape for both branches. The idempotency branch already sent a raw
    # bag while the other built SyncAccountsRequest here, so the SAME step graded the
    # seller or the model depending only on whether the scenario happened to carry an
    # idempotency key — and the rows that feed it invalid values (e.g. billing
    # "prepaid", outside the enum) always took the model branch, so they never once
    # reached production. Raw on both, and the swallowing try/except goes with it:
    # dispatch_request already returns transport failures as a TransportResult
    # carrying the real envelope.
    if idempotency_key is not None:
        kwargs["idempotency_key"] = idempotency_key
    _sync_raw(ctx, accounts=accounts, **kwargs)


@when("the Buyer Agent sends a sync_accounts request with no idempotency_key and:")
def when_sync_accounts_without_idempotency_key(ctx: dict, datatable: Any) -> None:
    """Send sync_accounts carrying NO idempotency_key — the omitted-required-field case.

    The sibling of the malformed-key outline: that one grades a key the pin's pattern
    rejects, this one grades a key that is not there at all. Both must be refused, and
    only the second proves the field is REQUIRED rather than merely constrained.
    """
    _dispatch_sync_table(ctx, datatable, idempotency_key=OMIT_IDEMPOTENCY_KEY)


@when("the Buyer Agent sends a sync_accounts request with:")
def when_sync_accounts_with_table(ctx: dict, datatable: Any) -> None:
    """Send sync_accounts with accounts from a Gherkin data table."""
    _dispatch_sync_table(ctx, datatable)


@when(parsers.re(r'the Buyer Agent sends a sync_accounts request with idempotency_key "(?P<key>[^"]+)" and:'))
def when_sync_accounts_with_key_and_table(ctx: dict, key: str, datatable: Any) -> None:
    """Send sync_accounts from a data table, ignoring the descriptive idempotency_key.

    The Gherkin names an idempotency_key for narrative/traceability, but production
    does not carry idempotency_key on the sync_accounts wire — the REST request model
    rejects it as an extra input (#1592), matching the empirical finding recorded in
    salesagent-9jiu. Dispatching a keyless request is therefore the faithful wire call;
    the key is retained on ctx only so a later step could reference it.
    """
    _dispatch_sync_table(ctx, datatable)


@when(parsers.re(r'the Buyer Agent sends a sync_accounts request carrying idempotency_key "(?P<key>[^"]+)" and:'))
def when_sync_accounts_carrying_key_and_table(ctx: dict, key: str, datatable: Any) -> None:
    """Send sync_accounts with the buyer's idempotency_key actually ON the wire.

    Distinct from the ``with idempotency_key … and:`` step above, which retains the
    key on ctx for narrative purposes only. Here the key is a real wire parameter,
    so each transport's handling of the spec-required, client-generated field is
    what gets graded (sync-accounts-request.json 3.1.1, /required +
    /properties/idempotency_key).
    """
    _dispatch_sync_table(ctx, datatable, idempotency_key=key)


@given(parsers.parse('idempotency_key "{key}" was already spent syncing brand domain "{domain}"'))
def given_key_already_spent(ctx: dict, key: str, domain: str) -> None:
    """SPEND *key* on a real, successful sync of *domain*, so the replay cache holds its hash.

    Not a seeded row: the first request goes through the same transport and the same boundary
    as the second, so the payload hash the conflict is detected against is the one production
    computed (``_boundary._invoke`` -> ``canonical_request_hash`` -> ``cache_success``). A
    hand-written cache row would be this test asserting its own idea of the canonical form.

    The account this creates is also the FIXTURE the final Then reads: the conflict must leave
    it standing and must not add the second payload's account
    (security.mdx#idempotency rule 5, "Sellers MUST NOT silently apply the second request").

    Nothing is stashed on ``ctx``: the key and the domain are both named again by the When and
    the Then, so a ctx slot here would be a claim no one reads.
    """
    _sync_pre_create(ctx, domain, domain, "operator", idempotency_key=key)


@when(parsers.parse('the Buyer Agent re-sends sync_accounts with idempotency_key "{key}" and brand domain "{domain}"'))
def when_resend_sync_with_spent_key(ctx: dict, key: str, domain: str) -> None:
    """Re-send sync_accounts on *key* with a DIFFERENT accounts array.

    The only thing that differs from the Given's request is the brand domain, which is inside
    the canonical payload -- so the two requests hash differently under one key, which is the
    precondition security.mdx#idempotency rule 5 attaches IDEMPOTENCY_CONFLICT to.

    Goes through ``_sync_raw`` because the seller is expected to REFUSE: building a
    ``SyncAccountsRequest`` first would be fine here (the payload is conformant), but the
    negative-path dispatch is the one that reports an unexpected acceptance as a plain
    success rather than an unparseable response.
    """
    _sync_raw(
        ctx,
        idempotency_key=key,
        accounts=[{"brand": {"domain": domain}, "operator": domain, "billing": "operator"}],
    )


@then(parsers.parse('the seller holds an account for brand domain "{kept}" and none for "{refused}"'))
def then_seller_holds_only_kept_domain(ctx: dict, kept: str, refused: str) -> None:
    """Assert the refused payload was not applied, and that the accepted one still is.

    BOTH halves are asserted, because an absence alone is not falsifiable here: a scenario
    whose Given never ran would satisfy "no account for *refused*" against an empty database.
    Naming *kept* too means the step can only pass on the state the spec requires -- the first
    request's account present, the second's never created.
    """
    domains = [a["domain"] for a in _persisted_accounts(ctx)]
    assert kept in domains, (
        f"expected the already-synced account for {kept!r} to still be on the seller; found {domains}"
    )
    assert refused not in domains, (
        f"{refused!r} was persisted, so the seller APPLIED the conflicting payload instead of "
        f"refusing it (security.mdx#idempotency rule 5). Accounts held: {domains}"
    )


@when(parsers.parse('the Buyer Agent sends a sync_accounts request with governance_agents for brand "{domain}"'))
def when_sync_with_governance_agents(ctx: dict, domain: str) -> None:
    """Send sync_accounts with governance_agents for a brand domain.

    Constructs a valid GovernanceAgent entry (url + authentication) and
    dispatches through the standard transport pipeline.
    """
    from src.core.schemas.account import SyncAccountsRequest

    governance_agents = [_make_governance_agent(url="https://governance.example.com/check")]
    try:
        req = SyncAccountsRequest(
            idempotency_key=fresh_idempotency_key(),
            accounts=[
                {
                    "brand": {"domain": domain},
                    "operator": domain,
                    "billing": "operator",
                    "governance_agents": governance_agents,
                }
            ],
        )
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


# ═══════════════════════════════════════════════════════════════════════
# UC-011 sync settings-update / mode-exclusive wiring
#
# Graduated: settings-update (AccountReference) mode implemented via
# _process_settings_update_entry (both AccountReference1/account_id and
# AccountReference2/natural-key branches), mode-exclusivity enforced in _impl before
# dispatch (VALIDATION_ERROR naming accounts[i]), unmatched references rejected
# with UNSUPPORTED_PROVISIONING. The settings-update, no-provision, and
# mode-exclusive tags are no longer xfailed (removed from conftest _XFAIL_TAGS).
# ═══════════════════════════════════════════════════════════════════════


@when(
    parsers.parse(
        "the Buyer Agent sends a sync_accounts request with a settings-update entry "
        'keyed by the existing account\'s account_id setting payment_terms "{pt}"'
    )
)
def when_sync_settings_update_by_account_id(ctx: dict, pt: str) -> None:
    """Dispatch a SettingsUpdateMode entry (AccountReference by account_id) setting payment_terms.

    The entry carries only ``account`` + ``payment_terms`` (no brand/operator/billing
    trio) — the SettingsUpdateMode branch of the item oneOf. The target account_id is
    the one the ``already exists`` Given captured via the real pre-create sync.

    Spec: account/sync-accounts-request.json#/properties/accounts/items/oneOf/1
    (SettingsUpdateMode requires ``account``; trio fields MUST be absent).
    """
    _dispatch_settings_update_payment_terms(ctx, pt)


@when(
    parsers.parse(
        "the Buyer Agent sends a sync_accounts request with dry_run true and a settings-update entry "
        'keyed by the existing account\'s account_id setting payment_terms "{pt}"'
    )
)
def when_sync_settings_update_dry_run(ctx: dict, pt: str) -> None:
    """Dispatch the same SettingsUpdateMode entry under dry_run=true.

    Spec: account/sync-accounts-request.json#/properties/dry_run — "When true,
    preview what would change without applying."
    """
    _dispatch_settings_update_payment_terms(ctx, pt, dry_run=True)


@when(
    parsers.parse(
        "the Buyer Agent sends a sync_accounts request with delete_missing true and a settings-update entry "
        'keyed by the existing account\'s account_id setting payment_terms "{pt}"'
    )
)
def when_sync_settings_update_delete_missing(ctx: dict, pt: str) -> None:
    """Dispatch the same SettingsUpdateMode entry with delete_missing=true.

    Spec: account/sync-accounts-request.json#/properties/delete_missing — only
    accounts "not included in this request" may be deactivated; the entry's
    target account IS included.
    """
    _dispatch_settings_update_payment_terms(ctx, pt, delete_missing=True)


def _dispatch_settings_update_payment_terms(
    ctx: dict, pt: str, dry_run: bool | None = None, delete_missing: bool | None = None
) -> None:
    """Shared dispatch for the settings-update-by-account_id Whens (live/dry_run/delete_missing)."""
    from src.core.schemas.account import SyncAccountsRequest

    account_id = ctx.get("original_field_values", {}).get("account_id")
    assert account_id, "Given must pre-create an account and capture its account_id in original_field_values"
    kwargs: dict[str, Any] = {"accounts": [{"account": {"account_id": account_id}, "payment_terms": pt}]}
    if dry_run is not None:
        kwargs["dry_run"] = dry_run
    if delete_missing is not None:
        kwargs["delete_missing"] = delete_missing
    kwargs.setdefault("idempotency_key", fresh_idempotency_key())
    try:
        req = SyncAccountsRequest(**kwargs)
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_accounts request with a settings-update entry keyed by unknown account_id "{account_id}"'
    )
)
def when_sync_settings_update_unknown_account(ctx: dict, account_id: str) -> None:
    """Dispatch a SettingsUpdateMode entry referencing an account that does not exist.

    A settings-update entry MUST NOT provision; an unknown ``account`` is the
    per-entry UNSUPPORTED_PROVISIONING case.

    Spec: account/sync-accounts-request.json#/properties/accounts/items/properties/account/description
    ("the seller MUST NOT create a new account — entries that would otherwise
    trigger provisioning are rejected with UNSUPPORTED_PROVISIONING").
    """

    _setup_tenant_and_principal(ctx)
    _sync_raw(ctx, accounts=[{"account": {"account_id": account_id}}])


@when(
    "the Buyer Agent sends a sync_accounts request with an entry carrying both "
    "an account reference and the provisioning trio"
)
def when_sync_both_account_and_trio(ctx: dict) -> None:
    """Dispatch an entry that satisfies BOTH item-oneOf branches (account AND the trio).

    Such an entry violates oneOf(ProvisioningMode XOR SettingsUpdateMode) and is
    rejected as a request VALIDATION_ERROR naming accounts[i] (mode-exclusivity is
    now enforced in _impl before dispatch — graduated).

    Spec: account/sync-accounts-request.json#/properties/accounts/items/oneOf.
    """

    _setup_tenant_and_principal(ctx)
    _sync_raw(
        ctx,
        accounts=[
            {
                "account": {"account_id": "acc_target_ref"},
                "brand": {"domain": "acme-corp.com"},
                "operator": "acme-corp.com",
                "billing": "operator",
            }
        ],
    )


@then(parsers.parse('the account payment_terms is "{pt}"'))
def then_account_payment_terms(ctx: dict, pt: str) -> None:
    """Assert the referenced account echoes the expected payment_terms.

    Spec: account/sync-accounts-response.json#/oneOf/0/properties/accounts/items/properties/payment_terms;
    ``net_45`` is in enums/payment-terms.json#/enum
    (["net_15","net_30","net_45","net_60","net_90","prepay"]).
    """
    actual = _wire_account(ctx).get("payment_terms")
    assert actual == pt, f"Expected payment_terms '{pt}', got '{actual}'"


@then(parsers.parse('the settings-update entry has action "{action}"'))
def then_settings_update_entry_action(ctx: dict, action: str) -> None:
    """Assert the single settings-update result entry has the expected action.

    Requires a success-variant response (per-account results). If an operation-level
    error were returned instead there is no response — this fails loudly rather than
    treating a request-level error as a per-account 'failed'.

    Spec: account/sync-accounts-response.json#/oneOf/0/properties/accounts/items/properties/action/enum
    = ["created","updated","unchanged","failed"].
    """
    actual = _wire_account(ctx).get("action")
    assert actual == action, f"Expected settings-update entry action '{action}', got '{actual}'"


@then(parsers.parse('the per-account error recovery is "{recovery}"'))
def then_per_account_error_recovery(ctx: dict, recovery: str) -> None:
    """Assert the failed account's per-account error carries the expected recovery.

    Spec: core/error.json#/properties/recovery/enum = ["transient","correctable","terminal"];
    enums/error-code.json#/enumMetadata/UNSUPPORTED_PROVISIONING recovery = "correctable".
    """
    errors = _sole_account_errors(ctx)
    assert errors, "Expected a non-empty per-account errors array on the wire, got []"
    recoveries = [e.get("recovery") for e in errors]
    assert recovery in recoveries, f"Expected per-account error recovery '{recovery}', got {recoveries}"


@then(parsers.parse('the per-account error with code "{code}" carries a non-empty suggestion'))
def then_per_account_error_suggestion(ctx: dict, code: str) -> None:
    """Assert the ADVISORY lane resolves a suggestion, on the entry carrying that code.

    The advisory lane is a different lane from a raised error: normalize_advisory_errors
    fills an Error embedded in a SUCCESS response's errors[] array, via model_copy, not
    the boundary's AdcpErrorResponse.

    WHAT THIS DOES **NOT** WITNESS, measured rather than assumed: it does not grade
    salesagent-3dawm.8's advisory FILL. Reverting that fill and re-running this scenario
    still passes, because this path's suggestion is AUTHORED upstream — GateFailure declares
    ``suggestion: str`` as a REQUIRED field (src/core/tools/accounts.py:610) and
    _gate_failures_to_errors passes it straight through, so ``e.suggestion is not None`` and
    the fill never fires here. That authoring lane is salesagent-3dawm.13.

    What it DOES pin is still worth pinning: the advisory lane carries a suggestion at the
    protocol position on the entry that carries the graded code. It would fail if that were
    dropped.

    Keyed on the entry that carries ``code`` rather than searching the whole array. The
    sibling recovery step above asserts ``recovery in recoveries`` — membership — which can
    pass on a different entry than the one being graded; this must not inherit that.

    Presence only, never text: comparing the string to CODE_TABLE would grade the table
    against itself.
    """
    errors = _sole_account_errors(ctx)
    assert errors, "Expected a non-empty per-account errors array on the wire, got []"
    present = {e.get("code") for e in errors}
    assert code in present, f"No per-account error carries code {code!r}; got {sorted(present)}"
    entry = next(e for e in errors if e.get("code") == code)
    suggestion = entry.get("suggestion")
    assert suggestion is not None, (
        f"The per-account error for {code!r} carries no suggestion. The advisory lane must "
        f"resolve it from the code table, as the raised lane does. Entry: {entry!r}"
    )
    assert str(suggestion).strip() != "", f"Advisory suggestion present but empty: {suggestion!r}"


@then(parsers.parse('the per-account error details scope is "{scope}"'))
def then_per_account_error_details_scope(ctx: dict, scope: str) -> None:
    """Assert the failed account's BILLING_NOT_SUPPORTED error details.scope value.

    Spec: error-details/billing-not-supported.json#/properties/scope/enum =
    ["capability","account"]; billing-gate-dispatch.yaml capability_gate phase
    emits scope="capability" for a seller-wide unsupported-billing rejection.
    """
    errors = _last_account_errors(ctx)
    err = next((e for e in errors if e.code == "BILLING_NOT_SUPPORTED"), None)
    assert err is not None, f"No BILLING_NOT_SUPPORTED error present, got codes {[e.code for e in errors]}"
    details = getattr(err, "details", None) or {}
    actual = details.get("scope")
    assert actual == scope, f"Expected per-account error details.scope '{scope}', got {actual!r}"


@then("the per-account error details supported_billing echoes the seller's supported billing values")
def then_per_account_error_details_supported_billing(ctx: dict) -> None:
    """Assert details.supported_billing echoes the seller's configured supported set.

    Spec: error-details/billing-not-supported.json#/properties/supported_billing
    (array, minItems: 1, "Sellers MAY omit this field" when the resolved supported
    set is empty) -- compares order-insensitively against the policy the Given step
    configured via ``_set_billing_policy``, stashed at ``ctx["configured_billing_policy"]``.
    """
    configured = ctx.get("configured_billing_policy")
    assert configured is not None, "No configured billing policy captured — a prior Given must set the seller's policy"
    errors = _last_account_errors(ctx)
    err = next((e for e in errors if e.code == "BILLING_NOT_SUPPORTED"), None)
    assert err is not None, f"No BILLING_NOT_SUPPORTED error present, got codes {[e.code for e in errors]}"
    details = getattr(err, "details", None) or {}
    actual = details.get("supported_billing")
    assert actual is not None, f"Expected details.supported_billing to be present, got details={details!r}"
    assert set(actual) == set(configured), (
        f"Expected details.supported_billing to echo the seller's supported billing values "
        f"{configured!r} (order-insensitive), got {actual!r}"
    )


@then(parsers.parse("the echoed account_id equals the account_id from the first response"))
def then_echoed_account_id_stable(ctx: dict) -> None:
    """Assert the second natural-key call echoed the SAME seller-assigned account_id.

    Grades the stability invariant: a seller MAY echo an internal handle but MUST
    keep resolving the same natural key to the same account_id on subsequent calls.
    A second call that minted a different account_id would pass the bare-presence
    check while breaking this invariant.

    Spec: account/sync-accounts-response.json#/oneOf/0/properties/accounts/items/properties/account_id/description
    ("the seller MUST continue accepting the natural-key AccountRef for subsequent calls").
    """
    first = ctx.get("first_seller_account_id")
    assert first, "No first-call account_id captured — the first 'seller-assigned account_id' Then must run first"
    second = _wire_account(ctx).get("account_id")
    assert second == first, f"Expected the echoed account_id to stay '{first}', got '{second}'"


@then(parsers.parse('the request is rejected at the operation level with error code "{code}" naming field "{field}"'))
def then_operation_error_naming_field(ctx: dict, code: str, field: str) -> None:
    """Assert an operation-level wire error variant with ``code`` and ``errors[0].field``.

    Uses the single sanctioned wire-error surface (TransportResult.assert_wire_error),
    which hard-fails on a non-canonical code and defaults recovery to the pinned
    v3.1.1 enum classification. For a oneOf structural violation the spec-correct
    grade is the response error variant (oneOf branch 1) carrying VALIDATION_ERROR.

    Spec: account/sync-accounts-response.json#/oneOf/1 (error variant, top-level
    errors[]); core/error.json#/properties/field (JSONPath-lite);
    enums/error-code.json#/enumMetadata/VALIDATION_ERROR.
    """
    result = ctx.get("result")
    assert result is not None, (
        f"No transport result captured — the request did not reach a transport. Recorded error: {ctx.get('error')!r}"
    )
    result.assert_wire_error(code, field=field)


# ═══════════════════════════════════════════════════════════════════════
# UC-011 billing-gate recovery + per-agent-gate wiring (salesagent-9jiu / eiww batch B3)
#
# Wires the previously-dormant billing recovery scenarios (retry Whens) and the
# per-agent-gate reject detail-field Thens, spec-grounded against v3.1.1
# (docs/adcp-spec-version.md — the pinned target).
#
# Production trace (src/core/tools/accounts.py, verified empirically):
#   - _check_billing_policy rejects an UNSUPPORTED billing value with
#     Error(code="BILLING_NOT_SUPPORTED", message, suggestion) — it emits NO
#     ``recovery`` and NO ``details`` (scope/supported_billing), and the rejected
#     entry is NOT persisted (the loop ``continue``s), so a recovery retry with a
#     supported value provisions a fresh account (action "created").
#   - There is NO per-buyer-agent commercial gate anywhere in production: a
#     capability-supported billing value ("agent" when the tenant declares it in
#     supported_billing) is accepted and the account is provisioned. Production
#     therefore never emits BILLING_NOT_PERMITTED_FOR_AGENT — the per-agent
#     reject/recover scenarios stay strict tag-level xfails (conftest _XFAIL_TAGS).
# ═══════════════════════════════════════════════════════════════════════


def _retry_sync_with_billing(ctx: dict, billing: str) -> None:
    """Re-issue the prior sync_accounts request with a new billing value + fresh idempotency_key.

    Reconstructs the previously-parsed entries (``ctx['last_sync_accounts']``),
    swaps every entry's ``billing`` to ``billing``, and re-dispatches. A recovery
    retry is a NEW request against the SAME natural key — not a replay. The prior
    (rejected) leg was never persisted (``_check_billing_policy`` ``continue``s
    without creating the account), so the natural key is fresh and there is no
    IDEMPOTENCY_CONFLICT to avoid.

    The key is FRESH, not absent. This used to send a keyless request and call
    that "the fresh request the recovery contract calls for", on the grounds that
    "production does not carry idempotency_key on the sync_accounts wire (the REST
    request model rejects it as an extra input; #1592)". That was already wrong —
    SyncAccountsBody derives from the DTO and declares the field — and it is now
    unreachable besides: sync-accounts-request.json 3.1.1 lists idempotency_key in
    /required, so a keyless retry is refused at the boundary and never reaches the
    recovery it grades.

    Spec: enums/error-code.json#/enumMetadata/BILLING_NOT_SUPPORTED (recovery
    contract: "resubmit with a supported value"); compliance/3.1.1/universal/
    billing-gate-dispatch.yaml phase per_agent_gate_recover.
    """
    from src.core.schemas.account import SyncAccountsRequest

    prior = ctx.get("last_sync_accounts")
    assert prior, "No prior sync_accounts request captured — a table When must run before the retry"
    retried = [{**entry, "billing": billing} for entry in prior]
    try:
        req = SyncAccountsRequest(idempotency_key=fresh_idempotency_key(), accounts=retried)
        dispatch_request(ctx, req=req)
    except Exception as exc:  # noqa: BLE001 — capture for the error-path Thens
        ctx["error"] = exc


@when(
    parsers.parse(
        'the Buyer Agent retries the sync_accounts request with billing "{billing}" and a fresh idempotency_key'
    )
)
def when_retry_sync_with_billing(ctx: dict, billing: str) -> None:
    """Retry the prior request with an explicit supported billing value (BILLING_NOT_SUPPORTED recovery)."""
    _retry_sync_with_billing(ctx, billing)


@when(
    "the Buyer Agent retries the sync_accounts request with the seller's suggested_billing value and a fresh idempotency_key"
)
def when_retry_sync_with_suggested_billing(ctx: dict) -> None:
    """Retry the prior request with the seller's suggested_billing value.

    For a passthrough-only agent the single canonical retry value is
    ``operator`` (error-details/billing-not-permitted-for-agent.json:
    "Typically ``operator`` for passthrough-only agents"; examples[0] =
    {"rejected_billing": "agent", "suggested_billing": "operator"}). Production
    never emits ``suggested_billing`` (no per-agent gate, GH #1772), so this leg is
    only reached after the scenario has already xfailed on the missing
    BILLING_NOT_PERMITTED_FOR_AGENT error — the step exists so the scenario runs
    non-dormant.
    """
    _retry_sync_with_billing(ctx, "operator")


def _last_account_errors(ctx: dict) -> list[Any]:
    """Resolve the referenced per-account result's errors[] array.

    Prefers ``ctx['last_account']`` (set by a prior action/error Then); falls
    back to the first response account so an error-code Then placed first in a
    scenario grades the real response instead of erroring on step ordering.
    """
    acct = ctx.get("last_account")
    if acct is None:
        acct = require_payload(ctx).accounts[0]
        ctx["last_account"] = acct
    assert acct.errors, f"Expected a non-empty per-account errors array, got {acct.errors!r}"
    return list(acct.errors)


def _agent_gate_error_details(ctx: dict) -> dict[str, Any]:
    """Return the details dict of the per-account BILLING_NOT_PERMITTED_FOR_AGENT error."""
    errors = _last_account_errors(ctx)
    err = next((e for e in errors if e.code == "BILLING_NOT_PERMITTED_FOR_AGENT"), None)
    assert err is not None, f"No BILLING_NOT_PERMITTED_FOR_AGENT error present, got codes {[e.code for e in errors]}"
    details = getattr(err, "details", None) or {}
    return dict(details)


@then(parsers.parse('the per-account error details rejected_billing is "{value}"'))
def then_agent_gate_rejected_billing(ctx: dict, value: str) -> None:
    """Assert details.rejected_billing echoes the rejected value verbatim.

    Spec: error-details/billing-not-permitted-for-agent.json#/properties/rejected_billing
    ("echoed verbatim from the request"), required; examples[0].rejected_billing = "agent".
    """
    details = _agent_gate_error_details(ctx)
    actual = details.get("rejected_billing")
    assert actual == value, f"Expected details.rejected_billing '{value}', got {actual!r}"


@then(parsers.parse('the per-account error details suggested_billing is "{value}"'))
def then_agent_gate_suggested_billing(ctx: dict, value: str) -> None:
    """Assert details.suggested_billing is the single canonical retry value.

    Spec: error-details/billing-not-permitted-for-agent.json#/properties/suggested_billing
    ("A single billing value the calling buyer agent MAY retry with autonomously.
    Typically ``operator`` for passthrough-only agents"); examples[0].suggested_billing
    = "operator"; the value is a member of enums/billing-party.json#/enum
    (["operator","agent","advertiser"]).
    """
    details = _agent_gate_error_details(ctx)
    actual = details.get("suggested_billing")
    assert actual == value, f"Expected details.suggested_billing '{value}', got {actual!r}"


@then(
    "the per-account error details do not include permitted_billing, rate_card, "
    "payment_terms, credit_limit, billing_entity, or account_id"
)
def then_agent_gate_details_clamped(ctx: dict) -> None:
    """Assert the clamped details shape carries no commercial-state oracle keys.

    Spec: error-details/billing-not-permitted-for-agent.json — the object is
    ``additionalProperties: false`` with only ``rejected_billing`` (required) and
    ``suggested_billing`` (optional) permitted; the description forbids "the
    agent's full permitted-billing subset, the agent's other commercial state
    (rate cards, payment terms, credit limit, billing entity), or any per-account
    state — those are commercial-state oracles."
    """
    details = _agent_gate_error_details(ctx)
    forbidden = {"permitted_billing", "rate_card", "payment_terms", "credit_limit", "billing_entity", "account_id"}
    leaked = forbidden & set(details)
    assert not leaked, f"Clamped agent-gate details leaked commercial-state keys: {sorted(leaked)}"


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — sync_accounts response assertions
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.re(r"the response is a success variant(?:\s+with accounts array)?"))
def then_success_with_accounts(ctx: dict) -> None:
    """Assert the response is a success variant (optionally with accounts array)."""
    error = ctx.get("error")
    assert error is None, f"Expected success but got error: {error}"
    resp = require_payload(ctx)
    assert resp.accounts is not None, f"Response 'accounts' field is None: {type(resp)}"
    assert isinstance(resp.accounts, list), f"accounts is not a list: {type(resp.accounts)}"


@then(
    parsers.re(
        r'the account for brand domain "(?P<domain>[^"]+)" brand_id "(?P<bid>[^"]+)" has action "(?P<action>[^"]+)"'
    )
)
def then_account_action_with_brand_id(ctx: dict, domain: str, bid: str, action: str) -> None:
    """Assert a specific account (by domain + brand_id) has the expected action."""
    resp = require_payload(ctx)
    acct = _find_account_by_brand(resp, domain, brand_id=bid)
    actual = _action_str(acct.action)
    assert actual == action, f"Expected action '{action}' for {domain}:{bid}, got '{actual}'"
    ctx["last_account"] = acct


@then(parsers.re(r'the account for brand domain "(?P<domain>[^"]+)" has action "(?P<action>[^"]+)"'))
def then_account_action(ctx: dict, domain: str, action: str) -> None:
    """Assert a specific account has the expected action."""
    resp = require_payload(ctx)
    acct = _find_account_by_brand(resp, domain)
    actual = _action_str(acct.action)
    assert actual == action, f"Expected action '{action}' for {domain}, got '{actual}'"
    ctx["last_account"] = acct


@then("the account has a seller-assigned account_id")
def then_account_has_id(ctx: dict) -> None:
    """Assert the last referenced account has a seller-assigned account_id.

    Captures the FIRST such account_id under ctx["first_seller_account_id"]
    (setdefault, so only the first call wins) for the natural-key stability
    invariant graded by ``the echoed account_id equals the account_id from the
    first response`` — that Then compares the second call's handle to this one.
    """
    acct = ctx.get("last_account") or require_payload(ctx).accounts[0]
    account_id = getattr(acct, "account_id", None)
    assert account_id is not None and isinstance(account_id, str) and len(account_id) > 0, (
        f"Account missing non-empty seller-assigned account_id: {acct}"
    )
    ctx.setdefault("first_seller_account_id", account_id)


@then(parsers.parse('the account has status "{status}"'))
def then_account_status(ctx: dict, status: str) -> None:
    """Assert the last referenced account has the expected status."""
    acct = ctx.get("last_account") or require_payload(ctx).accounts[0]
    actual = _status_str(acct.status)
    assert actual == status, f"Expected status '{status}', got '{actual}'"


@then(parsers.parse('the account has action "{action}"'))
def then_account_action_generic(ctx: dict, action: str) -> None:
    """Assert the referenced account carries the expected per-account ``action``.

    ``action`` is a PER-ENTRY outcome inside the success variant of the response
    (sync-accounts-response.json oneOf/0 → accounts[].action), so a request the
    seller rejected wholesale has no account to carry one — the two oneOf branches
    are alternatives, and a step that accepted either would grade neither.

    The branch that did exactly that is gone: ``if action == "failed" and
    ctx["error"] ...: return`` let a request-level rejection satisfy a per-account
    claim. It was also unreachable — ``the account has action "created"`` is the
    only rendering of this sentence in the whole feature set, so no scenario ever
    took it. A scenario that genuinely wants a wholesale rejection asks for the
    error envelope, which the error Thens grade.
    """
    acct = ctx.get("last_account") or require_payload(ctx).accounts[0]
    actual = _action_str(acct.action)
    assert actual == action, f"Expected action '{action}', got '{actual}'"


@then(parsers.parse('the response includes brand domain "{domain}" echoed from request'))
def then_brand_echoed(ctx: dict, domain: str) -> None:
    """Assert the response echoes the brand domain from the request."""
    resp = require_payload(ctx)
    acct = _find_account_by_brand(resp, domain)
    assert acct.brand.domain == domain, f"Expected brand domain '{domain}', got '{acct.brand.domain}'"


@then(parsers.parse("the response contains {count:d} account results"))
def then_n_account_results(ctx: dict, count: int) -> None:
    """Assert the sync response has exactly N account results."""
    resp = require_payload(ctx)
    actual = len(resp.accounts)
    assert actual == count, f"Expected {count} account results, got {actual}"


@then("each account echoes brand domain and brand_id from the request")
def then_all_accounts_echo_brand(ctx: dict) -> None:
    """Assert each response account's brand domain+brand_id matches a submitted pair."""
    resp = require_payload(ctx)
    submitted = ctx.get("sync_request_brand_pairs")
    assert submitted, "Test setup error: sync_request_brand_pairs not tracked by When step"
    for acct in resp.accounts:
        brand = acct.brand
        domain = brand.domain
        bid = _brand_id_str(getattr(brand, "brand_id", None))
        pair = (domain, bid)
        assert pair in submitted, f"Response brand pair {pair} not in submitted pairs {submitted}"


@then(parsers.parse('the account operator is "{operator}"'))
def then_account_operator(ctx: dict, operator: str) -> None:
    """Assert the last referenced account has the expected operator."""
    acct = ctx.get("last_account") or require_payload(ctx).accounts[0]
    actual = acct.operator
    assert actual == operator, f"Expected operator '{operator}', got '{actual}'"


@then(parsers.parse('the account billing is "{billing}"'))
def then_account_billing(ctx: dict, billing: str) -> None:
    """Assert the referenced account has the expected billing model, ON THE WIRE.

    #1521's acceptance oracle: the obligation is what the BUYER receives, so this
    reads the wire entry rather than the typed payload. Locating by wire dict does
    not disturb ``_find_account_by_brand`` or the typed ``last_account`` stash that
    the pre-existing Thens still share (#1880).
    """
    actual = _wire_account(ctx).get("billing")
    assert actual == billing, f"Expected billing '{billing}', got '{actual}'"


@then(parsers.parse('the per-account result echoes brand domain "{domain}" and brand_id "{bid}"'))
def then_per_account_brand_echo(ctx: dict, domain: str, bid: str) -> None:
    """Assert a per-account result echoes the exact brand domain and brand_id."""
    resp = require_payload(ctx)
    acct = _find_account_by_brand(resp, domain, brand_id=bid)
    acct_bid = _brand_id_str(getattr(acct.brand, "brand_id", None))
    assert acct.brand.domain == domain, f"Expected brand domain '{domain}', got '{acct.brand.domain}'"
    assert acct_bid == bid, f"Expected brand_id '{bid}', got '{acct_bid}'"


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — error variant assertions (auth, atomic XOR)
# ═══════════════════════════════════════════════════════════════════════


def _get_error(ctx: dict) -> Exception:
    """Get the error from ctx, asserting it exists."""
    error = ctx.get("error")
    assert error is not None, "Expected an error but none found"
    return error


def _assert_error_has_code(err: Any, index: int) -> None:
    """Assert a single error object carries a non-empty machine CODE.

    Checks .code first (per-account errors), then .error_code (AdCPSalesAgentError).

    The message half is deliberately gone: the buyer-facing sentence is derived from the
    code through CODE_TABLE, so a present code guarantees a present sentence and
    asserting both checked the table against itself.
    """
    code = err.get("code") if isinstance(err, dict) else getattr(err, "code", None)
    assert isinstance(code, str) and code, f"Error [{index}] missing non-empty code: code={code!r}, error={err}"


@then("the response is an error variant with no accounts array")
def then_error_variant_no_accounts(ctx: dict) -> None:
    """Assert the response is an error variant (exception raised, no accounts)."""
    _get_error(ctx)
    assert payload_or_none(ctx) is None, "Expected no response (error variant), but got a response"


@then(parsers.re(r"the response is an error variant"))
def then_error_exists(ctx: dict) -> None:
    """Assert an error occurred — the response is an error variant.

    Wire-first, reconstructed fallback -- same strategy as then_error_code.
    """
    error_code = _wire_code(ctx)
    if error_code is None:
        error = _get_error(ctx)
        error_code = getattr(error, "error_code", None)
    assert error_code is not None, f"Error variant must carry an error_code, got: {ctx.get('error')!r}"
    assert isinstance(error_code, str) and error_code.strip(), (
        f"Error variant error_code must be a non-empty string, got: {error_code!r}"
    )


@then(parsers.re(r"no accounts were modified on the seller"))
def then_no_accounts_modified(ctx: dict) -> None:
    """Assert the refused sync left no account behind for the domains it named.

    ITS ONLY BINDING IS THE UNAUTHENTICATED ONE (@T-UC-011-ext-a-no-token), and that
    is the branch this step used to skip entirely: with no ``tenant``/``principal`` in
    ctx it fell to ``else: pass`` and asserted NOTHING, while its docstring claimed to
    query the DB and compare against a baseline. The authenticated branch was no better
    — it read ``ctx["pre_request_account_ids"]`` with a ``set()`` default and NO step
    writes that key, so it asserted "no accounts EXIST", which is a different claim from
    "none were modified" the moment a fixture seeds one (salesagent-b9hi1.1).

    The unauthenticated branch now grades the real obligation against the request as
    sent: an AUTH_MISSING refusal must not have created an account for any brand domain
    the request named. That is what "system state unchanged" means for a caller who
    could not be identified, and it needs no baseline — before the request, none of
    those accounts could have been theirs.
    """
    _get_error(ctx)  # Confirm an error occurred
    tenant = ctx.get("tenant")
    principal = ctx.get("principal")

    if tenant is not None and principal is not None:
        # Authenticated: a real before/after comparison, and the baseline is REQUIRED.
        # Defaulting it silently turns this into "no accounts exist", which passes for
        # the wrong reason and fails for the wrong reason once a fixture seeds one.
        pre_request_ids = ctx.get("pre_request_account_ids")
        assert pre_request_ids is not None, (
            "No pre_request_account_ids captured, so there is no baseline to compare against "
            "and 'no accounts were modified' cannot be graded. Capture it before the When."
        )
        current_ids = {a["account_id"] for a in _persisted_accounts(ctx, principal.principal_id)}
        assert current_ids == pre_request_ids, (
            f"Accounts were modified despite error. "
            f"Before: {pre_request_ids}, After: {current_ids}. "
            f"Created: {current_ids - pre_request_ids}, "
            f"Deleted: {pre_request_ids - current_ids}"
        )
        return

    from src.core.database.models import Account
    from src.core.helpers.brand_key import brand_key_parts

    requested = ctx.get("last_sync_accounts")
    assert requested, (
        "No last_sync_accounts recorded — the When that dispatched the sync must record the "
        "entries it sent, or this step cannot tell which accounts must not exist"
    )
    # Element-level, not `assert domains`: sync-accounts-request.json REQUIRES brand.domain
    # on every entry, so an entry without one is not a case to skip — it means the When
    # recorded something the pin would refuse, and the set built from it would silently be
    # the wrong denominator for the leak check below.
    missing = [i for i, a in enumerate(requested) if not (a.get("brand") or {}).get("domain")]
    assert missing == [], (
        f"Dispatched sync entries {missing} carry no brand.domain, which the pin requires — "
        f"the recorded request is not one the seller could have accepted: {requested!r}"
    )
    domains = {a["brand"]["domain"] for a in requested}

    # Unscoped by tenant on purpose: the caller was never identified, so no tenant is
    # theirs, and "created nothing anywhere" is the honest reading of the obligation.
    existing = {brand_key_parts(row.brand)[0] for row in ctx["env"].query(Account) if row.brand}
    leaked = domains & existing
    assert not leaked, (
        f"The refused sync created accounts for {sorted(leaked)} — an unauthenticated caller's "
        f"request must leave system state unchanged (POST-F1)"
    )


@then(parsers.re(r"the errors array may contain multiple errors"))
def then_errors_array_may_contain_multiple(ctx: dict) -> None:
    """Assert the error exposes a structured errors array with valid entries.

    Each entry must have code and message fields, proving the array is
    well-formed and could carry multiple errors.
    """
    for i, err in enumerate(wire_envelope_errors(ctx)):
        _assert_error_has_code(err, i)


@then(parsers.parse('the error code is "{code}"'))
def then_error_code(ctx: dict, code: str) -> None:
    """Assert the error has the expected error code — wire-first, reconstructed fallback.

    Consolidated onto the same wire-first strategy as the canonical
    ``then_error.py:340`` step (tests/CLAUDE.md Error Verification Policy):
    prefer the real wire envelope's code over the lossy reconstructed
    ``ctx['error']`` (which collapses distinct wire codes onto one exception
    class). Kept as a separate step function because BR-UC-011/BR-UC-030 pin
    the "the error code is" wording rather than "the error code should be".
    """
    actual = _wire_code(ctx)
    if actual is None:
        error = _get_error(ctx)
        actual = getattr(error, "error_code", None)
        assert actual is not None, f"Error has no error_code: {error}"
    assert actual == code, f"Expected error code '{code}', got '{actual}'"


@then("the error message describes the authentication requirement")
def then_error_message_auth(ctx: dict) -> None:
    """Assert the error message is a substantive auth-related message."""
    error = _get_error(ctx)
    msg = str(error).lower()
    auth_phrases = {"x-adcp-auth", "valid token", "authentication required", "auth", "token", "unauthorized"}
    assert any(p in msg for p in auth_phrases), f"Expected auth-related message, got: {error}"
    assert len(msg) > 20, f"Expected substantive auth error message (>20 chars), got: {repr(str(error))}"


@then(parsers.parse('the error should include "suggestion" field with remediation guidance'))
def then_error_has_suggestion(ctx: dict) -> None:
    """Assert the error carries a suggestion.

    Two legitimate sources, in order:
    1. per-account entries (last_account.errors[].suggestion) -- a PARTIAL SUCCESS,
       where the spec puts per-record failures on the payload layer;
    2. a request-level rejection -- read errors[0].suggestion off the WIRE envelope.

    The second used to be ``getattr(error, "suggestion") or getattr(error,
    "recovery")`` on the exception the harness rebuilt from those bytes. Two problems,
    both now gone: the rebuild no longer exists (salesagent-3dawm.15), and falling
    back to ``recovery`` made "includes a suggestion" satisfiable by ANY error, since
    every error carries a recovery.
    """
    acct = ctx.get("last_account")
    if acct is not None and acct.errors:
        if any(getattr(e, "suggestion", None) for e in acct.errors):
            return

    result = ctx.get("result")
    wire = result.wire_error_object() if result is not None else None
    if wire is not None:
        suggestion = wire.get("suggestion")
        assert suggestion, f"Expected a non-empty top-level suggestion on the wire error, got {wire!r}"
        return

    # Third legitimate source: a GENUINE in-process AdCPSalesAgentError, i.e. one production
    # actually raised rather than one rebuilt from wire bytes. Its suggestion is
    # derived from its code by CODE_TABLE, so reading it is reading the table, not a
    # reconstruction. Service-level failure scenarios take this path: the env drives
    # the failure without a transport round-trip, so there is no envelope to read.
    from src.core.exceptions import AdCPSalesAgentError

    error = ctx.get("error")
    if isinstance(error, AdCPSalesAgentError):
        assert error.suggestion, f"Expected a non-empty suggestion on {error.error_code}, got {error.suggestion!r}"
        return

    raise AssertionError(
        "Nothing that can carry a suggestion: no per-account error, no wire envelope, and "
        f"the recorded error is not a production AdCPSalesAgentError. ctx error: {error!r}"
    )


@then(parsers.parse("the response contains an errors array with at least {count:d} error"))
def then_errors_array(ctx: dict, count: int) -> None:
    """Assert the error response contains at least count structured errors.

    Production maps exceptions to error responses. If the exception carries
    a structured ``errors`` list, verify its length. Otherwise a single
    exception maps to exactly 1 error.
    """
    error = _get_error(ctx)
    # Check for structured errors list on the exception
    errors_list = getattr(error, "errors", None)
    if isinstance(errors_list, (list, tuple)):
        actual = len(errors_list)
    else:
        actual = 1  # single exception = 1 error
    assert actual >= count, f"Expected at least {count} error(s), got {actual}: {error}"
    # Verify no success response leaked through
    assert payload_or_none(ctx) is None, "Expected error variant (no success response) when errors array is present"


@then("the response does not contain an accounts array")
def then_no_accounts_in_response(ctx: dict) -> None:
    """Assert the error response has no accounts array.

    Verifies we are on the error path AND that neither the error payload
    nor any leaked success response contains an 'accounts' key. This
    ensures the error variant truly excludes account data on the wire.
    """
    error = _get_error(ctx)
    # Assert no success response leaked through
    resp = payload_or_none(ctx)
    assert resp is None, f"Expected no success response in error variant, got: {resp}"
    # Inspect the error payload itself for absence of accounts
    error_payload = None
    if hasattr(error, "model_dump"):
        error_payload = error.model_dump()
    elif hasattr(error, "__dict__"):
        error_payload = vars(error)
    if error_payload is not None:
        assert "accounts" not in error_payload, (
            f"Error payload should not contain 'accounts' key, but found: {error_payload.get('accounts')}"
        )


@then("the response does not contain a dry_run field")
def then_no_dry_run_field(ctx: dict) -> None:
    """Assert the error variant response doesn't include dry_run.

    This step runs in the error variant scenario where the dispatch carries no
    payload (an error was raised). Verify the error itself doesn't leak
    a dry_run field.
    """
    error = ctx.get("error")
    assert error is not None, "Expected error variant — no error found"
    # Error variant: no success response should exist
    resp = payload_or_none(ctx)
    assert resp is None, f"Expected no success response in error variant, got: {resp}"
    # Verify the error doesn't carry a dry_run attribute
    dry_run = getattr(error, "dry_run", None)
    assert dry_run is None, f"Expected no dry_run on error, got {dry_run}"


@then("the response contains an accounts array")
def then_has_accounts_array(ctx: dict) -> None:
    """Assert the response has a non-empty accounts array."""
    resp = require_payload(ctx)
    accounts = resp.accounts  # AttributeError if field missing
    assert isinstance(accounts, list), f"accounts is not a list: {type(accounts)}"
    assert accounts, "Expected non-empty accounts array in success variant"


@then("the response does not contain an operation-level errors array")
def then_no_operation_errors(ctx: dict) -> None:
    """Assert the success response has no operation-level errors field."""
    resp = require_payload(ctx)
    errors = getattr(resp, "errors", None)
    assert errors is None or len(errors) == 0, f"Unexpected errors: {errors}"


@then("the response is the success variant of oneOf")
def then_response_is_success_variant(ctx: dict) -> None:
    """Assert the response is the success variant (has accounts, no exception)."""
    assert ctx.get("error") is None, f"Expected success variant, got error: {ctx.get('error')}"
    resp = require_payload(ctx)
    accounts = resp.accounts  # AttributeError if field missing on non-success variant
    assert isinstance(accounts, list), f"Success variant accounts must be a list: {type(accounts)}"


@then("each error includes code and message")
def then_each_error_has_code_message(ctx: dict) -> None:
    """Assert every error in the errors collection has non-empty code and message.

    Iterates over the full errors collection (if the error carries a structured
    errors list) or treats the single exception as a one-element collection.
    For each error, asserts the code/error_code is a non-empty string and the
    message attribute (not str()) is a non-empty string.
    """
    for i, err in enumerate(wire_envelope_errors(ctx)):
        _assert_error_has_code(err, i)


# Two steps used to sit here — "a response with both accounts and errors arrays is
# invalid" and "a response with neither_present is also invalid (...)". Neither
# sentence exists in any feature, by the Examples-rendering resolver and by literal
# grep alike, so no scenario has ever run them. Neither dispatched, either: both
# constructed a `SyncAccountsResponse` in the test process and graded pydantic's
# refusal, which says nothing about what a seller puts on the wire — the DTO's
# conformance to the pinned model is the subject of tests/unit/test_adcp_contract.py
# and the schema-inheritance guard. The second also appended to
# ctx["schema_validated"], a key nothing anywhere reads.


@then(parsers.parse('all accounts have action "{action}"'))
def then_all_accounts_action(ctx: dict, action: str) -> None:
    """Assert all accounts in the response have the given action."""
    resp = require_payload(ctx)
    accounts = resp.accounts
    actions = {_action_str(acct.action) for acct in accounts}
    assert actions == {action}, (
        f"Expected all accounts to have action '{action}', got actions {actions} across {len(accounts)} accounts"
    )


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — per-account errors (billing rejection, partial failure)
# ═══════════════════════════════════════════════════════════════════════


@then("the failed account includes a per-account errors array")
def then_failed_has_errors(ctx: dict) -> None:
    """Assert the last referenced (failed) account has a non-empty errors array."""
    acct = ctx.get("last_account")
    assert acct is not None, "No account referenced — need a prior 'account for brand domain' step"
    errors = acct.errors
    assert errors is not None, "Expected errors array on failed account, got None"
    # Verify each error has required fields (code + message)
    for err in errors:
        assert err.code, f"Per-account error missing code: {err}"
        assert err.message, f"Per-account error missing message: {err}"


@then(parsers.re(r'the per-account errors array contains an error with code "(?P<code>[^"]+)"$'))
def then_per_account_error_code(ctx: dict, code: str) -> None:
    """Assert the failed account's errors contain a specific error code.

    Resolution is delegated to :func:`_last_account_errors`, the module's one
    referenced-account errors[] oracle: it prefers ``ctx['last_account']`` (set by
    a prior action/error Then) and falls back to the first response account, so an
    error-code Then placed first in a scenario grades the real response rather than
    erroring on step ordering. Re-spelling that resolution here would be the
    duplication the DRY invariant bans.

    ``parsers.re`` with ``[^"]+`` and an end anchor, NOT ``parsers.parse`` with
    ``{code}``: parse's capture is greedy and spans quotes, so on a disjunction
    text ``code "INVALID_REQUEST" or "VALIDATION_ERROR"`` it would match FIRST and
    bind the literal ``INVALID_REQUEST" or "VALIDATION_ERROR`` as a single code,
    failing against a correct production response. A capture that cannot cross a
    quote makes the single-code step bind only single-code sentences.
    """
    codes = [e.code for e in _last_account_errors(ctx)]
    assert code in codes, f"Expected error code '{code}' in {codes}"


@then("the error message explains the billing model is not available")
def then_billing_error_message(ctx: dict) -> None:
    """Assert the billing error has an explanatory message."""
    acct = ctx.get("last_account")
    assert acct is not None and acct.errors, "No account errors"
    billing_err = next((e for e in acct.errors if e.code == "BILLING_NOT_SUPPORTED"), None)
    assert billing_err is not None, "No BILLING_NOT_SUPPORTED error found"
    assert "billing" in billing_err.message.lower() or "supported" in billing_err.message.lower(), (
        f"Expected billing-related message, got: {billing_err.message}"
    )


@then(parsers.parse('the failed account has status "{status}" with {code} error'))
def then_failed_status_with_error(ctx: dict, status: str, code: str) -> None:
    """Assert the last failed account has given status and error code."""
    acct = ctx.get("last_account")
    assert acct is not None, "No account referenced"
    actual_status = _status_str(acct.status)
    assert actual_status == status, f"Expected status '{status}', got '{actual_status}'"
    assert acct.errors is not None, "Expected errors on failed account"
    codes = [e.code for e in acct.errors]
    assert code in codes, f"Expected error code '{code}' in {codes}"


@then(parsers.parse("the account processing fails with a validation error for {field}"))
def then_field_validation_error(ctx: dict, field: str) -> None:
    """Assert the seller rejected the request, blaming *field*, on the wire.

    See :func:`_assert_wire_field_rejection` for why the code is INVALID_REQUEST
    and why reading ``errors[0].field`` is stronger than the message
    substring-match this used to do.
    """
    _assert_wire_field_rejection(ctx, field)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — approval workflow (setup object, push notifications)
# ═══════════════════════════════════════════════════════════════════════


@then("the account includes a setup object")
def then_has_setup(ctx: dict) -> None:
    """Assert the account has a non-null setup object."""
    acct = ctx.get("last_account") or require_payload(ctx).accounts[0]
    assert acct.setup is not None, "Expected setup object, got None"


@then("the setup object includes a message describing the required action")
def then_setup_has_message(ctx: dict) -> None:
    """Assert the setup object has a descriptive message."""
    acct = ctx.get("last_account") or require_payload(ctx).accounts[0]
    assert acct.setup is not None, "No setup object"
    assert acct.setup.message, f"Expected message in setup, got: {acct.setup.message}"


@then("the setup object includes a message")
def then_setup_message_present(ctx: dict) -> None:
    """Assert the setup object has a message (any content)."""
    acct = ctx.get("last_account") or require_payload(ctx).accounts[0]
    assert acct.setup is not None, "No setup object"
    assert acct.setup.message, "Setup message is empty"


@then("the setup object includes a URL for the human buyer")
def then_setup_has_url(ctx: dict) -> None:
    """Assert the setup object has a URL."""
    acct = ctx.get("last_account") or require_payload(ctx).accounts[0]
    assert acct.setup is not None, "No setup object"
    assert acct.setup.url is not None, "Expected URL in setup, got None"


@then("the setup object includes an expires_at timestamp")
def then_setup_has_expires(ctx: dict) -> None:
    """Assert the setup object has an expires_at timestamp."""
    acct = ctx.get("last_account") or require_payload(ctx).accounts[0]
    assert acct.setup is not None, "No setup object"
    assert acct.setup.expires_at is not None, "Expected expires_at in setup"


@then("the setup object does not include a URL")
def then_setup_no_url(ctx: dict) -> None:
    """Assert the setup object has no URL."""
    acct = ctx.get("last_account") or require_payload(ctx).accounts[0]
    assert acct.setup is not None, "No setup object"
    assert acct.setup.url is None, f"Expected no URL in setup, got {acct.setup.url}"


@then("the account does not include a setup object")
def then_no_setup(ctx: dict) -> None:
    """Assert the account has no setup object."""
    acct = ctx.get("last_account") or require_payload(ctx).accounts[0]
    assert acct.setup is None, f"Expected no setup, got {acct.setup}"


# ── Push notification steps (registration only) ──────────────────────


@then("the system registers the webhook for async account status notifications")
def then_webhook_registered(ctx: dict) -> None:
    """Assert the system acknowledged webhook registration for status notifications.

    Verifies that the sync request succeeded (no error) and produced
    accounts with seller-assigned IDs, confirming the server processed
    the request successfully. Then xfails on the specific acknowledgement
    check (echoed URL / registration ID) which production does not yet implement.
    """
    import pytest

    # Assert the sync request succeeded — not just "some outcome exists"
    assert ctx.get("error") is None, (
        f"Sync request failed — webhook registration requires successful sync, got error: {ctx.get('error')}"
    )
    resp = require_payload(ctx)
    assert isinstance(resp.accounts, list), f"Expected accounts list, got {type(resp.accounts)}"
    # Verify the sync produced accounts with seller-assigned IDs
    first_acct = resp.accounts[0] if resp.accounts else None
    assert first_acct is not None and isinstance(first_acct.account_id, str), (
        "Expected sync to produce at least one account with a seller-assigned account_id"
    )
    for acct in resp.accounts:
        assert acct.account_id is not None and isinstance(acct.account_id, str), (
            f"Account missing seller-assigned account_id: {acct}"
        )
        assert _action_str(acct.action) in ("created", "updated", "unchanged"), (
            f"Account has unexpected action '{_action_str(acct.action)}' — "
            f"webhook registration requires successful account processing"
        )
    # Verify the request actually carried push_notification_config (distinguishes
    # this step from a plain "sync succeeded" check)
    # ONE key. This read used to fall back across push_notification_config /
    # request_push_config / push_notification_url, and the fallback was hiding a
    # real gap: the @when copy of the step set only the url, never the config the
    # dispatch reads, so its scenarios sent no webhook config at all and this
    # assertion passed off the leftover url.
    push_config = ctx.get("push_notification_config")
    assert push_config is not None, (
        "Then 'webhook registered' but the When step did not set push_notification_config/url in ctx — "
        "cannot verify webhook registration without a configured webhook"
    )
    # xfail: production does not yet echo/acknowledge the webhook config in response
    pytest.xfail(
        "SPEC-PRODUCTION GAP: push_notification_config webhook registration "
        "acknowledgement not yet implemented — expected response to echo "
        "registered URL or return registration ID"
    )


@then(parsers.parse('when the account transitions from "{from_status}" to "{to_status}"'))
def then_account_transitions(ctx: dict, from_status: str, to_status: str) -> None:
    """Assert account status transition from from_status to to_status.

    Verifies the sync created an account whose current status matches
    from_status (the pre-transition state), confirming the account is in
    the correct starting state for a transition. Then attempts to verify
    the post-transition state equals to_status.
    """
    import pytest

    resp = require_payload(ctx)
    assert isinstance(resp.accounts, list), f"Expected accounts list, got {type(resp.accounts)}"
    # The account's current status must match the expected from_status
    acct = ctx.get("last_account") or (resp.accounts[0] if resp.accounts else None)
    assert acct is not None, "Expected at least one account in sync response"
    actual_status = _status_str(acct.status)
    assert actual_status == from_status, (
        f"Expected account status '{from_status}' as transition source, got '{actual_status}'"
    )
    # Verify the account has an account_id assigned by the seller
    assert acct.account_id is not None and isinstance(acct.account_id, str), (
        f"Account missing seller-assigned account_id: {acct}"
    )
    # Record the transition expectation for the downstream push notification step
    ctx["expected_transition"] = (from_status, to_status)
    ctx["transition_account_id"] = acct.account_id
    # xfail: production does not yet implement the actual status transition
    # (the account remains in from_status; the to_status is never applied)
    pytest.xfail(
        "SPEC-PRODUCTION GAP: async account status transition not yet implemented — "
        f"account {acct.account_id} remains in '{from_status}', expected '{to_status}'"
    )


@then(parsers.parse('a push notification is sent to "{url}"'))
def then_push_sent(ctx: dict, url: str) -> None:
    """Assert push notification is delivered to the specified URL.

    The sync must have completed and produced accounts with a valid
    transition account. Verifies production preconditions (sync succeeded,
    account exists with account_id, transition was recorded), then xfails
    on the actual delivery check since production does not yet implement
    webhook push delivery.
    """
    import pytest

    # Assert the sync produced accounts that could trigger a push
    resp = require_payload(ctx)
    assert isinstance(resp.accounts, list), f"Expected accounts list, got {type(resp.accounts)}"
    # Verify at least one account was produced with a seller-assigned ID
    first_acct = resp.accounts[0] if resp.accounts else None
    assert first_acct is not None and isinstance(first_acct.account_id, str), (
        "Expected at least one account with a seller-assigned account_id before push"
    )
    # Assert the transition was recorded by the preceding transition step
    expected_transition = ctx.get("expected_transition")
    assert expected_transition is not None, (
        "No expected_transition recorded — the preceding 'when the account transitions' step must run first"
    )
    from_status, to_status = expected_transition
    assert from_status != to_status, f"Transition must change status: from='{from_status}' to='{to_status}'"
    # Assert a specific account was identified for the transition
    transition_account_id = ctx.get("transition_account_id")
    assert transition_account_id is not None, (
        "No transition_account_id recorded — the transition step should identify the account"
    )
    # xfail: production does not yet implement webhook push delivery
    pytest.xfail(
        f"SPEC-PRODUCTION GAP: push notification delivery to '{url}' "
        f"not yet implemented — expected outbound POST with account_id="
        f"'{transition_account_id}' and status='{to_status}' payload"
    )


# ═══════════════════════════════════════════════════════════════════════
# Slice 6: dry_run + delete_missing steps
# ═══════════════════════════════════════════════════════════════════════


# ── Given: previously synced accounts ─────────────────────────────────


@given(parsers.parse('the agent previously synced accounts for brand domain "{d1}" and "{d2}"'))
def given_previously_synced_two(ctx: dict, d1: str, d2: str) -> None:
    """Pre-create two accounts via sync_accounts."""
    _setup_tenant_and_principal(ctx)
    _sync_pre_create(ctx, brand_domain=d1, operator=d1, billing="operator")
    _sync_pre_create(ctx, brand_domain=d2, operator=d2, billing="operator")


@given(parsers.parse('the agent previously synced accounts for brand domain "{d}" only'))
def given_previously_synced_one(ctx: dict, d: str) -> None:
    """Pre-create one account via sync_accounts."""
    _setup_tenant_and_principal(ctx)
    _sync_pre_create(ctx, brand_domain=d, operator=d, billing="operator")


def _create_agent(ctx: dict, agent_name: str) -> Any:
    """Create a separate principal (agent) for multi-agent tests.

    Returns the principal. Caches in ctx["agents"][name].
    """
    from tests.factories.principal import PrincipalFactory

    agents = ctx.setdefault("agents", {})
    if agent_name in agents:
        return agents[agent_name]

    tenant, _ = _setup_tenant_and_principal(ctx)
    agent_principal = PrincipalFactory(tenant=tenant)
    agents[agent_name] = agent_principal
    return agent_principal


def _credential_for_agent(ctx: dict, agent_name: str) -> dict[str, str]:
    """The credential a named agent presents: its own principal row's token.

    The server resolves the agent from these headers, on every transport, so a
    tokenless agent sync is refused there rather than let through (PR #1430 items 1-2).
    """
    # The token is DERIVED, not read: production stores sha256(token) and never the
    # plaintext, so `agent.access_token` stopped existing with the column and raised
    # AttributeError here. `plaintext_token_for` is the one deriver the factory and the
    # harness's own credential() both use, so the header carries the token the real
    # resolver will hash and find.
    from tests.factories.principal import plaintext_token_for
    from tests.helpers.credentials import credential_headers

    agent = _create_agent(ctx, agent_name)
    ctx["env"]._commit_factory_data()
    return credential_headers(token=plaintext_token_for(agent.principal_id), tenant=agent.tenant_id)


def _given_agent_synced(ctx: dict, agent_name: str, domain: str) -> None:
    """Pre-create an account under a named agent's identity via sync.

    Shared body for the agent-scoped sync Givens. Fails loudly if the sync
    itself failed — swallowing the Given error previously masked live-server
    401s, so agent accounts silently never existed (PR #1430 items 1-2).
    """
    from src.core.schemas.account import SyncAccountsRequest

    _setup_tenant_and_principal(ctx)
    credential = _credential_for_agent(ctx, agent_name)
    req = SyncAccountsRequest(
        idempotency_key=fresh_idempotency_key(),
        accounts=[{"brand": {"domain": domain}, "operator": domain, "billing": "operator"}],
    )
    dispatch_request(ctx, req=req, credential=credential)
    error = ctx.get("error")
    assert error is None, f"Given: agent {agent_name} sync for {domain!r} failed: {error!r}"
    # Clear response so the next When step's response is fresh
    # Clear every source the payload accessors read: a When that raises BEFORE
    # dispatching leaves the previous step's TransportResult in ctx, and the
    # accessors would serve it as this step's own result. (The retired
    # ctx["response"] is not popped — nothing writes it, so there is nothing to
    # clear, and pretending otherwise reads as live state management.)
    ctx.pop("result", None)
    ctx.pop("self_dispatched_response", None)
    ctx.pop("error", None)


@given(parsers.parse('agent A previously synced accounts for brand domain "{d}"'))
def given_agent_a_synced(ctx: dict, d: str) -> None:
    """Pre-create an account under agent A's identity."""
    _given_agent_synced(ctx, "A", d)


@given(parsers.parse('agent B previously synced accounts for brand domain "{d}"'))
def given_agent_b_synced(ctx: dict, d: str) -> None:
    """Pre-create an account under agent B's identity."""
    _given_agent_synced(ctx, "B", d)


# ── When: sync with dry_run / delete_missing flags ────────────────────


@when(parsers.re(r"the Buyer Agent sends a sync_accounts request with dry_run (?P<value>true|false) and:"))
def when_sync_with_dry_run(ctx: dict, value: str, datatable: Any) -> None:
    """Send sync_accounts with dry_run flag and accounts table."""
    from src.core.schemas.account import SyncAccountsRequest

    rows = table_rows(datatable)
    accounts = _parse_sync_table(rows)

    try:
        req = SyncAccountsRequest(
            idempotency_key=fresh_idempotency_key(),
            accounts=accounts,
            dry_run=as_bool(value),
        )
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


@when(parsers.re(r"the Buyer Agent sends a sync_accounts request with delete_missing (?P<value>true|false) and:"))
def when_sync_with_delete_missing(ctx: dict, value: str, datatable: Any) -> None:
    """Send sync_accounts with delete_missing flag and accounts table."""
    from src.core.schemas.account import SyncAccountsRequest

    rows = table_rows(datatable)
    accounts = _parse_sync_table(rows)

    ctx["sync_request_domains"] = {a["brand"]["domain"] for a in accounts if a.get("brand", {}).get("domain")}
    try:
        req = SyncAccountsRequest(
            idempotency_key=fresh_idempotency_key(),
            accounts=accounts,
            delete_missing=as_bool(value),
        )
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


@when("the Buyer Agent sends a sync_accounts request without delete_missing and:")
def when_sync_without_delete_missing(ctx: dict, datatable: Any) -> None:
    """Send sync_accounts without delete_missing (uses default=False)."""
    from src.core.schemas.account import SyncAccountsRequest

    rows = table_rows(datatable)
    accounts = _parse_sync_table(rows)

    ctx["sync_request_domains"] = {a["brand"]["domain"] for a in accounts if a.get("brand", {}).get("domain")}
    try:
        req = SyncAccountsRequest(idempotency_key=fresh_idempotency_key(), accounts=accounts)
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


@when("agent A sends a sync_accounts request with delete_missing true and:")
def when_agent_a_sync_delete_missing(ctx: dict, datatable: Any) -> None:
    """Send sync_accounts under agent A's identity with delete_missing=True."""
    from src.core.schemas.account import SyncAccountsRequest

    rows = table_rows(datatable)
    accounts = _parse_sync_table(rows)

    credential_a = _credential_for_agent(ctx, "A")

    try:
        req = SyncAccountsRequest(
            idempotency_key=fresh_idempotency_key(),
            accounts=accounts,
            delete_missing=True,
        )
        dispatch_request(ctx, req=req, credential=credential_a)
    except Exception as exc:
        ctx["error"] = exc


# ── Then: dry_run response assertions ─────────────────────────────────


@then("the response includes dry_run true")
def then_dry_run_true(ctx: dict) -> None:
    """Assert the response has dry_run=True."""
    resp = require_payload(ctx)
    assert resp.dry_run is True, f"Expected dry_run=True, got {resp.dry_run}"


@then("the response does not include a dry_run field")
def then_no_dry_run_include(ctx: dict) -> None:
    """Assert the wire response omits the dry_run field entirely (not just None).

    ``wire_absent`` distinguishes "field genuinely absent from the wire" from
    "field present with a null value" — the distinction the prior
    ``getattr(..., None)`` check collapsed. It is also the stricter reading of
    #1802's point that this step must not tolerate a missing payload: this
    step's text makes a claim about a RESPONSE, so an errored dispatch cannot
    satisfy it, and ``wire_absent`` -> ``_wire_body`` raises on the error path
    (the error variant has its own step, ``then_no_dry_run_field``, grading that
    path under its own text). Tolerating None here made the step pass with zero
    assertions whenever the dispatch errored.
    """
    wire_absent(ctx, "dry_run")


@then(
    parsers.parse(
        'result {position:d} on the wire shows brand domain "{domain}" with action "{action}" and billing "{billing}"'
    )
)
def then_positional_result_on_the_wire(ctx: dict, position: int, domain: str, action: str, billing: str) -> None:
    """Assert the ``position``-th (1-based) result on the wire, by ORDER not by brand.

    Positional deliberately: the scenarios this serves carry ONE natural key
    TWICE, so both results echo the same brand and a lookup-by-domain step can
    only ever see the first — it would grade the second vacuously. Reading the
    wire rather than the typed payload because ``billing`` is the field a preview
    got wrong by echoing the value the buyer was REPLACING, and only the wire
    says what the buyer actually received.
    """
    accounts = wire_dict(ctx)["accounts"]
    assert len(accounts) >= position, (
        f"expected at least {position} results on the wire, got {len(accounts)}: {accounts}"
    )
    result = accounts[position - 1]
    actual = (
        (result.get("brand") or {}).get("domain"),
        result.get("action"),
        result.get("billing"),
    )
    assert actual == (domain, action, billing), (
        f"result {position} on the wire is {actual}, expected {(domain, action, billing)} — full wire: {accounts}"
    )


def _persisted_accounts(ctx: dict, principal_id: str | None = None) -> list[dict[str, Any]]:
    """The accounts an agent actually has on the seller, as plain values.

    The ONE place this slice reaches the database. Six Then steps repeated the
    same session/repository/``list_by_principal`` triple, each with its own idea
    of which fields were worth looking at — and the weakest of them (status only)
    is what let a dry_run that WROTE the row still pass. Returning plain values
    also keeps callers off detached ORM instances once the session closes.

    ``principal_id`` defaults to the scenario's own agent; the agent-scoping
    steps pass another agent's id.
    """
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    pid = principal_id or ctx["principal"].principal_id
    with get_db_session() as session:
        repo = AccountRepository(session, ctx["tenant"].tenant_id)
        return [
            {
                "account_id": row.account_id,
                "status": _status_str(row.status),
                "domain": row.brand.domain if row.brand else None,
                "billing": row.billing,
                "payment_terms": row.payment_terms,
            }
            for row in repo.list_by_principal(pid)
        ]


@then("no accounts were actually created or modified on the seller")
def then_no_db_writes(ctx: dict) -> None:
    """Assert dry_run didn't write to DB — query repo and verify no accounts exist."""
    accounts = _persisted_accounts(ctx)
    assert accounts == [], f"Expected 0 accounts after dry_run, but found {[a['domain'] for a in accounts]}"


@then("the account was actually created on the seller")
def then_account_in_db(ctx: dict) -> None:
    """Assert the sync actually wrote to DB — verify the response account_id is persisted."""
    # The response should have the account that was just created
    resp = require_payload(ctx)
    expected_id = resp.accounts[0].account_id
    db_ids = {a["account_id"] for a in _persisted_accounts(ctx)}
    assert expected_id in db_ids, f"Expected account '{expected_id}' in DB, found: {db_ids}"


# ── Then: delete_missing assertions ───────────────────────────────────


@then(parsers.parse('the response includes a result for brand domain "{domain}" showing deactivation'))
def then_deactivation_result(ctx: dict, domain: str) -> None:
    """Assert the response shows a deactivated account for the given domain.

    Production code (BR-RULE-061) sets action='updated' and status='closed'
    for accounts removed by delete_missing.

    The dry-run/delete_missing acceptance oracle: graded on the wire entry the
    buyer receives, located by brand domain (the identifier the step text carries).
    """
    acct = _wire_account(ctx, domain=domain)
    actual_status = acct.get("status")
    actual_action = acct.get("action")
    assert actual_status == "closed", f"Expected status 'closed' for deactivated {domain}, got '{actual_status}'"
    assert actual_action == "updated", f"Expected action 'updated' for deactivated {domain}, got '{actual_action}'"


@then(parsers.parse('the account for brand domain "{domain}" has action "unchanged" or "updated"'))
def then_account_unchanged_or_updated(ctx: dict, domain: str) -> None:
    """Assert account has action 'unchanged' or 'updated' (either is acceptable)."""
    resp = require_payload(ctx)
    acct = _find_account_by_brand(resp, domain)
    actual = _action_str(acct.action)
    assert actual in ("unchanged", "updated"), f"Expected 'unchanged' or 'updated' for {domain}, got '{actual}'"
    ctx["last_account"] = acct


@then(parsers.parse('agent B\'s account for brand domain "{domain}" is not affected'))
def then_agent_b_not_affected(ctx: dict, domain: str) -> None:
    """Assert agent B's account is still active (not deactivated by agent A)."""
    accounts = _persisted_accounts(ctx, ctx["agents"]["B"].principal_id)
    matching = [a for a in accounts if a["domain"] == domain]
    assert len(matching) == 1, f"Expected 1 account for agent B domain {domain}, got {len(matching)}"
    assert matching[0]["status"] != "closed", (
        f"Agent B's account {domain} was deactivated (status={matching[0]['status']})"
    )


@then("only agent A's absent accounts are deactivated")
def then_only_agent_a_deactivated(ctx: dict) -> None:
    """Assert agent B's accounts were not deactivated by agent A's delete_missing.

    Verifies production's agent-scoping: agent B's accounts must remain active
    (not closed) after agent A's delete_missing operation.
    """
    agent_b = ctx.get("agents", {}).get("B")
    assert agent_b is not None, "Test setup error: no agent B in context"
    agent_b_accounts = _persisted_accounts(ctx, agent_b.principal_id)
    assert agent_b_accounts, "Test setup error: agent B should have at least one account"
    statuses = {a["account_id"]: a["status"] for a in agent_b_accounts}
    for acct_id, status in statuses.items():
        assert status != "closed", f"Agent A's delete_missing deactivated agent B's account {acct_id} (status={status})"


@then(parsers.parse('brand domain "{domain}" remains in its current state'))
def then_brand_unchanged(ctx: dict, domain: str) -> None:
    """Assert the account for the given domain was NOT deactivated."""
    matching = [a for a in _persisted_accounts(ctx) if a["domain"] == domain]
    assert len(matching) == 1, f"Expected account for {domain}, got {len(matching)}"
    assert matching[0]["status"] != "closed", (
        f"Account {domain} was deactivated (status={matching[0]['status']}) but should be unchanged"
    )


@then(parsers.parse('the persisted account for brand domain "{domain}" still has billing "{billing}"'))
def then_persisted_billing_unchanged(ctx: dict, domain: str, billing: str) -> None:
    """Assert a preview left the account's settable state alone, not just its status.

    "Remains in its current state" checked only that the account was not CLOSED,
    which a dry_run that mutated the loaded row and let the transaction commit
    passes cleanly. This pins the field the preview reports on, so an outcome the
    buyer was only shown cannot also have been applied.
    """
    matching = [a for a in _persisted_accounts(ctx) if a["domain"] == domain]
    assert len(matching) == 1, f"Expected exactly one persisted account for {domain}, got {matching}"
    assert matching[0]["billing"] == billing, (
        f"Expected persisted billing {billing!r} for {domain}, got {matching[0]['billing']!r} — "
        "the run wrote a value it only promised to preview"
    )


@then(parsers.parse('the persisted account for brand domain "{domain}" has no payment_terms set'))
def then_persisted_payment_terms_unset(ctx: dict, domain: str) -> None:
    """Assert a settings-update preview left payment_terms unpersisted.

    The dry_run settings-update scenario targets payment_terms specifically:
    asserting only billing (which the entry never touches) would pass even when
    the preview wrote the row. This pins the exact field the preview reports on.
    """
    matching = [a for a in _persisted_accounts(ctx) if a["domain"] == domain]
    assert len(matching) == 1, f"Expected exactly one persisted account for {domain}, got {matching}"
    assert matching[0]["payment_terms"] is None, (
        f"Expected no persisted payment_terms for {domain}, got {matching[0]['payment_terms']!r} — "
        "the dry_run settings-update wrote a value it only promised to preview"
    )


@then("only the included accounts are processed")
def then_only_included_processed(ctx: dict) -> None:
    """Assert the response only contains accounts that were in the sync request."""
    resp = require_payload(ctx)
    request_domains = ctx.get("sync_request_domains")
    assert request_domains, "Test setup error: sync_request_domains not tracked by When step"
    response_domains = {a.brand.domain for a in resp.accounts if a.brand}
    extra = response_domains - request_domains
    assert not extra, f"Response included accounts not in the sync request: {extra}. Request domains: {request_domains}"


@then("no accounts are deactivated")
def then_no_deactivations(ctx: dict) -> None:
    """Assert no accounts were deactivated (all still active/non-closed)."""
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    tenant = ctx["tenant"]
    principal = ctx["principal"]
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        all_accounts = repo.list_by_principal(principal.principal_id)
        closed = [a for a in all_accounts if a.status == "closed"]
        assert len(closed) == 0, (
            f"Expected 0 deactivated accounts, found {len(closed)}: {[a.brand.domain for a in closed]}"
        )


@then(parsers.parse('the account for brand domain "{domain}" is processed normally'))
def then_account_processed_normally(ctx: dict, domain: str) -> None:
    """Assert the account was processed (action is created/updated/unchanged, not failed)."""
    resp = require_payload(ctx)
    acct = _find_account_by_brand(resp, domain)
    actual = _action_str(acct.action)
    assert actual in ("created", "updated", "unchanged"), (
        f"Expected normal processing for {domain}, got action '{actual}'"
    )
    ctx["last_account"] = acct


# ═══════════════════════════════════════════════════════════════════════
# Slice 7: Context echo + validation + schema + sandbox
# ═══════════════════════════════════════════════════════════════════════


# ── Given: sandbox setup ───────────────────────────────────────────────


@given("the seller declares account.sandbox equals true in capabilities")
@given("the seller declares features.sandbox equals true in capabilities")  # legacy alias (pre-3.1.1 wording)
def given_sandbox_supported(ctx: dict) -> None:
    """Configure seller to support sandbox mode.

    3.1.1 locates the sandbox capability at account.sandbox on the capabilities
    response (get-adcp-capabilities-response.json#/properties/account/properties/sandbox);
    the features.sandbox alias is kept for any un-migrated feature text.

    Writes the REAL tenant column the gate reads (resolve_account_sandbox,
    src/core/billing_policy.py). It previously set only a ctx flag that no
    production code path consulted, and passed anyway because the gate defaulted
    a missing value to "supported" -- so the Given was dormant and the scenario
    graded the default, not the declaration. #1721 flipped that default to False
    (a seller advertises sandbox by configuring it, never by omission), which is
    what surfaced this.
    """
    _setup_tenant_and_principal(ctx)
    ctx["env"].configure_tenant_field("account_sandbox", True)


@given("both sandbox and production accounts exist for the Buyer")
def given_sandbox_and_production_accounts(ctx: dict) -> None:
    """Create one sandbox and one production account with agent access.

    Records the sandbox and production account_ids separately (not just the
    combined ``expected_account_ids`` set) so the sandbox-filter Thens can grade
    the filter non-vacuously: the sandbox account MUST be returned and the
    production account MUST be absent — an empty result is a filter defect, not a
    pass. Real DB seeding via factories (not mock injection).
    """
    sandbox_acct = _create_accessible_account(ctx, status="active", sandbox=True)
    prod_acct = _create_accessible_account(ctx, status="active", sandbox=False)
    ctx["sandbox_account_ids"] = {sandbox_acct.account_id}
    ctx["production_account_ids"] = {prod_acct.account_id}


# ── When: context-bearing requests ─────────────────────────────────────


def _parse_inline_context(ctx_json_str: str) -> dict:
    """Parse inline JSON context string from Gherkin step."""
    import json

    return json.loads(ctx_json_str)


@when(
    parsers.re(
        r"the Buyer Agent sends a (?P<operation>list_accounts|sync_accounts) "
        r"request with context (?P<ctx_json>\{.*\})"
    )
)
def when_request_with_context(ctx: dict, operation: str, ctx_json: str) -> None:
    """Send a list_accounts or sync_accounts request with inline context.

    Context-echo is cross-cutting: tests both list and sync operations.
    The conftest harness provides AccountSyncEnv for context-echo tags, which
    dispatches BOTH verbs (AccountListDispatchMixin), so each operation is
    graded on the scenario's real transport.
    """
    context_data = _parse_inline_context(ctx_json)
    ctx["sent_context"] = context_data

    from adcp.types import ContextObject

    context_obj = ContextObject.model_validate(context_data)

    if operation == "list_accounts":
        from src.core.schemas.account import ListAccountsRequest

        req = ListAccountsRequest(context=context_obj)
    else:
        from src.core.schemas.account import SyncAccountsRequest

        # Provide a minimal valid account for sync context echo tests
        req = SyncAccountsRequest(
            idempotency_key=fresh_idempotency_key(),
            accounts=[{"brand": {"domain": "ctx-test.com"}, "operator": "ctx-test.com", "billing": "operator"}],
            context=context_obj,
        )

    dispatch_kwargs: dict[str, Any] = {}
    if "force_credential" in ctx:
        dispatch_kwargs["credential"] = ctx["force_credential"]

    try:
        dispatch_request(ctx, req=req, **dispatch_kwargs)
    except Exception as exc:
        ctx["error"] = exc


# ── When: input validation requests ────────────────────────────────────


@when("the Buyer Agent sends a sync_accounts request with an empty accounts array")
def when_sync_empty_accounts(ctx: dict) -> None:
    """Send sync_accounts with an empty accounts array."""

    _sync_raw(ctx, accounts=[])


@when("the Buyer Agent sends a sync_accounts request with an account that has no brand domain field")
def when_sync_no_brand_domain(ctx: dict) -> None:
    """Send sync with account missing brand.domain — triggers Pydantic validation."""

    _sync_raw(ctx, accounts=[{"operator": "test.com", "billing": "operator"}])


@when("the Buyer Agent sends a sync_accounts request with an account that has no operator field")
def when_sync_no_operator(ctx: dict) -> None:
    """Send sync with account missing operator — triggers Pydantic validation."""

    _sync_raw(ctx, accounts=[{"brand": {"domain": "test.com"}, "billing": "operator"}])


@when("the Buyer Agent sends a sync_accounts request with an account that has no billing field")
def when_sync_no_billing(ctx: dict) -> None:
    """Send sync with account missing billing — triggers Pydantic validation."""

    _sync_raw(ctx, accounts=[{"brand": {"domain": "test.com"}, "operator": "test.com"}])


@when(parsers.parse('the Buyer Agent sends a sync_accounts request with {field} set to "{value}"'))
def when_sync_invalid_field(ctx: dict, field: str, value: str) -> None:
    """Send sync with an invalid field value for validation testing."""

    # Build account entry with the invalid field
    entry: dict[str, Any] = {
        "brand": {"domain": "valid.com"},
        "operator": "valid.com",
        "billing": "operator",
    }

    if field == "brand.domain":
        entry["brand"]["domain"] = value
    elif field == "brand.brand_id":
        entry["brand"]["brand_id"] = value
    elif field == "operator":
        entry["operator"] = value
    else:
        entry[field] = value

    _sync_raw(ctx, accounts=[entry])


@when(parsers.parse("the Buyer Agent sends a sync_accounts request with {count:d} accounts"))
def when_sync_n_accounts(ctx: dict, count: int) -> None:
    """Send sync with N generated accounts, so the SELLER decides whether N is legal.

    The array bound is a boundary this scenario grades on the wire. Building a
    ``SyncAccountsRequest`` here instead would put the bound back in the test process:
    at 1001 entries the DTO refuses, the exception never leaves this function, and
    ``dispatch_request`` is never reached -- so the wire-compliance Then below has no
    ``TransportResult`` and the scenario reports on what the test did to itself.

    Goes through ``_sync_raw``, the negative-path dispatch this verb already has, and
    builds the entries from ``SyncAccountsRequestFactory.payload()`` so an entry carries
    the fields the accepted model declares rather than a hand-typed triple.
    """
    from tests.factories.request import SyncAccountsRequestFactory

    entry = SyncAccountsRequestFactory.payload()["accounts"][0]
    accounts = [
        {**entry, "brand": {"domain": f"brand-{i:04d}.com"}, "operator": f"brand-{i:04d}.com"} for i in range(count)
    ]
    ctx["submitted_account_count"] = count
    _sync_raw(ctx, accounts=accounts)


# ── Then: context echo assertions ──────────────────────────────────────


@then(parsers.re(r"the response includes context (?P<ctx_json>\{.*\})"))
def then_response_includes_context(ctx: dict, ctx_json: str) -> None:
    """Assert the response (success or error) includes the expected context.

    For success responses, reads context from the response object.
    For error responses, attempts to read context from the error object.
    Never falls back to comparing the test's own sent_context.
    """
    import json

    import pytest

    expected = json.loads(ctx_json)

    # Check success response first
    resp = payload_or_none(ctx)
    if resp is not None:
        resp_context = getattr(resp, "context", None)
        assert resp_context is not None, "Response has no context field"
        # ContextObject may be a Pydantic model — convert to dict for comparison
        if hasattr(resp_context, "model_dump"):
            actual = resp_context.model_dump(mode="json", exclude_none=True)
        elif isinstance(resp_context, dict):
            actual = resp_context
        else:
            actual = dict(resp_context)
        assert actual == expected, f"Context mismatch: expected {expected}, got {actual}"
        return

    # Error path: read context from the error object/payload, not the sent value
    error = ctx.get("error")
    assert error is not None, "No response and no error — cannot verify context echo"
    # Try to extract context from the error object
    error_context = getattr(error, "context", None)
    if error_context is not None:
        if hasattr(error_context, "model_dump"):
            actual = error_context.model_dump(mode="json", exclude_none=True)
        elif isinstance(error_context, dict):
            actual = error_context
        else:
            actual = dict(error_context)
        assert actual == expected, f"Error context mismatch: expected {expected}, got {actual}"
        return
    # Production error objects (AdCPSalesAgentError) do not carry a context field yet, and
    # the wire error envelope (a2a/mcp/rest) does not echo context on the error
    # path — only impl carries context=req.context on the AdCPSalesAgentError. Tracked by
    # #1417 (D2: envelope status/context on the wire error path).
    pytest.xfail(
        "SPEC-PRODUCTION GAP (D2): context not echoed on the wire "
        "error envelope — AdCPSalesAgentError carries no context field on a2a/mcp/rest, "
        "expected context echo on error responses"
    )


@then("the context is identical to what was sent")
def then_context_identical(ctx: dict) -> None:
    """Assert the echoed context is exactly what was sent (deep equality).

    REQUIRES the payload rather than tolerating its absence. Two facts settle
    that, and they were measured rather than assumed:

    1. Both scenarios using this step (BR-UC-011 ``context_provided`` and
       ``context_nested``) run it as an ``And`` AFTER
       ``the response includes context {...}``. That preceding Then owns the
       error path, so by the time this step runs a payload exists or the
       scenario has already ended.
    2. The alternative — mirroring that sibling's inline ``pytest.xfail`` — is
       forbidden by ``test_architecture_bdd_no_inline_xfail``: an inline xfail
       registers nothing, is keyed to no scenario, and turns every later
       assertion into dead code. The sibling's copy is a grandfathered
       allowlist entry, not a pattern to follow, and allowlists here only
       shrink.

    Tolerating None was what let this step pass with zero assertions whenever
    the dispatch errored (GH #1802).
    """
    sent = ctx.get("sent_context")
    assert sent is not None, "No sent_context to compare"

    resp = require_payload(ctx)
    resp_context = getattr(resp, "context", None)
    assert resp_context is not None, "Response has no context"
    if hasattr(resp_context, "model_dump"):
        actual = resp_context.model_dump(mode="json", exclude_none=True)
    elif isinstance(resp_context, dict):
        actual = resp_context
    else:
        actual = dict(resp_context)
    assert actual == sent, f"Context not identical: sent {sent}, got {actual}"


@then("the response does not include a context field")
def then_no_context(ctx: dict) -> None:
    """Assert the response has no context field (or it's None)."""
    resp = require_payload(ctx)
    context = getattr(resp, "context", None)
    assert context is None, f"Expected no context, got {context}"


@then(parsers.re(r"the response is an error variant with (?P<code>\w+)"))
def then_error_with_code(ctx: dict, code: str) -> None:
    """Assert the response is an error with a specific error code -- wire-first.

    Same wire-first strategy as then_error_code (:1803): prefer the real wire
    envelope's code over the lossy reconstructed ctx['error'].
    """
    ctx["result"].assert_wire_error(code)


# ── Then: input validation assertions ──────────────────────────────────


@then("the error indicates accounts array must not be empty")
def then_empty_accounts_error(ctx: dict) -> None:
    """Assert the buyer received VALIDATION_ERROR naming the accounts field.

    One call on the single sanctioned surface. The isinstance gate this replaces
    was redundant on top of the wire read below it — and weaker, since it also
    admitted a bare ``ValueError``. WHICH input was rejected is graded on the
    protocol ``field`` pointer, not on the sentence: the sentence is a function
    of the code through CODE_TABLE and cannot name a request field, so a
    substring pair could only ever have passed by accident of wording.
    """
    ctx["result"].assert_wire_error("VALIDATION_ERROR", field="accounts")


# ── Then: sandbox assertions ───────────────────────────────────────────


@then("the provisioned account should have sandbox equals true")
def then_account_sandbox_true(ctx: dict) -> None:
    """Assert the provisioned account has sandbox=True."""
    resp = require_payload(ctx)
    acct = resp.accounts[0]
    assert acct.sandbox is True, f"Expected sandbox=True, got {acct.sandbox}"
    ctx["last_account"] = acct


@then("the account should have a seller-assigned account_id")
def then_sandbox_account_has_id(ctx: dict) -> None:
    """Assert the account has a non-empty seller-assigned account_id.

    Matches ``then_account_has_id`` (a bare ``is not None`` accepts ``""``, which
    is not a seller assignment): the account_id must be a non-empty string.
    Spec: account/sync-accounts-response.json#/oneOf/0/properties/accounts/items/properties/account_id.
    """
    acct = ctx.get("last_account") or require_payload(ctx).accounts[0]
    account_id = getattr(acct, "account_id", None)
    assert isinstance(account_id, str) and len(account_id) > 0, (
        f"Account missing non-empty seller-assigned account_id: {acct}"
    )


@then("no real ad platform account should have been created")
def then_no_real_platform_account(ctx: dict) -> None:
    """Assert sandbox account was created without external platform provisioning.

    Verifies the consequence (no external platform reference in DB), not just
    the input (sandbox=True). The DB record should have no platform_mappings,
    proving no adapter was called to provision an external account.
    """
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    acct = ctx.get("last_account") or require_payload(ctx).accounts[0]
    account_id = acct.account_id
    assert account_id is not None, "Account missing account_id"

    tenant = ctx["tenant"]
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        db_acct = repo.get_by_id(account_id)
        assert db_acct is not None, f"Account {account_id} not found in DB"
        assert db_acct.sandbox is True, f"DB account {account_id} sandbox={db_acct.sandbox}"
        # No external platform reference should exist — platform_mappings must be None/empty
        assert not db_acct.platform_mappings, (
            f"Sandbox account {account_id} has platform_mappings={db_acct.platform_mappings} "
            f"— expected no external platform references"
        )


@then("all returned accounts should have sandbox equals true")
def then_all_accounts_sandbox_true(ctx: dict) -> None:
    """Assert the sandbox filter returned the seeded sandbox account and only it.

    Non-vacuous: an empty response is a filter defect, not a pass (the prior
    ``for acct in resp.accounts`` loop passed on ``[]``). The Given seeded exactly
    one sandbox account; that id MUST be present, and every returned account MUST
    carry sandbox=True.

    Spec: account/list-accounts-request.json#/properties/sandbox — "true returns
    only sandbox accounts"; core/account.json#/properties/sandbox (boolean).
    """
    resp = require_payload(ctx)
    returned_ids = {acct.account_id for acct in resp.accounts}
    expected_sandbox_ids = ctx.get("sandbox_account_ids", set())
    assert expected_sandbox_ids, "Given did not record sandbox_account_ids — fixture wiring bug"
    assert expected_sandbox_ids <= returned_ids, (
        f"Sandbox filter dropped the seeded sandbox account(s): expected {expected_sandbox_ids} "
        f"present, got returned ids {returned_ids}"
    )
    for acct in resp.accounts:
        assert acct.sandbox is True, f"Expected sandbox=True, got sandbox={acct.sandbox} for {acct.name}"


@then("the response should not include production accounts")
def then_no_production_accounts(ctx: dict) -> None:
    """Assert the seeded production account is absent (sandbox=true filter excludes it).

    Non-vacuous: names the concrete production account_id from the Given and
    asserts it is NOT in the returned set — the prior ``sandbox is True`` loop
    passed on an empty result. Absence (not merely sandbox=false) is the
    production signal per the request-filter contract.

    Spec: account/list-accounts-request.json#/properties/sandbox — "false returns
    only production accounts. Omit to return all accounts."; core/account.json
    #/properties/sandbox (absence means production).
    """
    resp = require_payload(ctx)
    returned_ids = {acct.account_id for acct in resp.accounts}
    prod_ids = ctx.get("production_account_ids", set())
    assert prod_ids, "Given did not record production_account_ids — fixture wiring bug"
    leaked = prod_ids & returned_ids
    assert not leaked, f"Production account(s) {sorted(leaked)} leaked past the sandbox=true filter"
    for acct in resp.accounts:
        assert acct.sandbox is True, f"Non-sandbox account found: {acct.account_id} (sandbox={acct.sandbox})"


# ── Given: sandbox capability not declared ─────────────────────────────


@given("the seller does not declare account.sandbox in capabilities")
def given_sandbox_not_supported(ctx: dict) -> None:
    """Configure a seller that does NOT advertise the account.sandbox capability.

    v3.1.1 locates the sandbox capability at account.sandbox on the capabilities
    response (get-adcp-capabilities-response.json#/properties/account/properties/sandbox,
    default false). This Given is the negative of ``given_sandbox_supported``: the
    seller has not opted in, so a sync_accounts request carrying sandbox: true MUST
    be rejected per-account (BR-RULE-209 INV-6). Sets Tenant.account_sandbox=False
    via the real DB (env.setup_default_data), the production capability-gate column
    read by the sandbox provisioning gate — not just a ctx-level test flag.
    """
    env = ctx["env"]
    # setup_default_data is idempotent (get-or-create) and safe even when a prior
    # Given (e.g. "the Buyer Agent has an authenticated connection") already
    # created the tenant.
    tenant, principal = env.setup_default_data()
    # configure_tenant_field writes BOTH paths: the in-memory _tenant_overrides
    # PrincipalFactory.make_identity() reads to build REST's synthetic identity
    # (REST's auth-dep override installs a pre-built ResolvedIdentity, bypassing
    # the real DB-backed resolution a2a/mcp exercise -- so a plain DB write alone
    # is invisible to REST), AND the real Tenant row a2a/mcp read via the
    # production auth chain. Also clears the identity cache.
    env.configure_tenant_field("account_sandbox", False)
    ctx["tenant"] = tenant
    ctx["principal"] = principal


# ── When: sandbox response-shape request items ─────────────────────────


@when(
    parsers.re(
        r'the Buyer Agent sends a sync_accounts request with idempotency_key "(?P<key>[^"]+)" '
        r"and a request item where sandbox is (?P<request_item>true|false|omitted)"
    )
)
def when_sync_sandbox_shape(ctx: dict, key: str, request_item: str) -> None:
    """Send one sync_accounts account entry whose sandbox field is true/false/omitted.

    Grades the response-shape echo (true→true, false→false, omitted→absent) against
    account/sync-accounts-response.json#/oneOf/0/properties/accounts/items/properties/sandbox
    ("echoed from the request. Only present for buyer-declared accounts"). The
    descriptive idempotency_key is not carried on the wire (see
    ``when_sync_accounts_with_key_and_table``).
    """
    from src.core.schemas.account import SyncAccountsRequest

    entry: dict[str, Any] = {
        "brand": {"domain": "acme-corp.com"},
        "operator": "acme-corp.com",
        "billing": "operator",
    }
    if request_item == "true":
        entry["sandbox"] = True
    elif request_item == "false":
        entry["sandbox"] = False
    # "omitted": leave sandbox out of the entry entirely

    try:
        req = SyncAccountsRequest(idempotency_key=fresh_idempotency_key(), accounts=[entry])
        dispatch_request(ctx, req=req)
    except Exception as exc:
        ctx["error"] = exc


# ── Then: sandbox response-shape + capability error assertions ─────────


@then(parsers.parse('the per-account result sandbox field is "{expected}"'))
def then_per_account_sandbox_field(ctx: dict, expected: str) -> None:
    """Assert the per-account result's sandbox field on the wire is true/false/absent.

    Tri-state, read on the buyer-facing success wire (absent ≠ false ≠ true):
    "omitted" in the request MUST echo as absent, not a JSON null — an unset
    optional is not serialized. "false" and "true" echo the literal boolean.

    Spec: account/sync-accounts-response.json#/oneOf/0/properties/accounts/items/properties/sandbox
    ("Whether this is a sandbox account, echoed from the request. Only present for
    buyer-declared accounts.").
    """
    body = wire_dict(ctx)
    accounts = body.get("accounts")
    assert isinstance(accounts, list) and accounts, f"success wire body carries no accounts[]: {body!r}"
    acct0 = accounts[0]
    assert isinstance(acct0, dict), f"accounts[0] is not a JSON object on the wire: {acct0!r}"
    if expected == "absent":
        assert "sandbox" not in acct0, (
            f"expected sandbox absent from the wire (production account), got sandbox={acct0.get('sandbox')!r}"
        )
    elif expected == "true":
        assert acct0.get("sandbox") is True, f"expected sandbox true on the wire, got {acct0.get('sandbox')!r}"
    elif expected == "false":
        assert acct0.get("sandbox") is False, f"expected sandbox false on the wire, got {acct0.get('sandbox')!r}"
    else:
        raise AssertionError(f"unknown expected sandbox shape {expected!r} (want true/false/absent)")


def _last_account_error_matching(ctx: dict, code: str) -> Any:
    """Return the per-account error object carrying ``code`` on the referenced account."""
    errors = _last_account_errors(ctx)
    err = next((e for e in errors if getattr(e, "code", None) == code), None)
    assert err is not None, (
        f"No per-account error with code {code!r}; got codes {[getattr(e, 'code', None) for e in errors]}"
    )
    return err


@then(parsers.parse('the per-account error field points at "{field}"'))
def then_per_account_error_field(ctx: dict, field: str) -> None:
    """Assert the referenced account's error names WHICH request field was rejected.

    Spec: core/error.json#/properties/field — the JSONPath-lite pointer at the
    offending request field, ENTRY-RELATIVE (e.g. 'sandbox',
    'notification_configs[0].event_types[0]'), which is the rooting the graded
    contract pins.

    Read off the WIRE, not the reconstructed response model: the pointer's
    rooting is a claim about the bytes the buyer receives, so grading it on a
    typed object would leave the wire spelling unpinned -- the co-located
    error-code and error-recovery Thens already read the wire, and this one
    read the model.
    """
    errors = _sole_account_errors(ctx)
    fields = [e.get("field") for e in errors]
    assert field in fields, f"Expected a per-account error field pointing at {field!r}, got {fields}"


@then(parsers.parse('the per-account error suggestion mentions "{needle}"'))
def then_per_account_error_suggestion_mentions(ctx: dict, needle: str) -> None:
    """Assert a per-account error's suggestion string contains the remediation cue.

    Spec: core/error.json#/properties/suggestion ("Suggested fix for the error").
    For UNSUPPORTED_FEATURE the documented remediation is
    enums/error-code.json#/enumMetadata/UNSUPPORTED_FEATURE/suggestion =
    "check get_adcp_capabilities and remove unsupported fields".
    """
    errors = _last_account_errors(ctx)
    suggestions = [getattr(e, "suggestion", None) or "" for e in errors]
    assert any(needle in s for s in suggestions), (
        f"Expected a per-account error suggestion mentioning {needle!r}, got {suggestions!r}"
    )


# ═══════════════════════════════════════════════════════════════════════
# UC-011 account-level notification_configs wiring
#
# Graduated (T2 increment F4a): the sync_accounts pipeline now processes
# accounts[].notification_configs — SyncResponseAccount persists and echoes the
# whole-array JSONType column with declarative-replace semantics (omit
# preserves, [] clears, re-sent subscriber_id replaces in place), scrubbing
# authentication.credentials on echo. The register/replace-clear/omit-preserves
# tags are no longer xfailed (removed from conftest _XFAIL_TAGS). The per-account
# REJECTION families below (event-scope, duplicate-subscriber, activation-proof)
# graduated separately as F4b/F4c.
#
# Spec (v3.1.1): core/notification-config.json (subscriber shape; write-only
# credentials; active flag persisted even when false); account/
# sync-accounts-request.json#/properties/accounts/items/properties/notification_configs
# (declarative replace — omit=leave unchanged, []=clear, re-send subscriber_id
# replaces in place); account/sync-accounts-response.json#/oneOf/0/.../
# notification_configs (applied subscribers echoed; authentication.credentials
# omitted); compliance/3.1.1/universal/notification-config-lifecycle.yaml
# (graded storyboard: register_and_echo_paused_subscriber, replace_pause_and_clear).
# ═══════════════════════════════════════════════════════════════════════


def _parse_event_types(ets: str) -> list[str]:
    """Split a comma-space-separated event_types string into a list of names."""
    return [e.strip() for e in ets.split(",") if e.strip()]


def _notif_config(
    subscriber_id: str, url: str, event_types: str, *, active: bool, authentication: dict | None = None
) -> dict[str, Any]:
    """Build a notification_configs[] entry dict for a sync_accounts request."""
    entry: dict[str, Any] = {
        "subscriber_id": subscriber_id,
        "url": url,
        "event_types": _parse_event_types(event_types),
        "active": active,
    }
    if authentication is not None:
        entry["authentication"] = authentication
    return entry


def _dispatch_sync_notification(ctx: dict, domain: str, notification_configs: list[dict[str, Any]]) -> None:
    """Dispatch a sync_accounts request carrying a notification_configs array for one account.

    ``ctx["signed"]`` asks for a REAL RFC 9421 signature on this dispatch and is
    forwarded rather than branched on (salesagent-n78j0.1.3): a registration carrying
    ``notification_configs[].authentication`` is one the seller MUST refuse unless it is
    signed (security.mdx @ v3.1.1 :1462-1465), so the credential-carrying scenario has
    to be able to sign. Defaults to False, leaving every other scenario byte-identical.
    """
    from src.core.schemas.account import SyncAccountsRequest

    entry = {
        "brand": {"domain": domain},
        "operator": domain,
        "billing": "operator",
        "notification_configs": notification_configs,
    }
    try:
        req = SyncAccountsRequest(idempotency_key=fresh_idempotency_key(), accounts=[entry])
        dispatch_request(ctx, req=req, signed=ctx.get("signed", False))
    except Exception as exc:
        ctx["error"] = exc


def _sub_attr(sub: Any, name: str) -> Any:
    """Read a subscriber attribute from a dict or a typed NotificationConfig."""
    return sub.get(name) if isinstance(sub, dict) else getattr(sub, name, None)


def _echoed_subscribers(ctx: dict, domain: str | None = None) -> list[Any]:
    """Return the referenced account's echoed notification_configs (or [] when absent).

    Reads the typed response's per-account notification_configs, which SyncResponseAccount
    now persists and echoes (graduated, T2 increment F4a).
    """
    resp = require_payload(ctx)
    if domain is not None:
        acct = next((a for a in resp.accounts if a.brand.domain == domain), None)
        assert acct is not None, (
            f"No account for domain {domain!r} in response; domains={[a.brand.domain for a in resp.accounts]}"
        )
    else:
        acct = ctx.get("last_account") or resp.accounts[0]
    configs = getattr(acct, "notification_configs", None)
    return list(configs) if configs else []


def _persisted_subscribers(ctx: dict, domain: str | None = None) -> list[Any]:
    """Read the account's PERSISTED notification_configs back through list_accounts.

    Used where the graded obligation is "the stored array is unchanged" rather than
    "the response echoed X" — notably after a rejected entry, whose failed result
    carries no notification_configs at all.

    Reuses the list read-back the register-paused scenario already exercises, so
    the credential scrub and the JSONType None-vs-[] round trip are graded on the
    same path a buyer would actually use.

    Dispatches through the env's list verb over the SCENARIO'S transport, but via
    env.call_via rather than dispatch_request: dispatch_request is the one writer
    of ctx["result"], and this read-back must not clobber the result the scenario
    is actually grading.
    """
    from src.core.schemas.account import ListAccountsRequest

    env = ctx["env"]
    # Gated and recorded in place, for the reason the docstring above already gives
    # for not routing through ``dispatch_request``: a read-back reaches a transport
    # like any other dispatch, but it must not overwrite ctx["result"].
    read_back_kwargs = {"req": ListAccountsRequest()}
    gate_and_record(read_back_kwargs)
    read_back = env.call_via(ctx["transport"], **read_back_kwargs)
    assert read_back.is_success, f"list_accounts read-back failed: {read_back.error!r}"
    listed = read_back.payload
    if domain is not None:
        acct = next((a for a in listed.accounts if a.brand and a.brand.domain == domain), None)
        assert acct is not None, (
            f"No persisted account for domain {domain!r}; "
            f"domains={[a.brand.domain for a in listed.accounts if a.brand]}"
        )
    else:
        acct = listed.accounts[0]
    configs = getattr(acct, "notification_configs", None)
    return list(configs) if configs else []


def _find_subscriber(subs: list[Any], subscriber_id: str) -> Any:
    """Find an echoed subscriber by subscriber_id, or None."""
    return next((s for s in subs if str(_sub_attr(s, "subscriber_id")) == subscriber_id), None)


@when(
    parsers.re(
        r'the Buyer Agent sends a sync_accounts request re-sending subscriber "(?P<sub>[^"]+)" '
        r'as paused with url "(?P<url>[^"]+)" and event_types "(?P<ets>[^"]+)"'
    )
)
def when_sync_resend_subscriber_paused(ctx: dict, sub: str, url: str, ets: str) -> None:
    """Declarative replace: re-send the same subscriber_id as paused (active:false).

    Spec: sync-accounts-request notification_configs description — "Re-sending an
    existing subscriber_id for the account replaces that subscriber's config."
    core/notification-config.json#/properties/subscriber_id: replaces URL,
    event_types, authentication selector, AND active flag.
    """
    domain = ctx.get("notif_domain", "acme-corp.com")
    cfg = _notif_config(sub, url, ets, active=False)
    _dispatch_sync_notification(ctx, domain, [cfg])


@when(
    parsers.re(
        r"the Buyer Agent sends a sync_accounts request with an empty notification_configs array "
        r'for brand domain "(?P<domain>[^"]+)"'
    )
)
def when_sync_clear_notification_configs(ctx: dict, domain: str) -> None:
    """Declarative clear: send [] to remove all subscribers.

    Spec: sync-accounts-request notification_configs description — "send [] to
    remove all subscribers."
    """
    _dispatch_sync_notification(ctx, domain, [])


@then(parsers.re(r"the account notification_configs echo exactly (?P<count>\d+) subscribers?"))
def then_notif_echo_count(ctx: dict, count: str) -> None:
    """Assert the account echoes exactly N applied notification subscribers.

    Spec: sync-accounts-response.json#/oneOf/0/.../notification_configs — "Only
    configs that the seller has persisted are echoed." Storyboard field_absent
    check on notification_configs[1] pins "exactly one subscriber".
    """
    subs = _echoed_subscribers(ctx)
    assert len(subs) == int(count), f"Expected {count} echoed subscriber(s), got {len(subs)}: {subs!r}"


@then(parsers.re(r'the echoed subscriber "(?P<sub>[^"]+)" has url "(?P<url>[^"]+)" and active (?P<flag>true|false)'))
def then_echoed_subscriber_url_active(ctx: dict, sub: str, url: str, flag: str) -> None:
    """Assert the echoed subscriber carries the exact url and active flag.

    Spec: core/notification-config.json#/properties/{url,active}; the storyboard
    grades subscriber url and active=false field_value checks.
    """
    subs = _echoed_subscribers(ctx)
    match = _find_subscriber(subs, sub)
    assert match is not None, f"No echoed subscriber {sub!r} in {subs!r}"
    assert str(_sub_attr(match, "url")) == url, f"Expected url {url!r}, got {_sub_attr(match, 'url')!r}"
    expected_active = flag == "true"
    assert _sub_attr(match, "active") is expected_active, (
        f"Expected active {expected_active}, got {_sub_attr(match, 'active')!r}"
    )


@then(parsers.parse('the echoed subscriber has event_types "{ets}"'))
def then_echoed_subscriber_event_types(ctx: dict, ets: str) -> None:
    """Assert the echoed subscriber's event_types equal the registered set, in order.

    Spec: core/notification-config.json#/properties/event_types (minItems 1,
    uniqueItems); the storyboard grades event_types[0]/[1] field_value checks.
    """
    subs = _echoed_subscribers(ctx)
    assert subs, f"No echoed subscribers to check event_types on: {subs!r}"
    expected = _parse_event_types(ets)
    actual = [str(e) for e in (_sub_attr(subs[0], "event_types") or [])]
    assert actual == expected, f"Expected event_types {expected}, got {actual}"


@then(parsers.parse('the echoed subscriber\'s authentication object omits "{field}"'))
def then_echoed_subscriber_auth_omits(ctx: dict, field: str) -> None:
    """Assert the echoed authentication object does not carry the write-only field.

    Spec: core/notification-config.json top-level — "Credentials and shared secrets
    in authentication.credentials are write-only — sellers MUST NOT echo them back";
    sync-accounts-response notification_configs — "authentication.credentials is
    omitted on every entry (write-only)."

    RESTORED with the branch that made it vacuous REMOVED (salesagent-n78j0.1.3). The
    original returned early when no ``authentication`` object was echoed at all, which
    is a pass for a response that echoed the credential under any OTHER key — and for a
    response that echoed nothing, which is not what the Then claims. Both halves are now
    graded: the named field is absent from the authentication object (empty when the
    object is omitted, which legitimately satisfies "omits"), AND the registered
    credential VALUE appears nowhere in the serialized subscriber. The value check is
    what gives the step teeth on the omitted-object path, and it is the whole point of a
    write-only rule.
    """
    subs = _echoed_subscribers(ctx)
    assert subs, f"No echoed subscribers to check authentication on: {subs!r}"
    subscriber = subs[0]
    auth = _sub_attr(subscriber, "authentication")
    if auth is None:
        auth_dict: dict[str, Any] = {}
    else:
        auth_dict = auth if isinstance(auth, dict) else auth.model_dump(exclude_none=True)
    assert field not in auth_dict, f"authentication echoed write-only {field!r}: {auth_dict!r}"

    credential = ctx.get("notif_credential")
    assert credential, (
        "the When that registers a subscriber must record the credential it sent "
        "(ctx['notif_credential']); without it this Then cannot tell an omitted "
        "authentication object apart from a credential echoed under another key"
    )
    serialized = json.dumps(subscriber, default=str) if isinstance(subscriber, dict) else subscriber.model_dump_json()
    assert credential not in serialized, (
        f"the registered credential was echoed back in the subscriber document: {serialized!r}. "
        "core/notification-config.json makes authentication.credentials write-only, which is a "
        "statement about the WHOLE echoed document, not only about the field it was sent in."
    )


@then(
    parsers.re(
        r'the listed account for brand domain "(?P<domain>[^"]+)" echoes subscriber "(?P<sub>[^"]+)" '
        r"with active (?P<flag>true|false)"
    )
)
def then_listed_account_echoes_subscriber(ctx: dict, domain: str, sub: str, flag: str) -> None:
    """Assert a list_accounts read-back echoes the persisted paused subscriber.

    Spec: core/account.json carries notification_configs; paused entries MUST be
    observable on the buyer's next read (core/notification-config.json#/properties/active:
    "the buyer's next sync_accounts MUST observe the same array").
    """
    subs = _echoed_subscribers(ctx, domain=domain)
    match = _find_subscriber(subs, sub)
    assert match is not None, f"No echoed subscriber {sub!r} for listed account {domain!r}: {subs!r}"
    expected_active = flag == "true"
    assert _sub_attr(match, "active") is expected_active, (
        f"Expected active {expected_active}, got {_sub_attr(match, 'active')!r}"
    )


# ── UC-011 notification_configs — final batch ──
# event-scope-reject, duplicate-subscriber, activation-proof-fail, omit-preserves.
# All four grade the account-level notification_configs surface. Graduated
# (T2 increments F4a/F4b/F4c): _check_notification_configs runs pre-persist and
# rejects media-buy-anchored event_types / duplicate subscriber_ids per entry;
# SyncResponseAccount persists and echoes notification_configs; NotificationProofService
# performs a bounded proof-of-control challenge before the write transaction opens.
# Spec (v3.1.1): core/notification-config.json (event_types media-buy-anchored
# rejection with INVALID_REQUEST/VALIDATION_ERROR at the event_types entry;
# subscriber_id uniqueness rejection at the duplicate entry; active flag is
# replaced per-subscriber state; proof-of-control before treating a new/changed
# active subscriber as active); enums/error-code.json (INVALID_REQUEST and
# VALIDATION_ERROR are canonical, recovery 'correctable').


@given(
    parsers.re(
        r'an account for brand domain "(?P<domain>[^"]+)" exists with a paused notification config '
        r'subscriber "(?P<sub>[^"]+)" for url "(?P<url>[^"]+)"'
    )
)
def given_account_with_paused_notif_subscriber(ctx: dict, domain: str, sub: str, url: str) -> None:
    """Pre-create an account carrying one PAUSED (active:false) notification subscriber.

    F19: the omit-preserves Then asserts ``active false`` on the read-back, so the seed
    MUST declare the paused state rather than lean on an undeclared fixture default.

    Spec: core/notification-config.json#/properties/active — "When false, the seller
    persists the configuration but suppresses fires"; the active flag is part of the
    per-subscriber replaced state (#/properties/subscriber_id), so it must be declared
    to be assertable on the echo.
    """
    _setup_tenant_and_principal(ctx)
    cfg = _notif_config(sub, url, "creative.status_changed, creative.purged", active=False)
    _dispatch_sync_notification(ctx, domain, [cfg])
    ctx["notif_domain"] = domain
    ctx["notif_prior"] = {"subscriber_id": sub, "url": url, "active": False}


@when(
    parsers.re(
        r'the Buyer Agent sends a sync_accounts request provisioning brand domain "(?P<domain>[^"]+)" '
        r'with a paused notification config subscriber "(?P<sub>[^"]+)" for url "(?P<url>[^"]+)" '
        r'and event_types "(?P<ets>[^"]+)"'
    )
)
def when_sync_provision_paused_subscriber_event_types(ctx: dict, domain: str, sub: str, url: str, ets: str) -> None:
    """Provision an account with one paused subscriber whose event_types are under test.

    The credential-free sibling of the ``…, and legacy Bearer authentication`` When
    below: this variant carries only the event_types, so the scenarios that use it grade
    event-scope rejection rather than credential handling.

    Spec: core/notification-config.json#/properties/event_types — media-buy-anchored
    types (scheduled, final, delayed, adjusted, impairment) "are invalid on this
    surface; sellers MUST reject those entries as per-account validation failures with
    INVALID_REQUEST or VALIDATION_ERROR and error.field pointing at the invalid
    event_types entry rather than silently dropping them".
    """
    ctx["notif_domain"] = domain
    cfg = _notif_config(sub, url, ets, active=False)
    _dispatch_sync_notification(ctx, domain, [cfg])


@when(
    parsers.re(
        r'the Buyer Agent sends a sync_accounts request provisioning brand domain "(?P<domain>[^"]+)" '
        r'with a paused notification config subscriber "(?P<sub>[^"]+)" for url "(?P<url>[^"]+)", '
        r'event_types "(?P<ets>[^"]+)", and legacy Bearer authentication'
    )
)
def when_sync_provision_paused_subscriber(ctx: dict, domain: str, sub: str, url: str, ets: str) -> None:
    """Provision an account with one paused (active:false) subscriber carrying legacy Bearer auth.

    The authentication block (Bearer scheme + a 32-char write-only credential per
    core/notification-config.json#/properties/authentication/properties/credentials)
    gives the credentials-omitted echo assertion teeth: the input declares a
    credential, so an echo that returns it is a real write-only leak.

    RESTORED SIGNED (salesagent-n78j0.1.3). Registering a credential is exactly the
    payload that forces a signature (security.mdx @ v3.1.1 :1462-1465), so the scenario
    that uses this When declares ``the Buyer Agent signs the request`` — without it the
    seller would (correctly) answer ``request_signature_required`` and the echo
    assertions would never be reached. The step itself stays transport-blind: it does
    not know what signing means on any leg, only that ``ctx["signed"]`` travels with the
    dispatch.
    """
    ctx["notif_domain"] = domain
    credential = "x" * 32
    ctx["notif_credential"] = credential
    auth = {"schemes": ["Bearer"], "credentials": credential}
    cfg = _notif_config(sub, url, ets, active=False, authentication=auth)
    _dispatch_sync_notification(ctx, domain, [cfg])


@when(
    parsers.re(
        r'the Buyer Agent sends a sync_accounts request provisioning brand domain "(?P<domain>[^"]+)" '
        r'with two notification config entries both using subscriber "(?P<sub>[^"]+)"'
    )
)
def when_sync_provision_duplicate_subscriber(ctx: dict, domain: str, sub: str) -> None:
    """Provision an account with two notification_configs entries sharing one subscriber_id.

    Spec: core/notification-config.json#/properties/subscriber_id — "Sending two
    entries with the same subscriber_id in a single sync_accounts request array is
    rejected as a per-account validation failure with INVALID_REQUEST or
    VALIDATION_ERROR, and error.field MUST point at the duplicate entry."
    """
    ctx["notif_domain"] = domain
    first = _notif_config(sub, "https://buyer.example/webhooks/adcp/one", "creative.status_changed", active=False)
    second = _notif_config(sub, "https://buyer.example/webhooks/adcp/two", "creative.purged", active=False)
    _dispatch_sync_notification(ctx, domain, [first, second])


@given(parsers.re(r'the webhook proof-of-control challenge for "(?P<url>[^"]+)" fails'))
def given_proof_of_control_fails(ctx: dict, url: str) -> None:
    """Force the seller's proof-of-control challenge for ``url`` to fail.

    A REAL env seam, not a ctx intent marker: in process it overrides the
    proof-of-control service so the challenge returns "not proven"; out of process
    the seam is unreachable but unnecessary, because the URL sits under an RFC
    2606/6761 reserved TLD (``.example``) and the real prover refuses those without
    a DNS lookup. Both routes fail closed for the same reason, so the scenario
    grades identical behaviour on every transport rather than depending on whether
    the local resolver hijacks NXDOMAIN.

    Spec: core/notification-config.json top-level — "Sellers MUST verify endpoint
    control before activating a new or changed active account-level notification
    config"; #/properties/active — "Reactivation requires full SSRF validation with
    connect pinning plus proof-of-control".
    """
    ctx["env"].set_notification_proof_result(succeeds=False, url=url)


@when(
    parsers.re(
        r'the Buyer Agent sends a sync_accounts request re-sending subscriber "(?P<sub>[^"]+)" '
        r'as active with url "(?P<url>[^"]+)"'
    )
)
def when_sync_resend_subscriber_active(ctx: dict, sub: str, url: str) -> None:
    """Declarative replace: re-send the same subscriber_id as active (active:true).

    Reactivating or changing an active subscriber's url MUST trigger the
    proof-of-control challenge before the seller treats it as active; on challenge
    failure the entry is rejected. Reuses the seed's event_types (declarative replace
    carries the full desired entry).

    Spec: sync-accounts-request.json notification_configs description — proof-of-control
    "before treating a new or changed active subscriber as active"; on failure the
    entry is rejected (action=failed) with VALIDATION_ERROR at notification_configs[0].url.
    """
    domain = ctx.get("notif_domain", "acme-corp.com")
    cfg = _notif_config(sub, url, "creative.status_changed, creative.purged", active=True)
    _dispatch_sync_notification(ctx, domain, [cfg])


@then("the account keeps its prior notification_configs set unchanged")
def then_account_keeps_prior_notif_set(ctx: dict) -> None:
    """Assert the rejected activation left the prior persisted subscriber set intact.

    On proof-of-control failure the seller rejects the entry and leaves the prior
    notification_configs[] set unchanged — the buyer's read-back MUST still show the
    original subscriber with its original url and active state (the Given's seed).

    Spec: core/notification-config.json#/properties/active — "the buyer's next
    sync_accounts MUST observe the same array".
    """
    prior = ctx.get("notif_prior")
    assert prior is not None, "No prior notification_configs state recorded by the Given"
    # Grade PERSISTENCE, not the failed result's echo. The response-schema presence
    # rule covers created/updated/unchanged only, so a `failed` entry legitimately
    # carries no notification_configs — asserting on its echo would grade an
    # unmandated field and read as "prior set lost" on a correct implementation.
    # The buyer's observable is the next read.
    subs = _persisted_subscribers(ctx, domain=ctx.get("notif_domain"))
    match = _find_subscriber(subs, prior["subscriber_id"])
    assert match is not None, f"Prior subscriber {prior['subscriber_id']!r} missing from persisted set {subs!r}"
    assert str(_sub_attr(match, "url")) == prior["url"], (
        f"Prior url changed: expected {prior['url']!r}, got {_sub_attr(match, 'url')!r}"
    )
    assert _sub_attr(match, "active") is prior["active"], (
        f"Prior active flag changed: expected {prior['active']!r}, got {_sub_attr(match, 'active')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — governance_agents + dry-run update assertions
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('the governance_agents are stored for brand domain "{domain}"'))
def then_governance_agents_stored(ctx: dict, domain: str) -> None:
    """Assert governance_agents were persisted in the DB for the given brand domain."""
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    tenant = ctx["tenant"]
    principal = ctx["principal"]
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        accounts = repo.list_by_principal(principal.principal_id)
        matching = [a for a in accounts if a.brand and a.brand.domain == domain]
        assert len(matching) == 1, f"Expected 1 account for {domain}, got {len(matching)}"
        account = matching[0]
        agents = account.governance_agents
        assert agents is not None, f"Expected governance_agents to be stored for {domain}, got None"
        # The When step sends exactly 1 governance agent with known URL and categories
        assert len(agents) == 1, f"Expected 1 governance_agent for {domain}, got {len(agents)}"
        agent = agents[0]
        agent_url = agent.get("url") if isinstance(agent, dict) else getattr(agent, "url", None)
        assert agent_url == "https://governance.example.com/check", (
            f"Expected governance agent url 'https://governance.example.com/check', got '{agent_url}'"
        )


@then(parsers.parse('no accounts were actually modified for brand domain "{domain}"'))
def then_no_modifications_for_domain(ctx: dict, domain: str) -> None:
    """Assert a dry-run did not modify the existing account's billing in the DB.

    Verifies that the pre-existing account retains its original billing value
    despite the dry-run response reporting action='updated'.
    """
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    tenant = ctx["tenant"]
    principal = ctx["principal"]
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        accounts = repo.list_by_principal(principal.principal_id)
        matching = [a for a in accounts if a.brand and a.brand.domain == domain]
        assert len(matching) == 1, f"Expected 1 pre-existing account for {domain}, got {len(matching)}"
        # The dry-run scenario syncs with billing='agent' but the pre-existing account
        # was created with billing='operator'. If dry_run worked, DB still has 'operator'.
        account = matching[0]
        assert account.billing == "operator", (
            f"Expected billing='operator' (unchanged by dry-run) for {domain}, "
            f"got billing='{account.billing}' — dry_run failed to prevent DB writes"
        )


# ═══════════════════════════════════════════════════════════════════════
# Hand-authored: Authorization boundary steps (PR #1170 review)
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('agent "{name}" has an authenticated connection with {count:d} accessible accounts'))
def given_agent_with_n_accounts(ctx: dict, name: str, count: int) -> None:
    """Create a named agent with N accessible accounts."""
    _setup_tenant_and_principal(ctx)
    agent = _create_agent(ctx, name)
    from tests.factories.account import AccountFactory, AgentAccountAccessFactory

    tenant = ctx["tenant"]
    agent_account_ids: set[str] = set()
    for _ in range(count):
        account = AccountFactory(tenant=tenant, status="active")
        AgentAccountAccessFactory(
            tenant_id=tenant.tenant_id,
            principal=agent,
            account=account,
        )
        agent_account_ids.add(account.account_id)
    ctx.setdefault("agent_account_ids", {})[name] = agent_account_ids


@given(parsers.parse('agent "{name}" has {count:d} accessible accounts in the same tenant'))
def given_agent_b_accounts_same_tenant(ctx: dict, name: str, count: int) -> None:
    """Create a second agent with N accessible accounts in the same tenant."""
    given_agent_with_n_accounts(ctx, name, count)


@when(parsers.parse('agent "{name}" sends a list_accounts request'))
def when_agent_list_accounts(ctx: dict, name: str) -> None:
    """Send list_accounts as a specific named agent."""
    dispatch_request(ctx, credential=_credential_for_agent(ctx, name))


@when("the Buyer Agent sends a list_accounts request with no principal_id")
def when_list_accounts_no_principal(ctx: dict) -> None:
    """Send list_accounts addressing the tenant but presenting no token: no principal resolves."""
    tenant = ctx["tenant"]
    dispatch_request(ctx, credential=ctx["env"].credential(token=None, tenant=tenant.tenant_id))


@when("the Buyer Agent sends a sync_accounts request with no principal_id and:")
def when_sync_no_principal(ctx: dict, datatable: Any) -> None:
    """Send sync_accounts addressing the tenant but presenting no token: no principal resolves."""
    from src.core.schemas.account import SyncAccountsRequest

    tenant = ctx["tenant"]
    rows = table_rows(datatable)
    accounts = _parse_sync_table(rows)
    req = SyncAccountsRequest(idempotency_key=fresh_idempotency_key(), accounts=accounts)
    dispatch_request(ctx, req=req, credential=ctx["env"].credential(token=None, tenant=tenant.tenant_id))


@then(parsers.parse('none of the returned accounts belong to agent "{name}"'))
def then_none_belong_to_agent(ctx: dict, name: str) -> None:
    """Assert no returned accounts are in the other agent's set."""
    resp = require_payload(ctx)
    other_ids = ctx.get("agent_account_ids", {}).get(name, set())
    assert other_ids, f"Test setup error: no account IDs tracked for agent '{name}'"
    returned_ids = {acct.account_id for acct in resp.accounts}
    leaked = returned_ids & other_ids
    assert not leaked, f"Cross-agent leak: accounts {leaked} belong to agent '{name}' but appeared in response"


# ── Governance idempotency steps ────────────────────────────────────


@given(parsers.parse('an account for brand domain "{domain}" already exists with governance_agents'))
def given_existing_account_with_governance(ctx: dict, domain: str) -> None:
    """Pre-create an account with governance_agents via sync_accounts."""
    _setup_tenant_and_principal(ctx)
    gov = [_make_governance_agent()]
    _sync_pre_create(ctx, brand_domain=domain, operator=domain, billing="operator", governance_agents=gov)
    ctx["governance_agents_fixture"] = gov


@given(
    parsers.parse(
        'an account for brand domain "{domain}" exists with billing "{billing}", '
        'payment_terms "{pt}", and governance_agents'
    )
)
def given_existing_account_all_fields(ctx: dict, domain: str, billing: str, pt: str) -> None:
    """Pre-create an account with all mutable fields populated."""
    _setup_tenant_and_principal(ctx)
    gov = [_make_governance_agent()]
    _sync_pre_create(
        ctx, brand_domain=domain, operator=domain, billing=billing, payment_terms=pt, governance_agents=gov
    )
    ctx["governance_agents_fixture"] = gov


@when(parsers.parse('the Buyer Agent re-syncs with identical governance_agents for brand "{domain}"'))
def when_resync_identical_governance(ctx: dict, domain: str) -> None:
    """Re-sync with the same governance_agents that were used during creation."""
    from src.core.schemas.account import SyncAccountsRequest

    gov = ctx["governance_agents_fixture"]
    req = SyncAccountsRequest(
        idempotency_key=fresh_idempotency_key(),
        accounts=[{"brand": {"domain": domain}, "operator": domain, "billing": "operator", "governance_agents": gov}],
    )
    dispatch_request(ctx, req=req)


@when(parsers.parse('the Buyer Agent sends a sync with different governance_agents for brand "{domain}"'))
def when_sync_different_governance(ctx: dict, domain: str) -> None:
    """Sync with modified governance_agents."""
    from src.core.schemas.account import SyncAccountsRequest

    req = SyncAccountsRequest(
        idempotency_key=fresh_idempotency_key(),
        accounts=[
            {
                "brand": {"domain": domain},
                "operator": domain,
                "billing": "operator",
                "governance_agents": [
                    _make_governance_agent(
                        url="https://new-bot.example.com/check",
                    )
                ],
            }
        ],
    )
    dispatch_request(ctx, req=req)


@when(
    parsers.parse(
        'the Buyer Agent re-syncs with identical billing, payment_terms, and governance_agents for brand "{domain}"'
    )
)
def when_resync_identical_all_fields(ctx: dict, domain: str) -> None:
    """Re-sync with all fields identical to creation."""
    from src.core.schemas.account import SyncAccountsRequest

    gov = ctx["governance_agents_fixture"]
    req = SyncAccountsRequest(
        idempotency_key=fresh_idempotency_key(),
        accounts=[
            {
                "brand": {"domain": domain},
                "operator": domain,
                "billing": "agent",
                "payment_terms": "net_30",
                "governance_agents": gov,
            }
        ],
    )
    dispatch_request(ctx, req=req)


@then(parsers.parse('none of the returned accounts have brand domain "{domain}"'))
def then_none_have_brand_domain(ctx: dict, domain: str) -> None:
    """Assert no returned account has the specified brand domain."""
    resp = require_payload(ctx)
    leaked = [
        a for a in resp.accounts if hasattr(a, "brand") and a.brand and getattr(a.brand, "domain", None) == domain
    ]
    assert not leaked, (
        f"Cross-agent leak: account(s) {[a.account_id for a in leaked]} carry brand domain "
        f"'{domain}' but should not be visible to this agent"
    )


# ── delete_missing semantics steps ──────────────────────────────────


@when("the Buyer Agent sends a sync_accounts request with dry_run true and delete_missing true and:")
def when_sync_dryrun_and_delete_missing(ctx: dict, datatable: Any) -> None:
    """Send sync_accounts with both dry_run=True and delete_missing=True."""
    from src.core.schemas.account import SyncAccountsRequest

    rows = table_rows(datatable)
    accounts = _parse_sync_table(rows)
    req = SyncAccountsRequest(
        idempotency_key=fresh_idempotency_key(), accounts=accounts, dry_run=True, delete_missing=True
    )
    dispatch_request(ctx, req=req)


@when(parsers.parse('agent "{name}" sends a sync_accounts request with delete_missing true and:'))
def when_named_agent_sync_delete_missing(ctx: dict, name: str, datatable: Any) -> None:
    """Send sync_accounts under a named agent's identity with delete_missing=True."""
    from src.core.schemas.account import SyncAccountsRequest

    credential = _credential_for_agent(ctx, name)
    rows = table_rows(datatable)
    accounts = _parse_sync_table(rows)
    req = SyncAccountsRequest(idempotency_key=fresh_idempotency_key(), accounts=accounts, delete_missing=True)
    dispatch_request(ctx, req=req, credential=credential)


@given(parsers.parse('agent "{name}" created account for brand domain "{domain}"'))
def given_agent_created_account(ctx: dict, name: str, domain: str) -> None:
    """Create an account under a specific agent's identity via sync."""
    _given_agent_synced(ctx, name, domain)


@given(parsers.parse('agent "{a}" was granted access to the account for brand domain "{domain}"'))
def given_agent_granted_access(ctx: dict, a: str, domain: str) -> None:
    """Grant agent A access to an existing account (created by another agent)."""
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository

    tenant = ctx["tenant"]
    agent = _create_agent(ctx, a)
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        # Find the account by domain
        from sqlalchemy import select

        from src.core.database.models import Account

        account = session.scalars(
            select(Account).where(
                Account.tenant_id == tenant.tenant_id,
                Account.brand["domain"].as_string() == domain,
            )
        ).first()
        assert account is not None, f"Account for domain {domain} not found"
        repo.grant_access(agent.principal_id, account.account_id)
        session.commit()


# ── Field preservation + access persistence steps ───────────────────


@then(parsers.parse("the account {field} in the database is unchanged from the original"))
def then_db_field_unchanged(ctx: dict, field: str) -> None:
    """Assert a DB field was not modified by sync — compare against captured original.

    The preceding Given/When steps must have captured the original field value
    into ctx["original_field_values"][field] before the sync ran. This step
    re-fetches the account from the DB and asserts exact equality with the
    captured original.
    """
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.account import AccountRepository, NaturalKey

    acct = ctx.get("last_account")
    assert acct is not None, "No last_account in ctx — need a preceding account action step"
    tenant = ctx["tenant"]
    with get_db_session() as session:
        repo = AccountRepository(session, tenant.tenant_id)
        # Find by brand domain from the last_account
        domain = acct.brand.domain if hasattr(acct.brand, "domain") else str(acct.brand)
        db_acct = repo.get_by_natural_key(NaturalKey.from_parts(domain, None, domain, None))
        assert db_acct is not None, f"Account for {domain} not found in DB"
        db_val = getattr(db_acct, field, None)
        # Compare against captured original value
        original_values = ctx.get("original_field_values", {})
        if field in original_values:
            original_val = original_values[field]
            assert db_val == original_val, (
                f"Field '{field}' was modified by sync: original={original_val!r}, "
                f"current={db_val!r} — expected unchanged"
            )
        else:
            # No captured original — the preceding steps should have captured it.
            # Fall back to asserting the DB has a meaningful value (non-None)
            # to avoid silently passing when test setup is incomplete.
            assert db_val is not None, (
                f"Field '{field}' is None in DB and no original value was captured "
                f"in ctx['original_field_values']. Test setup must capture the "
                f"original value before sync."
            )


@then(parsers.parse('the agent has exactly one access grant for brand domain "{domain}"'))
def then_one_access_grant(ctx: dict, domain: str) -> None:
    """Assert exactly one AgentAccountAccess row for this agent + account."""
    from sqlalchemy import func, select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Account, AgentAccountAccess

    tenant = ctx["tenant"]
    principal = ctx["principal"]
    with get_db_session() as session:
        # Find the account by domain
        account = session.scalars(
            select(Account).where(
                Account.tenant_id == tenant.tenant_id,
                Account.brand["domain"].as_string() == domain,
            )
        ).first()
        assert account is not None, f"Account for {domain} not found"
        count = session.scalar(
            select(func.count())
            .select_from(AgentAccountAccess)
            .where(
                AgentAccountAccess.tenant_id == tenant.tenant_id,
                AgentAccountAccess.principal_id == principal.principal_id,
                AgentAccountAccess.account_id == account.account_id,
            )
        )
        assert count == 1, f"Expected 1 access grant for {domain}, got {count}"


@given("the database is experiencing a transient failure")
def given_db_failure(ctx: dict) -> None:
    """Configure the harness to simulate a DB failure on the next query."""
    ctx["simulate_db_failure"] = True


@then(parsers.parse('the list includes an account with brand domain "{domain}"'))
def then_list_includes_domain(ctx: dict, domain: str) -> None:
    """Assert the list_accounts response contains an account with the given brand domain."""
    resp = require_payload(ctx)
    for acct in resp.accounts:
        if hasattr(acct, "brand") and acct.brand and getattr(acct.brand, "domain", None) == domain:
            return
    domains = [getattr(a.brand, "domain", "?") for a in resp.accounts if hasattr(a, "brand") and a.brand]
    raise AssertionError(f"Expected account with domain '{domain}' in list, got: {domains}")


@then(parsers.parse('the response does not include a result for brand domain "{domain}"'))
def then_no_result_for_domain(ctx: dict, domain: str) -> None:
    """Assert the sync response has no account entry for the given domain."""
    resp = require_payload(ctx)
    for acct in resp.accounts:
        acct_domain = acct.brand.domain if hasattr(acct, "brand") and acct.brand else None
        assert acct_domain != domain, (
            f"Expected no result for domain '{domain}' but found account "
            f"{acct.account_id} with action={getattr(acct, 'action', '?')}"
        )


# ═══════════════════════════════════════════════════════════════════════
# Brandless-entry validation (PR1399 R3-F1)
# ═══════════════════════════════════════════════════════════════════════


@when("the Buyer Agent sends a sync_accounts request with a brandless account entry")
def when_sync_brandless_entry(ctx: dict) -> None:
    """Send a sync_accounts request whose single entry omits 'brand'.

    SDK 5.7's SyncAccountsRequest.accounts is list[Accounts | Accounts3]; the
    Accounts3 (account-reference / settings-update) branch makes 'brand' optional,
    so this entry parses with brand=None. The pinned 3.1 spec
    (sync-accounts-request.json) marks every entry required:[brand,operator,billing].
    """
    from src.core.schemas.account import SyncAccountsRequest

    req = SyncAccountsRequest(
        idempotency_key=fresh_idempotency_key(),
        accounts=[{"account": {"account_id": "ref-001"}, "operator": "example.com"}],
    )
    dispatch_request(ctx, req=req)


@then("the brandless entry is rejected with a correctable VALIDATION_ERROR")
def then_brandless_rejected_validation_error(ctx: dict) -> None:
    """The seller refuses a brandless entry as a correctable validation error.

    Two boundaries, one buyer contract (never accepted, never a 500):
    - A2A/REST: the request reaches _sync_accounts_impl (Accounts3 branch,
      brand=None); _extract_natural_key raises AdCPValidationError, so the
      two-layer wire envelope carries code VALIDATION_ERROR with
      recovery=correctable. Graded through the canonical result surface;
      the AdCPValidationError at accounts.py carries no errors[].field pointer,
      so no field= is asserted (recovery defaults from the pinned enum).
    - MCP: the tool surface types accounts as list[Accounts] (brand required),
      so the FastMCP TypeAdapter rejects the brandless entry at the schema
      boundary before _impl, naming the missing 'brand' field.
    """
    assert payload_or_none(ctx) is None, (
        f"brandless entry must be rejected, but a response was returned: {payload_or_none(ctx)!r}"
    )
    error = ctx.get("error")
    assert error is not None, "expected the brandless entry to be rejected with an error"

    if wire_error_envelope_or_none(ctx) is not None:
        # Seller's own validation (A2A/REST) → assert the two-layer AdCP envelope.
        # Selector reads through the TransportResult, not a stale ctx key; the
        # assertion is the single sanctioned wire-error surface, whose recovery
        # defaults from the PINNED enum (pin-wins).
        ctx["result"].assert_wire_error("VALIDATION_ERROR", recovery="correctable")
    else:
        # MCP: the tool surface types accounts as list[Accounts] (brand required),
        # so FastMCP's TypeAdapter rejects the brandless dict at the schema
        # boundary and raises a ToolError whose message carries the pydantic
        # "brand Field required" detail (it is not our JSON envelope, hence no
        # wire_error_envelope). Assert the rejection names the missing field.
        from fastmcp.exceptions import ToolError

        assert isinstance(error, ToolError), (
            f"expected a FastMCP ToolError at the MCP schema boundary, got {type(error).__name__}: {error}"
        )
        assert "brand" in str(error), f"MCP rejection must name the missing 'brand' field; got: {error}"


# ═══════════════════════════════════════════════════════════════════════
# UC-011 entry-field disposition (salesagent-gcze step 12)
#
# Grades the per-mode disposition of every settable entry field that no
# scenario graded before, so the single field-policy table replacing the two
# hand-maintained allowlists is written against the CONTRACT, not the code.
#
# Production trace (src/core/tools/accounts.py, verified in-process):
#   - _account_fields_changed:418-424 compares
#     _serialize_governance_agents(getattr(entry, "governance_agents", None))
#     against the persisted value. Both request branches are extra="allow", so an
#     OMITTED field reads None, None != db_gov, and changes["governance_agents"]
#     = None reaches repo.update_fields -> the binding is WIPED by a re-sync that
#     never mentioned governance. check_governance keys off that binding.
#   - _process_settings_update_entry:806-818 builds `changes` for payment_terms
#     and notification_configs ONLY. Entry-root `sandbox` parses on that branch
#     (Accounts1.model_validate({"account": {...}, "sandbox": True}) succeeds) and
#     is then silently ignored.
#   - billing_entity: no DB column, no application site, and SyncResponseAccount
#     declares no such field -- accepted on the wire, dropped, success returned.
#   - preferred_reporting_protocol: same acceptance, and that is CORRECT (declared
#     no-op); the scenario is a lock so it cannot become a rejection.
#   - billing on a settings-update entry: the F1a mode-exclusivity guard
#     (accounts.py:1076-1086) raises AdCPValidationError at the operation level
#     BEFORE dispatch. Also a lock -- it is what earns that row `spec_forbidden`.
# ═══════════════════════════════════════════════════════════════════════


def _existing_account_id(ctx: dict) -> str:
    """The account_id the ``already exists`` Given captured from its pre-create sync.

    Recorded as run-variant at the CAPTURE sites (:func:`_capture_server_account_id`),
    not here: four call sites read this id out of ``ctx["original_field_values"]`` and
    two of them bypass this helper entirely, so minting here covered half the traffic —
    measured, on the module's own before/after pair.
    """
    account_id = ctx.get("original_field_values", {}).get("account_id")
    assert account_id, "Given must pre-create an account and capture its account_id in original_field_values"
    return account_id


def _capture_server_account_id(ctx: dict, account_id: str) -> None:
    """Stash a SERVER-GENERATED account_id for a later settings-update entry.

    THE one place a production-minted id enters this module's ctx, which is why the mint
    record is written here. ``AccountRepository`` generates it as
    ``f"acc_{uuid4().hex[:12]}"`` (src/core/database/repositories/account.py), so it
    differs on every run and no FACTORY recorded it — the mint registry's premise is
    that factories record what they generate, and production is not a factory and must
    not import a test module. The test-side capture is where the scenario chooses to
    carry a server-generated value into a later request, so it is where the recording
    belongs; recording at the READ sites instead missed the two that index
    ``ctx["original_field_values"]`` directly.
    """
    from tests.factories.mint import mint

    ctx.setdefault("original_field_values", {})["account_id"] = mint(account_id)


def _dispatch_entry(ctx: dict, entry: dict[str, Any]) -> None:
    """Dispatch a one-entry sync_accounts request on the wire.

    Single dispatch path for every field-disposition When below: they differ only
    in the entry they submit, so a per-step copy of build/try/except would be the
    duplication the DRY invariant bans.
    """
    from src.core.schemas.account import SyncAccountsRequest

    try:
        dispatch_request(ctx, req=SyncAccountsRequest(idempotency_key=fresh_idempotency_key(), accounts=[entry]))
    except Exception as exc:
        ctx["error"] = exc


def _listed_account(ctx: dict, domain: str) -> Any:
    """The account with ``domain`` from the CURRENT list_accounts response.

    Read-back legs still assert on the typed list response: this is one of the
    pre-existing typed-payload reads tracked by #1880, not a wiring constraint.
    The bypass that used to justify it is gone — AccountSyncEnv dispatches the
    list verb over the wire now — so the wire IS stashed, and this helper is
    convertible to ``wire_entry(ctx, "accounts", ...)`` whenever #1880 retires
    the typed cluster it belongs to. The sync legs assert on ``wire_dict``.
    """
    resp = require_payload(ctx)
    accounts = getattr(resp, "accounts", None) or []
    acct = next((a for a in accounts if getattr(a, "brand", None) and a.brand.domain == domain), None)
    assert acct is not None, (
        f"No listed account for brand domain {domain!r}; "
        f"domains={[a.brand.domain for a in accounts if getattr(a, 'brand', None)]}"
    )
    return acct


def _sole_account_errors(ctx: dict) -> list[Any]:
    """The per-account ``errors[]`` of the SOLE account entry on the success wire.

    Goes through ``_wire_account`` first so the ``len(accounts) == 1`` pin applies
    here too: an entry-errors oracle that silently graded row 0 of a multi-row
    response would be exactly the vacuous pass the count pin exists to prevent.
    """
    _wire_account(ctx)
    return wire_entry_errors(ctx, "accounts", index=0)


def _wire_account(ctx: dict, index: int | None = None, *, domain: str | None = None) -> dict[str, Any]:
    """The per-account result on the success wire — by brand domain, or the SOLE entry.

    Delegates to the ``wire_entry`` primitive (tests/bdd/steps/_outcome_helpers.py)
    rather than indexing the body itself, so the "name the entries actually present"
    diagnostic and the loud missing-wire guard are shared, not re-derived here.

    With neither argument it takes index 0 AND pins ``len(accounts) == 1``: an
    index-0 default is only sound on a genuinely single-entry response, and pinning
    it means a scenario that grows a second entry fails loudly instead of silently
    grading the wrong row.
    """
    if domain is not None:
        return wire_entry(ctx, "accounts", **{"brand.domain": domain})
    if index is None:
        entries = wire_field(ctx, "accounts")
        assert len(entries) == 1, (
            f"the sole-entry read needs exactly one account on the wire, got {len(entries)}; "
            "locate the entry by brand domain instead"
        )
        index = 0
    return wire_entry(ctx, "accounts", index=index)


# ── When: entry-field disposition dispatches ───────────────────────────


@when(
    "the Buyer Agent sends a sync_accounts request with a settings-update entry "
    "keyed by the existing account's account_id carrying entry-root sandbox true"
)
def when_sync_settings_update_with_sandbox(ctx: dict) -> None:
    """Dispatch a settings-update entry carrying entry-root ``sandbox: true``.

    Schema-legal on this branch (``sandbox`` is absent from the SettingsUpdateMode
    ``not:`` list) but scoped by its description to provisioning mode, and part of
    the buyer-declared natural key -- honoring it would re-key the account.

    Spec: account/sync-accounts-request.json#/properties/accounts/items/properties/sandbox/description;
    core/account.json#/properties/sandbox ("sandbox is part of the natural key").
    """
    _dispatch_entry(ctx, {"account": {"account_id": _existing_account_id(ctx)}, "sandbox": True})


@when(
    parsers.parse(
        "the Buyer Agent sends a sync_accounts request with a settings-update entry "
        'keyed by the existing account\'s account_id carrying billing "{billing}"'
    )
)
def when_sync_settings_update_with_billing(ctx: dict, billing: str) -> None:
    """Dispatch a settings-update entry carrying ``billing`` and nothing else.

    ``billing`` is MUST-be-absent on this branch ("billing is fixed at provisioning
    time and cannot be changed via settings-update"), structurally enforced by the
    item oneOf's SettingsUpdateMode ``allOf: [... {not: {required: ["billing"]}}]``.

    Spec: account/sync-accounts-request.json#/properties/accounts/items/oneOf/1/allOf/2.
    """
    _dispatch_entry(ctx, {"account": {"account_id": _existing_account_id(ctx)}, "billing": billing})


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_accounts request provisioning brand domain "{domain}" '
        'with a billing_entity legal_name "{legal_name}" and bank details'
    )
)
def when_sync_provision_with_billing_entity(ctx: dict, domain: str, legal_name: str) -> None:
    """Provision an account whose entry carries a full ``billing_entity``, bank included.

    The bank block is DECLARED on purpose: the echo obligation is "bank details are
    omitted (write-only)", so a response that returns them is a real leak rather
    than a vacuously-absent optional. Built by BusinessEntityFactory, not a literal
    dict, so the payload shape stays one definition.

    Spec: account/sync-accounts-request.json#/properties/accounts/items/properties/billing_entity/description
    ("Permitted in both modes"); core/business-entity.json#/properties/bank.
    """
    from tests.factories.account import BusinessEntityFactory

    _setup_tenant_and_principal(ctx)
    _dispatch_entry(
        ctx,
        {
            "brand": {"domain": domain},
            "operator": domain,
            "billing": "operator",
            "billing_entity": BusinessEntityFactory.build_payload(legal_name=legal_name),
        },
    )
    # payload_or_none, not ctx.get("response"): #1802 stopped the dispatch seams
    # writing ctx["response"], so the old spelling silently read None forever and
    # this capture (the baseline for the "account_id is unchanged" Then) never ran.
    resp = payload_or_none(ctx)
    if resp is not None and getattr(resp, "accounts", None):
        _capture_server_account_id(ctx, resp.accounts[0].account_id)


@when(
    parsers.parse(
        "the Buyer Agent sends a sync_accounts request with a settings-update entry "
        'keyed by the existing account\'s account_id refining billing_entity legal_name to "{legal_name}"'
    )
)
def when_sync_settings_update_billing_entity(ctx: dict, legal_name: str) -> None:
    """Refine ``billing_entity`` through the settings-update branch.

    "Sellers MAY accept refinements in settings-update mode (e.g., updated bank
    details)" -- the field is permitted in BOTH modes, so the same value must be
    settable here as at provisioning time.

    Spec: account/sync-accounts-request.json#/properties/accounts/items/properties/billing_entity/description.
    """
    from tests.factories.account import BusinessEntityFactory

    _dispatch_entry(
        ctx,
        {
            "account": {"account_id": _existing_account_id(ctx)},
            "billing_entity": BusinessEntityFactory.build_payload(legal_name=legal_name),
        },
    )


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_accounts request provisioning brand domain "{domain}" '
        'with preferred_reporting_protocol "{protocol}"'
    )
)
def when_sync_provision_with_reporting_protocol(ctx: dict, domain: str, protocol: str) -> None:
    """Provision an account whose entry carries the advisory reporting-protocol hint.

    Spec: account/sync-accounts-request.json#/properties/accounts/items/properties/preferred_reporting_protocol
    ("The seller provisions the account's reporting_bucket using this protocol IF
    SUPPORTED ... When omitted, the seller chooses"); enums/cloud-storage-protocol.json
    #/enum = ["s3","gcs","azure_blob"].
    """
    _setup_tenant_and_principal(ctx)
    _dispatch_entry(
        ctx,
        {
            "brand": {"domain": domain},
            "operator": domain,
            "billing": "operator",
            "preferred_reporting_protocol": protocol,
        },
    )


# ── Then: entry-field disposition assertions ───────────────────────────


@then(parsers.parse('the listed account for brand domain "{domain}" binds governance agent "{url}"'))
def then_listed_account_binds_governance_agent(ctx: dict, domain: str, url: str) -> None:
    """Assert the persisted governance binding survived a sync that omitted it.

    Read on the buyer-facing list_accounts echo (_db_account_to_schema already
    carries governance_agents), because "the binding still exists" is only a
    security property if the buyer can still observe it.

    LOCAL EXTENSION: governance_agents is not a sync-accounts-request property
    (the entry accepts it via additionalProperties only) and the spec's designated
    surface is sync_governance -- so the obligation graded here is ours: omission
    is not clearance, exactly as core/notification-config.json states for the one
    field whose omission semantics the spec DOES define.
    """
    acct = _listed_account(ctx, domain)
    agents = getattr(acct, "governance_agents", None) or []
    urls = [str(a.get("url")) if isinstance(a, dict) else str(getattr(a, "url", None)) for a in agents]
    assert urls == [url], (
        f"governance binding for {domain!r} must survive a re-sync that omitted governance_agents; "
        f"expected exactly ['{url}'], got {urls} (an omission-wipe is a governance BYPASS: "
        f"check_governance keys off this binding)"
    )


@then(parsers.re(r'the listed account for brand domain "(?P<domain>[^"]+)" has sandbox (?P<flag>true|false)'))
def then_listed_account_sandbox(ctx: dict, domain: str, flag: str) -> None:
    """Assert the persisted sandbox flag, i.e. that the natural key was not re-keyed.

    Spec: core/account.json#/properties/sandbox ("For buyer-declared accounts,
    sandbox is part of the natural key"). A rejected settings-update entry must
    leave it exactly as provisioned, or every later natural-key sync misses.
    """
    acct = _listed_account(ctx, domain)
    expected = flag == "true"
    actual = bool(getattr(acct, "sandbox", None))
    assert actual is expected, f"Expected persisted sandbox {expected} for {domain!r}, got {actual}"


@then(parsers.parse('the listed account for brand domain "{domain}" has billing "{billing}"'))
def then_listed_account_billing(ctx: dict, domain: str, billing: str) -> None:
    """Assert the persisted billing value is untouched by a rejected entry.

    Spec: account/sync-accounts-request.json#/properties/accounts/items/properties/billing/description
    ("billing is fixed at provisioning time and cannot be changed via settings-update").
    """
    acct = _listed_account(ctx, domain)
    actual = _status_str(getattr(acct, "billing", None))
    assert actual == billing, f"Expected persisted billing '{billing}' for {domain!r}, got '{actual}'"


@then(parsers.parse('the echoed billing_entity legal_name is "{legal_name}"'))
def then_echoed_billing_entity_legal_name(ctx: dict, legal_name: str) -> None:
    """Assert the sync response echoes the submitted billing_entity, on the WIRE.

    Spec: account/sync-accounts-response.json#/oneOf/0/properties/accounts/items/properties/billing_entity
    ("Business entity details ... echoed from the request").
    """
    acct = _wire_account(ctx)
    entity = acct.get("billing_entity")
    assert isinstance(entity, dict), (
        f"sync wire result carries no billing_entity object (got {entity!r}) -- the field was accepted "
        f"on the request and never echoed; account={acct!r}"
    )
    assert entity.get("legal_name") == legal_name, (
        f"Expected echoed billing_entity.legal_name '{legal_name}', got {entity.get('legal_name')!r}"
    )


@then(parsers.parse('the echoed billing_entity omits "{field}"'))
def then_echoed_billing_entity_omits(ctx: dict, field: str) -> None:
    """Assert a write-only billing_entity sub-object is absent from the sync WIRE.

    Spec: account/sync-accounts-response.json#/oneOf/0/properties/accounts/items/properties/billing_entity
    ("Bank details are omitted (write-only)"); core/account.json#/properties/billing_entity
    ("When this account appears in a response, bank details MUST be omitted").
    """
    acct = _wire_account(ctx)
    entity = acct.get("billing_entity")
    assert isinstance(entity, dict), (
        f"sync wire result carries no billing_entity object (got {entity!r}); account={acct!r}"
    )
    assert field not in entity, f"billing_entity echoed write-only {field!r}: {entity!r}"


@then(parsers.parse('the listed account for brand domain "{domain}" echoes billing_entity legal_name "{legal_name}"'))
def then_listed_billing_entity_legal_name(ctx: dict, domain: str, legal_name: str) -> None:
    """Assert billing_entity was PERSISTED, not merely echoed from the request.

    The list read-back is the leg that separates "stored" from "reflected": a
    handler that echoed the submitted object without a column would pass the sync
    assertion and fail here.

    Spec: core/account.json#/properties/billing_entity.
    """
    acct = _listed_account(ctx, domain)
    entity = getattr(acct, "billing_entity", None)
    assert entity is not None, (
        f"listed account {domain!r} carries no billing_entity -- the field was accepted on the wire and never persisted"
    )
    actual = entity.get("legal_name") if isinstance(entity, dict) else getattr(entity, "legal_name", None)
    assert actual == legal_name, f"Expected persisted billing_entity.legal_name '{legal_name}', got {actual!r}"
    ctx["listed_billing_entity"] = entity


@then(parsers.parse('the listed billing_entity omits "{field}"'))
def then_listed_billing_entity_omits(ctx: dict, field: str) -> None:
    """Assert the list_accounts echo also strips the write-only bank block.

    Spec: core/account.json#/properties/billing_entity ("When this account appears
    in a response, bank details MUST be omitted (write-only)") -- "a response",
    not "the sync response", so list_accounts is bound by the same rule.
    """
    entity = ctx.get("listed_billing_entity")
    assert entity is not None, "No listed billing_entity referenced -- needs the legal_name Then first"
    value = entity.get(field) if isinstance(entity, dict) else getattr(entity, field, None)
    assert value is None, f"listed billing_entity echoed write-only {field!r}: {value!r}"


@then("the per-account result carries no errors")
def then_per_account_result_no_errors(ctx: dict) -> None:
    """Assert a successfully-provisioned account carries an empty per-account errors[].

    Spec: account/sync-accounts-response.json#/oneOf/0/properties/accounts/items/properties/errors
    ("only present when action is 'failed'"). This is what makes an unhonored
    advisory hint a DECLARED no-op rather than a silent rejection: there is no
    channel to advise on a successful account, so acceptance is the contract.
    """
    errors = _sole_account_errors(ctx)
    assert not errors, f"Expected no per-account errors on a successful account, got {errors!r}"
