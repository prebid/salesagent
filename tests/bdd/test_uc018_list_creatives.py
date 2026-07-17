"""BDD scenarios + steps for UC-018: list_creatives library queries.

Binds the UC-018 feature; several scenarios are wired (the rest xfail at the
conftest harness fixture):

- ``T-UC-018-storyboard-list-all-creatives-after-sync`` (#1405): after the buyer
  syncs creatives across formats, ``list_creatives`` with no filters returns the
  account's library — schema-valid against ``list-creatives-response.json``, each
  entry exposing ``creative_id``, ``name``, ``format_id``, ``status``. Source
  obligation: adcp ``protocols/creative/index.yaml`` · ``list_all``.
- ``T-UC-018-storyboard-filter-by-concept-id`` (#1407): ``filters.concept_ids``
  scopes results to a concept; each returned creative exposes ``concept_id`` and
  ``concept_name``. Source: adcp ``creative/list-creatives-request.json`` +
  ``core/creative-filters.json`` (concept_ids) and ``list-creatives-response.json``
  (concept_id/concept_name).

The first two are pinned at v3.1-04f59d2d5 (adcp 3.1.0-beta.3).

- ``T-UC-018-inv-034-1-holds`` / ``T-UC-018-inv-034-1-violated`` (#1503):
  BR-RULE-034 cross-principal isolation — an AdCP normative MUST (v3.1-04f59d2d5:
  accounts-and-security.mdx §Data Isolation; building/by-layer/L1/security.mdx §Agent
  and Account Isolation), ungraded by any conformance storyboard,
  so these two scenarios are its only executable guard. Two principals in one tenant
  each own creatives; a buyer authenticated as one sees exactly its own library (holds)
  and never the other's (counter). Enforced in production by
  ``CreativeRepository.get_by_principal``'s ``principal_id`` filter — dropping it
  leaks the co-tenant principal's rows and fails these scenarios. principal_id is
  ``Field(exclude=True)`` (never on the wire), so ownership is verified by matching
  returned creative_ids to the seeded per-principal id sets. See the section comment
  above those steps for the full spec citation.

Wired to real production across all wire transports (auto-parametrized; UC-018
-> CreativeListEnv via conftest ``_detect_uc`` / ``_harness_env``). The repo
sunsets the IMPL pseudo-transport in BDD, so the scenario runs on a2a/mcp/rest
(plus e2e_rest in-network: this branch's ``RestE2EDispatcher`` stashes the
success-path ``wire_response``, so the isolation Then steps assert real HTTP
bytes there too). Each transport returns the same typed response, and
the Then steps validate its production JSON serialization
(``model_dump(mode="json", exclude_none=True)`` — the same NestedModelSerializerMixin
path that produces the on-the-wire bytes); the parametrization still exercises
each dispatch path end to end (a broken transport surfaces as a missing/errored
response).

**Why steps live here (not in steps/domain/ + pytest_plugins):** pytest-bdd 8
resolves step definitions only from the scenario's own module, conftest, or
registered plugins — importing them does not register them. The generic
``schema-valid against <file>`` and ``authenticated as principal`` phrasings are
shared by other, already-wired feature files (UC-004/005/006); registering them
globally would alter those suites. Defining the steps inline scopes them to this
one scenario, keeping the blast radius to UC-018. The reusable, non-step schema
validator lives in ``tests.helpers.pinned_schema``.

The "synced" creatives are seeded via ``CreativeFactory`` rather than a live
``sync_creatives`` call: ``CreativeListEnv`` mocks only the audit logger (it has
none of sync's creative-agent / preview-generation patches), and the obligation
under test is ``list_all`` — the listing contract, not the sync path. The
creatives land in the same DB row shape sync would persist, so the listing query
is exercised faithfully.
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, scenarios, then, when

from tests.bdd.steps._outcome_helpers import _require_response
from tests.bdd.steps.generic._auth import authenticate_env_as
from tests.harness.transport import Transport
from tests.helpers.pinned_schema import validate_against_pinned_schema

# Three genuinely-different formats (display / video / audio) for the "three
# different formats" precondition. All three are in the standard format registry:
# the A2A path round-trips format_id through a string and re-validates it against
# known formats, so an unregistered id would be rejected on that transport.
_SYNCED_FORMATS = ("display_300x250", "video_640x480", "audio_30s")

# Bind the UC-018 feature. The wired scenarios are @list-after-sync (#1405),
# @concept-id (#1407), and the @BR-RULE-034 isolation invariants (#1503); the
# remaining scenarios xfail fast at the conftest _harness_env fixture. Whole-feature
# binding via scenarios() is the repo convention the CI shard-splitter requires
# (scripts/ci/shard_split.py).
scenarios("features/BR-UC-018-list-creatives.feature")


def _seed_creative(
    tenant: Any,
    principal: Any,
    fmt: str | None = None,
    *,
    concept_id: str | None = None,
    concept_name: str | None = None,
) -> Any:
    """Seed one approved creative owned by *principal*, optionally concept-tagged.

    The single place this module assembles a creative: the ``approved`` trait
    supplies ``status="approved"`` and CreativeFactory's realistic default ``assets``
    (which already satisfy the repository's ``data["assets"] IS NOT NULL`` guard — an
    empty ``{"assets": {}}`` is unnecessary). When a concept is given, its
    ``concept_id`` / ``concept_name`` are layered onto those realistic assets in this
    one merge site. Replaces the per-seeder ``status=`` + ``data={"assets": {}}``
    hand-rolls with a single factory idiom.
    """
    from tests.factories import CreativeFactory
    from tests.factories.creative_asset import build_assets, image_spec

    kwargs: dict[str, Any] = {"tenant": tenant, "principal": principal, "approved": True}
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


def _get_or_create_tenant_and_principal(env: Any) -> tuple[Any, Any]:
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


@given(parsers.parse('the Buyer is authenticated as principal "{principal_id}"'))
def given_buyer_authenticated_as_principal(ctx: dict, principal_id: str) -> None:
    """Authenticate the listing buyer as *principal_id* (Background).

    Uses the shared ``authenticate_env_as`` helper (which clears the identity cache and
    switches the env's principal) so list_creatives is principal-scoped to this buyer,
    and records the principal so the seed steps own their creatives under the same id
    the query authenticates as (list_creatives is principal-scoped — a mismatch returns
    an empty library).

    The helper owns the switch, the canonical ``ctx["principal_id"]``, and the
    identity post-condition.
    """
    authenticate_env_as(ctx, principal_id)


@given("the buyer recently synced three creatives in three different formats via sync_creatives")
def given_recently_synced_three_creatives(ctx: dict) -> None:
    """Seed three approved creatives (one per format) owned by the authenticated buyer.

    Seeded via CreativeFactory rather than a live sync_creatives call — see the
    module docstring. Records the synced creative_ids for the Then steps.
    """
    env = ctx["env"]
    tenant, principal = _get_or_create_tenant_and_principal(env)
    synced_ids = [_seed_creative(tenant, principal, fmt).creative_id for fmt in _SYNCED_FORMATS]
    ctx["tenant"] = tenant
    ctx["principal"] = principal
    ctx["synced_creative_ids"] = synced_ids


# ── When ─────────────────────────────────────────────────────────────


@when("the Buyer Agent sends list_creatives with no filters for the same account")
def when_list_creatives_no_filters(ctx: dict) -> None:
    """Dispatch list_creatives with no filters through the scenario's transport.

    Reuses the canonical generic dispatch helper (``env.call_via`` + ctx stash of
    ``response`` / ``wire_response`` / ``error``) rather than re-implementing it.
    No filter kwargs are passed, so the listing runs unfiltered; the helper maps a
    missing transport to IMPL.
    """
    from tests.bdd.steps.generic.when_request import _call_via

    _call_via(ctx, ctx.get("transport"))


# ── Then ─────────────────────────────────────────────────────────────


def _serialized_response(ctx: dict) -> dict[str, Any]:
    """Serialize the typed response through the production serializer (JSON mode).

    list_creatives returns the same typed payload on every transport, so each
    transport's Then steps assert on the same serialized document. ``mode="json"``
    drives ``NestedModelSerializerMixin`` — the same serializer that produces the
    on-the-wire bytes (format_id -> {agent_url, id}, datetimes -> ISO strings).
    ``exclude_none`` omits unset optional fields (format_summary, status_summary,
    sandbox, errors, ext, context), matching the buyer-visible REST wire and the
    AdCP contract, which type those fields only when present (a literal ``null``
    is not a valid array/object/boolean).

    The 4-transport parametrization still exercises each dispatch path end to end:
    a broken transport surfaces as a missing/errored ``ctx["response"]`` here.
    """
    return _require_response(ctx).model_dump(mode="json", exclude_none=True)


@then(parsers.parse("the response should be schema-valid against {schema_file}"))
def then_response_schema_valid(ctx: dict, schema_file: str) -> None:
    """Assert the serialized response validates against the pinned AdCP schema."""
    validate_against_pinned_schema(schema_file, _serialized_response(ctx))


@then("the creatives array should include each of the synced creatives")
def then_creatives_include_synced(ctx: dict) -> None:
    """Assert every creative_id seeded by the Given is present in the library."""
    expected = set(ctx["synced_creative_ids"])
    returned = {entry["creative_id"] for entry in _serialized_response(ctx)["creatives"]}
    missing = expected - returned
    assert not missing, (
        f"synced creatives missing from the list_creatives library: {sorted(missing)}; "
        f"returned creative_ids: {sorted(returned)}"
    )


@then("each creative entry should expose creative_id, name, format_id, and status")
def then_each_creative_exposes_core_fields(ctx: dict) -> None:
    """Assert every entry carries the four core fields, format_id as a {agent_url, id} object."""
    creatives = _serialized_response(ctx)["creatives"]
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
# pin v3.1-04f59d2d5. The concept identifier/name live on the creative's JSON data
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
    ctx["principal"] = principal
    ctx["concept_id"] = concept_id
    ctx["in_concept_creative_ids"] = in_concept_ids


@when(parsers.re(r"the Buyer Agent sends list_creatives with filters\.concept_ids \[(?P<concept_list>.+)\]"))
def when_list_creatives_concept_ids(ctx: dict, concept_list: str) -> None:
    """Dispatch list_creatives with a structured filters.concept_ids filter.

    Parses the bracketed concept-id list from the step text and dispatches the
    structured filter through the scenario's transport via the canonical helper.
    The filter travels as a JSON dict (built through CreativeFilters so minItems/
    field validation runs); each wire transport coerces it back to CreativeFilters
    server-side (FastMCP TypeAdapter / A2A skill / REST body), so a dict is the one
    shape that works uniformly across a2a/mcp/rest (IMPL is sunsetted in BDD).
    """
    import re

    from adcp import CreativeFilters

    from tests.bdd.steps.generic.when_request import _call_via

    concept_ids = re.findall(r'"([^"]+)"', concept_list)
    assert concept_ids, f"no concept ids parsed from {concept_list!r}"
    ctx["requested_concept_ids"] = concept_ids
    filters = CreativeFilters(concept_ids=concept_ids).model_dump(mode="json", exclude_none=True)
    _call_via(ctx, ctx.get("transport"), filters=filters)


def _wire_creatives(ctx: dict) -> list[dict[str, Any]]:
    """Return the creatives array as the buyer sees it on the wire.

    REST/A2A/MCP stash the real serialized response on ``ctx["wire_response"]``
    (CreativeListEnv stashes on all three wire transports), so the concept-field
    assertions check the actual on-the-wire bytes rather than a re-serialization.
    Falls back to the production serializer only when no wire was captured (e.g. a
    non-stashing path), so the step still has data to assert on.
    """
    wire = ctx.get("wire_response")
    transport = ctx.get("transport")
    # Loud guard (mirrors uc005_format_id_shape): a real-wire transport (a2a/mcp/rest/
    # e2e_rest) that didn't stash wire_response must trip here, not silently fall back
    # to a model_dump re-serialization and undercut the "real wire bytes" claim. IMPL
    # (and the unparametrized None default) legitimately have no wire.
    if wire is None and transport not in (None, Transport.IMPL):
        raise AssertionError(f"{transport}: wire_response missing — env does not stash success-path wire")
    if wire is not None:
        return wire["creatives"]
    return _serialized_response(ctx)["creatives"]


@then(parsers.parse('the creatives array should only include creatives belonging to concept "{concept_id}"'))
def then_only_creatives_in_concept(ctx: dict, concept_id: str) -> None:
    """Assert every returned creative belongs to the requested concept (and the set is non-empty)."""
    creatives = _wire_creatives(ctx)
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
    creatives = _wire_creatives(ctx)
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
# disjoint and the isolation assertion is well-formed. Assertions read
# ctx["wire_response"] (the real serialized bytes on a2a/mcp/rest) via _wire_creatives,
# satisfying the "actual wire bytes" constraint.

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
    from uuid import uuid4

    from tests.factories import PrincipalFactory, TenantFactory

    env = ctx["env"]
    tenant = ctx.get("tenant")
    if tenant is None:
        tenant_id = f"uc018_iso_{uuid4().hex[:8]}"
        tenant = TenantFactory(tenant_id=tenant_id)
        env.switch_tenant(tenant_id)
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
    identity via a FastAPI dependency override. Reuses the canonical generic dispatch
    helper (``_call_via`` stashes response / wire_response / error on ctx).
    """
    from tests.bdd.steps.generic.when_request import _call_via

    authenticate_env_as(ctx, principal_id)
    _call_via(ctx, ctx.get("transport"))


def _returned_creative_ids(ctx: dict) -> set[str]:
    """The set of creative_ids in the wire response.

    Ownership is id-based: principal_id is ``Field(exclude=True)`` and never on the
    wire, so a returned creative's owner is identified by which seeded id set its
    creative_id came from.
    """
    return {entry["creative_id"] for entry in _wire_creatives(ctx)}


@then(parsers.parse("the response contains exactly {count:d} creatives"))
def then_response_contains_exactly_n_creatives(ctx: dict, count: int) -> None:
    """Assert the wire response carries exactly *count* creatives (all fit on page 1)."""
    creatives = _wire_creatives(ctx)
    assert len(creatives) == count, (
        f"expected exactly {count} creatives, got {len(creatives)}: "
        f"{sorted(entry.get('creative_id') for entry in creatives)}"
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
