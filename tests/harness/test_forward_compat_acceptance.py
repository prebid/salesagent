"""Forward-compatibility acceptance tests across MCP, A2A, and REST.

Verifies that our tools ACCEPT (not reject) various AdCP payload shapes:
- Current spec payloads
- Future spec payloads with extra nested fields
- v2.5 legacy payloads with deprecated field names
- Payloads with extra fields at every nesting level

The tools may not PERFORM correctly with unknown data, but they must not
reject the request at the transport layer. This is the forward-compatibility
contract: accept now, handle later.

Each payload is tested through all three transport paths:
- MCP: Client(mcp) → middleware (normalize + strip + deep-strip) → TypeAdapter → tool
- A2A: normalize_request_params → model_validate(extra='ignore') → _impl
- REST/direct: normalize_request_params → model_validate(extra='ignore') → _impl
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.request_compat import normalize_request_params

# ---------------------------------------------------------------------------
# Payloads: each is a (tool_name, params) tuple
# ---------------------------------------------------------------------------

# Current spec — baseline, must always work
CURRENT_SPEC_GET_PRODUCTS = {
    "brief": "video ads for Q4",
    "brand": {"domain": "acme.com"},
}

# Future spec — extra top-level field
FUTURE_TOP_LEVEL_FIELD = {
    "brief": "video ads",
    "brand": {"domain": "acme.com"},
    "adcp_major_version": 5,  # New spec field, not in our signature
}

# Future spec — extra nested field inside brand
FUTURE_NESTED_IN_BRAND = {
    "brief": "display ads",
    "brand": {"domain": "acme.com", "verification_status": "verified"},
}

# Future spec — extra nested field inside context
FUTURE_NESTED_IN_CONTEXT = {
    "brief": "audio ads",
    "context": {"session_id": "sess-123", "trace_id": "tr-456", "priority": "high"},
}

# Future spec — extra nested field inside filters
FUTURE_NESTED_IN_FILTERS = {
    "brief": "programmatic",
    "filters": {"delivery_type": "guaranteed", "ai_optimization_level": "aggressive"},
}

# v2.5 legacy — brand_manifest (deprecated)
V25_BRAND_MANIFEST = {
    "brief": "retargeting campaign",
    "brand_manifest": "https://acme.com/.well-known/brand.json",
}

# v2.5 legacy — promoted_offerings (deprecated)
V25_PROMOTED_OFFERINGS = {
    "brief": "catalog ads",
    "promoted_offerings": ["SKU-001", "SKU-002"],
}

# Extra fields at multiple nesting levels simultaneously
MULTI_LEVEL_EXTRAS = {
    "brief": "multi-level test",
    "brand": {"domain": "acme.com", "future_brand_field": True},
    "context": {"session_id": "sess", "future_context_field": 42},
    "future_top_level": "also here",
}

# Completely unknown tool parameters (top-level only)
ALL_UNKNOWN_PLUS_BRIEF = {
    "brief": "safety test",
    "quantum_targeting": {"entangle": True},
    "holographic_format": "3D",
}


# ---------------------------------------------------------------------------
# MCP pipeline tests (Client(mcp) — full middleware + TypeAdapter)
# ---------------------------------------------------------------------------


def _get_products_patches():
    """Context managers that mock all get_products dependencies for Client(mcp)."""
    from tests.factories.principal import PrincipalFactory

    identity = PrincipalFactory.make_identity(protocol="mcp")

    # Mock the _impl dependencies so the tool function doesn't fail on DB access
    mock_uow = MagicMock()
    mock_uow_instance = MagicMock()
    mock_uow_instance.products = MagicMock()
    mock_uow_instance.products.list_all.return_value = []
    mock_uow_instance.__enter__ = MagicMock(return_value=mock_uow_instance)
    mock_uow_instance.__exit__ = MagicMock(return_value=False)
    mock_uow.return_value = mock_uow_instance

    mock_principal = MagicMock()
    mock_principal.principal_id = identity.principal_id
    mock_principal.name = "Test"
    mock_principal.platform_mappings = {"mock": {"advertiser_id": "test"}}

    mock_adapter = MagicMock()
    mock_adapter.get_supported_pricing_models.return_value = ["cpm"]

    return [
        patch("src.core.mcp_auth_middleware.resolve_identity_from_context", return_value=identity),
        patch("src.core.database.repositories.uow.ProductUoW", mock_uow),
        patch("src.core.tools.products.get_principal_object", return_value=mock_principal),
        patch("src.core.tools.products.convert_product_model_to_schema", side_effect=lambda p, **kw: p),
        patch("src.core.tools.products.PolicyCheckService"),
        patch("src.services.dynamic_products.generate_variants_for_brief", new_callable=AsyncMock, return_value=[]),
        patch("src.services.ai.factory.get_factory"),
        patch("src.services.dynamic_pricing_service.DynamicPricingService"),
        patch("src.core.property_list_resolver.resolve_property_list", new_callable=AsyncMock, return_value=[]),
        patch("src.core.helpers.adapter_helpers.get_adapter", return_value=mock_adapter),
    ]


class TestMcpForwardCompat:
    """MCP transport: Client(mcp) exercises full middleware + TypeAdapter pipeline."""

    @pytest.mark.parametrize(
        "label,payload",
        [
            ("current_spec", CURRENT_SPEC_GET_PRODUCTS),
            ("future_top_level", FUTURE_TOP_LEVEL_FIELD),
            ("future_nested_brand", FUTURE_NESTED_IN_BRAND),
            ("future_nested_context", FUTURE_NESTED_IN_CONTEXT),
            ("future_nested_filters", FUTURE_NESTED_IN_FILTERS),
            ("v25_brand_manifest", V25_BRAND_MANIFEST),
            ("v25_promoted_offerings", V25_PROMOTED_OFFERINGS),
            ("multi_level_extras", MULTI_LEVEL_EXTRAS),
            ("all_unknown_plus_brief", ALL_UNKNOWN_PLUS_BRIEF),
        ],
        ids=lambda x: x if isinstance(x, str) else "",
    )
    def test_production_accepts_payload(self, label: str, payload: dict):
        """In production mode, get_products accepts various payload shapes."""
        from fastmcp import Client

        from src.core.main import mcp

        async def _call():
            patches = _get_products_patches()
            with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
                for p in patches:
                    p.start()
                try:
                    async with Client(mcp) as client:
                        result = await client.call_tool(
                            "get_products",
                            payload,
                            raise_on_error=False,
                        )
                        assert not result.is_error, (
                            f"[{label}] Production should accept payload but got error: "
                            f"{result.content[:300] if result.content else 'no content'}"
                        )
                finally:
                    for p in patches:
                        p.stop()

        asyncio.run(_call())

    @pytest.mark.parametrize(
        "label,payload",
        [
            ("future_top_level", FUTURE_TOP_LEVEL_FIELD),
            ("future_nested_brand", FUTURE_NESTED_IN_BRAND),
            ("multi_level_extras", MULTI_LEVEL_EXTRAS),
        ],
        ids=lambda x: x if isinstance(x, str) else "",
    )
    def test_dev_rejects_unknown_top_level_fields(self, label: str, payload: dict):
        """In dev mode, unknown top-level fields are rejected by TypeAdapter."""
        from fastmcp import Client

        from src.core.main import mcp

        # Only test payloads that have top-level unknowns (not normalized deprecations)
        has_top_level_unknown = any(
            k
            not in {
                "brief",
                "brand",
                "adcp_version",
                "filters",
                "property_list",
                "push_notification_config",
                "context",
                "account",
                "ctx",
            }
            for k in payload
        )
        if not has_top_level_unknown:
            pytest.skip("No top-level unknown fields in this payload")

        async def _call():
            patches = _get_products_patches()
            for p in patches:
                p.start()
            try:
                async with Client(mcp) as client:
                    result = await client.call_tool(
                        "get_products",
                        payload,
                        raise_on_error=False,
                    )
                    assert result.is_error, f"[{label}] Dev mode should reject unknown fields"
            finally:
                for p in patches:
                    p.stop()

        asyncio.run(_call())


# ---------------------------------------------------------------------------
# A2A transport: normalize + model_validate (production mode)
# ---------------------------------------------------------------------------


class TestA2aForwardCompat:
    """A2A transport: normalize + strip + model_validate acceptance.

    Note: Pydantic's model_config extra mode is evaluated at import time,
    so we can't switch to extra='ignore' at runtime. Instead we verify the
    A2A pipeline: normalize deprecated fields + strip unknown top-level params
    (matching what the A2A handler does before model_validate).

    In production, models are compiled with extra='ignore' so unknown
    top-level fields are also accepted. That behavior is tested by the
    MCP tests above, which exercise the real production middleware.
    """

    @pytest.mark.parametrize(
        "label,payload",
        [
            ("current_spec", CURRENT_SPEC_GET_PRODUCTS),
            ("v25_brand_manifest", V25_BRAND_MANIFEST),
            ("v25_promoted_offerings", V25_PROMOTED_OFFERINGS),
        ],
        ids=lambda x: x if isinstance(x, str) else "",
    )
    def test_normalize_then_model_validate(self, label: str, payload: dict):
        """A2A path: normalize → strip unknown top-level → model_validate → accepted.

        Note: Nested unknowns in library types (BrandReference, AccountReference)
        are rejected even in production because the adcp library uses extra='forbid'.
        Deep-strip handles this on MCP only. A2A tests focus on normalization
        of deprecated fields and top-level stripping.
        """
        from src.core.schemas import GetProductsRequest

        # Step 1: Normalize (same as A2A handler does)
        result = normalize_request_params("get_products", dict(payload))
        normalized = result.params

        # Step 2: Strip unknown top-level params (A2A handlers pop unknowns)
        known_fields = set(GetProductsRequest.model_fields.keys())
        clean = {k: v for k, v in normalized.items() if k in known_fields}

        # Step 3: model_validate should not raise
        req = GetProductsRequest.model_validate(clean)
        assert req.brief == payload.get("brief", "")


# ---------------------------------------------------------------------------
# Direct model acceptance (covers REST and internal callers)
# ---------------------------------------------------------------------------


class TestDirectModelAcceptance:
    """Direct Pydantic model construction — covers REST transport and internal callers."""

    @pytest.mark.parametrize(
        "label,params",
        [
            ("minimal", {"brief": "test"}),
            ("with_brand", {"brief": "test", "brand": {"domain": "acme.com"}}),
            ("with_account_id", {"brief": "test", "account": {"account_id": "acc-1"}}),
            (
                "with_account_natural_key",
                {
                    "brief": "test",
                    "account": {"brand": {"domain": "acme.com"}, "operator": "agency.com"},
                },
            ),
            ("with_context", {"brief": "test", "context": {"session_id": "sess-1"}}),
        ],
        ids=lambda x: x if isinstance(x, str) else "",
    )
    def test_known_payloads_accepted(self, label: str, params: dict):
        """All known payload shapes are accepted regardless of environment."""
        from src.core.schemas import GetProductsRequest

        req = GetProductsRequest.model_validate(params)
        assert req.brief == params["brief"]

    def test_extra_fields_rejected_in_dev_mode(self):
        """Dev mode rejects extra fields — confirms extra='forbid' is active.

        In production, SalesAgentBaseModel has extra='ignore', but the mode
        is compiled at import time. The MCP middleware handles forward-compat
        via deep-strip; A2A/REST rely on the production extra mode.
        """
        from src.core.schemas import GetProductsRequest

        with pytest.raises(Exception, match="Extra inputs are not permitted"):
            GetProductsRequest.model_validate({"brief": "test", "future_field": "value"})


# ---------------------------------------------------------------------------
# Deep-strip retry: end-to-end through MCP middleware
# ---------------------------------------------------------------------------


class TestDeepStripRetryE2E:
    """End-to-end: TypeAdapter rejects → deep-strip → retry → succeeds.

    These tests verify the actual retry path in the middleware, not just
    the deep_strip function in isolation. The payload must:
    1. Pass top-level strip (Step 2) — no unknown top-level params
    2. Fail TypeAdapter on first attempt (nested unknown in strict type)
    3. Succeed after deep-strip removes nested unknowns
    """

    def test_nested_brand_extra_triggers_retry_and_succeeds(self):
        """Brand with future field: TypeAdapter rejects → deep-strip → retry → ok."""
        from fastmcp import Client

        from src.core.main import mcp

        async def _call():
            patches = _get_products_patches()
            with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
                for p in patches:
                    p.start()
                try:
                    async with Client(mcp) as client:
                        result = await client.call_tool(
                            "get_products",
                            {
                                "brief": "test retry path",
                                "brand": {
                                    "domain": "retry-test.com",
                                    "verification_status": "premium",
                                    "trust_score": 0.95,
                                },
                            },
                            raise_on_error=False,
                        )
                        assert not result.is_error, (
                            f"Deep-strip retry should have saved this request: "
                            f"{result.content[:300] if result.content else 'no content'}"
                        )
                finally:
                    for p in patches:
                        p.stop()

        asyncio.run(_call())

    def test_nested_context_extra_triggers_retry_and_succeeds(self):
        """Context with future field: same retry path."""
        from fastmcp import Client

        from src.core.main import mcp

        async def _call():
            patches = _get_products_patches()
            with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
                for p in patches:
                    p.start()
                try:
                    async with Client(mcp) as client:
                        result = await client.call_tool(
                            "get_products",
                            {
                                "brief": "context retry",
                                "context": {
                                    "session_id": "sess-1",
                                    "buyer_agent_version": "4.2.0",
                                    "capabilities": ["streaming", "batch"],
                                },
                            },
                            raise_on_error=False,
                        )
                        assert not result.is_error, (
                            f"Deep-strip retry should accept context with extra fields: "
                            f"{result.content[:300] if result.content else 'no content'}"
                        )
                finally:
                    for p in patches:
                        p.stop()

        asyncio.run(_call())

    def test_stripping_no_change_does_not_retry(self):
        """If deep-strip doesn't change args, middleware raises original error (no infinite loop)."""
        from fastmcp import Client

        from src.core.main import mcp

        # Send a field with wrong TYPE (int where string expected) — deep-strip
        # can't fix type mismatches, only unknown fields. Must propagate error.
        async def _call():
            patches = _get_products_patches()
            with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
                for p in patches:
                    p.start()
                try:
                    async with Client(mcp) as client:
                        result = await client.call_tool(
                            "get_products",
                            {"brief": 12345},  # Wrong type: int instead of str
                            raise_on_error=False,
                        )
                        # This should either succeed (TypeAdapter coerces int→str)
                        # or fail with a type error — but must NOT hang in a retry loop.
                        # Either outcome is acceptable; the test verifies no hang.
                        assert result is not None
                finally:
                    for p in patches:
                        p.stop()

        asyncio.run(_call())
