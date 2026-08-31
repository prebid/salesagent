"""Domain step definitions for UC-030: Manage Governance Binding (sync_governance).

Wires the in-scope BR-UC-030 ``@sync`` scenarios — the seller-side governance
binding — against the shared cross-transport harness (GovernanceSyncEnv), so the
core success path, the per-account authority failure, the partial-failure model,
and the request-validation boundary all execute and assert on the wire across
a2a/mcp/rest (no IMPL — BDD grades wire conformance).

Out of scope (routed to the conftest ``_XFAIL_TAGS`` registry, not stepped here):
- ``@check`` scenarios grade ``check_governance`` (enforcement), a capability this
  agent deliberately does not declare (``governance-aware-seller``).
- Idempotency replay / IDEMPOTENCY_CONFLICT and per-operation scope
  (PERMISSION_DENIED) grade behavior this PR defers.
- ``@sync @bva`` boundary outlines (cardinality, schemes, url, credentials, idempotency_key,
  per-account status) ARE wired here (``when_bva_*`` + ``then_request_verdict`` /
  ``then_response_verdict``). Their request-validation + valid-enum rows run and grade on the
  wire; the schema-unexpressible rows (a per-account status outside the {synced, failed} enum, a
  credential echoed on the response) and the unbuilt idempotency replay/conflict rows auto-xfail
  at the when-step (``NotImplementedError`` — #1934), so the conftest ``_XFAIL_TAGS`` registry no
  longer carries a per-tag entry for these three outlines (#1329 item 5).

Reuses the shared auth Givens ("the Buyer Agent has an authenticated/unauthenticated
connection") and the generic ``the error code is "X"`` step (uc011_accounts), which
are registered globally — this module defines only governance-specific steps.

ctx["env"] is a GovernanceSyncEnv (bound by the conftest UC-030 branch).
ctx["response"] / ctx["error"] / ctx["wire_response"] / ctx["wire_error_envelope"]
are populated by dispatch_request.

#1329 (UC-030)
"""

from __future__ import annotations

import re
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import _require_response, wire_dict, wire_error_envelope_or_none
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories import AccountFactory
from tests.factories.principal import _UNSET
from tests.harness.transport import _pinned_error_metadata
from tests.helpers.accounts import seed_account_with_access
from tests.helpers.governance import (
    BEARER_CREDS,
    DEFAULT_URL,
    LEAK_SECRET,
    account_entry,
    governance_agent_dict,
    leaky_governance_agent,
    normalize_url,
    persisted_governance_urls,
)

# A valid, well-formed idempotency_key (pattern ^[A-Za-z0-9_.:-]{16,255}$) and
# Bearer credentials (minLength 32) for scenarios that need a well-formed request
# so the assertion-under-test (auth, account resolution) is what actually fires.
_VALID_KEY = "uuid-v4-bdd-00000000000001"


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _tenant_principal(ctx: dict) -> tuple[Any, Any]:
    """Return the tenant/principal the shared auth Given set up in ctx."""
    return ctx["tenant"], ctx["principal"]


def _owned_account(ctx: dict, account_id: str) -> Any:
    """Create an account the authenticated agent has authority over (access grant)."""
    tenant, principal = _tenant_principal(ctx)
    account = seed_account_with_access(tenant, principal, account_id=account_id)
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
    return governance_agent_dict(url, cred_len=cred_len, credentials=credentials, scheme=scheme)


def _account_entry(account_id: str, agents: list[dict[str, Any]]) -> dict[str, Any]:
    # Thin id-form wrapper over the shared request-element builder (#1329).
    return account_entry({"account_id": account_id}, agents=agents)


# The credential-channel scenarios' leak secret + agent builder are the SHARED
# tests.helpers.governance.LEAK_SECRET / leaky_governance_agent — one home for the leak
# contract so this transport-blind BDD grade and the A2A+REST integration grade cannot drift
# on the secret value or the mistyped-key shape (#1329).


def _dispatch(ctx: dict, transport: str, *, identity: Any = _UNSET, **kwargs: Any) -> None:
    """Dispatch raw kwargs through the parametrized wire transport.

    ``transport`` (the "via MCP"/"via REST" token from the Gherkin) is accepted
    but IGNORED — pytest_generate_tests controls the actual transport
    (a2a/mcp/rest) via ``ctx["transport"]``, so each scenario executes across all
    wire transports (mirrors the shared auth Given's convention). Raw kwargs (not
    a pre-built request) are sent so request validation happens at the transport
    boundary and produces a real AdCP wire envelope.

    ``identity`` defaults to the repo's ``_UNSET`` sentinel (not a bespoke "__keep__"
    magic string — #1329): unset → dispatch under the scenario's own identity;
    an explicit value overrides it (the no-auth / wrong-principal scenarios).
    """
    if identity is _UNSET:
        dispatch_request(ctx, **kwargs)
    else:
        dispatch_request(ctx, identity=identity, **kwargs)


def _wire_accounts(ctx: dict) -> list[dict[str, Any]]:
    return wire_dict(ctx).get("accounts") or []


def _wire_account(ctx: dict, account_id: str) -> dict[str, Any]:
    """Return the wire per-account entry whose echoed ref matches ``account_id``.

    Finding the entry by its requested id IS the account-ref echo grade: if the wire
    did not echo the requested ref, this raises "No wire account". Callers therefore do
    NOT re-assert ``acct["account"]["account_id"] == account_id`` — that would be
    tautological against this lookup (#1329).
    """
    for acct in _wire_accounts(ctx):
        ref = acct.get("account") or {}
        if ref.get("account_id") == account_id:
            return acct
    available = [(a.get("account") or {}).get("account_id") for a in _wire_accounts(ctx)]
    raise AssertionError(f"No wire account {account_id!r}. Available: {available}")


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
    # so resolve_account raises AdCPAuthorizationError, which _sync_one_account
    # collapses to the uniform per-account ACCOUNT_NOT_FOUND (no enumeration oracle).
    _unowned_account(ctx, account_id)


