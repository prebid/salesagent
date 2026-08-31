"""Domain step definitions for UC-010: the get_adcp_capabilities honesty graders.

Wires the BR-UC-010 honesty graders against CapabilitiesEnv so the seller's honest declarations
execute and assert on the real a2a/mcp/rest wire (``dispatch_request`` + ``wire_dict``): the
account-sandbox grader, the specialisms grader, the idempotency-required grader, and the
no-tenant minimal-degradation grader. Dormancy for every OTHER BR-UC-010 scenario is the
undefined-step auto-xfail path — there is NO conftest complement/routing gate for UC-010 (the
"allow N tags, xfail the rest" gate was removed; see the conftest UC-010 branch), so a scenario
with no step definitions auto-xfails without darkening a stepped grader.

Honesty contract (#1329 gap 13): this seller declares ``account.sandbox=false`` UNCONDITIONALLY.
A media buy under a sandbox account routes to the exact same live adapter path as production, so
``account.sandbox`` is the seller's honest "no behavioral isolation" declaration, not a
reflection of any account's stored flag. get_adcp_capabilities is a TENANT-level, no-argument
discovery endpoint; the sandbox outline's rows are graded against the honest, unconditional
``sandbox=false`` on the wire (falsifiable — a dishonest ``sandbox=true`` reddens them across
a2a/mcp/rest), via ``assert_declared_capabilities``. The no-tenant path emits the account section
too (sandbox=false), graded by ``then_no_tenant_minimal``.

ctx["env"] is a CapabilitiesEnv (bound by the conftest UC-010 branch). #1329 (UC-010).
"""

from __future__ import annotations

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_dict
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.helpers import assert_declared_capabilities


@given(parsers.parse("the tenant account is configured for {boundary_point}"))
def given_account_configured_for(ctx: dict, boundary_point: str) -> None:
    """Record the boundary point under test.

    get_adcp_capabilities is a TENANT-level discovery endpoint — ``account.sandbox`` on the
    response is the seller's honest capability declaration (#1329), NOT a reflection of any
    account's stored sandbox flag, so the account configuration named here deliberately does
    not alter the response. The unconditional honest ``sandbox=false`` is what the Then grades.
    """
    ctx["sandbox_boundary"] = boundary_point


@when("the Buyer Agent calls get_adcp_capabilities MCP tool")
def when_call_get_capabilities(ctx: dict) -> None:
    """Dispatch get_adcp_capabilities through the parametrized wire transport.

    The "MCP tool" wording is the Gherkin's; the actual transport (a2a/mcp/rest) is driven by
    ``pytest_generate_tests`` via ctx["transport"] (the scenario carries no transport tag), so
    the honesty declaration is graded on every wire — mirrors the UC-030 dispatch convention.
    get_adcp_capabilities is an auth-optional, no-argument discovery call (no request body).

    When the ``no tenant can be resolved`` Given ran (``ctx["has_tenant"] is False``), dispatch
    with an identity whose tenant is None so production takes the minimal (no-tenant) branch on
    the real wire — the auth-optional discovery path exercised across a2a/mcp/rest (#1329 item 5).
    """
    if ctx.get("has_tenant") is False:
        from tests.harness.transport import TRANSPORT_PROTOCOL, Transport

        transport = ctx["transport"]
        transport = transport if isinstance(transport, Transport) else Transport(str(transport).lower())
        dispatch_request(ctx, identity=ctx["env"].no_tenant_identity(TRANSPORT_PROTOCOL[transport]))
    else:
        dispatch_request(ctx)
    # Discovery is read-only and auth-optional (POST-F1): it must SUCCEED for every boundary
    # point (all resolve to a valid response). Assert that here so an auth/wiring regression
    # surfaces as this step, not as a confusing missing-section in the Then.
    assert ctx.get("error") is None, f"get_adcp_capabilities discovery must not error: {ctx.get('error')!r}"


@then("the response reflects the honest no-tenant minimal capabilities shape")
def then_no_tenant_minimal(ctx: dict) -> None:
    """Grade the no-tenant (minimal) capabilities response on the real wire (#1329 item 5).

    BR-RULE-052 INV-1: no tenant → minimal response. In THIS seller the minimal response STILL
    carries the honest account capability section (sandbox=false, no behavioral isolation ships)
    and omits media_buy details — the account section is NOT absent (the generated .feature's
    "should NOT include account section" was wrong against the shipped shape). The full honesty
    envelope (account section, specialisms, idempotency posture, supported_protocols) is graded
    through the single coupling grader; a dishonest sandbox=true reddens it across a2a/mcp/rest.
    """
    body = wire_dict(ctx)
    assert_declared_capabilities(body)
    adcp = body.get("adcp") or {}
    majors = [m.get("root") if isinstance(m, dict) else m for m in (adcp.get("major_versions") or [])]
    assert 3 in majors, f"no-tenant response must include adcp.major_versions containing 3: {adcp}"
    # Minimal: media_buy details are omitted when no tenant resolves.
    assert body.get("media_buy") is None, f"no-tenant minimal response must omit media_buy details: {body}"
    # NB: adcp.supported_versions is NOT part of this seller's shipped shape yet (production
    # _adcp_metadata emits major_versions + idempotency only); that v3.1 gap is tracked by the
    # dormant @T-UC-010-v31-supported-versions scenario, not asserted here (#1329 item 5).


