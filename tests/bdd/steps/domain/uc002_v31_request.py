"""UC-002 create_media_buy steps: v3.1 idempotency states, envelope shapes, request fields.

Every step here builds on the shared create request (``_ensure_request_defaults`` or the
idempotency builder in ``uc002_create_media_buy``) and dispatches through the shared When,
so the scenarios run the same request on a2a, mcp, rest and e2e_rest. State a live server
must read (a prior buy under a key, an account) is seeded as committed rows through the
factories, which the e2e server reads from the same database.

Imports of other step modules are local to the functions: ``uc002_create_media_buy``
imports this module's outcome helpers at load time, and a step function imported at module
level would also register as a second definition of its sentence here.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from pytest_bdd import given, parsers, then

from tests.bdd.steps._outcome_helpers import WIRE_MISSING, wire_absent, wire_field, wire_lookup

# ═══════════════════════════════════════════════════════════════════════
# Idempotency: prior state under the request's key
# ═══════════════════════════════════════════════════════════════════════


def _seed_prior_buy_under_key(ctx: dict, *, created_at: datetime) -> None:
    """Commit a media buy carrying the pending request's idempotency key and no cache row.

    Scoped exactly as production scopes the key -- (tenant, principal, account) -- with the
    account the request will name, so the request's own insert hits the
    ``idx_media_buys_idempotency_key`` backstop and production decides what the collision
    means from this row's age. No ``idempotency_attempts`` row is written: inside the replay
    window that is a winner whose cache write has not landed, past it an evicted entry.
    """
    from tests.factories import MediaBuyFactory

    env = ctx["env"]
    key = ctx["request_kwargs"]["idempotency_key"]
    MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        account_id=env.setup_default_account().account_id,
        idempotency_key=key,
        status="active",
        created_at=created_at,
    )
    env._commit_factory_data()


@given("a prior request for the same (seller, account, idempotency_key) pair is still in flight")
def given_prior_request_in_flight(ctx: dict) -> None:
    """A same-key buy committed moments ago whose cached response has not been written."""
    _seed_prior_buy_under_key(ctx, created_at=datetime.now(UTC))


@given(
    "the (seller, account, idempotency_key) pair was recorded but its cached response expired past replay_ttl_seconds"
)
def given_prior_request_expired(ctx: dict) -> None:
    """A same-key buy older than the declared replay window, its cache entry evicted.

    An hour past the TTL, well clear of the pin's +/-60s clock-skew tolerance at the
    boundary (security.mdx Idempotency rule 6).
    """
    from src.core.idempotency_policy import DEFAULT_REPLAY_TTL

    _seed_prior_buy_under_key(ctx, created_at=datetime.now(UTC) - DEFAULT_REPLAY_TTL - timedelta(hours=1))


@given(parsers.parse('a media buy was already created for the same seller with idempotency_key "{key}"'))
def given_media_buy_created_under_key(ctx: dict, key: str) -> None:
    """Build the request under ``key`` and perform a REAL first create with it.

    The two existing steps, composed: the request builder and the first create that records
    ``first_media_buy_id`` for the replay Thens. The later Givens of the scenario then vary
    that same request.
    """
    from tests.bdd.steps.domain.uc002_create_media_buy import (
        given_media_buy_already_created_same_key,
        given_valid_request_with_idempotency_key,
    )

    given_valid_request_with_idempotency_key(ctx, key)
    given_media_buy_already_created_same_key(ctx)


def _reordered(value: Any) -> Any:
    """``value`` with the key order of every JSON object reversed, at every depth."""
    if isinstance(value, dict):
        return {k: _reordered(value[k]) for k in reversed(list(value))}
    if isinstance(value, list):
        return [_reordered(item) for item in value]
    return value


@given(
    parsers.parse(
        'a create_media_buy request with idempotency_key "{key}" whose payload differs only in field ordering'
    )
)
def given_request_reordered(ctx: dict, key: str) -> None:
    """Resend the first create's request with every object's key order reversed."""
    original = ctx["request_kwargs"]
    assert original["idempotency_key"] == key, (
        f"the reordered request must reuse the first create's key {original['idempotency_key']!r}, got {key!r}"
    )
    reordered = _reordered(original)
    assert reordered == original, "reordering changed a value, not just an order"
    assert list(reordered) != list(original), "the request has one key; reordering it changes nothing"
    ctx["request_kwargs"] = reordered


@given("the new request payload diverges from the prior request payload")
def given_request_payload_diverges(ctx: dict) -> None:
    """Change one buyer-authored field of the first create's request, keeping its key."""
    kwargs = ctx["request_kwargs"]
    assert "first_media_buy_id" in ctx, "a prior create under this key must run before its payload can diverge"
    diverged = "PO-IDEMPOTENCY-DIVERGED-002"
    assert kwargs.get("po_number") != diverged
    kwargs["po_number"] = diverged