@given(parsers.parse('no governance agent is currently bound to "{account_id}"'))
def given_no_binding(ctx: dict, account_id: str) -> None:
    account = ctx.get("gov_accounts", {}).get(account_id)
    assert account is not None, f"account {account_id!r} must be set up by a prior authority step"
    assert not account.governance_agents, (
        f"expected no prior binding on {account_id!r}, got {account.governance_agents}"
    )


@given(parsers.parse('account "{account_id}" is currently bound to governance agent "{url}"'))
def given_currently_bound(ctx: dict, account_id: str, url: str) -> None:
    """Seed a prior binding by dispatching a FIRST sync (the real write path), so the
    scenario's When exercises genuine replace-over-existing, not bind-from-empty. The
    account must already be owned (a prior authority Given created it). Records the prior
    url so the "no longer present" Then knows which account was replaced.
    """
    _dispatch(
        ctx,
        ctx.get("transport"),
        idempotency_key="uuid-v4-prebind-00000000000001",
        accounts=[_account_entry(account_id, [_agent(url)])],
    )
    acct = _wire_account(ctx, account_id)
    assert acct["status"] == "synced", f"pre-binding first sync must succeed on {account_id!r}: {acct}"
    ctx.setdefault("prior_bindings", {})[account_id] = url


@given(parsers.parse('the agent has authority over the implicit account for brand "{brand}" on operator "{operator}"'))
def given_authority_over_implicit(ctx: dict, brand: str, operator: str) -> None:
    """Seed a natural-key account (brand.domain + operator, non-sandbox) the agent owns.

    Unlike ``_owned_account`` (account_id only), the implicit-account scenario resolves by
    natural key, so the row must carry the operator + brand.domain the request references —
    the canonical seeder carries both (#1329).
    """
    tenant, principal = _tenant_principal(ctx)
    account_id = "acc-nk-" + f"{brand}-{operator}".replace(".", "-")
    account = seed_account_with_access(
        tenant, principal, account_id=account_id, operator=operator, brand_domain=brand, sandbox=False
    )
    ctx.setdefault("gov_accounts", {})[account_id] = account


# ═══════════════════════════════════════════════════════════════════════
# When — sync_governance dispatch (governance-specific)
# ═══════════════════════════════════════════════════════════════════════


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_governance request via {transport} with idempotency_key "{key}" '
        'and one account "{account_id}" bound to governance agent "{url}" with Bearer credentials of length {n:d}'
    )
)
@when(
    parsers.parse(
        'the Buyer Agent sends a sync_governance request via {transport} with idempotency_key "{key}" '
        'and account "{account_id}" bound to governance agent "{url}" with Bearer credentials of length {n:d}'
    )
)
@when(
    parsers.parse(
        'the Buyer Agent sends a sync_governance request via {transport} with idempotency_key "{key}" '
        'naming only account "{account_id}" bound to governance agent "{url}" with Bearer credentials of length {n:d}'
    )
)
def when_sync_one_account(ctx: dict, transport: str, key: str, account_id: str, url: str, n: int) -> None:
    """Sync a single named account. Three Gherkin phrasings share one body — "one account"
    / "account" / "naming only account" (the last is the per-account replace-scope scenario,
    which names only one of two owned accounts)."""
    _dispatch(ctx, transport, idempotency_key=key, accounts=[_account_entry(account_id, [_agent(url, cred_len=n)])])


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_governance request via {transport} with idempotency_key "{key}" '
        'and one account referenced by brand "{brand}" on operator "{operator}" bound to governance agent "{url}" '
        "with Bearer credentials of length {n:d}"
    )
)
def when_sync_natural_key_account(
    ctx: dict, transport: str, key: str, brand: str, operator: str, url: str, n: int
) -> None:
    """Sync governance to an account referenced by natural key (brand + operator), not id."""
    ref = {"brand": {"domain": brand}, "operator": operator, "sandbox": False}
    _dispatch(
        ctx,
        transport,
        idempotency_key=key,
        accounts=[account_entry(ref, agents=[_agent(url, cred_len=n)])],
    )


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
        'the Buyer Agent sends a sync_governance request with idempotency_key "{key}" and '
        'account "{account_id}" whose governance agent leaks a secret via {channel}'
    )
)
def when_sync_leaky_agent(ctx: dict, key: str, account_id: str, channel: str) -> None:
    """Dispatch a request whose governance agent carries a secret on the named credential channel.

    The request is rejected at the validation boundary (before account resolution), so no
    account seeding is needed — only the shared auth Given (so the request passes auth and
    reaches the boundary). The leaked secret is stashed for the absence assertion. Transport
    comes from parametrization (a2a/mcp/rest), so this grades every wire.
    """
    ctx["leaked_secret"] = LEAK_SECRET
    ctx["leak_channel"] = channel
    _dispatch(ctx, "", idempotency_key=key, accounts=[_account_entry(account_id, [leaky_governance_agent(channel)])])


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
    _dispatch(ctx, transport, accounts=[_account_entry(account_id, [_agent(DEFAULT_URL)])])


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
        accounts=[_account_entry(account_id, [_agent(DEFAULT_URL)])],
    )


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_governance request via {transport} with idempotency_key "{key}" '
        'and one account "{account_id}"'
    )
)
def when_sync_key_boundary(ctx: dict, transport: str, key: str, account_id: str) -> None:
    # idempotency_key boundary scenarios: vary only the key; keep the rest well-formed.
    _dispatch(ctx, transport, idempotency_key=key, accounts=[_account_entry(account_id, [_agent(DEFAULT_URL)])])


