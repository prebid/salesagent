"""BDD step definitions for UC-006: Sync Creatives — account resolution scenarios.

Focuses on account partition/boundary scenarios that test resolve_account()
in the sync_creatives context. The account resolution logic is shared with
UC-002 (create_media_buy) — same resolve_account(), same exceptions.

Steps dispatch through CreativeSyncEnv which exercises sync_creatives wrappers
(MCP/A2A/REST) that call enrich_identity_with_account() → resolve_account().

HOW A CREATIVE IS BUILT HERE, because the next step written in this file will copy
whatever it finds. Every creative item goes through
``CreativeAssetRequestFactory.payload()`` — the REQUEST model's factory, never a
hand-typed dict and never ``CreativeAssetFactory``, which builds the RESPONSE model.
Three rules follow from its override contract (tests/factories/request.py):

* ``format_id`` is ALWAYS stated. The factory's default normalises ``AGENT_URL`` to
  a trailing slash and names ``creative.adcontextprotocol.org``, while these
  scenarios talk to ``env.DEFAULT_AGENT_URL`` — inheriting it would silently change
  WHICH creative agent the payload names.
* ``assets`` is stated only when it differs from the conformant baseline. A step
  that says nothing about assets means "any valid asset will do", which is exactly
  what the baseline supplies.
* A deliberate wrongness is an OVERRIDE plus a declaration: ``assets=OMIT`` /
  ``format_id=None`` for the bytes, ``malformed(kind, why, ..., pin_rejects=...)``
  around them for the claim. Dropping the override lets a factory default repair the
  scenario, and ``assert_declared_malformations`` reports that at dispatch.

"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from pytest_bdd import given, parsers, then, when

from src.core.errors.codes import ErrorCode
from tests.bdd.steps._harness_db import db_session
from tests.bdd.steps._outcome_helpers import is_e2e, payload_or_none, require_payload, wire_field
from tests.bdd.steps.generic._account_resolution import ensure_tenant_principal
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories import CreativeFactory
from tests.factories.creative_asset import (
    assert_assets,
    asset_spec,
    build_assets,
    image_spec,
    text_spec,
    url_spec,
)
from tests.factories.malformed import malformed
from tests.factories.principal import PrincipalFactory
from tests.factories.request import OMIT, CreativeAssetRequestFactory
from tests.harness.creative_sync import creative_fingerprint
from tests.harness.media_buy_create import OMIT_ACCOUNT, OMIT_IDEMPOTENCY_KEY
from tests.helpers.account_seeding import seed_account_with_access, seed_natural_key_matches

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

# ═══════════════════════════════════════════════════════════════════════
# E2E format helpers — real creative agent data for Docker transport
# ═══════════════════════════════════════════════════════════════════════

# Docker's creative agent URL (internal to Docker network, used by the app
# container) — https via the shared tls-proxy front; the seam requires https
# unconditionally now (#1757), so this stopped being reachable at all once
# ADCP_OUTBOUND_ALLOW_INSECURE was deleted, and this literal was the one thing
# in this file that was never caught by that disease scan (it hardcodes a URL,
# not the flag itself).
_E2E_AGENT_URL = "https://creative-agent.adcp.test:8443/api/creative-agent"
# Real format that exists in Docker's creative agent catalog
_E2E_FORMAT_ID = "display_300x250_image"
# A creative agent that does not answer, on the wire: the TLS front's own name (so the
# egress gate resolves it and lets the dial through) at a port nothing listens on. A
# made-up host fails DNS inside the gate instead, which is a VALIDATION_ERROR on the
# buyer's agent_url -- a different outcome from an agent that is down.
_E2E_UNREACHABLE_AGENT_URL = "https://creative-agent.adcp.test:8444/api/creative-agent"


def _format_payload(ctx: dict, env: object) -> tuple[str, str, dict]:
    """Return (format_id, agent_url, assets) appropriate for current transport.

    For in-process transports: uses mock format/agent that the patched registry accepts.
    For e2e_rest: uses real format from Docker's creative agent catalog.
    """
    if is_e2e(ctx):
        return (
            _E2E_FORMAT_ID,
            _E2E_AGENT_URL,
            build_assets(
                image_spec("banner_image", url="https://example.com/banner.png"),
                url_spec("click_url", url="https://example.com/landing"),
            ),
        )
    # SDK 5.7 / AdCP 3.1: creative assets are a discriminated union keyed by role.
    # Build via the canonical AssetSpec mechanism (image_spec/build_assets) so the
    # shape lives in exactly one place — see GH #1391.
    return (
        "display_300x250",
        env.DEFAULT_AGENT_URL,
        build_assets(image_spec("image")),
    )


def latest_creative_id(ctx: dict) -> str:
    """The id of the creative the scenario most recently declared.

    ONE spelling of one fact. ``ctx["creatives"]`` holds the payloads that go on
    the wire, so the id inside the last one is the id production is about to see;
    51 sites already read it that way.

    ``ctx["creative_id"]`` was a scalar mirror, written by five Given steps
    immediately after they appended that very payload, and five readers spelled
    the choice ``ctx.get("creative_id") or ctx["creatives"][-1]["creative_id"]``.
    The mirror won that ``or``, so a scenario that declared a SECOND creative
    afterwards built its assignment against the FIRST one's id -- silently, and
    only in the five places that consulted the mirror. Two representations of one
    fact do not stay equal; the wire payload is the one that decides.
    """
    creatives = ctx["creatives"]
    assert creatives, "No creative declared yet — a Given must append a creative payload first"
    return creatives[-1]["creative_id"]


def _product_format_entry(ctx: dict, env: object) -> dict[str, str]:
    """Return a single ``{"agent_url": ..., "id": ...}`` for ProductFactory.format_ids.

    Must match the creative format_id returned by ``_format_payload`` so that
    format compatibility checks pass on all transports including e2e_rest.
    """
    if is_e2e(ctx):
        return {"agent_url": _E2E_AGENT_URL, "id": _E2E_FORMAT_ID}
    return {"agent_url": env.DEFAULT_AGENT_URL, "id": "display_300x250"}


def _scenario_format_entry(ctx: dict, env: object) -> dict[str, str]:
    """The ``{id, agent_url}`` a PERSISTED creative row must carry for this scenario.

    Both halves together, and that is the point. A format's identity is the PAIR
    ``(agent_url, id)`` -- ``format_id_identity`` in src/core/schemas/_base.py treats it that
    way -- so switching the id while leaving the agent_url pinned to the in-process default
    produces a row claiming the real catalog format at an agent that does not serve it, which
    resolves to nothing. That is the same defect with the halves swapped,
    and it is easy to introduce while fixing the original: the id is the visible half.

    The id honours ``ctx["creative_format_id"]`` when a Given set one, because a scenario
    testing a specific format must persist that format. The agent_url always comes from the
    transport switch: formats live at the transport's own agent whichever id is named.
    """
    _default_id, agent_url, _assets = _format_payload(ctx, env)
    return {"id": _scenario_format_id(ctx, env), "agent_url": agent_url}


def _creative_format_id_entry(ctx: dict, env: object) -> dict[str, str]:
    """The ``format_id`` object a creative payload carries, for the current transport.

     The creative-side twin of :func:`_product_format_entry`. Both read the same switch, which
     is the whole point: a creative and the product it is checked against must name the same
     format on every transport, and they only did in-process by coincidence
    .

     Returns only the identity. Callers that also need the matching ASSETS -- the two formats
     have different asset ids, so a switched format with unswitched assets fails just as
     surely -- take them from :func:`_format_payload` directly.
    """
    format_id, agent_url, _assets = _format_payload(ctx, env)
    return {"id": format_id, "agent_url": agent_url}


def _scenario_format_id(ctx: dict, env: object) -> str:
    """The format id this scenario is using: what a Given recorded, else the transport default.

    ``ctx["creative_format_id"]`` is this module's carrier for "the format under test" -- 30
    steps write it and several read it back. The reads used to default to the literal
    ``"display_300x250"``, which is the quiet half of the same defect: a scenario that never
    wrote the key got the IN-PROCESS format on every transport, including e2e_rest where the
    creative it is compared against had switched to ``display_300x250_image``.

    Defaulting through :func:`_format_payload` keeps the fallback on the same switch as the
    value it stands in for. A literal default is the same bug one level down.
    """
    recorded = ctx.get("creative_format_id")
    if recorded:
        return recorded
    format_id, _agent_url, _assets = _format_payload(ctx, env)
    return format_id


def _e2e_unique_id(prefix: str) -> str:
    """Generate a UUID-based unique ID for e2e tests.

    Factory sequences produce predictable IDs (mb_0000, pkg_0000) that collide
    with rows persisted in Docker's Postgres from prior runs. Use this for any
    factory object created for e2e_rest.
    """
    import uuid

    from tests.factories.mint import mint

    return mint(f"{prefix}_{uuid.uuid4().hex[:8]}")


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — request setup and account state
# ═══════════════════════════════════════════════════════════════════════


def _assignments_for_the_wire(
    assignments: dict[str, list[str]], terms: dict[tuple[str, str], dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """The ctx map, as the AdCP 3.1 assignments ARRAY the transports accept.

    The scenarios build (and two steps READ) assignments as the 2.5 map
    {creative_id: [package_ids]}. 3.1 declares an array of {creative_id, package_id}, so the
    map is rejected at the MCP and REST boundaries and the sync never runs -- which made two
    transactional-ordering scenarios look like data-integrity failures when they were really
    setup rejections. (a2a kept passing, since it forwards the raw bag to _impl; that is the
    evidence the ordering behaviour itself is intact.)

    Translated ONCE here rather than rewriting ~40 setup sites: the ctx shape stays map-like
    for the two steps that index into it, and only the wire form changes.

    ``terms`` carries the pin's optional per-entry fields (``weight``, ``placement_ids``)
    keyed by (creative_id, package_id) -- ``ctx["assignment_terms"]``, written by the Givens
    that ask for them through :func:`_ask_assignment_terms`.
    """
    return [
        {"creative_id": creative_id, "package_id": package_id, **(terms or {}).get((creative_id, package_id), {})}
        for creative_id, package_ids in assignments.items()
        for package_id in package_ids
    ]


def _ask_assignment_terms(ctx: dict, creative_id: str, package_id: str, **terms: Any) -> None:
    """Record the optional per-entry fields one (creative, package) assignment should carry."""
    ctx.setdefault("assignment_terms", {}).setdefault((creative_id, package_id), {}).update(terms)


@given("a creative with a known format_id")
def given_creative_with_format(ctx: dict) -> None:
    """Set up a creative payload with a known format_id for sync_creatives dispatch.

    Ensures tenant/principal exist, then builds a creative payload dict matching
    the shape that _sync_creatives_impl expects (CreativeAsset-compatible dict).
    Stores the payload in ctx["creatives"] for the When step to consume.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)

    format_id, agent_url, assets = _format_payload(ctx, env)
    creative_id = "creative-known-fmt-001"
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id=creative_id,
        name="Test Creative with Known Format",
        format_id={"id": format_id, "agent_url": agent_url},
        assets=assets,
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = format_id
    ctx["creative_agent_url"] = agent_url


@given(parsers.parse("account is {account_setup}"))
def given_account_is(ctx: dict, account_setup: str) -> None:
    """Set up account state from the scenario table's JSON or sentinel value.

    Parses account_setup as JSON to build an AccountReference, or handles
    sentinel values like "not provided".
    """
    from adcp.types import AccountReference, AccountReferenceById, AccountReferenceByNaturalKey, BrandReference

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant, principal = ctx["tenant"], ctx["principal"]

    if account_setup == "not provided":
        # ABSENT on the wire, not defaulted: sync-creatives-request.json lists account in
        # /required, so the request must genuinely omit it for the schema to refuse it.
        # A None here used to be replaced by the harness's default account.
        ctx["account_ref"] = OMIT_ACCOUNT
        return

    # Parse JSON account setup
    config = json.loads(account_setup)

    # Both branches of core/account-ref.json's oneOf at once: sent verbatim, so the
    # request model's union refuses it (each branch forbids the other's fields).
    if "account_id" in config and "brand" in config:
        ctx["account_ref"] = config
        return

    if "account_id" in config:
        account_id = config["account_id"]
        ctx["account_ref"] = AccountReference(root=AccountReferenceById(account_id=account_id))
        ctx["request_account_id"] = account_id

        # Create DB state based on known account IDs from the spec
        _setup_account_by_id(account_id, tenant, principal)

    elif "brand" in config:
        brand_domain = config["brand"]["domain"]
        operator = config["operator"]
        ctx["account_ref"] = AccountReference(
            root=AccountReferenceByNaturalKey(brand=BrandReference(domain=brand_domain), operator=operator),
        )
        ctx["request_brand"] = brand_domain
        ctx["request_operator"] = operator

        # Create DB state based on known domain patterns
        _setup_account_by_natural_key(brand_domain, operator, tenant, principal)


def _setup_account_by_id(account_id: str, tenant: object, principal: object) -> None:
    """Create DB state for account_id-based scenarios."""
    # Accounts that exist but belong to a different principal (AUTHORIZATION_ERROR)
    access_denied_ids = {"acc_other_agent"}

    status_map = {
        "acc_acme_001": "active",
        "acc_new_unconfigured": "pending_approval",
        "acc_overdue": "payment_required",
        "acc_suspended": "suspended",
    }
    status = status_map.get(account_id)

    if account_id in access_denied_ids:
        # Account exists but the test principal has no access — triggers AUTHORIZATION_ERROR
        domain = account_id.replace("_", "-") + ".com"
        other_principal = PrincipalFactory(tenant=tenant)
        seed_account_with_access(
            tenant, other_principal, account_id=account_id, status="active", brand_domain=domain, operator=domain
        )
        return

    if status is None:
        # Unknown account_id — don't create (tests not-found path)
        return

    # BrandReference domain must match ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]...)$
    # Replace underscores with hyphens for valid domains
    domain = account_id.replace("_", "-") + ".com"
    seed_account_with_access(
        tenant, principal, account_id=account_id, status=status, brand_domain=domain, operator=domain
    )


def _setup_account_by_natural_key(brand_domain: str, operator: str, tenant: object, principal: object) -> None:
    """Create DB state for natural-key-based scenarios."""
    # Domains where the account exists but belongs to a different principal (AUTHORIZATION_ERROR)
    access_denied_domains = {"other-agent.com"}

    if brand_domain == "multi.com":
        # Ambiguous: 3 accounts one brand+operator reference all resolve to, each
        # accessible to the requesting agent so the ambiguity is genuine FOR THIS
        # AGENT — natural-key resolution is access-scoped (#1417).
        seed_natural_key_matches(
            tenant,
            count=3,
            brand_domain=brand_domain,
            operator=operator,
            owner_for_index=lambda _i: principal,
            account_id_prefix="acc-multi",
        )
    elif brand_domain in ("unknown.com",):
        # Not found — don't create anything
        pass
    elif brand_domain in access_denied_domains:
        # Account exists but the test principal has no access — triggers AUTHORIZATION_ERROR
        other_principal = PrincipalFactory(tenant=tenant)
        seed_account_with_access(
            tenant,
            other_principal,
            account_id=f"acc-{brand_domain.replace('.', '-')}",
            status="active",
            brand_domain=brand_domain,
            operator=operator,
        )
    else:
        # Single match — create one active account
        seed_account_with_access(
            tenant,
            principal,
            account_id=f"acc-{brand_domain.replace('.', '-')}",
            status="active",
            brand_domain=brand_domain,
            operator=operator,
        )


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — send request
# ═══════════════════════════════════════════════════════════════════════


@when("the Buyer Agent syncs the creatives")
@when("the Buyer Agent syncs the creative")
@when("the Buyer Agent syncs the creative via the REST/A2A endpoint")
@when("the Buyer Agent syncs the creative via the MCP tool")
@when("the Buyer Agent sends a sync_creatives request")
@when("the Buyer Agent sends sync_creatives")
@when("the Buyer Agent sends sync_creatives with the corrected manifest")
def when_sync_creative(ctx: dict) -> None:
    """Send sync_creatives request with account reference through transport dispatch.

    The wrappers call enrich_identity_with_account() → resolve_account(),
    exercising the full account resolution chain across all transports.

    Always dispatches — even when account_ref is None or invalid — because
    the step text says "syncs the creative". Error handling is the production
    code's responsibility, not the step's.

    A no-auth Given's ``ctx["credential"]`` (a token-less credential, or one addressing
    a tenant that does not exist) is presented by ``dispatch_request`` itself, so the
    resolver's refusal fires on the wire without this step reading the key.

    ``ctx["signed"]`` asks for a REAL RFC 9421 signature on this dispatch. It is
    forwarded verbatim rather than branched on or collapsed with ``bool(...)``: it is a
    realization, not a flag (False / True / "malformed" / "tampered"), and what a
    signature IS on each transport belongs to the dispatcher. Defaulting to False keeps
    every existing scenario byte-identical.
    """
    account_ref = ctx.get("account_ref")
    creatives = ctx.get("creatives", [])
    kwargs: dict = {"account": account_ref, "creatives": creatives, "signed": ctx.get("signed", False)}
    if "assignments" in ctx:
        kwargs["assignments"] = _assignments_for_the_wire(ctx["assignments"], ctx.get("assignment_terms"))
    if "assignment_entries" in ctx:
        # Raw entries, verbatim: the shape a scenario about the ARRAY itself (an entry
        # missing one of its required fields) needs, which the ctx map cannot express.
        kwargs["assignments"] = ctx["assignment_entries"]
    if "validation_mode" in ctx:
        kwargs["validation_mode"] = ctx["validation_mode"]
    if "idempotency_key" in ctx:
        kwargs["idempotency_key"] = ctx["idempotency_key"]
    if "push_notification_config" in ctx:
        kwargs["push_notification_config"] = ctx["push_notification_config"]
    if "dry_run" in ctx:
        kwargs["dry_run"] = ctx["dry_run"]
    if "delete_missing" in ctx:
        kwargs["delete_missing"] = ctx["delete_missing"]
    if "creative_ids" in ctx:
        kwargs["creative_ids"] = ctx["creative_ids"]
    dispatch_request(ctx, **kwargs)


def _action_str(action: object) -> str:
    """Normalize a SyncCreativeResult.action to a plain string.

    Handles both enum instances (action.value) and raw strings.
    """
    if action is None:
        return "None"
    return str(action.value) if hasattr(action, "value") else str(action)


def _ensure_tenant_principal_from_db(ctx: dict, env: object) -> None:
    """Like _ensure_tenant_principal, but resolve existing DB rows first.

    When a prior Given step (e.g., ``the Buyer is authenticated as principal "X"``)
    triggers ``_ensure_default_data_for_auth``, the tenant and principal are
    created in the DB but not stored in ctx. Calling ``setup_default_data()``
    again would fail with a duplicate-key error. This helper checks the DB first.
    """
    if "tenant" in ctx:
        return

    session = env.get_session()
    if session is not None:
        from sqlalchemy import select

        from src.core.database.models import Principal, Tenant

        tenant = session.scalars(select(Tenant).filter_by(tenant_id=env._tenant_id)).first()
        if tenant is not None:
            principal = session.scalars(
                select(Principal).filter_by(
                    principal_id=env._principal_id,
                    tenant_id=env._tenant_id,
                )
            ).first()
            if principal is not None:
                ctx["tenant"] = tenant
                ctx["principal"] = principal
                return

    ensure_tenant_principal(ctx, env)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — account-specific assertions
# ═══════════════════════════════════════════════════════════════════════


@then("the request should proceed with resolved account")
def then_proceed_with_resolved_account(ctx: dict) -> None:
    """Assert account resolution succeeded and processing proceeded to a DB write.

    What "proceed with resolved account" means here, and what each assertion
    actually tests against production:

    1. No error + correct response type. This is the core resolution check:
       enrich_identity_with_account() → resolve_account() ran without raising,
       so the account_id/natural key was found and passed access + status checks.
       The sibling "account is <invalid>" scenarios prove the contrast — there
       resolve_account() RAISES and this assertion would fail.
    2. A creative result with a success action (created/updated/unchanged).
    3. The creative was actually PERSISTED. This is the load-bearing DB check:
       on a resolution failure _sync_creatives_impl never runs and nothing is
       persisted, so ``creative is not None`` distinguishes the success path
       from the error path. (The previous code guarded this behind a condition
       that was always false, so it silently verified nothing.)

    The principal-scoping line is a secondary cross-principal isolation guard,
    NOT a verification of account resolution: resolution never changes
    principal_id and the Creative table has no account column, so the creative
    is always scoped to the authenticated principal regardless of which account
    resolved. It is kept as cheap defense against a mis-scoping regression.
    """
    from src.core.schemas import SyncCreativesResponse

    # 1. Response succeeded with correct type
    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    resp = require_payload(ctx)
    assert isinstance(resp, SyncCreativesResponse), f"Expected SyncCreativesResponse, got {type(resp).__name__}"

    # 2. Production processed the request (account resolution succeeded)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, "Expected at least one creative result — account resolution should allow processing"
    actions = [str(getattr(getattr(r, "action", None), "value", getattr(r, "action", None))) for r in results]
    assert any(a in ("created", "updated", "unchanged") for a in actions), (
        f"Expected a success action proving account was resolved, got {actions}"
    )

    # 3. The load-bearing DB check: the creative was actually persisted.
    #    On a resolution failure _sync_creatives_impl never runs and nothing is
    #    written, so this is what proves processing proceeded past resolution.
    env = ctx["env"]
    # A missing DB session is a HARNESS defect, not a production gap: production's behaviour cannot influence whether the env opened one. Excusing it as an expected failure meant the persistence claim silently graded nothing.
    assert getattr(env, "use_real_db", False), (
        "this env has no real DB, so 'the creative was persisted' cannot be verified. "
        "Run this scenario under an IntegrationEnv, or the claim does not belong here."
    )

    # Authenticated principal the creative must be scoped to (isolation guard).
    # Given steps expose it as ctx["principal_id"] (string) or ctx["principal"]
    # (Principal object set by _ensure_tenant_principal).
    expected_principal = ctx.get("principal_id")
    if not expected_principal and ctx.get("principal") is not None:
        expected_principal = getattr(ctx["principal"], "principal_id", None)
    assert expected_principal, "Test setup error: no expected principal in ctx to verify account resolution"

    creative_id = latest_creative_id(ctx)
    from src.core.database.models import Creative as CreativeModel

    creative = env.get_one(
        CreativeModel,
        tenant_id=ctx.get("tenant_id", "test_tenant"),
        creative_id=creative_id,
    )
    assert creative is not None, (
        f"Creative '{creative_id}' was not persisted — account resolution did not lead to "
        f"processing under the resolved account"
    )
    assert creative.principal_id == expected_principal, (
        f"Creative was persisted under principal '{creative.principal_id}', "
        f"but account resolution should have scoped to '{expected_principal}'"
    )


def _extract_error_code_and_suggestion(ctx: dict, error: object) -> tuple[str | None, str | None]:
    """(error_code, suggestion) — from the WIRE first, then the payload's own Error.

    STRICT error.json conformance: ``suggestion`` is a top-level attribute, never a
    copy buried in the free-form ``details`` dict (#1417).

    Two populations reach this, and only one of them is a wire rejection:

    * a request-level rejection -> read ``errors[0]`` off the captured envelope.
      This used to read ``error.error_code`` off an exception the harness rebuilt
      from those same bytes; with the reconstruction gone that
      object is a ``WireError`` carrying the envelope, and reading it by attribute
      returns None.
    * a per-creative outcome from a PARTIAL SUCCESS -> an ``adcp.types.Error`` in
      ``response.errors``. That is not a reconstruction and stays supported: the spec
      puts per-record failures on the payload layer.
    """
    result = ctx.get("result")
    wire = result.wire_error_object() if result is not None else None
    if wire:
        return wire.get("code"), wire.get("suggestion")
    code = getattr(error, "error_code", None) or getattr(error, "code", None)
    return code, getattr(error, "suggestion", None)


@then(parsers.parse("the error should be {error_code} with suggestion"))
def then_error_code_with_suggestion(ctx: dict, error_code: str) -> None:
    """Assert error has the expected error_code and includes a suggestion.

    Accepts both src.core.exceptions.AdCPSalesAgentError and adcp.types.Error shapes —
    different UCs dispatch through different error hierarchies.
    """
    error = ctx.get("error")
    assert error is not None, f"Expected error {error_code} but none was recorded"

    actual_code, suggestion = _extract_error_code_and_suggestion(ctx, error)
    assert actual_code == error_code, (
        f"Expected error code '{error_code}', got '{actual_code}' ({type(error).__name__}: {error})"
    )
    assert suggestion, f"Expected non-empty suggestion on {error_code} error, got {suggestion!r} ({error!r})"


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — approval mode scenarios (BR-RULE-037)
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('a creative with name "{name}" and a known format_id'))
def given_creative_with_name_and_format(ctx: dict, name: str) -> None:
    """Set up a creative payload with a specific name and a known format_id."""
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)

    format_id, agent_url, assets = _format_payload(ctx, env)
    creative_id = f"creative-{name.lower().replace(' ', '-')}-001"
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id=creative_id,
        name=name,
        format_id={"id": format_id, "agent_url": agent_url},
        assets=assets,
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = format_id


#: The three creative approval modes BR-RULE-037 defines. Named once so the two
#: Given spellings and the writer's own validation cannot disagree about the set.
_CREATIVE_APPROVAL_MODES = ("auto-approve", "require-human", "ai-powered")

#: What production falls back to when the tenant configures no approval_mode
#: (BR-RULE-037 INV-1). Stated by the Gherkin's "not configured" / "" rows.
_DEFAULT_APPROVAL_MODE = "require-human"

#: The Slack endpoint a require-human tenant is configured with. Deliberately a
#: name that does not resolve: the seller's obligation is to DIAL the configured
#: webhook, and every observable of that (the in-process sender mock, the
#: ``webhook_deliveries`` row the live server writes before it dials) is recorded
#: ahead of the network, so reachability is not part of what is graded.
_SLACK_WEBHOOK_URL = "https://hooks.slack.test/approval"


@given(parsers.parse('the tenant has approval_mode "{mode}"'))
def given_tenant_has_approval_mode(ctx: dict, mode: str) -> None:
    """Set approval_mode on the tenant (partition scenario)."""
    _set_tenant_approval_mode(ctx, mode)


@given('the tenant has approval_mode ""')
@given("the tenant has no approval_mode configured")
def given_tenant_no_approval_mode(ctx: dict) -> None:
    """The tenant configures no approval mode, so production's default applies.

    Two Gherkin spellings of ONE state — the partition outline's empty ``mode``
    cell and the INV-1 scenario's sentence — so they share one step function
    rather than two identical bodies (``test_architecture_bdd_no_duplicate_steps``
    is the guard, and it is making the right point: two bodies could drift into
    configuring two different things while both sentences claim "not configured").
    """
    _set_tenant_approval_mode(ctx, _DEFAULT_APPROVAL_MODE)


@given(parsers.re(r"the tenant approval mode is (?P<approval_mode>.+)"))
def given_tenant_approval_mode_creative(ctx: dict, approval_mode: str) -> None:
    """Set tenant approval_mode for creative sync boundary scenarios.

    Handles creative approval modes: not configured, "auto-approve",
    "require-human", "ai-powered". Also delegates to the UC-003 step function
    for media buy modes (auto-approval, manual).

    Goes through ``_set_tenant_approval_mode`` — the SAME writer the partition
    outline's ``the tenant has approval_mode "<mode>"`` uses — rather than
    assigning ``tenant.approval_mode`` itself. Two spellings of one precondition
    had drifted into two behaviours: this one set the mode and nothing else, so
    the boundary outline's require-human row ran with NO slack_webhook_url and its
    "a review workflow should be created with Slack notification" was unsatisfiable.
    It passed anyway, because the assertion behind that sentence used to read the
    notification STEP's call count instead of the sender.
    """
    stripped = approval_mode.strip().strip('"')
    if stripped in ("not configured", "not set"):
        _set_tenant_approval_mode(ctx, _DEFAULT_APPROVAL_MODE)
    elif stripped in _CREATIVE_APPROVAL_MODES:
        _set_tenant_approval_mode(ctx, stripped)
    else:
        from tests.bdd.steps.domain.uc003_update_media_buy import given_tenant_approval_mode

        given_tenant_approval_mode(ctx, approval_mode)


def _configure_tenant_field(ctx: dict, field: str, value: object) -> None:
    """Write one tenant field for BOTH auth paths, through the env's own writer.

    Writing the ORM row is not enough: ``_sync_creatives_impl`` reads the tenant's
    fields off the RESOLVED IDENTITY's tenant dict (_sync.py logs "Tenant
    approval_mode field: NOT FOUND" when one is absent) and falls back to its
    default, so an ORM-only Given silently grades the default instead of what the
    scenario names. ``configure_tenant_field`` is the env-owned writer that
    updates the tenant overrides AND the DB column and clears the identity cache.

    One helper because four Given steps in this module needed the same statement
    and three of them had drifted onto the ORM row.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    env.configure_tenant_field(field, value)
    env._commit_factory_data()


def _set_tenant_approval_mode(ctx: dict, mode: str) -> None:
    """Configure the tenant's approval mode, plus what that mode implies.

    require-human is the one mode whose scenarios also need a Slack endpoint —
    BR-RULE-037 sends only on that branch, and only with a webhook configured —
    so the writer that sets the mode sets the endpoint with it. A Given that set
    the mode alone left the require-human boundary row unable to satisfy its own
    "with Slack notification" sentence.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)

    if mode not in _CREATIVE_APPROVAL_MODES:
        raise ValueError(f"Unknown approval mode: {mode}")

    _configure_tenant_field(ctx, "approval_mode", mode)
    if mode == "require-human":
        _configure_tenant_field(ctx, "slack_webhook_url", _SLACK_WEBHOOK_URL)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — approval mode assertions (BR-RULE-037)
# ═══════════════════════════════════════════════════════════════════════


@contextmanager
def _readback(ctx: dict) -> Iterator[Session]:
    """A session that re-reads rows PRODUCTION wrote, on every transport.

    ``db_session`` already hands back a session bound to the database the request
    under test actually writes: the per-test base in process, and the live
    server's own base over e2e_rest (the env's factories write there, and
    ``_db_scope_for`` points production's cached engine at the same URL for the
    scenario duration — ``_production_db_pointed_at``, tests/bdd/conftest.py).
    A read-back therefore needs no transport branch, which is why the skip this
    replaced was never the right primitive.

    What it does need is ``expire_all``. Production committed through its OWN
    session, so any row this one already loaded — every Given-created tenant,
    media buy or creative — is still served out of its identity map at the value
    the factory gave it. Over e2e_rest that is the whole difference between
    reading the media buy's new status and re-reading the fixture's. Same reason
    ``tests/harness/webhook_registration.py`` expires before its read-backs.
    """
    with db_session(ctx) as session:
        session.expire_all()
        yield session


def _get_creative_from_db(ctx: dict) -> object:
    """Retrieve the synced creative from the DB for status assertion.

    FIXME(#2236): this reads the creative as a raw ORM row and every caller then
    walks ``creative.data`` as an untyped ``JSONType`` blob — a chain of ``.get``
    calls with ``or {}`` at each hop, because the blob's shape is whatever the
    writer happened to store. A test asking "what provenance does this asset
    carry?" should ask a typed model, not a dict; a raw ``select()`` in a test
    body is the same defect one layer down (it is the reason this helper exists
    instead of a repository method).

    The class of defect — creative identity and creative payloads modelled as
    name-keyed dicts rather than typed values — is what #2236 (RFC:
    Re-engineer the creative format model on canonical format kinds) proposes to
    remove. Fix it there, as part of the creative redesign; do not paper over the
    next ``NoneType has no attribute 'get'`` with another ``or {}``.
    """
    from sqlalchemy import select

    from src.core.database.models import Creative

    tenant = ctx["tenant"]
    principal = ctx["principal"]
    with _readback(ctx) as session:
        creative = session.scalars(
            select(Creative).filter_by(
                tenant_id=tenant.tenant_id,
                principal_id=principal.principal_id,
            )
        ).first()
        assert creative is not None, (
            f"No creative found in DB for tenant={tenant.tenant_id}, principal={principal.principal_id}"
        )
        return creative


def _assert_workflow_steps(env: object, *, expect_present: bool) -> list:
    """Assert workflow steps exist or not, returning the steps list.

    Uses the harness's public get_workflow_steps() (tenant-scoped via the
    WorkflowStep -> Context relationship) rather than touching the harness's
    private session attribute.
    """
    steps = env.get_workflow_steps()
    if expect_present:
        assert steps, "Expected workflow steps but none were created"
        for step in steps:
            assert step.step_type == "creative_approval", (
                f"Expected step_type 'creative_approval', got '{step.step_type}'"
            )
            assert step.owner == "publisher", f"Expected owner 'publisher', got '{step.owner}'"
            assert step.status == "requires_approval", f"Expected status 'requires_approval', got '{step.status}'"
    else:
        assert not steps, (
            f"Expected no workflow steps, but found {len(steps)}: {[(s.step_type, s.status) for s in steps]}"
        )
    return steps


def _assert_success_response(ctx: dict) -> None:
    """Assert dispatch succeeded with no error."""
    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    assert payload_or_none(ctx) is not None, "Expected a response but got None"


def _creative_status_values() -> frozenset[str]:
    """The pinned CreativeStatus enum, read from the SDK rather than retyped.

    sync-creatives-response.json: "Values come from CreativeStatus only
    (processing, pending_review, approved, suspended, rejected, archived) — never
    from CreativeAction."
    """
    from adcp.types.generated_poc.enums.creative_status import CreativeStatus

    return frozenset(member.value for member in CreativeStatus)