# ═══════════════════════════════════════════════════════════════════════
# Request fields
# ═══════════════════════════════════════════════════════════════════════


def _create_request(ctx: dict) -> dict[str, Any]:
    """The shared valid create request, built once per scenario."""
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    return _ensure_request_defaults(ctx)


def _default_account_named_by_request(ctx: dict) -> Any:
    """The env's default account, refusing to run unless the shared request names it.

    A Given that states something about "the account" holds it on the default account's
    row; a request naming another account would grade a row the Given never touched.
    """
    account = ctx["env"].setup_default_account()
    named = _create_request(ctx)["account"]
    assert named == {"account_id": account.account_id}, (
        f"the request names {named!r}, not the default account {account.account_id!r}"
    )
    return account


_STATUS_CONDITIONS = (
    "creatives missing for all packages",
    "creatives present, future flight",
    "creatives present, asap flight",
)


@given(parsers.parse('a valid create_media_buy request producing the "{condition}" condition'))
def given_request_producing_condition(ctx: dict, condition: str) -> None:
    """Shape the request so the seller's lifecycle rule has one answer.

    The base request has a future flight and no creatives. "creatives present" assigns an
    approved library creative to the package; "asap" moves the flight start to now.
    """
    from tests.bdd.steps.generic.given_media_buy import _add_creative_ids_to_package, _create_approved_creative

    assert condition in _STATUS_CONDITIONS, f"unknown condition {condition!r}; expected one of {_STATUS_CONDITIONS}"
    kwargs = _create_request(ctx)
    if condition.startswith("creatives present"):
        creative = _create_approved_creative(ctx, "cr-status-values-001")
        _add_creative_ids_to_package(ctx, [creative.creative_id])
    if condition.endswith("asap flight"):
        kwargs["start_time"] = "asap"


@given("a valid create_media_buy request with one package having paused set to true")
def given_request_package_paused(ctx: dict) -> None:
    """The single package of the base request asks to be created paused."""
    packages = _create_request(ctx)["packages"]
    assert len(packages) == 1, f"the base request carries one package, got {len(packages)}"
    packages[0]["paused"] = True


@given("a valid create_media_buy request with the plan_id field omitted")
def given_request_without_plan_id(ctx: dict) -> None:
    """The base request without plan_id, which create-media-buy-request.json marks deprecated."""
    _create_request(ctx).pop("plan_id", None)


@given("the account has no governance_agents configured")
def given_account_without_governance_agents(ctx: dict) -> None:
    """The account the request names registers no governance agent (the column is NULL).

    Written rather than assumed: the seeded default leaves the column unset, and this
    step makes the state the scenario is about explicit on the row the request resolves.
    """
    account = _default_account_named_by_request(ctx)
    account.governance_agents = None
    ctx["env"]._commit_factory_data()


@given(
    parsers.re(
        r'a valid create_media_buy request with (?P<field>agency_estimate_number|advertiser_industry) "(?P<value>[^"]*)"'
    )
)
def given_request_buy_level_string(ctx: dict, field: str, value: str) -> None:
    """Carry one buy-level string field of create-media-buy-request.json, as written.

    The alternation names the fields this sentence may set, so an unknown field is an
    unbound step rather than a key the request silently grows.
    """
    _create_request(ctx)[field] = value


@given(parsers.parse('the request includes an artifact_webhook with delivery_mode "{mode}"'))
def given_request_artifact_webhook(ctx: dict, mode: str) -> None:
    """Carry an artifact_webhook with the three fields its schema requires.

    create-media-buy-request.json artifact_webhook ``required``: url, authentication,
    delivery_mode. The scheme defaults to Bearer; the next Given may name another.
    """
    _create_request(ctx)["artifact_webhook"] = {
        "url": "https://buyer.example.com/adcp/artifacts",
        "delivery_mode": mode,
        "authentication": {"schemes": ["Bearer"], "credentials": "artifact-webhook-credential-0001"},
    }


@given(parsers.parse('the artifact_webhook authentication scheme is "{scheme}"'))
def given_artifact_webhook_scheme(ctx: dict, scheme: str) -> None:
    """Name the artifact_webhook's authentication scheme."""
    webhook = ctx["request_kwargs"].get("artifact_webhook")
    assert webhook is not None, "the request carries no artifact_webhook to configure"
    webhook["authentication"]["schemes"] = [scheme]