# ═══════════════════════════════════════════════════════════════════════
# When — @bva request-validation boundary outlines (#1329 / Konstantin item 1)
# ═══════════════════════════════════════════════════════════════════════
#
# These wire the @sync @bva boundary outlines whose rows are all REQUEST-VALIDATION cases (no
# account seeding): the request is dispatched over the parametrized wire and graded by
# then_request_verdict. The construction-time boundary grades in TestSyncGovernanceBoundaryValues
# stay; this adds the missing WIRE grade the round-8 xfail gate withheld. Outlines with a
# response-shape row (credentials "present on response", per-account status enum) or a deferred
# replay row (idempotency_key) stay xfailed in the conftest UC-030 branch — they need seeding or
# an unimplemented feature, not just request validation.


def _bva_agent(url: Any = DEFAULT_URL, **overrides: Any) -> dict[str, Any]:
    """A well-formed request agent; only the boundary-under-test deviates from it.

    Delegates to the shared ``governance_agent_dict`` deviation vocabulary (#1329) —
    a different ``url`` overrides it, ``url=_UNSET`` REMOVES it (the url-absent boundary), and
    ``**overrides`` (e.g. ``authentication={...}``) replaces another key — instead of re-inlining
    the pinned shape and a second credentials literal. The url-absent boundary now flows through
    the sentinel too (``governance_agent_dict`` honors ``url=_UNSET``), so this no longer
    re-inlines a ``del agent["url"]``.
    """
    return governance_agent_dict(url, **overrides)


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_governance request exercising the governance_agents boundary case "{boundary}"'
    )
)
def when_bva_governance_agents(ctx: dict, boundary: str) -> None:
    agents = {
        "governance_agents has 0 entries": [],
        "governance_agents has 2 entries": [_bva_agent(), _bva_agent()],
    }[boundary]
    ctx["bva_grade"] = _BVA_GRADE.get(boundary)
    _dispatch(ctx, "", idempotency_key=_VALID_KEY, accounts=[_account_entry("acct-bva", agents)])


@when(
    parsers.parse('the Buyer Agent sends a sync_governance request exercising the accounts boundary case "{boundary}"')
)
def when_bva_accounts(ctx: dict, boundary: str) -> None:
    n = {"accounts has 0 entries": 0, "accounts has 100 entries": 100, "accounts has 101 entries": 101}[boundary]
    accounts = [_account_entry(f"acct-bva-{i}", [_bva_agent()]) for i in range(n)]
    ctx["bva_grade"] = _BVA_GRADE.get(boundary)
    _dispatch(ctx, "", idempotency_key=_VALID_KEY, accounts=accounts)


@when(
    parsers.parse(
        "the Buyer Agent sends a sync_governance request exercising the authentication.schemes "
        'boundary case "{boundary}"'
    )
)
def when_bva_auth_schemes(ctx: dict, boundary: str) -> None:
    auth: dict[str, Any] = {
        "exactly one valid scheme": {"schemes": ["Bearer"], "credentials": BEARER_CREDS},
        "empty array (0 items)": {"schemes": [], "credentials": BEARER_CREDS},
        "two items": {"schemes": ["Bearer", "Bearer"], "credentials": BEARER_CREDS},
        "single item outside enum": {"schemes": ["definitely-not-a-scheme"], "credentials": BEARER_CREDS},
        "schemes absent": {"credentials": BEARER_CREDS},
    }[boundary]
    ctx["bva_grade"] = _BVA_GRADE.get(boundary)
    _dispatch(
        ctx, "", idempotency_key=_VALID_KEY, accounts=[_account_entry("acct-bva", [_bva_agent(authentication=auth)])]
    )


@when(parsers.parse('the Buyer Agent sends a sync_governance request exercising the url boundary case "{boundary}"'))
def when_bva_url(ctx: dict, boundary: str) -> None:
    # Each case is a DELTA against the well-formed agent via the shared deviation vocabulary
    # (#1329): override the url, or REMOVE it with the _UNSET sentinel.
    if boundary == "https:// URL":
        agent = _bva_agent()
    elif boundary == "http:// URL (plaintext)":
        agent = _bva_agent(url="http://governance.example.com/hook")
    elif boundary == "non-uri string":
        agent = _bva_agent(url="not-a-uri")
    elif boundary == "url absent":
        agent = _bva_agent(url=_UNSET)
    else:
        raise AssertionError(f"unknown url boundary {boundary!r}")
    ctx["bva_grade"] = _BVA_GRADE.get(boundary)
    _dispatch(ctx, "", idempotency_key=_VALID_KEY, accounts=[_account_entry("acct-bva", [agent])])


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_governance request exercising the credentials boundary case "{boundary}"'
    )
)
def when_bva_credentials(ctx: dict, boundary: str) -> None:
    """Wire the @T-UC-030-bva-credentials outline (#1329 item 5).

    ``credentials absent`` is a request-validation row (graded invalid via ``_BVA_GRADE``).
    ``credentials present on response`` is a malformed-RESPONSE shape, not a request the buyer can
    send — this seller never echoes authentication (write-only), so it is NotImplementedError-
    xfailed here; the no-echo contract is graded on the happy-path wire (``then_account_status`` /
    ``test_happy_path_synced_wire``) and the rejection path (``assert_secret_absent``).
    """
    if boundary == "credentials absent":
        ctx["bva_grade"] = _BVA_GRADE.get(boundary)
        ctx["bva_boundary"] = boundary
        # authentication with schemes but NO credentials — credentials is required (minLength 32).
        agent = _bva_agent(authentication={"schemes": ["Bearer"]})
        _dispatch(ctx, "", idempotency_key=_VALID_KEY, accounts=[_account_entry("acct-bva", [agent])])
    elif boundary == "credentials present on response":
        raise NotImplementedError(
            "credentials-present-on-response is a malformed-RESPONSE shape, not a request rejection; "
            "the write-only no-echo contract is graded on the happy-path + rejection wire instead"
        )
    else:
        raise AssertionError(f"unknown credentials boundary {boundary!r}")