def _assert_creative_status(ctx: dict, status: str, *, why: str) -> None:
    """The creative sits at *status* after this sync, read off the WIRE.

    The wire is the authoritative observable here, not the ``creatives`` row.
    sync-creatives-response.json (adcp 3.1.1, creative/sync-creatives-response.json)
    defines the per-creative ``status`` as the "advisory review-lifecycle state of
    the creative after this sync", drawn from CreativeStatus — which is exactly
    what BR-RULE-037's approval modes decide. The row carries the same value as an
    implementation detail of storing it.

    Reading the row instead would also be unstable on the live stack for one of
    the four modes: ai-powered hands the creative to a real background reviewer
    that opens its own unit of work and COMMITS a new ``status`` onto the row
    (src/admin/blueprints/creatives.py ``_ai_review_creative``), so a row read
    after the response races that thread. The response cannot be rewritten after
    it is sent, so the wire grades the sync's own decision and nothing else.
    """
    _assert_success_response(ctx)
    entry = _wire_creatives_entry(ctx, latest_creative_id(ctx))
    assert entry.get("status") == status, f"{why}: expected status {status!r} on the wire, got {entry.get('status')!r}"


@then(parsers.parse('the creative status should be "{status}"'))
def then_creative_status_should_be(ctx: dict, status: str) -> None:
    """Assert the creative's advisory status matches the expected value."""
    _assert_creative_status(ctx, status, why="BR-RULE-037")


@then(parsers.parse('workflow steps created should be "{workflow}"'))
def then_workflow_steps_created(ctx: dict, workflow: str) -> None:
    """Assert whether workflow steps were created."""
    env = ctx["env"]
    if workflow == "none":
        _assert_workflow_steps(env, expect_present=False)
    elif workflow == "yes":
        _assert_workflow_steps(env, expect_present=True)
    else:
        raise ValueError(f"Unknown workflow expectation: {workflow}")


@then("the creative should use require-human as default")
def then_creative_use_require_human_default(ctx: dict) -> None:
    """Assert that when approval_mode is not configured, require-human is the default (INV-1)."""
    _assert_creative_status(ctx, "pending_review", why="INV-1: default approval mode")
    _assert_workflow_steps(ctx["env"], expect_present=True)


def _assert_slack_notified(ctx: dict, creative_ids: list[str], *, why: str) -> None:
    """Slack was dialled for exactly *creative_ids* during this sync.

    Read through ``env.slack_notified_creatives``, which is the SENDER on every
    transport — the in-process mock of ``get_slack_notifier``, and over e2e_rest
    the ``webhook_deliveries`` row the server's own sender leaves behind. The
    notification STEP is not the observable: production enters
    ``_send_creative_notifications`` for every creative needing approval and
    decides inside it whether Slack is reached.

    Naming the creatives rather than counting calls is what makes the mode
    attributable. Production dials Slack only on the require-human branch
    (src/core/tools/creatives/_workflow.py), so "Slack named THIS creative" is
    already the statement that the branch under test ran, and the empty-list
    assertions in INV-2/INV-4/INV-6 grade the same accessor.
    """
    _assert_success_response(ctx)
    notified = ctx["env"].slack_notified_creatives
    assert notified == creative_ids, f"{why}: expected Slack sends for {creative_ids}, got {notified}"


@then(parsers.parse('the per-creative result should carry advisory status "{status}"'))
def then_per_creative_result_carries_status(ctx: dict, status: str) -> None:
    """The scenario's creative entry carries *status* on the wire.

    sync-creatives-response.json: the per-creative ``status`` is the "advisory
    review-lifecycle state of the creative after this sync", drawn from CreativeStatus;
    "sellers with async review return processing or pending_review; sellers with
    synchronous review MAY return a terminal value (approved, rejected)".

    The same claim BR-RULE-037's own sentences make, so the same assertion —
    ``_assert_creative_status`` carries the reasoning about why the wire and not
    the row.
    """
    _assert_creative_status(ctx, status, why="the per-creative advisory status")


@then("the creative status should be set to approved immediately")
def then_creative_approved_immediately(ctx: dict) -> None:
    """Assert auto-approve sets status to approved with no workflow (INV-2)."""
    _assert_creative_status(ctx, "approved", why="INV-2: auto-approve")
    _assert_workflow_steps(ctx["env"], expect_present=False)


@then("a review workflow should be created with Slack notification")
def then_review_workflow_with_slack(ctx: dict) -> None:
    """Assert require-human creates workflow + sends Slack notification (INV-3)."""
    _assert_creative_status(ctx, "pending_review", why="INV-3: require-human")
    _assert_workflow_steps(ctx["env"], expect_present=True)
    _assert_slack_notified(ctx, [latest_creative_id(ctx)], why="INV-3: require-human + webhook configured")


@then("a review workflow should be created with AI review")
def then_review_workflow_with_ai(ctx: dict) -> None:
    """Assert ai-powered creates workflow + submits AI review (INV-4).

    Verifies:
    - Creative status is pending_review
    - Workflow steps exist with creative_approval type
    - The workflow step is attributable to ai-powered mode (the approval_mode
      configured in the Given step produced this workflow, confirming AI review
      was triggered, not just a human publisher-approval flow)
    """
    _assert_creative_status(ctx, "pending_review", why="INV-4: ai-powered")
    steps = _assert_workflow_steps(ctx["env"], expect_present=True)
    # Verify the workflow step references the synced creative
    creative_id = latest_creative_id(ctx)

    from sqlalchemy import select

    from src.core.database.models import ObjectWorkflowMapping

    with _readback(ctx) as session:
        mappings = list(
            session.scalars(
                select(ObjectWorkflowMapping).filter_by(
                    step_id=steps[0].step_id,
                )
            ).all()
        )
    assert mappings, (
        f"INV-4: workflow step {steps[0].step_id} has no object mappings — "
        f"cannot confirm AI review targets creative {creative_id}"
    )
    mapped_ids = [m.object_id for m in mappings]
    assert creative_id in mapped_ids, f"INV-4: workflow step maps to {mapped_ids}, expected creative {creative_id}"

    # The step records the approval mode that produced it in its request_data
    # (src/core/tools/creatives/_workflow.py), which is what distinguishes an AI-review
    # step from a human-only one. This used to poke ``step.metadata``, which on an ORM
    # row is SQLAlchemy's MetaData, not a column.
    recorded_mode = (steps[0].request_data or {}).get("approval_mode")
    assert recorded_mode == "ai-powered", (
        f"INV-4: the workflow step records approval_mode={recorded_mode!r}; expected 'ai-powered' "
        f"(request_data={steps[0].request_data!r})"
    )


@then("a workflow step should be created for the Seller")
def then_workflow_step_for_seller(ctx: dict) -> None:
    """Assert a workflow step was created with owner=publisher (the Seller).

    The Seller in this domain is the publisher. Explicitly verify the first
    workflow step's owner is 'publisher' at this step level (not just via
    the shared helper) to make the 'for the Seller' claim visible.
    """
    _assert_success_response(ctx)
    steps = _assert_workflow_steps(ctx["env"], expect_present=True)
    # Explicitly assert owner == "publisher" (the Seller) at step level
    assert steps[0].owner == "publisher", (
        f"Expected workflow step for the Seller (owner='publisher'), got owner='{steps[0].owner}'"
    )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — assignment format compatibility (mwtk) + package boundary (0xwq)
#   + assignments-structure boundary (ceox)
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("assignments to a package with {product_setup}"))
def given_assignments_to_package_with_setup(ctx: dict, product_setup: str) -> None:
    """Create a media buy + package whose product matches the Gherkin setup phrase.

    Supported phrases (from the assignment_format partition scenario):
      - ``product accepting the creative's format`` — product format_ids matches creative
      - ``product with empty format_ids`` — no restrictions
      - ``package with no product_id`` — format check skipped entirely
      - ``product accepting only a different format`` — format mismatch

    The creative always carries the format its transport's agent serves (the rows used
    to name a literal one, which the real e2e agent does not serve, so the "matches" rows
    failed the creative before any assignment ran); only the product's set varies.
    """
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    # The product declares its formats on the SAME agent the creative names, on every
    # transport: e2e_rest's creative is served by the Docker agent, not the in-process
    # default, and a product pinned to the default url would mismatch there on the rows
    # that say the formats match.
    agent_url = _scenario_format_entry(ctx, env)["agent_url"]

    # Create media buy for the package to belong to.
    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    product = None
    package_config: dict = {"budget": 1000.0}

    if product_setup == "product accepting the creative's format":
        product = ProductFactory(tenant=tenant, format_ids=[_scenario_format_entry(ctx, env)])
        package_config["product_id"] = product.product_id
    elif product_setup == "product with empty format_ids":
        product = ProductFactory(tenant=tenant, format_ids=[])
        package_config["product_id"] = product.product_id
    elif product_setup == "package with no product_id":
        # Package has no product_id — format compatibility check is skipped.
        pass
    elif product_setup == "product accepting only a different format":
        # Same agent, different id: identity is the (canonical agent_url, id) PAIR, so
        # this is not a match (formerly graded on its own as BR-RULE-039 INV-2).
        product = ProductFactory(tenant=tenant, format_ids=[{"agent_url": agent_url, "id": "video_30s"}])
        package_config["product_id"] = product.product_id
    else:
        raise ValueError(f"Unknown product_setup phrase: {product_setup!r}")

    package = MediaPackageFactory(
        media_buy=media_buy,
        package_config=package_config,
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    ctx["product"] = product
    # assignments payload for _sync_creatives_impl: dict[creative_id -> list[package_id]]
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {creative_id: [package.package_id]}


@given(parsers.parse('validation_mode is "{mode}"'))
def given_validation_mode(ctx: dict, mode: str) -> None:
    """Set validation_mode on the sync_creatives request (strict or lenient)."""
    ctx["validation_mode"] = mode


# --- 0xwq: assignment package boundary (existing pkg / existing assignment / missing pkg) ---


@given("an assignment to a package that exists in the tenant")
def given_assignment_to_existing_package(ctx: dict) -> None:
    """Create an existing package in the tenant and assign the creative to it."""
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]

    # Use UUID-based IDs for e2e_rest to avoid collisions with Docker's persistent DB.
    mb_kwargs: dict = {"tenant": tenant, "principal": principal, "status": "active"}
    prod_kwargs: dict = {"tenant": tenant, "format_ids": [_product_format_entry(ctx, env)]}
    if is_e2e(ctx):
        mb_kwargs["media_buy_id"] = _e2e_unique_id("mb")
        prod_kwargs["product_id"] = _e2e_unique_id("prod")

    media_buy = MediaBuyFactory(**mb_kwargs)
    # Product accepts the default known format so the format check passes.
    product = ProductFactory(**prod_kwargs)
    pkg_kwargs: dict = {
        "media_buy": media_buy,
        "package_config": {"product_id": product.product_id, "budget": 1000.0},
    }
    if is_e2e(ctx):
        pkg_kwargs["package_id"] = _e2e_unique_id("pkg")
    package = MediaPackageFactory(**pkg_kwargs)
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {creative_id: [package.package_id]}


def _seed_library_creative(ctx: dict, creative_id: str) -> None:
    """Seed *creative_id* in the caller's own library, on the request creative's format.

    An assignment-only reference: the request must still carry a creative
    (sync-creatives-request.json: ``creatives`` minItems 1), so the scenario's anchor
    creative stays in ``ctx["creatives"]`` and this row is the one the assignment names.
    """
    from tests.factories import CreativeFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    fmt = ctx["creatives"][-1]["format_id"]
    CreativeFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        creative_id=creative_id,
        name="Library Creative",
        agent_url=fmt["agent_url"],
        format=fmt["id"],
    )
    env._commit_factory_data()


@given(
    parsers.parse(
        'an assignment referencing the unknown creative "{creative_id}" to a package that exists in the tenant'
    )
)
def given_assignment_unknown_creative_existing_package(ctx: dict, creative_id: str) -> None:
    """The package is real; the creative_id the assignment names has no row anywhere."""
    given_assignment_to_existing_package(ctx)
    ctx["assignments"] = {creative_id: [ctx["package"].package_id]}


@given(
    parsers.parse(
        'an assignment referencing the library creative "{creative_id}" to a package that exists in the tenant'
    )
)
def given_assignment_library_creative_existing_package(ctx: dict, creative_id: str) -> None:
    """Both ends exist: a creative already in the library, a package in the tenant."""
    _seed_library_creative(ctx, creative_id)
    given_assignment_to_existing_package(ctx)
    ctx["assignments"] = {creative_id: [ctx["package"].package_id]}


@given(parsers.parse('an assignment referencing the library creative "{creative_id}" to the package "{package_id}"'))
def given_assignment_library_creative_named_package(ctx: dict, creative_id: str, package_id: str) -> None:
    """The creative exists in the library; the package id is whatever the sentence says (here: none)."""
    _seed_library_creative(ctx, creative_id)
    ctx["assignments"] = {creative_id: [package_id]}


@given("the creative has an empty name")
def given_creative_has_empty_name(ctx: dict) -> None:
    """Blank the request creative's name.

    core/creative-asset.json requires ``name`` but sets no minLength, so an empty string
    is a request the schema admits and the seller's own per-item validation refuses --
    the same mechanism BR-RULE-033 INV-1's "one with an empty name" pair relies on.
    """
    ctx["creatives"][-1]["name"] = ""


def _wire_creatives_entry(ctx: dict, creative_id: str) -> dict:
    """The ONE ``creatives[]`` entry on the wire whose creative_id is *creative_id*."""
    entries = wire_field(ctx, "creatives")
    matches = [entry for entry in entries if entry.get("creative_id") == creative_id]
    assert len(matches) == 1, (
        f"expected exactly one creatives entry for {creative_id!r} on the wire, found {len(matches)} "
        f"among {[entry.get('creative_id') for entry in entries]}"
    )
    return matches[0]


@then(parsers.parse('the creatives entry for "{creative_id}" is assigned to the package'))
def then_creatives_entry_assigned_to_package(ctx: dict, creative_id: str) -> None:
    """The wire entry selected by creative_id names the scenario's package in ``assigned_to``."""
    entry = _wire_creatives_entry(ctx, creative_id)
    expected = [ctx["package"].package_id]
    assert entry.get("assigned_to") == expected, (
        f"expected the entry for {creative_id!r} to carry assigned_to={expected}, got {entry.get('assigned_to')!r}"
    )


@then(parsers.parse('the creatives entry for "{creative_id}" reports the package as an assignment error'))
def then_creatives_entry_reports_package_assignment_error(ctx: dict, creative_id: str) -> None:
    """The entry names the scenario's package in ``assignment_errors`` and assigns nothing.

    Both halves matter: ``assignment_errors`` keyed by the package is the report the
    response schema defines for a skipped assignment, and an empty ``assigned_to`` is
    what proves it was skipped rather than attempted (GH #1418: the attempt was an FK
    violation surfacing as a 500).
    """
    entry = _wire_creatives_entry(ctx, creative_id)
    package_id = ctx["package"].package_id
    assignment_errors = entry.get("assignment_errors") or {}
    assert package_id in assignment_errors, (
        f"expected assignment_errors on the entry for {creative_id!r} to name {package_id!r}, got {assignment_errors!r}"
    )
    assert not entry.get("assigned_to"), (
        f"a creative that failed validation must not be assigned, but the entry carries assigned_to={entry.get('assigned_to')!r}"
    )


def _seed_absent_library_creative(ctx: dict) -> str:
    """A creative already in the library that the request will NOT mention.

    It is the observable subject of delete_missing: a full-library replace archives
    it and reports ``deleted``; every other scope leaves it out of the response.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    fmt = ctx["creatives"][-1]["format_id"]
    absent_id = "creative-absent-from-request-001"
    CreativeFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        creative_id=absent_id,
        name="Absent From Request",
        agent_url=fmt["agent_url"],
        format=fmt["id"],
    )
    env._commit_factory_data()
    ctx["absent_creative_id"] = absent_id
    return absent_id


def _scope_request(ctx: dict, *, delete_missing: bool | None, filtered: bool) -> None:
    """One request creative plus an absent library creative, then the scope flags.

    creative/sync-creatives-request.json: ``delete_missing`` archives creatives not in
    this sync ("Invalid when creative_ids is provided"); ``creative_ids`` limits the
    sync to those ids, minItems 1. The filtered scope names the request's own creative
    and a second one that is deliberately NOT in the filter, so "scoped to the subset"
    has something to leave out.
    """
    given_creative_with_format(ctx)
    _seed_absent_library_creative(ctx)
    if filtered:
        env = ctx["env"]
        format_id, agent_url, assets = _format_payload(ctx, env)
        ctx["creatives"].append(
            CreativeAssetRequestFactory.payload(
                creative_id="creative-outside-filter-001",
                name="Outside The Filter",
                format_id={"id": format_id, "agent_url": agent_url},
                assets=assets,
            )
        )
        ctx["creative_ids"] = [ctx["creatives"][0]["creative_id"]]
    if delete_missing is not None:
        ctx["delete_missing"] = delete_missing


_SCOPE_SETUPS: dict[str, dict[str, Any]] = {
    "delete_missing true and no creative_ids filter": {"delete_missing": True, "filtered": False},
    "delete_missing false and no creative_ids filter": {"delete_missing": False, "filtered": False},
    "neither delete_missing nor creative_ids provided": {"delete_missing": None, "filtered": False},
    "a creative_ids filter and no delete_missing flag": {"delete_missing": None, "filtered": True},
    "delete_missing true together with a creative_ids filter": {"delete_missing": True, "filtered": True},
}


@given(parsers.parse("a sync request whose scope is {scope_setup}"))
def given_sync_request_scope(ctx: dict, scope_setup: str) -> None:
    _scope_request(ctx, **_SCOPE_SETUPS[scope_setup])


@given("a sync request with both creative_ids filter and delete_missing set to true")
def given_sync_request_delete_missing_with_filter(ctx: dict) -> None:
    _scope_request(ctx, delete_missing=True, filtered=True)


@then("the request should proceed as a full-library replace")
def then_full_library_replace(ctx: dict) -> None:
    """The creative the request did not mention comes back ``deleted``."""
    entry = _wire_creatives_entry(ctx, ctx["absent_creative_id"])
    assert entry.get("action") == "deleted", (
        f"delete_missing without a filter archives the whole library's absentees; the absent "
        f"creative's entry says {entry.get('action')!r}"
    )


@then("the request should proceed and leave absent creatives unchanged")
def then_absent_creatives_untouched(ctx: dict) -> None:
    """No entry for the creative the request did not mention, and nothing ``deleted``."""
    entries = wire_field(ctx, "creatives")
    ids = [entry.get("creative_id") for entry in entries]
    assert ctx["absent_creative_id"] not in ids, (
        f"without delete_missing the sync must not touch a creative it did not mention, but the "
        f"response carries an entry for it: {ids}"
    )
    assert all(entry.get("action") != "deleted" for entry in entries), (
        f"nothing may be archived without delete_missing, got actions {[e.get('action') for e in entries]}"
    )


@then("the request should proceed scoped to the filtered subset")
def then_scoped_to_filter(ctx: dict) -> None:
    """Only the filtered ids are processed: the unfiltered request creative is absent."""
    ids = sorted(entry.get("creative_id") for entry in wire_field(ctx, "creatives"))
    assert ids == sorted(ctx["creative_ids"]), (
        f"creative_ids limits the sync to {ctx['creative_ids']}, but the response processed {ids}"
    )


@then("the incompatible package should be reported in assignment_errors")
def then_incompatible_package_in_assignment_errors(ctx: dict) -> None:
    entry = _wire_creatives_entry(ctx, latest_creative_id(ctx))
    incompatible = ctx["incompatible_package"].package_id
    assert incompatible in (entry.get("assignment_errors") or {}), (
        f"lenient mode records the format mismatch on the entry's assignment_errors keyed by "
        f"{incompatible!r}, got {entry.get('assignment_errors')!r}"
    )


@then("processing should continue without aborting")
def then_processing_continues(ctx: dict) -> None:
    """Lenient mode: the request succeeds and the creative itself is synced."""
    entry = _wire_creatives_entry(ctx, latest_creative_id(ctx))
    assert entry.get("action") in ("created", "updated"), (
        f"lenient mode must not abort on one failed assignment; the creative's entry says {entry.get('action')!r}"
    )


@given("an assignment that already exists for this creative")
def given_assignment_already_exists(ctx: dict) -> None:
    """Seed a pre-existing CreativeAssignment row so the sync acts as idempotent upsert."""
    from tests.factories import (
        CreativeAssignmentFactory,
        CreativeFactory,
        MediaBuyFactory,
        MediaPackageFactory,
        ProductFactory,
    )

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    fmt_entry = _product_format_entry(ctx, env)

    # Use UUID-based IDs for e2e_rest to avoid collisions with Docker's persistent DB.
    mb_kwargs: dict = {"tenant": tenant, "principal": principal, "status": "active"}
    prod_kwargs: dict = {"tenant": tenant, "format_ids": [fmt_entry]}
    if is_e2e(ctx):
        mb_kwargs["media_buy_id"] = _e2e_unique_id("mb")
        prod_kwargs["product_id"] = _e2e_unique_id("prod")

    media_buy = MediaBuyFactory(**mb_kwargs)
    product = ProductFactory(**prod_kwargs)
    pkg_kwargs: dict = {
        "media_buy": media_buy,
        "package_config": {"product_id": product.product_id, "budget": 1000.0},
    }
    if is_e2e(ctx):
        pkg_kwargs["package_id"] = _e2e_unique_id("pkg")
    package = MediaPackageFactory(**pkg_kwargs)
    # Use same creative_id as the payload so the pre-existing row matches.
    creative_payload = ctx["creatives"][-1]
    creative_id = creative_payload["creative_id"]
    # Pre-seed the Creative row and the assignment (what sync will see as "already exists").
    creative = CreativeFactory(
        tenant=tenant,
        principal=principal,
        creative_id=creative_id,
        name=creative_payload["name"],
        agent_url=fmt_entry["agent_url"],
        format=fmt_entry["id"],
    )
    existing_assignment = CreativeAssignmentFactory(
        creative=creative,
        media_buy=media_buy,
        package_id=package.package_id,
        weight=50,  # non-default so the upsert sets weight=100 proving update ran
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    ctx["existing_assignment_id"] = existing_assignment.assignment_id
    ctx["existing_assignment_weight_before"] = 50
    ctx["assignments"] = {creative_id: [package.package_id]}


@given("an assignment to a package that does not exist")
def given_assignment_to_missing_package(ctx: dict) -> None:
    """Reference a package_id that does NOT exist anywhere in the tenant."""
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    env._commit_factory_data()
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {creative_id: ["pkg-does-not-exist-404"]}
    # Strict mode triggers AdCPNotFoundError with recovery='correctable'.
    ctx.setdefault("validation_mode", "strict")


# --- yqpf: idempotent upsert + cross-tenant package (BR-RULE-038) ---


@given("a creative already assigned to a package")
def given_creative_already_assigned_to_package(ctx: dict) -> None:
    """Seed a creative with an existing assignment for idempotent upsert testing.

    Creates a media buy, package, Creative ORM row, and CreativeAssignment row.
    The creative_id matches the payload in ctx["creatives"] so that sync_creatives
    treats this as an update (existing creative + existing assignment).
    Stores the assignment_id and weight for verification in the Then step.
    """
    from tests.factories import (
        CreativeAssignmentFactory,
        CreativeFactory,
        MediaBuyFactory,
        MediaPackageFactory,
        ProductFactory,
    )

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    agent_url = env.DEFAULT_AGENT_URL

    # Ensure a creative payload exists
    if not ctx.get("creatives"):
        given_creative_with_format(ctx)

    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    product = ProductFactory(tenant=tenant, format_ids=[_product_format_entry(ctx, env)])
    package = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )

    creative_payload = ctx["creatives"][-1]
    creative_id = creative_payload["creative_id"]
    creative = CreativeFactory(
        tenant=tenant,
        principal=principal,
        creative_id=creative_id,
        name=creative_payload["name"],
        agent_url=_scenario_format_entry(ctx, env)["agent_url"],
        format=_scenario_format_entry(ctx, env)["id"],
    )
    existing_assignment = CreativeAssignmentFactory(
        creative=creative,
        media_buy=media_buy,
        package_id=package.package_id,
        weight=50,  # non-default so the upsert sets weight=100 proving update ran
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    ctx["existing_assignment_id"] = existing_assignment.assignment_id
    ctx["existing_assignment_weight_before"] = 50
    ctx["idempotent_package_id"] = package.package_id
    ctx["assignments"] = {creative_id: [package.package_id]}


@given("assignments referencing the same package_id")
def given_assignments_referencing_same_package(ctx: dict) -> None:
    """Wire assignments to the same package_id as the existing assignment.

    The previous step 'a creative already assigned to a package' already
    sets ctx["assignments"]. This step confirms / re-wires the assignments
    dict to reference the same package_id for idempotent upsert.
    """
    package_id = ctx["idempotent_package_id"]
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {creative_id: [package_id]}


@given("a package exists in a different tenant")
def given_package_in_different_tenant(ctx: dict) -> None:
    """Create a package in a different tenant (cross-tenant isolation test).

    Seeds a second tenant with its own media buy and package. The
    package_id is stored so the next step can reference it in assignments,
    but since it belongs to a different tenant, the sync should fail with
    PACKAGE_NOT_FOUND.
    """
    from tests.factories import MediaBuyFactory, MediaPackageFactory, PrincipalFactory, ProductFactory, TenantFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)

    # Ensure a creative payload exists
    if not ctx.get("creatives"):
        given_creative_with_format(ctx)

    other_tenant = TenantFactory(tenant_id="other_tenant_xtz", subdomain="other_xtz")
    other_principal = PrincipalFactory(tenant=other_tenant, principal_id="other_principal_xtz")
    other_buy = MediaBuyFactory(tenant=other_tenant, principal=other_principal, status="active")
    other_product = ProductFactory(tenant=other_tenant, format_ids=[_product_format_entry(ctx, env)])
    other_package = MediaPackageFactory(
        media_buy=other_buy,
        package_config={"product_id": other_product.product_id, "budget": 1000.0},
    )
    env._commit_factory_data()
    ctx["cross_tenant_package_id"] = other_package.package_id


@given("assignments referencing that package_id")
def given_assignments_referencing_that_package(ctx: dict) -> None:
    """Wire assignments to the cross-tenant package_id.

    Uses the package_id stored by 'a package exists in a different tenant'.
    """
    package_id = ctx["cross_tenant_package_id"]
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {creative_id: [package_id]}


# --- ceox: assignments-structure boundary (single entry + invalid structures) ---


@given("no assignments field")
def given_no_assignments_field(ctx: dict) -> None:
    """Explicitly omit the assignments field from the request (absent path)."""
    ctx.pop("assignments", None)


@given("an empty assignments array")
def given_empty_assignments_array(ctx: dict) -> None:
    """Set assignments to an empty value.

    The spec prescribes error ``ASSIGNMENTS_EMPTY`` for this case. Production
    currently treats empty as "no assignments" (no error). The Then step below
    xfails with SPEC-PRODUCTION GAP reason when production does not raise.
    """
    ctx["assignments"] = {}


@given(parsers.parse('an assignment with creative_id "{creative_id}" and package_id "{package_id}"'))
def given_assignment_with_ids(ctx: dict, creative_id: str, package_id: str) -> None:
    """Set up a real package with ``package_id`` and assign creative_id to it.

    Because the creative payload already has its own generated ``creative_id``,
    we reuse that id (the scenario label ``"c1"`` is a placeholder for "the
    creative"). The package is created with the literal ``package_id`` label
    from the scenario so lookup matches exactly.
    """
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]

    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    product = ProductFactory(tenant=tenant, format_ids=[_product_format_entry(ctx, env)])
    package = MediaPackageFactory(
        media_buy=media_buy,
        package_id=package_id,
        package_config={"package_id": package_id, "product_id": product.product_id, "budget": 1000.0},
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    # Use the real creative_id from the payload (the "c1" label is symbolic).
    real_creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {real_creative_id: [package.package_id]}


@given("an assignment entry with only package_id")
def given_assignment_entry_missing_creative_id(ctx: dict) -> None:
    """An assignments[] entry that omits creative_id.

    sync-creatives-request.json requires creative_id and package_id on every entry, so
    the request is refused as a schema violation. Sent as a raw entry: the ctx map is
    keyed by creative_id and cannot leave it out.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    from tests.factories import MediaBuyFactory, MediaPackageFactory

    tenant = ctx["tenant"]
    principal = ctx["principal"]
    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    package = MediaPackageFactory(media_buy=media_buy, package_id=_e2e_unique_id("pkg"))
    env._commit_factory_data()
    ctx["assignment_entries"] = [{"package_id": package.package_id}]


@given("an assignment entry with only creative_id")
def given_assignment_entry_missing_package_id(ctx: dict) -> None:
    """An assignments[] entry that omits package_id (see the sibling above)."""
    ctx["assignment_entries"] = [{"creative_id": latest_creative_id(ctx)}]


@given("an assignment with weight 0")
def given_assignment_with_weight_zero(ctx: dict) -> None:
    """assignments[].weight 0: "assigned but paused (receives no delivery)" (the pin)."""
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    product = ProductFactory(tenant=tenant, format_ids=[_product_format_entry(ctx, env)])
    package = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {creative_id: [package.package_id]}
    _ask_assignment_terms(ctx, creative_id, package.package_id, weight=0)


@given('an assignment with placement_ids ["slot_a"]')
def given_assignment_with_placement_ids(ctx: dict) -> None:
    """assignments[].placement_ids: "Restrict this creative to specific placements" (the pin)."""
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    product = ProductFactory(tenant=tenant, format_ids=[_product_format_entry(ctx, env)])
    package = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {creative_id: [package.package_id]}
    _ask_assignment_terms(ctx, creative_id, package.package_id, placement_ids=["slot_a"])


# --- 5o9e: assignment-basic Given steps (package_id+weight, multi-package, duplicate, missing fields) ---


def _setup_assignment_package(
    ctx: dict,
    *,
    package_id: str | None = None,
) -> tuple[object, object]:
    """Create media_buy + product + package for assignment Given steps.

    Returns (media_buy, package). Stores them in ctx["media_buy"] and
    ctx["package"] as well.  Avoids the repeated 10-line setup block in
    every assignment Given step (DRY invariant).
    """
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]

    # Use UUID-based IDs for e2e_rest to avoid collisions with Docker's persistent DB.
    mb_kwargs: dict = {"tenant": tenant, "principal": principal, "status": "active"}
    prod_kwargs: dict = {"tenant": tenant, "format_ids": [_product_format_entry(ctx, env)]}
    if is_e2e(ctx):
        mb_kwargs["media_buy_id"] = _e2e_unique_id("mb")
        prod_kwargs["product_id"] = _e2e_unique_id("prod")

    media_buy = MediaBuyFactory(**mb_kwargs)
    product = ProductFactory(**prod_kwargs)
    pkg_kwargs: dict = {
        "media_buy": media_buy,
        "package_config": {"product_id": product.product_id, "budget": 1000.0},
    }
    if package_id is not None:
        pkg_kwargs["package_id"] = package_id
    elif is_e2e(ctx):
        pkg_kwargs["package_id"] = _e2e_unique_id("pkg")
    package = MediaPackageFactory(**pkg_kwargs)
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    return media_buy, package


@given(parsers.re(r'an assignment with package_id "(?P<package_id>[^"]+)" and weight (?P<weight>.*)$'))
def given_assignment_with_package_and_weight(ctx: dict, package_id: str, weight: str) -> None:
    """Set up an assignment with a specific package_id and optional weight.

    Handles both ``weight 50`` (explicit int) and ``weight `` (empty = the field omitted,
    which the pin defines as equal rotation).
    """
    _media_buy, package = _setup_assignment_package(ctx, package_id=package_id)
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {creative_id: [package.package_id]}
    weight_stripped = weight.strip()
    if weight_stripped:
        _ask_assignment_terms(ctx, creative_id, package.package_id, weight=int(weight_stripped))


