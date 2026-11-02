"""BDD step definitions for the UC-006 storyboard-conformance scenarios.

Covers the six ``@uc006-storyboard-routing @storyboard-v3.1`` scenarios in
``BR-UC-006-sync-creatives.feature`` (provenance_enforcement Phases 2/3/5/6,
creative/index.yaml sync_multiple, and the media-buy/index.yaml format_id
roundtrip). These scenarios previously auto-xfailed at
``StepDefinitionNotFoundError`` — every Given/When/Then below is new
.

The provenance scenarios grade ``check_provenance_policy``
(src/core/tools/creatives/_validation.py): a creative that does not meet the
product's provenance policy is a per-creative ``action="failed"`` result carrying
the pin's ``PROVENANCE_REQUIRED`` / ``PROVENANCE_DIGITAL_SOURCE_TYPE_MISSING`` /
``PROVENANCE_DISCLOSURE_MISSING`` code (core/creative-policy.json makes the
refusal a MUST). Every Then in this module asserts unconditionally: a dispatch
error or a wrong value FAILS. A genuine production gap is registered the ONE
sanctioned way — a scenario/Examples-row tag in the ratcheted ledger
(``_UC006_SPECGAP_XFAIL_TAGS``, tests/bdd/conftest.py) — never as a
per-assertion escape hatch inside a step body.
An earlier revision inlined per-assertion xfails here, which meant a 401
regression, a 500 and a timeout all came out green and indistinguishable from a
real spec gap; a scenario-level tag cannot make that mistake because it names
WHICH scenario is known-broken rather than swallowing whatever happens to go
wrong inside it. Enforced by
``tests/unit/test_architecture_bdd_no_inline_xfail.py``.

Reuses the private helpers already established in uc006_sync_creatives.py
(``_ensure_tenant_principal``, ``_build_creative_payload``,
``_setup_product_with_creative_policy``, ``_action_str``,
``when_sync_creative``) rather than re-deriving creative-payload/dispatch
logic — the same cross-file private-import precedent
``uc005_format_id_roundtrip.py`` uses against ``uc005_format_id_shape.py``.
"""

from __future__ import annotations

import asyncio

import pytest
from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import is_e2e, payload_or_none, wire_dict
from tests.bdd.steps.domain.uc006_sync_creatives import (
    _action_str,
    _build_creative_payload,
    _creative_format_id_entry,
    _product_format_entry,
    _setup_product_with_creative_policy,
    when_sync_creative,
)
from tests.bdd.steps.generic._account_resolution import ensure_tenant_principal
from tests.bdd.steps.generic._dispatch import gate_and_record
from tests.factories.creative_asset import build_assets, image_spec, text_spec, url_spec, video_spec
from tests.factories.request import CreativeAssetRequestFactory

# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — provenance structural-rejection scenarios
# (provenance_enforcement Phases 2/3/5/6; PROVENANCE_REQUIRED/
# PROVENANCE_DIGITAL_SOURCE_TYPE_MISSING/PROVENANCE_DISCLOSURE_MISSING)
# ═══════════════════════════════════════════════════════════════════════


@given("the tenant has a product with creative_policy.provenance_requirements.require_digital_source_type = true")
def given_tenant_product_requires_digital_source_type(ctx: dict) -> None:
    """Product creative_policy nests provenance_requirements.require_digital_source_type.

    ``provenance_required`` rides along: core/creative-policy.json says the requirements
    object "refines provenance_required" and, when that flag is false or absent,
    "receivers MUST ignore it" -- a policy carrying only the refinement asks for nothing.
    """
    _setup_product_with_creative_policy(
        ctx,
        creative_policy={
            "provenance_required": True,
            "provenance_requirements": {"require_digital_source_type": True},
        },
    )


@given("the tenant has a product with creative_policy.provenance_requirements.require_disclosure_metadata = true")
def given_tenant_product_requires_disclosure_metadata(ctx: dict) -> None:
    """Product creative_policy nests provenance_requirements.require_disclosure_metadata (see above)."""
    _setup_product_with_creative_policy(
        ctx,
        creative_policy={
            "provenance_required": True,
            "provenance_requirements": {"require_disclosure_metadata": True},
        },
    )