@when(
    parsers.parse(
        'the Buyer Agent sends a sync_governance request exercising the idempotency_key boundary case "{boundary}"'
    )
)
def when_bva_idempotency_key(ctx: dict, boundary: str) -> None:
    """Wire the @T-UC-030-bva-idempotency-key outline (#1329 item 5).

    The two request-validation rows (absent / disallowed character) run and grade invalid via
    ``_BVA_GRADE``. The two replay rows (identical / divergent payload) need idempotency replay
    dedup + IDEMPOTENCY_CONFLICT — unbuilt capability homed on #1934, not request validation — so
    they are NotImplementedError-xfailed here (the registry no longer blanket-xfails the whole tag).
    """
    account = [_account_entry("acct-bva", [_bva_agent()])]
    if boundary == "absent (field not provided)":
        ctx["bva_grade"] = _BVA_GRADE.get(boundary)
        ctx["bva_boundary"] = boundary
        _dispatch(ctx, "", accounts=account)  # idempotency_key omitted
    elif boundary == "valid length, disallowed character (e.g. space)":
        ctx["bva_grade"] = _BVA_GRADE.get(boundary)
        ctx["bva_boundary"] = boundary
        _dispatch(ctx, "", idempotency_key="abcdef 1234567890", accounts=account)
    elif boundary in ("replay: same key + identical payload", "replay: same key + divergent payload"):
        raise NotImplementedError(
            "idempotency replay dedup / IDEMPOTENCY_CONFLICT not implemented (#1934) — "
            f"boundary {boundary!r} needs the replay feature, not just request validation"
        )
    else:
        raise AssertionError(f"unknown idempotency_key boundary {boundary!r}")


@when(parsers.parse('a sync_governance response exercises the per-account status boundary case "{boundary}"'))
def when_bva_sync_account_status(ctx: dict, boundary: str) -> None:
    """Wire the @T-UC-030-bva-sync-account-status outline (#1329 item 5).

    The two valid rows are dispatched as real syncs: an OWNED account resolves to per-account
    ``synced`` (echoing its url), and an unresolvable account resolves to per-account ``failed``
    (carrying a per-account errors[]) — the two members of the {synced, failed} enum. The third
    row ("status value outside the two-member enum") is a malformed-RESPONSE shape the server
    cannot be made to emit via a request, so it is NotImplementedError-xfailed here (#1329).
    """
    if boundary == "status=synced with echoed governance_agents URL":
        account_id = "acct-bva-synced"
        _owned_account(ctx, account_id)
        ctx["bva_status"] = ("synced", account_id)
        _dispatch(ctx, "", idempotency_key=_VALID_KEY, accounts=[_account_entry(account_id, [_bva_agent()])])
    elif boundary == "status=failed with per-account errors[]":
        # An unseeded account is unresolvable → per-account failed (ACCOUNT_NOT_FOUND).
        account_id = "acct-bva-nonexistent"
        ctx["bva_status"] = ("failed", account_id)
        _dispatch(ctx, "", idempotency_key=_VALID_KEY, accounts=[_account_entry(account_id, [_bva_agent()])])
    elif boundary == "status value outside the two-member enum":
        raise NotImplementedError(
            "a per-account status outside the {synced, failed} two-member enum is not a response the "
            "server can be made to emit via a request; the two valid enum members are graded above"
        )
    else:
        raise AssertionError(f"unknown per-account status boundary {boundary!r}")


@then(parsers.parse('the response verdict is "{verdict}"'))
def then_response_verdict(ctx: dict, verdict: str) -> None:
    """Grade a @bva per-account status RESPONSE-shape boundary on the real wire.

    ``valid`` → the seeded account's per-account status is exactly the expected two-member enum
    value (synced / failed) AND carries its discriminator-required shape (a synced entry echoes a
    governance_agents url; a failed entry carries a per-account errors[]). The single unbuildable
    row ("status outside the enum") is NotImplementedError-xfailed at the when-step (#1329).
    """
    if verdict != "valid":
        raise AssertionError(f"unknown response verdict {verdict!r} for the per-account status outline")
    expected_status, account_id = ctx["bva_status"]
    acct = _wire_account(ctx, account_id)
    assert acct["status"] == expected_status, f"account {account_id}: expected {expected_status}, got {acct['status']}"
    assert acct["status"] in {"synced", "failed"}, f"per-account status must be a two-member enum value: {acct}"
    if expected_status == "synced":
        agents = acct.get("governance_agents") or []
        assert agents and agents[0].get("url"), f"synced entry must echo a governance_agents url: {acct}"
    else:
        assert acct.get("errors"), f"failed entry must carry a per-account errors array: {acct}"


