"""UC-005 roundtrip: format_id advertised on products resolves through list_creative_formats.

Scenario T-UC-005-storyboard-format-id-roundtrip-from-products (@format-id-roundtrip):
The Buyer Agent captures a format_id from get_products and sends it to list_creative_formats.
The sales agent MUST return the format it advertised — an empty formats[] is a compliance failure.

Source: media-buy/index.yaml list_formats_integrity phase (AdCP v3.1-04f59d2d5).
"""

from __future__ import annotations

import asyncio

from pytest_bdd import given, then, when

from tests.bdd.steps.domain.uc005_format_id_shape import _serialized_formats
from tests.helpers.format_assertions import assert_wire_format_id_is_object

# Shared by ProductFactory defaults and mock creative registry (_get_mock_formats).
_AGENT_URL = "https://creative.adcontextprotocol.org"
# Must match a format_id.id in _get_mock_formats() so the filter returns a result.
# Production filters list_creative_formats on the (agent_url, id) pair via
# format_id_identity (creative_formats.py:279-280), so the returned formats[]
# contains only the single matching entry regardless of catalog order --
# formats[0] is always the captured pair.
_FORMAT_ID = "display_300x250_image"


@given("the Buyer Agent captured a format_id object {agent_url, id} from a prior get_products response")
def given_captured_format_id_from_get_products(ctx: dict) -> None:
    """Call get_products in-process to capture the advertised format_id.

    Runs inside the outer CreativeFormatsEnv: uses its session and identity.
    Creates a minimal Product whose format_id.id matches a format in the mock
    registry so the subsequent list_creative_formats call resolves it.
    No additional patches are needed — the tenant has no gemini_api_key (policy
    disabled), no dynamic templates (variants return []), and no product_ranking_prompt
    (AI ranking skipped).
    """
    from src.core.schemas import GetProductsRequest
    from src.core.tools.products import _get_products_impl
    from tests.factories import PricingOptionFactory, ProductFactory, TenantFactory

    env = ctx["env"]

    # TRANSPORT-BYPASS: this Given step seeds the scenario by calling get_products
    # in-process to capture a real format_id. It is not a When dispatch — there is
    # no transport parametrization for the capture step; transport only varies the
    # subsequent list_creative_formats call in the When step.

    # Create Tenant + Product in DB bound to the outer CreativeFormatsEnv session.
    tenant = TenantFactory(tenant_id=env._tenant_id, ad_server="mock")
    product = ProductFactory(
        tenant=tenant,
        format_ids=[{"agent_url": _AGENT_URL, "id": _FORMAT_ID}],
    )
    PricingOptionFactory(product=product)
    env._commit_factory_data()

    req = GetProductsRequest(brief="roundtrip test")
    response = asyncio.run(_get_products_impl(req, env.identity))

    assert response.products, "get_products returned no products — cannot capture format_id"
    product = response.products[0]
    assert product.format_ids, "product has no format_ids — cannot capture format_id"

    fid = product.format_ids[0]
    ctx["captured_format_id"] = {
        "agent_url": str(fid.agent_url),
        "id": str(fid.id),
    }


@when("the Buyer Agent sends list_creative_formats with format_ids [{captured agent_url, captured id}]")
def when_send_list_creative_formats_with_captured_format_id(ctx: dict) -> None:
    """Send list_creative_formats filtered to the captured format_id."""
    from src.core.schemas import FormatId, ListCreativeFormatsRequest
    from tests.bdd.steps.generic.when_request import _call_via

    captured = ctx["captured_format_id"]
    fid = FormatId(agent_url=captured["agent_url"], id=captured["id"])
    req = ListCreativeFormatsRequest(format_ids=[fid])
    _call_via(ctx, ctx["transport"], req=req)


def _assert_formats_non_empty(ctx: dict, failure_message: str) -> list[dict]:
    """Shared non-empty check for the two storyboard steps that assert the same predicate."""
    formats = _serialized_formats(ctx)
    assert formats, failure_message
    return formats


@then("the response should be schema-valid against list-creative-formats-response.json")
def then_response_schema_valid(ctx: dict) -> None:
    """Assert the serialized wire response carries a formats list.

    Asserts the actual wire payload (``ctx["wire_response"]`` on a2a/mcp/rest, or
    the production-serialized payload on IMPL) via ``_serialized_formats`` — not
    the typed ``ListCreativeFormatsResponse``, whose fields are already coerced
    to their declared types and so cannot observe a serialization regression.
    """
    formats = _serialized_formats(ctx)
    assert isinstance(formats, list), f"Expected formats to be a list, got {type(formats).__name__!r}"


@then("the formats array should contain at least one entry")
def then_formats_array_non_empty(ctx: dict) -> None:
    """Assert formats[] is non-empty — an empty array is a compliance failure."""
    _assert_formats_non_empty(
        ctx,
        "formats[] is empty. The sales agent advertised this format_id on its products "
        "but cannot resolve it through list_creative_formats. "
        "(AdCP list_formats_integrity: format_ids on products MUST resolve through list_creative_formats)",
    )


@then("formats[0].format_id should roundtrip verbatim with the captured {agent_url, id}")
def then_format_id_roundtrip_verbatim(ctx: dict) -> None:
    """Assert the captured format_id roundtrips verbatim as formats[0].format_id.

    Production filters list_creative_formats on the (agent_url, id) pair via
    format_id_identity (creative_formats.py:279-280), so the mock registry's
    single seeded match for the captured pair is exactly formats[0] on every
    transport. Asserts the wire object-shape (agent_url + id present, never a
    bare string) plus id and agent_url verbatim. The pair-filter matches on the
    *canonical* agent_url, so a canonically-equal but raw-different agent_url
    (trailing slash / fragment / userinfo) resolves through the filter yet must
    still be checked verbatim on the wire.
    """
    captured = ctx["captured_format_id"]
    formats = _assert_formats_non_empty(ctx, "formats[] is empty — cannot verify roundtrip")

    format_id = formats[0]["format_id"]
    assert_wire_format_id_is_object(format_id)
    assert format_id["id"] == captured["id"], (
        f"formats[0].format_id.id mismatch: expected {captured['id']!r}, got {format_id['id']!r}"
    )
    assert format_id["agent_url"] == captured["agent_url"], (
        f"agent_url mismatch: {format_id['agent_url']!r} != {captured['agent_url']!r}"
    )


@then("an empty formats[] would indicate a stale catalog reference and is a compliance failure")
def then_empty_formats_is_compliance_failure(ctx: dict) -> None:
    """Document the compliance implication of an empty formats[] by asserting it is non-empty.

    An empty formats[] means the format_id advertised on a product cannot be
    resolved at buy time, which would cause sync_creatives to fail silently after
    the media buy is committed. The assertion message names this as a compliance
    failure per AdCP list_formats_integrity.
    """
    _assert_formats_non_empty(
        ctx,
        "COMPLIANCE FAILURE: formats[] is empty. The format_id was advertised on a "
        "product but cannot be resolved through list_creative_formats. A buyer who "
        "committed a media buy against this product would fail silently at "
        "sync_creatives. (AdCP list_formats_integrity phase)",
    )