@given("the Buyer Agent submits a creative whose provenance object omits digital_source_type")
def given_creative_provenance_omits_digital_source_type(ctx: dict) -> None:
    """Provenance dict present but missing digital_source_type.

    ``digital_source_type`` is optional on the wire-level Provenance model
    (``adcp.types.generated_poc.core.provenance.Provenance`` — the type
    actually bound to ``CreativeAsset.provenance``), so this dict is
    otherwise fully valid: sent as a raw dict rather than a constructed
    ``Provenance`` model so the wire payload genuinely represents "the buyer
    omitted the field", not a harness-side pre-validation.
    """
    _build_creative_payload(
        ctx,
        provenance={"human_oversight": "selected"},
    )


@given("the Buyer Agent submits a creative whose provenance object lacks a disclosure block")
def given_creative_provenance_lacks_disclosure(ctx: dict) -> None:
    """Provenance dict carries digital_source_type but omits the disclosure field."""
    _build_creative_payload(
        ctx,
        provenance={"digital_source_type": "digital_capture"},
    )


@given("a creative submission that previously failed with provenance rejection codes")
def given_creative_submission_previously_failed(ctx: dict) -> None:
    """Narrative precondition for the corrected-resubmission scenario.

    Production's provenance handling (``check_provenance_policy``) does not
    persist any prior-rejection state to replay — the subsequent "resubmits"
    Given builds the corrected payload fresh. This step only seeds
    tenant/principal so the storyboard's narrative sequencing (prior failure
    -> corrected resubmission) reads as two Givens, matching the feature
    text, without duplicating DB setup the next step already performs.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)


@given(
    "the Buyer Agent resubmits with a complete disclosure block and an on-list verify_agent "
    "from the seller's accepted_verifiers"
)
def given_creative_resubmits_corrected(ctx: dict) -> None:
    """Build a creative payload with a complete, valid provenance object.

    Shape matches ``adcp.types.generated_poc.core.provenance.Provenance``
    (the type bound to ``CreativeAsset.provenance``): ``disclosure`` is a
    ``Disclosure`` object (``{"required": bool}``), ``human_oversight`` is
    one of the ``HumanOversight`` enum values, and ``verification`` is a
    LIST of ``VerificationItem`` objects — not the flat strings this
    scenario's narrative suggests. ``accepted_verifiers``/verify_agent
    trust-listing is not implemented in production (see the module
    docstring) — nothing gates on ``verification``'s contents. What this
    Given DOES exercise for real: a well-formed provenance object must not
    be rejected by anything production DOES implement, so the corrected
    resubmission genuinely succeeds.
    """
    _build_creative_payload(
        ctx,
        provenance={
            "digital_source_type": "trained_algorithmic_media",
            "human_oversight": "edited",
            "disclosure": {"required": True},
            "verification": [
                {
                    "verified_by": "https://verify.creative.example.com",
                    "result": "authentic",
                }
            ],
        },
    )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — bulk multi-format sync (creative/index.yaml sync_multiple)
# ═══════════════════════════════════════════════════════════════════════


@given("the Buyer Agent submits three creatives in three different formats in a single sync_creatives call")
def given_three_creatives_three_formats(ctx: dict) -> None:
    """Build three creative payloads (display/video/native) for one sync_creatives call.

    CreativeSyncEnv's mocked registry (``get_format``) returns a fixed truthy
    format spec regardless of the requested format_id/agent_url, so distinct
    format_ids resolve without per-format mock wiring — the scenario exercises
    "one call, three creatives, three per-creative results", not per-format
    catalog behavior (that is UC-005's concern).
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    display = _creative_format_id_entry(ctx, env)
    agent_url = display["agent_url"]
    # The video and native formats must be ones the transport's agent SERVES: on
    # e2e_rest the real reference agent (v3.1.1 catalog, mirrored by the SDK's
    # v1-reference-formats.json) answers, and an unserved id is a failed entry with no
    # status -- which would grade the catalog, not the per-creative status. Their asset
    # slots are the catalog's required ones for the same reason.
    if is_e2e(ctx):
        video_id, native_id = "video_standard_30s", "native_standard"
        video_assets = build_assets(video_spec("video_file", url="https://example.com/video.mp4"))
        native_assets = build_assets(
            text_spec("title", content="Discover something new"),
            text_spec("description", content="A native placement"),
            image_spec("main_image", url="https://example.com/native.png"),
            text_spec("cta_text", content="Learn more"),
            text_spec("sponsored_by", content="Acme"),
        )
    else:
        video_id, native_id = "video_30s", "native_content"
        video_assets = build_assets(video_spec("video", url="https://example.com/video.mp4"))
        native_assets = build_assets(
            text_spec("headline", content="Discover something new"),
            image_spec("main_image", url="https://example.com/native.png"),
        )

    creatives = [
        CreativeAssetRequestFactory.payload(
            creative_id="creative-bulk-display-001",
            name="Bulk Display Creative",
            format_id=display,
            assets=build_assets(image_spec("banner_image", url="https://example.com/banner.png")),
        ),
        CreativeAssetRequestFactory.payload(
            creative_id="creative-bulk-video-001",
            name="Bulk Video Creative",
            format_id={"id": video_id, "agent_url": agent_url},
            assets=video_assets,
        ),
        CreativeAssetRequestFactory.payload(
            creative_id="creative-bulk-native-001",
            name="Bulk Native Creative",
            format_id={"id": native_id, "agent_url": agent_url},
            assets=native_assets,
        ),
    ]
    ctx.setdefault("creatives", []).extend(creatives)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / WHEN steps — format_id roundtrip on sync (media-buy/index.yaml)
# ═══════════════════════════════════════════════════════════════════════


@given("the Buyer Agent captured a format_id {agent_url, id} from a prior get_products response")
def given_captured_format_id_from_get_products_for_sync(ctx: dict) -> None:
    """Call get_products in-process to capture a real format_id, then send it to sync_creatives.

    Mirrors ``uc005_format_id_roundtrip.given_captured_format_id_from_get_products``
    (same capture mechanism), but seeds a Product on CreativeSyncEnv's tenant
    instead of CreativeFormatsEnv's — the two envs are unrelated harnesses so
    the capture must run against whichever DB/session this scenario's env owns.

    On e2e_rest, sync_creatives runs against the real Docker creative-agent
    container (no format-registry mock), so the captured format_id must be one
    that's actually registered there — the same real agent_url/format_id
    ``uc006_sync_creatives._format_payload`` uses for e2e, not the mocked
    in-process default.
    """
    from src.core.schemas import GetProductsRequest
    from src.core.tools.products import _get_products_impl
    from tests.factories import PricingOptionFactory, ProductFactory

    # TRANSPORT-BYPASS: this Given step seeds the scenario by calling get_products
    # in-process to capture a real format_id. It is not a When dispatch — there is
    # no transport parametrization for the capture step; transport only varies the
    # subsequent sync_creatives call in the When step.
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    # The switch lives in _product_format_entry, not inline here. An inline copy is a second
    # place the two transports' formats can drift apart, which is the defect this module's
    # sibling site at given_three_creatives_three_formats actually had (salesagent-6mm5z).
    entry = _product_format_entry(ctx, env)
    agent_url, format_id = entry["agent_url"], entry["id"]

    product = ProductFactory(tenant=tenant, format_ids=[entry])
    PricingOptionFactory(product=product)
    env._commit_factory_data()

    req = GetProductsRequest(brief="format_id roundtrip test")
    response = asyncio.run(_get_products_impl(req, env.identity))

    assert response.products, "get_products returned no products — cannot capture format_id"
    captured_product = response.products[0]
    assert captured_product.format_ids, "product has no format_ids — cannot capture format_id"

    fid = captured_product.format_ids[0]
    ctx["captured_format_id"] = {"agent_url": str(fid.agent_url), "id": str(fid.id)}


@when("the Buyer Agent sends sync_creatives carrying a creative whose format_id matches the captured object")
def when_sync_creative_with_captured_format_id(ctx: dict) -> None:
    """Send sync_creatives with a creative whose format_id is the captured {agent_url, id} verbatim.

    On e2e_rest the real Docker creative-agent expects assets matching its
    registered format's asset roles, not the mocked in-process shape — mirrors
    ``uc006_sync_creatives._format_payload``'s e2e asset set.
    """
    captured = ctx["captured_format_id"]
    if is_e2e(ctx):
        assets = build_assets(
            image_spec("banner_image", url="https://example.com/banner.png"),
            url_spec("click_url", url="https://example.com/landing"),
        )
    else:
        assets = build_assets(image_spec("banner_image", url="https://example.com/banner.png"))
    creative_payload = CreativeAssetRequestFactory.payload(
        creative_id="creative-format-roundtrip-001",
        name="Format ID Roundtrip Creative",
        # The CAPTURED object verbatim, which is the scenario's whole subject: a
        # seller must accept back the format_id it handed out. Overrides reach the
        # wire unmodified, so an un-normalised agent_url stays un-normalised here.
        format_id={"id": captured["id"], "agent_url": captured["agent_url"]},
        assets=assets,
    )
    ctx.setdefault("creatives", []).append(creative_payload)
    when_sync_creative(ctx)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — shared response-shape helpers
# ═══════════════════════════════════════════════════════════════════════


def _require_response(ctx: dict, expectation: str) -> object:
    """Return the dispatch's typed payload, or FAIL — a dispatch error is never a "known gap".

    Reads the dispatch's own ``TransportResult`` through ``payload_or_none``, so
    the value arrives WITH its provenance instead of as a detached payload copy
    that cannot tell a wire fact from an in-process reconstruction (enforced by
    ``tests/unit/test_architecture_bdd_wire_discipline.py`` Check D).
    ``payload_or_none`` — not ``require_payload`` — because the expectation-aware
    diagnostic below is the whole point of this helper: it names WHAT was
    expected and forbids swallowing the failure.

    This replaces a ``_response_or_xfail`` helper that turned ANY dispatch
    failure into a green xfail. Its docstring argued that an operation-level
    exception "is itself evidence production has not implemented the graded
    behavior" — but an exception is evidence of SOMETHING, and the step cannot
    tell which: a 401 auth regression, a 500, a timeout and a harness wiring gap
    all arrived here and all came out green.

    A genuine known gap is registered exactly ONE way in this repo — a
    scenario/Examples-row tag in the ratcheted ledger — never as a per-assertion
    escape hatch inside a step body. So this asserts, and a real gap is ledgered
    by tag instead.
    """
    resp = payload_or_none(ctx)
    err = ctx.get("error")
    assert resp is not None, (
        f"Expected {expectation}, but the dispatch produced no response "
        f"({type(err).__name__ if err else 'no response and no error'}: {err}). "
        "If this is a genuine spec-production gap, ledger the scenario's tag; "
        "do not swallow it here."
    )
    return resp


def _first_creative_result(ctx: dict, expectation: str) -> object:
    """Return the first per-creative result off the dispatch payload's ``creatives``.

    ASSERTS on absence rather than xfailing it: an empty ``creatives``
    list means the seller returned no per-creative result at all, which is a
    failure of the obligation, not a known gap to be tolerated.
    """
    resp = _require_response(ctx, expectation)
    results = resp.creatives
    assert results, f"Expected at least one per-creative result for {expectation}, got: {resp}"
    return results[0]


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — response envelope schema validity
# ═══════════════════════════════════════════════════════════════════════


@then("the response envelope should be schema-valid against sync-creatives-response.json")
def then_response_envelope_schema_valid(ctx: dict) -> None:
    """Validate the serialized sync_creatives response against the pinned AdCP JSON schema.

    Asserts on the TRANSPORT WIRE via ``wire_dict``. This used to re-serialize
    the in-memory response with ``model_dump(mode="json")`` because
    CreativeSyncEnv's A2A dispatch bypassed ``_run_a2a_handler`` and so stashed
    no A2A wire — meaning the step graded a fresh serialization of an in-process
    object rather than anything the buyer received, and could not have caught a
    transport that mangled the envelope. That bypass is gone (the env now
    routes through the real A2A pipeline), so the wire exists on every transport
    and the fallback is deleted rather than kept as a quiet escape hatch.

    ``wire_dict``'s guard is the point: a real-wire transport that stops stashing
    now raises loudly here instead of silently degrading to a self-consistent
    re-serialization. IMPL legitimately has no wire and serializes the typed
    payload through the production serializer.
    """
    from tests.helpers.adcp_schema_validator import AdCPSchemaValidator, SchemaValidationError

    _require_response(ctx, "a sync-creatives response envelope")
    payload = wire_dict(ctx)

    async def _validate() -> None:
        async with AdCPSchemaValidator() as validator:
            await validator.validate_response("sync-creatives", payload)

    try:
        asyncio.run(_validate())
    except SchemaValidationError as exc:
        pytest.fail("sync-creatives response failed AdCP schema validation: " + "; ".join(exc.validation_errors))


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — per-creative action assertions
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('the per-creative result should report action "{action}"'))
def then_per_creative_result_reports_action(ctx: dict, action: str) -> None:
    """Assert the first per-creative result's action matches *action*.

    The message carries the observed action and its errors/warnings, so a wrong
    action reads as what production actually answered.
    """
    first = _first_creative_result(ctx, f'a per-creative result with action="{action}"')
    actual = _action_str(first.action)
    assert actual == action, (
        f"expected per-creative action={action!r}, production returned {actual!r} "
        f"(errors={first.errors!r}, warnings={first.warnings!r})"
    )