@given("assignments mapping the creative to valid package_ids")
def given_assignments_mapping_creative_to_valid_packages(ctx: dict) -> None:
    """Assign the creative to two valid packages in the same media buy."""
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]

    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    product = ProductFactory(
        tenant=tenant,
        format_ids=[_product_format_entry(ctx, env)],
    )
    pkg1 = MediaPackageFactory(
        media_buy=media_buy,
        package_id="pkg-valid-1",
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )
    pkg2 = MediaPackageFactory(
        media_buy=media_buy,
        package_id="pkg-valid-2",
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {creative_id: [pkg1.package_id, pkg2.package_id]}


@given(parsers.parse('assignments mapping creative "{creative_id}" to packages "{pkg1}" and "{pkg2}"'))
def given_assignments_mapping_creative_to_two_packages(ctx: dict, creative_id: str, pkg1: str, pkg2: str) -> None:
    """Assign creative to two named packages (scenario outline parameterized).

    The ``creative_id`` label (e.g. "c1") is symbolic — we use the actual
    creative_id from the payload built by the preceding Given step.
    """
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]

    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    product = ProductFactory(
        tenant=tenant,
        format_ids=[_product_format_entry(ctx, env)],
    )
    package1 = MediaPackageFactory(
        media_buy=media_buy,
        package_id=pkg1,
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )
    package2 = MediaPackageFactory(
        media_buy=media_buy,
        package_id=pkg2,
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    real_creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {real_creative_id: [package1.package_id, package2.package_id]}


@given("two assignment entries with same creative_id and package_id")
def given_two_assignment_entries_same_ids(ctx: dict) -> None:
    """Submit duplicate (creative_id, package_id) pair — spec expects idempotent upsert."""
    _media_buy, package = _setup_assignment_package(ctx)
    creative_id = latest_creative_id(ctx)
    # The assignments dict shape (creative_id → [pkg_ids]) naturally deduplicates,
    # so we store a flag for the When step to send the duplicate explicitly.
    ctx["assignments"] = {creative_id: [package.package_id, package.package_id]}


@given(
    parsers.parse('an assignment with creative_id "{creative_id}", package_id "{package_id}", and weight {weight:d}')
)
def given_assignment_with_ids_and_weight(ctx: dict, creative_id: str, package_id: str, weight: int) -> None:
    """Set up an assignment with explicit creative_id, package_id, and weight.

    The ``creative_id`` label is symbolic (scenario outline placeholder).
    """
    _media_buy, package = _setup_assignment_package(ctx, package_id=package_id)
    real_creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {real_creative_id: [package.package_id]}
    _ask_assignment_terms(ctx, real_creative_id, package.package_id, weight=weight)


@given(
    parsers.parse(
        'an assignment with creative_id "{creative_id}", package_id "{package_id}", and placement_ids {placement_ids}'
    )
)
def given_assignment_with_ids_and_placement(ctx: dict, creative_id: str, package_id: str, placement_ids: str) -> None:
    """Set up an assignment with explicit creative_id, package_id, and placement_ids."""
    _media_buy, package = _setup_assignment_package(ctx, package_id=package_id)
    real_creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {real_creative_id: [package.package_id]}
    _ask_assignment_terms(ctx, real_creative_id, package.package_id, placement_ids=json.loads(placement_ids))


@given("an assignment entry missing creative_id")
def given_assignment_entry_missing_creative_id_alias(ctx: dict) -> None:
    """Alias for 'an assignment entry with only package_id' (scenario outline text variant)."""
    given_assignment_entry_missing_creative_id(ctx)


@given("an assignment entry missing package_id")
def given_assignment_entry_missing_package_id_alias(ctx: dict) -> None:
    """Alias for 'an assignment entry with only creative_id' (scenario outline text variant)."""
    given_assignment_entry_missing_package_id(ctx)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — assignment outcomes (mwtk + 0xwq + ceox)
# ═══════════════════════════════════════════════════════════════════════


def _get_creative_assigned_to(ctx: dict) -> list[str]:
    """Return the assigned_to list from the response's first creative result."""
    resp = require_payload(ctx)
    # SyncCreativesResponse.creatives is list[SyncCreativeResult] (adcp 3.9 schema)
    results = resp.creatives
    assert results, f"Response has no creatives: {resp}"
    return list(results[0].assigned_to or [])


def _get_assignment_from_db(ctx: dict) -> object:
    """Return the CreativeAssignment row created by the sync.

    Performs the lookup and existence assertions shared by assignment
    outcome steps: success (no error), the package present in assigned_to,
    then a tenant/creative/package-scoped DB lookup that is asserted to exist.
    Callers add their own divergent attribute checks.

    The row is the only observable for the assignment's own attributes:
    sync-creatives-response.json puts the package ids in ``assigned_to`` and the
    per-package failures in ``assignment_errors``, and carries neither ``weight``
    nor ``placement_ids`` on the wire at all.
    """
    from sqlalchemy import select

    from src.core.database.models import CreativeAssignment

    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    assigned = _get_creative_assigned_to(ctx)
    expected_pkg = ctx["package"].package_id
    assert expected_pkg in assigned, f"Expected {expected_pkg!r} in assigned_to, got {assigned}"

    tenant_id = ctx["tenant"].tenant_id
    creative_id = latest_creative_id(ctx)
    with _readback(ctx) as session:
        assignment = session.scalars(
            select(CreativeAssignment).filter_by(
                tenant_id=tenant_id,
                creative_id=creative_id,
                package_id=expected_pkg,
            )
        ).first()
        assert assignment is not None, f"No CreativeAssignment found for creative={creative_id}, package={expected_pkg}"
        return assignment


@then("the assignment should be created successfully")
def then_assignment_created_successfully(ctx: dict) -> None:
    """Assert the sync response reports the package was assigned to the creative."""
    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    assigned = _get_creative_assigned_to(ctx)
    expected = ctx["package"].package_id
    assert expected in assigned, f"Expected {expected!r} in assigned_to, got {assigned}"


@then("both assignments should be created")
def then_both_assignments_created(ctx: dict) -> None:
    """Assert both packages from a multi-assignment Given step appear in assigned_to."""
    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    assigned = _get_creative_assigned_to(ctx)
    # ctx["assignments"] is {creative_id: [pkg1, pkg2]}
    all_expected_pkgs = []
    for pkg_ids in ctx["assignments"].values():
        all_expected_pkgs.extend(pkg_ids)
    assert len(all_expected_pkgs) >= 2, f"Expected at least 2 packages in assignments, got {all_expected_pkgs}"
    for pkg_id in all_expected_pkgs:
        assert pkg_id in assigned, f"Expected package {pkg_id!r} in assigned_to, got {assigned}"


@then(parsers.parse("the assignment should be created with weight {weight:d}"))
def then_assignment_created_with_weight(ctx: dict, weight: int) -> None:
    """The persisted assignment carries the weight the entry asked for (assignments[].weight)."""
    assignment = _get_assignment_from_db(ctx)
    assert assignment.weight == weight, f"Expected assignment weight {weight}, got {assignment.weight}"


@then("the existing assignment should be updated")
def then_existing_assignment_updated(ctx: dict) -> None:
    """Assert the pre-existing CreativeAssignment row was updated (weight set to 100)."""
    from sqlalchemy import select

    from src.core.database.models import CreativeAssignment

    assert "error" not in ctx, f"Expected success (idempotent upsert) but got error: {ctx.get('error')}"
    assignment_id = ctx["existing_assignment_id"]
    tenant_id = ctx["tenant"].tenant_id
    with db_session(ctx) as session:
        updated = session.scalars(
            select(CreativeAssignment).filter_by(tenant_id=tenant_id, assignment_id=assignment_id)
        ).first()
        assert updated is not None, f"Existing assignment {assignment_id} disappeared after sync"
        assert updated.weight == 100, (
            f"Idempotent upsert should set weight=100, but weight is {updated.weight} "
            f"(was {ctx['existing_assignment_weight_before']} before sync)"
        )


@then("the existing assignment should be updated (not duplicated)")
def then_existing_assignment_updated_not_duplicated(ctx: dict) -> None:
    """Assert idempotent upsert: the assignment row was updated, not a second row created.

    Verifies:
    1. The original assignment_id still exists with updated weight.
    2. No duplicate rows for the same (creative_id, package_id) in the tenant.
    """
    from sqlalchemy import select

    from src.core.database.models import CreativeAssignment

    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: idempotent upsert should succeed, but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)

    assignment_id = ctx["existing_assignment_id"]
    tenant_id = ctx["tenant"].tenant_id
    creative_id = latest_creative_id(ctx)
    package_id = ctx["idempotent_package_id"]
    with db_session(ctx) as session:
        # Check no duplicate rows
        all_rows = session.scalars(
            select(CreativeAssignment).filter_by(
                tenant_id=tenant_id,
                creative_id=creative_id,
                package_id=package_id,
            )
        ).all()
        assert len(all_rows) == 1, (
            f"Idempotent upsert should produce exactly 1 row, got {len(all_rows)} "
            f"for creative={creative_id}, package={package_id}"
        )
        updated = all_rows[0]
        assert updated.assignment_id == assignment_id, (
            f"Expected same assignment_id {assignment_id}, got {updated.assignment_id} "
            f"(a new row was created instead of updating)"
        )
        assert updated.weight == 100, (
            f"Idempotent upsert should set weight=100, but weight is {updated.weight} "
            f"(was {ctx['existing_assignment_weight_before']} before sync)"
        )


@then("the cross-tenant package should not be accessible")
def then_cross_tenant_not_accessible(ctx: dict) -> None:
    """Assert the sync rejected the cross-tenant package reference.

    The cross-tenant package_id should not be accessible from the buyer's
    tenant. The previous Then step already asserts the error code. This
    step confirms no assignment was created for the cross-tenant package.

    Cross-tenant isolation is a security invariant — a leaked assignment
    is a hard test failure, never a tolerated spec-gap.
    """
    from sqlalchemy import select

    from src.core.database.models import CreativeAssignment

    cross_pkg = ctx["cross_tenant_package_id"]
    tenant_id = ctx["tenant"].tenant_id
    with db_session(ctx) as session:
        assignment = session.scalars(
            select(CreativeAssignment).filter_by(
                tenant_id=tenant_id,
                package_id=cross_pkg,
            )
        ).first()
        assert assignment is None, (
            f"Cross-tenant isolation violation: package {cross_pkg} from another tenant "
            f"should not be accessible, but an assignment was created: "
            f"assignment_id={assignment.assignment_id if assignment else 'N/A'}"
        )


@then("no assignment processing should occur")
def then_no_assignment_processing(ctx: dict) -> None:
    """Assert response succeeded and no assignment side-effects occurred.

    When ``assignments`` is absent, production returns success and
    SyncCreativeResult.assigned_to is None/empty.
    """
    assert "error" not in ctx, f"Expected success (no assignments) but got error: {ctx.get('error')}"
    resp = require_payload(ctx)
    # There may be 0 results if the creative also failed validation for other reasons,
    # but the defining property is: no assigned_to populated.
    for r in resp.creatives:
        assigned = r.assigned_to or []
        assert not assigned, (
            f"Expected no assignments processed (the request omitted the assignments field), "
            f"but SyncCreativeResult({r.creative_id}).assigned_to={assigned}"
        )


@then("the assignment should be created as paused")
def then_assignment_created_as_paused(ctx: dict) -> None:
    """assignments[].weight 0 is "assigned but paused (receives no delivery)": persisted as 0."""
    assignment = _get_assignment_from_db(ctx)
    assert assignment.weight == 0, (
        f"weight=0 means assigned but PAUSED (pinned 3.1 assignments[].weight); got {assignment.weight}"
    )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / THEN steps — authentication boundary & partition (dke8)
# ═══════════════════════════════════════════════════════════════════════


@given("a creative with a format_id that does not exist in any agent registry")
def given_creative_with_unknown_format(ctx: dict) -> None:
    """Set up a creative whose format is not registered with any agent.

    Configures the registry mock's ``get_format`` coroutine to return None
    (agent is reachable but format does not exist), so
    ``_validate_creative_input`` raises a ValueError whose message points
    the buyer at ``list_creative_formats`` (spec POST-F2/F3 → error_code
    CREATIVE_FORMAT_UNKNOWN).
    """
    from unittest.mock import AsyncMock

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)

    format_id = "nonexistent_format_999"
    creative_id = "creative-unknown-fmt-001"
    # The transport's REAL agent, asked for an id it does not serve: on e2e_rest the
    # Docker agent answers its catalog and the id is simply absent. A made-up agent host
    # fails the egress gate's DNS lookup first, which is a VALIDATION_ERROR on agent_url
    # -- a different defect from an unknown format.
    _default_id, agent_url, _assets = _format_payload(ctx, env)
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id=creative_id,
        name="Unknown Format Creative",
        # Well-formed but unknown: the pin ACCEPTS this id (it satisfies
        # FormatId.id's pattern), so the wrongness is a registry miss downstream
        # and there is nothing here to declare malformed.
        format_id={"id": format_id, "agent_url": agent_url},
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = format_id

    registry = env.mock["registry"].return_value
    registry.get_format = AsyncMock(return_value=None)


@given("a creative with a format_id whose agent_url is unreachable")
def given_creative_with_unreachable_agent(ctx: dict) -> None:
    """Set up a creative whose format agent returns a connection error.

    Configures the registry mock's ``get_format`` coroutine to raise a
    ConnectionError so ``_validate_creative_input`` wraps it into a
    'Cannot validate format ... is unreachable' ValueError, producing
    a failed SyncCreativeResult (POST-F2/F3).
    """
    from unittest.mock import AsyncMock

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)

    format_id, _, _ = _format_payload(ctx, env)
    creative_id = "creative-unreachable-001"
    # In-process the mock below is what does not answer; on e2e_rest the wire itself
    # must, so the creative names a resolvable agent at a closed port.
    agent_url = _E2E_UNREACHABLE_AGENT_URL if is_e2e(ctx) else env.DEFAULT_AGENT_URL
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id=creative_id,
        name="Unreachable Agent Creative",
        format_id={"id": format_id, "agent_url": agent_url},
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = format_id

    # What the registry actually raises when the agent does not answer: the egress
    # seam types every undelivered request as OutboundDeliveryFailed, which IS an
    # AdCPServiceUnavailableError, and _processing.py re-raises it as the request's
    # answer (SERVICE_UNAVAILABLE, transient). A raw ConnectionError modelled a leak
    # production's registry never has, and landed in the generic per-item branch.
    from src.core.security.egress.attempts import OutboundDeliveryFailed

    registry = env.mock["registry"].return_value
    registry.get_format = AsyncMock(side_effect=OutboundDeliveryFailed(attempts=1, http_status=None))


# "the request has an empty principal_id" is a sentence of the generic
# ``given_buyer_no_auth`` (tests/bdd/steps/generic/given_auth.py): on the wire a principal
# is present or absent, never empty, and absent is the state that step establishes.


def _assert_auth_rejection(ctx: dict, expected_code: str) -> None:
    """Assert the sync was rejected with the spec-named auth error code.

    Wire-first, reconstructed fallback (tests/CLAUDE.md Error Verification
    Policy) — consolidated onto the same strategy as the canonical
    ``then_error.py:340`` step. Production splits authentication failures
    into AUTH_MISSING (absent credential, correctable) / AUTH_INVALID
    (presented-but-rejected credential, terminal) per v3.1.1
    error-code.json (#2092).
    """
    from tests.bdd.steps.generic.then_error import _wire_code

    error = ctx.get("error")
    actual_code = _wire_code(ctx)
    if actual_code is None:
        assert error is not None, f"Expected {expected_code} error but got response: {payload_or_none(ctx)}"
        actual_code, _ = _extract_error_code_and_suggestion(ctx, error)
    detail = f" ({type(error).__name__}: {error})" if error is not None else ""
    assert actual_code == expected_code, f"Expected error code '{expected_code}', got '{actual_code}'{detail}"


@then("the creative should be processed successfully")
def then_creative_processed_successfully(ctx: dict) -> None:
    """Assert the sync returned a response and the creative was created."""
    from src.core.schemas import SyncCreativesResponse

    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    resp = payload_or_none(ctx)
    assert isinstance(resp, SyncCreativesResponse), (
        f"Expected SyncCreativesResponse, got {type(resp).__name__ if resp else None}"
    )
    results = getattr(resp, "results", None) or getattr(resp, "creatives", None) or []
    actions_str = [str(getattr(getattr(r, "action", None), "value", getattr(r, "action", None))) for r in results]
    assert any(a in ("created", "updated", "unchanged") for a in actions_str), (
        f"Expected at least one action in (created, updated, unchanged), got {actions_str}"
    )


@then(parsers.parse("the request should be rejected with {expected_code}"))
def then_rejected_with_auth_code(ctx: dict, expected_code: str) -> None:
    """Assert the sync was rejected with the given auth error code.

    Parametrized (was a fixed-text ``AUTH_REQUIRED`` step) to cover the
    v3.1.1 AUTH_MISSING/AUTH_INVALID split (#2092).
    """
    _assert_auth_rejection(ctx, expected_code)


@then("the assignment should include placement targeting")
def then_assignment_includes_placement(ctx: dict) -> None:
    """assignments[].placement_ids restricts the creative to those placements: persisted as sent."""
    assignment = _get_assignment_from_db(ctx)
    creative_id = latest_creative_id(ctx)
    expected_pkg = ctx["package"].package_id
    placement_ids = assignment.placement_ids
    assert placement_ids, (
        "assignments[].placement_ids restricts a creative to specific placements within the "
        f"package; the stored assignment carries {placement_ids!r} "
        f"(creative={creative_id}, package={expected_pkg})"
    )


@then('the creative should have action "created"')
def then_creative_action_created(ctx: dict) -> None:
    """Assert the per-creative SyncCreativeResult has action == "created"."""
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected action='created', but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, f"Expected at least one SyncCreativeResult in response, got: {resp}"
    first = results[0]
    action_val = getattr(first, "action", None)
    action_str = str(getattr(action_val, "value", action_val))
    assert action_str == "created", (
        f"Expected creative action 'created', got '{action_str}' (errors={getattr(first, 'errors', None)})"
    )


@then('the creative should have action "failed"')
def then_creative_action_failed(ctx: dict) -> None:
    """Assert the per-creative SyncCreativeResult has action == "failed".

    Production reports per-creative failures as a successful response containing
    a SyncCreativeResult with action="failed" and a string in errors[].
    Promotes the first error string to ctx["error"] as a synthetic object
    so downstream generic Then steps (error code, message, suggestion) can run.
    """
    resp = payload_or_none(ctx)
    err = ctx.get("error")
    assert resp is not None, (
        f"SPEC-PRODUCTION GAP: scenario expects action='failed' on a SyncCreativeResult but the dispatch raised {type(err).__name__}: {err}"
    )

    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, f"Expected at least one SyncCreativeResult in response, got: {resp}"
    first = results[0]
    action_val = getattr(first, "action", None)
    action_str = str(getattr(action_val, "value", action_val))
    assert action_str == "failed", (
        f"Expected creative action 'failed', got '{action_str}' (errors={getattr(first, 'errors', None)})"
    )

    # Only the promotion, which the docstring above describes and which
    # ctx["error"]'s 226 readers consume. The two writes that stood here —
    # ctx["failed_creative_result"] and ctx["failed_creative_errors"] — had ZERO
    # readers anywhere in tests/, by both a reader scan and a literal grep: each
    # name occurred exactly once in the tree, at its own assignment. A Then that
    # writes state nothing reads makes later steps depend on assertion order for
    # nothing in return.
    errs = getattr(first, "errors", None) or []
    _promote_creative_errors_to_ctx(ctx, errs)


def _promote_creative_errors_to_ctx(ctx: dict, errs: list) -> None:
    """Promote SyncCreativeResult.errors[] to ctx["error"] for downstream Then steps.

    Production emits per-creative failures as TYPED errors — src/core/tools/creatives/
    _processing.py builds each entry with build_error_object(), so every element is an
    adcp Error carrying code/recovery/suggestion/field/details. This helper therefore
    just forwards the first one.

    It used to SYNTHESIZE an error object, inferring the code from the message text with
    a keyword ladder. That existed because production once emitted unstructured strings
    here; it no longer does, and inferring a code from prose is the exact defect this
    epic removes — it laundered a guess into something downstream steps read as if it
    were the real typed error.
    """
    if not errs:
        return
    ctx["error"] = errs[0]


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — assignment-error operation failure (bxhz + ryv4)
# ═══════════════════════════════════════════════════════════════════════


@then("the operation should fail with an assignment error")
def then_operation_fails_with_assignment_error(ctx: dict) -> None:
    """Assert the operation failed and the failure originated in assignment processing.

    Production raises AdCPValidationError (FORMAT_MISMATCH branch) or
    AdCPNotFoundError (package-not-found branch) from _assignments.py.
    Both are AdCPSalesAgentError subclasses surfaced as ctx["error"] by dispatch.

    SPEC-PRODUCTION GAP handling:
      * MCP transport may reject the format string at the FastMCP TypeAdapter
        boundary because adcp.types.FormatId.id pattern is ^[a-zA-Z0-9_-]+$
        (does not allow ``/``). When that happens the error message contains
        "format_id.id" and "string_pattern_mismatch" — same gap as the
        existing ``then_uc006_result_should_be`` step documents.
      * When the spec format id contains ``/`` and slips past TypeAdapter
        (REST path), creative validation fails, no creative row is persisted,
        and assignment processing then raises an SQLAlchemy ForeignKeyViolation
        (creative_assignments FK on creatives). Same root-cause gap as
        the MCP case — surface as the same SPEC-PRODUCTION GAP xfail.
      * AdCPNotFoundError.error_code == "REFERENCE_NOT_FOUND" but the spec demands
        "PACKAGE_NOT_FOUND" — the next Gherkin step asserts the spec code
        and would fail strict equality. We pre-empt by mapping the error
        for downstream Then steps via details["error_code"].
    """

    from src.core.exceptions import AdCPSalesAgentError

    error = ctx.get("error")
    if error is None:
        # Promote response.errors if available (partial-success pattern), then re-check
        resp = payload_or_none(ctx)
        if resp is not None and getattr(resp, "errors", None):
            error = resp.errors[0]
            ctx["error"] = error

    assert error is not None, (
        f"SPEC-PRODUCTION GAP: expected an assignment error but production succeeded. Response: {payload_or_none(ctx)!r}"
    )

    # MCP/TypeAdapter pre-impl rejection of FormatId pattern — surface as gap
    err_str = str(error)

    # SQLAlchemy FK violation cascade from format-id-with-slash gap (REST/impl path)

    # E2E server crash from format-id-with-slash gap — same root cause as above
    # but manifested as HTTP 500 with empty body on the real Docker stack.
    # A 500 here is a real defect worth failing on. The scenario's format id
    # "agent1/<name>" is SCHEMA-INVALID: the pinned 3.1 core/format-id.json defines id as a
    # "slug matching [a-zA-Z0-9_-]+", so the slash is forbidden BY THE SPEC, not merely by
    # production's pattern. A spec-invalid input must be refused with a validation error,
    # never answered with an empty-bodied 500 — excusing that hid a crash behind a
    # scenario bug.
    assert not (
        isinstance(error, AdCPSalesAgentError) and error.error_code == "INTERNAL_ERROR" and "HTTP 500" in err_str
    ), (
        "server returned INTERNAL_ERROR/HTTP 500 for a format_id whose id contains '/'. "
        "The pin forbids the slash, so this input must be REFUSED with a validation error, "
        "not crash the server"
    )

    # Catch-all for fictional format IDs with slashes (e.g. "agent1/banner-300x250").
    # Production's FormatId.id pattern is ^[a-zA-Z0-9_-]+$ — slashes are invalid.
    # Different transports reject at different layers with different error types.
    creative_fmt = str(ctx.get("creative_format_id", ""))

    # The two SPEC-PRODUCTION GAP xfails that used to live here are DELETED, because
    # both the gap and the mechanism that detected it are gone:
    #
    #   THE GAP CLOSED. They said production emitted generic codes -- "production:
    #   'NOT_FOUND'" and "production: 'VALIDATION_ERROR'" -- where the spec demanded
    #   specific ones. Production now raises AdCPPackageNotFoundError
    #   (PACKAGE_NOT_FOUND, _assignments.py:163) and AdCPCreativeRejectedError
    #   (CREATIVE_REJECTED, :236). Both are published members and both are what the
    #   scenarios ask for.
    #
    #   THE DETECTOR WAS DEAD ANYWAY. They routed on PROSE --
    #   "package not found" in error.message.lower() -- and message is now derived
    #   from the code via CODE_TABLE, so neither substring
    #   can appear and neither branch could ever fire. An xfail route that silently
    #   stopped working is worse than none: it reads as tracked work while grading
    #   nothing.
    #
    # What remains is the real assertion, on the wire the buyer received rather than
    # on the class of a rebuilt exception.
    result = ctx.get("result")
    wire_code = result.wire_error_code() if result is not None else None
    assert wire_code is not None, (
        f"Expected an assignment rejection on the WIRE, but no wire error envelope was captured. "
        f"dispatch_error={type(error).__name__}: {error}"
    )
    assert wire_code in _ASSIGNMENT_REJECTION_CODES, (
        f"Expected an assignment-processing rejection, got wire code {wire_code!r}. "
        f"Expected one of {sorted(_ASSIGNMENT_REJECTION_CODES)}"
    )


#: The codes strict-mode assignment processing can reject with, all published.
#: PACKAGE_NOT_FOUND when the named package does not resolve, CREATIVE_NOT_FOUND when
#: the creative does not, VALIDATION_ERROR when the creative's format is one the
#: package's product does not declare -- the pinned enum's "violates business rules
#: beyond schema validation", and NOT CREATIVE_REJECTED, which the enum reserves for
#: "failed content policy review" (the update path emits the same, see #1417).
_ASSIGNMENT_REJECTION_CODES = frozenset({"PACKAGE_NOT_FOUND", "CREATIVE_NOT_FOUND", "VALIDATION_ERROR"})


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / THEN steps — validation mode behavior (lzhr)
# ═══════════════════════════════════════════════════════════════════════


@given("assignments to a non-existent package")
def given_assignments_to_nonexistent_package(ctx: dict) -> None:
    """Reference a package_id that does NOT exist — validation_mode controls behavior.

    Unlike ``given_assignment_to_missing_package`` (line 747), this step does NOT
    default ``validation_mode``, allowing the scenario's separate
    ``validation_mode is "<mode>"`` Given step to control it.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    env._commit_factory_data()
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {creative_id: ["pkg-nonexistent-lzhr-404"]}


@then("the assignment processing should abort with an error")
def then_assignment_processing_should_abort(ctx: dict) -> None:
    """Assert assignment processing aborted due to non-existent package (strict mode).

    The scenario sets up a non-existent package assignment under strict
    validation_mode. Production must raise AdCPNotFoundError whose message
    references the missing package — not just any AdCPSalesAgentError subclass.

    Error code is wire-first (real wire envelope preferred over the lossy
    reconstructed exception), same strategy as then_error_code.
    """

    error = ctx.get("error")
    assert error is not None, (
        "Strict mode with non-existent package should abort with an error, "
        f"but production succeeded. Response: {payload_or_none(ctx)}"
    )
    # Graded on the WIRE: the code says WHICH failure, details say which package.
    # The old form accepted three different things -- an AdCPNotFoundError instance,
    # "not_found" appearing in the code string, or "not found" appearing anywhere in
    # str(error) -- so a stringified incidental failure could satisfy it. And the
    # "references the bad package" check searched str(error), which is now
    # the table sentence and can never contain an id.
    result = ctx["result"]
    result.assert_wire_error("PACKAGE_NOT_FOUND")
    bad_package = ctx.get("nonexistent_package_id", "")
    if bad_package:
        details = result.wire_error_details("PACKAGE_NOT_FOUND")
        assert bad_package in str(details.values()), (
            f"Expected the rejection to name the missing package {bad_package!r} in its details, got {dict(details)!r}"
        )


@then("the behavior should match strict mode")
def then_behavior_matches_strict_mode(ctx: dict) -> None:
    """Assert the behavior is identical to strict mode (default validation_mode).

    When validation_mode is not specified, the default must be strict. This
    means the same abort-on-error behavior as explicit strict mode. We verify
    an error was raised (same assertion as the abort step).
    """

    error = ctx.get("error")
    assert error is not None, (
        "Default validation_mode should be 'strict' (abort on error), "
        f"but production succeeded without raising. Response: {payload_or_none(ctx)}"
    )
    # Graded on the wire, and on the codes strict-mode abort actually emits.
    result = ctx["result"]
    actual_code = result.wire_error_code()
    assert actual_code is not None, f"Strict-mode abort should reach the wire, but no envelope was captured: {error}"
    assert actual_code in _ASSIGNMENT_REJECTION_CODES, (
        f"Strict mode abort should produce one of {sorted(_ASSIGNMENT_REJECTION_CODES)}, got {actual_code!r}"
    )


@then("no assignments should be created")
def then_no_assignments_created(ctx: dict) -> None:
    """Assert no assignments were created (strict abort rolled back all work).

    Queries the DB for CreativeAssignment rows scoped to the tenant and
    creative under test. The postcondition (zero rows) must hold regardless
    of whether the abort surfaced as a raised error or an error-bearing response.
    """
    from sqlalchemy import select

    from src.core.database.models import CreativeAssignment

    tenant_id = ctx["tenant"].tenant_id
    creative_id = latest_creative_id(ctx)
    with db_session(ctx) as session:
        assignments = session.scalars(
            select(CreativeAssignment).filter_by(
                tenant_id=tenant_id,
                creative_id=creative_id,
            )
        ).all()
        assert not assignments, (
            f"Strict mode should create no assignments on error, but found "
            f"{len(assignments)} assignment(s) for creative={creative_id}: "
            f"{[a.package_id for a in assignments]}"
        )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / THEN steps — main-flow create / update (088e + 1bb6)
# ═══════════════════════════════════════════════════════════════════════


@given("the creative does not exist in the Seller's library")
def given_creative_does_not_exist(ctx: dict) -> None:
    """Guard step: verify creative payload exists but no DB row was pre-seeded."""
    assert ctx.get("creatives"), "Precondition: ctx['creatives'] must be populated by a prior Given step"


@given("the creative already exists in the Seller's library for this principal")
def given_creative_already_exists(ctx: dict) -> None:
    """Pre-seed the creative in the DB so sync produces action="updated"."""
    from tests.factories import CreativeFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    creative_payload = ctx["creatives"][-1]
    creative_id = creative_payload["creative_id"]
    fmt_id = creative_payload["format_id"]
    CreativeFactory(
        tenant=tenant,
        principal=principal,
        creative_id=creative_id,
        name=creative_payload["name"],
        agent_url=fmt_id["agent_url"],
        format=fmt_id["id"],
    )
    env._commit_factory_data()


@given("the creative already exists with identical data")
def given_creative_already_exists_identical(ctx: dict) -> None:
    """The creative is already in the library EXACTLY as a sync of this payload leaves it.

    "Identical data" is only identical if the row was written by the same path the
    resync goes through: a factory row carrying the raw request dict differs from what
    production stores (url/click_url/width/height/duration are derived from the
    assets), so the resync reported those as changed and "unchanged" was unreachable
    from this Given. Seeding by a first sync through the transport is what makes the
    second one a true resync, on every transport.
    """
    when_sync_creative(ctx)


@given("a creative that does not exist in the library")
def given_creative_not_in_library(ctx: dict) -> None:
    """Set up a creative payload for a creative that has no DB row.

    Similar to ``given_creative_does_not_exist`` but uses different wording
    (INV-3 scenario). Ensures tenant/principal exist and builds a payload.

    When preceded by ``the Buyer is authenticated as principal "..."``
    the tenant may already exist in the DB (created by harness
    ``_ensure_default_data_for_auth``) but not in ctx. We resolve from
    the DB to avoid a duplicate-key error.
    """
    env = ctx["env"]
    _ensure_tenant_principal_from_db(ctx, env)
    format_id, agent_url, assets = _format_payload(ctx, env)
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-new-no-db-001",
        name="Brand New Creative",
        format_id={"id": format_id, "agent_url": agent_url},
        assets=assets,
    )
    ctx.setdefault("creatives", []).append(creative_payload)


@given('a creative with name "" and a known format_id')
def given_creative_with_empty_name(ctx: dict) -> None:
    """Set up a creative payload with an empty name — triggers CREATIVE_NAME_EMPTY.

    ``parsers.parse`` cannot match empty strings between quotes, so this
    literal step handles the ``name=""`` case explicitly.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    format_id, agent_url, assets = _format_payload(ctx, env)
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-empty-name-001",
        # An empty name is CONFORMANT to the pin — its wrongness is production's
        # CREATIVE_NAME_EMPTY, downstream of validation — so it takes no
        # malformation declaration. See tests/factories/malformed.py's asymmetry.
        name="",
        format_id={"id": format_id, "agent_url": agent_url},
        assets=assets,
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = format_id


@given(parsers.parse('a creative with name "{name}" but no format_id'))
def given_creative_with_name_no_format(ctx: dict, name: str) -> None:
    """Set up a creative payload with a name but no format_id — triggers CREATIVE_FORMAT_REQUIRED."""
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    creative_payload = malformed(
        "explicit_none",
        "format_id is present and explicitly None — not omitted. The scenario exercises "
        "CREATIVE_FORMAT_REQUIRED, so the key has to be on the wire carrying null; "
        "CreativeAssetRequest rejects it with [type=oneOf] provide exactly one of format_id or "
        "format_kind. The pin reports an omitted key identically, which is exactly why the kind "
        "is declared here rather than derived from the value.",
        CreativeAssetRequestFactory.payload(
            creative_id=f"creative-no-fmt-{name.lower().replace(' ', '-')}-001",
            name=name,
            # None, not OMIT: the key must be ON THE WIRE carrying null. The two are
            # indistinguishable in the pin's error and distinguishable only here.
            format_id=None,
        ),
        obligation=ErrorCode.INVALID_REQUEST,
    )
    ctx.setdefault("creatives", []).append(creative_payload)


@given("a creative with format_id but an empty name")
def given_creative_format_id_empty_name(ctx: dict) -> None:
    """Set up a creative with a valid format_id but empty name — boundary case."""
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-fmt-empty-name-001",
        name="",
        format_id=_creative_format_id_entry(ctx, env),
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = _scenario_format_id(ctx, env)


