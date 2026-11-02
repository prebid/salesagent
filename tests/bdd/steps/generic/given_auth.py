"""Authentication steps: what credential a scenario presents, and what it dispatches with.

These steps set up the authentication state in ``ctx`` that When/Then steps
rely on. They are generic across all use cases — any scenario that needs
an authenticated buyer, a missing tenant, or a sandbox account can reuse them.

The one ``@when`` here belongs with them rather than in a use case's own module: it
presents no credential and nothing else, which is tool-agnostic because the tool is the
env's declaration. Two use cases had written that body separately.
"""

from __future__ import annotations

from pytest_bdd import given, parsers, when

from tests.bdd.steps.generic._account_resolution import ensure_tenant_principal
from tests.bdd.steps.generic._dispatch import dispatch_request

# ── Authenticated / tenant-present paths ────────────────────────────


# `a valid tenant context exists`, `the Buyer has tenant context` and `the Buyer has tenant
# context via MCP session` bound here. None of the three occurs in tests/bdd/features, by
# literal grep and by matching against all 49534 sentences rendered from every feature's
# Examples. They set `ctx["has_tenant"]`, which no step anywhere read — the key is gone,
# along with every other write-never-read flag in this tree. The MCP variant also
# assigned `ctx["transport"] = "mcp"`, which would have overwritten the parametrized
# transport the whole suite dispatches on — a live scenario binding it would have silently
# run every transport's copy against MCP.


# ── Missing-auth / missing-tenant paths ─────────────────────────────


@given("the Buyer has no authentication credentials")
@given("the request has no valid authentication")
@given("the request has an empty principal_id")
@given("no authentication context")
def given_buyer_no_auth(ctx: dict) -> None:
    """Buyer has no authentication credentials at all.

    The credential presents no token and still addresses the env's tenant, so every
    dispatcher sends a token-less request to a known seller and the REAL resolver
    answers it (AUTH_MISSING on a protected tool) — nothing is simulated (#1417).
    ``ctx["credential"]`` is the key the When steps read to override the env's own
    authenticated credential.
    """
    ctx["has_auth"] = False
    ctx["credential"] = ctx["env"].credential(token=None)


@when("the Buyer Agent sends a delivery metrics request without authentication")
@when("the Buyer Agent sends a list_accounts request without an authentication token")
def when_dispatch_without_credential(ctx: dict) -> None:
    """Dispatch this scenario's tool presenting the env's tenant and NO token.

    One body, two sentences, for the reason ``given_buyer_no_auth`` above has four: the
    tool is the ENV's declaration, selected by the scenario's routing tag, so a step that
    presents no credential is tool-agnostic and the sentences differ only in what they
    name. They were two functions, in uc004_delivery and uc011_accounts, with
    byte-identical bodies.

    The tenant is addressed so the request reaches a known seller, and nothing is
    presented for it to verify, which is 3.1.1's AUTH_MISSING state ("no Authorization
    header was included in the request"). The refusal comes from the real resolver.

    A scenario whose Given already established a credential needs no such When: the
    dispatcher presents ``ctx["credential"]`` itself. This step is for the scenarios whose
    only statement about auth is the When.
    """
    dispatch_request(ctx, credential=ctx["env"].credential(token=None))


@given("a presented credential that resolves to no principal")
def given_credential_resolves_to_no_principal(ctx: dict) -> None:
    """A credential IS presented and verification fails: the pin's AUTH_INVALID case.

    3.1.1 ``enums/error-code.json`` splits the two refusals on whether anything was
    presented. ``AUTH_INVALID``: "Credentials were presented but rejected — revoked,
    malformed signature, or a key no longer in the seller's keystore. Sellers MUST
    return this code when an ``Authorization`` header was present but verification
    failed", recovery ``terminal``. ``AUTH_MISSING``: "No credentials were presented",
    which is the sibling row on no authentication context, recovery ``correctable``.

    Nothing is simulated. The header carries a token that hashes to no principals row,
    so the REAL resolver rejects it before a protected tool runs — which is what the
    row asserts by saying the refusal happens before any database access.
    """
    from tests.harness._base import INVALID_TOKEN

    ctx["has_auth"] = True
    ctx["credential"] = ctx["env"].credential(token=INVALID_TOKEN)