@then('the per-creative result should report action "created" or "updated"')
def then_per_creative_result_created_or_updated(ctx: dict) -> None:
    """Assert the first per-creative result's action is created or updated.

    A well-formed, spec-compliant resubmission is expected to be accepted.
    If production instead rejects it, that is a genuine gap worth
    surfacing rather than masking: xfail with the observed errors instead
    of a bare assertion failure.
    """
    first = _first_creative_result(ctx, 'a per-creative result with action="created" or "updated"')
    actual = _action_str(first.action)
    if actual not in ("created", "updated"):
        raise AssertionError(
            f"SPEC-PRODUCTION GAP: expected per-creative action 'created' or 'updated' for a "
            f"well-formed corrected resubmission, production returned {actual!r} "
            f"(errors={first.errors!r}) — production's internal Creative.provenance model "
            "(src/core/schemas/creative.py) is structurally incompatible with the wire-level "
            "adcp.types Provenance shape it is converted from (disclosure: str vs. a Disclosure "
            "object, human_oversight: bool vs. an enum, verification: dict vs. a list), so a "
            "spec-compliant provenance object is unconditionally rejected on internal re-validation."
        )


@then('the per-creative result should NOT report action "failed"')
def then_per_creative_result_not_failed(ctx: dict) -> None:
    """Assert the first per-creative result's action is NOT 'failed'."""
    first = _first_creative_result(ctx, 'a per-creative result with action != "failed"')
    actual = _action_str(first.action)
    assert actual != "failed", f"Expected action != 'failed', got 'failed' (errors={first.errors!r})"