@then(parsers.parse('the request verdict is "{verdict}"'))
def then_request_verdict(ctx: dict, verdict: str) -> None:
    """Grade a @bva request-validation boundary on the real wire.

    ``invalid`` → a top-level VALIDATION_ERROR envelope (mutation: relax the boundary check and
    this reddens). ``valid`` → the request is ACCEPTED at the validation boundary; the response is
    the success variant (an unseeded account then fails per-account resolution, which is NOT a
    top-level error), so assert the dispatch did not error (#1329).
    """
    result = ctx["result"]
    if verdict == "invalid":
        # Every invalid @bva row MUST carry a _BVA_GRADE entry (recorded by its when-step), so
        # the field + exact message_substr + suggestion_substr are pinned — a min-vs-max, wrong-
        # field, or missing-suggestion regression reddens, and two different boundaries can no
        # longer pass on one byte-identical assertion (#1329). A missing entry is a
        # loud failure, not a silent degrade to a bare code check.
        grade = ctx.get("bva_grade")
        assert grade is not None, (
            f"invalid @bva boundary has no _BVA_GRADE entry — every invalid row must pin "
            f"field + message_substr + suggestion_substr (#1329); ctx={ctx.get('bva_boundary')!r}"
        )
        result.assert_wire_error("VALIDATION_ERROR", require_suggestion=True, **grade)
    elif verdict == "valid":
        assert not result.is_error, (
            "a boundary-valid request must be accepted at validation (per-account resolution may "
            "still fail on an unseeded account — the success variant, not a top-level error); "
            f"got wire error {wire_error_envelope_or_none(ctx)!r}"
        )
    else:
        raise AssertionError(f"unknown request verdict {verdict!r}")


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
    result = ctx["result"]
    assert result.is_error, f"expected error variant, got response {ctx.get('response')!r}"
    # Route the code-agnostic two-layer structural grade through the single harness accessor
    # (both layers present, codes non-empty AND agreeing, recovery set) instead of hand-digging
    # adcp_error.code/errors[0].code/recovery out of the dict. The SPECIFIC code is pinned by
    # the scenario's following step (`the error code is "X"` / a `then_error_*`). A single-layer
    # or code-less envelope ("flip the code to garbage and this stays green") fails here (#1329).
    result.assert_wire_error_shape()


@then("the response does NOT carry an operation-level errors array")
def then_no_operation_errors(ctx: dict) -> None:
    # Success (partial-failure) variant: per-account results live under accounts[], and
    # there is NO operation-level error envelope (spec oneOf: accounts XOR adcp_error).
    body = wire_dict(ctx)
    # Falsifiable: the ERROR variant carries a top-level adcp_error and NO accounts[], so
    # pinning adcp_error's absence AND accounts' presence grades that this is genuinely the
    # success variant. (The earlier `"errors" not in body` half was vacuous — the response
    # model is extra='forbid', so a top-level errors[] can never appear — #1329.)
    assert body.get("adcp_error") is None, f"expected success variant, got an error envelope: {body}"
    assert body.get("accounts") is not None, f"success variant must carry an accounts array: {body}"


@then(parsers.parse('the account "{account_id}" has status "{status}"'))
@then(parsers.parse('account "{account_id}" has status "{status}" and echoes the governance_agents URL'))
def then_account_status(ctx: dict, account_id: str, status: str) -> None:
    # _wire_account fetches the entry by its echoed ref — the by-id lookup IS the ref-echo
    # grade (it raises "No wire account {id}. Available: ..." if the ref was dropped/wrong),
    # so no separate membership pre-assert (that is redundant against the lookup, #1329).
    acct = _wire_account(ctx, account_id)
    assert acct["status"] == status, f"account {account_id}: expected status {status}, got {acct['status']}"
    if status == "synced":
        agents = acct.get("governance_agents") or []
        assert agents and agents[0].get("url"), f"synced account {account_id} must echo a governance_agents url"
        # Credentials are write-only: a synced echo MUST NOT carry authentication (wire-level).
        assert "authentication" not in agents[0], f"synced echo must not carry credentials: {agents[0]}"


@then(parsers.parse('account "{account_id}" has status "{status}" and carries a per-account errors array'))
def then_account_status_with_errors(ctx: dict, account_id: str, status: str) -> None:
    # _wire_account's by-id lookup IS the ref-echo grade (raises on a dropped/wrong ref);
    # no redundant membership pre-assert (#1329).
    acct = _wire_account(ctx, account_id)
    assert acct["status"] == status, f"account {account_id}: expected status {status}, got {acct['status']}"
    assert acct.get("errors"), f"failed account {account_id} must carry a per-account errors array: {acct}"


@then(parsers.parse('the response account "{account_id}" echoes governance_agents[{idx:d}].url "{url}"'))
def then_echo_url(ctx: dict, account_id: str, idx: int, url: str) -> None:
    # _wire_account's by-id lookup IS the ref-echo grade (raises on a dropped/wrong ref);
    # no redundant membership pre-assert (#1329).
    acct = _wire_account(ctx, account_id)
    agents = acct.get("governance_agents") or []
    actual = agents[idx]["url"]
    assert actual == normalize_url(url), f"account {account_id}: expected echoed url {url}, got {actual}"


@then(parsers.parse('the response account "{account_id}" does NOT echo governance_agents[{idx:d}].authentication'))
def then_no_echo_auth(ctx: dict, account_id: str, idx: int) -> None:
    # _wire_account's by-id lookup IS the ref-echo grade (raises on a dropped/wrong ref);
    # no redundant membership pre-assert (#1329).
    acct = _wire_account(ctx, account_id)
    agents = acct.get("governance_agents") or []
    assert "authentication" not in agents[idx], f"credentials must not be echoed: {agents[idx]}"