# ═══════════════════════════════════════════════════════════════════════
# invoice_recipient override (BR-RULE-214)
# ═══════════════════════════════════════════════════════════════════════

#: The account the override is addressed to: the id "the account exists and is active" seeds
#: when no earlier Given names one, so the request and that Given meet on one row.
_INVOICE_OVERRIDE_ACCOUNT = "acc-001"


@given("a create_media_buy request that carries an invoice_recipient overriding the account default billing entity")
def given_request_invoice_recipient_override(ctx: dict) -> None:
    """The shared create request, naming one account and an invoice_recipient for this buy.

    create-media-buy-request.json invoice_recipient: "Override the account's default billing
    entity for this specific buy." The recipient is a whole business entity, so the request
    carries one built by the request-payload factory under a legal name of its own.
    """
    from tests.factories.account import BusinessEntityFactory

    kwargs = _create_request(ctx)
    account_id = ctx.setdefault("request_account_id", _INVOICE_OVERRIDE_ACCOUNT)
    kwargs["account"] = {"account_id": account_id}
    kwargs["invoice_recipient"] = BusinessEntityFactory.build_payload(legal_name="Unlinked Receivables GmbH")


@given("the invoice_recipient is not authorized for the account")
def given_invoice_recipient_not_authorized_for_account(ctx: dict) -> None:
    """Hold the precondition against the account the request names, read through its repository.

    This seller links a business entity to an account in one place, the account's own
    ``billing_entity``; there is no other authorization record. So the recipient is
    unauthorized exactly when the named account exists and does not hold it as its billing
    entity. Checked rather than assumed: an account Given that seeded the same entity would
    turn this scenario into the authorized case without changing a word of it.
    """
    from src.core.database.repositories.uow import AccountUoW

    kwargs = ctx["request_kwargs"]
    recipient = kwargs.get("invoice_recipient")
    assert recipient is not None, "the request carries no invoice_recipient to authorize"
    account_id = kwargs["account"]["account_id"]
    with AccountUoW(ctx["tenant"].tenant_id) as uow:
        assert uow.accounts is not None
        account = uow.accounts.get_by_id(account_id)
        assert account is not None, f"the request names account {account_id!r}, which no Given seeded"
        held = account.billing_entity
    assert held is None or held.legal_name != recipient["legal_name"], (
        f"account {account_id!r} holds {recipient['legal_name']!r} as its billing entity, so the "
        "recipient is authorized and the scenario would grade the accepted case"
    )


# ═══════════════════════════════════════════════════════════════════════
# Sandbox boundary (@T-UC-002-boundary-sandbox-response)
# ═══════════════════════════════════════════════════════════════════════

_SANDBOX_ACCOUNT = "sandbox account"
_PRODUCTION_ACCOUNT = "production account by id"
_PRODUCTION_NATURAL_KEY = "production account by natural key with sandbox false"
_SANDBOX_INVALID_BUDGET = "sandbox account with a zero budget"

_SANDBOX_BOUNDARY_SETUP = {
    "sandbox: true in response (sandbox account)": _SANDBOX_ACCOUNT,
    "sandbox: true on create_media_buy synchronous success shape": _SANDBOX_ACCOUNT,
    "sandbox absent in response (production account)": _PRODUCTION_ACCOUNT,
    "sandbox: false in the natural key (explicit production)": _PRODUCTION_NATURAL_KEY,
    "sandbox account with invalid budget (real validation error)": _SANDBOX_INVALID_BUDGET,
    "sandbox present on create_media_buy terminal-failure (errors) shape": _SANDBOX_INVALID_BUDGET,
}


@given(parsers.parse('a create_media_buy request whose account resolution matches "{boundary_point}"'))
def given_sandbox_boundary_request(ctx: dict, boundary_point: str) -> None:
    """Name the account the boundary row is about on a full create request.

    The account rows come from the generic sandbox/production account steps, which seed
    the row with the principal's access and point the request at it. The natural-key row
    names a production account by brand + operator with ``sandbox: false`` spelled out, the
    one place the pin lets a request say it (sandbox.mdx: a sandbox boolean on an
    ``account_id`` reference "is invalid").
    """
    from tests.bdd.steps.generic.given_auth import given_production_account, given_sandbox_account
    from tests.bdd.steps.generic.given_media_buy import given_request_with_total_budget

    setup = _SANDBOX_BOUNDARY_SETUP.get(boundary_point)
    assert setup is not None, f"unmapped sandbox boundary point {boundary_point!r}"
    kwargs = _create_request(ctx)
    if setup == _SANDBOX_INVALID_BUDGET:
        given_request_with_total_budget(ctx, 0)
    if setup in (_SANDBOX_ACCOUNT, _SANDBOX_INVALID_BUDGET):
        given_sandbox_account(ctx)
    elif setup == _PRODUCTION_ACCOUNT:
        given_production_account(ctx)
    else:
        from tests.factories.account import AccountFactory, AgentAccountAccessFactory

        domain = "explicit-production.example.com"
        account = AccountFactory(tenant=ctx["tenant"], sandbox=False, brand={"domain": domain}, operator=domain)
        AgentAccountAccessFactory(tenant=ctx["tenant"], principal=ctx["principal"], account=account)
        ctx["env"]._commit_factory_data()
        kwargs["account"] = {"brand": {"domain": domain}, "operator": domain, "sandbox": False}