@then('the per-creative result should NOT report action "failed" due to format_id rejection')
def then_per_creative_result_not_failed_due_to_format_id(ctx: dict) -> None:
    """Assert the seller accepted its own advertised format_id (action != failed)."""
    first = _first_creative_result(ctx, "a per-creative result accepting the seller's own format_id")
    actual = _action_str(first.action)
    assert actual != "failed", (
        f"Seller rejected its own advertised format_id: action=failed, errors={first.errors!r} "
        "(AdCP format_id roundtrip: seller MUST accept its own format_ids on sync_creatives)"
    )


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — bulk multi-format sync assertions
# ═══════════════════════════════════════════════════════════════════════


@then("the creatives array should carry one result per submitted creative")
def then_creatives_array_one_result_per_submitted(ctx: dict) -> None:
    """Assert response.creatives has exactly one entry per submitted creative."""
    resp = _require_response(ctx, "one per-creative result per submitted creative")
    submitted = ctx.get("creatives", [])
    assert len(resp.creatives) == len(submitted), (
        f"Expected {len(submitted)} per-creative results (one per submitted creative), got {len(resp.creatives)}"
    )


@then("every per-creative result should expose an action field")
def then_every_result_exposes_action(ctx: dict) -> None:
    """Assert every per-creative result carries a non-None action field.

    DECOMPOSED from a compound "action and status" step.
    While the two obligations shared one step, the status half — a known
    production gap — aborted the scenario, which made the sibling
    action-value assertion below DEAD CODE on all three transports. Splitting
    them lets the action obligations run live while the status obligation is
    ledgered on its own scenario.
    """
    resp = _require_response(ctx, "per-creative results exposing an action")
    assert resp.creatives, "Expected at least one per-creative result"
    for result in resp.creatives:
        assert result.action is not None, f"per-creative result {result.creative_id!r} has no action"


