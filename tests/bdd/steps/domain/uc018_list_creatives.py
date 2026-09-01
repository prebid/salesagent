"""UC-018 ``list_creatives`` step definitions.

Registered LOCALLY by ``tests/bdd/test_uc018_list_creatives.py`` (``from ... import *``),
NOT via ``conftest.pytest_plugins`` — the uc019 pattern. Two of these step texts collide
with globally-registered steps: ``the Buyer is authenticated as principal "…"``
(``uc003_ext_error_scenarios``) and the parameterized ``the response should be schema-valid
against …`` (``uc005_format_id_roundtrip``'s literal for a different file). Global
registration would put both at plugin scope and let plugin order silently clobber UC-005's
roundtrip scenario. Module-scoped (local-import) registration keeps every step resolving for
UC-018 scenarios only — the blast radius the module docstring in the test file describes.

Living under ``tests/bdd/steps/`` puts these steps inside the directory every BDD structural
guard scans (wire-discipline, assertion-strength, no-response-subscript, …), so they are held
to the same bar as the other domain step modules — the exemption the old inline location had is
gone. Reachability is documented in ``test_architecture_bdd_step_module_reachability.py``'s
intentional-local allowlist alongside uc019.

The scenario/wiring rationale (which scenarios are wired, spec anchors, why creatives are
factory-seeded rather than synced) lives in the test module's docstring next to ``scenarios()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_dict, wire_field
from tests.bdd.steps.generic._auth import authenticate_env_as
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.helpers.pinned_schema import validate_against_pinned_schema

if TYPE_CHECKING:
    from src.core.database.models import Creative, Principal, Tenant

# Three genuinely-different formats (display / video / audio) for the "three
# different formats" precondition. All three are in the standard format registry:
# the A2A path round-trips format_id through a string and re-validates it against
# known formats, so an unregistered id would be rejected on that transport.
_SYNCED_FORMATS = ("display_300x250", "video_640x480", "audio_30s")


def _seed_creative(
    tenant: Tenant,
    principal: Principal,
    fmt: str | None = None,
    *,
    status: str = "approved",
    concept_id: str | None = None,
    concept_name: str | None = None,
) -> Creative:
    """Seed one creative owned by *principal* in *status*, optionally concept-tagged.

    The format/concept-aware single-insert for this module (the list-all, concept, and
    isolation Givens). ``status`` defaults to ``"approved"`` (what those callers want).
    CreativeFactory's realistic default ``assets`` already satisfy the repository's
    ``data["assets"] IS NOT NULL`` guard, so an empty ``{"assets": {}}`` is unnecessary.
    When a concept is given, its ``concept_id`` / ``concept_name`` are layered onto those
    realistic assets in this one merge site.

    The status-only distribution seed (the @BR-RULE-146 statuses scenarios) goes through
    the ``CreativeListEnv.seed_creatives_in_statuses`` env capability instead, whose
    in-process body is the shared ``seed_creative_in_status`` primitive — so the statuses
    family seeds through one surface across BDD and the integration tests. This helper and
    that primitive both bottom out on ``CreativeFactory``, so a factory-default change
    reaches both.
    """
    from tests.factories import CreativeFactory
    from tests.factories.creative_asset import build_assets, image_spec

    kwargs: dict[str, Any] = {"tenant": tenant, "principal": principal, "status": status}
    if fmt is not None:
        kwargs["format"] = fmt
    if concept_id or concept_name:
        data: dict[str, Any] = {"assets": build_assets(image_spec("banner"))}
        if concept_id:
            data["concept_id"] = concept_id
        if concept_name:
            data["concept_name"] = concept_name
        kwargs["data"] = data
    return CreativeFactory(**kwargs)


# ── Given ────────────────────────────────────────────────────────────


def _get_or_create_tenant_and_principal(env: Any) -> tuple[Tenant, Principal]:
    """Idempotently seed the env's tenant + principal (shared e2e_rest DB).

    Rationale on ``get_or_create`` (jdy1-M3, #1418): a prior e2e_rest scenario's
    rows survive in the live-server DB, so plain factory inserts UniqueViolate.
    """
    from src.core.database.models import Principal, Tenant
    from tests.factories import PrincipalFactory, TenantFactory
    from tests.factories.core import get_or_create

    tenant = get_or_create(
        env,
        Tenant,
        {"tenant_id": env._tenant_id},
        lambda: TenantFactory(tenant_id=env._tenant_id),
    )
    principal = get_or_create(
        env,
        Principal,
        {"tenant_id": env._tenant_id, "principal_id": env._principal_id},
        lambda: PrincipalFactory(tenant=tenant, principal_id=env._principal_id),
    )
    return tenant, principal


def _fresh_tenant(env: Any, prefix: str) -> Tenant:
    """Create a fresh uniquely-named tenant, switch *env* to it, and return it.

    Scenarios that pin exact counts (the @BR-RULE-034 isolation invariants and the
    @BR-RULE-146 statuses invariants) each seed into their own tenant so a survivor
    from a prior e2e_rest scenario in a shared tenant can't leak into the count. The
    uuid suffix keeps the tenant_id unique across scenarios; ``switch_tenant`` clears
    the identity cache so the next auth resolves the buyer against the new tenant.
    """
    from uuid import uuid4

    from tests.factories import TenantFactory

    tenant = TenantFactory(tenant_id=f"{prefix}_{uuid4().hex[:8]}")
    env.switch_tenant(tenant.tenant_id)
    return tenant


@given(parsers.parse('the Buyer is authenticated as principal "{principal_id}"'))
def given_buyer_authenticated_as_principal(ctx: dict, principal_id: str) -> None:
    """Authenticate the listing buyer as *principal_id* (Background).

    Uses the shared ``authenticate_env_as`` helper (which clears the identity cache and
    switches the env's principal) so list_creatives is principal-scoped to this buyer,
    and records the principal so the seed steps own their creatives under the same id
    the query authenticates as (list_creatives is principal-scoped — a mismatch returns
    an empty library).

    The helper owns the switch, the canonical ``ctx["principal_id"]``, and the
    identity post-condition. Scoped to UC-018 (local registration), so it does not
    compete with uc003_ext_error_scenarios' identical-text step for other UCs' scenarios.
    """
    authenticate_env_as(ctx, principal_id)


@given("the buyer recently synced three creatives in three different formats via sync_creatives")
def given_recently_synced_three_creatives(ctx: dict) -> None:
    """Seed three approved creatives (one per format) owned by the authenticated buyer.

    Seeded via CreativeFactory rather than a live sync_creatives call — see the
    test module docstring. Records the synced creative_ids for the Then steps.
    """
    env = ctx["env"]
    tenant, principal = _get_or_create_tenant_and_principal(env)
    synced_ids = [_seed_creative(tenant, principal, fmt).creative_id for fmt in _SYNCED_FORMATS]
    ctx["tenant"] = tenant
    ctx["synced_creative_ids"] = synced_ids


# ── When ─────────────────────────────────────────────────────────────


def _dispatch_unfiltered(ctx: dict) -> None:
    """Dispatch list_creatives with no filter kwargs through the scenario's transport.

    The unfiltered-listing dispatch shared by UC-018's two no-filter Whens (list-all and
    the isolation re-auth When). Routes through the canonical generic ``dispatch_request``
    (stashes the typed ``ctx["result"]`` so Then steps read the wire through the single
    guarded accessor, plus the flat ``response``/``wire_response``/``error``); it raises on
    a missing/unrecognized transport rather than falling back to IMPL (sunset in BDD).
    Named so the two callers share one body — and so this module does not add a third bare
    ``dispatch_request(ctx)`` step body (uc004/uc011 already have two; the
    no-duplicate-steps guard's threshold is three).
    """
    dispatch_request(ctx)


@when("the Buyer Agent sends list_creatives with no filters for the same account")
def when_list_creatives_no_filters(ctx: dict) -> None:
    """Dispatch list_creatives with no filters through the scenario's transport."""
    _dispatch_unfiltered(ctx)


# ── Then ─────────────────────────────────────────────────────────────


@then(parsers.parse("the response should be schema-valid against {schema_file}"))
def then_response_schema_valid(ctx: dict, schema_file: str) -> None:
    """Assert the on-the-wire response validates against the pinned AdCP schema.

    Reads the real serialized wire via the single guarded accessor (``wire_dict``) so
    the schema is graded against the bytes the buyer receives, not a re-serialization
    of the typed payload.
    """
    validate_against_pinned_schema(schema_file, wire_dict(ctx))


@then("the creatives array should include each of the synced creatives")
def then_creatives_include_synced(ctx: dict) -> None:
    """Assert every creative_id seeded by the Given is present in the library."""
    expected = set(ctx["synced_creative_ids"])
    returned = {entry["creative_id"] for entry in wire_field(ctx, "creatives")}
    missing = expected - returned
    assert not missing, (
        f"synced creatives missing from the list_creatives library: {sorted(missing)}; "
        f"returned creative_ids: {sorted(returned)}"
    )


@then("each creative entry should expose creative_id, name, format_id, and status")
def then_each_creative_exposes_core_fields(ctx: dict) -> None:
    """Assert every entry carries the four core fields, format_id as a {agent_url, id} object."""
    creatives = wire_field(ctx, "creatives")
    assert creatives, "list_creatives returned an empty creatives array"
    for entry in creatives:
        for field in ("creative_id", "name", "format_id", "status"):
            assert field in entry, f"creative entry missing {field!r}: {entry}"
            assert entry[field] not in (None, "", {}), f"creative entry has empty {field!r}: {entry}"
        # v3.1 federation contract: format_id is an object carrying agent_url + id.
        fid = entry["format_id"]
        assert isinstance(fid, dict) and fid.get("agent_url") and fid.get("id"), (
            f"format_id must be an object with agent_url and id, got: {fid!r}"
        )


# ── @concept-id storyboard scenario (#1407) ─────────────────────────────
#
# v3.1 ADDED filters.concept_ids (array of concept-id strings, minItems 1).
# Concepts group related creatives across sizes and formats; each returned
# creative exposes concept_id and concept_name. Source obligation: adcp
# creative/list-creatives-request.json + core/creative-filters.json (concept_ids)
# and creative/list-creatives-response.json (creatives[].concept_id/concept_name),
# at the module's AdCP 3.1.1 schema anchor. The concept identifier/name live on the creative's JSON data
# blob (no native sync_creatives field in adcp 5.7.0 — concepts originate from
# external creative-management systems), so they are seeded directly.

# Human-readable label paired with the target concept_id, asserted non-empty by
# the Then step. Two registered formats give the concept creatives genuinely
# different sizes/formats (the point of a concept); the A2A path re-validates
# format_id against the registry, so unregistered ids would be rejected there.
_CONCEPT_NAME = "Summer 2026 Campaign"
_CONCEPT_FORMATS = ("display_300x250", "video_640x480")
_DECOY_CONCEPT_ID = "concept_winter_2025"


@given(
    parsers.parse(
        'the authenticated principal has creatives grouped under concept "{concept_id}" '
        "and other creatives under different concepts"
    )
)
def given_creatives_grouped_under_concept(ctx: dict, concept_id: str) -> None:
    """Seed concept-tagged creatives plus decoys so the filter is falsifiable.

    Under the target concept: two approved creatives in two formats (concepts span
    sizes/formats). Decoys: one under a different concept, one with no concept at
    all. A broken filter that returned the whole library would surface a decoy
    whose concept_id != the requested one (or is absent), failing the Then steps.

    Seeded via ``_seed_creative`` rather than a live sync (CreativeListEnv has no
    sync patches; the obligation under test is the listing/filter contract). The
    helper supplies the factory's realistic default ``assets`` (the repository drops
    rows whose ``data["assets"]`` IS NULL) and layers the concept fields on top.
    """
    env = ctx["env"]
    tenant, principal = _get_or_create_tenant_and_principal(env)

    in_concept_ids = [
        _seed_creative(tenant, principal, fmt, concept_id=concept_id, concept_name=_CONCEPT_NAME).creative_id
        for fmt in _CONCEPT_FORMATS
    ]

    # Decoy under a different concept.
    _seed_creative(
        tenant,
        principal,
        _CONCEPT_FORMATS[0],
        concept_id=_DECOY_CONCEPT_ID,
        concept_name="Winter 2025 Campaign",
    )
    # Decoy with no concept at all.
    _seed_creative(tenant, principal, _CONCEPT_FORMATS[0])

    ctx["tenant"] = tenant
    ctx["concept_id"] = concept_id
    ctx["in_concept_creative_ids"] = in_concept_ids


def _dispatch_structured_filter(ctx: dict, field: Literal["concept_ids", "statuses"], bracketed: str) -> list[str]:
    """Parse a bracketed ``"a", "b"`` list from step text, dispatch list_creatives with
    ``filters.<field>`` set to it through the scenario's transport, and return the values.

    Both structured-filter When steps (concept_ids, statuses) are the same operation with
    only the field name substituted: parse the quoted values, build a validated
    ``CreativeFilters`` dict (so minItems / enum validation runs client-side), and forward
    it as a dict — the one shape that coerces back to CreativeFilters uniformly across
    a2a/mcp/rest server-side (FastMCP TypeAdapter / A2A skill / REST body; IMPL is sunsetted
    in BDD). Dispatched through the canonical ``dispatch_request`` (stashes ``ctx["result"]``).

    NOTE: the filter is built client-side through ``CreativeFilters``, so a bracketed value
    that violates the schema (the gated boundary rows ``["deleted"]`` / ``[]``) raises a
    Pydantic error HERE rather than producing a wire ``VALIDATION_ERROR``. Wiring those rows
    (#1652) needs a dict-passthrough dispatch that skips this client-side build, not this
    helper.
    """
    import re

    from adcp import CreativeFilters

    values = re.findall(r'"([^"]+)"', bracketed)
    assert values, f"no {field} values parsed from {bracketed!r}"
    filters = CreativeFilters(**{field: values}).model_dump(mode="json", exclude_none=True)
    dispatch_request(ctx, filters=filters)
    return values


@when(parsers.re(r"the Buyer Agent sends list_creatives with filters\.concept_ids \[(?P<concept_list>.+)\]"))
def when_list_creatives_concept_ids(ctx: dict, concept_list: str) -> None:
    """Dispatch list_creatives with a structured filters.concept_ids filter (see
    ``_dispatch_structured_filter``). Records the requested ids for the Then steps."""
    ctx["requested_concept_ids"] = _dispatch_structured_filter(ctx, "concept_ids", concept_list)


@then(parsers.parse('the creatives array should only include creatives belonging to concept "{concept_id}"'))
def then_only_creatives_in_concept(ctx: dict, concept_id: str) -> None:
    """Assert every returned creative belongs to the requested concept (and the set is non-empty)."""
    creatives = wire_field(ctx, "creatives")
    assert creatives, f"list_creatives returned no creatives for concept {concept_id!r}"
    offenders = [
        {"creative_id": entry.get("creative_id"), "concept_id": entry.get("concept_id")}
        for entry in creatives
        if entry.get("concept_id") != concept_id
    ]
    assert not offenders, f"concept_ids filter leaked creatives outside concept {concept_id!r}: {offenders}"
    # Falsifiability anchor: the seeded in-concept creatives are exactly what comes back.
    returned_ids = {entry["creative_id"] for entry in creatives}
    assert returned_ids == set(ctx["in_concept_creative_ids"]), (
        f"expected exactly the in-concept creatives {sorted(ctx['in_concept_creative_ids'])}, "
        f"got {sorted(returned_ids)}"
    )


@then(parsers.parse('each returned creative should carry concept_id "{concept_id}" and a concept_name'))
def then_each_creative_carries_concept(ctx: dict, concept_id: str) -> None:
    """Assert each returned creative exposes concept_id (== requested) and a non-empty concept_name."""
    creatives = wire_field(ctx, "creatives")
    assert creatives, "list_creatives returned an empty creatives array"
    for entry in creatives:
        assert entry.get("concept_id") == concept_id, (
            f"creative {entry.get('creative_id')!r} concept_id mismatch: {entry}"
        )
        assert entry.get("concept_name"), f"creative {entry.get('creative_id')!r} missing concept_name: {entry}"


# ── @BR-RULE-034 cross-principal isolation scenarios (#1503) ────────────
#
# BR-RULE-034 (P0): list_creatives is principal-scoped — a buyer sees only its own
# creatives, never another principal's, even within the same tenant.
#
# Spec ground (Spec-Grounding Gate): this is an AdCP normative MUST, pinned at
# v3.1-04f59d2d5 — docs/media-buy/advanced-topics/accounts-and-security.mdx §Data
# Isolation (L33-37): a created object is "permanently associated with the account",
# and for any later read "the server MUST verify that the agent has access to that
# account", else it "MUST return a permission denied error". The deeper normative
# reference is docs/building/by-layer/L1/security.mdx §Agent and Account Isolation
# (L159), incl. §"Client-side isolation: cross-principal tool-call confusion" (L229).
# (At the pin the superseded 2.5.3 principals-and-security.mdx was renamed to
# accounts-and-security.mdx; the source docs/ paths resolve at the pin — the built
# dist/docs/3.1.0-beta.3/ tree is only on later commits.) It is ungraded-by-storyboard:
# no conformance storyboard grades multi-principal isolation (universal/security.yaml
# grades authentication, not authenticated isolation), so these two scenarios are the
# ONLY executable guard of that MUST.
#
# Enforcement site: CreativeRepository.get_by_principal's ``principal_id=principal_id``
# filter (src/core/database/repositories/creative.py). Dropping that filter leaks
# the co-tenant principal's rows and fails both scenarios below (INV-1 holds asserts
# an exact-set match; INV-1 counter asserts zero overlap with the other principal).
#
# principal_id is ``Field(exclude=True)`` on the Creative schema, so it never appears
# on the buyer-facing wire. Ownership is therefore verified by matching each returned
# creative_id against the per-principal id sets recorded at seed time — CreativeFactory
# assigns a globally-unique creative_id per row, so the two principals' id sets are
# disjoint and the isolation assertion is well-formed. Assertions read the real
# serialized bytes on a2a/mcp/rest via the guarded ``wire_field`` accessor, satisfying
# the "actual wire bytes" constraint.

_ISOLATION_CREATIVES_KEY = "isolation_creatives_by_principal"


@given(parsers.parse('principal "{principal_id}" has {count:d} creatives'))
@given(parsers.parse('principal "{principal_id}" has {count:d} creatives in the same tenant'))
def given_principal_has_n_creatives(ctx: dict, principal_id: str, count: int) -> None:
    """Seed *count* approved creatives owned by *principal_id* under a fresh tenant.

    Both isolation scenarios seed two principals in ONE tenant — the scenario's
    requirement. WHICH tenant is env plumbing: each scenario gets its own
    uniquely-named tenant (created on the first seed, reused via ctx on the
    second) and the env is re-pointed at it with ``switch_tenant``. Over
    e2e_rest the live-server DB is shared across scenarios, and the sibling
    UC-018 Givens seed creatives for this same buyer — under a shared tenant
    those survivors would leak into the unfiltered list and break the
    exact-count / set-equality assertions (and re-seeding the same
    tenant/principal rows would UniqueViolate). A fresh tenant per scenario
    keeps every assertion at full strength on all transports. Records each
    principal's creative_ids so the Then steps can attribute ownership
    (principal_id is off-wire — see the section comment).

    Two ``@given`` phrasings map to this one body: ``parsers.parse`` requires a
    whole-string match, so the "in the same tenant" variant needs its own decorator.
    """
    from tests.factories import PrincipalFactory

    env = ctx["env"]
    tenant = ctx.get("tenant")
    if tenant is None:
        tenant = _fresh_tenant(env, "uc018_iso")
        ctx["tenant"] = tenant
    principal = PrincipalFactory(tenant=tenant, principal_id=principal_id)
    seeded: dict[str, list[str]] = ctx.setdefault(_ISOLATION_CREATIVES_KEY, {})
    seeded[principal_id] = [_seed_creative(tenant, principal).creative_id for _ in range(count)]


@when(parsers.parse('the Buyer Agent authenticated as "{principal_id}" sends a list_creatives request'))
def when_authenticated_principal_lists_creatives(ctx: dict, principal_id: str) -> None:
    """Authenticate as *principal_id* and dispatch an unfiltered list_creatives.

    Re-authenticates via the shared ``authenticate_env_as`` helper (which clears the
    identity cache) AFTER the seed steps committed the principals, so the next identity
    build resolves the principal's real token from the DB rather than the tokenless
    identity cached during Background (which ran before any principal row existed). On
    MCP/A2A this exercises the full header -> token -> DB-lookup auth chain; REST resolves
    identity via a FastAPI dependency override. Dispatched through the shared
    ``_dispatch_unfiltered`` (canonical ``dispatch_request``: stashes response /
    wire_response / result / error on ctx).
    """
    authenticate_env_as(ctx, principal_id)
    _dispatch_unfiltered(ctx)


def _returned_creative_ids(ctx: dict) -> set[str]:
    """The set of creative_ids in the wire response.

    Ownership is id-based: principal_id is ``Field(exclude=True)`` and never on the
    wire, so a returned creative's owner is identified by which seeded id set its
    creative_id came from.
    """
    return {entry["creative_id"] for entry in wire_field(ctx, "creatives")}


@then(parsers.parse("the response contains exactly {count:d} creatives"))
@then(parsers.parse("the response contains {count:d} creatives"))
def then_response_contains_n_creatives(ctx: dict, count: int) -> None:
    """Assert the wire response carries exactly *count* creatives (all fit on page 1).

    Shared by the @BR-RULE-034 isolation "contains exactly N" (#1503) and the
    @BR-RULE-146 statuses "contains N" (#1502) phrasings — one assertion, two bindings.
    """
    creatives = wire_field(ctx, "creatives")
    assert len(creatives) == count, (
        f"expected {count} creatives, got {len(creatives)}: {sorted(entry.get('creative_id') for entry in creatives)}"
    )


@then(parsers.parse('all creatives belong to principal "{principal_id}"'))
def then_all_creatives_belong_to(ctx: dict, principal_id: str) -> None:
    """Assert the returned creatives are exactly the ones this principal seeded."""
    owned = set(ctx[_ISOLATION_CREATIVES_KEY][principal_id])
    returned = _returned_creative_ids(ctx)
    assert returned, "list_creatives returned an empty creatives array"
    strangers = returned - owned
    assert not strangers, f"creatives not owned by {principal_id!r} leaked into the response: {sorted(strangers)}"
    # Falsifiability anchor: an unscoped query returns MORE than the owner's library.
    assert returned == owned, f"expected exactly {principal_id!r}'s creatives {sorted(owned)}, got {sorted(returned)}"


@then(parsers.parse('none of the returned creatives belong to principal "{principal_id}"'))
def then_none_belong_to(ctx: dict, principal_id: str) -> None:
    """Assert no returned creative belongs to the co-tenant principal (isolation counter)."""
    returned = _returned_creative_ids(ctx)
    assert returned, "isolation counter is vacuous on an empty response (list_creatives returned no creatives)"
    leaked = returned & set(ctx[_ISOLATION_CREATIVES_KEY][principal_id])
    assert not leaked, (
        f"cross-principal leak: creatives owned by {principal_id!r} appeared in the response: {sorted(leaked)}"
    )


# ── @BR-RULE-146 statuses-filter invariants (#1502) ─────────────────────
#
# BR-RULE-146: filters.statuses is a match-any array — a creative is returned iff its
# status is one of the requested statuses. The semantics and their spec grounding are
# stated once at the enforcement site (CreativeRepository.get_by_principal's
# Creative.status.in_(...)); this suite grades the behavior against the pinned schema
# (core/creative-filters.json, statuses) at the module's AdCP 3.1.1 anchor. Wired
# (explicit-statuses success path):
#   inv-146-2-holds     statuses ["archived"]            -> only archived
#   inv-146-2-violated  statuses ["approved"]            -> approved, none archived
#   inv-146-3-holds     statuses ["approved","rejected"] -> match-any, none archived
#
# What grades the #1502 fix: inv-146-3-holds, specifically its "contains 3 creatives" COUNT
# assertion. Pre-fix the multi-value array was narrowed to its first element ["approved"],
# dropping the rejected creative -> 2 returned, not 3, so the count reddens (its "none
# archived" assertion holds either way). The other two pin single-status match-any and pass
# on the unfixed code (a single-element filter was already applied), so they guard the
# semantics, not the regression.
#
# Enforcement site: CreativeRepository.get_by_principal's Creative.status.in_(...), fed by
# _list_creatives_impl.effective_statuses. Reverting the full-list threading (back to
# statuses[0]) reddens inv-146-3-holds's count; the archived/other-status decoys are the
# falsifiable negative controls for the "none/all returned have status" assertions.
#
# Per-scenario tenant isolation (mirrors @BR-RULE-034): the assertions pin exact
# counts under a NON-unique status filter, so a survivor from a prior e2e_rest scenario
# in a shared tenant would break the count. Each scenario seeds into its own fresh
# tenant; the When re-authenticates the buyer under it (seeds now committed).


def _seed_statuses_in_fresh_tenant(ctx: dict, counts: dict[str, int]) -> None:
    """Seed ``{status: count}`` creatives for the Background buyer in a fresh tenant.

    Each statuses scenario has exactly one Given, so the fresh tenant is created
    unconditionally. The assert guards against a future two-Given scenario silently
    re-pointing ``env`` at a second tenant and orphaning the first Given's rows — the
    exact-count Thens would then grade against the wrong data and pass vacuously.

    Delegates the actual seeding to the ``CreativeListEnv.seed_creatives_in_statuses``
    env capability (in-process body: the shared ``seed_creative_in_status`` primitive),
    so BDD and the integration statuses tests seed through ONE surface — the capability
    resolves the fresh tenant's principal from the env identity (get-or-create).
    """
    assert ctx.get("tenant") is None, "statuses seeder expects a single Given per scenario (no pre-existing tenant)"
    env = ctx["env"]
    ctx["tenant"] = _fresh_tenant(env, "uc018_status")
    env.seed_creatives_in_statuses(counts)


@given(
    parsers.re(r"the authenticated principal has (?P<approved>\d+) approved and (?P<archived>\d+) archived creatives?")
)
def given_principal_has_approved_and_archived(ctx: dict, approved: str, archived: str) -> None:
    """Seed N approved + M archived creatives for the Background buyer (fresh tenant)."""
    _seed_statuses_in_fresh_tenant(ctx, {"approved": int(approved), "archived": int(archived)})


@given(
    parsers.re(
        r"the authenticated principal has (?P<approved>\d+) approved, (?P<rejected>\d+) rejected, "
        r"and (?P<archived>\d+) archived creatives?"
    )
)
def given_principal_has_approved_rejected_archived(ctx: dict, approved: str, rejected: str, archived: str) -> None:
    """Seed N approved + M rejected + K archived creatives for the Background buyer (fresh tenant)."""
    _seed_statuses_in_fresh_tenant(
        ctx, {"approved": int(approved), "rejected": int(rejected), "archived": int(archived)}
    )


@given(parsers.re(r"the authenticated principal has creatives in statuses (?P<statuses>.+)"))
def given_principal_has_creatives_in_statuses(ctx: dict, statuses: str) -> None:
    """Seed one creative per quoted status for the Background buyer (fresh tenant).

    Binds the ``Given the authenticated principal has creatives in statuses "a", "b", …``
    phrasing (the @creative-status boundary + @default-query partition outlines) — one
    creative per named status, so an explicit statuses filter over those statuses has a
    falsifiable positive for each and a decoy for every status left out. Reuses the same
    ``seed_creatives_in_statuses`` capability as the count phrasings.
    """
    import re

    named = re.findall(r'"([^"]+)"', statuses)
    assert named, f"no statuses parsed from {statuses!r}"
    _seed_statuses_in_fresh_tenant(ctx, dict.fromkeys(named, 1))


@when(parsers.re(r"the Buyer Agent sends a list_creatives request with statuses filter \[(?P<status_list>[^\]]+)\]"))
def when_list_creatives_statuses_filter(ctx: dict, status_list: str) -> None:
    """Dispatch list_creatives with a structured filters.statuses filter (see
    ``_dispatch_structured_filter``).

    The one distinct line: re-authenticate the buyer under the scenario's fresh tenant
    (seeds now committed) before dispatching — mirrors the isolation When.
    """
    authenticate_env_as(ctx, ctx["principal_id"])
    _dispatch_structured_filter(ctx, "statuses", status_list)


@then(parsers.parse('all returned creatives have status "{status}"'))
def then_all_returned_have_status(ctx: dict, status: str) -> None:
    """Assert every returned creative carries *status* (match-any includes this status)."""
    creatives = wire_field(ctx, "creatives")
    assert creatives, "list_creatives returned an empty creatives array"
    # Subscript ``status`` (not .get): a returned creative that omits status on the wire is
    # a bug the KeyError must surface, not a fail-open pass.
    wrong = [(c.get("creative_id"), c["status"]) for c in creatives if c["status"] != status]
    assert not wrong, f"expected all returned creatives to have status {status!r}, found: {wrong}"


@then(parsers.parse("only {status} creatives are returned"))
def then_only_status_creatives_returned(ctx: dict, status: str) -> None:
    """Assert the returned set is exactly the requested single status (positive + negative in one).

    Binds the ``Then only <status> creatives are returned`` outcome of the @creative-status /
    @default-query single-status rows (post-substitution ``only approved creatives are
    returned`` etc.). Non-empty, every returned creative has *status*, and (since the Given
    seeds one creative per status) nothing of another status leaked. The multi-status boundary
    row's outcome (``only approved and rejected creatives are returned``) is a distinct phrasing,
    not this single-status binding — its wiring is tracked in the F1 follow-up."""
    creatives = wire_field(ctx, "creatives")
    assert creatives, f"list_creatives returned no creatives for status {status!r}"
    wrong = [(c.get("creative_id"), c["status"]) for c in creatives if c["status"] != status]
    assert not wrong, f"expected only status {status!r} creatives, found others: {wrong}"


@then(parsers.parse('none of the returned creatives have status "{status}"'))
def then_none_returned_have_status(ctx: dict, status: str) -> None:
    """Assert no returned creative carries *status* (the excluded-status negative control)."""
    creatives = wire_field(ctx, "creatives")
    assert creatives, "list_creatives returned an empty creatives array"
    # Subscript ``status`` (not .get): Creative.status is not required and the listing path
    # is exclude_none, so a serializer that omitted status would make ``.get(...) == status``
    # silently False and the control pass vacuously. The KeyError surfaces that regression.
    offenders = [c.get("creative_id") for c in creatives if c["status"] == status]
    assert not offenders, f"creatives with status {status!r} leaked into the response: {offenders}"


@then(parsers.parse('filters_applied reports statuses "{statuses}"'))
def then_filters_applied_reports_statuses(ctx: dict, statuses: str) -> None:
    """Assert query_summary.filters_applied echoes the FULL applied statuses list on the wire.

    Grades the REPORTING half of #1502 (report == scoped set) on the wire-authoritative
    scenario, where the scoping half (the "contains N creatives" count) already runs. #1502
    is two coupled defects — the query narrowed the array to statuses[0] AND filters_applied
    echoed the whole array — that only diverge on a multi-value filter. The count assertion
    above grades scoping; this grades reporting: a regression that emits only the first status
    into filters_applied while leaving the query correct reddens HERE on every wire transport
    (a2a/mcp/rest + e2e_rest) and nowhere else in the BDD/e2e suite.

    Reads through the single guarded ``wire_dict`` accessor — a real-wire transport that
    failed to stash the wire trips loudly rather than falling back to a re-serialization,
    so this asserts the real on-the-wire ``filters_applied`` value (a list of ``field=values``
    strings), never a reconstruction.
    """
    filters_applied = wire_dict(ctx)["query_summary"]["filters_applied"]
    assert f"statuses={statuses}" in filters_applied, (
        f"filters_applied must report the full applied statuses list statuses={statuses!r}, got: {filters_applied!r}"
    )