@given("a creative with invalid schema structure")
def given_creative_invalid_schema(ctx: dict) -> None:
    """Set up a creative payload with invalid schema — triggers CREATIVE_VALIDATION_FAILED.

    Has a format_id (to avoid CREATIVE_FORMAT_REQUIRED) but provides assets
    in the wrong structure (string instead of dict) to trigger schema validation.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    creative_payload = malformed(
        "wrong_type",
        "assets is a str where the pin requires an object — CreativeAssetRequest rejects it "
        "with assets Input should be a valid dictionary [type=dict_type]. format_id is valid on "
        "purpose so the scenario grades CREATIVE_VALIDATION_FAILED and not "
        "CREATIVE_FORMAT_REQUIRED; sending the wrong type is the scenario's whole subject, so "
        "these exact bytes must reach the wire unrepaired.",
        CreativeAssetRequestFactory.payload(
            creative_id="creative-invalid-schema-001",
            name="Invalid Schema Creative",
            format_id=_creative_format_id_entry(ctx, env),
            assets="not-a-valid-assets-structure",
        ),
        obligation=ErrorCode.INVALID_REQUEST,
    )
    ctx.setdefault("creatives", []).append(creative_payload)


def _get_sync_creative_result(ctx: dict) -> object:
    """Extract the first SyncCreativeResult from the response."""
    from src.core.schemas import SyncCreativesResponse

    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    resp = require_payload(ctx)
    assert isinstance(resp, SyncCreativesResponse), f"Expected SyncCreativesResponse, got {type(resp).__name__}"
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, f"Expected at least one SyncCreativeResult, got empty: {resp}"
    return results[0]


@then(parsers.parse('the response should include the creative with action "{action}"'))
def then_response_includes_creative_with_action(ctx: dict, action: str) -> None:
    """Assert the first SyncCreativeResult has the expected action (POST-S2)."""
    result = _get_sync_creative_result(ctx)
    action_val = getattr(result, "action", None)
    action_str = str(getattr(action_val, "value", action_val))
    assert action_str == action, f"POST-S2: Expected creative action '{action}', got '{action_str}'"


@then("the creative should have a status reflecting the approval workflow")
def then_creative_has_approval_workflow_status(ctx: dict) -> None:
    """The per-creative advisory status is drawn from the review lifecycle.

    The values are the pinned CreativeStatus enum
    (adcp 3.1.1, enums/creative-status.json), which sync-creatives-response.json
    names as the source for this field. Read off the wire for the reason
    ``_assert_creative_status`` gives.
    """
    _assert_success_response(ctx)
    entry = _wire_creatives_entry(ctx, latest_creative_id(ctx))
    status = entry.get("status")
    allowed = _creative_status_values()
    assert status in allowed, f"expected a CreativeStatus value (one of {sorted(allowed)}) on the wire, got {status!r}"


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / THEN steps — provenance policy boundary (kank)
# ═══════════════════════════════════════════════════════════════════════


def _build_creative_payload(ctx: dict, *, provenance: dict | None = None) -> dict:
    """Build a creative payload with optional provenance metadata."""
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    format_id, agent_url, assets = _format_payload(ctx, env)
    creative_id = f"creative-provenance-{'with' if provenance else 'without'}-001"
    payload: dict = CreativeAssetRequestFactory.payload(
        creative_id=creative_id,
        name="Provenance Test Creative",
        format_id={"id": format_id, "agent_url": agent_url},
        assets=assets,
    )
    if provenance is not None:
        payload["provenance"] = provenance
    ctx.setdefault("creatives", []).append(payload)
    ctx["creative_format_id"] = format_id
    return payload


@given("a creative with provenance metadata")
def given_creative_with_provenance(ctx: dict) -> None:
    """Set up a creative that includes AI provenance/disclosure metadata.

    Shaped by core/provenance.json: ``digital_source_type`` is an IPTC enum member,
    ``ai_tool`` an object naming the tool, ``disclosure`` an object with the declared
    ``required`` claim. The scalar ``source`` / ``model`` / ``disclosure`` string this
    used to send are not pin fields, so the request was refused before provenance
    handling ran and the scenario graded the refusal instead of the policy.
    """
    _build_creative_payload(
        ctx,
        provenance={
            "digital_source_type": "trained_algorithmic_media",
            "ai_tool": {"name": "Stable Diffusion XL"},
            "disclosure": {"required": True},
        },
    )


@given("a creative without provenance metadata")
@given("a creative with a known format_id but no provenance metadata")
@given("a creative with no provenance metadata")
@given("the Buyer Agent submits a creative whose manifest carries no provenance object at all")
def given_creative_without_provenance(ctx: dict) -> None:
    """Set up a creative that has no provenance metadata."""
    _build_creative_payload(ctx, provenance=None)


@given("a product with creative_policy.provenance_required = true")
def given_product_with_provenance_required_true(ctx: dict) -> None:
    """Create a product whose creative_policy requires provenance."""
    _setup_product_with_creative_policy(ctx, provenance_required=True)


@given("a product with creative_policy.provenance_required = false")
def given_product_with_provenance_required_false(ctx: dict) -> None:
    """Create a product whose creative_policy explicitly does NOT require provenance."""
    _setup_product_with_creative_policy(ctx, provenance_required=False)


@given("a product with creative_policy = null")
def given_product_with_null_creative_policy(ctx: dict) -> None:
    """Create a product whose creative_policy is null (not set)."""
    _setup_product_with_creative_policy(ctx, creative_policy=None)


@given("no product with provenance_required")
def given_no_product_with_provenance_required(ctx: dict) -> None:
    """No product exists in the tenant with provenance_required — check is skipped."""
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    env._commit_factory_data()


@given("the tenant has a slack_webhook_url configured")
def given_tenant_has_slack_webhook(ctx: dict) -> None:
    """Set a slack_webhook_url for both auth paths (see _set_tenant_approval_mode)."""
    _configure_tenant_field(ctx, "slack_webhook_url", _SLACK_WEBHOOK_URL)


@given("the tenant has no slack_webhook_url configured")
def given_tenant_no_slack_webhook(ctx: dict) -> None:
    """Ensure the tenant has no slack_webhook_url.

    Through ``configure_tenant_field`` like every sibling: clearing only the ORM
    column leaves the resolved identity's tenant dict carrying the old value, so
    INV-6 ("no Slack without a webhook") would be graded against a tenant
    production still sees a webhook on.
    """
    _configure_tenant_field(ctx, "slack_webhook_url", None)


def _setup_product_with_creative_policy(
    ctx: dict,
    *,
    provenance_required: bool | None = None,
    creative_policy: dict | None | object = ...,
) -> None:
    """Create a product with specified creative_policy for provenance boundary tests."""
    from tests.factories import ProductFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    format_id, agent_url, _assets = _format_payload(ctx, env)

    product_kwargs: dict = {
        "tenant": tenant,
        "format_ids": [{"agent_url": agent_url, "id": format_id}],
    }

    if creative_policy is None:
        product_kwargs["creative_policy"] = None
    elif creative_policy is not ...:
        product_kwargs["creative_policy"] = creative_policy
    elif provenance_required is not None:
        product_kwargs["creative_policy"] = {"provenance_required": provenance_required}

    product = ProductFactory(**product_kwargs)
    env._commit_factory_data()
    ctx["product"] = product


@then("the creative should be processed without warning")
def then_creative_processed_without_warning(ctx: dict) -> None:
    """Assert the creative was processed successfully with no provenance warnings."""
    assert "error" not in ctx, f"Expected successful processing without warning, but got error: {ctx.get('error')}"
    resp = require_payload(ctx)
    results = resp.creatives
    assert results, "Expected creative results for provenance check, but response.creatives is empty"
    first = results[0]
    warnings = first.warnings or []
    provenance_warnings = [w for w in warnings if "provenance" in str(w).lower()]
    assert not provenance_warnings, f"Expected no provenance warnings, got: {provenance_warnings}"


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / WHEN / THEN steps — media buy status transitions (avw0 + amto)
#   + ai-powered workflow (mah2) + workflow step attributes (nbfu)
#   + INV-6 no-product-id format skip (x1if)
#   + asset-level provenance (rx9u)
# ═══════════════════════════════════════════════════════════════════════


def _create_media_buy_with_status(
    ctx: dict,
    *,
    status: str,
    approved_at_set: bool,
) -> None:
    """Create a media buy with given status and approved_at state."""
    from datetime import UTC, datetime

    from tests.factories import MediaBuyFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    mb_kwargs: dict = {"tenant": tenant, "principal": principal, "status": status}
    if approved_at_set:
        mb_kwargs["approved_at"] = datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC)
    else:
        mb_kwargs["approved_at"] = None
    media_buy = MediaBuyFactory(**mb_kwargs)
    env._commit_factory_data()
    ctx["media_buy"] = media_buy


@given(parsers.parse('a media buy with status "{status}" and approved_at set'))
def given_media_buy_with_approved_at_set(ctx: dict, status: str) -> None:
    """Create a media buy with given status and non-null approved_at (BR-RULE-038 INV-4)."""
    _create_media_buy_with_status(ctx, status=status, approved_at_set=True)


@given(parsers.parse('a media buy with status "{status}" and approved_at null'))
def given_media_buy_with_approved_at_null(ctx: dict, status: str) -> None:
    """Create a media buy with given status and null approved_at (BR-RULE-038 INV-4 violated)."""
    _create_media_buy_with_status(ctx, status=status, approved_at_set=False)


@given(parsers.parse('a media buy with status "{status}" (non-draft)'))
def given_media_buy_non_draft(ctx: dict, status: str) -> None:
    """Create a non-draft media buy (BR-RULE-038 INV-5).

    The sentence's claim is the STATUS, and the Given refuses a row that contradicts
    it: INV-4's twin (``... and approved_at set``) seeds the same buy, and the only
    thing that made this one its own claim was the parenthetical nobody checked.
    """
    assert status != "draft", (
        f"the sentence claims a non-draft media buy, but the scenario asked for status {status!r}; "
        "INV-5 grades the non-draft path and cannot be established with a draft"
    )
    _create_media_buy_with_status(ctx, status=status, approved_at_set=True)


@given("assignments to a package in that media buy")
def given_assignments_to_package_in_that_media_buy(ctx: dict) -> None:
    """Create a package in ctx['media_buy'] and wire assignments for the creative.

    If no creative payload exists yet, creates a default one so the assignment
    can reference a creative_id. This supports scenarios (e.g., BR-RULE-040)
    that set up a media buy before the creative.
    """
    from tests.factories import MediaPackageFactory, ProductFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    media_buy = ctx["media_buy"]

    if not ctx.get("creatives"):
        given_creative_with_format(ctx)

    product = ProductFactory(tenant=tenant, format_ids=[_product_format_entry(ctx, env)])
    package = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )
    env._commit_factory_data()
    ctx["package"] = package
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {creative_id: [package.package_id]}


@given(parsers.re(r"an assignment to a package in a media buy with (?P<buy_state>.+)"))
def given_assignment_to_package_in_media_buy_with(ctx: dict, buy_state: str) -> None:
    """Create a media buy per buy_state description, then a package + assignment.

    buy_state phrases (from boundary scenario):
      - "status=draft and approved_at set"
      - "status=draft and no approved_at"
      - "status=active"
    """
    from tests.factories import MediaPackageFactory, ProductFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]

    if "draft" in buy_state and "approved_at set" in buy_state:
        _create_media_buy_with_status(ctx, status="draft", approved_at_set=True)
    elif "draft" in buy_state and "no approved_at" in buy_state:
        _create_media_buy_with_status(ctx, status="draft", approved_at_set=False)
    elif "active" in buy_state:
        _create_media_buy_with_status(ctx, status="active", approved_at_set=False)
    else:
        raise ValueError(f"Unknown buy_state phrase: {buy_state!r}")

    media_buy = ctx["media_buy"]
    product = ProductFactory(tenant=tenant, format_ids=[_product_format_entry(ctx, env)])
    package = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )
    env._commit_factory_data()
    ctx["package"] = package
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {creative_id: [package.package_id]}


@given("an existing assignment to a package in that media buy")
def given_existing_assignment_in_media_buy(ctx: dict) -> None:
    """Create a package with an existing assignment in ctx["media_buy"].

    Seeds the Creative ORM row + CreativeAssignment row for the first package,
    so that the sync sees it as a pre-existing assignment (for upsert).
    The media buy must already be created by a preceding Given step.
    """
    from tests.factories import (
        CreativeAssignmentFactory,
        CreativeFactory,
        MediaPackageFactory,
        ProductFactory,
    )

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    agent_url = env.DEFAULT_AGENT_URL
    media_buy = ctx["media_buy"]

    if not ctx.get("creatives"):
        given_creative_with_format(ctx)

    product = ProductFactory(tenant=tenant, format_ids=[_product_format_entry(ctx, env)])
    package_1 = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": product.product_id, "budget": 500.0},
    )

    creative_payload = ctx["creatives"][-1]
    creative_id = creative_payload["creative_id"]
    creative = CreativeFactory(
        tenant=tenant,
        principal=principal,
        creative_id=creative_id,
        name=creative_payload["name"],
        agent_url=_scenario_format_entry(ctx, env)["agent_url"],
        format=_scenario_format_entry(ctx, env)["id"],
    )
    CreativeAssignmentFactory(
        creative=creative,
        media_buy=media_buy,
        package_id=package_1.package_id,
        weight=100,
    )
    env._commit_factory_data()
    # Start building the assignments dict with the existing package
    ctx["assignments"] = {creative_id: [package_1.package_id]}


@given("a new assignment to another package in the same media buy")
def given_new_assignment_to_another_package(ctx: dict) -> None:
    """Add a second package to the media buy and include it in assignments.

    The first package already has an existing assignment (from the previous step).
    This second package is NEW — no prior assignment exists. Both should trigger
    the media buy status transition check.
    """
    from tests.factories import MediaPackageFactory, ProductFactory

    env = ctx["env"]
    tenant = ctx["tenant"]
    media_buy = ctx["media_buy"]

    product = ProductFactory(tenant=tenant, format_ids=[_product_format_entry(ctx, env)])
    package_2 = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": product.product_id, "budget": 500.0},
    )
    env._commit_factory_data()
    # Add the new package to the existing assignments dict
    creative_id = latest_creative_id(ctx)
    ctx["assignments"][creative_id].append(package_2.package_id)


@when("the Buyer Agent syncs the creative with assignments")
def when_sync_creative_with_assignments(ctx: dict) -> None:
    """Send sync_creatives request including assignments (media buy status tests)."""
    creatives = ctx.get("creatives", [])
    kwargs: dict = {"creatives": creatives}
    if "assignments" in ctx:
        kwargs["assignments"] = _assignments_for_the_wire(ctx["assignments"], ctx.get("assignment_terms"))
    if "assignment_entries" in ctx:
        # Raw entries, verbatim: the shape a scenario about the ARRAY itself (an entry
        # missing one of its required fields) needs, which the ctx map cannot express.
        kwargs["assignments"] = ctx["assignment_entries"]
    if "validation_mode" in ctx:
        kwargs["validation_mode"] = ctx["validation_mode"]
    dispatch_request(ctx, **kwargs)


def _get_media_buy_status_from_db(ctx: dict) -> str:
    """Re-read the media buy status from the DB after sync.

    The row is the only observable: sync-creatives-response.json describes the
    creatives it processed and the packages they were assigned to, and says
    nothing about the media buy's own lifecycle status. BR-RULE-038/040's
    transition is therefore a persistence obligation, and ``_readback`` expires
    the fixture's copy of the row first.
    """
    from sqlalchemy import select

    from src.core.database.models import MediaBuy

    media_buy = ctx["media_buy"]
    with _readback(ctx) as session:
        mb = session.scalars(
            select(MediaBuy).filter_by(
                media_buy_id=media_buy.media_buy_id,
                tenant_id=media_buy.tenant_id,
            )
        ).first()
        assert mb is not None, f"Media buy {media_buy.media_buy_id} not found in DB"
        return mb.status


@then(parsers.parse('the media buy status should transition to "{target_status}"'))
def then_media_buy_status_should_transition_to(ctx: dict, target_status: str) -> None:
    """Assert the media buy transitioned to the target status after sync."""
    assert "error" not in ctx, (
        f"Expected media buy transition to '{target_status}' but sync raised "
        f"{type(ctx.get('error')).__name__}: {ctx.get('error')}"
    )
    actual = _get_media_buy_status_from_db(ctx)
    assert actual == target_status, (
        f"Expected media buy status '{target_status}', got '{actual}'. "
        f"BR-RULE-038/040: draft + approved_at should transition to pending_creatives."
    )


@then(parsers.parse('the media buy status should remain "{expected_status}"'))
def then_media_buy_status_should_remain(ctx: dict, expected_status: str) -> None:
    """Assert the media buy status did NOT change from the expected value."""
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected media buy to remain '{expected_status}' but sync raised {type(error).__name__}: {error}"
    )
    actual = _get_media_buy_status_from_db(ctx)
    assert actual == expected_status, f"Expected media buy status to remain '{expected_status}', but got '{actual}'"


@then("the media buy should transition to pending_creatives")
def then_media_buy_should_transition_to_pending_creatives(ctx: dict) -> None:
    """Assert draft + approved_at media buy transitioned to pending_creatives (boundary)."""
    then_media_buy_status_should_transition_to(ctx, "pending_creatives")


@then("the media buy should remain in draft status")
def then_media_buy_should_remain_in_draft(ctx: dict) -> None:
    """Assert draft + no approved_at media buy stays draft (boundary)."""
    then_media_buy_status_should_remain(ctx, "draft")


@then("the media buy status should not change")
def then_media_buy_status_should_not_change(ctx: dict) -> None:
    """Assert a non-draft media buy's status was unchanged (boundary)."""
    assert "error" not in ctx, (
        f"Expected no status change but sync raised {type(ctx.get('error')).__name__}: {ctx.get('error')}"
    )
    original_status = ctx["media_buy"].status
    actual = _get_media_buy_status_from_db(ctx)
    assert actual == original_status, (
        f"Expected media buy status to remain '{original_status}' (non-draft), got '{actual}'"
    )


@then(parsers.parse('the media buy status should be "{status}"'))
def then_media_buy_status_uc006(ctx: dict, status: str) -> None:
    """UC-006 override: check media buy status from DB after creative sync.

    The generic then_media_buy.py step reads resp.status, which is absent on
    SyncCreativesResponse. This override queries the DB directly when a media
    buy was created by a UC-006 Given step.
    """
    assert "error" not in ctx, (
        f"Expected media buy status '{status}' but sync raised {type(ctx.get('error')).__name__}: {ctx.get('error')}"
    )
    assert "media_buy" in ctx, "No media buy in ctx — cannot check status from DB"
    actual = _get_media_buy_status_from_db(ctx)
    assert actual == status, (
        f"Expected media buy status '{status}', got '{actual}'. "
        f"Production may not implement media buy status transition during creative sync."
    )


# --- mah2: ai-powered workflow steps (BR-RULE-037 INV-4) ---


@then("a workflow step should be created")
def then_workflow_step_should_be_created(ctx: dict) -> None:
    """Assert that at least one workflow step was created (INV-4)."""
    _assert_success_response(ctx)
    _assert_workflow_steps(ctx["env"], expect_present=True)


@then("a background AI review task should be submitted")
def then_background_ai_review_submitted(ctx: dict) -> None:
    """Assert ai-powered mode submitted a background AI review task (INV-4).

    Production's ai-powered path in _processing.py hands the creative to the
    background AI-review executor. ``_assert_ai_reviews_taken_up`` names the
    observable per transport — the submit in process, the committed verdict over
    e2e_rest.
    """
    _assert_ai_reviews_taken_up(
        ctx,
        _requested_creative_ids(ctx),
        why="INV-4: ai-powered submits one AI review per synced creative",
    )


# --- local-uc006-dry-run-out-of-transaction-effects: the AI-review submit seam ---
#
# The ai-powered branch of _processing.py hands a job to `_ai_review_executor`; that
# job opens its OWN AdminCreativeUoW, COMMITS `status` + `data["ai_review"]`, and
# then sends Slack and the push webhook. None of it is inside the sync
# transaction, so a preview cannot undo it by rolling back — the submit itself is
# the thing that must not happen. CreativeSyncEnv mocks the executor
# (EXTERNAL_PATCHES "ai_review_executor"), which turns "no AI review happened"
# into a value comparison instead of a race against a background thread.


def _requested_creative_ids(ctx: dict) -> list[str]:
    """The creative_ids this scenario's request named, sorted and deduplicated."""
    return sorted({c["creative_id"] for c in ctx["creatives"]})


def _assert_ai_reviews_taken_up(ctx: dict, expected: list[str], *, why: str) -> None:
    """The background AI reviewer took up exactly *expected*, and nothing else.

    ``env.ai_reviews_taken_up`` owns the per-transport answer: the mocked
    executor's submit calls in process, and over e2e_rest a bounded wait on the
    verdict the real reviewer commits (tests/harness/creative_sync.py). What the
    step EXPECTS is also the wait target, so the negative sentence ("no AI review
    is submitted") reads the current state without waiting for one, and the
    positive sentences wait for the creatives they name.
    """
    _assert_success_response(ctx)
    taken_up = ctx["env"].ai_reviews_taken_up(awaiting=expected)
    assert taken_up == expected, f"{why}: expected AI reviews for {expected}, got {taken_up}"


@when("the Buyer Agent previews the creative with dry_run true")
def when_preview_creative_dry_run(ctx: dict) -> None:
    """Dispatch the SAME sync_creatives request under dry_run=true.

    Spec: creative/sync-creatives-request.json#/properties/dry_run — "preview
    changes without applying them."
    """
    ctx["dry_run"] = True
    when_sync_creative(ctx)


@then("the AI review submissions name exactly the synced creative")
def then_ai_review_submitted_for_synced_creative(ctx: dict) -> None:
    """Control: the live ai-powered branch submits one review, for THIS creative.

    Naming the creative_id (rather than counting calls) is what makes the sibling
    preview scenario's empty-list assertion non-vacuous: a wrong patch target or a
    dead ai-powered branch fails here first.
    """
    _assert_ai_reviews_taken_up(
        ctx,
        _requested_creative_ids(ctx),
        why="the ai-powered live sync must submit an AI review for the creative it synced",
    )


@then("no AI review is submitted")
def then_no_ai_review_submitted(ctx: dict) -> None:
    """A preview must not submit a review whose job commits outside its transaction."""
    _assert_ai_reviews_taken_up(
        ctx,
        [],
        why=(
            "dry_run must submit no AI review — that job opens its own AdminCreativeUoW "
            "and commits a verdict, which the preview's rollback cannot undo"
        ),
    )


@then("each AI review submission observes the creative exactly as the sync committed it")
def then_ai_review_observes_committed_creative(ctx: dict) -> None:
    """The live-branch half of the seam: the effect runs AFTER its transaction commits.

    The submitted job opens its own session, so committed state is the only
    state it can read. CreativeSyncEnv records, at each submit, what an
    INDEPENDENT connection sees; this compares that against what the finished
    request actually left on the row. They are the same read of the same row, so
    equality is the whole invariant, and each branch fails it differently while the
    submit stays inside the transaction: the create branch's row is not there at all
    (``None``), the update branch's is there carrying its PRE-update state.
    """
    _assert_success_response(ctx)
    observed = ctx["env"].ai_review_commit_observations
    expected_ids = [c["creative_id"] for c in ctx["creatives"]]
    committed = _persisted_creative_fingerprints(ctx)

    assert sorted(observed) == sorted(expected_ids), (
        f"expected an AI-review observation for each of {expected_ids}, got {sorted(observed)}"
    )
    assert observed == {creative_id: committed.get(creative_id) for creative_id in expected_ids}, (
        f"the AI review was handed creatives the sync had not committed: observed {observed}, "
        f"committed {committed} — the job opens its own session, so it would have read exactly "
        "what is observed here (None = no row yet; a differing fingerprint = pre-update state)"
    )


def _creative_agent_calls(ctx: dict) -> list[str]:
    """Outbound creative-agent calls the sync made, as method names.

    ``registry`` is the patched external service
    (CreativeSyncEnv.EXTERNAL_PATCHES), so build_creative / preview_creative
    land on it as recorded calls. This is the ONLY way to grade the four
    outbound gates: they exist to make a preview DIFFER from a live run in side
    effects, and the dry_run parity oracle compares preview against live, so by
    construction it can never see them (a parity oracle scores un-gating as MORE
    parity, not less).
    """
    registry = ctx["env"].mock.get("registry")
    assert registry is not None, (
        "CreativeSyncEnv must patch src.core.creative_agent_registry.get_creative_agent_registry "
        "for the outbound creative-agent calls to be observable"
    )
    # Read the two methods directly rather than parent.method_calls: the env
    # ASSIGNS build_creative as its own AsyncMock, which replaces the auto-created
    # child and so never lands in the parent's method_calls -- an accessor reading
    # only method_calls would report "no calls" for a live run and grade nothing.
    agent_registry = registry.return_value
    return [
        name
        for name in ("build_creative", "preview_creative")
        if getattr(getattr(agent_registry, name, None), "call_count", 0)
    ]


@given("a generative creative whose format is served by a creative agent")
def given_generative_creative_served_by_agent(ctx: dict) -> None:
    """A creative whose format actually REACHES the creative agent.

    The plain "known format_id" creative is static, and a static creative never
    calls build_creative/preview_creative at all -- so a preview asserting "no
    agent request" against it would pass without the gates existing. Generative
    is the shape that exercises them.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)

    fmt = env.setup_generative_build(format_id="gen_banner")
    creative_id = "creative-generative-001"
    ctx.setdefault("creatives", []).append(
        CreativeAssetRequestFactory.payload(
            creative_id=creative_id,
            name="Generative Creative",
            format_id=fmt,
            # Built through the canonical AssetSpec mechanism, never hand-rolled:
            # 3.1 assets are a discriminated union keyed by role, and a hand-built
            # dict misses the asset_type discriminator (GH #1391).
            assets=build_assets(text_spec("prompt", content="a headline about running shoes")),
        )
    )
    ctx["creative_format_id"] = fmt["id"]
    ctx["creative_agent_url"] = fmt["agent_url"]
    ctx["expected_creative_ids"] = [creative_id]


def _persisted_creative_fingerprints(ctx: dict) -> dict[str, tuple]:
    """A per-row FINGERPRINT of the tenant's library: {creative_id: (status, data)}.

    Not the id SET. An id set can only see a row being ADDED, so a preview that
    commits an in-place UPDATE to a row that already existed is invisible to it
    -- and the update branch is exactly where that happens. Measured: with the
    transaction disposal inverted, the seeded rows came back carrying
    generative_build_result / preview_response and an id-set oracle still passed.
    """
    from src.core.database.repositories.uow import CreativeUoW

    with CreativeUoW(ctx["tenant"].tenant_id) as uow:
        assert uow.creatives is not None
        return {
            c.creative_id: creative_fingerprint(c)
            for c in uow.creatives.list_by_principal(ctx["principal"].principal_id)
        }


@given(parsers.parse("a {creative_state} creative on a {format_kind} format served by a creative agent"))
def given_creative_reaching_the_agent(ctx: dict, creative_state: str, format_kind: str) -> None:
    """A creative payload that reaches EXACTLY ONE of production's four outbound sites.

    The four ``registry.build_creative`` / ``registry.preview_creative`` calls in
    _processing.py partition on two independent dimensions, and a payload only
    ever reaches one cell:

        new      x generative  -> build_creative   (create branch)
        new      x static      -> preview_creative (create branch)
        existing x generative  -> build_creative   (update branch)
        existing x static      -> preview_creative (update branch)

    Which is why one scenario cannot grade all four: an outline over both
    dimensions is the only shape that reaches every site. ``static`` here means
    agent-served but non-generative -- ``output_format_ids`` empty is precisely
    what sends production down the preview branch instead of the build branch.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)

    generative = format_kind == "generative"
    fmt = env.configure_agent_served_creative(
        generative=generative, format_id="gen_banner" if generative else "static_300x250"
    )

    creative_id = f"creative-{creative_state}-{format_kind}-001"
    ctx.setdefault("creatives", []).append(
        CreativeAssetRequestFactory.payload(
            creative_id=creative_id,
            name="Agent-served Creative",
            format_id=fmt,
            assets=build_assets(text_spec("prompt", content="a headline about running shoes")),
        )
    )
    ctx["creative_format_id"] = fmt["id"]
    ctx["creative_agent_url"] = fmt["agent_url"]
    ctx["expected_creative_ids"] = [creative_id]

    if creative_state == "existing":
        # The update branch is unreachable for a creative that is not already on
        # file, so two of the four sites can only be graded from a seeded row.
        given_creative_already_exists(ctx)

    # The persisted set BEFORE the request. A preview must leave it exactly as
    # it found it -- which for a new creative means "still empty" and for an
    # existing one means "still just that row", not "empty".
    ctx["pre_request_library"] = _persisted_creative_fingerprints(ctx)


@then("no creative agent request is made")
def then_no_creative_agent_request(ctx: dict) -> None:
    """A preview must not fire a request at a creative agent's endpoint.

    Same rule the accounts branch states for activation proofs
    (src/core/tools/accounts.py: "a preview must not fire a request at a buyer's
    endpoint") and that plan section 6 keeps _resolve_activation_proofs' own
    dry_run branch for. An outbound HTTP call is not undone by the transaction's
    rollback, so a preview that makes one has already had a real effect on a
    third party.
    """
    _assert_success_response(ctx)
    calls = _creative_agent_calls(ctx)
    assert calls == [], (
        f"dry_run called the creative agent: {calls} — an outbound request is not "
        "reachable by the preview's rollback, so the preview has already acted"
    )


@then("the creative agent is called to build or preview the creative")
def then_creative_agent_called(ctx: dict) -> None:
    """Non-vacuity control for the preview assertion above.

    Without this, "no creative agent request is made" would also pass against a
    renamed method, a wrong patch target, or a format that never reaches the
    agent at all -- i.e. it would grade nothing.
    """
    _assert_success_response(ctx)
    calls = _creative_agent_calls(ctx)
    assert calls != [], (
        "the live branch made no creative-agent call, so the preview scenario's 'no request' assertion proves nothing"
    )


@then("no creative is persisted for the tenant")
def then_no_creative_persisted_for_tenant(ctx: dict) -> None:
    """The other half of the same claim: the preview left the library untouched.

    Compared against the set captured BEFORE the request, not against empty: on
    the update cells the Given legitimately seeds a row, and asserting emptiness
    there would fail an honest preview. What must hold either way is that the
    request added nothing.
    """
    _assert_success_response(ctx)
    before = ctx.get("pre_request_library", {})
    after = _persisted_creative_fingerprints(ctx)
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(cid for cid in set(after) & set(before) if after[cid] != before[cid])
    # MODIFIED is what covers this lane's riskiest deletion: delete_missing lost
    # its `if not dry_run:` guard, so a preview now performs the deactivation
    # write and relies entirely on the rollback. That write ARCHIVES (sets
    # status), and list_by_principal does not filter on status, so an
    # un-rolled-back deactivation shows up as a MODIFIED row, never a missing
    # one -- measured, by injecting exactly that write out-of-transaction.
    # REMOVED is defence-in-depth: nothing in the sync path can make a row
    # disappear today, so it guards against a future hard delete rather than
    # against delete_missing.
    assert added == [] and removed == [] and modified == [], (
        f"dry_run wrote to the library — added {added}, removed {removed}, modified {modified}; "
        "a preview must leave it exactly as it found it"
    )


@then("Slack notification should be deferred until AI review completes")
def then_slack_notification_deferred(ctx: dict) -> None:
    """Assert Slack notification was NOT sent immediately for ai-powered mode (INV-4).

    In ai-powered mode, Slack notification is deferred until AI review completes, so the
    Slack sender must not have been used during the sync. Production enters the
    notification step for every creative needing approval and decides inside it, so the
    step being entered is not the observable; the sender is.
    """
    _assert_slack_notified(ctx, [], why="INV-4: ai-powered defers Slack until AI review completes")
    # DEFERRED is not NEVER. Asserting only "nothing was sent" is the body
    # ``then_no_slack_notification`` already has, and it passes just as happily when the
    # notification is never sent at all -- which is a different invariant (INV-2/INV-6) and
    # a bug on this path. What makes this sentence its own claim is that something exists to
    # defer TO, so the review task is asserted here as well.
    then_background_ai_review_submitted(ctx)


# --- nbfu: workflow step attributes (BR-RULE-037 INV-5) ---


def _get_first_workflow_step(ctx: dict) -> object:
    """Assert workflow steps exist and return the first one for attribute checks."""
    _assert_success_response(ctx)
    steps = _assert_workflow_steps(ctx["env"], expect_present=True)
    return steps[0]


@then(parsers.parse('the workflow step should have step_type "{expected}"'))
def then_workflow_step_has_step_type(ctx: dict, expected: str) -> None:
    """Assert the workflow step's step_type matches (INV-5)."""
    step = _get_first_workflow_step(ctx)
    assert step.step_type == expected, f"INV-5: Expected step_type '{expected}', got '{step.step_type}'"


@then(parsers.parse('the workflow step should have owner "{expected}"'))
def then_workflow_step_has_owner(ctx: dict, expected: str) -> None:
    """Assert the workflow step's owner matches (INV-5)."""
    step = _get_first_workflow_step(ctx)
    assert step.owner == expected, f"INV-5: Expected owner '{expected}', got '{step.owner}'"


@then(parsers.parse('the workflow step should have status "{expected}"'))
def then_workflow_step_has_status(ctx: dict, expected: str) -> None:
    """Assert the workflow step's status matches (INV-5)."""
    step = _get_first_workflow_step(ctx)
    assert step.status == expected, f"INV-5: Expected status '{expected}', got '{step.status}'"


# --- x1if: INV-6 no product_id on package skips format check (BR-RULE-039) ---