@then("every per-creative result should expose a status field")
def then_every_result_exposes_status(ctx: dict) -> None:
    """Assert every per-creative result carries a non-None status field.

    The other half of the decomposition above. A synced creative has a review state
    (this seller has a review lifecycle), and sync-creatives-response.json makes the
    per-creative ``status`` that state's advisory mirror.
    """
    resp = _require_response(ctx, "per-creative results exposing a status")
    assert resp.creatives, "Expected at least one per-creative result"
    missing = [r.creative_id for r in resp.creatives if r.status is None]
    assert not missing, f"per-creative results {missing!r} carry no status"


@then(parsers.parse('every action value should be "{a1}", "{a2}", or "{a3}"'))
def then_every_action_value_in_set(ctx: dict, a1: str, a2: str, a3: str) -> None:
    """Assert every per-creative action is one of the three listed values."""
    resp = _require_response(ctx, f'every action in ("{a1}", "{a2}", "{a3}")')
    allowed = {a1, a2, a3}
    actions = [_action_str(result.action) for result in resp.creatives]
    assert all(a in allowed for a in actions), f"Expected every action in {allowed}, got {actions}"


@then("every status value should be drawn from the creative-status enum")
def then_every_status_in_creative_status_enum(ctx: dict) -> None:
    """Assert every per-creative status is a member of the pinned creative-status enum.

    sync-creatives-response.json: "Values come from CreativeStatus only (processing,
    pending_review, approved, suspended, rejected, archived) -- never from CreativeAction."
    """
    from adcp.types.generated_poc.enums.creative_status import CreativeStatus

    resp = _require_response(ctx, "every status drawn from the creative-status enum")
    statuses = [result.status for result in resp.creatives]
    members = {member.value for member in CreativeStatus}
    outside = [s for s in statuses if (s.value if hasattr(s, "value") else s) not in members]
    assert not outside, f"per-creative statuses {outside!r} are not CreativeStatus members ({sorted(members)})"
    valid = {member.value for member in CreativeStatus}
    assert all(s in valid for s in statuses if s is not None), f"Expected every status in {valid}, got {statuses}"


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — format_id roundtrip verification
# ═══════════════════════════════════════════════════════════════════════


