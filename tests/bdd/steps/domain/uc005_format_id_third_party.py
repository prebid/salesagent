"""UC-005 storyboard: third-party format_id (foreign agent_url) is an observation, not a failure.

Scenario ``T-UC-005-storyboard-format-id-third-party-agent-out-of-scope``
(@third-party-agent): when a product advertises a ``format_id`` whose ``agent_url``
points at a creative agent OTHER than this seller, the seller cannot verify it
locally. Per the ``list_formats`` storyboard step such a reference is OUT OF
SCOPE — ``scope.equals=$agent_url`` with ``on_out_of_scope: warn`` — so the seller
MUST NOT fabricate a local format entry to cover it, and an empty result is an
observation, never a graded failure.

Wired to real production across a2a/mcp/rest (auto-parametrized; UC-005 →
CreativeFormatsEnv). Falsifiability comes from the COLLISION setup: the seller's own
catalog holds a format whose ``id`` matches the third-party reference but under the
SELLER's ``agent_url``. A filter comparing ``id`` alone would return that local
format as if it satisfied the third-party reference; the v3.1 ``(agent_url, id)``
federation filter (``format_id_identity``) returns nothing, which is the correct
observation. Both the production filter fix and the REST harness fix
(``build_rest_body`` now transmits ``format_ids``) are required for this to hold on
all three transports.

@source repo=adcp ref=v3.1.0-beta.3
  path=static/compliance/source/protocols/media-buy/index.yaml
  (step list_formats, refs_resolve: match_keys [agent_url, id], scope.equals $agent_url, on_out_of_scope: warn)
"""

from __future__ import annotations

from pytest_bdd import given, then, when

from src.core.schemas import FormatId, ListCreativeFormatsRequest, format_id_identity
from tests.bdd.steps._outcome_helpers import _require_response
from tests.bdd.steps.generic.when_request import _call
from tests.factories import FormatFactory

# The seller's own creative agent — matches the agent_url the CreativeFormatsEnv
# mock catalog uses, so a seeded format reads as "hosted by this seller".
SELLER_AGENT_URL = "https://creative.adcontextprotocol.org"
# A DIFFERENT creative agent the seller does not proxy — the out-of-scope reference.
THIRD_PARTY_AGENT_URL = "https://third-party-creative.example.com"
# Shared id: the third-party reference and the seller's local catalog entry collide
# on id so the test can prove discrimination is on agent_url, not id.
COLLIDING_FORMAT_ID = "display_300x250_image"


@given("a product advertises a format_id whose agent_url points at a third-party creative agent")
def given_product_advertises_third_party_format_id(ctx: dict) -> None:
    """Capture the format_id a product carries, hosted by a third-party agent."""
    ctx["third_party_format_id"] = FormatId(agent_url=THIRD_PARTY_AGENT_URL, id=COLLIDING_FORMAT_ID)


@given("the seller has no local copy of that format in its own catalog")
def given_seller_has_no_local_copy(ctx: dict) -> None:
    """Seed the seller catalog with ONLY a same-id format under the seller's own agent_url.

    The seller has no copy of the *third-party* format. This same-id/own-agent_url
    collision is the falsifier: an id-only filter would wrongly surface this local
    entry for the third-party reference; the (agent_url, id) filter must not.
    """
    fid: FormatId = ctx["third_party_format_id"]
    local = FormatFactory(format_id=FormatId(agent_url=SELLER_AGENT_URL, id=fid.id))
    ctx["env"].set_registry_formats([local])


@when("the Buyer Agent sends list_creative_formats with that third-party format_id")
def when_send_list_with_third_party_format_id(ctx: dict) -> None:
    """Dispatch list_creative_formats filtered by the third-party format_id (all transports)."""
    req = ListCreativeFormatsRequest(format_ids=[ctx["third_party_format_id"]])
    _call(ctx, req=req)


@then("the seller should NOT fabricate a local format entry to satisfy the third-party reference")
def then_no_fabricated_local_entry(ctx: dict) -> None:
    """No returned format is the third-party reference, nor a substituted local same-id format."""
    response = _require_response(ctx)
    returned = {format_id_identity(f.format_id) for f in response.formats}

    third_party = format_id_identity(ctx["third_party_format_id"])
    assert third_party not in returned, (
        f"seller fabricated a third-party-attributed entry {third_party} it does not host: {returned}"
    )

    # Falsifiable core: the seller's own same-id format must NOT be substituted for
    # the foreign reference. id-only matching would surface it here.
    seller_local = (SELLER_AGENT_URL, ctx["third_party_format_id"].id)
    assert seller_local not in returned, (
        f"seller substituted its own format {seller_local} for the third-party reference "
        f"{third_party}; the federation filter must match on (agent_url, id), not id alone"
    )


@then("the verification result should be reported as an observation rather than a graded failure")
def then_reported_as_observation(ctx: dict) -> None:
    """An unresolvable foreign reference is a successful (empty) result, not an error envelope."""
    assert ctx.get("error") is None, (
        f"out-of-scope third-party reference raised an error instead of an observation: {ctx.get('error')!r}"
    )
    response = _require_response(ctx)
    # The foreign reference resolves to nothing locally — that empty match is the
    # observation (on_out_of_scope: warn), distinct from a graded failure/error.
    third_party = format_id_identity(ctx["third_party_format_id"])
    returned = {format_id_identity(f.format_id) for f in response.formats}
    assert third_party not in returned