@then("the response carries an echoed adcp_version envelope")
def then_adcp_version(ctx: dict) -> None:
    body = wire_dict(ctx)
    # POST-S4: sync_governance now echoes the seller's implemented adcp_version at release
    # precision (_sync_governance_impl -> _WIRE_ADCP_VERSION). Graded on the real wire — the
    # prior in-step xfail is gone; the field IS emitted now (#1329).
    version = body.get("adcp_version")
    assert version, f"expected an echoed adcp_version envelope field, got keys {list(body)}"
    # Release-precision (major.minor) per the wire contract, not patch-precise.
    assert re.fullmatch(r"\d+\.\d+", version), f"adcp_version must be release-precision (major.minor), got {version!r}"


def _failed_wire_account_ids(ctx: dict) -> list[str]:
    """Return the echoed account_ids of the failed per-account entries on the wire.

    Uses status MEMBERSHIP (not a count/bare-truthiness check — the no_count_only guard) to
    assert at least one entry failed, then returns their ids so the per-account graders route
    the errors[] + recovery read through the ONE harness accessor (``assert_account_error``)
    instead of each hand-rolling ``(acct.get("errors") or [{}])[0]`` (#1329).
    """
    accounts = _wire_accounts(ctx)
    statuses = {a.get("status") for a in accounts}
    assert "failed" in statuses, f"expected a failed per-account entry, got statuses {statuses}"
    return [(a.get("account") or {}).get("account_id") for a in accounts if a.get("status") == "failed"]


@then("the per-account errors include an ACCOUNT_NOT_FOUND code")
def then_per_account_authority_code(ctx: dict) -> None:
    """Assert a failed per-account entry carries ACCOUNT_NOT_FOUND on the wire.

    Graduates BR-UC-030 ``sync-no-authority`` from dormant to executing across a2a/mcp/rest, and
    makes the error-code choice wire-graded. Production emits the SINGLE uniform
    ``ACCOUNT_NOT_FOUND`` code — an existing-but-unowned account is indistinguishable from a
    nonexistent one (the ``*_NOT_FOUND`` uniform-response MUST). Routed through the single
    harness per-account accessor, which pins the code + the pinned-enum recovery off the
    accounts[] wire: ``SCOPE_INSUFFICIENT`` (the value the fix removed) fails the code pin, and a
    terminal->transient recovery drift reddens — no hand-rolled scan or literal (#1329).
    """
    result = ctx["result"]
    for account_id in _failed_wire_account_ids(ctx):
        result.assert_account_error(account_id, "ACCOUNT_NOT_FOUND")


@then("the per-account error message does not reveal whether the account exists")
def then_per_account_message_uniform(ctx: dict) -> None:
    """Grade the uniform-response MUST on the wire: the failed per-account message MUST
    NOT carry the authorization-specific ``does not have access to account 'X'`` phrasing
    (which would distinguish exists-but-unowned from not-found — a cross-principal
    enumeration oracle). The failed+code read routes through the single harness accessor;
    the message check is the specific grade this step adds (#1329)."""
    result = ctx["result"]
    for account_id in _failed_wire_account_ids(ctx):
        result.assert_account_error(account_id, "ACCOUNT_NOT_FOUND")
    for acct in _wire_accounts(ctx):
        if acct.get("status") != "failed":
            continue
        for err in acct.get("errors") or []:
            message = err.get("message") or ""
            assert "does not have access" not in message, f"per-account message leaks account existence: {message!r}"


@then(parsers.parse('each per-account error should include a "{field}" field guiding remediation'))
def then_per_account_suggestion(ctx: dict, field: str) -> None:
    # The failed+code+recovery read routes through the single harness accessor; then grade the
    # requested field CONTENT against the pinned enum (the authority) when the enum defines it —
    # presence-only (`e.get(field)`) is a serializer tautology that stays green if production ships
    # any non-empty value, so it would not surface a drift from the canonical ACCOUNT_NOT_FOUND
    # suggestion/recovery. This step is generic over the requested field ("suggestion", "recovery"),
    # so grade each against its own pinned value; a field the enum does not carry falls back to
    # presence (#1329).
    result = ctx["result"]
    for account_id in _failed_wire_account_ids(ctx):
        result.assert_account_error(account_id, "ACCOUNT_NOT_FOUND")
    expected = _pinned_error_metadata().get("ACCOUNT_NOT_FOUND", {}).get(field)
    for acct in _wire_accounts(ctx):
        if acct["status"] != "failed":
            continue
        errs = acct.get("errors") or []
        assert errs, f"failed account {acct.get('account')} must carry a per-account errors array: {acct}"
        values = {e.get(field) for e in errs}
        if expected is not None:
            assert values == {expected}, (
                f"each per-account {field!r} must equal the pinned ACCOUNT_NOT_FOUND {field} {expected!r}, "
                f"got {values} for account {acct.get('account')}"
            )
        else:
            assert all(e.get(field) for e in errs), f"each per-account error must include a non-empty {field!r}: {acct}"


# ═══════════════════════════════════════════════════════════════════════
# Then — persisted binding (replace semantics; reads below the wire)
# ═══════════════════════════════════════════════════════════════════════
#
# The wire echo shows the current sync's result, so proving REPLACE (prior binding gone)
# and per-account SCOPE (an unnamed account untouched) requires reading the persisted row.
# These read it back via the shared session-safe persisted_governance_urls, matching the
# below-wire integration test test_replace_semantics_overwrites_prior_binding (#1329).


@then(parsers.parse('the persisted governance agent on "{account_id}" is "{url}"'))
@then(parsers.parse('the binding on account "{account_id}" remains "{url}" unchanged'))
def then_persisted_binding_is(ctx: dict, account_id: str, url: str) -> None:
    # One body, two phrasings (replace-overwrites vs per-account-scope-unchanged): both
    # assert the persisted binding on the account is EXACTLY [url]. Stacked parsers rather
    # than two identical bodies (#1329; mirrors the stacked @when parsers).
    # An absent/unbound account reads back as [], so the len==1 check also covers persistence.
    urls = persisted_governance_urls(ctx["tenant"].tenant_id, account_id)
    assert urls == [normalize_url(url)], f"expected {account_id} persisted binding == [{url!r}], got {urls}"