@given("a creative with any format_id")
def given_creative_with_any_format(ctx: dict) -> None:
    """A creative whose own format is deliberately UNCONSTRAINED.

    The two scenarios on this sentence — BR-RULE-039 INV-3 (empty product
    format_ids allows all formats) and INV-6 (no product_id skips the check) —
    grade the PRODUCT side of format compatibility. The creative's format is the
    part that must not matter, so the arrangement here is the plain known-format
    creative and nothing else.

    Deliberately NOT registering the format with the creative agent, which is what
    separates this from its two neighbours: arranging an agent-served format would
    establish a property these scenarios are asserting the absence of a dependence
    on, and a later reader would not be able to tell which of the two the pass
    depended on.
    """
    given_creative_with_format(ctx)


@given("assignments to a package that has no product_id")
def given_assignments_to_package_no_product_id(ctx: dict) -> None:
    """Create a package with no product_id so format compatibility is skipped."""
    from tests.factories import MediaBuyFactory, MediaPackageFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    package = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"budget": 1000.0},
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {creative_id: [package.package_id]}


def _assert_format_check_outcome(ctx: dict, label: str) -> None:
    """Assert the creative was accepted past the format compatibility gate.

    Shared by the ``skipped`` and ``passed`` outcomes: no error was raised, the
    response carries at least one SyncCreativeResult, the first result's action
    is accept-like (created/updated/unchanged), and there are no format-related
    warnings. ``label`` tags the outcome in the failure messages.
    """
    assert "error" not in ctx, f"format check ({label}): expected no error, but production raised: {ctx.get('error')}"
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, f"format check ({label}): expected at least one SyncCreativeResult"
    first = results[0]
    action_str = str(getattr(getattr(first, "action", None), "value", getattr(first, "action", None)))
    assert action_str in ("created", "updated", "unchanged"), (
        f"format check ({label}): expected creative accepted (created/updated/unchanged), got action='{action_str}'"
    )
    warnings = getattr(first, "warnings", None) or []
    format_warnings = [w for w in warnings if "format" in str(w).lower()]
    assert not format_warnings, f"format check ({label}): expected no format warnings, got: {format_warnings}"


@then("the format compatibility check should be skipped")
def then_format_check_skipped(ctx: dict) -> None:
    """Assert the format check was skipped because the package has no product_id.

    Verifies: (1) the package genuinely has no product_id (precondition for skip),
    (2) the creative was accepted despite having an arbitrary format_id that could
    not match any product format set — proving the check was bypassed, not passed.
    (3) No format-related warnings or errors appear on the per-creative result.
    """
    # Verify the skip precondition: the package has no product_id
    package = ctx.get("package")
    if package is not None:
        pkg_product_id = getattr(package, "product_id", None)
        assert pkg_product_id is None, (
            f"INV-6: format check skip requires package with no product_id, but got '{pkg_product_id}'"
        )
    _assert_format_check_outcome(ctx, "skipped")


@then("the format compatibility check should pass")
def then_format_check_should_pass(ctx: dict) -> None:
    """Assert the format check passed (empty format_ids allows all).

    The positive outcome is: the creative was accepted past the format
    compatibility gate (action is created/updated, no format-incompatibility
    warning in the per-creative result). Hard-asserts the happy path.
    """
    _assert_format_check_outcome(ctx, "passed")


# --- pzlv: assignment format compatibility boundary (BR-RULE-039) ---


def _setup_assignment_package_for_format(
    ctx: dict,
    *,
    product_format_ids: list[dict] | None,
    product_id_in_config: bool = True,
) -> None:
    """Shared helper to create a media buy + package for format compatibility scenarios.

    Args:
        product_format_ids: Format IDs for the product. None means no product at all.
        product_id_in_config: Whether to include product_id in package_config.
    """
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]

    # Use UUID-based IDs for e2e_rest to avoid collisions in shared Docker DB
    extra_mb: dict = {}
    extra_pkg: dict = {}
    extra_prod: dict = {}
    if is_e2e(ctx):
        extra_mb["media_buy_id"] = _e2e_unique_id("mb")
        extra_pkg["package_id"] = _e2e_unique_id("pkg")
        extra_prod["product_id"] = _e2e_unique_id("prod")

    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active", **extra_mb)
    package_config: dict = {"budget": 1000.0}

    if product_id_in_config and product_format_ids is not None:
        product = ProductFactory(tenant=tenant, format_ids=product_format_ids, **extra_prod)
        package_config["product_id"] = product.product_id
        ctx["product"] = product

    package = MediaPackageFactory(media_buy=media_buy, package_config=package_config, **extra_pkg)
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {creative_id: [package.package_id]}


@given("an assignment to a package whose product accepts this format")
def given_assignment_product_accepts_format(ctx: dict) -> None:
    """Create a package whose product's format_ids exactly match the creative's format.

    Uses the creative_format_id and creative_agent_url set by the preceding
    'a creative with a known format_id' Given step.
    """
    format_id = ctx["creative_format_id"]
    agent_url = ctx["creative_agent_url"]
    _setup_assignment_package_for_format(
        ctx,
        product_format_ids=[{"agent_url": agent_url, "id": format_id}],
    )


@given("an assignment to a package with no product_id")
def given_assignment_package_no_product_id(ctx: dict) -> None:
    """Create a package with no product_id in its config.

    Per BR-RULE-039 INV-6: format compatibility check is skipped entirely.
    """
    _setup_assignment_package_for_format(ctx, product_format_ids=None, product_id_in_config=False)


@given("an assignment to a package whose product does not accept this format")
def given_assignment_product_rejects_format(ctx: dict) -> None:
    """Create a package whose product only accepts a different format.

    The creative has format_id 'display_300x250' but the product only
    accepts 'video_30s', causing a format mismatch.
    """
    agent_url = ctx["creative_agent_url"]
    _setup_assignment_package_for_format(
        ctx,
        product_format_ids=[{"agent_url": agent_url, "id": "video_30s"}],
    )
    ctx["validation_mode"] = "strict"


@then("the assignment should be created (all formats allowed)")
def then_assignment_created_all_formats(ctx: dict) -> None:
    """Assert the assignment succeeded because the product has no format restrictions.

    Per BR-RULE-039 INV-3: empty format_ids means all creative formats are allowed.
    """
    assert "error" not in ctx, f"Expected success (all formats allowed) but got error: {ctx.get('error')}"
    assigned = _get_creative_assigned_to(ctx)
    expected = ctx["package"].package_id
    assert expected in assigned, f"Expected {expected!r} in assigned_to (empty format_ids), got {assigned}"


# --- yqpf: format compatibility — format_id key variants + URL normalization (BR-RULE-039) ---


def _product_format_at_respelled_agent(ctx: dict, respell) -> None:
    """Seed the product's format at the SAME agent the creative uses, spelled differently.

    Both INV-1 scenarios are this, with one function swapped: re-spell the agent_url on an
    axis the pinned canonicalization algorithm COLLAPSES (host case) or one it PRESERVES
    (the path), and let the wire say whether the seller agrees.

    The WHOLE entry comes from the shared helper before ``respell`` touches only its
    agent_url. A format's identity is the (agent_url, id) PAIR, so a re-spelled agent
    carrying a hand-picked id would name a format that agent does not serve, and the
    scenario would grade the wrong thing.

    The agent is whatever the CURRENT transport actually serves. The original version used
    a fictional "https://agent.example.com", which no live registry can resolve: the
    scenario could not succeed on e2e_rest in principle, and in-process it only appeared to
    because the registry is mocked.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    entry = dict(_scenario_format_entry(ctx, env))
    original = entry["agent_url"]
    entry["agent_url"] = respell(original)
    assert entry["agent_url"] != original, (
        f"this step exists to make the two spellings DIFFER, but re-spelling {original!r} "
        f"produced the same string, so the scenario would grade nothing"
    )
    _setup_assignment_package_for_format(ctx, product_format_ids=[entry])


@given("a product whose format agent_url is the same agent with the host upper-cased")
def given_product_agent_url_host_upper_cased(ctx: dict) -> None:
    """BR-RULE-039 INV-1: host case is collapsed at step 2, for ANY URL.

    Wire-observable: if canonicalization stopped equating the two spellings the product's
    format would not resolve against the creative's, no assignment would be created, and
    assigned_to would come back empty. That is why this is a scenario and not a unit test
    of a string helper.

    Host case rather than a trailing slash: step 5 collapses a trailing slash only on an
    EMPTY path, so a trailing-slash scenario is true of the in-process seed and false of
    the e2e one, whose agent_url carries "/api/creative-agent". Host case is spec-true on
    every transport, so the expectation does not need revisiting per transport -- or when
    the vendored canonicalizer is replaced by the SDK's.
    """

    def upper_host(url: str) -> str:
        scheme, sep, rest = url.partition("://")
        host, slash, path = rest.partition("/")
        return f"{scheme}{sep}{host.upper()}{slash}{path}"

    _product_format_at_respelled_agent(ctx, upper_host)


@given("a product whose format agent_url is the same host at a different path")
def given_product_agent_url_different_path(ctx: dict) -> None:
    """BR-RULE-039 INV-1b: the PATH is preserved, so a different path is a different agent.

    The negative half. ``remove_dot_segments`` normalizes a path but never discards one,
    and the pin's own example agent_url is path-bearing
    ("https://publisher.com/.well-known/adcp/sales") -- so one host may serve MCP at /mcp
    and A2A at /a2a as two distinct agents. A canonicalizer that collapsed too much would
    satisfy INV-1 and fail here; only the pair pins the rule.
    """
    _product_format_at_respelled_agent(ctx, lambda url: url.rstrip("/") + "/some-other-agent")


@given("the creative agent is reachable")
def given_creative_agent_is_reachable(ctx: dict) -> None:
    """Ensure the creative agent mock returns valid format data (agent reachable).

    The CreativeSyncEnv already mocks the registry by default. This step
    explicitly configures it to return a successful response for the creative's
    format, confirming the agent is reachable.
    """
    from unittest.mock import AsyncMock

    env = ctx["env"]
    agent_url = env.DEFAULT_AGENT_URL
    format_id = _scenario_format_id(ctx, env)

    from tests.factories.format import FormatFactory, FormatIdFactory

    fid = FormatIdFactory(agent_url=agent_url, id=format_id)
    fmt = FormatFactory(format_id=fid)
    registry = env.mock["registry"].return_value
    registry.get_format = AsyncMock(return_value=fmt)


# --- rx9u: asset-level provenance replaces creative-level (BR-RULE-094 INV-5) ---


@given(parsers.parse('a creative with provenance declaring digital_source_type "{source_type}"'))
def given_creative_with_provenance_source_type(ctx: dict, source_type: str) -> None:
    """Build a creative payload with creative-level provenance.digital_source_type.

    BOTH HALVES of the format identity come from the transport switch, and the assets
    with them. This step used to take the ID from ``_format_payload`` and pin the
    ``agent_url`` to ``env.DEFAULT_AGENT_URL``, which is precisely the defect
    ``_scenario_format_entry``'s docstring names with the halves swapped: over e2e_rest the
    payload claimed the live catalog's ``display_300x250_image`` at
    ``creative.test.example.com``, an agent that does not serve it. ``fetch_format_spec``
    then resolved nothing and ``_validate_creative_input`` refused the entry with
    ``AdCPFormatNotFoundError`` (src/core/tools/creatives/_validation.py) -- inside a
    transport-level SUCCESS, so the compliance Then above passed and the read-back
    underneath reported "No creative found in DB". The factory's default assets were wrong
    for the same reason: they key an ``image`` slot, and the catalog format wants
    ``banner_image``.

    In process the pair and the assets are byte-identical to what this built before.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    format_id_entry = _creative_format_id_entry(ctx, env)
    _format_id, _agent_url, assets = _format_payload(ctx, env)
    creative_id = "creative-provenance-source-001"
    payload: dict = CreativeAssetRequestFactory.payload(
        creative_id=creative_id,
        name="Provenance Source Type Creative",
        format_id=format_id_entry,
        assets=assets,
        # ADDING a real field, not perturbing one: provenance is declared on
        # CreativeAssetRequest, so extra="forbid" would catch a typo here.
        provenance={"digital_source_type": source_type},
    )
    ctx.setdefault("creatives", []).append(payload)
    ctx["creative_format_id"] = format_id_entry["id"]


@given(parsers.parse('an asset within the creative declaring digital_source_type "{source_type}"'))
def given_asset_with_provenance_source_type(ctx: dict, source_type: str) -> None:
    """Add asset-level provenance to the last creative's first asset.

    The image slot is an individual asset (single object), so asset-level provenance
    attaches directly to that object (AdCP core/provenance.json — provenance attaches to
    individual assets; the most-specific provenance replaces the inherited one).
    """
    creative_payload = ctx["creatives"][-1]
    assets = creative_payload.get("assets", {})
    first_key = next(iter(assets))
    assets[first_key]["provenance"] = {"digital_source_type": source_type}
    ctx["asset_provenance_source_type"] = source_type


@then(
    parsers.re(r'the asset should have provenance "(?P<expected>[^"]+)" ' r'\(not inherited "(?P<inherited>[^"]+)"\)')
)
def then_asset_has_provenance_not_inherited(ctx: dict, expected: str, inherited: str) -> None:
    """Assert asset-level provenance replaces creative-level (INV-5, BR-RULE-094).

    Production stores provenance on the creative's data dict. The asset-level
    provenance should replace creative-level entirely (no field-level merge).
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected provenance assertion but sync raised {type(error).__name__}: {error}"
    )
    creative = _get_creative_from_db(ctx)
    data = getattr(creative, "data", None) or {}
    # `or {}` at every hop, never a .get default: the default applies only to an ABSENT
    # key, and the live server stores these as explicit NULLs where the in-process path
    # leaves them absent. `data.get("provenance", {})` therefore returned None in-network
    # and the next .get raised AttributeError, which says nothing about the obligation.
    # This is a band-aid, kept only so the next run reports the obligation instead of a
    # crash — the defect is the untyped traversal itself: FIXME(#2236) on
    # `_get_creative_from_db`.
    assets = data.get("assets") or {}
    assert assets, (
        "SPEC-PRODUCTION GAP: creative.data has no 'assets' key — asset-level provenance storage not implemented in production. BR-RULE-094 INV-5: asset-level provenance should replace creative-level."
    )
    first_asset = next(iter(assets.values())) or {}
    asset_provenance = (first_asset or {}).get("provenance") or {}
    asset_source = asset_provenance.get("digital_source_type")
    assert asset_source is not None, (
        "SPEC-PRODUCTION GAP: asset-level provenance.digital_source_type not stored in creative.data.assets — production may not support per-asset provenance yet. BR-RULE-094 INV-5."
    )
    assert asset_source == expected, (
        f"INV-5: Expected asset provenance '{expected}', got '{asset_source}' "
        f"(creative-level was '{inherited}' — should NOT be inherited)"
    )


@then("no field-level merging should occur")
def then_no_field_level_merging(ctx: dict) -> None:
    """Assert that asset-level provenance is a full replacement, not a merge (INV-5).

    If asset provenance has only digital_source_type but creative provenance had
    additional fields, those additional fields should NOT appear in the asset's
    provenance — full replacement semantics.
    """
    assert "error" not in ctx, f"Expected no-merge assertion but sync raised: {ctx.get('error')}"
    creative = _get_creative_from_db(ctx)
    data = getattr(creative, "data", None) or {}
    # `or {}` at every hop — see the sibling step above: a .get default does not fire on a
    # key that is present and null, which is how the live server stores an undeclared
    # block. Band-aid, same FIXME(#2236).
    assets = data.get("assets") or {}
    assert assets, (
        "SPEC-PRODUCTION GAP: creative.data has no 'assets' key — cannot verify no-merge semantics. BR-RULE-094 INV-5."
    )
    creative_provenance = data.get("provenance") or {}
    first_asset = next(iter(assets.values())) or {}
    asset_provenance = (first_asset or {}).get("provenance") or {}
    assert asset_provenance, (
        "SPEC-PRODUCTION GAP: no asset-level provenance stored — cannot verify replacement semantics. BR-RULE-094 INV-5."
    )
    # Full replacement: a creative-only provenance VALUE must not appear on the asset.
    # Provenance is stored as the typed model it is, so every optional field is present
    # on both sides, None when undeclared; a None is an absent field, not a merged one,
    # and only a declared creative-level value that turns up on the asset is a leak.
    creative_only_keys = {k for k, v in creative_provenance.items() if v is not None} - {"digital_source_type"}
    leaked = {k for k in creative_only_keys if asset_provenance.get(k) is not None}
    assert not leaked, (
        f"INV-5: Field-level merge detected — creative-only provenance fields "
        f"leaked into asset provenance: {leaked}. "
        f"creative_provenance={creative_provenance}, "
        f"asset_provenance={asset_provenance}"
    )
    # Verify asset provenance reflects its own declared value, not the creative's
    expected_asset_source = ctx.get("asset_provenance_source_type")
    if expected_asset_source is not None:
        actual_asset_source = asset_provenance.get("digital_source_type")
        assert actual_asset_source == expected_asset_source, (
            f"INV-5: asset provenance digital_source_type should be "
            f"'{expected_asset_source}' (asset's own value), "
            f"got '{actual_asset_source}' — may have inherited from creative"
        )


# --- additional steps for related scenarios ---


@then("the creative should be processed normally")
def then_creative_processed_normally(ctx: dict) -> None:
    """Assert the creative was processed with a success action (created/updated)."""
    error = ctx.get("error")
    assert error is None, f"Expected normal processing, but got {type(error).__name__}: {error}"
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, f"Expected at least one creative result, got empty from {type(resp).__name__}"
    actions = [str(getattr(getattr(r, "action", None), "value", getattr(r, "action", None))) for r in results]
    assert any(a in ("created", "updated") for a in actions), (
        f"Expected a success action (created/updated) for normal processing, got {actions}"
    )


@then("no provenance warning should be generated")
def then_no_provenance_warning(ctx: dict) -> None:
    """Assert no provenance-related warnings in the response."""
    assert "error" not in ctx, f"Expected no provenance warnings, but got error: {ctx.get('error')}"
    resp = require_payload(ctx)
    for r in resp.creatives:
        warnings = r.warnings or []
        provenance_warnings = [w for w in warnings if "provenance" in str(w).lower()]
        assert not provenance_warnings, f"Expected no provenance warnings, got: {provenance_warnings}"


@then("no workflow steps should be created")
def then_no_workflow_steps(ctx: dict) -> None:
    """Assert no workflow steps were created (INV-2: auto-approve)."""
    _assert_success_response(ctx)
    _assert_workflow_steps(ctx["env"], expect_present=False)


@then("no Slack notification should be sent")
def then_no_slack_notification(ctx: dict) -> None:
    """Assert no Slack notification was sent (INV-2/INV-6)."""
    _assert_slack_notified(ctx, [], why="BR-RULE-037 INV-6: Slack requires require-human AND a webhook")


@then("a Slack notification should be sent immediately")
def then_slack_notification_sent(ctx: dict) -> None:
    """Assert Slack notification was sent immediately (INV-3: require-human + webhook configured)."""
    _assert_slack_notified(ctx, [latest_creative_id(ctx)], why="INV-3: require-human + webhook configured")


@then(parsers.parse('a workflow step should be created with type "{step_type}"'))
def then_workflow_step_created_with_type(ctx: dict, step_type: str) -> None:
    """Assert a workflow step was created with the specified type (INV-3)."""
    _assert_success_response(ctx)
    steps = _assert_workflow_steps(ctx["env"], expect_present=True)
    assert any(s.step_type == step_type for s in steps), (
        f"Expected workflow step with type '{step_type}', got types: {[s.step_type for s in steps]}"
    )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / WHEN / THEN steps — BR-RULE-034 cross-principal isolation (s81f)
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('a creative "{creative_id}" exists for principal "{principal_id}" in the tenant'))
def given_creative_exists_for_principal(ctx: dict, creative_id: str, principal_id: str) -> None:
    """Pre-seed a creative in the DB keyed by (tenant_id, principal_id, creative_id).

    Seeds tenant/principal idempotently: the auth Given only mutates the env
    identity (no DB writes — the assumption that it seeds rows was never true;
    surfaced when the dormant BR-RULE-034 scenarios were wired). The named
    principal may differ from the authenticated one (INV-2 seeds the OTHER
    principal's creative), so it gets its own get-or-create row.
    """
    from src.core.database.models import Principal, Tenant
    from tests.factories import CreativeFactory, PrincipalFactory, TenantFactory
    from tests.factories.core import get_or_create

    env = ctx["env"]
    tenant = ctx.get("tenant")
    if tenant is None:
        tenant = get_or_create(
            env,
            Tenant,
            {"tenant_id": env._tenant_id},
            lambda: TenantFactory(tenant_id=env._tenant_id),
        )
        ctx["tenant"] = tenant
    principal = get_or_create(
        env,
        Principal,
        {"principal_id": principal_id, "tenant_id": tenant.tenant_id},
        lambda: PrincipalFactory(tenant=tenant, principal_id=principal_id),
    )
    ctx.setdefault("principal", principal)
    creative = CreativeFactory(
        tenant=tenant,
        principal=principal,
        creative_id=creative_id,
        name=f"Pre-existing creative {creative_id}",
        agent_url=_scenario_format_entry(ctx, env)["agent_url"],
        format=_scenario_format_entry(ctx, env)["id"],
    )
    env._commit_factory_data()
    ctx["pre_existing_creative_id"] = creative_id


@when(parsers.parse('the Buyer Agent syncs creative "{creative_id}"'))
def when_sync_specific_creative(ctx: dict, creative_id: str) -> None:
    """Sync a specific creative by ID (uses the authenticated principal from ctx)."""
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id=creative_id,
        name=f"Synced creative {creative_id}",
        format_id=_creative_format_id_entry(ctx, env),
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    dispatch_request(ctx, creatives=ctx["creatives"])


@then("the existing creative should be updated (matched by triple key)")
def then_existing_creative_updated_by_triple_key(ctx: dict) -> None:
    """Assert the creative was updated (not duplicated) by triple key lookup."""
    from sqlalchemy import select

    from src.core.database.models import Creative

    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected creative update by triple key, but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)

    creative_id = ctx["pre_existing_creative_id"]
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    with _readback(ctx) as session:
        rows = session.scalars(
            select(Creative).filter_by(
                tenant_id=tenant.tenant_id,
                principal_id=principal.principal_id,
                creative_id=creative_id,
            )
        ).all()
        assert len(rows) == 1, (
            f"Expected exactly 1 creative row for triple key "
            f"(tenant={tenant.tenant_id}, principal={principal.principal_id}, creative_id={creative_id}), "
            f"found {len(rows)} — upsert should have matched by triple key, not duplicated"
        )

    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    if results:
        action_str = str(getattr(getattr(results[0], "action", None), "value", getattr(results[0], "action", None)))
        assert action_str == "updated", f"Expected action 'updated' for triple-key match, got '{action_str}'"


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / WHEN / THEN steps — BR-RULE-033 per-creative failure (jn3k)
# ═══════════════════════════════════════════════════════════════════════


@given("two creatives: one valid and one with an empty name")
def given_two_creatives_one_valid_one_empty_name(ctx: dict) -> None:
    """Set up two creative payloads: one valid, one with empty name (triggers per-creative failure)."""
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    format_id, agent_url, assets = _format_payload(ctx, env)
    valid_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-valid-001",
        name="Valid Creative",
        format_id={"id": format_id, "agent_url": agent_url},
        assets=assets,
    )
    # "invalid" to PRODUCTION, not to the pin: an empty name is schema-conformant
    # and fails per-creative validation, which is what makes the pair grade a
    # partial success rather than a whole-request refusal.
    invalid_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-invalid-empty-name",
        name="",
        format_id={"id": format_id, "agent_url": agent_url},
        assets=assets,
    )
    ctx["creatives"] = [valid_payload, invalid_payload]
    ctx["valid_creative_id"] = "creative-valid-001"
    ctx["invalid_creative_id"] = "creative-invalid-empty-name"


@when("the Buyer Agent syncs both creatives")
def when_sync_both_creatives(ctx: dict) -> None:
    """Send sync_creatives with both creative payloads."""
    dispatch_request(ctx, creatives=ctx["creatives"])


def _get_creative_result_by_id(ctx: dict, creative_id: str) -> object | None:
    """Find a SyncCreativeResult by creative_id in the response."""
    resp = payload_or_none(ctx)
    if resp is None:
        return None
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    for r in results:
        if getattr(r, "creative_id", None) == creative_id:
            return r
    return None


@then(parsers.parse('the valid creative should have action "{action}"'))
def then_valid_creative_action(ctx: dict, action: str) -> None:
    """Assert the valid creative has the expected action."""
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected valid creative action '{action}' but dispatch raised {type(error).__name__}: {error}"
    )
    result = _get_creative_result_by_id(ctx, ctx["valid_creative_id"])
    assert result is not None, f"No result found for valid creative {ctx['valid_creative_id']}"
    action_str = str(getattr(getattr(result, "action", None), "value", getattr(result, "action", None)))
    assert action_str == action, f"Expected valid creative action '{action}', got '{action_str}'"


@then(parsers.parse('the invalid creative should have action "{action}"'))
def then_invalid_creative_action(ctx: dict, action: str) -> None:
    """Assert the invalid creative has the expected action."""
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected invalid creative action '{action}' but dispatch raised {type(error).__name__}: {error}"
    )
    result = _get_creative_result_by_id(ctx, ctx["invalid_creative_id"])
    assert result is not None, f"No result found for invalid creative {ctx['invalid_creative_id']}"
    action_str = str(getattr(getattr(result, "action", None), "value", getattr(result, "action", None)))
    assert action_str == action, f"Expected invalid creative action '{action}', got '{action_str}'"


@then("the valid creative should not be affected by the invalid one")
def then_valid_not_affected_by_invalid(ctx: dict) -> None:
    """Assert both results are present — the valid one was not aborted by the invalid one."""
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected per-creative isolation, but dispatch raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert len(results) == 2, f"Expected 2 creative results (one valid, one failed), got {len(results)}"
    valid_result = _get_creative_result_by_id(ctx, ctx["valid_creative_id"])
    assert valid_result is not None, "Valid creative result missing from response"
    action_str = str(getattr(getattr(valid_result, "action", None), "value", getattr(valid_result, "action", None)))
    assert action_str in (
        "created",
        "updated",
    ), f"Valid creative should have succeeded (created/updated), got '{action_str}'"


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / THEN steps — BR-RULE-035 INV-2 adapter format (j9wc)
# ═══════════════════════════════════════════════════════════════════════


@given("a creative with a non-HTTP adapter format_id")
def given_creative_with_adapter_format(ctx: dict) -> None:
    """Set up a creative whose format_id has a non-HTTP agent_url (adapter format)."""
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    format_id = "adapter_display_300x250"
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-adapter-fmt-001",
        name="Adapter Format Creative",
        format_id={"id": format_id, "agent_url": "adapter://local-gam"},
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = format_id


@then("the creative should be processed without external agent validation")
def then_processed_without_external_validation(ctx: dict) -> None:
    """Assert the creative was processed without external agent validation.

    The load-bearing assertion is that the external validation agent mock was
    NOT invoked (call_count == 0). The creative should still be processed
    successfully (action created/updated). If the harness does not wire an
    external validation mock, xfail only that specific check.
    """
    assert "error" not in ctx, (
        f"Expected adapter format to skip external validation, but production raised: {ctx.get('error')}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, "Expected at least one SyncCreativeResult"
    first = results[0]
    action_str = str(getattr(getattr(first, "action", None), "value", getattr(first, "action", None)))
    assert action_str in ("created", "updated", "unchanged"), (
        f"Expected creative processed successfully (created/updated/unchanged), got action='{action_str}'"
    )
    # The external validation IS the creative-agent registry lookup: _validation.py
    # resolves a dialled agent_url through fetch_format_spec -> registry.get_format,
    # and skips that call for an adapter (non-HTTP) agent_url. CreativeSyncEnv patches
    # that registry, so "no external validation" is measurable as "get_format was never
    # awaited". This used to look for validate_creative / external_validation /
    # creative_agent_validate -- names no env has ever wired -- and refused on the
    # missing mock, which parked every scenario carrying the sentence.
    get_format = ctx["env"].mock["registry"].return_value.get_format
    assert get_format.await_count == 0, (
        f"an adapter (non-HTTP) format must not be looked up with the creative agent, "
        f"but registry.get_format was awaited {get_format.await_count} time(s)"
    )


@then('the creative should have action "created" or "updated"')
def then_creative_action_created_or_updated(ctx: dict) -> None:
    """Assert the creative's action is either "created" or "updated"."""
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected action created/updated, but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, "Expected at least one SyncCreativeResult"
    first = results[0]
    action_str = str(getattr(getattr(first, "action", None), "value", getattr(first, "action", None)))
    assert action_str in ("created", "updated"), f"Expected action 'created' or 'updated', got '{action_str}'"


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / THEN steps — BR-RULE-039 INV-2 format match (hlmr)
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('the assignment should fail with "{error_code}"'))
def then_assignment_should_fail_with(ctx: dict, error_code: str) -> None:
    """Assert the assignment failed with the specified error code.

    Hard-asserts production actually failed and the failure carries the expected
    code. For AdCPSalesAgentError, checks error_code directly. For FORMAT_MISMATCH,
    also accepts 'not supported' in the error message (production's phrasing).
    """

    error = ctx.get("error")
    assert error is not None, (
        f"Expected assignment failure with {error_code}, but production succeeded. Response: {payload_or_none(ctx)}"
    )
    # The message-based escape hatch for FORMAT_MISMATCH is gone. It existed because
    # "production may use different code names", and it matched on
    # "not supported" in error.message -- a substring the derived sentence can no
    # longer contain, so it had become a dead branch that
    # merely looked lenient. Production now emits a published code for this outcome
    # (CREATIVE_REJECTED, _assignments.py:236), so the scenario names it directly.
    result = ctx.get("result")
    wire_code = result.wire_error_code() if result is not None else None
    if wire_code is not None:
        assert wire_code == error_code, f"Expected wire error code {error_code!r}, got {wire_code!r}"
    else:
        # Non-AdCPSalesAgentError: assert the error string contains the expected code
        assert error_code.lower() in str(error).lower(), (
            f"Expected error containing '{error_code}', got {type(error).__name__}: {error}"
        )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / THEN steps — BR-RULE-033 INV-3 lenient mode (yw4j)
# ═══════════════════════════════════════════════════════════════════════


@given("assignments to two packages: one valid and one non-existent")
def given_assignments_two_packages_one_valid_one_missing(ctx: dict) -> None:
    """Create two package assignments: one valid, one non-existent."""
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]

    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    product = ProductFactory(tenant=tenant, format_ids=[_product_format_entry(ctx, env)])
    valid_package = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["valid_package"] = valid_package
    ctx["nonexistent_package_id"] = "pkg-nonexistent-two-mix-404"
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {creative_id: [valid_package.package_id, "pkg-nonexistent-two-mix-404"]}


@then("the valid assignment should be created")
def then_valid_assignment_created(ctx: dict) -> None:
    """Assert the valid package assignment was created despite the non-existent one."""
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: lenient mode should continue despite invalid assignment, but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, (
        "SPEC-PRODUCTION GAP: expected creative results with assignment info, but response has no creatives/results."
    )
    assigned = results[0].assigned_to or []
    valid_pkg = ctx["valid_package"].package_id
    assert valid_pkg in assigned, (
        f"SPEC-PRODUCTION GAP: lenient mode should create valid assignment to {valid_pkg}, but assigned_to={assigned}"
    )
    # The claim, stated on the path that RETURNS. The xfail above records the
    # known gap; without this the satisfied path returned having graded nothing.
    assert valid_pkg in assigned, f"expected assignment to {valid_pkg}, got assigned_to={assigned}"


@then("the non-existent package should be reported as a warning")
def then_nonexistent_package_reported_as_warning(ctx: dict) -> None:
    """Assert the non-existent package is reported in assignment_errors or warnings.

    Lenient mode must surface the non-existent package to the buyer (POST-S1/S2).
    The error-raised path is the only legitimate xfail (lenient mode raising is a
    genuine spec-vs-production gap). Missing results or missing warning is a hard
    failure.
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: lenient mode should warn about non-existent package, but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, "Lenient mode must return per-creative results (POST-S1/S2)"
    first = results[0]
    assignment_errors = getattr(first, "assignment_errors", None) or {}
    warnings_list = getattr(first, "warnings", None) or []
    bad_pkg = ctx["nonexistent_package_id"]
    found = any(
        bad_pkg in str(e)
        for e in (assignment_errors.values() if isinstance(assignment_errors, dict) else assignment_errors)
    ) or any(bad_pkg in str(w) for w in warnings_list)
    assert found, (
        f"Non-existent package '{bad_pkg}' must be reported in assignment_errors "
        f"or warnings, but assignment_errors={assignment_errors}, "
        f"warnings={warnings_list}"
    )


@then("processing should continue normally")
def then_processing_continues_normally(ctx: dict) -> None:
    """Assert the overall sync completed successfully in lenient mode.

    In lenient mode, a raised error means the sync aborted instead of
    continuing -- that is the failure mode this scenario catches.
    Verifies the response contains at least one creative result with
    a success outcome (created/updated), confirming the sync completed.
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: lenient mode should continue normally, but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, "Expected at least one creative result from a completed sync"
    actions = [str(getattr(getattr(r, "action", None), "value", getattr(r, "action", None))) for r in results]
    assert any(a in ("created", "updated") for a in actions), (
        f"Expected a success action (created/updated) confirming sync completed, got {actions}"
    )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / THEN steps — BR-RULE-033 INV-3 non-draft status (1eja)