@then(parsers.parse("the capabilities response should be {expected} for the sandbox flag"))
def then_capabilities_sandbox_flag(ctx: dict, expected: str) -> None:
    """Grade the #1329 sandbox honesty on the real wire for every boundary row.

    This seller has no behavioral sandbox isolation, so get_adcp_capabilities (a tenant-level,
    no-argument discovery endpoint) declares ``account.sandbox=false`` UNCONDITIONALLY — it does
    not read per-account state or perform provisioning, so the boundary_point does not drive the
    grade. The outline's fourth row was re-expressed from a (unobservable) provisioning-rejection
    to the honest "provisioning requested → still declares false" case, so all four rows are
    ``valid`` and grade the same unconditional honest declaration (#1329) — no per-row
    xfail. Falsifiable: a dishonest ``sandbox=true`` reddens every row across a2a/mcp/rest.
    """
    if expected != "valid":
        raise AssertionError(f"unknown expected verdict {expected!r} for the sandbox-flag outline")
    # Grade the WHOLE declared-honesty envelope on the real wire through the single coupling
    # grader (#1329): account.{sandbox, require_operator_auth, required_for_products,
    # account_financials, supported_billing} + adcp.idempotency.{supported, no
    # replay_ttl_seconds} + specialisms — and it fails on any emitted-but-ungraded field, so a
    # new declared capability cannot ship dark. This scenario configures no tenant
    # supported_billing, so production takes the default-fallback branch whose honest value is
    # exactly {operator, agent}. Falsifiable: a dishonest sandbox=true or a re-flipped
    # idempotency.supported=true reddens this across a2a/mcp/rest.
    assert_declared_capabilities(wire_dict(ctx))


@given("the tenant has full capabilities configured")
def given_tenant_full_capabilities(ctx: dict) -> None:
    """Record that the scenario intends a fully-configured tenant (informational).

    get_adcp_capabilities declares its idempotency posture UNCONDITIONALLY
    (_adcp_metadata → supported=False), so this Given does not drive the response — the
    Then grades the emitted posture on the wire (#1329).
    """
    ctx["full_capabilities"] = True


@then("adcp.idempotency should be present in the response")
def then_idempotency_present(ctx: dict) -> None:
    """v3.1 REQUIRES adcp.idempotency on every capabilities response — grade its presence."""
    body = wire_dict(ctx)
    adcp_meta = body.get("adcp") or {}
    assert "idempotency" in adcp_meta, f"adcp.idempotency must be present (v3.1 required): {adcp_meta}"


@then("adcp.idempotency.supported should be a boolean discriminator")
def then_idempotency_supported_boolean(ctx: dict) -> None:
    """The union discriminator ``supported`` must be a real boolean on the wire.

    This seller declares the honest ``supported=false`` (Idempotency3) variant; grade that it
    is a boolean discriminator (not absent/null) — the withdrawal VALUE itself is graded by
    assert_declared_capabilities on the sandbox scenario + the integration wire test (#1329).
    """
    body = wire_dict(ctx)
    supported = (body.get("adcp") or {}).get("idempotency", {}).get("supported")
    assert isinstance(supported, bool), f"adcp.idempotency.supported must be a boolean discriminator, got {supported!r}"


@given(parsers.parse("the tenant claims specialisms {specialisms}"))
def given_tenant_claims_specialisms(ctx: dict, specialisms: str) -> None:
    """Record the storyboard's CLAIMED specialisms list (informational).

    This seller derives its specialisms from an HONESTY AUDIT — a hardcoded, audited set
    (_DECLARED_SPECIALISMS) — NOT from tenant config, so the claim here does NOT drive the
    response. The Thens grade the EMITTED list (what the seller honestly advertises), which is
    the real obligation for an honesty-pass seller (#1329).
    """
    ctx["claimed_specialisms"] = specialisms


@then("specialisms should be a unique array of kebab-case enum IDs")
def then_specialisms_kebab_unique(ctx: dict) -> None:
    """Grade the emitted specialisms on the real wire through the single coupling grader.

    Routes through ``assert_declared_capabilities`` (#1329) rather than
    re-implementing its specialism asserts — the grader pins ``specialisms`` BY VALUE against
    the production audit table (kebab-case enum ids by construction) and unique. Falsifiable: a
    specialism the wire declares that the audit does not reddens the exact-set check.
    """
    assert_declared_capabilities(wire_dict(ctx))


@then("each specialism should roll up to a protocol in supported_protocols")
def then_specialisms_roll_up(ctx: dict) -> None:
    """Every emitted specialism's parent protocol must be in supported_protocols.

    Parent protocols are derived from the production ``_SPECIALISM_AUDIT`` table (the SSOT),
    NOT a hand-copied map — a test whose oracle is a hand-copy of the claim it grades cannot
    catch that claim being wrong (#1329). A KeyError on an emitted specialism absent
    from the audit is a loud failure (the audit must cover every declared id).
    """
    from adcp.types.generated_poc.enums.specialism import AdcpSpecialism

    from src.core.tools.capabilities import _SPECIALISM_AUDIT

    body = wire_dict(ctx)
    specialisms = body.get("specialisms") or []
    protocols = set(body.get("supported_protocols") or [])
    assert protocols, f"supported_protocols must be present to grade rollup: {body}"
    for s in specialisms:
        parent = _SPECIALISM_AUDIT[AdcpSpecialism(s)].parent_protocol  # loud on an un-audited id
        assert parent in protocols, (
            f"specialism {s!r} rolls up to protocol {parent!r}, absent from supported_protocols {protocols}"
        )
