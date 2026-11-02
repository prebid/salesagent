"""BDD scenarios + steps for UC-018: list_creatives library queries.

Binds the UC-018 feature. There is no longer a "wired subset": the catch-all route that
parked the rest by carrying a reason string is gone, so every scenario of this feature
executes, and one whose step has no binding FAILS with the missing step named rather than
xfailing out of sight. The families that still have no binding are the ones production
cannot yet seed or answer — the delivery snapshot, dynamic-content variables, multi-asset
items, account rate cards for pricing_options, the has_variables / has_served boolean
filters, and cursor traversal — plus the ``fields`` projection, which those rows state in a
way the pinned response schema cannot satisfy (a creative object carrying one field, and
compliance with six REQUIRED members).

Three scenarios have their own notes below because they were the first wired, and their
spec citations are the ones the rest were checked against:

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
registered plugins — importing them does not register them. So a step defined
here is reachable only from this module's scenarios.

Note what that is NOT a licence for. The generic ``schema-valid against <file>``
and ``authenticated as principal`` phrasings are owned by
``tests/bdd/steps/generic/``. Re-registering either sentence here would not
"keep the blast radius small" — it would give one Gherkin sentence two meanings,
with the local, usually weaker, definition silently winning for this file while
every other suite kept the generic one. That is the defect
``test_architecture_bdd_no_shadowed_steps.py`` now fails on, and it is exactly
how UC-005 ended up grading ``isinstance(formats, list)`` under a sentence that
promises full pinned-schema validation.

A step belongs inline only when its SENTENCE is specific to this scenario. When
the behaviour is genuinely UC-018-specific, give it its own wording rather than
narrowing a shared one. The reusable, non-step schema validator lives in
``tests.helpers.pinned_schema``.

The "synced" creatives are seeded via ``CreativeFactory`` rather than a live
``sync_creatives`` call: ``CreativeListEnv`` mocks only the audit logger (it has
none of sync's creative-agent / preview-generation patches), and the obligation
under test is ``list_all`` — the listing contract, not the sync path. The
creatives land in the same DB row shape sync would persist, so the listing query
is exercised faithfully.

**Corrupt-blob coercion reconciliation (#1508):** ``list_creatives`` drops a corrupt
``tags``/``assets`` blob value to absent, and collapses a stored empty ``tags`` list to
omission (both conformant at 3.1.1 — the schema permits ``[]`` and absent for ``tags``,
``{}`` and absent for ``assets``, ``null`` for neither). An "all fields are present" Then
therefore asserts value-when-present over a library seeded WITH those values, never
key-presence of the 13 enum members: a creative with empty or absent tags legitimately
omits the key, and a seller holding no snapshot for a creative omits that too. The
coercion itself is graded on real wire bytes across a2a/mcp/rest in
``tests/integration/test_list_creatives_concept_filter.py``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

from pytest_bdd import given, parsers, scenarios, then, when

from tests.bdd.steps._outcome_helpers import error_envelope_or_none, require_payload, wire_dict, wire_field
from tests.bdd.steps.generic._auth import authenticate_env_as
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.helpers.envelope_assertions import assert_envelope_shape
from tests.helpers.pinned_schema import validator_for

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
    tags: Sequence[str] | None = None,
    agent_url: str | None = None,
    **overrides: Any,
) -> Any:
    """Seed one approved creative owned by *principal*, optionally concept- or tag-bearing.

    The single place this module assembles a creative: the ``approved`` trait
    supplies ``status="approved"`` and CreativeFactory's realistic default ``assets``
    (which already satisfy the repository's ``data["assets"] IS NOT NULL`` guard — an
    empty ``{"assets": {}}`` is unnecessary). When a concept or a tag list is given, the
    corresponding blob keys are layered onto those realistic assets in this one merge
    site. Replaces the per-seeder ``status=`` + ``data={"assets": {}}`` hand-rolls with
    a single factory idiom.

    ``tags`` writes ``data["tags"]`` — where the creative's tags actually live (there is
    no tags column, and list_creatives reads them back from that key). They used to be
    smuggled in as NAME substrings, because the repository matched the ``tags`` filter
    with ``Creative.name.contains``; core/creative-filters.json asks about the creative's
    tags, so the repository now does too and a tag is a tag here.
    """
    from tests.factories import CreativeFactory
    from tests.factories.creative_asset import build_assets, image_spec

    kwargs: dict[str, Any] = {"tenant": tenant, "principal": principal}
    if "status" not in overrides:
        kwargs["approved"] = True
    if fmt is not None:
        kwargs["format"] = fmt
    if agent_url is not None:
        kwargs["agent_url"] = agent_url
    # An explicit name (the ``name_contains`` filter is a name substring match), a
    # non-approved status, and explicit created_at / updated_at so the ordering
    # assertions have distinct, deterministic sort keys instead of every row sharing one
    # server_default timestamp. Layered here rather than in a second seeder so this stays
    # the one place the module assembles a creative.
    for key in ("name", "status", "created_at", "updated_at"):
        if key in overrides:
            kwargs[key] = overrides.pop(key)
    if overrides:
        raise TypeError(f"_seed_creative got unexpected overrides: {sorted(overrides)}")
    if concept_id or concept_name or tags is not None:
        data: dict[str, Any] = {"assets": build_assets(image_spec("banner"))}
        if concept_id:
            data["concept_id"] = concept_id
        if concept_name:
            data["concept_name"] = concept_name
        if tags is not None:
            data["tags"] = list(tags)
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


# 'the Buyer is authenticated as principal "{principal_id}"' is NOT registered here.
# This module used to declare it, which meant the sentence had two definitions — this
# one and the identical parser in steps/domain/uc003_ext_error_scenarios.py (registered
# globally via conftest pytest_plugins). UC-018 silently got the local one and every
# other feature got the plugin one; the two bodies differed only by a ctx["has_auth"]
# flag, so the divergence was invisible until someone diffed them. Deleted so the
# sentence has one meaning. Both call the same authenticate_env_as helper, and the
# extra has_auth flag is read only by a UC-003 step, so nothing here changes.


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
    No filter kwargs are passed, so the listing runs unfiltered; a missing
    transport raises in ``_call_via`` (loud guard — there is no IMPL fallback).
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
    a broken transport surfaces as a missing/errored dispatch payload here.
    """
    return require_payload(ctx).model_dump(mode="json", exclude_none=True)


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
    filters = CreativeFilters(concept_ids=concept_ids).model_dump(mode="json", exclude_none=True)
    _call_via(ctx, ctx.get("transport"), filters=filters)


def _wire_creatives(ctx: dict) -> list[dict[str, Any]]:
    """Return the creatives array as the buyer sees it on the wire.

    Delegates to the canonical :func:`wire_field` guard (GH #1744 collapsed the
    private guard clone this used to carry, and #1858 replaced its last
    remnant). The guard branches on the DECLARATION the dispatcher made
    (``TransportResult.has_wire``) and raises when a declared wire was not
    captured, so the concept-field assertions read the actual on-the-wire bytes
    rather than a re-serialization. This function used to key on transport
    IDENTITY (``transport not in (None, Transport.IMPL)``), which is the
    spelling the shared helper replaced across the suite: a lookup against an
    enum member reclassifies every result the day that member moves, and it made
    this the last site free to drift from the guard the others share.
    """
    return wire_field(ctx, "creatives")


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
# the real serialized bytes on a2a/mcp/rest via _wire_creatives (which reads the
# dispatcher's own wire declaration through wire_field),
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


# ── #1721 lane D: rows whose behavior the transport-seam conversion can delete ──
#
# converts _handle_list_creatives_skill (and the other handlers this
# PR opened) to build the typed request through the shared build_*_request seam, and
# moves the MCP structured->flat sort/pagination coercion into
# ListCreativesRequest. Two families of behavior are silently deletable by
# that conversion, and both were ungraded — the whole UC-018 partition/boundary set
# xfailed fast at the conftest harness gate:
#
#  1. media_buy_id + media_buy_ids merge/dedup. ListCreativesRequest declares NEITHER
#     key (they live on CreativeFilters, adcp 3.1.1 core/creative-filters.json), so
#     the plan's literal prescription — build_X_request(**select_request_fields(
#     ListCreativesRequest, bag)) — drops both. The merge lives in the builder
#     (listing.py:151-156) and the DB join in CreativeRepository.get_by_principal.
#
#  2. The flat-path silent coercions: sort_order outside {asc, desc} -> "desc"
#     (listing.py:126-130) and sort_by outside the field_mapping -> "created_date"
#     (:161-178). If the moved coercion writes the structured Sort object straight
#     onto ListCreativesRequest instead of landing ahead of the flat path, the SDK
#     Sort enum REJECTS those values and the buyer gets a wire error where they used
#     to get a silently-coerced ordering.
#
# Rows in these three outlines that grade behavior this lane does not implement
# (the fields[] projection, max_results/PaginationRequest, assignment_count sorting,
# tag AND/OR semantics) are parked per-row in tests/bdd/conftest.py _SELECTIVE_XFAIL,
# each citing #1721 — per-ROW, so the rows this PR's behavior change touches execute
# while the untouched siblings stay declared rather than silently green.
#
# Spec ground: adcp v3.1.1 dist/schemas/3.1.1/core/creative-filters.json declares
# media_buy_ids (array) with no singular sibling, and
# dist/schemas/3.1.1/creative/list-creatives-request.json carries filters/sort/
# pagination. The singular media_buy_id is this agent's documented backward-compat
# flat param, which is exactly why nothing on the request model protects it.

#: The media buys the filter rows name. Literal ids because the scenarios name them
#: literally; the decoy buys each library needs are spelled by that library's own spec.
_MERGE_MEDIA_BUY_IDS = ("mb1", "mb2")

#: The page size the reader applies when the request carries no pagination object
#: (core/pagination-request.json's default for max_results).
_DEFAULT_PAGE_SIZE = 50


def _seed_media_buy(tenant: Any, principal: Any, media_buy_id: str) -> Any:
    """Seed one media buy with a literal id, via the factory."""
    from tests.factories import MediaBuyFactory

    return MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id=media_buy_id)


def _assign(tenant: Any, creative: Any, media_buy: Any) -> Any:
    """Attach *creative* to *media_buy*; the media_buy_ids filter joins on this row."""
    from tests.factories import CreativeAssignmentFactory

    return CreativeAssignmentFactory(creative=creative, media_buy=media_buy)


@given(parsers.parse("the authenticated principal has {count:d} approved creatives"))
def given_n_approved_creatives(ctx: dict, count: int) -> None:
    """Seed *count* approved creatives with strictly decreasing created_at.

    Each row is one minute older than the previous, so "sorted by created_date
    descending" has a single correct answer and the ordering assertions below can
    compare an exact id sequence rather than "is it sorted by something".
    Names are alphabetically ordered the SAME way, so a step that silently sorted by
    name instead of created_date would still have to explain the coercion rows.
    """
    _seed_library(
        ctx,
        [
            _Spec(
                label=f"paged creative {index:03d}",
                created_at=_ANCHOR - timedelta(minutes=index),
                # Rotating assignment counts, so the library can also answer the
                # assignment_count sort field: over creatives that all carry the same count,
                # an ordering assertion compares a constant list to itself and passes even
                # when the sort field is ignored. The counts do not disturb the created_at
                # ordering the pagination rows assert.
                assignment_count=index % 3,
            )
            for index in range(count)
        ],
    )


# ── When ─────────────────────────────────────────────────────────────