_SANDBOX_SUCCESS_TRUE = "success with sandbox echoed true"
_SANDBOX_SUCCESS_NOT_TRUE = "success with sandbox absent or false"
_SANDBOX_ERROR_SUFFIX = " without a sandbox field"


def is_sandbox_outcome(outcome: str) -> bool:
    """True for the sandbox boundary's own outcome cells (``the result should be <outcome>``)."""
    return outcome in (_SANDBOX_SUCCESS_TRUE, _SANDBOX_SUCCESS_NOT_TRUE) or (
        outcome.startswith("error ") and outcome.endswith(_SANDBOX_ERROR_SUFFIX)
    )


def assert_sandbox_outcome(ctx: dict, outcome: str) -> None:
    """Grade a sandbox boundary outcome on the wire.

    Success rows require a created buy (``media_buy_id`` on the wire) and then grade
    ``sandbox``: exactly ``true`` for a sandbox account, absent or ``false`` otherwise. The
    error row grades the named code and that the refusal carries no ``sandbox`` anywhere
    in the document: the error branch of create-media-buy-response.json forbids it.
    """
    from tests.bdd.steps._outcome_helpers import wire_error_dict

    if outcome.startswith("error "):
        code = outcome[len("error ") : -len(_SANDBOX_ERROR_SUFFIX)]
        ctx["result"].assert_wire_error(code)
        envelope = wire_error_dict(ctx)
        assert "sandbox" not in envelope, f"the error response carries sandbox={envelope['sandbox']!r}: {envelope}"
        return
    wire_field(ctx, "media_buy_id")
    if outcome == _SANDBOX_SUCCESS_TRUE:
        sandbox = wire_lookup(ctx, "sandbox")
        assert sandbox is True, f"expected sandbox: true on a sandbox account's success, got {sandbox!r}"
    else:
        then_sandbox_absent_or_false(ctx)


@then("the response sandbox field should be absent or false")
def then_sandbox_absent_or_false(ctx: dict) -> None:
    """A production account's response does not claim sandbox mode: ``sandbox`` absent or false.

    create-media-buy-response.json describes only ``sandbox: true`` ("this response contains
    simulated data"), so an explicit ``false`` is as conformant as an absent field.
    """
    sandbox = wire_lookup(ctx, "sandbox")
    assert sandbox is WIRE_MISSING or sandbox is False, (
        f"a production account's response must not claim sandbox mode, got sandbox={sandbox!r}"
    )


# ═══════════════════════════════════════════════════════════════════════
# Governance plan_id boundary (@T-UC-002-boundary-plan-id-governance)
# ═══════════════════════════════════════════════════════════════════════

#: The deprecated plan_id each row sends, or None for a row that omits it.
_PLAN_ID_GOVERNANCE_ROWS: dict[str, str | None] = {
    "no governance agents + plan_id absent": None,
    "no governance agents + plan_id present": "plan-2026-q1",
}

_GOVERNANCE_SKIPPED = "proceeds, governance skipped"


@given(parsers.parse('a create_media_buy request and account governance state matching "{boundary_point}"'))
def given_plan_id_governance_request(ctx: dict, boundary_point: str) -> None:
    """The shared create request against an account with no governance_agents binding.

    The request names the env's default account, whose row is seeded without
    ``governance_agents``; that is asserted rather than assumed, so a seed that gains a
    binding fails here instead of grading a governed account under an ungoverned row.
    ``plan_id`` is the pin's deprecated request field (create-media-buy-request.json @ 3.2.7).
    """
    assert boundary_point in _PLAN_ID_GOVERNANCE_ROWS, f"unmapped plan_id governance row {boundary_point!r}"
    kwargs = _create_request(ctx)
    account = _default_account_named_by_request(ctx)
    assert not account.governance_agents, (
        f"account {account.account_id!r} carries governance_agents {account.governance_agents!r}; "
        "this row is about an account with none"
    )
    plan_id = _PLAN_ID_GOVERNANCE_ROWS[boundary_point]
    if plan_id is not None:
        kwargs["plan_id"] = plan_id