@then(parsers.parse('the previous binding to "{url}" is no longer present'))
def then_previous_binding_absent(ctx: dict, url: str) -> None:
    # The replace scenario binds exactly one account; read it back and confirm the old url
    # is gone (replace overwrote, not appended).
    prior = ctx.get("prior_bindings") or {}
    assert prior, "no prior binding recorded by the pre-binding Given"
    account_id = next(iter(prior))
    urls = persisted_governance_urls(ctx["tenant"].tenant_id, account_id)
    assert normalize_url(url) not in urls, f"stale binding {url!r} still present on {account_id}: {urls}"


@then(parsers.parse('the account for brand "{brand}" on operator "{operator}" has status "{status}"'))
def then_natural_key_account_status(ctx: dict, brand: str, operator: str, status: str) -> None:
    accounts = _wire_accounts(ctx)
    assert len(accounts) == 1, f"expected exactly one wire account for the natural-key request, got {accounts}"
    acct = accounts[0]
    assert acct["status"] == status, f"expected status {status!r}, got {acct.get('status')!r}: {acct}"
    ref = acct.get("account") or {}
    assert (ref.get("brand") or {}).get("domain") == brand and ref.get("operator") == operator, (
        f"wire must echo the requested natural key (brand={brand}, operator={operator}), got {ref}"
    )


# ═══════════════════════════════════════════════════════════════════════
# Then — validation / boundary wire errors (governance-specific)
# ═══════════════════════════════════════════════════════════════════════


# These route through the harness's guarded, transport-independent error grader
# (result.assert_wire_error) rather than scanning str(envelope): recovery defaults to the
# pinned AdCP enum (not a hardcoded "correctable"). Field-level violations pin the STRUCTURED
# errors[0].field EXACTLY (both layers, via field=) — a substring token like "accounts" or
# "governance_agents" is a prefix of several governance paths and would stay green on a field
# wrong for the scenario (#1329). field is transport-stable (the MCP TypeAdapter
# boundary diverges on message, not field). The url https-requirement is a model validator, so
# its field is empty → assert the message there. Every request-validation rejection also carries
# a top-level suggestion (require_suggestion=True). Verified against the real per-transport
# envelopes (#1329).
_CREDENTIALS_FIELD = "accounts[0].governance_agents[0].authentication.credentials"
_AGENTS_FIELD = "accounts[0].governance_agents"
# The url gates (userinfo/https/SSRF) now raise a field-located error here (was a bare
# ValueError with an empty field), so the step pins field= too (#1329).
_URL_FIELD = "accounts[0].governance_agents[0].url"
# The extra_forbidden gate for the mistyped `credential` (singular) key rejects HERE — the
# leak channel's exact field, transport-stable across a2a/mcp/rest.
_CREDENTIAL_EXTRA_FIELD = "accounts[0].governance_agents[0].authentication.credential"
# Exact field per credential leak channel — pinned so a leaf wrong for the scenario reddens
# (a shared ...governance_agents[0] prefix would stay green on either leaf) (#1329).
_LEAK_CHANNEL_FIELD = {
    "url-userinfo": _URL_FIELD,
    "extra-authentication-key": _CREDENTIAL_EXTRA_FIELD,
}

_ACCOUNTS_FIELD = "accounts"
_SCHEMES_FIELD = "accounts[0].governance_agents[0].authentication.schemes"
_SCHEMES_ITEM_FIELD = "accounts[0].governance_agents[0].authentication.schemes[0]"

# The expected wire envelope for EVERY invalid @bva boundary row, keyed on the Examples
# `boundary` string (the _LEAK_CHANNEL_FIELD pattern above). Before this, only the
# governance_agents when-step recorded an exact grade and the other three when-steps recorded
# nothing, so `then_request_verdict` degraded to a bare `assert_wire_error("VALIDATION_ERROR")`
# for 8 rows × 3 transports — e.g. `url absent` and `non-uri string` graded by a byte-identical
# assertion (#1329). Every invalid row now pins field + message_substr +
# suggestion_substr; then_request_verdict fails loudly if an invalid row has no entry. Values
# verified against the real per-transport envelope (message_substr distinguishes minItems from
# maxItems on a shared field; suggestion_substr pins the corrective verb).
_BVA_GRADE: dict[str, dict[str, str]] = {
    "governance_agents has 0 entries": {
        "field": _AGENTS_FIELD,
        "message_substr": "at least 1 item",
        "suggestion_substr": "Provide a valid",
    },
    "governance_agents has 2 entries": {
        "field": _AGENTS_FIELD,
        "message_substr": "at most 1 item",
        "suggestion_substr": "Provide a valid",
    },
    "accounts has 0 entries": {
        "field": _ACCOUNTS_FIELD,
        "message_substr": "at least 1 item",
        "suggestion_substr": "Provide a valid",
    },
    "accounts has 101 entries": {
        "field": _ACCOUNTS_FIELD,
        "message_substr": "at most 100 items",
        "suggestion_substr": "Provide a valid",
    },
    "empty array (0 items)": {
        "field": _SCHEMES_FIELD,
        "message_substr": "at least 1 item",
        "suggestion_substr": "Provide a valid",
    },
    "two items": {"field": _SCHEMES_FIELD, "message_substr": "at most 1 item", "suggestion_substr": "Provide a valid"},
    "single item outside enum": {
        "field": _SCHEMES_ITEM_FIELD,
        "message_substr": "Input should be 'Bearer'",
        "suggestion_substr": "Correct the",
    },
    "schemes absent": {
        "field": _SCHEMES_FIELD,
        "message_substr": "Required field is missing",
        "suggestion_substr": "Provide the required",
    },
    "http:// URL (plaintext)": {
        "field": _URL_FIELD,
        "message_substr": "must use https://",
        "suggestion_substr": "Correct the",
    },
    "non-uri string": {
        "field": _URL_FIELD,
        "message_substr": "should be a valid URL",
        "suggestion_substr": "Correct the",
    },
    "url absent": {
        "field": _URL_FIELD,
        "message_substr": "Required field is missing",
        "suggestion_substr": "Provide the required",
    },
    # credentials boundary (@T-UC-030-bva-credentials): the request-validation row. The
    # response-shape row ("credentials present on response") is NotImplementedError-xfailed at
    # the when-step — it is not a request the buyer can send (#1329).
    "credentials absent": {
        "field": _CREDENTIALS_FIELD,
        "message_substr": "Required field is missing",
        "suggestion_substr": "Provide the required",
    },
    # idempotency_key boundary (@T-UC-030-bva-idempotency-key): the two request-validation rows.
    # The replay rows are NotImplementedError-xfailed at the when-step (#1934 unbuilt).
    "absent (field not provided)": {
        "field": "idempotency_key",
        "message_substr": "Required field is missing",
        "suggestion_substr": "Provide the required",
    },
    "valid length, disallowed character (e.g. space)": {
        "field": "idempotency_key",
        "message_substr": "String should match pattern",
        "suggestion_substr": "Provide a valid",
    },
}