@given("no hostname-based tenant resolution is possible")
def given_no_hostname_tenant(ctx: dict) -> None:
    """No tenant can be resolved from hostname.

    In process there is no hostname to resolve from: the tenant reaches the resolver
    through the credential's ``x-adcp-tenant``, so "no hostname resolution" holds
    exactly when the request carries no token — which the preceding "the Buyer has no
    authentication credentials" Given establishes. This step checks that pairing
    rather than setting a ``hostname_tenant`` key no step read; the sentence used
    to hold whether or not the scenario had actually removed the credential.
    """
    credential = ctx.get("credential")
    assert credential is not None and "Authorization" not in credential, (
        "Step claims no hostname-based tenant resolution is possible, but the "
        "scenario still presents a credential that resolves one — the request would "
        f"reach a principal anyway: {credential!r}"
    )


@given("no tenant can be resolved from the request context")
def given_no_tenant_resolved(ctx: dict) -> None:
    """No tenant can be resolved from any source — so there is no SELLER, not merely no buyer.

    Expressed as NO HEADERS AT ALL, not as a token-less credential. A token-less credential
    is what "the Buyer has no authentication credentials" already means (20 feature lines),
    and that is a different state: a buyer presenting nothing still reached a host, and the
    host is what names the tenant. The capabilities request cannot name one --
    ``get-adcp-capabilities-request.json`` declares only ``protocols``, ``context`` and
    ``ext`` -- so the connection is the sole channel, and losing a credential does not lose
    the seller.

    Both sentences used to produce the identical dispatch, which meant these scenarios were
    served WITH a tenant and graded the opposite of what they say.

    An empty headers dict is what makes ``_detect_tenant`` match nothing, so the resolver
    itself produces the state: no principal, no tenant_id, and hence no tenant context.
    """
    ctx["has_auth"] = False
    ctx["credential"] = {}


# ── Sandbox / production account ─────────────────────────────────────


def _seed_account_for_principal(ctx: dict, *, sandbox: bool) -> None:
    """Seed an Account with the given sandbox flag, reachable by the scenario principal.

    Writes the Account and AgentAccountAccess rows and commits them, so the
    identity resolves to an account carrying that flag.

    Seeds the row AND names it as the request's account (``ctx["account_ref"]``), for
    the When steps that carry one: ``account`` is a request field -- optional on
    get_media_buys and get_products, REQUIRED on create_media_buy, update_media_buy and
    sync_creatives -- and a request that named some other account would grade that
    account's flag, not this one's. Tools whose When sends no account still reach it
    through the principal's access grant.
    """
    from adcp.types import AccountReference, AccountReferenceById

    from tests.factories.account import AccountFactory, AgentAccountAccessFactory

    env = ctx["env"]
    # The account hangs off the scenario's tenant and principal, which an EARLIER Given
    # usually seeded — but not always: a scenario whose only other Given is the
    # Background's authentication reached this step with no ctx["tenant"] and raised
    # KeyError. The shared bootstrap is a no-op when they are already there.
    ensure_tenant_principal(ctx, env)
    account = AccountFactory(tenant=ctx["tenant"], sandbox=sandbox)
    AgentAccountAccessFactory(tenant=ctx["tenant"], principal=ctx["principal"], account=account)
    env._commit_factory_data()
    ctx["account_ref"] = AccountReference(root=AccountReferenceById(account_id=account.account_id))
    ctx.setdefault("tenant_id", "sandbox_tenant" if sandbox else "prod_tenant")


@given("the request targets a sandbox account")
def given_sandbox_account(ctx: dict) -> None:
    """Seed a sandbox account for the principal (the token infers the account)."""
    _seed_account_for_principal(ctx, sandbox=True)


@given("the request targets a production account")
def given_production_account(ctx: dict) -> None:
    """Seed a production (non-sandbox) account for the principal."""
    _seed_account_for_principal(ctx, sandbox=False)


