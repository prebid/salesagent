"""BDD scenarios + steps for UC-018: list_creatives library queries.

Binds the UC-018 feature; two storyboard scenarios are wired (the rest xfail at
the conftest harness fixture):

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

Both pinned at v3.1-04f59d2d5 (adcp 3.1.0-beta.3).

Wired to real production across all wire transports (auto-parametrized; UC-018
-> CreativeListEnv via conftest ``_detect_uc`` / ``_harness_env``). The repo
sunsets the IMPL pseudo-transport in BDD, so the scenario runs on a2a/mcp/rest
(plus e2e_rest in-network). Each transport returns the same typed response, and
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
from tests.harness.transport import Transport
from tests.helpers.pinned_schema import validate_against_pinned_schema

# Three genuinely-different formats (display / video / audio) for the "three
# different formats" precondition. All three are in the standard format registry:
# the A2A path round-trips format_id through a string and re-validates it against
# known formats, so an unregistered id would be rejected on that transport.
_SYNCED_FORMATS = ("display_300x250", "video_640x480", "audio_30s")

# Bind the UC-018 feature. Only the @list-after-sync storyboard scenario is wired
# here (#1405); the remaining scenarios xfail fast at the conftest _harness_env
# fixture (no UC-018 harness yet). Whole-feature binding via scenarios() is the
# repo convention the CI shard-splitter requires (scripts/ci/shard_split.py).
scenarios("features/BR-UC-018-list-creatives.feature")


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

    Mutates the harness env identity so list_creatives is principal-scoped to
    this buyer, and records it so the seed step owns the creatives under the same
    principal the query authenticates as (list_creatives is principal-scoped — a
    mismatch would return an empty library).
    """
    env = ctx["env"]
    env._identity_cache.clear()
    env._principal_id = principal_id
    ctx["principal_id"] = principal_id
    # Post-condition: verify the identity mutation took effect.
    actual = env.identity.principal_id
    assert actual == principal_id, f"env.identity.principal_id is {actual!r} after setting {principal_id!r}"


@given("the buyer recently synced three creatives in three different formats via sync_creatives")
def given_recently_synced_three_creatives(ctx: dict) -> None:
    """Seed three approved creatives (one per format) owned by the authenticated buyer.

    Seeded via CreativeFactory rather than a live sync_creatives call — see the
    module docstring. Records the synced creative_ids for the Then steps.
    """
    from tests.factories import CreativeFactory

    env = ctx["env"]
    tenant, principal = _get_or_create_tenant_and_principal(env)
    synced_ids: list[str] = []
    for fmt in _SYNCED_FORMATS:
        creative = CreativeFactory(
            tenant=tenant,
            principal=principal,
            format=fmt,
            status="approved",
            # An empty-but-present assets object: the repository filters out rows
            # whose data["assets"] IS NULL (legacy guard), so the key must exist;
            # keeping it empty stays schema-valid (no asset-union coupling — the
            # storyboard grades the listing contract, not asset shape).
            data={"assets": {}},
        )
        synced_ids.append(creative.creative_id)
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

    Seeded via CreativeFactory rather than a live sync (CreativeListEnv has no sync
    patches; the obligation under test is the listing/filter contract). The
    empty-but-present ``assets`` is mandatory — the repository drops rows whose
    ``data["assets"]`` IS NULL (legacy guard).
    """
    from tests.factories import CreativeFactory

    env = ctx["env"]
    tenant, principal = _get_or_create_tenant_and_principal(env)

    in_concept_ids: list[str] = []
    for fmt in _CONCEPT_FORMATS:
        creative = CreativeFactory(
            tenant=tenant,
            principal=principal,
            format=fmt,
            status="approved",
            data={"assets": {}, "concept_id": concept_id, "concept_name": _CONCEPT_NAME},
        )
        in_concept_ids.append(creative.creative_id)

    # Decoy under a different concept.
    CreativeFactory(
        tenant=tenant,
        principal=principal,
        format=_CONCEPT_FORMATS[0],
        status="approved",
        data={"assets": {}, "concept_id": _DECOY_CONCEPT_ID, "concept_name": "Winter 2025 Campaign"},
    )
    # Decoy with no concept at all.
    CreativeFactory(
        tenant=tenant,
        principal=principal,
        format=_CONCEPT_FORMATS[0],
        status="approved",
        data={"assets": {}},
    )

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