@then("the seller's own format_id object should roundtrip through sync_creatives without modification")
def then_format_id_roundtrips_verbatim(ctx: dict) -> None:
    """Assert the creative's format_id roundtrips verbatim — ON THE WIRE first.

    "Roundtrip without modification" is a claim about what the BUYER can read
    back, so the PRIMARY assertion reads the creative back over the transport via
    ``list_creatives`` and compares the wire's ``format_id`` to the captured
    ``{agent_url, id}``. Grading only the DB row (as this step used to) cannot
    detect a seller that persists the format_id correctly and then serializes it
    wrong on the wire — which is exactly the roundtrip being graded.

    ``CreativeSyncEnv`` exposes no ``list_creatives`` primitive (its MCP_TOOL /
    REST_ENDPOINT are sync-only), but ``AdCPTestClient.call`` resolves any tool
    off the address table on the SAME env, so no second env and no raw repository
    read is introduced as the primary check.

    The ``CreativeRepository`` read is KEPT, after the wire assertion, as a
    redundant in-process check: if the two ever disagree, the wire is the
    buyer-facing contract and the DB read localizes the fault.
    """
    from src.core.database.repositories.creative import CreativeRepository
    from tests.harness.client import AdCPTestClient

    first = _first_creative_result(ctx, "a persisted creative whose format_id roundtrips verbatim")
    captured = ctx["captured_format_id"]

    env = ctx["env"]

    # ── PRIMARY: read the creative back over the wire ──────────────────────
    client = ctx.get("client") or AdCPTestClient(env)
    # A READ-BACK still reaches a transport, so it owes the same two obligations as
    # any other dispatch. It does NOT go through ``dispatch_via_client``: that entry
    # is the single writer of the ctx dispatch-result contract, and this read-back
    # must not clobber the result the scenario is actually grading.
    gate_and_record({})
    listed = client.call("list_creatives", {}, ctx["transport"])
    wire = listed.wire_response
    assert isinstance(wire, dict), (
        f"list_creatives returned no wire body to verify the roundtrip against "
        f"(error={listed.error!r}); the roundtrip cannot be graded on this transport"
    )
    listed_ids = {c.get("creative_id") for c in (wire.get("creatives") or [])}
    assert first.creative_id in listed_ids, (
        f"creative {first.creative_id!r} did not come back on the list_creatives wire; "
        f"wire listed {sorted(i for i in listed_ids if i)!r}"
    )
    entry = next(c for c in wire["creatives"] if c.get("creative_id") == first.creative_id)
    # Both halves of the v3.1 federation pair are asserted UNCONDITIONALLY. The
    # agent_url half used to be gated on `if wire_agent_url is not None`, so a wire
    # that dropped agent_url — or flattened format_id back to a bare string —
    # passed silently while the id half stayed strict: the same
    # grades-nothing-when-absent defect as the C4 status check, one field over.
    wire_format = entry.get("format_id")
    assert isinstance(wire_format, dict), (
        f"format_id did not roundtrip as the v3.1 {{agent_url, id}} pair on the wire: "
        f"expected a mapping, wire carried {wire_format!r}"
    )
    assert wire_format.get("id") == captured["id"], (
        f"format_id.id did not roundtrip on the wire: expected {captured['id']!r}, "
        f"wire carried {wire_format.get('id')!r}"
    )
    assert wire_format.get("agent_url") == captured["agent_url"], (
        f"format_id.agent_url did not roundtrip on the wire: "
        f"expected {captured['agent_url']!r}, wire carried {wire_format.get('agent_url')!r}"
    )

    # ── REDUNDANT in-process check (localizes a wire/DB disagreement) ──────
    session = env.get_session()
    repo = CreativeRepository(session, env._tenant_id)
    db_creative = repo.get_by_id(first.creative_id, env._principal_id)
    assert db_creative is not None, f"Creative {first.creative_id!r} not found in DB after sync"
    assert db_creative.format == captured["id"], (
        f"format_id.id did not roundtrip: expected {captured['id']!r}, stored {db_creative.format!r}"
    )
    assert db_creative.agent_url == captured["agent_url"], (
        f"format_id.agent_url did not roundtrip: expected {captured['agent_url']!r}, stored {db_creative.agent_url!r}"
    )