@then("the error references the url field and indicates https is required")
def then_error_url_https(ctx: dict) -> None:
    ctx["result"].assert_wire_error(
        "VALIDATION_ERROR", field=_URL_FIELD, message_substr="url must use https", require_suggestion=True
    )


@then("the error references the credentials field")
def then_error_credentials(ctx: dict) -> None:
    ctx["result"].assert_wire_error("VALIDATION_ERROR", field=_CREDENTIALS_FIELD, require_suggestion=True)


@then("the response is a CREDENTIAL_IN_ARGS error on the wire naming the governance agent field")
def then_secret_channel_wire_error(ctx: dict) -> None:
    # Wire-graded: a credential placed in request args (userinfo in the url, or a
    # credential-bearing extra field) is rejected with the pinned CREDENTIAL_IN_ARGS code
    # (recovery=terminal, pin-defaulted; @source authentication.mdx L2) — NOT VALIDATION_ERROR.
    # Pin the EXACT field per leak channel (transport-stable across a2a/mcp/rest) rather than a
    # shared ...governance_agents[0] prefix — a substring stays green even if the leaf is wrong.
    # require_suggestion=True: CREDENTIAL_IN_ARGS carries the pinned "do NOT auto-retry" hint.
    expected_field = _LEAK_CHANNEL_FIELD[ctx["leak_channel"]]
    ctx["result"].assert_wire_error("CREDENTIAL_IN_ARGS", field=expected_field, require_suggestion=True)


@then("the wire envelope does NOT contain the leaked secret")
def then_wire_envelope_omits_secret(ctx: dict) -> None:
    # The security invariant: a rejected credential must never be echoed. Routed through the one
    # harness accessor (assert_secret_absent) which scans the REAL wire (both success body and
    # error envelope) and raises loudly if neither was captured — so disabling the strip/redaction
    # reddens the wire, and the check can't pass vacuously on an empty capture (#1329).
    ctx["result"].assert_secret_absent(ctx["leaked_secret"])


@then("the error references the governance_agents maximum cardinality")
def then_error_cardinality_max(ctx: dict) -> None:
    # maxItems 1 and minItems 1 both point at the same field (accounts[0].governance_agents);
    # the distinguishing token lives in the message ("at most" / "at least 1 item") on all
    # three transports (#1329).
    ctx["result"].assert_wire_error(
        "VALIDATION_ERROR", field=_AGENTS_FIELD, message_substr="at most 1 item", require_suggestion=True
    )


@then("the error references the governance_agents minimum cardinality")
def then_error_cardinality_min(ctx: dict) -> None:
    ctx["result"].assert_wire_error(
        "VALIDATION_ERROR", field=_AGENTS_FIELD, message_substr="at least 1 item", require_suggestion=True
    )


@then("the error references the accounts array size")
def then_error_accounts_size(ctx: dict) -> None:
    ctx["result"].assert_wire_error("VALIDATION_ERROR", field="accounts", require_suggestion=True)


@then("the error code indicates the missing idempotency_key")
def then_error_missing_key(ctx: dict) -> None:
    ctx["result"].assert_wire_error("VALIDATION_ERROR", field="idempotency_key", require_suggestion=True)


@then(parsers.parse('the response outcome is "{outcome}"'))
def then_response_outcome(ctx: dict, outcome: str) -> None:
    # idempotency_key boundary: "accepted" == request passed operation-level validation
    # (success variant, even if per-account resolution failed); "rejected" == a
    # request-validation wire error fired.
    if outcome == "accepted":
        assert ctx.get("error") is None, f"expected accepted, got error {ctx.get('error')!r}"
        _require_response(ctx)
    elif outcome == "rejected":
        # A too-short / malformed idempotency_key violates the request schema, so the
        # rejection is a VALIDATION_ERROR on the wire. Grade the code + pinned-enum recovery
        # + a non-empty top-level suggestion via the guarded helper, not just "an envelope exists".
        ctx["result"].assert_wire_error("VALIDATION_ERROR", field="idempotency_key", require_suggestion=True)
    else:
        raise AssertionError(f"unknown outcome {outcome!r}")