#   (Given step 'a media buy with status "{status}" (non-draft)' already exists at line ~1848)
# ═══════════════════════════════════════════════════════════════════════

# Steps already exist:
#   - given_media_buy_non_draft (line ~1848)
#   - given_assignments_to_package_in_that_media_buy (line ~1854)
#   - when_sync_creative_with_assignments (line ~1922)
#   - then_media_buy_status_should_remain (line ~1970)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / THEN steps — idempotency key boundary (llcj)
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.re(r'idempotency_key is (?:"(?P<key_value>[^"]*)"|(?P<empty>))\s*$'))
def given_idempotency_key(ctx: dict, key_value: str | None, empty: str | None) -> None:
    """Set the idempotency_key on the sync_creatives request.

    Handles: absent (empty match), empty string (""), and quoted strings.
    Some values use ]xN notation for length generation (e.g., "a]x254").

    ABSENT MEANS ABSENT ON THE WIRE. This used to return without touching ctx, and
    the harness then supplied its default key -- so the "absent" row sent a valid key
    and graded nothing about absence. The sentinel makes the harness DROP the field;
    the pin lists idempotency_key in sync-creatives-request.json /required, so what
    the row grades is the schema rejection.
    """
    if key_value is None and empty is not None:
        ctx["idempotency_key"] = OMIT_IDEMPOTENCY_KEY
        return

    actual_value = key_value or ""
    actual_value = _expand_length_notation(actual_value)
    ctx["idempotency_key"] = actual_value


def _expand_length_notation(value: str) -> str:
    """Expand ]xN notation: 'a]x254' -> 'a' repeated to 254 chars."""
    import re

    match = re.match(r"^(.)]x(\d+)$", value)
    if match:
        char = match.group(1)
        length = int(match.group(2))
        return char * length
    return value


@then("the request should proceed normally")
def then_request_proceed_normally(ctx: dict) -> None:
    """Assert the request completed successfully with no error raised.

    Verifies the response is a valid successful result (has products or
    creatives/results), confirming the request was not rejected.
    """
    error = ctx.get("error")
    assert error is None, f"Expected request to proceed normally, but production raised {type(error).__name__}: {error}"
    resp = require_payload(ctx)
    # Confirm the response is a successful result, not an error envelope
    has_products = getattr(resp, "products", None) is not None
    has_creatives = getattr(resp, "creatives", None) is not None or getattr(resp, "results", None) is not None
    assert has_products or has_creatives, (
        f"Expected a successful response with products or creatives, got {type(resp).__name__}"
    )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / THEN steps — generative creative / Gemini key missing (wvl5)
# ═══════════════════════════════════════════════════════════════════════


@given("a creative with a generative format (output_format_ids present)")
def given_creative_with_generative_format(ctx: dict) -> None:
    """Set up a creative with a generative format (output_format_ids populated)."""
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    fmt = env.setup_generative_build(format_id="display_gen", gemini_api_key="test-gemini-key")
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-generative-001",
        name="Generative Creative",
        format_id=fmt,
        assets=build_assets(text_spec("message", content="Generate a banner ad for summer sale")),
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = fmt["id"]


@given("the Seller Agent does not have GEMINI_API_KEY configured")
def given_no_gemini_api_key(ctx: dict) -> None:
    """Remove GEMINI_API_KEY from the config mock."""
    ctx["env"].set_gemini_api_key(None)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / THEN steps — format validation partition (wcwr)
# ═══════════════════════════════════════════════════════════════════════


@given("a creative with a known HTTP-based format_id")
@given("a creative with a known HTTP-registered format_id")
def given_creative_with_known_http_format(ctx: dict) -> None:
    """A creative whose format the registry SERVES, over a dialled HTTP agent_url.

    The sentence names two properties and this step now arranges both instead of
    inheriting them. KNOWN: ``configure_agent_served_creative`` puts the format in
    the listing ``_processing.py`` searches, so ``find_format`` resolves it and
    production enters the agent-backed branch that T-UC-006-rule-035-static grades
    ("validated by the creative agent", "preview URLs should be generated").
    Delegating to the plain known-format Given did NOT arrange that: the harness
    default leaves ``run_async`` returning ``[]``, ``find_format`` returns None, and
    the agent-backed branch is skipped entirely — so the scenario asserted a preview
    that production had no path to produce.

    HTTP-BASED: asserted rather than assumed, because it is the discriminator
    against this outline's sibling row ``a creative with an adapter (non-HTTP)
    format_id``. Production splits on ``is_dialled_agent_url`` (_validation.py:124),
    so the same predicate decides it here — an ``adapter://`` DEFAULT_AGENT_URL would
    silently turn this row into a duplicate of its sibling.
    """
    from src.core.format_resolver import is_dialled_agent_url

    env = ctx["env"]
    fmt = env.configure_agent_served_creative(generative=False, format_id="display_300x250")
    assert is_dialled_agent_url(fmt["agent_url"]), (
        f"this sentence promises an HTTP-based format, but the agent_url is {fmt['agent_url']!r}, "
        "which production classifies as an adapter format and exempts from external validation"
    )
    given_creative_with_format(ctx)


@given("a creative with no format_id")
def given_creative_with_no_format_id(ctx: dict) -> None:
    """Set up a creative payload with format_id omitted.

    BOUND, despite grepping like dead code. No feature file contains this step's
    literal text; it is reached through Examples substitution, from
    ``BR-UC-006-sync-creatives.feature``'s "Format validation — <partition>" outline
    (``And a creative with <format_setup>``, row ``missing_format_id`` whose
    ``format_setup`` is ``no format_id``). That renders to exactly this step name and
    resolves here — confirmed against pytest-bdd's own registered parsers, not by
    grep. It grades three nodeids, ``test_format_validation__partition``
    ``[a2a|mcp|rest-missing_format_id-...]``, all xfail-dormant at SETUP on
    "UC-006 harness not yet wired for non-account scenarios" — so deleting this step
    would leave every count unchanged while removing the binding.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    creative_payload = malformed(
        "absent_key",
        "no 'format_id' key at all — the partition's subject is a request that never names a "
        "format, so the key must be absent on the wire rather than null. CreativeAssetRequest "
        "rejects it with [type=oneOf] provide exactly one of format_id or format_kind, the same "
        "message an explicit None produces, which is why absent_key is declared rather than "
        "inferred.",
        CreativeAssetRequestFactory.payload(
            creative_id="creative-no-fmt-001",
            name="Creative Without Format",
            # OMIT, not None: the key must be ABSENT. Drop this line and the factory
            # default puts a valid format_id back — reported now, not silent.
            format_id=OMIT,
        ),
        obligation=ErrorCode.INVALID_REQUEST,
    )
    ctx.setdefault("creatives", []).append(creative_payload)


#: vast-tracker-asset.json / daast-tracker-asset.json ``not: {enum: [...]}`` -- the events a
#: tracker asset refuses because they belong to another VAST/DAAST element.
_NON_TRACKING_EVENTS = frozenset(
    {
        "impression",
        "clickTracking",
        "customClick",
        "error",
        "viewable",
        "notViewable",
        "viewUndetermined",
        "measurableImpression",
        "viewableImpression",
    }
)

_TRACKER_PHRASE = re.compile(
    r'a (?P<kind>VAST|DAAST) tracker for "(?P<event>\w+)"'
    r'(?: at offset "(?P<offset>[^"]+)")?(?P<no_offset> without an offset)?(?: targeting "(?P<target>\w+)")?$'
)


@given(parsers.parse("a creative with a known format_id whose assets carry {tracker_assets}"))
def given_creative_with_tracker_assets(ctx: dict, tracker_assets: str) -> None:
    """A creative on the transport's served format whose assets carry decomposed trackers.

    ``tracker_assets`` is one or more ``a VAST tracker for "<event>"`` phrases joined by
    " and ", each optionally ``at offset "<offset>"``, ``without an offset`` or
    ``targeting "<target>"`` -- the fields core/assets/vast-tracker-asset.json and
    daast-tracker-asset.json define. The specs are kept for the persisted-assets Then.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    format_id, agent_url, assets = _format_payload(ctx, env)
    specs = []
    refusals: list[str] = []
    for phrase in tracker_assets.split(" and "):
        match = _TRACKER_PHRASE.fullmatch(phrase.strip())
        assert match, f"unrecognised tracker phrase {phrase!r}"
        kind, event = match["kind"].lower(), match["event"]
        fields: dict[str, Any] = {
            f"{kind}_event": event,
            "url": f"https://tracking.example.com/{kind}/{event}",
        }
        if match["offset"]:
            fields["offset"] = match["offset"]
        if match["target"]:
            fields["target"] = match["target"]
        specs.append(asset_spec(f"{kind}_{event}_tracker", f"{kind}_tracker", **fields))
        # The pin's refusals, named so the payload can DECLARE itself malformed: the
        # non-TrackingEvents events, a progress tracker with no offset, a DAAST target
        # outside {linear, companion}.
        if event in _NON_TRACKING_EVENTS:
            refusals.append(f"{kind}_event {event!r} belongs to another {kind.upper()} element")
        if event == "progress" and not match["offset"]:
            refusals.append("offset is required when the event is progress")
        if match["target"] and match["target"] not in ("linear", "companion"):
            refusals.append(f"DAAST target {match['target']!r} is not linear or companion")
    ctx["tracker_specs"] = specs
    creative_payload: dict[str, Any] = CreativeAssetRequestFactory.payload(
        creative_id="creative-trackers-001",
        name="Creative With Trackers",
        format_id={"id": format_id, "agent_url": agent_url},
        assets={**assets, **build_assets(*specs)},
    )
    if refusals:
        # The reason is a literal so the declaration stays auditable; which rule the row
        # breaks is the row's own phrase.
        creative_payload = malformed(
            "semantic",
            "the tracker asset is shaped correctly but its VALUE breaks a rule the pin states on "
            "vast-tracker-asset.json / daast-tracker-asset.json: a non-TrackingEvents event, a "
            "progress tracker without offset, or a DAAST target outside {linear, companion}",
            creative_payload,
            obligation=ErrorCode.INVALID_REQUEST,
        )
    ctx.setdefault("creatives", []).append(creative_payload)


@then("the creative should be created with its tracker assets stored")
def then_creative_created_with_trackers_stored(ctx: dict) -> None:
    """The entry reports ``created`` and the library row keeps every tracker as sent."""
    _assert_success_response(ctx)
    entry = _wire_creatives_entry(ctx, latest_creative_id(ctx))
    assert entry.get("action") == "created", f"Expected action 'created', got {entry.get('action')!r}"
    # The row is the only observable: sync-creatives-response.json's per-creative
    # entry carries no assets, so "stored as sent" is a persistence obligation.
    assert_assets(_stored_assets_for_last_creative(ctx), *ctx["tracker_specs"])


@given("a creative with a known format_id whose agent returns no preview and that carries no media url")
def given_creative_no_preview_no_media_url(ctx: dict) -> None:
    """A text-only creative on a format the agent serves, answered with zero previews.

    The format must be one the agent SERVES (``configure_agent_served_creative``): a
    format absent from the agent's catalogue never reaches the preview step at all. The
    agent then answers the preview with nothing, and with no image or video asset there
    is no media_url to fall back on -- which production classifies as CREATIVE_REJECTED
    carrying ``reasons`` (src/core/tools/creatives/_processing.py). The env method
    declares its own e2e unrealizability: a live agent cannot be told to answer nothing.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    fmt = env.configure_agent_no_preview(format_id=_scenario_format_id(ctx, env))
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-no-preview-001",
        name="Nothing Renderable",
        format_id={"id": fmt["id"], "agent_url": fmt["agent_url"]},
        assets=build_assets(text_spec("headline", content="Nothing renderable here")),
    )
    ctx.setdefault("creatives", []).append(creative_payload)


@given("a creative with a format_id unknown to all agents")
@given("a creative with an unknown format_id")
def given_creative_with_format_unknown_to_all(ctx: dict) -> None:
    """Set up a creative whose format_id is not registered with any agent."""
    given_creative_with_unknown_format(ctx)


@given("a creative with a format_id whose agent is unreachable")
def given_creative_with_unreachable_agent_format(ctx: dict) -> None:
    """Set up a creative whose format agent returns a connection error."""
    given_creative_with_unreachable_agent(ctx)


# Format validation partition outcomes are handled by the existing
# then_uc006_result_should_be step which dispatches on outcome string.


# ═══════════════════════════════════════════════════════════════════════
# Helpers — generative build assertions (thm4)
# ═══════════════════════════════════════════════════════════════════════


def _assert_standard_processing(ctx: dict) -> None:
    """Assert creative was processed as static (no generative build invoked).

    Production returns action=created/updated. The registry.build_creative
    mock must NOT have been called.
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected 'standard processing' but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    actions = [str(getattr(getattr(r, "action", None), "value", getattr(r, "action", None))) for r in results]
    assert any(a in ("created", "updated", "unchanged") for a in actions), (
        f"Expected created/updated/unchanged for static processing, got {actions}"
    )
    # Verify generative build was NOT invoked
    env = ctx["env"]
    registry = env.mock["registry"].return_value
    if hasattr(registry.build_creative, "called"):
        assert not registry.build_creative.called, (
            "build_creative should NOT be called for static (non-generative) creatives"
        )


def _assert_generative_build(ctx: dict, prompt_source: str) -> None:
    """Assert generative build was invoked with the expected prompt source.

    Args:
        prompt_source: "assets" (prompt from message asset) or
                       "name_fallback" (prompt derived from creative name).
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected 'generative build' but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    actions = [str(getattr(getattr(r, "action", None), "value", getattr(r, "action", None))) for r in results]
    assert any(a in ("created", "updated") for a in actions), (
        f"Expected created/updated for generative build, got {actions}"
    )
    # Verify build_creative WAS invoked
    env = ctx["env"]
    registry = env.mock["registry"].return_value
    assert registry.build_creative.called, "build_creative should have been called for generative format"
    # Verify prompt content
    call_kwargs = registry.build_creative.call_args
    message_arg = call_kwargs.kwargs.get("message") or (call_kwargs.args[2] if len(call_kwargs.args) > 2 else None)
    if message_arg is None:
        # Try positional or named kwarg patterns
        for kw_name in ("message", "prompt"):
            message_arg = call_kwargs.kwargs.get(kw_name)
            if message_arg:
                break
    assert message_arg is not None, "build_creative must be called with a message/prompt"
    if prompt_source == "assets":
        assert "Create a creative for:" not in message_arg, (
            f"Expected prompt from assets, but got name fallback: {message_arg!r}"
        )
    elif prompt_source == "name_fallback":
        assert "Create a creative for:" in message_arg, (
            f"Expected name fallback prompt ('Create a creative for: ...'), got: {message_arg!r}"
        )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — generative build partition (thm4)
# ═══════════════════════════════════════════════════════════════════════


@given("a creative with no output_format_ids")
@given("a creative with a static format (no output_format_ids)")
def given_creative_no_output_format_ids(ctx: dict) -> None:
    """A creative whose format is REGISTERED and carries an empty output_format_ids.

    ``configure_agent_served_creative(generative=False, ...)`` is the single place
    that decides what "static" means — it sets ``output_format_ids = []``, which is
    exactly what ``format_resolver.is_generative`` reads — so this step states the
    precondition instead of hoping for it.

    It previously said it "uses the default registry mock which returns a format
    without output_format_ids". That was not what happened. The default leaves
    ``run_async`` returning ``[]``, so ``find_format`` resolves NOTHING and
    ``_processing.py:329`` never reaches the generative/static split at all. The row
    "static creative (no output_format_ids) -> processed without generative build"
    was therefore passing on a request that had no format to classify, which is a
    weaker fact than the one it claims and is true of an unknown format too.
    """
    ctx["env"].configure_agent_served_creative(generative=False, format_id="display_300x250")
    given_creative_with_format(ctx)


@given("a creative with output_format_ids present")
def given_creative_output_format_ids_present(ctx: dict) -> None:
    """Set up a creative with a generative format (output_format_ids populated).

    Delegates to the existing generative format setup but does NOT add
    assets — prompt source is controlled by the next Given step.

    ``assets=OMIT`` states that absence rather than leaving it to happen: the
    factory HAS a conformant assets default, so dropping the line would put an
    image asset in a creative whose whole point is that its prompt source is
    chosen later. NOT declared ``malformed``, and that is measured rather than
    assumed — the pin does reject an absent ``assets``, but every scenario using
    this sentence follows it with a step that writes the key
    (``message asset with prompt text``, ``no prompt assets or inputs``,
    ``message asset but no GEMINI_API_KEY``; feature lines 894-896), so the
    DISPATCHED item is conformant. ``malformed(..., pin_rejects=True)`` here would
    be graded against the dispatched bytes and reported as REPAIRED.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    fmt = env.setup_generative_build(format_id="display_gen", gemini_api_key="test-gemini-key")
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-generative-part-001",
        name="Generative Partition Creative",
        format_id=fmt,
        assets=OMIT,
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = fmt["id"]


@given("a creative with output_format_ids present (create)")
def given_creative_output_format_ids_present_create(ctx: dict) -> None:
    """Set up a NEW generative creative (create path, not update).

    Same as output_format_ids present but explicitly a new creative_id
    so production takes the create path where name fallback applies.

    ``assets=OMIT`` for the reason given on ``given_creative_output_format_ids_present``:
    the absence is deliberate and the next Given writes the key, so the dispatched
    item is conformant and no malformation is declared.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    fmt = env.setup_generative_build(format_id="display_gen", gemini_api_key="test-gemini-key")
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-generative-create-001",
        name="My Summer Campaign Banner",
        format_id=fmt,
        assets=OMIT,
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = fmt["id"]


@given("any assets")
def given_any_assets(ctx: dict) -> None:
    """Add generic image assets to the last creative in the list.

    For static creatives, any assets suffice — this step just ensures
    the creative payload has an assets dict.
    """
    creatives = ctx.get("creatives", [])
    assert creatives, "No creative in context to add assets to"
    last_creative = creatives[-1]
    last_creative.setdefault("assets", {}).update(build_assets(image_spec("image")))


@given("message asset with prompt text")
def given_message_asset_with_prompt(ctx: dict) -> None:
    """Add a message asset with prompt text to the last creative.

    Production extracts prompt from assets with role "message", "brief",
    or "prompt" (BR-RULE-036 INV-2).
    """
    creatives = ctx.get("creatives", [])
    assert creatives, "No creative in context to add message asset to"
    last_creative = creatives[-1]
    last_creative.setdefault("assets", {}).update(
        build_assets(text_spec("message", content="Generate a banner ad for summer sale"))
    )


@given("no prompt assets or inputs")
def given_no_prompt_assets_or_inputs(ctx: dict) -> None:
    """Ensure the last creative has NO prompt-bearing assets or inputs.

    Removes any message/brief/prompt assets so the create path falls
    through to name fallback (BR-RULE-036 INV-4).
    """
    creatives = ctx.get("creatives", [])
    assert creatives, "No creative in context to strip prompt from"
    last_creative = creatives[-1]
    assets = last_creative.get("assets", {})
    for role in ("message", "brief", "prompt"):
        assets.pop(role, None)
    last_creative["assets"] = assets
    last_creative.pop("inputs", None)


@given("message asset but no GEMINI_API_KEY")
def given_message_asset_no_gemini_key(ctx: dict) -> None:
    """Add a message asset but remove the GEMINI_API_KEY from config.

    Production checks gemini_api_key early in the generative path and
    raises ValueError when missing (BR-RULE-036, INV formerly-2).
    """
    creatives = ctx.get("creatives", [])
    assert creatives, "No creative in context to add message asset to"
    last_creative = creatives[-1]
    last_creative.setdefault("assets", {}).update(
        build_assets(text_spec("message", content="Generate a banner ad for summer sale"))
    )
    ctx["env"].set_gemini_api_key(None)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — format validation boundary (thm4)
#
# Several boundary steps reuse the same text as partition steps above
# (e.g., "a creative with a format_id whose agent is unreachable").
# These are NOT duplicated here — pytest-bdd matches the existing
# step definition.
#
# New boundary-only step texts that differ from partition equivalents:
# ═══════════════════════════════════════════════════════════════════════


@given("a creative with an adapter (non-HTTP) format_id")
def given_creative_adapter_non_http_format(ctx: dict) -> None:
    """Set up a creative with a non-HTTP adapter format_id (boundary)."""
    given_creative_with_adapter_format(ctx)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — generative build boundary (thm4)
# ═══════════════════════════════════════════════════════════════════════


@given("a creative with a generative format and prompt in assets")
def given_creative_generative_with_prompt(ctx: dict) -> None:
    """Set up a generative creative with a message asset containing prompt text (boundary)."""
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    fmt = env.setup_generative_build(format_id="display_gen", gemini_api_key="test-gemini-key")
    asset_prompt = "Design a responsive ad for holiday promotion"
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-gen-prompt-001",
        name="Generative With Prompt",
        format_id=fmt,
        assets=build_assets(text_spec("message", content=asset_prompt)),
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = fmt["id"]
    ctx["expected_asset_prompt"] = asset_prompt


@given("a new creative with a generative format and no prompt but a name")
def given_new_creative_generative_no_prompt_with_name(ctx: dict) -> None:
    """Set up a NEW generative creative with a name but no prompt assets (boundary).

    On the create path, production falls back to 'Create a creative for: {name}'
    (BR-RULE-036 INV-4).
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    fmt = env.setup_generative_build(format_id="display_gen", gemini_api_key="test-gemini-key")
    # assets={}, not OMIT: an EMPTY assets object is what the scenario means by "no
    # prompt", and the pin accepts it — so this is a conformant payload whose
    # wrongness is downstream, not a malformation.
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-gen-name-fallback-001",
        name="Summer Sale Banner",
        format_id=fmt,
        assets={},
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = fmt["id"]


@given("a creative with a generative format but GEMINI_API_KEY not configured")
def given_creative_generative_no_gemini(ctx: dict) -> None:
    """Set up a generative creative but with GEMINI_API_KEY removed (boundary).

    Production raises ValueError when gemini_api_key is not configured
    for a generative format.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    # Generative format, and the key NOT configured. Said once, as an argument, rather
    # than set and then unset: the two-step form left the env briefly in a state no
    # scenario describes, and the second step had to know the config mock's shape.
    fmt = env.setup_generative_build(format_id="display_gen", gemini_api_key=None)
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-gen-no-key-001",
        name="Generative No Key",
        format_id=fmt,
        assets=build_assets(text_spec("message", content="Generate a banner ad")),
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = fmt["id"]


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — format validation boundary (thm4)
# ═══════════════════════════════════════════════════════════════════════


@then("the creative should skip external format validation")
def then_skip_external_format_validation(ctx: dict) -> None:
    """Assert adapter (non-HTTP) format skipped external agent validation (boundary).

    Delegates to the existing assertion for adapter format processing.
    """
    then_processed_without_external_validation(ctx)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — generative build boundary (thm4)
# ═══════════════════════════════════════════════════════════════════════


@then("the creative should be processed without generative build")
def then_processed_without_generative_build(ctx: dict) -> None:
    """Assert the creative was processed as static — no generative build invoked."""
    _assert_standard_processing(ctx)


@then("the generative build should use the message asset as the prompt")
def then_generative_build_uses_message_asset(ctx: dict) -> None:
    """The build ran, and its prompt came from the request's message asset.

    The outline row that says this does not record the prompt text the way the
    INV-2 scenario's Given does, so it grades the prompt's SOURCE rather than its
    exact value; the exact-value form is the sentence below.
    """
    _assert_generative_build(ctx, prompt_source="assets")


@then("the system should invoke generative build with the asset prompt")
def then_invoke_generative_with_asset_prompt(ctx: dict) -> None:
    """Assert generative build was invoked using the exact prompt from assets."""
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected 'generative build' but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    actions = [str(getattr(getattr(r, "action", None), "value", getattr(r, "action", None))) for r in results]
    assert any(a in ("created", "updated") for a in actions), (
        f"Expected created/updated for generative build, got {actions}"
    )
    env = ctx["env"]
    registry = env.mock["registry"].return_value
    assert registry.build_creative.called, "build_creative should have been called for generative format"
    # Positive assertion: the message arg matches the exact asset prompt from Given
    call_kwargs = registry.build_creative.call_args
    message_arg = call_kwargs.kwargs.get("message") or (call_kwargs.args[2] if len(call_kwargs.args) > 2 else None)
    if message_arg is None:
        for kw_name in ("prompt",):
            message_arg = call_kwargs.kwargs.get(kw_name)
            if message_arg:
                break
    assert message_arg is not None, "build_creative must be called with a message/prompt"
    expected_prompt = ctx["expected_asset_prompt"]
    assert expected_prompt in message_arg, (
        f"Expected asset prompt {expected_prompt!r} in build_creative message, got {message_arg!r}"
    )


@then("the system should use the creative name as prompt fallback")
def then_use_creative_name_as_prompt_fallback(ctx: dict) -> None:
    """Assert generative build was invoked using the creative name as fallback."""
    _assert_generative_build(ctx, prompt_source="name_fallback")


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — BR-RULE-036 invariant scenarios (3vp2)
# ═══════════════════════════════════════════════════════════════════════


@given("a creative with a format that has output_format_ids defined")
def given_creative_format_with_output_format_ids(ctx: dict) -> None:
    """Set up a creative with a generative format (output_format_ids populated).

    INV-1: format_obj.output_format_ids is truthy -> creative classified as generative.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    fmt = env.setup_generative_build(format_id="display_gen", gemini_api_key="test-gemini-key")
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-gen-inv1-001",
        name="Generative Detection Test",
        format_id=fmt,
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = fmt["id"]


@given("GEMINI_API_KEY is configured")
def given_gemini_api_key_configured(ctx: dict) -> None:
    """Ensure the GEMINI_API_KEY is set on the config mock.

    If setup_generative_build was already called, the key is already set.
    This step acts as an explicit guard / documentation step.
    """
    ctx["env"].set_gemini_api_key("test-gemini-key")


@given(parsers.parse('a generative creative with an asset of role "{role}" containing "{content}"'))
def given_generative_creative_with_asset_role(ctx: dict, role: str, content: str) -> None:
    """Set up a generative creative with a specific asset role containing prompt text.

    INV-2: prompt found in assets (message/brief/prompt role) -> that text used as build prompt.

    The asset goes through ``text_spec`` rather than being written as
    ``{role: {"content": content}}``: that hand-built form carries no
    ``asset_type`` discriminator, and ``CreativeAssetRequest`` rejects it with
    ``assets.<role>.AssetVariant Unable to extract tag using discriminator
    'asset_type' [type=union_tag_not_found]``. The scenario's subject is WHICH
    text becomes the build prompt, never the missing discriminator, so this is a
    defect fixed rather than a malformation declared (GH #1391).
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    fmt = env.setup_generative_build(format_id="display_gen", gemini_api_key="test-gemini-key")
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-gen-inv2-001",
        name="Generative Prompt From Assets",
        format_id=fmt,
        assets=build_assets(text_spec(role, content=content)),
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = fmt["id"]


@given(
    parsers.re(
        r'a generative creative with no prompt assets but inputs\[0\]\.context_description = "(?P<description>[^"]+)"'
    )
)
def given_generative_creative_with_context_description(ctx: dict, description: str) -> None:
    """Set up a generative creative with context_description in inputs (no prompt assets).

    INV-3: no prompt in assets, but inputs[0].context_description exists
    -> context_description used as build prompt.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    fmt = env.setup_generative_build(format_id="display_gen", gemini_api_key="test-gemini-key")
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-gen-inv3-001",
        name="Generative Context Description",
        format_id=fmt,
        assets={},
        # inputs is a declared CreativeAssetRequest field, so this ADDS a real key
        # rather than smuggling one past extra="forbid".
        inputs=[{"name": "default", "context_description": description}],
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = fmt["id"]


@given(parsers.parse('a generative creative named "{name}" with no prompt assets or inputs'))
def given_generative_creative_named_no_prompt(ctx: dict, name: str) -> None:
    """Set up a NEW generative creative with a name but no prompt sources.

    INV-4: no prompt in assets or inputs (create) -> creative name used as
    fallback: "Create a creative for: {name}".
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    fmt = env.setup_generative_build(format_id="display_gen", gemini_api_key="test-gemini-key")
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-gen-inv4-001",
        name=name,
        format_id=fmt,
        assets={},
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = fmt["id"]


@given("a generative creative that already exists with generated content")
def given_generative_creative_exists_with_content(ctx: dict) -> None:
    """Pre-seed a generative creative in the DB with existing generated content.

    INV-5: sets up a creative that already has generative_build_result,
    generative_status, and generative_context_id in its data field.
    The update step will then modify this creative without a prompt.
    """
    from tests.factories import CreativeFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    fmt = env.setup_generative_build(format_id="display_gen", gemini_api_key="test-gemini-key")

    creative_id = "creative-gen-inv5-001"
    existing_data = {
        "generative_build_result": {
            "status": "draft",
            "context_id": "ctx-existing-001",
            "creative_output": {
                "assets": {"headline": {"text": "Previously generated headline"}},
                "output_format": {"url": "https://generated.example.com/existing.html"},
            },
        },
        "generative_status": "draft",
        "generative_context_id": "ctx-existing-001",
        "output_format": {"url": "https://generated.example.com/existing.html"},
    }
    CreativeFactory(
        tenant=tenant,
        principal=principal,
        creative_id=creative_id,
        name="Existing Generative Creative",
        agent_url=env.DEFAULT_AGENT_URL,
        format="display_gen",
        data=existing_data,
    )
    env._commit_factory_data()

    # Prepare the update payload (no prompt assets or inputs yet — added by next step).
    # assets=OMIT states the absence; "the update has no prompt assets or inputs"
    # (feature line 489) then writes assets={}, which the pin accepts, so the
    # dispatched item is conformant and takes no malformation declaration.
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id=creative_id,
        name="Existing Generative Creative",
        format_id=fmt,
        assets=OMIT,
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = fmt["id"]
    ctx["existing_generative_data"] = existing_data


@given("a generative creative with both user-provided assets and generative prompt")
def given_generative_creative_with_user_assets_and_prompt(ctx: dict) -> None:
    """Set up a generative creative with both user assets and a prompt message.

    INV-6: user-provided assets present alongside generative output ->
    user assets take priority over generative output.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    fmt = env.setup_generative_build(format_id="display_gen", gemini_api_key="test-gemini-key")
    user_image = image_spec("image", url="https://example.com/user-banner.png")
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-gen-inv6-001",
        name="Generative With User Assets",
        format_id=fmt,
        assets=build_assets(text_spec("message", content="Generate a responsive ad"), user_image),
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = fmt["id"]
    # The same spec(s) used to build the mock are stored for verification (assert_assets).
    ctx["user_provided_assets"] = [user_image]


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — BR-RULE-036 invariant scenarios (3vp2)
# ═══════════════════════════════════════════════════════════════════════


@when("the Buyer Agent creates the creative")
def when_buyer_creates_creative(ctx: dict) -> None:
    """Send sync_creatives for a NEW creative (create path).

    Delegates to the standard sync dispatch — the create/update distinction
    is determined by whether the creative_id exists in the DB.
    """
    when_sync_creative(ctx)


@when("the Buyer Agent updates the creative")
def when_buyer_updates_creative(ctx: dict) -> None:
    """Send sync_creatives for an EXISTING creative (update path).

    Delegates to the standard sync dispatch — the creative was pre-seeded
    by a prior Given step, so production takes the update path.
    """
    when_sync_creative(ctx)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — BR-RULE-036 invariant scenarios (3vp2)
# ═══════════════════════════════════════════════════════════════════════


@then("the creative should be processed as generative")
def then_processed_as_generative(ctx: dict) -> None:
    """Assert the creative was classified as generative and build_creative was called.

    INV-1: format_obj.output_format_ids is truthy -> creative classified as generative.
    """
    error = ctx.get("error")
    assert error is None, f"SPEC-PRODUCTION GAP: expected generative processing but got {type(error).__name__}: {error}"
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    actions = [str(getattr(getattr(r, "action", None), "value", getattr(r, "action", None))) for r in results]
    assert any(a in ("created", "updated") for a in actions), (
        f"Expected created/updated for generative processing, got {actions}"
    )
    # Verify build_creative WAS invoked (generative detection)
    env = ctx["env"]
    registry = env.mock["registry"].return_value
    assert registry.build_creative.called, (
        "build_creative should have been called for generative format (output_format_ids present)"
    )


@then("the creative should have generated content")
def then_creative_has_generated_content(ctx: dict) -> None:
    """Assert the creative response contains generated content from the build.

    INV-1: verifies the generative build result was stored in the DB.
    """
    error = ctx.get("error")
    assert error is None, f"SPEC-PRODUCTION GAP: expected generated content but got {type(error).__name__}: {error}"

    # Verify via DB: read the creative back and check for generative data
    env = ctx["env"]
    session = env.get_session()
    # A missing DB session is a HARNESS defect, not a production gap: production's behaviour cannot influence whether the env opened one. Excusing it as an expected failure meant the persistence claim silently graded nothing.
    assert session is not None, "no DB session, so the generated-content claim cannot be verified against storage"

    from sqlalchemy import select

    from src.core.database.models import Creative as CreativeModel

    creative_id = latest_creative_id(ctx)
    db_creative = session.scalars(
        select(CreativeModel).filter_by(
            creative_id=creative_id,
            tenant_id=env._tenant_id,
        )
    ).first()
    assert db_creative is not None, f"Creative {creative_id} not found in DB"
    creative_data = db_creative.data or {}
    assert "generative_build_result" in creative_data, (
        f"Expected 'generative_build_result' in creative data, got keys: {list(creative_data.keys())}"
    )
    env = ctx["env"]
    registry = env.mock["registry"].return_value
    assert registry.build_creative.called, "build_creative should have been called to generate content"


