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
logic.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import (
    assert_listed_on_canonical_kind,
    assert_sync_entry_refused_and_not_stored,
    is_e2e,
    payload_or_none,
    stored_creative_column,
    wire_dict,
)
from tests.bdd.steps.domain.uc006_sync_creatives import (
    _action_str,
    _build_creative_payload,
    _creative_format_id_entry,
    _e2e_unique_id,
    _product_format_entry,
    _setup_product_with_creative_policy,
    latest_creative_id,
    when_sync_creative,
)
from tests.bdd.steps.generic._account_resolution import ensure_tenant_principal
from tests.bdd.steps.generic._dispatch import dispatch_via_client
from tests.factories.creative import format_option_ref_for_scope
from tests.factories.creative_asset import (
    build_assets,
    canonical_assets,
    named_format_assets,
    url_spec,
)
from tests.factories.request import OMIT, CreativeAssetRequestFactory

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
    (``adcp.types.domains.core.provenance.Provenance`` — the type
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

    Shape matches ``adcp.types.domains.core.provenance.Provenance``
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
    format spec regardless of the requested format_id/agent_url, so the EXISTENCE
    check passes without per-format mock wiring — the scenario exercises
    "one call, three creatives, three per-creative results", not per-format
    catalog behavior (that is UC-005's concern).

    THE PROJECTION IS A SECOND QUESTION and ``get_format`` is not on its path. The
    canonical kind comes from ``find_format(all_formats, ...)``, which this env once
    answered with ``[]`` -- so ``native_content``, which the reference agent SERVES and
    no registry glob matches, had no canonical projection and was refused. The env now
    serves the capture in process by default, which is what the e2e server does, so the
    four transports resolve these three ids the same way with nothing to arrange here.
    """
    env = ctx["env"]
    ensure_tenant_principal(ctx, env)
    display = _creative_format_id_entry(ctx, env)
    # The video and native formats must be ones the transport's agent SERVES: on
    # e2e_rest the real reference agent (v3.1.1 catalog, mirrored by the SDK's
    # v1-reference-formats.json) answers, and an unserved id is a failed entry with no
    # status -- which would grade the catalog, not the per-creative status. Their asset
    # slots are the catalog's required ones for the same reason.
    # VIDEO IS NOT FORKED. The env serves the in-process transports the same
    # committed capture the e2e server reads, so the rule this
    # docstring states -- name a format the agent SERVES -- now has one answer for all
    # four. The in-process half named ``video_30s``, which the capture does
    # not carry, so production refused it with AdCPFormatNotFoundError and the entry came
    # back action="failed" with no status: 3 regressed nodeids [a2a, mcp, rest] in box run
    # innet_181026_0202. ``video_file`` is the capture's one required slot for this format.
    # Every creative carries the assets its named format requires (``named_format_assets``):
    # the seller holds a legacy manifest to them, so a missing one fails the entry and the
    # scenario would grade the manifest, not the per-creative status.
    video_id = "video_standard_30s"
    video_assets = named_format_assets(video_id, media_url="https://example.com/video.mp4")
    native_id = "native_standard" if is_e2e(ctx) else "native_content"
    native_assets = named_format_assets(native_id, media_url="https://example.com/native.png")

    # Each format is named at the agent the env says publishes it, the display one included
    # (``_creative_format_id_entry`` asks the same question): on e2e that is the tenant's
    # hosted agent, which publishes only the formats it is asked for.
    creatives = [
        CreativeAssetRequestFactory.payload(
            creative_id="creative-bulk-display-001",
            name="Bulk Display Creative",
            format_id=display,
            assets=named_format_assets(display["id"], media_url="https://example.com/banner.png"),
        ),
        CreativeAssetRequestFactory.payload(
            creative_id="creative-bulk-video-001",
            name="Bulk Video Creative",
            format_id={"id": video_id, "agent_url": env.catalog_agent_url(video_id)},
            assets=video_assets,
        ),
        CreativeAssetRequestFactory.payload(
            creative_id="creative-bulk-native-001",
            name="Bulk Native Creative",
            format_id={"id": native_id, "agent_url": env.catalog_agent_url(native_id)},
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

    Seeds the Product on CreativeSyncEnv's tenant, so the capture runs against the
    DB/session this scenario's env owns.

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
    # What the captured format requires, on every transport: the seller holds a legacy
    # manifest to its named format's required assets.
    assets = named_format_assets(captured["id"], media_url="https://example.com/banner.png")
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


@then('the per-creative result\'s action should be "created" or "updated"')
def then_per_creative_result_created_or_updated(ctx: dict) -> None:
    """Assert the first per-creative result's action is created or updated.

    The pin's provenance_enforcement accept_with_disclosure step grades exactly this set
    (``allowed_values: ["created", "updated"]``): a resubmission of a creative_id the
    seller already recorded is conformantly answered either way.
    """
    first = _first_creative_result(ctx, 'a per-creative result with action="created" or "updated"')
    actual = _action_str(first.action)
    assert actual in ("created", "updated"), (
        f"expected per-creative action 'created' or 'updated', production returned {actual!r} (errors={first.errors!r})"
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


@then("every per-creative result should carry a status exactly when its action is neither failed nor deleted")
def then_status_present_exactly_when_not_failed_or_deleted(ctx: dict) -> None:
    """Assert the pin's two-sided status rule on every per-creative result.

    sync-creatives-response.json: status "MUST be omitted when action is failed or
    deleted". This seller has a review lifecycle, so every other entry carries one. Both
    directions are checked, so a seller stamping a status on a failed entry fails as surely
    as one dropping it from a created entry.
    """
    resp = _require_response(ctx, "a status on exactly the entries that are neither failed nor deleted")
    assert resp.creatives, "Expected at least one per-creative result"
    wrong = [
        (r.creative_id, _action_str(r.action), r.status)
        for r in resp.creatives
        if (r.status is None) != (_action_str(r.action) in ("failed", "deleted"))
    ]
    assert not wrong, f"(creative_id, action, status) entries violating the status rule: {wrong!r}"


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
    from adcp.types.domains.enums.creative_status import CreativeStatus

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


def _listed_back(ctx: dict, creative_id: str) -> dict:
    """The library entry for *creative_id* exactly as ``list_creatives`` puts it on the wire.

    sync-creatives-response.json carries no format identity per creative, so what a sync
    STORED is observable to the buyer only through a following ``list_creatives``; this is
    that read, on the scenario's own transport.

    ``CreativeSyncEnv`` exposes no ``list_creatives`` primitive (its MCP_TOOL /
    REST_ENDPOINT are sync-only), but ``AdCPTestClient.call`` resolves any tool off the
    address table on the SAME env, so no second env and no raw repository read is needed.

    A READ-BACK still reaches a transport, so it owes the same two obligations as any other
    dispatch, and it gets them from ``dispatch_via_client`` like every other one. That entry
    writes the dispatch-result contract into the ctx it is handed, so the read-back hands it
    a ctx of its own: the scenario's ``ctx["result"]`` is the one being graded and must not
    be overwritten by the read.
    """
    from tests.harness.client import AdCPTestClient

    read_back: dict = {"client": ctx.get("client") or AdCPTestClient(ctx["env"]), "transport": ctx["transport"]}
    dispatch_via_client(read_back, "list_creatives", {})
    listed = read_back["result"]
    wire = listed.wire_response
    assert isinstance(wire, dict), (
        f"list_creatives returned no wire body to read the synced creative back from "
        f"(error={listed.error!r}); the readback cannot be graded on this transport"
    )
    matches = [c for c in (wire.get("creatives") or []) if c.get("creative_id") == creative_id]
    assert len(matches) == 1, (
        f"expected creative {creative_id!r} exactly once on the list_creatives wire, found {len(matches)}; "
        f"wire listed {sorted(c.get('creative_id') or '' for c in wire.get('creatives') or [])!r}"
    )
    return matches[0]


@then("the seller's own format_id object should roundtrip through sync_creatives without modification")
def then_format_id_roundtrips_verbatim(ctx: dict) -> None:
    """Assert the creative's format_id roundtrips verbatim — ON THE WIRE first.

    "Roundtrip without modification" is a claim about what the BUYER can read
    back, so the PRIMARY assertion reads the creative back over the transport via
    ``list_creatives`` and compares the wire's ``format_id`` to the captured
    ``{agent_url, id}``. Grading only the DB row (as this step used to) cannot
    detect a seller that persists the format_id correctly and then serializes it
    wrong on the wire — which is exactly the roundtrip being graded. The read is
    :func:`_listed_back`.

    The ``CreativeRepository`` read is KEPT, after the wire assertion, as a
    redundant in-process check: if the two ever disagree, the wire is the
    buyer-facing contract and the DB read localizes the fault.
    """
    from src.core.database.repositories.creative import CreativeRepository
    from src.core.schemas import CreativeFormatIdentity

    first = _first_creative_result(ctx, "a persisted creative whose format_id roundtrips verbatim")
    captured = ctx["captured_format_id"]

    env = ctx["env"]

    # ── PRIMARY: read the creative back over the wire ──────────────────────
    entry = _listed_back(ctx, first.creative_id)
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
    # The row keeps the buyer's reference VERBATIM in ``legacy_format_id``; ``assertion`` is
    # that reference, which is the half this step grades. The canonical comparison key beside
    # it would answer a different question.
    stored = CreativeFormatIdentity.from_row(db_creative).assertion
    assert stored is not None, (
        f"the synced creative retained no legacy format_id at all: expected {captured!r}, stored nothing"
    )
    assert str(stored.id) == captured["id"], (
        f"format_id.id did not roundtrip: expected {captured['id']!r}, stored {str(stored.id)!r}"
    )
    assert str(stored.agent_url) == captured["agent_url"], (
        f"format_id.agent_url did not roundtrip: expected {captured['agent_url']!r}, stored {str(stored.agent_url)!r}"
    )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / THEN steps — canonical format identity survives sync
# (canonical-formats-migration.mdx § Completion checklist: sync and library
# paths preserve format_kind and format_option_ref)
# ═══════════════════════════════════════════════════════════════════════


@given(
    parsers.parse(
        'the buyer syncs a creative identified by canonical kind "{kind}" with a {scope}-scope format_option_ref'
    )
)
def given_sync_canonical_creative_with_option_ref(ctx: dict, kind: str, scope: str) -> None:
    """The creative payload the sync will carry: canonical kind, narrowed to one option.

    ``format_id=OMIT`` is what makes it canonical: core/creative-asset.json is a oneOf on
    the identifier, so the legacy arm has to be ABSENT rather than null. The reference the
    buyer sends is recorded in ``ctx`` so the readback compares against what was SENT,
    never against a value recomputed in the Then.

    The assets fill the kind's required slots: a sync that assigns the creative to nothing
    holds its manifest to the canonical format its kind names (creative-manifests.mdx
    § Validation), so the factory's default ``image``-keyed asset would be refused for the
    missing ``image_main`` before the identity this scenario reads back was stored.
    """
    _sync_canonical_creative(ctx, kind, scope, assets=canonical_assets(kind))


@given(
    parsers.parse(
        'the buyer syncs a creative carrying only a tag_url, identified by canonical kind "{kind}" '
        "with a {scope}-scope format_option_ref"
    )
)
def given_sync_canonical_tag_creative_with_option_ref(ctx: dict, kind: str, scope: str) -> None:
    """The same canonical claim, whose one asset is the slot ``display_tag`` requires.

    formats/canonical/display_tag.json (v3.2.7) declares one required slot, ``tag_url`` of
    asset type ``url``. The default payload's image asset satisfies no slot of that kind,
    and the pinned reference agent refuses a manifest missing a required slot, so the
    scenario that sends this creative to the agent sends the asset the kind asks for.
    """
    _sync_canonical_creative(ctx, kind, scope, assets=build_assets(url_spec("tag_url", url=_TAG_URL)))


#: The tag a display_tag creative serves. HTTPS and credential-free, which is what the
#: reference agent requires of a url-typed slot.
_TAG_URL = "https://ads.example.com/tag.js"


def _sync_canonical_creative(ctx: dict, kind: str, scope: str, **members: Any) -> None:
    """ONE canonical sync payload: ``format_kind`` plus a *scope* option ref, no format_id key.

    The reference is recorded in ``ctx`` so a readback compares against what was SENT.
    """
    reference = format_option_ref_for_scope(scope)
    ctx["creatives"] = [
        CreativeAssetRequestFactory.payload(
            creative_id=_e2e_unique_id("creative_canonical"),
            format_id=OMIT,
            format_kind=kind,
            format_option_ref=reference,
            **members,
        )
    ]
    ctx["sent_format_option_ref"] = reference


@given("the operator's creative agent previews that creative's canonical identity")
def given_operator_agent_previews_canonical_identity(ctx: dict) -> None:
    """The creative the scenario will sync is one the operator's creative agent can preview.

    The kind and the option ref are read off the payload a prior Given built, so the
    agent is configured for exactly the identity that goes on the wire. The env checks
    the precondition both legs need and, in process, answers only a request that routes
    that identity canonically (``operator_agent_previews_canonical_identity``).
    """
    sent = ctx["creatives"][-1]
    ctx["env"].operator_agent_previews_canonical_identity(sent["format_kind"], sent.get("format_option_ref"))


@given(parsers.parse("the resync narrows the same creative to a {scope}-scope format_option_ref"))
def given_resync_narrows_option_ref(ctx: dict, scope: str) -> None:
    """Change only the option the NEXT sync names, on the creative a prior Given synced.

    The kind and every other member stay as stored, so the one difference the resync
    carries is the format_option_ref -- a different option on the same kind, which is a
    real identity change and not a respelling.
    """
    reference = format_option_ref_for_scope(scope)
    assert ctx["creatives"][-1].get("format_option_ref") != reference, (
        "the resync must name a DIFFERENT option than the stored one, or it grades nothing"
    )
    ctx["creatives"][-1] = {**ctx["creatives"][-1], "format_option_ref": reference}
    ctx["sent_format_option_ref"] = reference


@then(
    parsers.parse(
        'listing the library returns the creative on format_kind "{kind}" with the format_option_ref '
        "the buyer last sent, scope included"
    )
)
def then_listed_back_with_canonical_identity(ctx: dict, kind: str) -> None:
    """What the sync STORED, read back over the scenario's own transport.

    sync-creatives-response.json carries no format identity per creative, so the listing is
    the only place the buyer can observe that the sync preserved it. Three values are
    compared: the kind, the absence of the legacy arm (the item oneOf makes the two
    exclusive and this creative was synced on the canonical one), and the whole option
    reference -- an equality, because ``scope`` is the discriminator a subset check over
    the members the seller did emit would miss.
    """
    entry = _listed_back(ctx, latest_creative_id(ctx))
    assert_listed_on_canonical_kind(entry, kind, option_ref=ctx["sent_format_option_ref"])


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / THEN steps — representation_selection lineage on sync
# (creative-manifests.mdx § Libraries and trafficking; representation-sets.mdx
# § Sync, review, and readback; sync_creatives.mdx, all v3.2.7)
# ═══════════════════════════════════════════════════════════════════════

#: The pin's own list of what the seller-bound manifest projection drops, quoted from
#: core/representation-selection.json ``selected_output_digest`` rather than imported from
#: production: an oracle built from the code it grades agrees with it by construction.
_PROJECTION_DROPS = frozenset(
    "$schema, representation_selection, creative_id, revision_id, name, tags, status, weight, "
    "placement_refs, placement_ids, inputs".split(", ")
)

#: The pin's worked example (representation-sets.mdx § Seller-bound resolution, v3.2.7): the
#: paired-redirect manifest and the ``selected_output_digest`` the pin publishes for it. The
#: oracle below must reproduce it before any scenario trusts a digest it computes.
_PIN_EXAMPLE_MANIFEST = {
    "$schema": "https://adcontextprotocol.org/schemas/v3/core/creative-manifest.json",
    "format_kind": "display_tag",
    "format_option_ref": {"scope": "product", "format_option_id": "acme_internal_redirect_300x250"},
    "assets": {
        "serving_tag": {
            "asset_type": "display_tag",
            "delivery_type": "paired_redirect",
            "ad_request_url": "https://ads.acme-example.com/ad?slot=42",
            "clickthrough_url": "https://click.acme-example.com/redirect",
        }
    },
}
_PIN_EXAMPLE_DIGEST = "sha256:02735eb8341dc1c2c25b63d20a0a675577407f7500084a67a6ad71ac1bd0e4c3"


def _output_digest(item: dict[str, Any]) -> str:
    """``selected_output_digest`` of the wire item *item*: SHA-256 over RFC 8785 JCS of its projection."""
    import hashlib

    import rfc8785

    projection = {key: value for key, value in item.items() if key not in _PROJECTION_DROPS}
    return f"sha256:{hashlib.sha256(rfc8785.dumps(projection)).hexdigest()}"


def _source_digest(revision_id: str) -> str:
    """A ``revision_content_digest`` for the source set named *revision_id*.

    The complete set never reaches this seller -- "A downstream seller that receives only
    the selected manifest ... cannot claim it independently inspected hidden source
    representations" -- so any well-formed value distinct per revision stands in for it.
    """
    import hashlib

    return f"sha256:{hashlib.sha256(revision_id.encode()).hexdigest()}"


def _send_selection(ctx: dict, *, revision_id: str, representation_id: str) -> None:
    """Put the lineage on the pending item: its ``revision_id`` and a selection describing it."""
    item = ctx["creatives"][-1]
    item["revision_id"] = revision_id
    item["representation_selection"] = {
        "creative_id": item["creative_id"],
        "revision_id": revision_id,
        "revision_content_digest": _source_digest(revision_id),
        "selected_representation_id": representation_id,
        "strategy": "representation_order",
        "selected_output_digest": _output_digest(item),
        "resolved_by": "buyer",
    }
    ctx["sent_representation_selection"] = item["representation_selection"]


@given(
    parsers.parse('the buyer syncs a creative identified by canonical kind "{kind}" selected from a representation set')
)
def given_sync_selected_creative(ctx: dict, kind: str) -> None:
    """A canonical creative the buyer resolved from a representation set, carrying its lineage.

    The output is an ordinary manifest (the kind's required slots, a product-scope option
    ref); what makes it a selected output is ``representation_selection`` naming this
    ``creative_id`` and ``revision_id`` and the digest of exactly these bytes. The digest
    comes from :func:`_output_digest`, which is first held to the pin's published example.
    """
    assert _output_digest(_PIN_EXAMPLE_MANIFEST) == _PIN_EXAMPLE_DIGEST, (
        "the test's digest oracle does not reproduce the pin's worked example, so no digest it computes can grade"
    )
    _sync_canonical_creative(ctx, kind, "product", assets=canonical_assets(kind))
    _send_selection(ctx, revision_id="rev_launch_1", representation_id="source-hosted")


@given(parsers.parse("the creative's representation_selection disagrees with the item on {member}"))
def given_selection_disagrees(ctx: dict, member: str) -> None:
    """Make ONE lineage member describe something other than the item that carries it.

    ``creative_id`` and ``revision_id`` must equal the item's (sync_creatives.mdx); the
    ``selected_output_digest`` must be the digest of the item's own projection, so the
    disagreeing one is the digest of different output bytes -- the same manifest without
    its ``format_option_ref``.
    """
    item = ctx["creatives"][-1]
    selection = item["representation_selection"]
    if member == "creative_id":
        selection["creative_id"] = f"{item['creative_id']}_other"
    elif member == "revision_id":
        selection["revision_id"] = "rev_other"
    elif member == "selected_output_digest":
        other_bytes = {key: value for key, value in item.items() if key != "format_option_ref"}
        selection["selected_output_digest"] = _output_digest(other_bytes)
    else:
        raise AssertionError(f"no disagreement is defined for lineage member {member!r}")


@given("the resync selects another representation of the same source revision")
def given_resync_selects_another_representation(ctx: dict) -> None:
    """A different selected execution, with the source revision and the output bytes unchanged.

    Only ``selected_representation_id`` moves, so the one thing that differs from the stored
    creative is the execution review fingerprint (representation-sets.mdx § Sync, review,
    and readback).
    """
    stored = ctx["sent_representation_selection"]
    _send_selection(ctx, revision_id=str(stored["revision_id"]), representation_id="source-alternate")
    assert ctx["sent_representation_selection"]["selected_output_digest"] == stored["selected_output_digest"]


@given("the resync carries a new source revision that selects the same output")
def given_resync_new_revision_same_output(ctx: dict) -> None:
    """A new buyer revision of the source set that pins the same execution.

    The ``revision_id`` and its digest move; the selected representation and the output
    bytes do not, so the execution fingerprint is the stored one.
    """
    stored = ctx["sent_representation_selection"]
    _send_selection(ctx, revision_id="rev_launch_2", representation_id=str(stored["selected_representation_id"]))
    assert ctx["sent_representation_selection"]["selected_output_digest"] == stored["selected_output_digest"]


@then("listing the library returns the creative carrying the representation_selection the buyer last sent")
def then_listed_with_selection(ctx: dict) -> None:
    """The lineage read back over the scenario's own transport, as a whole document.

    list-creatives-response.json ``representation_lineage_readback``: "must_equal":
    "last_accepted_current_representation_selection". Equality, not a subset, because every
    member is lineage: a seller that kept the first revision, or dropped the strategy,
    lists a different selection.
    """
    entry = _listed_back(ctx, latest_creative_id(ctx))
    listed = entry.get("representation_selection")
    assert listed == ctx["sent_representation_selection"], (
        f"the listed representation_selection is not the one the buyer last sent: "
        f"{listed!r} != {ctx['sent_representation_selection']!r}"
    )


@then(parsers.parse('the creative is reported failed with {code:w} at "{field}" and nothing is stored'))
def then_selected_creative_refused(ctx: dict, code: str, field: str) -> None:
    """The batch entry refused with *code* naming *field*, and the creative not stored."""
    assert_sync_entry_refused_and_not_stored(ctx, latest_creative_id(ctx), code, field=field)


@then("the stored creative carries the preview the operator's creative agent returned")
def then_stored_with_operator_agent_preview(ctx: dict) -> None:
    """The seller asked the operator's agent for a preview and kept what it answered.

    Read off the stored row because the preview is not on the wire:
    sync-creatives-response.json carries ``preview_url`` only for generative formats, and
    list-creatives-response.json has no preview member. What is graded is that a preview
    with at least one render was requested AND stored -- not what the render looks like,
    which is the agent's business. A seller that never previews a creative identified by
    ``format_kind`` (the gate this scenario exists for) stores no preview at all; one that
    sends the request without the canonical identity is answered with no previews.
    """
    data = stored_creative_column(ctx, latest_creative_id(ctx), "data") or {}
    previews = (data.get("preview_response") or {}).get("previews") or []
    assert previews and previews[0].get("renders"), (
        "the seller stored no preview from the operator's creative agent for a creative identified by "
        f"format_kind; stored preview_response={data.get('preview_response')!r}"
    )
