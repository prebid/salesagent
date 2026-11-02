"""FormatId spec compliance tests.

The AdCP spec defines FormatId as an object {agent_url, id, width?, height?, duration_ms?},
NOT a bare string. These tests verify that our code correctly handles FormatId objects
throughout the pipeline. Each xfail marks a known spec violation to fix.

Audit: 2026-04-30 — found via full codebase audit of format_id usage patterns.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from adcp.types import FormatId as LibraryFormatId

from src.core.schemas._base import FormatId

# ---------------------------------------------------------------------------
# Bug 1 (HIGH): format_resolver.py:56 — FormatId object compared to string
# ---------------------------------------------------------------------------


# Graduated 2026-08-25: the xfail routing is gone because production now implements the
# behaviour, not because the test stopped being able to fail. Both halves were verified
# before removal, per .claude/rules/workflows/xpass-graduation.md:
#   - The test was VACUOUS. It patched `src.core.format_resolver.get_creative_agent_registry`,
#     an attribute that module does not have (the import is function-local), so `patch()`
#     raised AttributeError before any production code ran, and the strict xfail had been
#     green on its own broken setup since the 2026-04-30 audit. It also returned the
#     coroutine from `run_async_in_sync_context` instead of the awaited list. Both were
#     corrected FIRST, and the test then failed for its stated reason: NOT_FOUND raised for
#     a format the listing contains.
#   - Production then changed: get_format's no-agent_url branch resolves through
#     find_format_by_id, which compares the id component instead of a str against a model.
# Traced end to end: get_format -> find_format_by_id -> format_identity -> format_id_identity.
def test_format_resolver_finds_format_without_agent_url():
    """get_format() without agent_url should search all agents and find a match.

    Two patch targets, both load-bearing, and both wrong in the version of this test
    that shipped with the 2026-04-30 audit:

    - ``get_creative_agent_registry`` is imported INSIDE ``get_format``, so it is never
      an attribute of ``src.core.format_resolver``; patching it there raised
      AttributeError before a line of production code ran. The test was passing as an
      xfail on the strength of its own broken setup, proving nothing about the bug it
      claimed to document. Patch the source module instead.
    - ``run_async_in_sync_context`` must return the AWAITED value. Returning the
      coroutine itself (``lambda coro: coro``) makes the production loop iterate a
      coroutine and blow up with TypeError -- again, a red mark for the wrong reason.

    With both fixed, the failure that remains is the real one: the listing contains the
    requested format and ``get_format`` raises NOT_FOUND anyway.
    """
    from src.core.exceptions import AdCPFormatNotFoundError
    from src.core.format_resolver import get_format

    mock_format = MagicMock()
    mock_format.format_id = FormatId(
        agent_url="https://creative.test.example.com/api/creative-agent",
        id="display_300x250_image",
    )

    mock_registry = MagicMock()

    with (
        patch("src.core.creative_agent_registry.get_creative_agent_registry", return_value=mock_registry),
        patch("src.core.format_resolver.run_async_in_sync_context", side_effect=lambda coro: [mock_format]),
    ):
        try:
            result = get_format(format_id="display_300x250_image")
        except AdCPFormatNotFoundError as exc:  # the bug, stated as the failure it is
            raise AssertionError(
                f"get_format raised NOT_FOUND for a format the registry listing contains: {exc}"
            ) from exc

    assert result is mock_format


# ---------------------------------------------------------------------------
# Bug 2 (MEDIUM): creative_agent_registry.py:810 — build_creative sends
#   format_id as bare string to MCP, unlike preview_creative which sends object
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="build_creative() sends format_id as a bare string to MCP tool call. "
    "preview_creative() correctly sends {id, agent_url} object. Inconsistent.",
    strict=True,
)
@pytest.mark.asyncio
async def test_build_creative_sends_format_id_as_object():
    """build_creative() should send format_id as a FormatId object, not a bare string.

    Bug: creative_agent_registry.py:810 passes `"format_id": format_id` (string)
    while preview_creative:754 correctly passes `"format_id": {"id": ..., "agent_url": ...}`.
    """
    from src.core.creative_agent_registry import CreativeAgentRegistry

    registry = CreativeAgentRegistry.__new__(CreativeAgentRegistry)

    mock_result = MagicMock()
    mock_result.structured_content = {"message": "ok", "context_id": "ctx1", "status": "draft"}
    mock_call_mcp_tool = AsyncMock(return_value=mock_result)

    # Patched one frame below the registry: ``build_creative`` reaches the dial
    # through ``call_operator_mcp_tool``, so this keeps the real argument
    # forwarding (and ``extract_tool_payload``) in the path being graded.
    with patch("src.core.utils.operator_mcp.call_mcp_tool", mock_call_mcp_tool):
        await registry.build_creative(
            agent_url="https://creative.test.example.com/api/creative-agent",
            format_id="display_300x250_generative",
            message="Create a banner ad",
            gemini_api_key="test-key",
        )

    # Verify format_id was sent as an object, not a string
    call_args = mock_call_mcp_tool.call_args
    params = call_args.kwargs["arguments"]
    format_id_sent = params["format_id"]

    assert isinstance(format_id_sent, dict), (
        f"format_id should be a dict {{id, agent_url}}, got {type(format_id_sent).__name__}: {format_id_sent}"
    )
    assert format_id_sent["id"] == "display_300x250_generative"
    assert "agent_url" in format_id_sent


# ---------------------------------------------------------------------------
# Bug 3 (LOW): products.py:559 — triple-type-check for format_id extraction
#   suggests FormatId objects not consistently used in product.format_ids
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="Product.format_ids items can arrive as str, dict, or FormatId. "
    "After Pydantic validation, they should always be FormatId objects.",
    strict=True,
)
def test_product_format_ids_are_always_format_id_objects():
    """Product.format_ids should be validated to FormatId objects by Pydantic.

    Bug: products.py:559-566 has a triple isinstance check (str/dict/FormatId)
    to extract the format_id string. If Pydantic validation worked correctly,
    format_ids would always be FormatId objects and only one branch would be needed.
    """
    from src.core.schemas.product import Product

    product = Product(
        product_id="prod_001",
        name="Test Product",
        format_ids=[
            {"agent_url": "https://creative.test.example.com", "id": "display_300x250"},
        ],
    )

    for fid in product.format_ids:
        assert isinstance(fid, (FormatId, LibraryFormatId)), (
            f"format_id should be a FormatId object after validation, got {type(fid).__name__}: {fid}"
        )


# ---------------------------------------------------------------------------
# Bug 4 (LOW): media_buy_update.py:674 — dict access pattern for format_ids
#   with .get("id") or .get("format_id") fallback
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="media_buy_update format compatibility check treats format_ids as dicts "
    "instead of FormatId objects. After ORM deserialization, they should be typed.",
    strict=True,
)
def test_media_buy_update_format_ids_are_typed():
    """Product.format_ids from DB should deserialize as FormatId objects.

    Bug: media_buy_update.py:674 does `fmt.get("id") or fmt.get("format_id")`
    which treats format_ids as raw dicts. After ORM/Pydantic deserialization,
    they should be FormatId objects accessible via `.id` attribute.
    """
    from src.core.database.models import Product as DBProduct

    # Simulate what comes back from DB — JSONType stores as dict
    product = DBProduct(
        product_id="prod_001",
        name="Test Product",
        tenant_id="t1",
        format_ids=[
            {"agent_url": "https://creative.test.example.com", "id": "display_300x250"},
        ],
    )

    # After deserialization, format_ids should be typed objects, not raw dicts
    for fid in product.format_ids:
        assert hasattr(fid, "id") and hasattr(fid, "agent_url"), (
            f"format_id should have .id and .agent_url attributes, got {type(fid).__name__}"
        )
        assert not isinstance(fid, dict), f"format_id should not be a raw dict after deserialization, got dict: {fid}"


# ---------------------------------------------------------------------------
# Bug 5 (LOW): dynamic_pricing_service.py:94 — triple-type-check for format_id
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="DynamicPricingService has triple isinstance check for format_id types. "
    "With consistent FormatId objects, only one code path would be needed.",
    strict=True,
)
def test_pricing_service_receives_format_id_objects():
    """DynamicPricingService should receive FormatId objects, not mixed types.

    Bug: dynamic_pricing_service.py:94-101 has a triple isinstance check
    (dict/FormatId/str) to handle inconsistent format_id types. This defensive
    code shouldn't be necessary if the pipeline consistently uses FormatId.
    """
    from src.services.dynamic_pricing_service import DynamicPricingService

    service = DynamicPricingService()

    mock_product = MagicMock()
    mock_product.format_ids = [
        FormatId(agent_url="https://creative.test.example.com", id="display_300x250_image"),
    ]
    mock_product.pricing_model = "CPM"
    mock_product.base_rate = 5.0

    # If format_ids are FormatId objects, the service should handle them
    # without needing isinstance checks for str or dict
    result = service._calculate_product_pricing(mock_product)
    assert "base_rate" in result or result is not None


# ---------------------------------------------------------------------------
# Bug 6 (LOW): CreateCreativeRequest.format_id is typed as str, not FormatId
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="CreateCreativeRequest.format_id is typed as `str` instead of FormatId. "
    "AdCP spec defines format_id as an object with agent_url and id.",
    strict=True,
)
def test_create_creative_request_accepts_format_id_object():
    """CreateCreativeRequest should accept FormatId objects, not just strings.

    Bug: creative.py:618 defines `format_id: str` instead of `format_id: FormatId`.
    The AdCP spec expects format_id to be an object with agent_url and id.
    """
    from src.core.schemas.creative import CreateCreativeRequest

    req = CreateCreativeRequest(
        format_id={"agent_url": "https://creative.test.example.com", "id": "display_300x250"},
        content_uri="https://example.com/banner.png",
        name="Test Creative",
    )

    # format_id should be a FormatId object (or at least a dict), not rejected
    assert hasattr(req.format_id, "id") or isinstance(req.format_id, dict), (
        f"format_id should accept FormatId objects, got {type(req.format_id).__name__}"
    )