@given("the Buyer is authenticated")
@given(parsers.parse('the Buyer is authenticated as principal "{principal_id}" on tenant "{tenant_id}"'))
@given("the Buyer is authenticated with a valid principal_id")
@given("the Buyer Agent has an authenticated connection")
@given(parsers.parse("the Buyer Agent has an authenticated connection via {transport}"))
def given_buyer_authenticated(
    ctx: dict,
    transport: str | None = None,
    principal_id: str | None = None,
    tenant_id: str | None = None,
) -> None:
    """P01 — a buyer with a valid identity. THE authentication setup, 381 feature lines.

    Five sentences, one function, ONE body. The collapse is proven at the
    implementation, not at the wording: every spelling registered here was
    measured to normalize (``ast.dump`` with attributes stripped) to exactly the
    two statements below, so they are interchangeable by construction rather
    than by anyone judging them similar. ``the Buyer is authenticated with a
    valid principal_id`` (234 feature lines) and ``the Buyer Agent has an
    authenticated connection`` (118) were two functions in
    ``steps/domain/uc011_accounts.py`` with byte-identical bodies, saying the
    same thing in different words because nothing forced them to meet.

    ``the Buyer is authenticated`` is the CANONICAL spelling — the one a feature
    file should be written in when the scenario carries no principal/tenant data
    of its own. The other three sentences are legacy spellings kept so the ~295
    feature lines still using them keep resolving; pytest-bdd matches on text, so
    deleting a spelling breaks every scenario that uses it.

    ``principal_id``/``tenant_id`` are the P01 parameters: when a scenario names
    who it authenticates as, the env is re-pointed FIRST and
    ``ensure_tenant_principal`` then seeds exactly that pair (the factories read
    the env's ids), so the named identity is the one that reaches the wire. When
    the sentence names neither — every one of the 381 lines today — both are
    ``None``, nothing is switched, and the body is byte-for-byte the collapsed
    one. This is deliberately NOT the same step as
    ``uc003_ext_error_scenarios.given_buyer_authenticated_as`` (``... as
    principal "P"``, no tenant): that one has a DIFFERENT normalized body
    (``authenticate_env_as`` — a principal switch with no tenant/principal
    seeding), so it is not P01 however similar it reads, and it is left alone.

    ``transport`` is parsed and DISCARDED, and that is not an oversight in this
    function: ``pytest_generate_tests`` parametrizes every scenario over
    a2a/mcp/rest, so a sentence naming one either lies or defeats the
    parametrization. The 29 feature lines that say "via <transport>" are a
    Gherkin-generation defect; the parameter is accepted so they keep resolving
    until the generator stops emitting them, and ignored so they cannot pin.
    """
    env = ctx["env"]
    named = tenant_id is not None or principal_id is not None

    # REFUSE rather than silently seed nothing. ensure_tenant_principal returns
    # early when ctx already holds a tenant, so naming a DIFFERENT principal/tenant
    # after one is established would re-point the env at the named pair and seed
    # nothing for it -- _resolve_auth_token then finds no Principal row and returns
    # None, and the scenario runs UNAUTHENTICATED while its own sentence says
    # otherwise. That is the quiet failure this repo forbids, and it is worse than a
    # crash: the scenario still reports a result, just not the one it names.
    #
    # The refusal turns on the pair DIFFERING, not merely on ctx already holding a
    # tenant. An env route may seed the identity before any Given runs -- UC-019's
    # route seeds tenant + "buyer-001" and its Background then names "buyer-001" --
    # and naming the pair that is already established is a no-op, not a conflict.
    # Refusing it would reject the scenario for agreeing with its own seed.
    if named and "tenant" in ctx:
        established = (
            getattr(ctx.get("tenant"), "tenant_id", None),
            getattr(ctx.get("principal"), "principal_id", None),
        )
        wanted = (tenant_id or established[0], principal_id or established[1])
        if wanted != established:
            raise AssertionError(
                f"this scenario already authenticated as {established} before naming "
                f"principal={principal_id!r} tenant={tenant_id!r}, so the named pair would "
                f"be switched to but never seeded, and the request would go out "
                f"unauthenticated. Name the identity in the FIRST authentication step of "
                f"the scenario (or its Background), not in a later one."
            )
        return

    if tenant_id is not None:
        env.switch_tenant(tenant_id)
    if principal_id is not None:
        env.switch_principal(principal_id)
    ctx["has_auth"] = True
    ensure_tenant_principal(ctx, env)