@then(parsers.parse('the generative build should use "{expected_prompt}" as the prompt'))
def then_generative_build_uses_prompt(ctx: dict, expected_prompt: str) -> None:
    """Assert the generative build was invoked with the exact expected prompt.

    Covers INV-2 (prompt from assets), INV-3 (from context_description),
    and INV-4 (name fallback: "Create a creative for: {name}").
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected generative build with prompt '{expected_prompt}' but got {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)

    env = ctx["env"]
    registry = env.mock["registry"].return_value
    assert registry.build_creative.called, "build_creative should have been called for generative format"
    call_kwargs = registry.build_creative.call_args
    # Extract the message argument (keyword or positional)
    message_arg = call_kwargs.kwargs.get("message")
    if message_arg is None:
        # Try positional: build_creative(agent_url, format_id, message, ...)
        if len(call_kwargs.args) > 2:
            message_arg = call_kwargs.args[2]
    if message_arg is None:
        for kw_name in ("prompt", "text"):
            message_arg = call_kwargs.kwargs.get(kw_name)
            if message_arg:
                break
    assert message_arg is not None, (
        f"build_creative must be called with a message/prompt, got args={call_kwargs.args}, kwargs={call_kwargs.kwargs}"
    )
    assert message_arg == expected_prompt, f"Expected prompt '{expected_prompt}', got '{message_arg}'"


@then("the generative build should be skipped")
def then_generative_build_skipped(ctx: dict) -> None:
    """Assert the generative build was NOT invoked (update without prompt).

    INV-5: no prompt in assets or inputs (update) -> generative build skipped.
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected generative build to be skipped but got {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    actions = [str(getattr(getattr(r, "action", None), "value", getattr(r, "action", None))) for r in results]
    assert any(a in ("updated", "unchanged") for a in actions), (
        f"Expected updated/unchanged when build skipped, got {actions}"
    )
    # build_creative should NOT have been called
    env = ctx["env"]
    registry = env.mock["registry"].return_value
    assert not registry.build_creative.called, "build_creative should NOT be called when update has no prompt"


@then("the existing creative data should be preserved")
def then_existing_data_preserved(ctx: dict) -> None:
    """Assert the existing generative data was preserved after a prompt-less update.

    INV-5: existing creative data (generative_build_result, generative_status,
    generative_context_id) should be preserved. Hard-asserts all conditions.
    """
    assert "error" not in ctx, f"Expected data preservation but got error: {ctx.get('error')}"
    env = ctx["env"]
    session = env.get_session()
    assert session is not None, "DB session required to verify data preservation"

    from sqlalchemy import select

    from src.core.database.models import Creative as CreativeModel

    creative_id = latest_creative_id(ctx)
    db_creative = session.scalars(
        select(CreativeModel).filter_by(
            creative_id=creative_id,
            tenant_id=env._tenant_id,
        )
    ).first()
    assert db_creative is not None, f"Creative {creative_id} not found in DB after update"

    expected_data = ctx.get("existing_generative_data", {})
    assert expected_data, (
        "Test setup must populate ctx['existing_generative_data'] with baseline values before asserting preservation"
    )
    creative_data = db_creative.data or {}
    for key in ("generative_build_result", "generative_status", "generative_context_id"):
        if key in expected_data:
            assert key in creative_data, (
                f"Expected preserved key '{key}' in creative data, got keys: {list(creative_data.keys())}"
            )
            assert creative_data[key] == expected_data[key], (
                f"Expected preserved '{key}' = {expected_data[key]!r}, got {creative_data[key]!r}"
            )


def _stored_assets_for_last_creative(ctx: dict) -> dict:
    """Fetch the last synced creative's stored ``data['assets']`` from the DB."""
    from sqlalchemy import select

    from src.core.database.models import Creative as CreativeModel

    env = ctx["env"]
    creative_id = latest_creative_id(ctx)
    with _readback(ctx) as session:
        db_creative = session.scalars(
            select(CreativeModel).filter_by(creative_id=creative_id, tenant_id=env._tenant_id)
        ).first()
        assert db_creative is not None, f"Creative {creative_id} not found in DB"
        return (db_creative.data or {}).get("assets", {})


@then("the user-provided assets should be preserved")
def then_user_assets_preserved(ctx: dict) -> None:
    """Assert user-provided assets are preserved in the DB after generative build.

    INV-6: user assets take priority over generative output. Verify the stored
    creative's assets still carry each user-provided asset's fields (field
    containment, via assert_assets — production enriches the stored object with
    null-default fields, so this is containment, not byte-for-byte equality). This
    catches generated content overwriting user values.
    """
    error = ctx.get("error")
    assert error is None, f"SPEC-PRODUCTION GAP: expected user assets preserved but got {type(error).__name__}: {error}"

    stored_assets = _stored_assets_for_last_creative(ctx)
    specs = ctx["user_provided_assets"]
    assert specs, "Given step must populate user_provided_assets with at least one asset"
    # Same specs that built the mock verify the stored result (shape + containment handled in AssetSpec).
    assert_assets(stored_assets, *specs)


@then("user assets should take priority over any generated content")
def then_user_assets_priority_over_generated(ctx: dict) -> None:
    """Assert user assets take priority over generative output in the DB.

    INV-6: verify the creative's stored data uses user-provided assets,
    not the generated ones from build_creative.
    """
    error = ctx.get("error")
    assert error is None, f"SPEC-PRODUCTION GAP: expected user asset priority but got {type(error).__name__}: {error}"
    # A missing DB session is a HARNESS defect, not a production gap: production's behaviour cannot influence whether the env opened one. Excusing it as an expected failure meant the persistence claim silently graded nothing.
    assert ctx["env"].get_session() is not None, (
        "no DB session, so the user-asset-priority claim cannot be verified against storage"
    )

    stored_assets = _stored_assets_for_last_creative(ctx)
    # User-provided assets must survive the generative build (not overwritten by generated content).
    assert_assets(stored_assets, *ctx.get("user_provided_assets", []))
    # PRIORITY (distinct from mere preservation): the generative output must NOT be merged in —
    # no asset role beyond the ones the buyer submitted may appear in the stored creative.
    submitted_roles = set(ctx["creatives"][-1].get("assets", {}))
    leaked = set(stored_assets) - submitted_roles
    assert not leaked, (
        f"INV-6: generated assets {sorted(leaked)} leaked into stored creative; "
        f"user-submitted roles were {sorted(submitted_roles)}. User assets must take priority."
    )


# ═══════════════════════════════════════════════════════════════════════
# Missing step definitions, pzlv, 28p6, wsc1,
# thm4, bkbu, yqpf
# ═══════════════════════════════════════════════════════════════════════


# --- 5o9e: bare error-code Then steps (without "with suggestion") ---


# --- pzlv: Given steps for format compatibility scenarios ---


@given("assignments to a package whose product has empty format_ids")
def given_assignments_to_package_empty_format_ids(ctx: dict) -> None:
    """Create assignments to a package whose product has format_ids=[].

    Per BR-RULE-039 INV-3: empty format_ids means all formats are allowed.
    Delegates to ``_setup_assignment_package_for_format`` with empty list.
    """
    _setup_assignment_package_for_format(ctx, product_format_ids=[])


@given("assignments to two packages: one with compatible format and one incompatible")
def given_assignments_two_packages_format_compat(ctx: dict) -> None:
    """Create two package assignments: one format-compatible, one not.

    The compatible package's product accepts the creative's format_id.
    The incompatible package's product only accepts a different format.
    """
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    agent_url = ctx.get("creative_agent_url", env.DEFAULT_AGENT_URL)
    creative_format = _scenario_format_id(ctx, env)

    # Unique ids on every transport, the same way. The factory's default package_id is
    # one constant, so leaving it to the factory gave both packages the same id: the
    # assignment lookup then resolved both entries to whichever row won, and the
    # "compatible" assignment was graded against the incompatible product. There is no
    # reason to mint ids only for e2e_rest -- a fresh id is right everywhere.
    extra_mb: dict = {"media_buy_id": _e2e_unique_id("mb")}
    extra_pkg_compat: dict = {"package_id": _e2e_unique_id("pkg")}
    extra_pkg_incompat: dict = {"package_id": _e2e_unique_id("pkg")}
    extra_prod_compat: dict = {"product_id": _e2e_unique_id("prod")}
    extra_prod_incompat: dict = {"product_id": _e2e_unique_id("prod")}

    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active", **extra_mb)

    # Compatible package: product accepts the creative's format
    compatible_product = ProductFactory(
        tenant=tenant,
        format_ids=[_scenario_format_entry(ctx, env)],
        **extra_prod_compat,
    )
    compatible_package = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": compatible_product.product_id, "budget": 1000.0},
        **extra_pkg_compat,
    )

    # Incompatible package: product only accepts a different format
    incompatible_product = ProductFactory(
        tenant=tenant,
        format_ids=[{"agent_url": agent_url, "id": "video_30s_incompatible"}],
        **extra_prod_incompat,
    )
    incompatible_package = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": incompatible_product.product_id, "budget": 1000.0},
        **extra_pkg_incompat,
    )
    env._commit_factory_data()

    ctx["media_buy"] = media_buy
    ctx["compatible_package"] = compatible_package
    ctx["incompatible_package"] = incompatible_package
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {
        creative_id: [compatible_package.package_id, incompatible_package.package_id],
    }


# --- 28p6: Then step for assignment_errors in response ---


@then("the response should include assignment_errors")
def then_response_includes_assignment_errors(ctx: dict) -> None:
    """Assert the response has a non-empty assignment_errors field.

    In lenient mode with a non-existent package, the spec requires the
    response to record the failure in assignment_errors rather than aborting.
    Warnings are NOT a substitute for assignment_errors.
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: lenient mode should return response with assignment_errors, but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, "Expected at least one creative result to check for assignment_errors"
    first = results[0]
    assignment_errors = getattr(first, "assignment_errors", None) or []
    assert assignment_errors, (
        f"SPEC-PRODUCTION GAP: expected non-empty assignment_errors on creative result (INV-4 lenient mode), but assignment_errors is empty: {assignment_errors!r}"
    )


# --- wsc1: creative setup and assertion steps ---


@given(parsers.parse('a creative "{creative_id}" exists for principal "{principal_id}" in the same tenant'))
def given_creative_exists_for_principal_same_tenant(ctx: dict, creative_id: str, principal_id: str) -> None:
    """Pre-seed a creative for a (possibly different) principal in the same tenant.

    Used by cross-principal isolation tests (BR-RULE-034 INV-2): the creative
    belongs to principal_id (e.g. "buyer-A"), but the authenticated principal
    (e.g. "buyer-B") is different — sync should create a new creative.
    Delegates to the "in the tenant" Given (same semantics; the named principal
    gets its own get-or-create row, never overwriting ctx["principal"]).
    """
    given_creative_exists_for_principal(ctx, creative_id, principal_id)


@then(parsers.parse('the created creative should be associated with principal "{principal_id}"'))
def then_creative_associated_with_principal(ctx: dict, principal_id: str) -> None:
    """Assert the synced creative's principal_id matches the authenticated principal.

    BR-RULE-034 INV-3: new creatives are stamped with the authenticated principal.

    The row is the only observable: the principal is the seller's own attribution
    of the creative and sync-creatives-response.json carries no principal on the
    per-creative entry (``account`` is the buying account, a different thing).
    """
    from sqlalchemy import select

    from src.core.database.models import Creative as CreativeModel

    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected creative created for principal '{principal_id}', but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)

    creative_id = latest_creative_id(ctx)
    tenant_id = ctx["tenant"].tenant_id
    with _readback(ctx) as session:
        creative = session.scalars(
            select(CreativeModel).filter_by(
                creative_id=creative_id,
                tenant_id=tenant_id,
            )
        ).first()
        assert creative is not None, f"Creative {creative_id} not found in DB after sync"
        assert creative.principal_id == principal_id, (
            f"Expected creative principal_id='{principal_id}', got '{creative.principal_id}'"
        )


# --- cswm: cross-principal creative isolation (BR-RULE-034 INV-2) ---


@when(parsers.parse('the Buyer Agent syncs creative "{creative_id}" as principal "{principal_id}"'))
def when_sync_creative_as_principal(ctx: dict, creative_id: str, principal_id: str) -> None:
    """Sync a specific creative_id as a specific principal.

    BR-RULE-034 INV-2: the authenticated principal differs from the pre-existing
    creative's owner. The sync should create a new creative for the authenticated
    principal rather than updating the existing one.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    # The auth Given only mutates the env identity; the AUTHENTICATED principal
    # (e.g. buyer-B) still needs a DB row — the pre-existing creative's Given
    # seeded only the OTHER principal. Without it the new creative's insert
    # violates the principals FK and the sync reports action='failed'.
    from src.core.database.models import Principal
    from tests.factories import PrincipalFactory
    from tests.factories.core import get_or_create

    tenant = ctx["tenant"]
    get_or_create(
        env,
        Principal,
        {"principal_id": principal_id, "tenant_id": tenant.tenant_id},
        lambda: PrincipalFactory(tenant=tenant, principal_id=principal_id),
    )
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id=creative_id,
        name=f"Synced creative {creative_id}",
        format_id=_creative_format_id_entry(ctx, env),
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    dispatch_request(ctx, creatives=ctx["creatives"])


@when('the Buyer Agent syncs an assignment of creative "creative-xp" to a package owned by the authenticated principal')
def when_sync_cross_principal_assignment(ctx: dict) -> None:
    """Dispatch sync_creatives carrying the caller's OWN creative plus an assignment
    referencing another principal's creative — the cross-principal FK-500 surface
    (local feature; upstream storyboard gap, PR #1430 review).

    The caller's own creative is present because the PIN REQUIRES it: in
    3.1/media-buy/sync-creatives-request.json, ``creatives`` is in ``required`` and
    carries ``minItems: 1``. This step used to send ``creatives=[]``, so every
    transport correctly refused the whole request with INVALID_REQUEST before the
    assignment logic was ever reached — the scenario could not observe the skip it
    exists to grade, and the refusal it got instead read as a production bug.
    A spec-valid request is the only one that reaches the behavior under test.
    """
    from src.core.database.models import Principal
    from tests.factories import MediaBuyFactory, MediaPackageFactory, PrincipalFactory
    from tests.factories.core import get_or_create

    env = ctx["env"]
    tenant = ctx["tenant"]
    authenticated = get_or_create(
        env,
        Principal,
        {"principal_id": env._principal_id, "tenant_id": tenant.tenant_id},
        lambda: PrincipalFactory(tenant=tenant, principal_id=env._principal_id),
    )
    media_buy = MediaBuyFactory(tenant=tenant, principal=authenticated)
    pkg = MediaPackageFactory(media_buy=media_buy)
    env._commit_factory_data()
    ctx["xp_package_id"] = pkg.package_id
    format_id, agent_url, assets = _format_payload(ctx, env)
    own_creative = CreativeAssetRequestFactory.payload(
        creative_id="creative-own-b",
        name="Own Creative",
        format_id={"id": format_id, "agent_url": agent_url},
        assets=assets,
    )
    dispatch_request(
        ctx,
        creatives=[own_creative],
        assignments=[{"creative_id": "creative-xp", "package_id": pkg.package_id}],
        validation_mode="lenient",
    )


@then("the sync operation should not fail")
def then_sync_did_not_fail(ctx: dict) -> None:
    """The cross-principal reference must be skipped — never a raw FK 500 —
    and the skip must be VISIBLE: a synthesized per-item action='failed'
    result naming the package in assignment_errors (;
    the spec's success branch forbids response-level errors, so the outcome
    rides creatives[]). A bare no-error check survives deletion of the
    error recording — this assertion does not.
    """
    error = ctx.get("error")
    assert error is None, f"sync_creatives failed on a cross-principal assignment reference: {error!r}"
    response = require_payload(ctx)
    entries = {r.creative_id: r for r in (getattr(response, "creatives", None) or [])}
    entry = entries.get("creative-xp")
    assert entry is not None, (
        f"Skipped cross-principal assignment must surface as a per-item result entry, got creatives={sorted(entries)}"
    )
    assert entry.action == "failed", f"Expected action='failed' for the skipped reference, got {entry.action!r}"
    pkg_id = ctx["xp_package_id"]
    assert (entry.assignment_errors or {}).get(pkg_id), (
        f"assignment_errors must name the skipped package {pkg_id}: {entry.assignment_errors!r}"
    )


@then('no assignment should exist for creative "creative-xp" in the tenant')
def then_no_assignment_for_xp_creative(ctx: dict) -> None:
    """DB read-back: the cross-principal reference must not create a row."""
    from sqlalchemy import select

    from src.core.database.models import CreativeAssignment as DBAssignment

    tenant = ctx["tenant"]
    with db_session(ctx) as session:
        rows = session.scalars(
            select(DBAssignment).filter_by(tenant_id=tenant.tenant_id, creative_id="creative-xp")
        ).all()
    assert len(rows) == 0, f"Cross-principal assignment reference created {len(rows)} row(s) — must be 0"


@then(parsers.parse('a new creative should be created for principal "{principal_id}"'))
def then_new_creative_created_for_principal(ctx: dict, principal_id: str) -> None:
    """Assert a new creative was created for the given principal (BR-RULE-034 INV-2).

    Checks both:
    - The response contains a creative with action="created"
    - The DB row for this creative has the correct principal_id
    """
    from sqlalchemy import select

    from src.core.database.models import Creative as CreativeModel

    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected creative created for principal '{principal_id}', but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)

    # Assert response has action="created"
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, f"Expected at least one SyncCreativeResult in response, got: {resp}"
    first = results[0]
    action_val = getattr(first, "action", None)
    action_str = str(getattr(action_val, "value", action_val))
    assert action_str == "created", f"Expected creative action 'created' for cross-principal sync, got '{action_str}'"

    # Assert DB creative has correct principal_id
    creative_id = latest_creative_id(ctx)
    tenant_id = ctx["tenant"].tenant_id
    with db_session(ctx) as session:
        creative = session.scalars(
            select(CreativeModel).filter_by(
                creative_id=creative_id,
                tenant_id=tenant_id,
                principal_id=principal_id,
            )
        ).first()
        assert creative is not None, f"Creative {creative_id} not found in DB for principal '{principal_id}'"
        assert creative.principal_id == principal_id, (
            f"Expected principal_id='{principal_id}', got '{creative.principal_id}'"
        )


@then(parsers.parse('the existing creative for principal "{principal_id}" should remain unchanged'))
def then_existing_creative_unchanged(ctx: dict, principal_id: str) -> None:
    """Assert the pre-existing creative for a different principal is untouched.

    BR-RULE-034 INV-2: cross-principal sync must not modify another principal's creative.
    Verifies the pre-existing creative still exists with the same name and principal_id.
    """
    from sqlalchemy import select

    from src.core.database.models import Creative as CreativeModel

    pre_existing_id = ctx["pre_existing_creative_id"]
    tenant_id = ctx["tenant"].tenant_id

    with _readback(ctx) as session:
        creative = session.scalars(
            select(CreativeModel).filter_by(
                creative_id=pre_existing_id,
                tenant_id=tenant_id,
                principal_id=principal_id,
            )
        ).first()
        assert creative is not None, (
            f"Pre-existing creative '{pre_existing_id}' for principal '{principal_id}' "
            f"was deleted or not found — cross-principal sync should not affect it"
        )
        assert creative.principal_id == principal_id, (
            f"Pre-existing creative's principal_id changed from '{principal_id}' "
            f"to '{creative.principal_id}' — cross-principal isolation violated"
        )


@then("the creative should be validated by the creative agent")
def then_creative_validated_by_agent(ctx: dict) -> None:
    """Assert the creative was processed through external agent validation.

    BR-RULE-035: HTTP-based format_ids trigger external creative agent validation.
    Verifies registry.get_format was called for the specific format_id from Given,
    and that the response shows a successful sync outcome (action created/updated).
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected creative agent validation, but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)

    # Assert observable outcome: the creative was successfully synced
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, "Expected at least one SyncCreativeResult from agent-validated sync"
    actions = [str(getattr(getattr(r, "action", None), "value", getattr(r, "action", None))) for r in results]
    assert any(a in ("created", "updated") for a in actions), (
        f"Expected created/updated action for agent-validated creative, got {actions}"
    )

    # Assert the registry was consulted for the specific format_id
    env = ctx["env"]
    registry_mock = env.mock.get("registry")
    assert registry_mock is not None, "Harness must wire registry mock for agent validation scenario"
    registry_instance = registry_mock.return_value
    assert registry_instance.get_format.call_count > 0, (
        "Expected creative agent validation (registry.get_format called), but it was never called"
    )
    expected_format_id = ctx.get("creative_format_id")
    if expected_format_id is not None:
        # get_format(agent_url, format_id): the id is the SECOND positional argument (or
        # the keyword). This read args[0] -- the agent_url -- and so could never match.
        called_format_ids = [
            c.kwargs.get("format_id", c.args[1] if len(c.args) > 1 else None)
            for c in registry_instance.get_format.call_args_list
        ]
        assert expected_format_id in called_format_ids, (
            f"Expected get_format called with format_id={expected_format_id!r}, but was called with {called_format_ids}"
        )


@then(parsers.parse('the response should include one creative with action "{action}"'))
def then_response_includes_one_creative_with_action(ctx: dict, action: str) -> None:
    """Assert exactly one SyncCreativeResult in the response has the given action.

    Used by partial-success scenarios where multiple creatives are synced
    and the response contains mixed results (e.g. one "created", one "failed").
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected one creative with action '{action}', but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, f"Expected at least one SyncCreativeResult, got empty: {resp}"

    all_actions = []
    found = False
    for r in results:
        action_val = getattr(r, "action", None)
        action_str = str(getattr(action_val, "value", action_val))
        all_actions.append(action_str)
        if action_str == action:
            found = True
    assert found, f"Expected at least one creative with action '{action}', got actions: {all_actions}"


# --- thm4: Given step for creative with invalid format_id ---


@given("a creative with an invalid format_id")
def given_creative_with_invalid_format_id(ctx: dict) -> None:
    """Build a creative payload with a format_id that is syntactically invalid.

    Uses a format_id string that fails the FormatId.id pattern validation
    (e.g. contains spaces or special characters).
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)

    format_id = "invalid format!!!"
    creative_id = "creative-invalid-fmt-001"
    creative_payload = malformed(
        "semantic",
        "format_id is shaped correctly — a str in the id slot, alongside a real agent_url — but "
        "its VALUE breaks FormatId.id's pattern: CreativeAssetRequest rejects it with "
        "format_id.id String should match pattern '^[a-zA-Z0-9_-]+$' "
        "[type=string_pattern_mismatch]. The scenario grades syntactic format-id validation, so "
        "the spaces and '!!!' are the payload's point and must survive to the wire.",
        CreativeAssetRequestFactory.payload(
            creative_id=creative_id,
            name="Invalid Format Creative",
            format_id={"id": format_id, "agent_url": env.DEFAULT_AGENT_URL},
        ),
        # semantic, and the pin REJECTS it. The kind says nothing about that: a different
        # semantic case — an unknown but well-formed format id — the pin ACCEPTS.
        obligation=ErrorCode.INVALID_REQUEST,
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = format_id


# --- bkbu: Given step for validation_mode not set ---


@given("validation_mode is not set")
def given_validation_mode_not_set(ctx: dict) -> None:
    """Ensure validation_mode is not set on the request.

    Counterpart to ``validation_mode is "{mode}"`` — removes any
    previously set validation_mode so the default (strict) applies.
    """
    ctx.pop("validation_mode", None)


# --- yqpf: assignment lifecycle steps ---


@given("assignments to an existing package")
def given_assignments_to_existing_package(ctx: dict) -> None:
    """Create an existing package and assign the creative to it.

    Used by assignment_format partition scenarios. Creates a media buy with
    a package whose product accepts the creative's format.
    """
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    agent_url = env.DEFAULT_AGENT_URL
    creative_format = _scenario_format_id(ctx, env)

    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    product = ProductFactory(
        tenant=tenant,
        format_ids=[_scenario_format_entry(ctx, env)],
    )
    package = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {creative_id: [package.package_id]}


@given("the creative is already assigned to package")
def given_creative_already_assigned_to_package_partition(ctx: dict) -> None:
    """Seed a pre-existing creative + assignment for idempotent upsert testing.

    Used by the assignment_format partition scenario. Creates a Creative ORM row,
    a package, and a CreativeAssignment row. The sync should update (not duplicate).
    """
    from tests.factories import (
        CreativeAssignmentFactory,
        CreativeFactory,
        MediaBuyFactory,
        MediaPackageFactory,
        ProductFactory,
    )

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    agent_url = env.DEFAULT_AGENT_URL
    creative_format = _scenario_format_id(ctx, env)

    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    product = ProductFactory(
        tenant=tenant,
        format_ids=[_scenario_format_entry(ctx, env)],
    )
    package = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )

    creative_payload = ctx["creatives"][-1]
    creative_id = creative_payload["creative_id"]
    creative = CreativeFactory(
        tenant=tenant,
        principal=principal,
        creative_id=creative_id,
        name=creative_payload["name"],
        agent_url=agent_url,
        format=creative_format,
    )
    existing_assignment = CreativeAssignmentFactory(
        creative=creative,
        media_buy=media_buy,
        package_id=package.package_id,
        weight=50,
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    ctx["existing_assignment_id"] = existing_assignment.assignment_id
    ctx["existing_assignment_weight_before"] = 50
    ctx["idempotent_package_id"] = package.package_id
    ctx["assignments"] = {creative_id: [package.package_id]}


@given("assignments to three packages: two valid, one non-existent")
def given_assignments_three_packages_mixed(ctx: dict) -> None:
    """Create three package assignments: two valid, one non-existent.

    Used by the lenient-mode partial-success main flow scenario.
    """
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    format_id, agent_url, _assets = _format_payload(ctx, env)

    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    product = ProductFactory(
        tenant=tenant,
        format_ids=[{"agent_url": agent_url, "id": format_id}],
    )
    # Two DISTINCT packages: the factory's default package_id is one literal, so two
    # rows built from it collapsed into one assignment.
    valid_pkg_1 = MediaPackageFactory(
        media_buy=media_buy,
        package_id=_e2e_unique_id("pkg"),
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )
    valid_pkg_2 = MediaPackageFactory(
        media_buy=media_buy,
        package_id=_e2e_unique_id("pkg"),
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["valid_packages"] = [valid_pkg_1, valid_pkg_2]
    nonexistent_pkg_id = "pkg-nonexistent-three-mix-404"
    ctx["nonexistent_package_id"] = nonexistent_pkg_id
    creative_id = latest_creative_id(ctx)
    ctx["assignments"] = {
        creative_id: [valid_pkg_1.package_id, valid_pkg_2.package_id, nonexistent_pkg_id],
    }


@then("the assignment should use equal rotation")
def then_assignment_equal_rotation(ctx: dict) -> None:
    """weight omitted: "the creative receives equal rotation with other unweighted creatives".

    Every unweighted assignment is persisted with the same default, 100 -- one value for
    all of them is what equal rotation means, so the default is what this reads.
    """
    assignment = _get_assignment_from_db(ctx)
    assert assignment.weight == 100, f"Expected the equal-rotation default weight 100, got {assignment.weight}"


@then("the assignment should be created")
def then_assignment_created_bare(ctx: dict) -> None:
    """Assert the sync response reports the package was assigned to the creative.

    Bare variant (without "successfully") for scenarios that focus on
    weight-absent semantics (e.g. INV-2 — weight omitted means equal rotation).
    """
    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    assigned = _get_creative_assigned_to(ctx)
    expected = ctx["package"].package_id
    assert expected in assigned, f"Expected {expected!r} in assigned_to, got {assigned}"


@then("the assignment should be created with placement targeting")
def then_assignment_created_with_placement(ctx: dict) -> None:
    """The persisted assignment carries the placement_ids the entry asked for."""
    assignment = _get_assignment_from_db(ctx)
    assert assignment.placement_ids == ["slot_a"], (
        f"Expected placement_ids ['slot_a'], got {assignment.placement_ids} for assignment {assignment.assignment_id}"
    )


@then("the second should be an idempotent upsert")
def then_second_is_idempotent_upsert(ctx: dict) -> None:
    """Assert the duplicate (creative_id, package_id) pair was handled as idempotent upsert.

    Verifies only one assignment row exists for the (creative_id, package_id) pair
    after syncing two identical entries — the second entry should update, not duplicate.
    """
    from sqlalchemy import select

    from src.core.database.models import CreativeAssignment

    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: idempotent upsert should succeed, but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)

    tenant_id = ctx["tenant"].tenant_id
    creative_id = latest_creative_id(ctx)
    package_id = ctx["package"].package_id
    with db_session(ctx) as session:
        all_rows = session.scalars(
            select(CreativeAssignment).filter_by(
                tenant_id=tenant_id,
                creative_id=creative_id,
                package_id=package_id,
            )
        ).all()
        assert len(all_rows) == 1, (
            f"Idempotent upsert should produce exactly 1 row, got {len(all_rows)} "
            f"for creative={creative_id}, package={package_id}"
        )


@then("the assignment should be created as paused (no delivery)")
def then_assignment_created_as_paused_no_delivery(ctx: dict) -> None:
    """Spec: weight=0 assignment is paused (no delivery).

    Production hard-codes weight=100 on all new assignments and has no API
    surface for per-entry weight. SPEC-PRODUCTION GAP on weight/delivery only.
    """
    from sqlalchemy import select

    from src.core.database.models import CreativeAssignment

    # Assert what production DOES provide: assignment was created
    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    assigned = _get_creative_assigned_to(ctx)
    expected_pkg = ctx["package"].package_id
    assert expected_pkg in assigned, f"Expected {expected_pkg!r} in assigned_to, got {assigned}"

    # Verify the DB row exists. The weight is nowhere on the wire, so the row is
    # the only observable for it (sync-creatives-response.json carries assigned_to
    # and assignment_errors, no per-assignment attributes).
    tenant_id = ctx["tenant"].tenant_id
    creative_id = latest_creative_id(ctx)
    with _readback(ctx) as session:
        assignment = session.scalars(
            select(CreativeAssignment).filter_by(
                tenant_id=tenant_id,
                creative_id=creative_id,
                package_id=expected_pkg,
            )
        ).first()
        assert assignment is not None, f"No CreativeAssignment found for creative={creative_id}, package={expected_pkg}"
        # GAP: the pinned 3.1 sync-creatives-request.json defines assignments[].weight, production
        # hard-codes 100. Declared ONCE in _SELECTIVE_XFAIL so it XPASSes
        # loudly if production implements it; this step now ASSERTS instead of excusing itself.
        assert assignment.weight == 0, (
            f"weight=0 means assigned but PAUSED, receiving no delivery; got {assignment.weight}"
        )


@then("the response should include the creative with assignment results")
def then_response_includes_creative_with_assignment_results(ctx: dict) -> None:
    """Assert the response includes a SyncCreativeResult with assigned_to populated.

    POST-S3: Buyer knows which packages each creative was assigned to.
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected creative with assignment results, but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, f"Expected at least one SyncCreativeResult, got empty: {resp}"
    first = results[0]
    assigned = first.assigned_to or []
    # The response should include assignment results (assigned_to list)
    assert assigned, f"POST-S3: Expected non-empty assigned_to on SyncCreativeResult, got assigned_to={assigned}"


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — error path assertions (pljp)
# ═══════════════════════════════════════════════════════════════════════


@then("the operation should abort with PACKAGE_NOT_FOUND")
def then_operation_should_abort_package_not_found(ctx: dict) -> None:
    """Assert strict-mode abort with PACKAGE_NOT_FOUND for non-existent package.

    Production raises AdCPNotFoundError with error_code="REFERENCE_NOT_FOUND" (not the
    spec-required "PACKAGE_NOT_FOUND"). We assert the error was raised and
    xfail on the error_code mismatch.
    """

    error = ctx.get("error")
    assert error is not None, (
        f"SPEC-PRODUCTION GAP: strict mode with non-existent package should abort with PACKAGE_NOT_FOUND, but production succeeded without raising. Response: {payload_or_none(ctx)!r}"
    )

    # The xfail that used to sit here said "AdCPNotFoundError.error_code is
    # 'NOT_FOUND' -- needs a domain-specific subclass". That subclass now exists and
    # is used: _assignments.py:163 raises AdCPPackageNotFoundError, which emits the
    # published PACKAGE_NOT_FOUND. The gap is closed, so the scenario asserts it
    # rather than excusing it.
    result = ctx.get("result")
    wire_code = result.wire_error_code() if result is not None else None
    assert wire_code is not None, (
        f"Expected PACKAGE_NOT_FOUND on the wire, but no envelope was captured: {type(error).__name__}: {error}"
    )
    assert wire_code == "PACKAGE_NOT_FOUND", (
        f"Strict mode with a non-existent package must reject with PACKAGE_NOT_FOUND, got {wire_code!r}"
    )


