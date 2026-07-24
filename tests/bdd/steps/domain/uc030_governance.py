"""Domain step definitions for UC-030: Manage Governance Binding (sync_governance).

Wires the in-scope BR-UC-030 ``@sync`` scenarios — the seller-side governance
binding — against the shared cross-transport harness (GovernanceSyncEnv), so the
core success path, the per-account authority failure, the partial-failure model,
and the request-validation boundary all execute and assert on the wire across
a2a/mcp/rest (no IMPL — BDD grades wire conformance).

Out of scope (routed to ``_UC030_XFAIL_TAGS`` in conftest, not stepped here):
- ``@check`` scenarios grade ``check_governance`` (enforcement), a capability this
  agent deliberately does not declare (``governance-aware-seller``).
- Idempotency replay / IDEMPOTENCY_CONFLICT and per-operation scope
  (PERMISSION_DENIED) grade behavior this PR defers.
- ``@bva`` abstract-verdict outlines are covered concretely by the ``T-UC-030-sync-*``
  scenarios; their generic verdict-step wiring is a follow-up.

Reuses the shared auth Givens ("the Buyer Agent has an authenticated/unauthenticated
connection") and the generic ``the error code is "X"`` step (uc011_accounts), which
are registered globally — this module defines only governance-specific steps.

ctx["env"] is a GovernanceSyncEnv (bound by the conftest UC-030 branch).
ctx["response"] / ctx["error"] / ctx["wire_response"] / ctx["wire_error_envelope"]
are populated by dispatch_request.

beads: #1329 (UC-030)
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import _require_response, wire_dict
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories import AccountFactory, AgentAccountAccessFactory
from tests.helpers import assert_envelope_shape

# A valid, well-formed idempotency_key (pattern ^[A-Za-z0-9_.:-]{16,255}$) and
# Bearer credentials (minLength 32) for scenarios that need a well-formed request
# so the assertion-under-test (auth, account resolution) is what actually fires.
_VALID_KEY = "uuid-v4-bdd-00000000000001"
_DEFAULT_URL = "https://governance.example.com"


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _tenant_principal(ctx: dict) -> tuple[Any, Any]:
    """Return the tenant/principal the shared auth Given set up in ctx."""
    return ctx["tenant"], ctx["principal"]


def _owned_account(ctx: dict, account_id: str) -> Any:
    """Create an account the authenticated agent has authority over (access grant)."""
    tenant, principal = _tenant_principal(ctx)
    account = AccountFactory(tenant=tenant, account_id=account_id)
    AgentAccountAccessFactory(tenant=tenant, principal=principal, account=account)
    ctx.setdefault("gov_accounts", {})[account_id] = account
    return account


def _unowned_account(ctx: dict, account_id: str) -> Any:
    """Create an account WITHOUT an access grant (agent has no authority over it)."""
    tenant, _principal = _tenant_principal(ctx)
    account = AccountFactory(tenant=tenant, account_id=account_id)
    ctx.setdefault("gov_accounts", {})[account_id] = account
    return account


def _agent(url: str, *, cred_len: int = 64, credentials: str | None = None, scheme: str = "Bearer") -> dict[str, Any]:
    """Build a request-side governance agent dict (url + authentication)."""
    creds = credentials if credentials is not None else "x" * cred_len
    return {"url": url, "authentication": {"schemes": [scheme], "credentials": creds}}


def _account_entry(account_id: str, agents: list[dict[str, Any]]) -> dict[str, Any]:
    return {"account": {"account_id": account_id}, "governance_agents": agents}


def _dispatch(ctx: dict, transport: str, *, identity: Any = "__keep__", **kwargs: Any) -> None:
    """Dispatch raw kwargs through the parametrized wire transport.

    ``transport`` (the "via MCP"/"via REST" token from the Gherkin) is accepted
    but IGNORED — pytest_generate_tests controls the actual transport
    (a2a/mcp/rest) via ``ctx["transport"]``, so each scenario executes across all
    wire transports (mirrors the shared auth Given's convention). Raw kwargs (not
    a pre-built request) are sent so request validation happens at the transport
    boundary and produces a real AdCP wire envelope.
    """
    if identity == "__keep__":
        dispatch_request(ctx, **kwargs)
    else:
        dispatch_request(ctx, identity=identity, **kwargs)


def _wire_accounts(ctx: dict) -> list[dict[str, Any]]:
    return wire_dict(ctx).get("accounts") or []


def _wire_account(ctx: dict, account_id: str) -> dict[str, Any]:
    for acct in _wire_accounts(ctx):
        ref = acct.get("account") or {}
        if ref.get("account_id") == account_id:
            return acct
    available = [(a.get("account") or {}).get("account_id") for a in _wire_accounts(ctx)]
    raise AssertionError(f"No wire account {account_id!r}. Available: {available}")


def _url_eq(actual: str, expected: str) -> bool:
    """Compare urls tolerant of AnyUrl trailing-slash normalization."""
    return actual.rstrip("/") == expected.rstrip("/")


def _assert_wire_validation(ctx: dict, *tokens: str) -> None:
    """Assert a VALIDATION_ERROR wire envelope that references the given field tokens."""
    envelope = ctx.get("wire_error_envelope")
    assert envelope is not None, f"expected a wire error envelope; got response {ctx.get('response')!r}"
    assert_envelope_shape(envelope, "VALIDATION_ERROR", recovery="correctable")
    blob = str(envelope).lower()
    for token in tokens:
        assert token.lower() in blob, f"expected {token!r} referenced in validation envelope: {envelope}"


# ═══════════════════════════════════════════════════════════════════════
# Given — authority setup (governance-specific)
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('the agent has authority over account "{account_id}"'))
def given_authority_over(ctx: dict, account_id: str) -> None:
    _owned_account(ctx, account_id)


@given(parsers.parse('the agent has authority over accounts "{a}" and "{b}"'))
def given_authority_over_two(ctx: dict, a: str, b: str) -> None:
    _owned_account(ctx, a)
    _owned_account(ctx, b)


@given(parsers.parse('the agent does NOT have authority over account "{account_id}"'))
def given_no_authority_over(ctx: dict, account_id: str) -> None:
    # The account exists in the tenant but carries no access grant for this agent,
    # so resolve_account raises AdCPAuthorizationError -> per-account SCOPE_INSUFFICIENT.
    _unowned_account(ctx, account_id)


@given(parsers.parse('no governance agent is currently bound to "{account_id}"'))
def given_no_binding(ctx: dict, account_id: str) -> None:
    account = ctx.get("gov_accounts", {}).get(account_id)
    assert account is not None, f"account {account_id!r} must be set up by a prior authority step"
    assert not account.governance_agents, (
        f"expected no prior binding on {account_id!r}, got {account.governance_agents}"
    )


# ═══════════════════════════════════════════════════════════════════════
# When — sync_governance dispatch (governance-specific)
# ═══════════════════════════════════════════════════════════════════════


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_governance request via {transport} with idempotency_key "{key}" '
        'and one account "{account_id}" bound to governance agent "{url}" with Bearer credentials of length {n:d}'
    )
)
def when_sync_one_account(ctx: dict, transport: str, key: str, account_id: str, url: str, n: int) -> None:
    _dispatch(ctx, transport, idempotency_key=key, accounts=[_account_entry(account_id, [_agent(url, cred_len=n)])])


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_governance request via {transport} with idempotency_key "{key}" '
        'and account "{account_id}" bound to governance agent "{url}" with Bearer credentials of length {n:d}'
    )
)
def when_sync_account_len(ctx: dict, transport: str, key: str, account_id: str, url: str, n: int) -> None:
    _dispatch(ctx, transport, idempotency_key=key, accounts=[_account_entry(account_id, [_agent(url, cred_len=n)])])


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_governance request via {transport} with idempotency_key "{key}" '
        'and account "{account_id}" bound to governance agent "{url}" with Bearer credentials "{credentials}"'
    )
)
def when_sync_account_literal_creds(
    ctx: dict, transport: str, key: str, account_id: str, url: str, credentials: str
) -> None:
    _dispatch(
        ctx,
        transport,
        idempotency_key=key,
        accounts=[_account_entry(account_id, [_agent(url, credentials=credentials)])],
    )


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_governance request via {transport} with idempotency_key "{key}" '
        'and two accounts "{a}" and "{b}" both bound to governance agent "{url}"'
    )
)
def when_sync_two_accounts(ctx: dict, transport: str, key: str, a: str, b: str, url: str) -> None:
    _dispatch(
        ctx,
        transport,
        idempotency_key=key,
        accounts=[_account_entry(a, [_agent(url)]), _account_entry(b, [_agent(url)])],
    )


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_governance request via {transport} with idempotency_key "{key}" '
        'and account "{account_id}" bound to TWO governance agents "{u1}" and "{u2}"'
    )
)
def when_sync_two_agents(ctx: dict, transport: str, key: str, account_id: str, u1: str, u2: str) -> None:
    _dispatch(ctx, transport, idempotency_key=key, accounts=[_account_entry(account_id, [_agent(u1), _agent(u2)])])


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_governance request via {transport} with idempotency_key "{key}" '
        'and account "{account_id}" with an empty governance_agents array'
    )
)
def when_sync_empty_agents(ctx: dict, transport: str, key: str, account_id: str) -> None:
    _dispatch(ctx, transport, idempotency_key=key, accounts=[_account_entry(account_id, [])])


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_governance request via {transport} with idempotency_key "{key}" '
        'and {n:d} accounts each bound to "{url}"'
    )
)
def when_sync_n_accounts(ctx: dict, transport: str, key: str, n: int, url: str) -> None:
    accounts = [_account_entry(f"acct-{i}", [_agent(url)]) for i in range(n)]
    _dispatch(ctx, transport, idempotency_key=key, accounts=accounts)


@when(
    parsers.parse(
        "the Buyer Agent sends a sync_governance request via {transport} without an idempotency_key "
        'and one account "{account_id}"'
    )
)
def when_sync_no_key(ctx: dict, transport: str, account_id: str) -> None:
    # Well-formed agent so the ONLY defect is the missing key.
    _dispatch(ctx, transport, accounts=[_account_entry(account_id, [_agent(_DEFAULT_URL)])])


@when(
    parsers.parse(
        "the Buyer Agent sends a sync_governance request via {transport} without an authentication token "
        'and one account "{account_id}"'
    )
)
def when_sync_no_auth(ctx: dict, transport: str, account_id: str) -> None:
    # Well-formed request so the operation-level failure is AUTH_REQUIRED, not validation.
    _dispatch(
        ctx,
        transport,
        identity=None,
        idempotency_key=_VALID_KEY,
        accounts=[_account_entry(account_id, [_agent(_DEFAULT_URL)])],
    )


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_governance request via {transport} with idempotency_key "{key}" '
        'and one account "{account_id}"'
    )
)
def when_sync_key_boundary(ctx: dict, transport: str, key: str, account_id: str) -> None:
    # idempotency_key boundary scenarios: vary only the key; keep the rest well-formed.
    _dispatch(ctx, transport, idempotency_key=key, accounts=[_account_entry(account_id, [_agent(_DEFAULT_URL)])])


# ═══════════════════════════════════════════════════════════════════════
# Then — response variant / per-account / echo (wire assertions)
# ═══════════════════════════════════════════════════════════════════════


@then("the response variant is success")
@then(parsers.parse("the response variant is success and carries an accounts array with {n:d} item"))
def then_variant_success(ctx: dict, n: int | None = None) -> None:
    assert ctx.get("error") is None, f"expected success variant, got error {ctx.get('error')!r}"
    _require_response(ctx)
    accounts = _wire_accounts(ctx)
    assert accounts, "success variant must carry a non-empty accounts array"
    if n is not None:
        assert len(accounts) == n, f"expected {n} account(s), got {len(accounts)}: {accounts}"


@then(parsers.parse("the response accounts array has {n:d} items"))
def then_accounts_count(ctx: dict, n: int) -> None:
    accounts = _wire_accounts(ctx)
    assert len(accounts) == n, f"expected {n} accounts, got {len(accounts)}"


@then("the response variant is error")
def then_variant_error(ctx: dict) -> None:
    assert ctx.get("error") is not None, f"expected error variant, got response {ctx.get('response')!r}"
    envelope = ctx.get("wire_error_envelope")
    assert envelope is not None, "error variant must carry a two-layer wire error envelope"
    assert "adcp_error" in envelope and envelope.get("errors"), f"malformed wire error envelope: {envelope}"


@then("the response does NOT carry an operation-level errors array")
def then_no_operation_errors(ctx: dict) -> None:
    # Success (partial-failure) variant: per-account errors live under accounts[].errors,
    # never as a top-level operation-level errors[] (spec oneOf: accounts XOR errors).
    assert "errors" not in wire_dict(ctx), f"success variant must not carry top-level errors: {wire_dict(ctx)}"


@then(parsers.parse('the account "{account_id}" has status "{status}"'))
@then(parsers.parse('account "{account_id}" has status "{status}" and echoes the governance_agents URL'))
def then_account_status(ctx: dict, account_id: str, status: str) -> None:
    acct = _wire_account(ctx, account_id)
    assert acct["account"]["account_id"] == account_id, f"wire must echo the requested account ref {account_id}"
    assert acct["status"] == status, f"account {account_id}: expected status {status}, got {acct['status']}"
    if status == "synced":
        agents = acct.get("governance_agents") or []
        assert agents and agents[0].get("url"), f"synced account {account_id} must echo a governance_agents url"
        # Credentials are write-only: a synced echo MUST NOT carry authentication (wire-level).
        assert "authentication" not in agents[0], f"synced echo must not carry credentials: {agents[0]}"


@then(parsers.parse('account "{account_id}" has status "{status}" and carries a per-account errors array'))
def then_account_status_with_errors(ctx: dict, account_id: str, status: str) -> None:
    acct = _wire_account(ctx, account_id)
    assert acct["account"]["account_id"] == account_id, f"wire must echo the requested account ref {account_id}"
    assert acct["status"] == status, f"account {account_id}: expected status {status}, got {acct['status']}"
    assert acct.get("errors"), f"failed account {account_id} must carry a per-account errors array: {acct}"


@then(parsers.parse('the response account "{account_id}" echoes governance_agents[{idx:d}].url "{url}"'))
def then_echo_url(ctx: dict, account_id: str, idx: int, url: str) -> None:
    acct = _wire_account(ctx, account_id)
    assert acct["account"]["account_id"] == account_id, f"wire must echo the requested account ref {account_id}"
    agents = acct.get("governance_agents") or []
    actual = agents[idx]["url"]
    assert _url_eq(actual, url), f"account {account_id}: expected echoed url {url}, got {actual}"


@then(parsers.parse('the response account "{account_id}" does NOT echo governance_agents[{idx:d}].authentication'))
def then_no_echo_auth(ctx: dict, account_id: str, idx: int) -> None:
    acct = _wire_account(ctx, account_id)
    assert acct["account"]["account_id"] == account_id, f"wire must echo the requested account ref {account_id}"
    agents = acct.get("governance_agents") or []
    assert "authentication" not in agents[idx], f"credentials must not be echoed: {agents[idx]}"


@then("the response carries an echoed adcp_version envelope")
def then_adcp_version(ctx: dict) -> None:
    body = wire_dict(ctx)
    assert body.get("adcp_version"), f"expected an echoed adcp_version envelope field, got keys {list(body)}"


@then(parsers.parse('each per-account error should include a "{field}" field guiding remediation'))
def then_per_account_suggestion(ctx: dict, field: str) -> None:
    accounts = _wire_accounts(ctx)
    statuses = {a["status"] for a in accounts}
    assert "failed" in statuses, f"expected a failed per-account entry to carry {field!r}, got statuses {statuses}"
    for acct in accounts:
        if acct["status"] != "failed":
            continue
        errs = acct.get("errors") or []
        assert errs, f"failed account {acct.get('account')} must carry a per-account errors array: {acct}"
        assert all(e.get(field) for e in errs), f"each per-account error must include a non-empty {field!r}: {acct}"


# ═══════════════════════════════════════════════════════════════════════
# Then — validation / boundary wire errors (governance-specific)
# ═══════════════════════════════════════════════════════════════════════


@then("the error references the url field and indicates https is required")
def then_error_url_https(ctx: dict) -> None:
    _assert_wire_validation(ctx, "url", "https")


@then("the error references the credentials field")
def then_error_credentials(ctx: dict) -> None:
    _assert_wire_validation(ctx, "credentials")


@then("the error references the governance_agents cardinality")
def then_error_cardinality(ctx: dict) -> None:
    _assert_wire_validation(ctx, "governance_agents")


@then("the error references the accounts array size")
def then_error_accounts_size(ctx: dict) -> None:
    _assert_wire_validation(ctx, "accounts")


@then("the error code indicates the missing idempotency_key")
def then_error_missing_key(ctx: dict) -> None:
    _assert_wire_validation(ctx, "idempotency_key")


@then(parsers.parse('the response outcome is "{outcome}"'))
def then_response_outcome(ctx: dict, outcome: str) -> None:
    # idempotency_key boundary: "accepted" == request passed operation-level validation
    # (success variant, even if per-account resolution failed); "rejected" == a
    # request-validation wire error fired.
    if outcome == "accepted":
        assert ctx.get("error") is None, f"expected accepted, got error {ctx.get('error')!r}"
        _require_response(ctx)
    elif outcome == "rejected":
        assert ctx.get("error") is not None, f"expected rejected, got response {ctx.get('response')!r}"
        assert ctx.get("wire_error_envelope") is not None, "rejected must carry a wire error envelope"
    else:
        raise AssertionError(f"unknown outcome {outcome!r}")