def is_governance_skipped_outcome(outcome: str) -> bool:
    """True for the plan_id boundary's ungoverned-account outcome cell."""
    return outcome == _GOVERNANCE_SKIPPED


def assert_governance_skipped_outcome(ctx: dict) -> None:
    """An account without governance_agents is processed normally: the buy is created.

    governance/campaign/specification.mdx @ 3.2.7: "When `governance_agents` is absent, the
    seller processes media buy requests normally", and "Sellers MUST NOT require governance
    checks as a prerequisite for all accounts". A seller that refused the request -- for a
    missing plan_id, or for carrying the deprecated one -- emits no ``media_buy_id``.
    """
    wire_field(ctx, "media_buy_id")


# ═══════════════════════════════════════════════════════════════════════
# THEN steps
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('the error should name idempotency_key as rejected by schema keyword "{keyword}"'))
def then_idempotency_key_rejected_by_keyword(ctx: dict, keyword: str) -> None:
    """The INVALID_REQUEST names the key in ``field`` and the rejecting keyword in ``issues[]``.

    core/error.json: ``issues[].keyword`` is the "Schema keyword that rejected the payload",
    and ``field`` MUST be ``issues[0].pointer`` translated to JSONPath-lite.
    """
    ctx["result"].assert_wire_error(
        "INVALID_REQUEST",
        field="idempotency_key",
        issues=[{"pointer": "/idempotency_key", "keyword": keyword}],
    )


@then("the error should carry no details, issues or field pointer")
def then_error_exposes_only_the_code(ctx: dict) -> None:
    """Every error object on the wire carries none of the three positions that could leak.

    security.mdx, IDEMPOTENCY_CONFLICT response shape: no cached response, original payload,
    diff or fingerprint, and no ``field`` hint -- "The error body exposes only the code."
    """
    errors = ctx["result"].wire_error_objects()
    assert errors, "no wire error object was captured"
    for error in errors:
        leaked = {"details", "issues", "field"} & set(error)
        assert not leaked, f"the error exposes {sorted(leaked)} beyond its code: {error}"


@then(parsers.re(r"the response should not include an? (?P<field>\w+)(?: array)? at the envelope level"))
def then_submitted_envelope_omits(ctx: dict, field: str) -> None:
    """The submitted envelope does not carry ``field`` (create-media-buy-response.json /oneOf/2/not)."""
    wire_absent(ctx, field)


@then("the submitted envelope carries a plain-text message of at most 2000 characters")
def then_submitted_message_is_bounded_plain_text(ctx: dict) -> None:
    """The submitted envelope's ``message`` is text within the schema's maxLength, with no markup.

    create-media-buy-response.json /oneOf/2/properties/message: "maxLength": 2000, "Plain
    text only".
    """
    message = wire_field(ctx, "message")
    assert isinstance(message, str) and message, f"expected a non-empty text message, got {message!r}"
    assert len(message) <= 2000, f"message is {len(message)} characters, over the schema's maxLength of 2000"
    assert re.search(r"<[A-Za-z/!]", message) is None, f"message carries markup, not plain text: {message!r}"


@then("the paused package should be created in a paused state")
def then_paused_package_reported_paused(ctx: dict) -> None:
    """The one package the request asked to pause is reported ``paused: true`` on the wire."""
    packages = wire_field(ctx, "packages")
    assert len(packages) == 1, f"the request carried one package, the response reports {len(packages)}"
    assert packages[0].get("paused") is True, f"expected the package created paused, got {packages[0]!r}"


@then(parsers.parse('the media buy should persist agency_estimate_number "{value}"'))
def then_media_buy_persists_agency_estimate_number(ctx: dict, value: str) -> None:
    """The buy the response names stores the buyer's agency_estimate_number, read through the repository."""
    from src.core.database.repositories.uow import MediaBuyUoW

    media_buy_id = wire_field(ctx, "media_buy_id")
    with MediaBuyUoW(ctx["tenant"].tenant_id) as uow:
        assert uow.media_buys is not None
        buy = uow.media_buys.get_by_id(media_buy_id)
        assert buy is not None, f"the response names media buy {media_buy_id!r}, which was not stored"
        stored = (buy.raw_request or {}).get("agency_estimate_number")
    assert stored == value, f"expected agency_estimate_number {value!r} on media buy {media_buy_id!r}, got {stored!r}"