@then("the assignment_errors should contain the package_id")
def then_assignment_errors_contain_package_id(ctx: dict) -> None:
    """Assert assignment_errors references the non-existent package_id.

    In lenient mode, the response should include assignment_errors keyed by
    the missing package_id. The production populates assignment_errors as
    dict[str, str] where keys are package_ids and values are error messages.
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: lenient mode should return response with assignment_errors, but production raised {type(error).__name__}: {error}"
    )

    resp = require_payload(ctx)

    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, f"Response has no creative results: {resp}"

    first = results[0]
    assignment_errors = getattr(first, "assignment_errors", None)
    assert assignment_errors, (
        f"SPEC-PRODUCTION GAP: expected non-empty assignment_errors in response, but got assignment_errors={assignment_errors!r}"
    )

    # Retrieve the expected non-existent package_id from ctx
    assignments_dict = ctx.get("assignments", {})
    expected_pkg_ids = set()
    for pkg_list in assignments_dict.values():
        expected_pkg_ids.update(pkg_list)

    assert isinstance(assignment_errors, dict), (
        f"Expected assignment_errors to be a dict, got {type(assignment_errors).__name__}"
    )
    matched = expected_pkg_ids & set(assignment_errors.keys())
    assert matched, (
        f"assignment_errors keys {list(assignment_errors.keys())} do not contain "
        f"any of the expected non-existent package_ids {expected_pkg_ids}"
    )
    # Verify the error message is a non-empty string
    for pkg_id in matched:
        assert assignment_errors[pkg_id], f"assignment_errors[{pkg_id!r}] is empty: {assignment_errors[pkg_id]!r}"


@then("the system should reject with VALIDATION_ERROR")
def then_system_should_reject_validation_error(ctx: dict) -> None:
    """Assert an unknown validation_mode is rejected, graded ON THE WIRE.

    The pinned 3.1 enums/validation-mode.json admits exactly ["strict", "lenient"], so
    "partial" is schema-invalid and MUST be refused. Which LAYER refuses it differs by
    transport -- MCP's TypeAdapter rejects before _impl, REST/A2A reach the boundary --
    and that is precisely why this reads the wire code rather than the class of
    ctx["error"]: the buyer sees a code, not a Python type, and asserting on the rebuilt
    exception is what tests/CLAUDE.md forbids. Two xfails used to record the transport
    difference as a production gap; the gap was in the assertion's layer.
    """
    result = ctx.get("result")
    assert result is not None, "no dispatch result recorded — the When step did not run"
    wire_code = result.wire_error_code()
    assert wire_code is not None, (
        f"validation_mode 'partial' is not in the pinned enum [strict, lenient] and must be "
        f"refused, but no error reached the wire. Response: {payload_or_none(ctx)!r}"
    )
    expected_codes = {"VALIDATION_ERROR", "INVALID_REQUEST"}
    assert wire_code in expected_codes, (
        f"expected the refusal to carry one of {sorted(expected_codes)} on the wire, got {wire_code!r}"
    )


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — assignment outcome assertions (7ni0)
# ═══════════════════════════════════════════════════════════════════════


@then("the compatible package assignment should be created")
def then_compatible_package_assignment_created(ctx: dict) -> None:
    """Assert the compatible package is in assigned_to (lenient format-compat scenario).

    In the INV-5 format-mismatch-lenient scenario, two packages are assigned:
    one compatible (format matches), one incompatible. The compatible one
    should appear in assigned_to.
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected compatible assignment to succeed, but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, f"Expected at least one SyncCreativeResult, got empty: {resp}"
    first = results[0]
    assigned = first.assigned_to or []
    compatible_pkg = ctx["compatible_package"]
    assert compatible_pkg.package_id in assigned, (
        f"Expected compatible package {compatible_pkg.package_id!r} in assigned_to, got {assigned}"
    )
    # Also verify the incompatible package is NOT in assigned_to
    incompatible_pkg = ctx["incompatible_package"]
    assert incompatible_pkg.package_id not in assigned, (
        f"Incompatible package {incompatible_pkg.package_id!r} should NOT be in assigned_to, but it is: {assigned}"
    )


@then("the assignment should be skipped with a warning")
def then_assignment_skipped_with_warning(ctx: dict) -> None:
    """Assert the non-existent package assignment was skipped and a warning/error recorded.

    Used by the validation_mode boundary scenario (lenient mode with
    non-existent package). The assignment should NOT appear in assigned_to,
    and the response should include either assignment_errors or warnings
    referencing the skipped package.
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: lenient mode should skip and warn, but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, f"Expected at least one SyncCreativeResult, got empty: {resp}"
    first = results[0]

    # The non-existent package should NOT be in assigned_to
    assigned = first.assigned_to or []
    creative_id = latest_creative_id(ctx)
    nonexistent_pkgs = ctx["assignments"][creative_id]
    for pkg_id in nonexistent_pkgs:
        assert pkg_id not in assigned, (
            f"Non-existent package {pkg_id!r} should NOT be in assigned_to, but it is: {assigned}"
        )

    # There should be a warning or assignment_error referencing the skipped package
    assignment_errors = first.assignment_errors or {}
    warnings = first.warnings or []
    has_error_entry = any(pkg_id in assignment_errors for pkg_id in nonexistent_pkgs)
    has_warning_entry = any(any(pkg_id in w for pkg_id in nonexistent_pkgs) for w in warnings)
    assert has_error_entry or has_warning_entry, (
        f"SPEC-PRODUCTION GAP: expected assignment_errors or warnings referencing skipped package(s) {nonexistent_pkgs}, but assignment_errors={assignment_errors}, warnings={warnings}"
    )
    # If we have an error entry, verify it has a meaningful message
    if has_error_entry:
        for pkg_id in nonexistent_pkgs:
            if pkg_id in assignment_errors:
                assert assignment_errors[pkg_id], (
                    f"assignment_errors[{pkg_id!r}] should have a non-empty message, got {assignment_errors[pkg_id]!r}"
                )


@then("the assignment results should list the assigned packages")
def then_assignment_results_list_assigned_packages(ctx: dict) -> None:
    """Assert each assigned package_id appears in the creative's assigned_to list.

    POST-S3: The buyer knows which packages each creative was assigned to.
    Verifies that the specific package_ids from the Given step appear in
    the response's assigned_to field.
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected assignment results, but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, f"Expected at least one SyncCreativeResult, got empty: {resp}"
    first = results[0]
    assigned = first.assigned_to or []

    # Get the expected package_ids from the Given step's assignments
    creative_id = latest_creative_id(ctx)
    expected_pkgs = ctx["assignments"][creative_id]
    for pkg_id in expected_pkgs:
        assert pkg_id in assigned, f"Expected package {pkg_id!r} in assigned_to, got {assigned}"


@then("two assignments should be created successfully")
def then_two_assignments_created_successfully(ctx: dict) -> None:
    """Assert exactly 2 successful assignments with correct package_ids.

    Used by the lenient-mode partial-success scenario (3 packages: 2 valid,
    1 non-existent). The two valid packages should appear in assigned_to.
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: expected 2 successful assignments, but production raised {type(error).__name__}: {error}"
    )
    resp = require_payload(ctx)
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, f"Expected at least one SyncCreativeResult, got empty: {resp}"
    first = results[0]
    assigned = first.assigned_to or []

    # Assert exactly 2 successful assignments
    assert len(assigned) == 2, f"Expected exactly 2 assigned packages, got {len(assigned)}: {assigned}"

    # Verify the correct packages (the two valid ones from the Given step)
    valid_packages = ctx["valid_packages"]
    expected_pkg_ids = {pkg.package_id for pkg in valid_packages}
    actual_pkg_ids = set(assigned)
    assert actual_pkg_ids == expected_pkg_ids, f"Expected assigned packages {expected_pkg_ids}, got {actual_pkg_ids}"


@then("the response should include assignment_errors for the non-existent package")
def then_response_includes_assignment_errors_for_nonexistent(ctx: dict) -> None:
    """Assert assignment_errors contains an entry keyed by the non-existent package_id.

    Used by the lenient-mode partial-success scenario (3 packages: 2 valid,
    1 non-existent). The non-existent package should appear in
    assignment_errors with a non-empty error message.
    """
    error = ctx.get("error")
    assert error is None, (
        f"SPEC-PRODUCTION GAP: lenient mode should return response with assignment_errors, but production raised {type(error).__name__}: {error}"
    )

    resp = require_payload(ctx)

    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    assert results, f"Expected at least one SyncCreativeResult, got empty: {resp}"

    first = results[0]
    assignment_errors = getattr(first, "assignment_errors", None)
    nonexistent_pkg_id = ctx["nonexistent_package_id"]

    assert assignment_errors, (
        f"SPEC-PRODUCTION GAP: expected non-empty assignment_errors in response, but got assignment_errors={assignment_errors!r}"
    )

    assert isinstance(assignment_errors, dict), (
        f"Expected assignment_errors to be a dict, got {type(assignment_errors).__name__}"
    )
    assert nonexistent_pkg_id in assignment_errors, (
        f"Expected non-existent package {nonexistent_pkg_id!r} in assignment_errors keys "
        f"{list(assignment_errors.keys())}"
    )
    assert assignment_errors[nonexistent_pkg_id], (
        f"assignment_errors[{nonexistent_pkg_id!r}] should have a non-empty error message, "
        f"got {assignment_errors[nonexistent_pkg_id]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — creative scope resolution / boundary (3kw9)
# ═══════════════════════════════════════════════════════════════════════


def _build_creative_scope_payload(ctx: dict, creative_id: str) -> dict:
    """Build a creative payload and add it to ctx["creatives"].

    Shared helper for creative scope Given steps. Ensures tenant/principal
    exist, then builds a minimal payload with a known format_id.

    NOTE: renamed from _build_creative_payload to avoid shadowing the
    original at line 2526 which accepts provenance= keyword.
    """
    env = ctx["env"]
    _ensure_tenant_principal_from_db(ctx, env)
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id=creative_id,
        name=f"Creative {creative_id}",
        format_id=_creative_format_id_entry(ctx, env),
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    return creative_payload


def _preseed_creative_for_principal(ctx: dict, creative_id: str, principal_id: str) -> None:
    """Pre-seed a creative in the DB for a given principal, then build the sync payload.

    Shared helper for "exists for principal" Given steps. The creative is created
    in the DB under (tenant_id, principal_id, creative_id) so that the sync
    operation can find it (update) or not (create for a different principal).
    """
    from sqlalchemy import select

    from src.core.database.models import Principal, Tenant
    from tests.factories import CreativeFactory

    env = ctx["env"]
    _ensure_tenant_principal_from_db(ctx, env)

    # Retrieve tenant from DB (created by auth step)
    with db_session(ctx) as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=env._tenant_id)).first()
        assert tenant is not None, f"Tenant {env._tenant_id!r} not found — auth step should have created it"

        # The principal owning the pre-existing creative may differ from the
        # authenticated principal (cross-principal isolation tests).
        owner_principal = session.scalars(
            select(Principal).filter_by(principal_id=principal_id, tenant_id=env._tenant_id)
        ).first()

    if owner_principal is None:
        owner_principal = PrincipalFactory(tenant=tenant, principal_id=principal_id)
        env._commit_factory_data()

    CreativeFactory(
        tenant=tenant,
        principal=owner_principal,
        creative_id=creative_id,
        name=f"Pre-existing creative {creative_id}",
        agent_url=_scenario_format_entry(ctx, env)["agent_url"],
        format=_scenario_format_entry(ctx, env)["id"],
    )
    env._commit_factory_data()
    ctx["pre_existing_creative_id"] = creative_id

    # Build the creative payload for the sync request
    _build_creative_scope_payload(ctx, creative_id)


@given(parsers.parse('creative "{creative_id}" does not exist for this principal'))
def given_creative_does_not_exist_for_principal(ctx: dict, creative_id: str) -> None:
    """Set up a creative payload for a creative_id with no pre-existing DB row.

    The authenticated principal has no creative with this ID. The sync
    should create a new creative (action="created").
    """
    _build_creative_scope_payload(ctx, creative_id)


@given(parsers.parse('creative "{creative_id}" exists for principal {principal_id}'))
@given(parsers.parse('creative "{creative_id}" exists for principal {principal_id} only'))
@given(parsers.parse('creative "{creative_id}" exists for principal "{principal_id}"'))
@given(parsers.parse('creative "{creative_id}" already exists for principal "{principal_id}"'))
def given_creative_exists_for_principal_scope(ctx: dict, creative_id: str, principal_id: str) -> None:
    """Pre-seed a creative in the DB for a given principal, then build the sync payload.

    Handles all Gherkin variants for creative scope resolution/boundary:
    - ``exists for principal buyer-A`` (unquoted, scope_resolution partition)
    - ``exists for principal buyer-A only`` (unquoted "only" suffix, cross-principal)
    - ``exists for principal "buyer-abc"`` (quoted, scope_boundary)
    - ``already exists for principal "buyer-abc"`` (quoted "already", scope_boundary)

    If the authenticated principal matches principal_id, sync should update
    the creative. If different, sync creates a new one (cross-principal isolation).
    """
    _preseed_creative_for_principal(ctx, creative_id, principal_id)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — creative scope resolution / boundary (1atj)
# ═══════════════════════════════════════════════════════════════════════


def _get_action_str(result: object) -> str:
    """Extract the action string from a SyncCreativeResult.

    The action field is a CreativeAction enum; normalise to a plain string.
    """
    action_val = getattr(result, "action", None)
    return str(getattr(action_val, "value", action_val))


@then(parsers.parse('the action should be "{expected_action}"'))
def then_action_should_be(ctx: dict, expected_action: str) -> None:
    """Assert the first SyncCreativeResult has the expected action."""
    result = _get_sync_creative_result(ctx)
    action_str = _get_action_str(result)
    assert action_str == expected_action, f"Expected creative action '{expected_action}', got '{action_str}'"


@then("the existing creative should be updated")
def then_existing_creative_updated(ctx: dict) -> None:
    """Assert the sync result indicates the creative was updated (action == 'updated')."""
    result = _get_sync_creative_result(ctx)
    action_str = _get_action_str(result)
    assert action_str == "updated", f"Expected creative action 'updated', got '{action_str}'"


@then("a new creative should be created")
def then_new_creative_created(ctx: dict) -> None:
    """Assert the sync result indicates a new creative was created (action == 'created')."""
    result = _get_sync_creative_result(ctx)
    action_str = _get_action_str(result)
    assert action_str == "created", f"Expected creative action 'created', got '{action_str}'"


@then(parsers.parse('a new creative should be created for "{principal_id}"'))
def then_new_creative_created_for_principal_scope(ctx: dict, principal_id: str) -> None:
    """Assert the sync created a new creative stamped with the given principal_id.

    Verifies both:
    1. The SyncCreativeResult action is "created"
    2. The creative in the DB belongs to the expected principal (cross-principal isolation)
    """
    result = _get_sync_creative_result(ctx)
    action_str = _get_action_str(result)
    assert action_str == "created", f"Expected creative action 'created', got '{action_str}'"

    # Verify the creative was stamped with the correct principal_id in DB — the
    # principal is the seller's own attribution and is not on the wire.
    from sqlalchemy import select

    from src.core.database.models import Creative

    creative_id = getattr(result, "creative_id", None) or latest_creative_id(ctx)
    assert creative_id is not None, "No creative_id found in result or ctx"

    env = ctx["env"]
    with _readback(ctx) as session:
        creative = session.scalars(
            select(Creative).filter_by(
                tenant_id=env._tenant_id,
                principal_id=principal_id,
                creative_id=creative_id,
            )
        ).first()
        assert creative is not None, (
            f"No creative found in DB for tenant={env._tenant_id}, principal={principal_id}, creative_id={creative_id}"
        )
        assert creative.principal_id == principal_id, (
            f"Expected creative stamped with principal_id '{principal_id}', got '{creative.principal_id}'"
        )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — assignment weight for proportional delivery (tuq3)
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('creative "{creative_id}" assigned to "{package_id}" with weight {weight:d}'))
def given_creative_assigned_to_package_with_weight(ctx: dict, creative_id: str, package_id: str, weight: int) -> None:
    """Set up a creative payload with an assignment to a package at a given weight.

    Builds the creative payload (if not already present for this creative_id)
    and adds an assignment entry with the specified weight.
    """
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    _ensure_tenant_principal_from_db(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]

    # Build the creative payload if not already present for this creative_id
    existing_ids = {c["creative_id"] for c in ctx.get("creatives", [])}
    if creative_id not in existing_ids:
        _build_creative_scope_payload(ctx, creative_id)

    # Ensure a media buy + package exist for the assignment
    if "media_buy" not in ctx:
        media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
        env._commit_factory_data()
        ctx["media_buy"] = media_buy

    # Create the package if it doesn't exist yet
    packages = ctx.setdefault("_packages", {})
    if package_id not in packages:
        product = ProductFactory(
            tenant=tenant,
            format_ids=[_product_format_entry(ctx, env)],
        )
        package = MediaPackageFactory(
            media_buy=ctx["media_buy"],
            package_id=package_id,
            package_config={"product_id": product.product_id, "budget": 1000.0},
        )
        env._commit_factory_data()
        packages[package_id] = package

    # Add the assignment mapping (creative_id -> [package_id]) and its weight
    assignments = ctx.setdefault("assignments", {})
    assignments.setdefault(creative_id, [])
    if package_id not in assignments[creative_id]:
        assignments[creative_id].append(package_id)
    _ask_assignment_terms(ctx, creative_id, package_id, weight=weight)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — proportional delivery assertions (tuq3)
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('the assignment of "{creative_id}" to "{package_id}" should carry weight {weight:d}'))
def then_assignment_of_creative_carries_weight(ctx: dict, creative_id: str, package_id: str, weight: int) -> None:
    """assignments[].weight is per entry: two creatives on one package each keep their own.

    "When multiple creatives are assigned to the same package, weights determine
    impression distribution proportionally" (the pin). The persisted weights are what
    the ad server rotates on; delivery itself is not observable on this tool.
    """
    from sqlalchemy import select

    from src.core.database.models import CreativeAssignment

    _assert_success_response(ctx)
    with _readback(ctx) as session:
        assignment = session.scalars(
            select(CreativeAssignment).filter_by(
                tenant_id=ctx["tenant"].tenant_id,
                creative_id=creative_id,
                package_id=package_id,
            )
        ).first()
    assert assignment is not None, f"No CreativeAssignment found for creative={creative_id}, package={package_id}"
    assert assignment.weight == weight, (
        f"Expected assignment weight {weight} for creative={creative_id}, package={package_id}, got {assignment.weight}"
    )


# ═══════════════════════════════════════════════════════════════════════
# All-failed success-variant invariant (PR1399 R3-F2)
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("the Buyer has {count:d} creatives that all fail validation"))
def given_creatives_all_fail(ctx: dict, count: int) -> None:
    """Set up `count` creatives that each fail per-creative validation.

    Empty name fails validation; under validation_mode='lenient' each failure
    is reported as a per-creative result (action='failed') INSIDE the success
    variant rather than collapsing the whole operation to the error variant.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)

    format_id, agent_url, assets = _format_payload(ctx, env)
    creatives = ctx.setdefault("creatives", [])
    for i in range(count):
        creatives.append(
            CreativeAssetRequestFactory.payload(
                creative_id=f"creative-fail-{i:03d}",
                name="",  # empty name → per-creative validation failure, and pin-conformant
                format_id={"id": format_id, "agent_url": agent_url},
                assets=assets,
            )
        )
    ctx["validation_mode"] = "lenient"


@then("the response is the success variant carrying a creatives array")
def then_success_variant_with_creatives(ctx: dict) -> None:
    """Success variant (not error variant): a creatives array is present.

    The all-failed sync must NOT collapse to the error variant — per the pinned
    3.1 oneOf, per-item failures live inside SyncCreativesSuccess (required:
    ['creatives']). So there is no operation-level error and the response
    carries a creatives list.
    """
    assert ctx.get("error") is None, (
        f"expected the success variant, but got an operation-level error: {ctx.get('error')!r}"
    )
    response = require_payload(ctx)
    assert isinstance(response.creatives, list), f"creatives must be a list, got {type(response.creatives).__name__}"


@then(parsers.parse('every creative result has action "{action}"'))
def then_every_creative_action(ctx: dict, action: str) -> None:
    """Every per-creative result carries the given action (e.g. 'failed').

    Also the dry_run parity grader: suppressing the outbound creative-agent call
    must not change WHAT the preview reports. dry_run "returns what would be
    created/updated/deleted" (sync-creatives-request.json#/properties/dry_run @
    v3.1.1), so a preview of a create still says "created" -- never a
    SERVICE_UNAVAILABLE blaming the agent for a call the gate never made. The
    live control binds this same step, so preview and live are compared on the
    same field.

    Read off the typed success payload through ``require_payload`` -- the one
    guarded accessor for the dispatch's own payload -- because CreativeSyncEnv
    stashes no success-path wire envelope on a2a (verified). Deliberately NOT an
    either-layer wire-or-typed fallback -- that is the antipattern that lets a
    one-layer regression pass every call site. One locus, stated.
    """
    response = require_payload(ctx)
    assert response is not None, "expected a sync_creatives response payload"
    assert response.creatives, "expected at least one per-creative result"
    actions = [_action_str(c.action) for c in response.creatives]
    assert actions == [action] * len(actions), f"expected every action == {action!r}, got {actions}"


@then("the response does not carry an operation-level errors array")
def then_no_operation_level_errors(ctx: dict) -> None:
    """Confirm the success variant, not the error variant.

    The success variant has no operation-level `errors` field (that field is
    unique to the SyncCreativesError variant). Per-creative errors live on each
    result's own `errors`, which is distinct.
    """
    response = require_payload(ctx)
    operation_errors = getattr(response, "errors", None)
    assert operation_errors is None, (
        f"success variant must not carry operation-level errors[]; got {operation_errors!r}"
    )


# ═══════════════════════════════════════════════════════════════════════
# The workflow-step write path and its escaping notification (GH #2002)
# ═══════════════════════════════════════════════════════════════════════
#
# Three rows make up that write -- one Context, one WorkflowStep per creative
# awaiting approval, one ObjectWorkflowMapping per step -- and today
# _create_sync_workflow_steps writes them through its OWN WorkflowUoW, outside
# the transaction that owns the creatives. Every assertion below therefore reads
# them over an INDEPENDENT connection (CreativeSyncEnv.committed_* ), never
# through env.get_workflow_steps(): that accessor reads the SCOPED session,
# which inside a request IS the transaction under test, so it cannot tell a row
# that is committed from a row that is about to be rolled back -- the only
# question a preview asks.


def _synced_creative_ids(ctx: dict) -> list[str]:
    """The creative_ids this scenario's request carried, sorted."""
    return sorted(c["creative_id"] for c in ctx["creatives"])


@given("the effects escaping the sync transaction are observed as they fire")
def given_observe_escaping_effects(ctx: dict) -> None:
    """Record committed DB state at the instant the Slack notification fires.

    Ordering is only observable AT the effect: once the request has returned,
    the steps are committed and the assignments are committed under either
    order, and nothing distinguishes them.
    """
    ctx["env"].observe_effects_at_notification()


@then("the committed workflow rows name exactly the synced creatives")
def then_committed_workflow_rows_name_synced_creatives(ctx: dict) -> None:
    """The live control: one Context, one step and one mapping per creative.

    Counts AND identities. A count alone would pass against a mapping that
    pointed at some other object, and an identity check alone would pass against
    a second, duplicate step -- the two together are what the preview branch's
    "zero rows" assertion is measured against.
    """
    _assert_success_response(ctx)
    expected_ids = _synced_creative_ids(ctx)
    effects = ctx["env"].committed_sync_effects()

    assert effects.workflow_object_ids == expected_ids, (
        f"expected one committed workflow mapping per synced creative {expected_ids}, got {effects.workflow_object_ids}"
    )
    assert len(effects.workflow_step_ids) == len(expected_ids), (
        f"expected {len(expected_ids)} committed workflow step(s) for {expected_ids}, got {effects.workflow_step_ids}"
    )
    assert len(effects.context_ids) == 1, (
        f"expected exactly one committed Context row for the tenant, got {effects.context_ids}"
    )
    # Each mapping must name a row that is actually on file: a mapping pointing
    # at a creative the request did not persist is a dangling approval queue
    # entry, which is the failure the whole write path exists to avoid.
    persisted = sorted(_persisted_creative_fingerprints(ctx))
    assert effects.workflow_object_ids == sorted(set(effects.workflow_object_ids) & set(persisted)), (
        f"committed workflow mappings name creatives that are not persisted: "
        f"mapped {effects.workflow_object_ids}, persisted {persisted}"
    )


@then("no workflow step, mapping or context row is committed for the tenant")
def then_no_committed_workflow_rows(ctx: dict) -> None:
    """The preview branch: the write path ran, and the rollback took all of it.

    Measured against the live control above, which proves this exact payload on
    this exact tenant does produce all three rows.
    """
    _assert_success_response(ctx)
    effects = ctx["env"].committed_sync_effects()
    observed = (effects.workflow_step_ids, effects.workflow_object_ids, effects.context_ids)
    assert observed == ([], [], []), (
        f"dry_run committed workflow state — steps {effects.workflow_step_ids}, "
        f"mappings {effects.workflow_object_ids}, contexts {effects.context_ids}; these rows are "
        "written outside the creatives transaction, so the preview's rollback never reached them"
    )


def _notification_observation(ctx: dict) -> tuple[tuple[list[str], list[str]], int]:
    """What an independent connection saw at Slack-notification time.

    Fails loudly rather than returning a default when nothing was observed: a
    scenario that forgot the observer Given, or a request that never notified,
    must redden here rather than silently grading None.
    """
    env = ctx["env"]
    rows = env.workflow_rows_at_notification
    count = env.assignment_count_at_notification
    assert rows is not None and count is not None, (
        "no Slack notification was observed — the scenario must bind 'the effects escaping the "
        "sync transaction are observed as they fire' before the When step, and the request must "
        "actually notify"
    )
    return rows, count


@then("the workflow steps the request committed were already visible when Slack was notified")
def then_workflow_steps_visible_at_notification(ctx: dict) -> None:
    """The notification must not overtake the steps it tells a human to open.

    Compares two reads of the same rows: what an INDEPENDENT connection could
    see at the instant _send_creative_notifications was entered, and what the
    finished request left committed. Equality is the whole invariant -- the
    person following the Slack link opens exactly the state that connection
    could see, so any difference is a step the message names and nobody can
    find. A flush without a commit yields an empty read here, so the assertion
    cannot hold vacuously.
    """
    _assert_success_response(ctx)
    rows, _count = _notification_observation(ctx)
    effects = ctx["env"].committed_sync_effects()
    expected_ids = _synced_creative_ids(ctx)
    assert effects.workflow_object_ids == expected_ids, (
        f"the request committed no workflow mapping for {expected_ids} "
        f"(got {effects.workflow_object_ids}), so comparing the notification-time read against it "
        "would prove nothing"
    )
    assert rows == (effects.workflow_step_ids, effects.workflow_object_ids), (
        f"Slack was notified about workflow state that was not committed yet: at notification time "
        f"an independent connection saw {rows}, the request finally committed "
        f"{(effects.workflow_step_ids, effects.workflow_object_ids)}"
    )


@then("no creative assignment was committed when Slack was notified")
def then_no_assignment_committed_at_notification(ctx: dict) -> None:
    """The approval notification precedes the assignment stage.

    Once the workflow-step write joins the creatives transaction, the
    notification is an after_commit effect draining at ``stack.close()`` — which
    is BEFORE _process_assignments runs. Today it fires at the end of the impl,
    after that stage has committed its rows, so this reads a non-zero count.
    Graded together with the step visibility above: one observation, taken at
    one instant, that reddens if either ordering regresses.
    """
    _assert_success_response(ctx)
    _rows, count = _notification_observation(ctx)
    assert count == 0, (
        f"{count} creative assignment(s) were already committed when Slack was notified — the "
        "approval notification is an effect of the creatives commit and must drain before the "
        "assignment stage runs"
    )


@then("the assignment the request made is committed")
def then_assignment_is_committed(ctx: dict) -> None:
    """Non-vacuity control for the ordering assertion above.

    Without it, "no assignment at notification time" also holds for a request
    that never created one — i.e. it would grade nothing.
    """
    _assert_success_response(ctx)
    committed = ctx["env"].committed_sync_effects().assignment_count
    assert committed == 1, (
        f"expected the request to leave exactly 1 committed creative assignment, got {committed}; "
        "without one the notification-time count of 0 proves nothing"
    )


@then("every committed creative awaiting approval has a committed workflow step")
def then_no_orphan_creative_awaiting_approval(ctx: dict) -> None:
    """GH #1987: no creative left in the queue unqueued.

    A creative committed at ``pending_review`` is a promise that a human will be
    asked to review it, and the workflow step is the only thing that asks. When
    _process_assignments raises in strict mode the creatives are already
    committed (``if not dry_run: stack.close()``) while the workflow-step call
    sits after the raise — so the promise is committed and the queue entry never
    written. Once the steps join the creatives' transaction they commit in the
    same ``stack.close()``, before the raise, and the two cannot diverge.
    """
    effects = ctx["env"].committed_sync_effects()
    awaiting = effects.creatives_awaiting_approval
    expected_ids = _synced_creative_ids(ctx)
    # Non-vacuity: the require-human tenant must actually have committed the
    # creative before the raise. If it did not, there is no orphan to look for
    # and the mapping comparison below would hold over two empty sets.
    assert awaiting == expected_ids, (
        f"expected the strict-mode failure to leave {expected_ids} committed at pending_review, "
        f"got {awaiting}; without a committed creative there is no orphan to grade"
    )
    assert sorted(set(effects.workflow_object_ids) & set(awaiting)) == awaiting, (
        f"creatives {sorted(set(awaiting) - set(effects.workflow_object_ids))} were committed "
        "awaiting approval with no workflow step naming them — the assignment failure aborted "
        "before the steps were written, but after the creatives were committed (GH #1987)"
    )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / THEN steps — idempotency_key BEHAVIOR (replay + conflict)
#
# AdCP 3.1.1 dist/compliance/3.1.1/universal/idempotency.yaml: a replay with the
# same key and an equivalent payload "returns the cached response without
# re-executing resource mutations"; the same key with a materially different
# payload rejects with IDEMPOTENCY_CONFLICT. sync_creatives' pinned request
# schema marks idempotency_key REQUIRED, but the universal storyboard has no
# `task: sync_creatives` step at 3.1.1 — these scenarios are the obligation's
# only grading for this tool.
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('that creative was already synced with idempotency_key "{key}"'))
def given_creative_already_synced_with_key(ctx: dict, key: str) -> None:
    """Perform the FIRST sync under *key*, through the scenario's own transport.

    The retry the When step issues must be indistinguishable from a network
    retry of THIS call, so the first call goes through the same
    ``dispatch_request`` path with the same payload rather than being seeded
    behind production's back.

    The first sync is asserted to have succeeded: a Given that silently failed
    would leave the retry looking like a first call, and every Then in the
    scenario would pass vacuously. The pre-retry approval-workflow-step count is
    stashed so the re-execution assertion has an exact baseline.
    """
    ctx["idempotency_key"] = key
    when_sync_creative(ctx)

    assert ctx.get("error") is None, f"the first sync under idempotency_key {key!r} failed: {ctx['error']}"
    first = payload_or_none(ctx)
    assert first is not None, f"the first sync under idempotency_key {key!r} returned no response"
    assert [_action_str(c.action) for c in first.creatives] == ["created"], (
        "the first sync must CREATE the creative so the retry has something to replay; got "
        f"{[_action_str(c.action) for c in first.creatives]}"
    )

    ctx["workflow_steps_before_retry"] = len(ctx["env"].get_workflow_steps())


@given(parsers.parse('the creative name is changed to "{name}"'))
def given_creative_name_changed(ctx: dict, name: str) -> None:
    """Materially change the pending creative payload, keeping the same key.

    This is the spec's "same key, materially different payload" input: the key
    the buyer reuses no longer describes the request it originally identified.
    """
    creatives = ctx.get("creatives")
    assert creatives, "no creative payload to modify — the creative Given must run first"
    creatives[0]["name"] = name


@then("the per-creative result should carry no changes list")
def then_no_changes_list(ctx: dict) -> None:
    """A verbatim replay returns the ORIGINAL result, which recorded no changes.

    ``changes`` is populated only when the sync actually re-wrote fields
    (sync-creatives-response.json: "Field names that were modified (only present
    when action='updated')"), so a NON-EMPTY list on the retry is the seller
    admitting it re-executed the write.

    Empty, not None, is the spec-valid "nothing was re-written" value here:
    ``SyncCreativeResult`` pins ``changes`` to ``list[str]`` with
    ``default_factory=list`` on purpose — spec 3.1.1 types it ``array``, and a
    None default serializes to the spec-invalid ``null`` on MCP (see the comment
    on that field). Asserting ``is None`` was unsatisfiable by construction.
    """
    response = payload_or_none(ctx)
    assert response is not None, "expected a sync_creatives response payload"
    assert response.creatives, "expected at least one per-creative result"
    changes = [c.changes for c in response.creatives]
    assert not any(changes), (
        f"a replayed sync must record no field changes; got {changes} — the retry re-wrote the creative"
    )


@then("no additional creative approval workflow step should have been created")
def then_no_additional_workflow_step(ctx: dict) -> None:
    """The retry executed NO resource mutation.

    Each executed sync_creatives under the tenant's require-human approval mode
    mints an approval workflow step. A second step for one buyer intent is the
    concrete double-execution the idempotency contract forbids.
    """
    before = ctx.get("workflow_steps_before_retry")
    assert before is not None, "baseline workflow-step count missing — the first-sync Given must run first"
    after = len(ctx["env"].get_workflow_steps())
    assert after == before, (
        f"the retry minted {after - before} additional approval workflow step(s) "
        f"({before} -> {after}) — the mutation re-executed instead of replaying"
    )