# Every When line in this feature reads "the Buyer Agent sends a list_creatives request
# with <request_params>", where <request_params> is an Examples cell — some sixty distinct
# phrases over one small language. One step function per phrase would be sixty
# near-identical bodies, which is the duplication the repo's DRY invariant forbids, so the
# phrases are a TABLE and the step is one function.
#
# The table is also what the step's own parser is built from: the regex is the alternation
# of these patterns, so a phrase the table cannot translate matches no step at all and the
# scenario reports a missing binding — instead of dispatching a request nobody wrote. The
# patterns therefore carry unnamed groups only: pytest-bdd passes NAMED groups as step
# arguments, and one name may not repeat across an alternation. Each handler re-matches its
# own pattern to read its own groups.
#
# THE VOCABULARY IS THE PINNED REQUEST'S VOCABULARY. 3.1.1 list-creatives-request.json
# declares filters / sort / pagination / fields / account and the include_* flags, and
# nothing flat: no status, tags, sort_by, sort_order, limit, page, max_results or
# media_buy_id at the top level. Cells naming those were corrected in the feature rather
# than translated here — a step that quietly rewrote "flat status" into filters.statuses
# would make the scenario claim to grade a precedence rule the spec does not define.

#: A status outside enums/creative-status.json, for the rows that grade the refusal.
_UNKNOWN_STATUS = "unknown"

#: Reused by the creative_ids maxItems rows: ids no seeded creative carries, so the filter
#: can be sent at exactly the boundary length without changing which creatives match.
_ABSENT_CREATIVE_ID = "absent_creative_{index:04d}"


def _quoted(text: str) -> list[str]:
    """The quoted members of a bracketed Examples list: ``'"a", "b"'`` -> ``["a", "b"]``."""
    return re.findall(r'"([^"]*)"', text)


def _max_results(raw: str) -> object:
    """``pagination.max_results`` as the cell spells it — int, or the literal for a refusal.

    core/pagination-request.json types it integer, minimum 1, maximum 100. A cell carrying
    ``"abc"`` means the buyer sent a non-integer, so it must travel as a string and be
    refused by the seller rather than coerced by the test.
    """
    stripped = raw.strip().strip('"')
    try:
        return int(stripped)
    except ValueError:
        return stripped


def _every_selectable_field(expected_count: int) -> list[str]:
    """The whole ``fields`` enum, READ OFF the pinned request schema.

    A row that says "all 13 enum values" is asking for every member there is, so the list
    comes from list-creatives-request.json rather than being copied into the feature file
    or into this module — two places that would then have to be updated when the enum
    grows, and would silently under-select until someone did. The count the row states is
    asserted against the enum's real size, so a spec bump reddens the row instead of
    quietly changing what it means.
    """
    members = validator_for("creative/list-creatives-request.json").schema["properties"]["fields"]["items"]["enum"]
    assert len(members) == expected_count, (
        f"the scenario says {expected_count} fields members; list-creatives-request.json declares "
        f"{len(members)}: {members}"
    )
    return list(members)


def _creative_ids_of_length(ctx: dict, count: int) -> list[str]:
    """A ``filters.creative_ids`` array of exactly *count* ids, seeded ones first.

    The maxItems boundary rows care about the LENGTH; which creatives match is decided by
    the seeded ids at the front, so padding with ids nothing carries keeps the 100-item row
    and the 101-item row asking the same question about the same library.

    ONE SEEDED CREATIVE IS DELIBERATELY LEFT OUT. Naming the whole library made the row
    unfalsifiable: dropping the filter from the query returns exactly the same creatives
    the filter would have selected, so the row passed either way. Measured, not reasoned —
    a mutation that skipped the filter entirely left this row green while seven sibling
    behaviours reddened. With the last seeded creative unnamed, an unapplied filter returns
    it and the exact-set assertion fails.
    """
    seeded = [record.creative_id for record in ctx.get("library", ())]
    padding = [_ABSENT_CREATIVE_ID.format(index=index) for index in range(count)]
    return (seeded[:-1] + padding)[:count]


#: phrase pattern -> the request kwargs it means. Order matters only where one pattern is a
#: prefix of another; each is matched with ``fullmatch``, so they are otherwise independent.
_REQUEST_PHRASES: tuple[tuple[str, Callable[[re.Match[str], dict], dict[str, Any]]], ...] = (
    # "send it as it stands" — every one of these cells names a field the request OMITS.
    (
        r"no (?:parameters|filters|filter parameters|pagination params|sort params|fields parameter"
        r"|statuses filter|include_pricing parameter|include_snapshot parameter)",
        lambda match, ctx: {},
    ),
    (r"empty filters object", lambda match, ctx: {"filters": {}}),
    # filters.statuses — an ARRAY in core/creative-filters.json, so the singular cell
    # ("statuses filter \"unknown\"") means an array of that one member.
    (r"statuses filter \[(.*)\]", lambda match, ctx: {"filters": {"statuses": _quoted(match.group(1))}}),
    (r'statuses filter "([^"]*)"', lambda match, ctx: {"filters": {"statuses": [match.group(1)]}}),
    (r"statuses filter as empty array", lambda match, ctx: {"filters": {"statuses": []}}),
    (r"invalid status filter", lambda match, ctx: {"filters": {"statuses": [_UNKNOWN_STATUS]}}),
    (r"structured statuses \[(.*)\]", lambda match, ctx: {"filters": {"statuses": _quoted(match.group(1))}}),
    (
        r'structured filters with statuses \[(.*)\] and name_contains "([^"]*)"',
        lambda match, ctx: {
            "filters": {"statuses": _quoted(match.group(1)), "name_contains": match.group(2)},
        },
    ),
    (r'name_contains filter "([^"]*)"', lambda match, ctx: {"filters": {"name_contains": match.group(1)}}),
    # filters.tags (all must match) and filters.tags_any (any must match).
    (r"tags filter \[(.*)\]", lambda match, ctx: {"filters": {"tags": _quoted(match.group(1))}}),
    (r"tags filter as empty array", lambda match, ctx: {"filters": {"tags": []}}),
    (r"tags_any filter \[(.*)\]", lambda match, ctx: {"filters": {"tags_any": _quoted(match.group(1))}}),
    # filters.creative_ids — maxItems 100.
    (
        r"creative_ids with (?:exactly )?(\d+) items",
        lambda match, ctx: {"filters": {"creative_ids": _creative_ids_of_length(ctx, int(match.group(1)))}},
    ),
    # filters.created_after / created_before — format: date-time.
    (
        r'created_after "([^"]*)" and created_before "([^"]*)"',
        lambda match, ctx: {"filters": {"created_after": match.group(1), "created_before": match.group(2)}},
    ),
    (
        r'created_(after|before) (?:as )?"([^"]*)"',
        lambda match, ctx: {"filters": {f"created_{match.group(1)}": match.group(2)}},
    ),
    # filters.media_buy_ids — the only media-buy key the spec declares.
    (
        r"structured filters with media_buy_ids \[(.*)\]",
        lambda match, ctx: {"filters": {"media_buy_ids": _quoted(match.group(1))}},
    ),
    # filters.has_variables / has_served.
    (
        r"has_(variables|served) (true|false)",
        lambda match, ctx: {"filters": {f"has_{match.group(1)}": match.group(2) == "true"}},
    ),
    # pagination.
    (r"pagination max_results (\S+)", lambda match, ctx: {"pagination": {"max_results": _max_results(match.group(1))}}),
    # sort — an object whose two members are closed enums.
    (
        r'sort field "([^"]*)" direction "([^"]*)"',
        lambda match, ctx: {"sort": {"field": match.group(1), "direction": match.group(2)}},
    ),
    (r'sort field "([^"]*)"', lambda match, ctx: {"sort": {"field": match.group(1)}}),
    (r'sort direction "([^"]*)"', lambda match, ctx: {"sort": {"direction": match.group(1)}}),
    # the include_* projection flags.
    (
        r"include_(assignments|snapshot|items|variables|pricing) (true|false)",
        lambda match, ctx: {f"include_{match.group(1)}": match.group(2) == "true"},
    ),
    (r"include_pricing true and no account(?: reference)?", lambda match, ctx: {"include_pricing": True}),
    # fields — the array itself is pin-declared (minItems 1, closed enum of 13 values).
    (r"fields as empty array", lambda match, ctx: {"fields": []}),
    (
        r"fields with all (\d+) enum values",
        lambda match, ctx: {"fields": _every_selectable_field(int(match.group(1)))},
    ),
    (
        r"fields \[(.*)\] and include_snapshot true",
        lambda match, ctx: {"fields": _quoted(match.group(1)), "include_snapshot": True},
    ),
    (r"fields \[(.*)\]", lambda match, ctx: {"fields": _quoted(match.group(1))}),
    (r'fields containing "([^"]*)"', lambda match, ctx: {"fields": ["creative_id", match.group(1)]}),
    (r"fields containing integer (\d+)", lambda match, ctx: {"fields": [int(match.group(1))]}),
)

#: The step's parser, DERIVED from the table above so the two cannot disagree about which
#: phrases have a meaning.
_REQUEST_PHRASE_ALTERNATION = "|".join(f"(?:{pattern})" for pattern, _ in _REQUEST_PHRASES)


def _request_kwargs(phrase: str, ctx: dict) -> dict[str, Any]:
    """Translate one Examples ``request_params`` cell into request kwargs."""
    for pattern, build in _REQUEST_PHRASES:
        match = re.fullmatch(pattern, phrase)
        if match is not None:
            return build(match, ctx)
    raise AssertionError(
        f"no request-phrase translation for {phrase!r}. The step's parser is built from "
        "_REQUEST_PHRASES, so reaching this line means a pattern matched in the alternation "
        "and not on its own — fix the pattern rather than widening this function."
    )


@when(parsers.re(rf"the Buyer Agent sends a list_creatives request with (?P<phrase>{_REQUEST_PHRASE_ALTERNATION})"))
def when_list_creatives_with_phrase(ctx: dict, phrase: str) -> None:
    """Dispatch the request one Examples cell describes, through the scenario's transport.

    The kwargs are also recorded on ``ctx["request"]`` — the REQUEST, not the response, so
    a Then whose expectation depends on what the buyer asked for (a date cutoff, the ids it
    named) reads it from the request rather than from production's echo of it.
    """
    kwargs = _with_account(ctx, _request_kwargs(phrase, ctx))
    ctx["request"] = kwargs
    dispatch_request(ctx, **kwargs)


def _with_account(ctx: dict, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Name the account a Given targeted, unless the phrase already settled it.

    "The request targets a sandbox account" is a statement about the REQUEST, and
    ``account`` is where list-creatives-request.json puts it — a request that named no
    account would be answered against no account at all, and the scenario would grade the
    absence of a flag it never asked for. The account reference is the one the shared
    account Given seeded (``ctx["account_ref"]``).
    """
    account_ref = ctx.get("account_ref")
    if account_ref is None or "account" in kwargs:
        return kwargs
    # As a JSON-ready dict, because the reference has to survive three transports: the REST
    # dispatcher puts the request body on the wire as JSON and a pydantic model is not
    # serializable there. The DTO coerces it back to AccountReference server-side, so every
    # transport sends the same document.
    return {**kwargs, "account": account_ref.model_dump(mode="json", exclude_none=True)}


@when("the Buyer Agent sends a list_creatives request")
def when_list_creatives_bare(ctx: dict) -> None:
    """Dispatch with no parameters at all — the request every default is read from."""
    kwargs = _with_account(ctx, {})
    ctx["request"] = kwargs
    dispatch_request(ctx, **kwargs)


# ── Then ─────────────────────────────────────────────────────────────


def _wire_creative_ids(ctx: dict) -> list[str]:
    """The creative_ids the buyer received, in wire order."""
    return [entry["creative_id"] for entry in _wire_creatives(ctx)]


@then("creatives for both mb1 and mb2 returned (deduplicated)")
def then_merged_media_buy_creatives(ctx: dict) -> None:
    """Exactly the union of mb1's and mb2's creatives — no decoys, no duplicates.

    Three distinct regressions fail here, which is why the assertion is an exact SET
    plus a length check rather than a containment check:
      - the singular media_buy_id dropped  -> only mb2's creative comes back;
      - the plural media_buy_ids dropped   -> only mb1's;
      - both dropped (the shape a bare select_request_fields(ListCreativesRequest,
        bag) produces, since neither key is declared on that model) -> the whole
        library including the decoy-buy and unassigned creatives.
    """
    returned = _wire_creative_ids(ctx)
    assert set(returned) == ctx["expected_merged_creative_ids"], (
        f"expected exactly the mb1+mb2 creatives {sorted(ctx['expected_merged_creative_ids'])}, got {sorted(returned)}"
    )
    assert len(returned) == len(ctx["expected_merged_creative_ids"]), (
        f"media_buy_id/media_buy_ids merge emitted duplicate rows: {returned}"
    )


@then("assignment data excluded from creatives")
@then("assignment data excluded")
def then_assignments_excluded(ctx: dict) -> None:
    """No creative on the wire carries assignment data, though every seeded creative
    has a CreativeAssignment row in the database."""
    creatives = _wire_creatives(ctx)
    assert {entry["creative_id"] for entry in creatives} == set(ctx["seeded_creative_ids"]), (
        f"expected the 3 seeded creatives {sorted(ctx['seeded_creative_ids'])}, "
        f"got {sorted(entry['creative_id'] for entry in creatives)}"
    )
    leaked = [entry["creative_id"] for entry in creatives if entry.get("assignments") is not None]
    assert leaked == [], f"include_assignments=false still emitted assignment data for: {leaked}"


def _ids_by_created_at(ctx: dict, *, newest_first: bool) -> list[str]:
    """The seeded creative_ids in created_at order — the reader's default sort key."""
    library = _library(ctx)
    missing = [record.label for record in library if record.created_at is None]
    assert not missing, f"these seeded creatives carry no created_at, so an order cannot be asserted: {missing}"
    return [
        record.creative_id for record in sorted(library, key=lambda record: record.created_at, reverse=newest_first)
    ]


def _assert_wire_order(ctx: dict, expected_newest_first: bool) -> None:
    """Assert the returned page is the expected slice of the seeded created_at order."""
    expected = _ids_by_created_at(ctx, newest_first=expected_newest_first)[:_DEFAULT_PAGE_SIZE]
    assert _wire_creative_ids(ctx) == expected, (
        f"expected the first {_DEFAULT_PAGE_SIZE} creatives "
        f"{'newest' if expected_newest_first else 'oldest'}-first, got a different sequence"
    )


@then("creatives sorted descending")
def then_creatives_sorted_descending(ctx: dict) -> None:
    _assert_wire_order(ctx, expected_newest_first=True)


@then("creatives sorted ascending")
def then_creatives_sorted_ascending(ctx: dict) -> None:
    _assert_wire_order(ctx, expected_newest_first=False)


def then_creatives_sorted_by_default_after_coercion(ctx: dict) -> None:
    """Both silent coercions land on the SAME observable: the default created_date-desc
    ordering, with no wire error.

      - sort_order="random" is not a member of the AdCP sort-direction enum; the agent
        coerces it to "desc" (listing.py:126-130) rather than rejecting the request.
      - sort_by="unknown_field" is outside the builder's field_mapping; the agent
        coerces it to "created_date" (:161-178), direction defaulting to desc.

    A coercion is only observable as an ORDERING plus the ABSENCE of an error, so the
    exact newest-first sequence pins both halves: ``_wire_creatives`` raises loudly if
    the dispatch produced an error envelope instead of a response, and the sequence
    discriminates against the other candidate coercion targets (name-desc and
    status-desc both yield a different order over this fixture).

    One body, two rows: the two coercions differ in WHICH input is out of range, not in
    what the buyer is supposed to receive — writing them as two identical functions
    would be the duplication the repo's DRY invariant forbids.
    """
    _assert_wire_order(ctx, expected_newest_first=True)


@then(parsers.parse('error "{code}" with suggestion'))
def then_error_code_with_suggestion(ctx: dict, code: str) -> None:
    """The spec's closed enums make an out-of-enum sort value an ERROR, not a coercion.

    Registered HERE because pytest-bdd resolves Then steps per MODULE: uc019 implements the
    identical text, but a step defined in another module does not bind these scenarios, and
    an unbound Then does not fail -- it raises StepDefinitionNotFoundError, which the
    non-strict auto-xfail swallows (conftest.py:164) with a reason containing no "production
    gap", so the dormancy classifier does not catch it either. Four rows that were PASSING
    went silently dormant that way when this outline was rewritten onto the spec vocabulary,
    and the NET xfail count went DOWN at the same time because new rows were passing -- so
    the aggregate hid it. Per-row disposition is the only honest check.
    """
    envelope = ctx["result"].wire_error_envelope
    assert envelope is not None, (
        f"expected the wire to carry a {code} envelope; got none. An out-of-enum sort value "
        f"or an out-of-range pagination.max_results violates a SCHEMA CONSTRAINT under "
        f"pinned 3.1.1, so it must be refused rather than silently coerced."
    )
    # The code no longer differs by transport, so the row's code is asserted as written.
    # An out-of-enum sort value, or pagination.max_results above the schema's maximum of
    # 100, violates a SCHEMA CONSTRAINT -- which pinned 3.1.1 assigns to INVALID_REQUEST
    # ("violates schema constraints"), not VALIDATION_ERROR ("beyond schema validation").
    # This step used to downgrade the expectation to VALIDATION_ERROR on mcp and a2a,
    # because only REST reached the spec-correct code (its body is derived from the DTO,
    # so the failure was attributed at the schema layer while the others raised from the
    # typed boundary first). That exception was recorded as shrinking to nothing once the
    # transports agreed. They now do -- adcp_error_for maps a pydantic ValidationError to
    # AdCPInvalidRequestError -- so it is gone rather than left to rot into a downgrade
    # that hides a future regression.
    assert_envelope_shape(envelope, code, recovery="correctable")
    assert envelope["errors"][0].get("suggestion"), (
        f"the {code} envelope must carry a recovery suggestion: {envelope['errors'][0]}"
    )


@then("creatives sorted by assignment_count")
def then_creatives_sorted_by_assignment_count(ctx: dict) -> None:
    """assignment_count is the last member of creative-sort-field.json -- an enum boundary.

    Asserts the wire order is actually BY assignment_count, not merely that the request
    succeeded: a sort field that is accepted and then ignored looks identical to one that
    works, and that is exactly what an enum-boundary row exists to catch.

    The count is read from ``assignments.assignment_count`` — the shape
    list-creatives-response.json declares (an OBJECT carrying the count and the assigned
    packages). This step used to read ``len(entry["assignments"])``, which is a list's
    length; over the object that is the number of KEYS, identical for every creative, so
    the non-vacuity guard below could never be satisfied.
    """
    creatives = _wire_creatives(ctx)
    assert creatives, "no creatives on the wire to check the sort of"
    counts = [(entry.get("assignments") or {}).get("assignment_count") for entry in creatives]

    # NON-VACUITY FIRST. Over creatives that all carry the same count, a bare
    # `counts == sorted(counts, reverse=True)` compares a constant list to itself and passes
    # no matter what order the seller returned -- including when the sort field is ignored
    # entirely. A row that cannot fail is worse than no row: it would turn a real production
    # gap into an XPASS and retire its own xfail entry. The seeded library therefore carries
    # three DIFFERENT counts, in an order that matches none of the other sort fields'.
    assert len(set(counts)) > 1, (
        "cannot grade assignment_count ordering: every creative on the wire has the same "
        f"assignment count ({counts}). Seed creatives with DIFFERING assignment counts "
        "before asserting an order, or this passes while the sort field is ignored."
    )
    assert counts == sorted(counts, reverse=True), (
        f"expected creatives ordered by assignment_count descending, got {counts}"
    )


# ── Given: the seeded libraries the outlines read ────────────────────
#
# Every remaining Given in this feature says "the authenticated principal has <a
# library>". They differ only in WHICH creatives, so there is one seeder and a spec table
# per library; a Then names the creatives it expects by LABEL, which is also the creative's
# name, so a failure message reads as the scenario does.
#
# Statuses are always seeded EXPLICITLY (never the factory default) and created_at is
# always distinct, because both are graded: the archival-exclusion rows assert which
# statuses come back, and the ordering rows assert an exact sequence, which rows sharing
# one server_default timestamp cannot produce.


class _Spec(NamedTuple):
    """One creative to seed. ``label`` is its name, and how a Then refers to it."""

    label: str
    status: str = "approved"
    tags: tuple[str, ...] = ()
    media_buy_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    #: Extra package assignments, for the rows that grade the assignment_count sort field.
    assignment_count: int = 0


class _Seeded(NamedTuple):
    """One seeded creative, as the Then steps need to know it."""

    creative_id: str
    label: str
    status: str
    tags: tuple[str, ...]
    created_at: datetime | None
    assignment_count: int = 0


#: The buy the extra assignments hang off. No request names it, so it changes only counts.
_ASSIGNMENT_COUNT_MEDIA_BUY_ID = "mb_assignment_counts"


#: The status the pin excludes from an unfiltered read (core/creative-filters.json).
_ARCHIVED = "archived"

#: The five statuses this feature's outlines name, in the order they name them. NOT derived
#: from enums/creative-status.json, which carries a sixth member (``suspended``) no row of
#: this feature exercises — deriving would seed six creatives under a row that asserts four
#: non-archived out of five.
_FEATURE_STATUSES = ("processing", "approved", "rejected", "pending_review", "archived")

#: Anchor for every seeded created_at/updated_at. A fixed instant, so an ordering assertion
#: compares an exact sequence rather than "is it sorted by something".
_ANCHOR = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _seed_library(ctx: dict, specs: Sequence[_Spec]) -> list[_Seeded]:
    """Seed one library for the scenario's buyer and record it on ``ctx["library"]``."""
    env = ctx["env"]
    tenant, principal = _get_or_create_tenant_and_principal(env)

    media_buys: dict[str, Any] = {}

    def media_buy(media_buy_id: str) -> Any:
        """One media buy per id, seeded once.

        ``setdefault`` would not do: it evaluates its default eagerly, so a second
        creative on the same buy ran the factory again and violated the media_buys
        primary key.
        """
        if media_buy_id not in media_buys:
            media_buys[media_buy_id] = _seed_media_buy(tenant, principal, media_buy_id)
        return media_buys[media_buy_id]

    seeded: list[_Seeded] = []
    for spec in specs:
        overrides: dict[str, Any] = {"status": spec.status, "name": spec.label}
        if spec.created_at is not None:
            overrides["created_at"] = spec.created_at
        if spec.updated_at is not None:
            overrides["updated_at"] = spec.updated_at
        creative = _seed_creative(tenant, principal, tags=list(spec.tags) or None, **overrides)
        if spec.media_buy_id is not None:
            _assign(tenant, creative, media_buy(spec.media_buy_id))
        # Extra assignments on a buy no filter names: the count is the subject here, and
        # the factory's own package_id sequence keeps them distinct rows.
        for _ in range(spec.assignment_count):
            _assign(tenant, creative, media_buy(_ASSIGNMENT_COUNT_MEDIA_BUY_ID))
        seeded.append(
            _Seeded(
                creative.creative_id,
                spec.label,
                spec.status,
                spec.tags,
                spec.created_at,
                spec.assignment_count + (1 if spec.media_buy_id is not None else 0),
            )
        )

    ctx["tenant"] = tenant
    ctx["principal"] = principal
    ctx["library"] = seeded
    return seeded


def _library(ctx: dict) -> list[_Seeded]:
    """The seeded library, or a loud failure — a Then over an unseeded library grades nothing."""
    library = ctx.get("library")
    assert library, "no library was seeded; the Given did not run or seeded nothing"
    return library


def _ids_labelled(ctx: dict, *labels: str) -> set[str]:
    """The creative_ids of the seeded creatives with these labels."""
    by_label = {record.label: record.creative_id for record in _library(ctx)}
    missing = [label for label in labels if label not in by_label]
    assert not missing, f"the scenario names creatives the Given did not seed: {missing}; seeded {sorted(by_label)}"
    return {by_label[label] for label in labels}


def _ids_with_status(ctx: dict, *statuses: str) -> set[str]:
    """The creative_ids of the seeded creatives in any of these statuses."""
    wanted = set(statuses)
    ids = {record.creative_id for record in _library(ctx) if record.status in wanted}
    assert ids, f"the Given seeded no creative in {sorted(wanted)}, so this assertion cannot fail"
    return ids


def _non_archived_ids(ctx: dict) -> set[str]:
    """Every seeded creative the pin's default read must return."""
    library = _library(ctx)
    assert any(record.status == _ARCHIVED for record in library), (
        "the Given seeded no archived creative, so 'archived is excluded by default' cannot fail here"
    )
    return {record.creative_id for record in library if record.status != _ARCHIVED}


def _seed_status_library(ctx: dict, statuses: Sequence[str]) -> list[_Seeded]:
    """Seed one creative per named status, newest first, every name carrying "nike".

    The shared name is what makes the ``name_contains`` row falsifiable: the archived
    creative matches the search too, so a read that returned it is caught by the
    archival-exclusion half of that row's outcome rather than passing on a narrower filter.
    """
    return _seed_library(
        ctx,
        [
            _Spec(label=f"nike {status} creative", status=status, created_at=_ANCHOR - timedelta(minutes=index))
            for index, status in enumerate(statuses)
        ],
    )


@given(
    parsers.re(
        r"the authenticated principal has (?:(?P<count>\d+) )?creatives? (?:with|in) statuses (?P<statuses>\".*\")"
    )
)
def given_creatives_in_statuses(ctx: dict, count: str | None, statuses: str) -> None:
    """Seed the library of named statuses the scenario lists."""
    named = _quoted(statuses)
    if count is not None:
        assert int(count) == len(named), f"the scenario says {count} creatives but names {len(named)} statuses: {named}"
    _seed_status_library(ctx, named)


@given("the authenticated principal has creatives in various statuses")
def given_creatives_in_various_statuses(ctx: dict) -> None:
    """The same five-status library, for the boundary outline that does not list them."""
    _seed_status_library(ctx, _FEATURE_STATUSES)


@given(
    parsers.re(r"the authenticated principal has (?P<approved>\d+) approved and (?P<archived>\d+) archived creatives?")
)
def given_approved_and_archived(ctx: dict, approved: str, archived: str) -> None:
    """Seed N approved + M archived creatives (BR-RULE-146's default-exclusion invariants)."""
    _seed_library(ctx, _counted_specs({"approved": int(approved), _ARCHIVED: int(archived)}))


@given(
    parsers.re(r"the authenticated principal has (?P<approved>\d+) approved and (?P<rejected>\d+) rejected creatives?")
)
def given_approved_and_rejected(ctx: dict, approved: str, rejected: str) -> None:
    """Seed N approved + M rejected creatives (BR-RULE-148's statuses-filter invariants)."""
    _seed_library(ctx, _counted_specs({"approved": int(approved), "rejected": int(rejected)}))


@given(
    parsers.re(
        r"the authenticated principal has (?P<approved>\d+) approved, (?P<rejected>\d+) rejected, "
        r"and (?P<archived>\d+) archived creatives?"
    )
)
def given_approved_rejected_and_archived(ctx: dict, approved: str, rejected: str, archived: str) -> None:
    """Seed the three-status library BR-RULE-146 INV-3 reads."""
    _seed_library(
        ctx,
        _counted_specs({"approved": int(approved), "rejected": int(rejected), _ARCHIVED: int(archived)}),
    )


def _counted_specs(counts: dict[str, int]) -> list[_Spec]:
    """``{"approved": 3, "archived": 1}`` -> four specs with distinct created_at."""
    specs: list[_Spec] = []
    for status, count in counts.items():
        for index in range(count):
            specs.append(
                _Spec(
                    label=f"{status} creative {index}",
                    status=status,
                    created_at=_ANCHOR - timedelta(minutes=len(specs)),
                )
            )
    return specs


#: Three approved creatives whose created_at, updated_at, name and assignment-count orders
#: are all DIFFERENT, so each of the four sort fields this outline names produces its own
#: distinguishable sequence and none of them passes by accident:
#:   created_date desc -> charlie, bravo, alpha
#:   name asc          -> alpha, bravo, charlie
#:   updated_date desc -> bravo, alpha, charlie
#:   assignment_count  -> alpha (3), charlie (2), bravo (1)
_APPROVED_SPECS = (
    _Spec(
        label="charlie approved",
        created_at=_ANCHOR,
        updated_at=_ANCHOR - timedelta(minutes=2),
        assignment_count=2,
    ),
    _Spec(
        label="bravo approved",
        created_at=_ANCHOR - timedelta(minutes=1),
        updated_at=_ANCHOR,
        assignment_count=1,
    ),
    _Spec(
        label="alpha approved",
        created_at=_ANCHOR - timedelta(minutes=2),
        updated_at=_ANCHOR - timedelta(minutes=1),
        assignment_count=3,
    ),
)


@given("the authenticated principal has approved creatives")
@given("the authenticated principal has creatives created on different dates")
def given_approved_creatives(ctx: dict) -> None:
    """Seed the three-creative approved library the ordering and pricing rows read."""
    _seed_library(ctx, _APPROVED_SPECS)


@given("the authenticated principal has no creatives")
def given_no_creatives(ctx: dict) -> None:
    """Seed the buyer and nothing else — an empty library is a success, not an error."""
    env = ctx["env"]
    tenant, principal = _get_or_create_tenant_and_principal(env)
    ctx["tenant"] = tenant
    ctx["principal"] = principal
    ctx["library"] = []


@given(parsers.re(r"the authenticated principal has creatives created in (?P<year>\d{4})"))
def given_creatives_created_in_year(ctx: dict, year: str) -> None:
    """Seed two creatives inside *year* and one the year before.

    The earlier creative is what makes a ``created_after`` row falsifiable: a filter that
    was dropped returns it too.
    """
    inside = datetime(int(year), 6, 1, 12, 0, tzinfo=UTC)
    _seed_library(
        ctx,
        [
            _Spec(label=f"mid {year} creative", created_at=inside),
            _Spec(label=f"late {year} creative", created_at=inside + timedelta(days=30)),
            _Spec(label=f"pre {year} creative", created_at=datetime(int(year) - 1, 6, 1, 12, 0, tzinfo=UTC)),
        ],
    )


#: The filter-semantics library (BR-RULE-148). Every filter row of that outline reads this
#: one library, so each row's expectation is a different SUBSET of the same five creatives —
#: which is what makes a dropped filter visible: it returns a superset.
_FILTER_SPECS = (
    _Spec(
        label="nike q1 launch",
        tags=("q1",),
        media_buy_id="mb1",
        created_at=datetime(2024, 3, 1, 12, 0, tzinfo=UTC),
    ),
    _Spec(
        label="nike brand anthem",
        tags=("q1", "brand"),
        media_buy_id="mb2",
        created_at=datetime(2024, 4, 1, 12, 0, tzinfo=UTC),
    ),
    _Spec(
        label="adidas q1 retargeting",
        tags=("brand",),
        media_buy_id="mb3",
        created_at=datetime(2023, 12, 1, 12, 0, tzinfo=UTC),
    ),
    _Spec(
        label="nike rejected cut",
        status="rejected",
        created_at=datetime(2024, 5, 1, 12, 0, tzinfo=UTC),
    ),
    _Spec(
        label="nike archived classic",
        status=_ARCHIVED,
        tags=("q1", "brand"),
        created_at=datetime(2024, 2, 1, 12, 0, tzinfo=UTC),
    ),
)


@given("the authenticated principal has creatives with various tags, statuses, and media buy associations")
def given_creatives_with_tags_statuses_and_media_buys(ctx: dict) -> None:
    """Seed the filter-semantics library: real tags, three media buys, two excluded statuses.

    The falsifiability of every row here comes from the decoys. ``mb3`` is a buy no request
    names and one creative is unassigned, so a media-buy filter that collapsed to "no filter
    at all" returns them and fails the exact-set assertion. The archived creative carries
    BOTH tags, so a tags row that ignored the pin's default archival exclusion returns it.

    Tags are real tags on the creative (``data["tags"]``), not name substrings: the
    repository asks jsonb for containment, because ``tags``/``tags_any`` in
    core/creative-filters.json are about the creative's tags and ``name_contains`` is the
    filter that asks about names.
    """
    seeded = _seed_library(ctx, _FILTER_SPECS)
    # The media_buy_ids row expects exactly the mb1 + mb2 creatives (the existing Then reads
    # this key), which is also the set a half-honoured filter cannot produce.
    ctx["expected_merged_creative_ids"] = _ids_labelled(ctx, "nike q1 launch", "nike brand anthem")
    assert len(seeded) == len(_FILTER_SPECS)


#: The filter-BOUNDARY library: a single-tag creative, a differently-tagged one, and an
#: untagged one, spread either side of 2024-01-01 for the created_after boundary row.
_BOUNDARY_FILTER_SPECS = (
    _Spec(
        label="single tagged creative",
        tags=("single_tag",),
        media_buy_id="mb1",
        created_at=datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
    ),
    _Spec(
        label="other tagged creative",
        tags=("other_tag",),
        media_buy_id="mb1",
        created_at=datetime(2023, 6, 1, 12, 0, tzinfo=UTC),
    ),
    _Spec(label="untagged creative", created_at=datetime(2024, 7, 1, 12, 0, tzinfo=UTC)),
)


@given("the authenticated principal has creatives with various tags, media buy associations, and creation dates")
def given_boundary_filter_library(ctx: dict) -> None:
    """Seed the library the filter-boundary outline reads (tags, media buys, dates)."""
    _seed_library(ctx, _BOUNDARY_FILTER_SPECS)


#: "Full data" means every field this seller can populate for a creative: assets (the
#: factory's realistic default), tags, and a concept. The rows that read it grade whether an
#: UNPROJECTED response carries them all.
_FULL_DATA_TAGS = ("q1", "brand")
_FULL_DATA_CONCEPT_ID = "concept_summer_2026"


@given("the authenticated principal has creatives with full data")
@given(parsers.re(r"the authenticated principal has (?P<count>\d+) approved creatives with full data"))
def given_creatives_with_full_data(ctx: dict, count: str | None = None) -> None:
    """Seed approved creatives carrying assets, tags, a concept AND a package assignment.

    The assignment row is part of "full data" and it is what makes ``include_assignments
    false`` falsifiable: a projection flag that did nothing would have assignment data to
    leak. Written here rather than in a second seeder so this stays the one place the
    full-data library is assembled.
    """
    env = ctx["env"]
    tenant, principal = _get_or_create_tenant_and_principal(env)
    media_buy = _seed_media_buy(tenant, principal, _MERGE_MEDIA_BUY_IDS[0])
    total = int(count) if count is not None else 3
    seeded: list[_Seeded] = []
    for index in range(total):
        created_at = _ANCHOR - timedelta(minutes=index)
        label = f"full data creative {index}"
        creative = _seed_creative(
            tenant,
            principal,
            name=label,
            created_at=created_at,
            updated_at=created_at,
            tags=list(_FULL_DATA_TAGS),
            concept_id=_FULL_DATA_CONCEPT_ID,
            concept_name=_CONCEPT_NAME,
        )
        _assign(tenant, creative, media_buy)
        seeded.append(_Seeded(creative.creative_id, label, "approved", _FULL_DATA_TAGS, created_at))
    ctx["tenant"] = tenant
    ctx["principal"] = principal
    ctx["library"] = seeded
    ctx["seeded_creative_ids"] = [record.creative_id for record in seeded]


@given(
    parsers.re(
        r"the authenticated principal has a creative with tags \[(?P<first>.*)\] and a creative with tags \[(?P<second>.*)\]"
    )
)
def given_two_creatives_with_tag_sets(ctx: dict, first: str, second: str) -> None:
    """Seed one creative per tag set, so an AND filter has exactly one match and one miss."""
    _seed_library(
        ctx,
        [
            _Spec(label="creative with both tags", tags=tuple(_quoted(first)), created_at=_ANCHOR),
            _Spec(
                label="creative with one tag",
                tags=tuple(_quoted(second)),
                created_at=_ANCHOR - timedelta(minutes=1),
            ),
        ],
    )


@given(parsers.re(r"the authenticated principal has a creative with tags \[(?P<only>.*)\] only"))
def given_one_creative_with_tag_set(ctx: dict, only: str) -> None:
    """Seed the single under-tagged creative the AND counter-example excludes."""
    _seed_library(ctx, [_Spec(label="creative with one tag", tags=tuple(_quoted(only)), created_at=_ANCHOR)])


@given(
    parsers.re(
        r'the authenticated principal has a creative with tag "(?P<first>[^"]+)" and a creative with tag "(?P<second>[^"]+)"'
    )
)
def given_two_creatives_one_tag_each(ctx: dict, first: str, second: str) -> None:
    """Seed one creative per tag, so an OR filter returns both and an AND filter neither."""
    _seed_library(
        ctx,
        [
            _Spec(label=f"creative tagged {first}", tags=(first,), created_at=_ANCHOR),
            _Spec(label=f"creative tagged {second}", tags=(second,), created_at=_ANCHOR - timedelta(minutes=1)),
        ],
    )


@given(
    parsers.re(
        r'the authenticated principal has creatives associated with media buys "(?P<first>[^"]+)" and "(?P<second>[^"]+)"'
    )
)
def given_creatives_on_two_media_buys(ctx: dict, first: str, second: str) -> None:
    """Seed one creative per named media buy, plus one on a buy no request names."""
    _seed_library(
        ctx,
        [
            _Spec(label=f"creative on {first}", media_buy_id=first, created_at=_ANCHOR),
            _Spec(label=f"creative on {second}", media_buy_id=second, created_at=_ANCHOR - timedelta(minutes=1)),
            _Spec(
                label="creative on an unnamed buy", media_buy_id="mb_decoy", created_at=_ANCHOR - timedelta(minutes=2)
            ),
        ],
    )
    ctx["expected_merged_creative_ids"] = _ids_labelled(ctx, f"creative on {first}", f"creative on {second}")


@given(parsers.re(r'the authenticated principal has a creative associated with media buy "(?P<media_buy_id>[^"]+)"'))
def given_one_creative_on_media_buy(ctx: dict, media_buy_id: str) -> None:
    """Seed one creative on the named buy, plus one elsewhere, for the dedup row."""
    _seed_library(
        ctx,
        [
            _Spec(label=f"creative on {media_buy_id}", media_buy_id=media_buy_id, created_at=_ANCHOR),
            _Spec(
                label="creative on an unnamed buy", media_buy_id="mb_decoy", created_at=_ANCHOR - timedelta(minutes=1)
            ),
        ],
    )


@given(
    parsers.re(
        r'the authenticated principal has a creative with database status "(?P<status>[^"]+)" \(not in protocol enum\)'
    )
)
def given_creative_with_unreadable_status(ctx: dict, status: str) -> None:
    """Seed a creative whose stored status no AdCP version defines."""
    _seed_library(ctx, [_Spec(label=f"creative stored as {status}", status=status, created_at=_ANCHOR)])


@given(
    'the buyer has synced creatives in formats including {agent_url, "display_300x250"} and {agent_url, "video_30s"}'
)
def given_creatives_in_two_formats(ctx: dict) -> None:
    """Seed one creative per format on the SAME agent_url, plus one with the same id elsewhere.

    The third creative is what makes the object filter falsifiable in both directions: it
    carries the requested ``id`` under a DIFFERENT agent_url, so a filter that matched only
    the id returns it. ``video_30s`` is not in the standard registry, so the registered
    ``video_640x480`` stands in for the second format — the A2A path re-validates a
    creative's format_id against the registry and would reject an unknown id before any
    filter ran. The scenario's point is "a second, different format", which it is.
    """
    env = ctx["env"]
    tenant, principal = _get_or_create_tenant_and_principal(env)
    other_agent_url = "https://other-creative.example.com"
    wanted = _seed_creative(tenant, principal, _CONCEPT_FORMATS[0], name="display creative on this agent")
    sibling = _seed_creative(tenant, principal, _CONCEPT_FORMATS[1], name="video creative on this agent")
    decoy = _seed_creative(
        tenant, principal, _CONCEPT_FORMATS[0], name="display creative on another agent", agent_url=other_agent_url
    )
    ctx["tenant"] = tenant
    ctx["principal"] = principal
    ctx["library"] = [
        _Seeded(wanted.creative_id, "display creative on this agent", "approved", (), None),
        _Seeded(sibling.creative_id, "video creative on this agent", "approved", (), None),
        _Seeded(decoy.creative_id, "display creative on another agent", "approved", (), None),
    ]
    ctx["format_filter_agent_url"] = wanted.agent_url
    ctx["format_filter_id"] = _CONCEPT_FORMATS[0]


@when(
    parsers.re(
        r"the Buyer Agent sends list_creatives with filters\.format_ids carrying one format_id object "
        r'\{agent_url, "(?P<format_id>[^"]+)"\}'
    )
)
def when_list_creatives_format_ids(ctx: dict, format_id: str) -> None:
    """Dispatch ``filters.format_ids`` carrying the one {agent_url, id} object.

    format_ids items are format_id OBJECTS (core/format-id.json), never bare strings, so
    the filter names both members of the seeded creative's format.
    """
    dispatch_request(
        ctx,
        filters={"format_ids": [{"agent_url": ctx["format_filter_agent_url"], "id": ctx["format_filter_id"]}]},
    )


@when(
    parsers.re(
        r"the Buyer Agent sends a list_creatives request without specifying "
        r"include_(?P<flag>assignments|snapshot|items|variables|pricing)"
    )
)
def when_list_creatives_without_flag(ctx: dict, flag: str) -> None:
    """Dispatch a request that OMITS one include_* flag, so its pinned default applies."""
    dispatch_request(ctx)


# ── Then: the outcomes the outlines name ─────────────────────────────
#
# An outcome cell is an English sentence about WHICH creatives came back, so every step
# here resolves to the same two questions: the exact id set, and the exact wire order. Both
# are answered against the ids the Given recorded, never against a recomputation of the
# response — which is what makes a dropped filter visible as a superset rather than as a
# count that still happens to match.


def _assert_returned_exactly(ctx: dict, expected: set[str], what: str) -> None:
    """Assert the wire carried exactly *expected*, once each."""
    returned = _wire_creative_ids(ctx)
    labels = {record.creative_id: record.label for record in _library(ctx)}
    assert set(returned) == expected, (
        f"expected exactly {what}: {sorted(labels.get(i, i) for i in expected)}, "
        f"got {sorted(labels.get(i, i) for i in returned)}"
    )
    assert len(returned) == len(expected), f"the response repeated a creative: {returned}"


def _assert_count(ctx: dict, count: int) -> list[str]:
    """Assert the wire carried *count* creatives, and return them in wire order."""
    returned = _wire_creative_ids(ctx)
    assert len(returned) == count, f"expected {count} creatives on the wire, got {len(returned)}: {returned}"
    return returned


def _status_names(text: str) -> list[str]:
    """``"approved and archived"`` -> ``["approved", "archived"]``."""
    return [name for name in re.split(r",\s*|\s+and\s+", text.strip()) if name]


@then(parsers.re(r"(?P<count>\d+) non-archived creatives are returned"))
def then_n_non_archived_returned(ctx: dict, count: str) -> None:
    """The default read returns every non-archived creative and nothing else."""
    expected = _non_archived_ids(ctx)
    assert len(expected) == int(count), (
        f"the scenario expects {count} non-archived creatives but the Given seeded {len(expected)}"
    )
    _assert_returned_exactly(ctx, expected, "the non-archived creatives")


@then(
    parsers.re(
        r"all non-archived creatives (?:are )?returned"
        r"(?: \(defaults apply\)| \(archived excluded by default\))?"
    )
)
def then_all_non_archived_returned(ctx: dict) -> None:
    """core/creative-filters.json: archived creatives are excluded unless named."""
    _assert_returned_exactly(ctx, _non_archived_ids(ctx), "the non-archived creatives")


@then(parsers.re(r"(?:only|both) (?P<statuses>[a-z_]+(?:(?:, |, and | and )[a-z_]+)*) creatives (?:are )?returned"))
def then_only_creatives_with_statuses(ctx: dict, statuses: str) -> None:
    """A statuses filter returns exactly the creatives in those statuses."""
    named = _status_names(statuses)
    _assert_returned_exactly(ctx, _ids_with_status(ctx, *named), f"the {', '.join(named)} creatives")


@then(parsers.re(r"all (?P<count>\d+) creatives including archived (?:are )?returned"))
def then_all_creatives_including_archived(ctx: dict, count: str) -> None:
    """Naming every status in the filter is how a buyer asks for the archived rows too."""
    library = _library(ctx)
    assert len(library) == int(count), f"the scenario expects {count} creatives; the Given seeded {len(library)}"
    _assert_returned_exactly(ctx, {record.creative_id for record in library}, "every seeded creative")


@then(parsers.re(r'only approved creatives matching "(?P<needle>[^"]+)" returned'))
def then_only_approved_matching(ctx: dict, needle: str) -> None:
    """statuses + name_contains compose: both must hold, and archived stays out."""
    expected = {
        record.creative_id
        for record in _library(ctx)
        if record.status == "approved" and needle.lower() in record.label.lower()
    }
    assert expected, f"the Given seeded no approved creative whose name contains {needle!r}"
    _assert_returned_exactly(ctx, expected, f"the approved creatives matching {needle!r}")


@then(
    parsers.re(
        r"(?P<count>\d+) creatives? returned"
        r"(?: \(default page size\)| \(all available, below cap\)| \(all available\))?"
    )
)
def then_n_creatives_returned(ctx: dict, count: str) -> None:
    """A page carries *count* creatives — and, when the library has an order, THOSE ones.

    The count alone would pass on any slice, so where the seeded library has distinct
    created_at values the assertion is the exact newest-first prefix: the page the pin's
    default sort produces.
    """
    returned = _assert_count(ctx, int(count))
    expected = _ids_by_created_at(ctx, newest_first=True)[: int(count)]
    assert returned == expected, f"expected the {count} newest creatives in order, got a different sequence"


@then(parsers.re(r"the response contains (?P<count>\d+) creatives?$"))
def then_response_contains_n_creatives(ctx: dict, count: str) -> None:
    """The response carries *count* creatives (the scenario's own count of its fixture)."""
    _assert_count(ctx, int(count))


@then(parsers.re(r"the response contains a creatives array with (?P<count>\d+) items"))
def then_creatives_array_has_n_items(ctx: dict, count: str) -> None:
    """``creatives`` is REQUIRED on the response, so an empty library is ``[]`` and not absent."""
    creatives = wire_dict(ctx).get("creatives")
    assert isinstance(creatives, list), f"the response carries no creatives array: {sorted(wire_dict(ctx))}"
    assert len(creatives) == int(count), f"expected {count} creatives, got {len(creatives)}"


@then(parsers.re(r"(?:the )?pagination shows has_more as (?P<value>true|false)"))
def then_pagination_has_more(ctx: dict, value: str) -> None:
    """``pagination`` is REQUIRED (core/pagination-response.json), and so is its answer."""
    pagination = wire_dict(ctx).get("pagination")
    assert isinstance(pagination, dict), f"the response carries no pagination object: {sorted(wire_dict(ctx))}"
    assert pagination.get("has_more") is (value == "true"), (
        f"expected has_more {value}, got {pagination.get('has_more')!r} in {pagination}"
    )


@then(parsers.re(r"the query_summary shows total_matching as (?P<total>\d+)(?: and returned as (?P<returned>\d+))?"))
def then_query_summary_counts(ctx: dict, total: str, returned: str | None) -> None:
    """total_matching counts the whole match; returned counts this page. Both are required."""
    summary = wire_dict(ctx).get("query_summary")
    assert isinstance(summary, dict), f"the response carries no query_summary: {sorted(wire_dict(ctx))}"
    assert summary.get("total_matching") == int(total), (
        f"expected total_matching {total}, got {summary.get('total_matching')!r}"
    )
    if returned is not None:
        assert summary.get("returned") == int(returned), (
            f"expected returned {returned}, got {summary.get('returned')!r}"
        )


@then(parsers.re(r'the query_summary shows sort_applied as "(?P<field>\w+) (?P<direction>asc|desc)"'))
def then_query_summary_sort_applied(ctx: dict, field: str, direction: str) -> None:
    """sort_applied is an OBJECT of field + direction (list-creatives-response.json).

    The scenario writes the pair as one phrase; the wire carries the two members, and the
    buyer must be told the sort that was applied even when it is the pinned default (the
    request's sort.field defaults to created_date and sort.direction to desc).
    """
    summary = wire_dict(ctx).get("query_summary") or {}
    assert summary.get("sort_applied") == {"field": field, "direction": direction}, (
        f"expected sort_applied {{'field': {field!r}, 'direction': {direction!r}}}, got {summary.get('sort_applied')!r}"
    )


@then("the archived creative is not included in the results")
def then_archived_not_included(ctx: dict) -> None:
    """The archived row is seeded and must NOT come back — the exclusion's counter-example.

    Asserted as the WHOLE set the read must return, not as the absence of the archived ids.
    An absence holds over an empty response, so a seller that returned nothing — or a
    scenario whose seller never had the rows — would satisfy "the archived creative is not
    included" while grading nothing at all.
    """
    archived = {record.creative_id for record in _library(ctx) if record.status == _ARCHIVED}
    assert archived, "the Given seeded no archived creative, so this assertion cannot fail"
    _assert_returned_exactly(ctx, _non_archived_ids(ctx), "the non-archived creatives, and no archived one")


@then("each creative includes creative_id, name, format_id, status, created_date, updated_date")
def then_each_creative_has_required_fields(ctx: dict) -> None:
    """The six fields list-creatives-response.json marks REQUIRED on every item."""
    creatives = _wire_creatives(ctx)
    assert creatives, "no creatives on the wire to inspect"
    for entry in creatives:
        for field in ("creative_id", "name", "format_id", "status", "created_date", "updated_date"):
            assert entry.get(field) not in (None, "", {}), f"creative entry missing a required {field!r}: {entry}"


@then(parsers.re(r'(?P<quantifier>none of the returned|all returned) creatives have status "(?P<status>\w+)"'))
def then_returned_statuses(ctx: dict, quantifier: str, status: str) -> None:
    """Grade the status each returned creative carries ON THE WIRE."""
    creatives = _wire_creatives(ctx)
    assert creatives, "no creatives on the wire, so a status assertion over them is vacuous"
    statuses = {entry.get("status") for entry in creatives}
    if quantifier == "all returned":
        assert statuses == {status}, f"expected every creative to be {status!r}, got {sorted(statuses)}"
    else:
        assert status not in statuses, f"a {status!r} creative came back: {sorted(statuses)}"


@then(parsers.re(r"(?:creatives sorted by|the creatives are ordered by) created_date(?: descending| \(default\))?"))
def then_sorted_by_created_date(ctx: dict) -> None:
    """The pinned default ordering: created_date, descending."""
    _assert_wire_order(ctx, expected_newest_first=True)


@then("creatives sorted by name ascending")
def then_sorted_by_name_ascending(ctx: dict) -> None:
    """sort.field=name + sort.direction=asc, asserted as the exact name-ordered sequence.

    The seeded library's name order is the REVERSE of its created_at order, so a seller that
    ignored the sort object and answered with the default would fail here.
    """
    library = _library(ctx)
    expected = [record.creative_id for record in sorted(library, key=lambda record: record.label)]
    assert _wire_creative_ids(ctx) == expected[:_DEFAULT_PAGE_SIZE], (
        "expected the creatives name-ascending, got a different sequence"
    )


@then("creatives sorted by updated_date")
def then_sorted_by_updated_date(ctx: dict) -> None:
    """sort.field=updated_date — the enum's second member, and a third distinct order.

    The approved library's updated_at order differs from BOTH its created_at order and its
    name order, so this sequence can only come from honouring the named sort field.
    """
    ids_by_label = {record.label: record.creative_id for record in _library(ctx)}
    ordered_labels = [spec.label for spec in sorted(_APPROVED_SPECS, key=lambda spec: spec.updated_at, reverse=True)]
    missing = [label for label in ordered_labels if label not in ids_by_label]
    assert not missing, f"this Then expects the approved library; it was not seeded: {missing}"
    expected = [ids_by_label[label] for label in ordered_labels]
    assert _wire_creative_ids(ctx) == expected, (
        "expected the creatives ordered by updated_date descending, got a different sequence"
    )


@then(parsers.re(r'creatives with tag "(?P<tag>[^"]+)" returned'))
def then_creatives_with_tag_returned(ctx: dict, tag: str) -> None:
    """A single-member tags/tags_any filter returns exactly the creatives carrying it."""
    expected = {record.creative_id for record in _library(ctx) if tag in record.tags}
    assert expected, f"the Given seeded no creative tagged {tag!r}"
    _assert_returned_exactly(ctx, expected, f"the creatives tagged {tag!r}")


@then(parsers.re(r"only creatives with BOTH (?P<first>\w+) AND (?P<second>\w+) tags returned"))
def then_creatives_with_both_tags(ctx: dict, first: str, second: str) -> None:
    """``tags`` is AND: "all tags must match" (core/creative-filters.json)."""
    expected = {
        record.creative_id
        for record in _library(ctx)
        if {first, second} <= set(record.tags) and record.status != _ARCHIVED
    }
    assert expected, f"the Given seeded no non-archived creative carrying both {first!r} and {second!r}"
    _assert_returned_exactly(ctx, expected, f"the creatives tagged {first!r} AND {second!r}")


@then(parsers.re(r"creatives with EITHER (?P<first>\w+) OR (?P<second>\w+) tag returned"))
def then_creatives_with_either_tag(ctx: dict, first: str, second: str) -> None:
    """``tags_any`` is OR: "any tag must match" (core/creative-filters.json)."""
    expected = {
        record.creative_id
        for record in _library(ctx)
        if set(record.tags) & {first, second} and record.status != _ARCHIVED
    }
    assert expected, f"the Given seeded no non-archived creative tagged {first!r} or {second!r}"
    _assert_returned_exactly(ctx, expected, f"the creatives tagged {first!r} OR {second!r}")


@then(parsers.re(r'the returned creative has both tags "(?P<first>[^"]+)" and "(?P<second>[^"]+)"'))
def then_returned_creative_has_both_tags(ctx: dict, first: str, second: str) -> None:
    """Read the tags back OFF THE WIRE, not off the seeded row."""
    creatives = _wire_creatives(ctx)
    assert creatives, "no creatives on the wire"
    for entry in creatives:
        tags = set(entry.get("tags") or ())
        assert {first, second} <= tags, f"creative {entry.get('creative_id')!r} carries tags {sorted(tags)}"


@then(parsers.re(r'the creative with only tag "(?P<tag>[^"]+)" is not returned'))
def then_under_tagged_creative_excluded(ctx: dict, tag: str) -> None:
    """The AND counter-example: one matching tag is not enough.

    Asserted as the exact set the filter must return — which over this library is EMPTY,
    because the one creative seeded carries only one of the two tags asked for. Written as
    "the forbidden id is absent" it would also have held if the seller had returned nothing
    for an unrelated reason, or if the scenario's library had never been seeded; written as
    an exact set it holds only when the filter ran over the rows it was supposed to.
    """
    filter_tags = set(ctx["request"]["filters"]["tags"])
    under_tagged = {record.creative_id for record in _library(ctx) if record.tags == (tag,)}
    assert under_tagged, f"the Given seeded no creative whose only tag is {tag!r}"
    matching = {
        record.creative_id for record in _library(ctx) if filter_tags <= set(record.tags) and record.status != _ARCHIVED
    }
    _assert_returned_exactly(ctx, matching, f"the creatives carrying every tag in {sorted(filter_tags)}")


@then(parsers.re(r'the response contains creatives from both "(?P<first>[^"]+)" and "(?P<second>[^"]+)"'))
def then_creatives_from_both_media_buys(ctx: dict, first: str, second: str) -> None:
    """filters.media_buy_ids over two buys returns both buys' creatives and no others."""
    _assert_returned_exactly(ctx, ctx["expected_merged_creative_ids"], f"the {first} + {second} creatives")


@then(parsers.re(r"the filter resolves to media_buy_ids \[(?P<ids>.*)\] \(deduplicated\)"))
def then_media_buy_filter_deduplicated(ctx: dict, ids: str) -> None:
    """query_summary.filters_applied reports the applied filter — once per named buy.

    The buyer sent the id twice; the filter that was applied names it once, and
    filters_applied is where the buyer reads which filters ran (POST-S7).
    """
    expected = f"media_buy_ids={','.join(_quoted(ids))}"
    applied = (wire_dict(ctx).get("query_summary") or {}).get("filters_applied") or []
    assert expected in applied, f"expected {expected!r} among the applied filters, got {applied}"


@then(parsers.re(r'the creative for "(?P<media_buy_id>[^"]+)" is returned exactly once'))
def then_creative_returned_once(ctx: dict, media_buy_id: str) -> None:
    """A repeated media_buy_id must not multiply the rows the join produces."""
    _assert_returned_exactly(ctx, _ids_labelled(ctx, f"creative on {media_buy_id}"), f"the {media_buy_id} creative")


@then(parsers.re(r"creatives for (?P<media_buy_id>\w+) returned \(deduplicated, no duplicate results\)"))
def then_creatives_for_media_buy_deduplicated(ctx: dict, media_buy_id: str) -> None:
    """The boundary row's outcome: one row per creative assigned to the named buy."""
    expected = {
        record.creative_id
        for record in _library(ctx)
        if record.label in {f"creative on {media_buy_id}", "single tagged creative", "other tagged creative"}
    }
    assert expected, f"the Given seeded no creative on {media_buy_id}"
    _assert_returned_exactly(ctx, expected, f"the {media_buy_id} creatives")


def _assert_created_after(ctx: dict) -> None:
    """Assert exactly the creatives created after the cutoff the When sent came back."""
    request = ctx["request"]
    cutoff = datetime.fromisoformat(request["filters"]["created_after"])
    expected = {record.creative_id for record in _library(ctx) if record.created_at >= cutoff}
    assert expected, "the Given seeded no creative after the cutoff, so this assertion cannot fail"
    excluded = {record.creative_id for record in _library(ctx) if record.created_at < cutoff}
    assert excluded, "the Given seeded no creative BEFORE the cutoff, so a dropped filter would still pass"
    _assert_returned_exactly(ctx, expected, f"the creatives created after {cutoff.isoformat()}")


@then(parsers.re(r"creatives created after the date returned"))
def then_creatives_created_after_the_date(ctx: dict) -> None:
    _assert_created_after(ctx)


@then(parsers.re(r"the response contains creatives created after the specified (?:date|timestamp)"))
def then_response_contains_creatives_created_after(ctx: dict) -> None:
    _assert_created_after(ctx)


@then("only creatives within date range returned")
def then_creatives_within_date_range(ctx: dict) -> None:
    """created_after AND created_before compose into a closed interval."""
    request = ctx["request"]
    after = datetime.fromisoformat(request["filters"]["created_after"])
    before = datetime.fromisoformat(request["filters"]["created_before"])
    expected = {
        record.creative_id
        for record in _library(ctx)
        if after <= record.created_at <= before and record.status != _ARCHIVED
    }
    assert expected, "the Given seeded no creative inside the range"
    _assert_returned_exactly(ctx, expected, "the creatives inside the date range")


@then("creatives matching those IDs returned")
def then_creatives_matching_ids(ctx: dict) -> None:
    """filters.creative_ids at the maxItems boundary still answers with the named creatives."""
    named = set(ctx["request"]["filters"]["creative_ids"])
    expected = {record.creative_id for record in _library(ctx) if record.creative_id in named}
    assert expected, "the request named no seeded creative, so this assertion cannot fail"
    _assert_returned_exactly(ctx, expected, "the named creatives")


@then(parsers.re(r'the operation should fail with error code "(?P<code>[A-Z_]+)"'))
def then_operation_fails_with_code(ctx: dict, code: str) -> None:
    """Assert the refusal the buyer received, through the harness's one envelope helper.

    ``assert_wire_error`` defaults ``recovery`` from the pinned error-code table, so the
    assertion also grades the retry semantics without the scenario repeating them.

    Registered in this module because pytest-bdd resolves step definitions per MODULE: the
    identical sentence exists in steps/domain/uc019_query_media_buys.py, which is not a
    registered plugin, so it binds that module's scenarios and not these.
    """
    result = ctx.get("result")
    assert result is not None, "no TransportResult on the context — the When step did not dispatch"
    result.assert_wire_error(code)


@then(parsers.re(r"(?:the operation succeeds|no error is returned|no error is raised|the response is not an error)"))
def then_operation_succeeded(ctx: dict) -> None:
    """The dispatch was ANSWERED, not refused — read off the result, not off ctx bookkeeping."""
    result = ctx.get("result")
    assert result is not None, "no TransportResult on the context — the When step did not dispatch"
    assert result.is_success, f"the request was refused: {error_envelope_or_none(ctx)!r}"


@then(
    parsers.re(r"(?:no pricing_options in any creative|no creative in the response includes a pricing_options field)")
)
def then_no_pricing_options(ctx: dict) -> None:
    """include_pricing defaults to false, and pricing is then "not computed" (the pin's word)."""
    creatives = _wire_creatives(ctx)
    assert creatives, "no creatives on the wire, so an absence assertion over them is vacuous"
    carrying = [entry["creative_id"] for entry in creatives if entry.get("pricing_options") is not None]
    assert not carrying, f"pricing_options appeared without include_pricing: {carrying}"


@then(
    parsers.re(r"the creative in the response does not include (?P<what>a delivery snapshot|items data|variables data)")
)
def then_creative_omits_enrichment(ctx: dict, what: str) -> None:
    """include_snapshot / include_items / include_variables each default to FALSE."""
    field = {"a delivery snapshot": "snapshot", "items data": "items", "variables data": "variables"}[what]
    creatives = _wire_creatives(ctx)
    assert creatives, "no creatives on the wire, so an absence assertion over them is vacuous"
    carrying = [entry["creative_id"] for entry in creatives if entry.get(field) is not None]
    assert not carrying, f"{field!r} appeared without its include_ flag: {carrying}"


@then(
    parsers.re(
        r"(?:neither snapshot nor snapshot_unavailable_reason present"
        r"|the creative in the response includes neither a snapshot nor a snapshot_unavailable_reason)"
    )
)
def then_no_snapshot_fields(ctx: dict) -> None:
    """snapshot_unavailable_reason is "present only when include_snapshot was true"."""
    creatives = _wire_creatives(ctx)
    assert creatives, "no creatives on the wire, so an absence assertion over them is vacuous"
    for entry in creatives:
        assert entry.get("snapshot") is None, f"a snapshot appeared unasked: {entry['creative_id']}"
        assert entry.get("snapshot_unavailable_reason") is None, (
            f"a snapshot_unavailable_reason appeared with no snapshot requested: {entry['creative_id']}"
        )


@then(parsers.re(r'the creative is returned with status "(?P<status>\w+)"'))
def then_creative_returned_with_status(ctx: dict, status: str) -> None:
    """A stored status outside the closed enum is reported as *status* and still returned."""
    creatives = _wire_creatives(ctx)
    assert len(creatives) == 1, f"expected the one seeded creative, got {len(creatives)}"
    assert creatives[0].get("status") == status, (
        f"expected status {status!r} for the unreadable stored value, got {creatives[0].get('status')!r}"
    )


@then(
    parsers.re(
        r"(?:all fields included in each creative object|all fields included in response|all fields returned"
        r"|each creative in the response contains all available fields)"
    )
)
def then_all_fields_present(ctx: dict) -> None:
    """With ``fields`` omitted the response is UNPROJECTED: every field the seller holds.

    "Every field the seller holds" is what the Given seeded — assets, tags and a concept on
    top of the six required members — so a projection that dropped one is caught. It is not
    the 13-member enum: a seller that holds no snapshot for a creative omits it, which the
    pin permits (those members are typed only when present).
    """
    creatives = _wire_creatives(ctx)
    assert creatives, "no creatives on the wire to inspect"
    for entry in creatives:
        for field in (
            "creative_id",
            "name",
            "format_id",
            "status",
            "created_date",
            "updated_date",
            "tags",
            "assets",
            "concept_id",
            "concept_name",
        ):
            assert entry.get(field) not in (None, "", {}, []), f"creative entry is missing {field!r}: {sorted(entry)}"


@then("the creatives array should only include creatives whose format_id matches both agent_url and id")
def then_only_matching_format_id(ctx: dict) -> None:
    """A format_id filter is an OBJECT match: both members, or it is a different format."""
    _assert_returned_exactly(ctx, _ids_labelled(ctx, "display creative on this agent"), "the matching creative")


@then("the creatives array should NOT include creatives whose format_id has a different id even on the same agent_url")
def then_no_other_format_id(ctx: dict) -> None:
    """The counter-example: the sibling format on the SAME agent_url must stay out."""
    excluded = _ids_labelled(ctx, "video creative on this agent", "display creative on another agent")
    leaked = excluded & set(_wire_creative_ids(ctx))
    assert not leaked, f"the format_ids filter returned creatives of another format: {sorted(leaked)}"


@then("matching non-archived creatives are returned (archival exclusion applies)")
def then_matching_non_archived_returned(ctx: dict) -> None:
    """A name filter narrows the library; the archival default still applies on top.

    The library's archived creative matches the search too, which is what makes the second
    half of this outcome falsifiable rather than a restatement of the first.
    """
    needle = ctx["request"]["filters"]["name_contains"].lower()
    library = _library(ctx)
    assert any(needle in record.label.lower() and record.status == _ARCHIVED for record in library), (
        f"no ARCHIVED creative matches {needle!r}, so 'archival exclusion applies' cannot fail here"
    )
    expected = {
        record.creative_id for record in library if needle in record.label.lower() and record.status != _ARCHIVED
    }
    _assert_returned_exactly(ctx, expected, f"the non-archived creatives matching {needle!r}")


@given(
    parsers.re(r"the authenticated principal has (?:an |(?P<count>\d+) )?approved creatives? with package assignments")
)
def given_approved_creatives_with_assignments(ctx: dict, count: str | None) -> None:
    """Seed approved creatives carrying a DIFFERENT number of package assignments each.

    Differing counts are what make "includes assignment data" gradeable as a value rather
    than as key-presence: the block each creative carries has to be its own.
    """
    total = int(count) if count is not None else 1
    _seed_library(
        ctx,
        [
            _Spec(
                label=f"assigned creative {index}",
                created_at=_ANCHOR - timedelta(minutes=index),
                assignment_count=index + 1,
            )
            for index in range(total)
        ],
    )


@then(parsers.re(r"(?:each creative includes assignment data|the creative in the response includes assignment data)"))
def then_creatives_include_assignment_data(ctx: dict) -> None:
    """include_assignments defaults to TRUE, and the block carries each creative's own count.

    list-creatives-response.json shapes ``assignments`` as an object with a REQUIRED
    ``assignment_count`` plus ``assigned_packages``; the count asserted here is the one the
    Given seeded for that creative, so a block built for the wrong creative — or a constant
    one — fails.
    """
    seeded_counts = {record.creative_id: record.assignment_count for record in _library(ctx)}
    creatives = _wire_creatives(ctx)
    assert creatives, "no creatives on the wire to inspect"
    for entry in creatives:
        block = entry.get("assignments")
        assert isinstance(block, dict), f"creative {entry['creative_id']!r} carries no assignments block: {entry}"
        expected = seeded_counts[entry["creative_id"]]
        assert block.get("assignment_count") == expected, (
            f"creative {entry['creative_id']!r} was seeded with {expected} assignments, "
            f"the wire says {block.get('assignment_count')!r}"
        )
        assert len(block.get("assigned_packages") or []) == expected, (
            f"creative {entry['creative_id']!r} reports {expected} assignments but lists "
            f"{block.get('assigned_packages')!r}"
        )


@given(parsers.re(r"the authenticated principal has creatives in a (?:sandbox|production) account"))
@given("the authenticated principal has creatives")
def given_creatives_in_an_account(ctx: dict) -> None:
    """Seed the buyer's library for the sandbox rows.

    WHICH account it is belongs to the sibling Given ("the request targets a sandbox /
    production account"), which seeds the Account row and names it on the request; this
    step only supplies creatives for that account's library to contain, so the sandbox
    assertions are made over a non-empty response.
    """
    _seed_library(ctx, _APPROVED_SPECS)


# ── The delivery snapshot: a capability this seller declines, in the pin's own words ──
#
# enums/snapshot-unavailable-reason.json describes SNAPSHOT_UNSUPPORTED as "The seller
# platform does not support delivery snapshots for this entity", and that is this seller:
# no column holds a creative's lifetime impressions or last-served date. So every Given
# below seeds a plain creative — "whose snapshot is unavailable" is a state it is always in
# — and the Thens grade the DISCLOSURE, which is what the pin asks of a seller in that
# state. The rows that asserted a snapshot's presence, or one of the two enum members whose
# conditions this seller cannot enter, were corrected or deleted in the feature file, each
# with its citation there.


@given("the authenticated principal has an approved creative")
@given("the authenticated principal has an approved creative whose snapshot is unavailable")
@given(
    parsers.re(
        r"the authenticated principal has an approved creative whose snapshot is unavailable due to "
        r"the platform never supports snapshots"
    )
)
def given_one_approved_creative(ctx: dict) -> None:
    """Seed one approved creative, the subject of the snapshot-disclosure scenarios."""
    _seed_library(ctx, [_Spec(label="approved creative", created_at=_ANCHOR)])


@then(
    parsers.re(
        r"(?:each creative includes|the creative includes) a snapshot_unavailable_reason of "
        r'"(?P<reason>[A-Z_]+)"'
    )
)
@then(parsers.re(r'creative has snapshot_unavailable_reason "(?P<reason>[A-Z_]+)"'))
@then(parsers.re(r'snapshot_unavailable_reason is "(?P<reason>[A-Z_]+)"'))
def then_snapshot_unavailable_reason(ctx: dict, reason: str) -> None:
    """Every returned creative carries that machine-readable reason, and no snapshot.

    Both halves matter: the pin permits the reason "only when include_snapshot was true and
    snapshot data is unavailable for this creative", so a response carrying a reason AND a
    snapshot would be self-contradicting.
    """
    creatives = _wire_creatives(ctx)
    assert creatives, "no creatives on the wire to inspect"
    for entry in creatives:
        assert entry.get("snapshot_unavailable_reason") == reason, (
            f"creative {entry['creative_id']!r} should decline the snapshot with {reason!r}, "
            f"the wire says {entry.get('snapshot_unavailable_reason')!r}"
        )
        assert entry.get("snapshot") is None, (
            f"creative {entry['creative_id']!r} carries both a snapshot and a reason for not having one: {entry}"
        )


@then("the creative in the response omits the snapshot")
def then_creative_omits_snapshot(ctx: dict) -> None:
    """The snapshot itself is absent — the reason is what stands in its place."""
    creatives = _wire_creatives(ctx)
    assert creatives, "no creatives on the wire to inspect"
    carrying = [entry["creative_id"] for entry in creatives if entry.get("snapshot") is not None]
    assert not carrying, f"a snapshot came back from a seller that does not support them: {carrying}"


@then("the operation succeeds and returns the full creatives array")
def then_operation_succeeds_with_full_array(ctx: dict) -> None:
    """A declined enrichment degrades the response, never the listing.

    Exactly the seeded library comes back: the buyer asked for a snapshot the seller cannot
    give, and the answer is still every creative it asked about.
    """
    result = ctx.get("result")
    assert result is not None, "no TransportResult on the context — the When step did not dispatch"
    assert result.is_success, f"the listing failed instead of degrading: {error_envelope_or_none(ctx)!r}"
    _assert_returned_exactly(ctx, {record.creative_id for record in _library(ctx)}, "the whole seeded library")


# ── The fields projection ────────────────────────────────────────────
#
# The six members list-creatives-response.json marks REQUIRED on every creative item.
# They are on the wire whatever the buyer selected, because `fields` cannot express keeping
# them and a projection that dropped one would violate the schema the selection was made
# against. Read off the pinned schema rather than typed out here, so a future required
# member is picked up by both sides at once.
_REQUIRED_CREATIVE_FIELDS = frozenset(
    validator_for("creative/list-creatives-response.json").schema["properties"]["creatives"]["items"]["required"]
)

#: The two `fields` members that name more than one response member. The request schema
#: states the first ("The 'concept' value returns both concept_id and concept_name"); the
#: second is one answer in two shapes, the snapshot or the reason it is absent.
_FIELD_SELECTS_ON_THE_WIRE = {
    "concept": ("concept_id", "concept_name"),
    "snapshot": ("snapshot", "snapshot_unavailable_reason"),
}

#: Response members the `fields` enum cannot name — `assets` today. Read as the difference
#: between the two pinned schemas rather than listed, so an upstream change to either side
#: moves this set instead of silently contradicting it.
_UNSELECTABLE_WIRE_FIELDS = frozenset(
    validator_for("creative/list-creatives-response.json").schema["properties"]["creatives"]["items"]["properties"]
) - {
    wire_field
    for member in validator_for("creative/list-creatives-request.json").schema["properties"]["fields"]["items"]["enum"]
    for wire_field in _FIELD_SELECTS_ON_THE_WIRE.get(member, (member,))
}


@then(parsers.re(r"only the selected and required fields in (?:each creative object|response)"))
def then_only_selected_and_required_fields(ctx: dict) -> None:
    """The projected creative carries the selected members, the required ones, nothing else.

    The selection is read from the REQUEST this row sent, so the outline's rows each grade
    their own `fields` array through one sentence, and an extra member the seller failed to
    drop fails here as loudly as a missing one.
    """
    selected = ctx["request"].get("fields")
    assert selected, "this Then belongs on a row whose request carries a fields selection"
    # The required members, the selected ones, and the members the `fields` vocabulary
    # cannot name: a buyer with no way to ask for `assets` had no way to decline it either,
    # so a projection that dropped it would answer a selection the buyer never made.
    permitted = set(_REQUIRED_CREATIVE_FIELDS) | _UNSELECTABLE_WIRE_FIELDS
    for field in selected:
        permitted.update(_FIELD_SELECTS_ON_THE_WIRE.get(field, (field,)))

    creatives = _wire_creatives(ctx)
    assert creatives, "no creatives on the wire to inspect"
    for entry in creatives:
        unselected = sorted(set(entry) - permitted)
        assert not unselected, (
            f"creative {entry['creative_id']!r} carries members the buyer did not select: {unselected}"
        )
        missing = sorted(_REQUIRED_CREATIVE_FIELDS - set(entry))
        assert not missing, f"creative {entry['creative_id']!r} is missing required members: {missing}"


# ── The v3.1 boolean filters, and the enrichments read off the creative platform ──
#
# core/creative-variable.json sources a creative's variables from the creative platform
# ("variable_id: Variable identifier on the creative platform"), and AdCP standardizes no
# variables input on sync_creatives — the same position concept_id is in, so the same
# carrier: the creative's data blob. `has_variables` then asks whether that value is a
# non-empty array, which is a question the seller can answer from what it stores.

#: One dynamic-content variable, in the pinned shape. A single required text slot, so the
#: creative is unambiguously a DCO creative and the seeded value survives validation.
_DCO_VARIABLE = {
    "variable_id": "headline_text",
    "name": "Headline",
    "variable_type": "text",
    "default_value": "Summer Sale",
    "required": True,
}


def _seed_creative_with_variables(ctx: dict, *, label: str) -> Any:
    """Seed one approved creative carrying a dynamic-content variable on its data blob."""
    env = ctx["env"]
    tenant, principal = _get_or_create_tenant_and_principal(env)
    from tests.factories import CreativeFactory
    from tests.factories.creative_asset import build_assets, image_spec

    creative = CreativeFactory(
        tenant=tenant,
        principal=principal,
        approved=True,
        name=label,
        created_at=_ANCHOR,
        data={"assets": build_assets(image_spec("banner")), "variables": [_DCO_VARIABLE]},
    )
    ctx["tenant"] = tenant
    ctx["principal"] = principal
    return creative


@given(
    parsers.re(r"the authenticated principal has (?:an approved creative|a creative) with dynamic-content variables")
)
def given_creative_with_variables(ctx: dict) -> None:
    """Seed the DCO creative the include_variables scenarios read."""
    creative = _seed_creative_with_variables(ctx, label="dco creative")
    ctx["library"] = [_Seeded(creative.creative_id, "dco creative", "approved", (), _ANCHOR)]


@given("the authenticated principal has both creatives matching and not matching has_variables")
def given_dco_and_static_creatives(ctx: dict) -> None:
    """Seed one DCO creative and one static one, so either value of the filter partitions.

    Both halves are needed for either row to be falsifiable: a filter that was dropped
    returns both, and each row asserts exactly one of them.
    """
    dco = _seed_creative_with_variables(ctx, label="dco creative")
    static = _seed_creative(
        ctx["tenant"], ctx["principal"], name="static creative", created_at=_ANCHOR - timedelta(minutes=1)
    )
    ctx["library"] = [
        _Seeded(dco.creative_id, "dco creative", "approved", (), _ANCHOR),
        _Seeded(static.creative_id, "static creative", "approved", (), _ANCHOR - timedelta(minutes=1)),
    ]


@then(parsers.re(r"only creatives with dynamic variables \(DCO\) are returned"))
def then_only_dco_creatives(ctx: dict) -> None:
    """has_variables=true returns the DCO creative and not the static one."""
    _assert_returned_exactly(ctx, _ids_labelled(ctx, "dco creative"), "the creative carrying variables")


@then(parsers.re(r"only static creatives \(no dynamic variables\) are returned"))
def then_only_static_creatives(ctx: dict) -> None:
    """has_variables=false returns the static creative and not the DCO one."""
    _assert_returned_exactly(ctx, _ids_labelled(ctx, "static creative"), "the creative carrying no variables")


@given("the authenticated principal has a multi-asset creative with items")
def given_multi_asset_creative(ctx: dict) -> None:
    """Seed one creative whose assets are several distinct elements.

    That IS a multi-asset creative: core/creative-item.json calls an item an "Item within a
    multi-asset creative format ... composed of multiple distinct elements", and the
    elements this seller holds are the assets the buyer synced. A headline text asset plus
    two images gives the projection both of the discriminated shapes to produce.
    """
    from tests.factories.creative_asset import build_assets, image_spec, text_spec

    env = ctx["env"]
    tenant, principal = _get_or_create_tenant_and_principal(env)
    from tests.factories import CreativeFactory

    creative = CreativeFactory(
        tenant=tenant,
        principal=principal,
        approved=True,
        name="carousel creative",
        created_at=_ANCHOR,
        data={
            "assets": build_assets(
                image_spec("product_one"),
                image_spec("product_two"),
                text_spec("headline", content="Summer Sale"),
            )
        },
    )
    ctx["tenant"] = tenant
    ctx["principal"] = principal
    ctx["library"] = [_Seeded(creative.creative_id, "carousel creative", "approved", (), _ANCHOR)]
    ctx["expected_item_asset_ids"] = {"product_one", "product_two", "headline"}


@then("the creative includes items data")
def then_creative_includes_items(ctx: dict) -> None:
    """The items array names every element of the multi-asset creative.

    Asserted by ASSET ID rather than by length: an items array of the right size built from
    the wrong assets would pass a count, and the ids are what a buyer uses to match an item
    to the asset it came from.
    """
    creatives = _wire_creatives(ctx)
    assert len(creatives) == 1, f"expected the one seeded creative, got {len(creatives)}"
    items = creatives[0].get("items")
    assert items, f"include_items was requested but no items came back: {creatives[0]}"
    assert {item["asset_id"] for item in items} == ctx["expected_item_asset_ids"], (
        f"expected items for {sorted(ctx['expected_item_asset_ids'])}, got {sorted(i['asset_id'] for i in items)}"
    )
    for item in items:
        assert item["asset_kind"] in ("media", "text"), f"item carries no valid asset_kind: {item}"
        assert item.get("content_uri") or item.get("content"), f"item carries neither content_uri nor content: {item}"


@then("the creative includes variables data")
def then_creative_includes_variables(ctx: dict) -> None:
    """The variables array carries the seeded DCO slot, by id and by type."""
    creatives = _wire_creatives(ctx)
    assert len(creatives) == 1, f"expected the one seeded creative, got {len(creatives)}"
    variables = creatives[0].get("variables")
    assert variables, f"include_variables was requested but no variables came back: {creatives[0]}"
    assert [variable["variable_id"] for variable in variables] == [_DCO_VARIABLE["variable_id"]], (
        f"expected the seeded variable, got {variables}"
    )
    assert variables[0]["variable_type"] == _DCO_VARIABLE["variable_type"]


# ── Cursor traversal ─────────────────────────────────────────────────
#
# core/pagination-request.json takes an "Opaque cursor from a previous response to fetch
# the next page", and core/pagination-response.json returns one "Only present when has_more
# is true". Opaque means the scenario never builds a cursor: it carries back whatever the
# previous response handed it, which is the only thing a buyer can do with one.

_FIRST_PAGE_IDS_KEY = "first_page_creative_ids"


@when("the Buyer Agent sends a list_creatives request with the cursor from the previous response")
def when_list_creatives_with_cursor(ctx: dict) -> None:
    """Fetch the next page with the cursor the last response carried.

    The first page's creative_ids are recorded before dispatching, because the dispatch
    replaces the result this scenario's later Thens read — and "do not overlap with the
    first page" is a claim about two responses, so one of them has to be kept.
    """
    ctx[_FIRST_PAGE_IDS_KEY] = _wire_creative_ids(ctx)
    cursor = (wire_dict(ctx).get("pagination") or {}).get("cursor")
    assert cursor, "the previous response carried no cursor, so there is nothing to page with"
    kwargs = {"pagination": {"max_results": len(ctx[_FIRST_PAGE_IDS_KEY]), "cursor": cursor}}
    ctx["request"] = kwargs
    dispatch_request(ctx, **kwargs)


@then("the pagination includes a cursor for the next page")
def then_pagination_includes_cursor(ctx: dict) -> None:
    """A page with more behind it carries the cursor that reaches the rest."""
    pagination = wire_dict(ctx).get("pagination") or {}
    assert pagination.get("has_more") is True, (
        f"this Then belongs on a page that has more behind it; pagination says {pagination}"
    )
    assert pagination.get("cursor"), f"has_more is true but no cursor came back: {pagination}"


@then(parsers.re(r"the response contains (?P<count>\d+) creatives from the second page"))
def then_second_page_contains_n(ctx: dict, count: str) -> None:
    """The second page is the next slice of the seeded order, not a repeat of the first."""
    expected = _ids_by_created_at(ctx, newest_first=True)[
        len(ctx[_FIRST_PAGE_IDS_KEY]) : len(ctx[_FIRST_PAGE_IDS_KEY]) + int(count)
    ]
    assert _wire_creative_ids(ctx) == expected, (
        f"expected the {count} creatives following the first page, in the same order, got a different slice"
    )


@then("the creatives do not overlap with the first page results")
def then_pages_do_not_overlap(ctx: dict) -> None:
    """The counter-example: a cursor that restarted would return page one again."""
    first_page = set(ctx[_FIRST_PAGE_IDS_KEY])
    assert first_page, "no first page was recorded, so overlap cannot be judged"
    repeated = first_page & set(_wire_creative_ids(ctx))
    assert not repeated, f"the second page repeated creatives from the first: {sorted(repeated)}"
