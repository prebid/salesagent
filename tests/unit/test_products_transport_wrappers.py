"""Unit tests for get_products transport wrappers — MCP, A2A, REST, _impl.

salesagent-pm9k: Cover MCP/A2A transport wrapper lines in products.py.

Tests the wrapper logic (request construction, error translation,
response serialization, version compat) independent of business logic.
Business logic is mocked via _get_products_impl.

# --- Test Source-of-Truth Audit ---
# Audited: 2026-03-07
#
# SPEC_BACKED (1 test):
#   test_rest_returns_json_response — AdCP get-products-response.json + protocol-envelope.json
#
# ARCH_BACKED (9 tests):
#   test_mcp_wrapper_returns_tool_result — CLAUDE.md #5: MCP returns ToolResult
#   test_mcp_wrapper_validation_error_raises_adcp_validation — CLAUDE.md #5 + no-ToolError guard
#   test_mcp_wrapper_value_error_raises_adcp_validation — CLAUDE.md #5: error translation
#   test_mcp_wrapper_reads_identity_from_ctx_state — CLAUDE.md #5: wrapper resolves identity
#   test_a2a_wrapper_returns_response_model — CLAUDE.md #5 + protocol-envelope.json notes
#   test_a2a_wrapper_passes_identity_to_impl — CLAUDE.md #5: forward all params
#   test_a2a_wrapper_constructs_request_from_params — CLAUDE.md #5: wrapper builds request
#   test_mcp_passes_none_identity_when_no_ctx — CLAUDE.md #5: identity optional
#   test_a2a_passes_none_identity_when_not_provided — CLAUDE.md #5: identity optional
#
# DECISION_BACKED (2 tests):
#   test_a2a_wrapper_no_version_compat — arch decision: compat at handler level
#   test_mcp_wrapper_no_version_compat — arch decision: compat at handler level (parity with A2A)
#
# CHARACTERIZATION (2 tests):
#   test_a2a_wrapper_empty_brief_uses_empty_string — locks: empty brief handling
#   test_rest_applies_version_compat — locks: REST applies compat
# ---
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.server.context import Context
from pydantic import ValidationError

from src.core.exceptions import AdCPValidationError
from src.core.schemas import GetProductsResponse
from tests.factories import PrincipalFactory


def _mock_response() -> GetProductsResponse:
    """Build a minimal GetProductsResponse for testing wrapper logic."""
    return GetProductsResponse(products=[])


def _response_with_fixed_price(fixed_price: float = 5.0) -> GetProductsResponse:
    """A GetProductsResponse whose single product carries a fixed-price CPM option.

    The pricing-option model is what apply_version_compat reads to derive the
    v2-compat fields (is_fixed / rate), so the response must carry a real model
    (not a pre-dumped dict) for the transform to fire.
    """
    from tests.helpers.adcp_factories import make_get_products_response_with_pricing

    return make_get_products_response_with_pricing(fixed_price=fixed_price)


# ---------------------------------------------------------------------------
# MCP wrapper: get_products()
# ---------------------------------------------------------------------------


class TestMcpGetProductsWrapper:
    """Tests for the async MCP wrapper get_products()."""

    def test_mcp_wrapper_returns_tool_result(self):
        """MCP wrapper returns ToolResult with structured_content."""
        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=PrincipalFactory.make_identity(protocol="mcp"))

        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ):
            from src.core.tools.products import get_products

            result = asyncio.run(get_products(brief="video ads", ctx=mock_ctx))

        assert result.structured_content is not None
        assert "products" in result.structured_content
        assert result.content is not None  # Human-readable text

    def test_mcp_wrapper_validation_error_raises_adcp_validation(self):
        """MCP wrapper translates ValidationError to AdCPValidationError."""
        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=None)

        with patch(
            "src.core.tools.products.create_get_products_request",
            side_effect=ValidationError.from_exception_data(
                title="GetProductsRequest",
                line_errors=[
                    {
                        "type": "missing",
                        "loc": ("brief",),
                        "msg": "Field required",
                        "input": {},
                    }
                ],
            ),
        ):
            from src.core.tools.products import get_products

            with pytest.raises(AdCPValidationError):
                asyncio.run(get_products(brief="", ctx=mock_ctx))

    def test_mcp_wrapper_value_error_raises_adcp_validation(self):
        """MCP wrapper translates ValueError to AdCPValidationError."""
        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=None)

        with patch(
            "src.core.tools.products.create_get_products_request",
            side_effect=ValueError("Invalid brand format"),
        ):
            from src.core.tools.products import get_products

            with pytest.raises(AdCPValidationError, match="Invalid get_products request"):
                asyncio.run(get_products(brief="ads", ctx=mock_ctx))

    def test_mcp_wrapper_no_version_compat(self):
        """MCP wrapper does NOT apply version compat — that's the handler's job (parity with A2A)."""
        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=PrincipalFactory.make_identity(protocol="mcp"))

        with (
            patch(
                "src.core.tools.products._get_products_impl",
                new_callable=AsyncMock,
                return_value=_mock_response(),
            ),
            patch("src.core.version_compat.apply_version_compat") as mock_compat,
        ):
            from src.core.tools.products import get_products

            asyncio.run(get_products(brief="ads", ctx=mock_ctx))

        mock_compat.assert_not_called()

    def test_mcp_wrapper_reads_identity_from_ctx_state(self):
        """MCP wrapper reads identity from ctx.get_state('identity')."""
        identity = PrincipalFactory.make_identity(protocol="mcp")
        mock_ctx = MagicMock(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=identity)

        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ) as mock_impl:
            from src.core.tools.products import get_products

            asyncio.run(get_products(brief="video", ctx=mock_ctx))

        mock_ctx.get_state.assert_awaited_once_with("identity")
        mock_impl.assert_awaited_once()
        _, call_identity = mock_impl.call_args.args
        assert call_identity is identity


# ---------------------------------------------------------------------------
# A2A wrapper: get_products_raw()
# ---------------------------------------------------------------------------


class TestA2AGetProductsRawWrapper:
    """Tests for the A2A wrapper get_products_raw()."""

    def test_a2a_wrapper_returns_response_model(self):
        """A2A wrapper returns GetProductsResponse directly (not ToolResult)."""
        identity = PrincipalFactory.make_identity(protocol="a2a")

        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ):
            from src.core.tools.products import get_products_raw

            result = asyncio.run(get_products_raw(brief="display ads", identity=identity))

        assert isinstance(result, GetProductsResponse)
        assert result.products == []

    def test_a2a_wrapper_passes_identity_to_impl(self):
        """A2A wrapper forwards identity to _get_products_impl."""
        identity = PrincipalFactory.make_identity(protocol="a2a")

        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ) as mock_impl:
            from src.core.tools.products import get_products_raw

            asyncio.run(get_products_raw(brief="video", identity=identity))

        mock_impl.assert_awaited_once()
        _, call_identity = mock_impl.call_args.args
        assert call_identity is identity

    def test_a2a_wrapper_constructs_request_from_params(self):
        """A2A wrapper constructs GetProductsRequest from its parameters."""
        identity = PrincipalFactory.make_identity(protocol="a2a")

        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ) as mock_impl:
            from src.core.tools.products import get_products_raw

            asyncio.run(
                get_products_raw(
                    brief="sports ads",
                    filters={"delivery_types": ["guaranteed"]},
                    identity=identity,
                )
            )

        req = mock_impl.call_args.args[0]
        assert req.brief == "sports ads"
        assert req.filters is not None

    def test_a2a_wrapper_no_version_compat(self):
        """A2A wrapper does NOT apply version compat — that's the A2A handler's job."""
        identity = PrincipalFactory.make_identity(protocol="a2a")

        with (
            patch(
                "src.core.tools.products._get_products_impl",
                new_callable=AsyncMock,
                return_value=_mock_response(),
            ),
            patch("src.core.version_compat.apply_version_compat") as mock_compat,
        ):
            from src.core.tools.products import get_products_raw

            asyncio.run(get_products_raw(brief="ads", identity=identity))

        mock_compat.assert_not_called()

    def test_a2a_wrapper_empty_brief_uses_empty_string(self):
        """A2A wrapper passes empty string when brief is empty."""
        identity = PrincipalFactory.make_identity(protocol="a2a")

        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ) as mock_impl:
            from src.core.tools.products import get_products_raw

            asyncio.run(get_products_raw(brief="", identity=identity))

        req = mock_impl.call_args.args[0]
        # brief="" → create_get_products_request normalizes to None
        assert req.brief is None or req.brief == ""


# ---------------------------------------------------------------------------
# REST wrapper: /api/v1/products
# ---------------------------------------------------------------------------


class TestRestGetProductsWrapper:
    """Tests for the REST endpoint POST /api/v1/products."""

    def test_rest_returns_json_response(self):
        """REST endpoint returns JSON with products array."""
        identity = PrincipalFactory.make_identity(protocol="rest")

        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ):
            from starlette.testclient import TestClient

            from src.app import app
            from src.core.auth_context import _require_auth_dep, _resolve_auth_dep

            app.dependency_overrides[_require_auth_dep] = lambda: identity
            app.dependency_overrides[_resolve_auth_dep] = lambda: identity
            try:
                client = TestClient(app)
                response = client.post(
                    "/api/v1/products",
                    json={"brief": "video ads"},
                )
            finally:
                app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert "products" in data

    def _post_products(self, body: dict):
        """POST /api/v1/products (no query pins) with a fixed-price product."""
        return self._post_products_to("/api/v1/products", body)

    def test_rest_forwards_and_echoes_request_context(self):
        """REST /products forwards the buyer's context so the response echoes it (#1512).

        REST was the only transport dropping context (MCP/A2A forward it and the impl
        already echoes ``req.context``). Asserts the actual wire JSON, mutation-sensitive
        to the REST forward: if the handler drops context, ``req.context`` is None and the
        echoed response context is absent.
        """

        async def _echo_context_impl(req, identity):
            resp = _response_with_fixed_price(fixed_price=5.0)
            resp.context = req.context  # mirror the production impl's context echo
            return resp

        identity = PrincipalFactory.make_identity(protocol="rest")
        with patch("src.core.tools.products._get_products_impl", new=_echo_context_impl):
            from starlette.testclient import TestClient

            from src.app import app
            from src.core.auth_context import _require_auth_dep, _resolve_auth_dep

            app.dependency_overrides[_require_auth_dep] = lambda: identity
            app.dependency_overrides[_resolve_auth_dep] = lambda: identity
            try:
                response = TestClient(app).post(
                    "/api/v1/products", json={"brief": "ads", "context": {"context_id": "ctx-rest-echo"}}
                )
            finally:
                app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json()["context"] == {"context_id": "ctx-rest-echo"}

    def _post_products_to(self, url: str, body: dict):
        """POST to a products URL (optionally carrying query version pins).

        apply_version_compat is NOT mocked — the assertion is on the real wire
        JSON, so the transform must actually run against the returned model.
        """
        identity = PrincipalFactory.make_identity(protocol="rest")

        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_response_with_fixed_price(fixed_price=5.0),
        ):
            from starlette.testclient import TestClient

            from src.app import app
            from src.core.auth_context import _require_auth_dep, _resolve_auth_dep

            app.dependency_overrides[_require_auth_dep] = lambda: identity
            app.dependency_overrides[_resolve_auth_dep] = lambda: identity
            try:
                return TestClient(app).post(url, json=body)
            finally:
                app.dependency_overrides.clear()

    def test_rest_unpinned_client_gets_v2_compat_fields_on_the_wire(self):
        """Unpinned REST client (Body "1.0.0" default) gets v2-compat pricing on the wire.

        Regression for the dict-passthrough no-op (#1546 review): the endpoint must
        pass the response MODEL to apply_version_compat, not a pre-dumped dict, or
        the v2 fields are never derived. Asserts the ACTUAL REST JSON, not that the
        helper was merely called.
        """
        response = self._post_products({"brief": "ads"})  # no adcp_version → "1.0.0" default

        assert response.status_code == 200
        po = response.json()["products"][0]["pricing_options"][0]
        assert po["is_fixed"] is True, f"unpinned legacy client should get v2-compat is_fixed: {po}"
        assert po["rate"] == 5.0, f"v2-compat rate must mirror fixed_price: {po}"

    def test_rest_v3_pin_gets_clean_response_no_compat_fields(self):
        """A same-major v3 pin gets a clean v3 response — proving the compat is version-gated.

        Guards against a regression that unconditionally applies v2 compat: pinning
        a supported release must NOT inject the v2 is_fixed/rate fields.
        """
        from src.core.adcp_version import supported_adcp_versions

        response = self._post_products({"brief": "ads", "adcp_version": supported_adcp_versions()[0]})

        assert response.status_code == 200
        po = response.json()["products"][0]["pricing_options"][0]
        assert "is_fixed" not in po, f"v3 client must get a clean response, got v2 fields: {po}"
        assert "rate" not in po

    def test_rest_query_release_pin_gets_clean_response(self):
        """A v3 release pinned via the QUERY string gets clean v3, not the v2 shape (#1512).

        Response compat previously gated on GetProductsBody.adcp_version alone
        (default "1.0.0"), which a query pin never populates — so a v3 query client
        was silently served is_fixed/rate.
        """
        from urllib.parse import quote

        from src.core.adcp_version import supported_adcp_versions

        response = self._post_products_to(
            f"/api/v1/products?adcp_version={quote(supported_adcp_versions()[0])}", {"brief": "ads"}
        )

        assert response.status_code == 200
        po = response.json()["products"][0]["pricing_options"][0]
        assert "is_fixed" not in po, f"query v3 pin must get a clean response, got v2 fields: {po}"
        assert "rate" not in po

    def test_rest_body_major_pin_gets_clean_response(self):
        """A v3 pin via the deprecated body adcp_major_version gets clean v3 (#1512)."""
        from src.core.adcp_version import adcp_major_version

        response = self._post_products({"brief": "ads", "adcp_major_version": adcp_major_version()})

        assert response.status_code == 200
        po = response.json()["products"][0]["pricing_options"][0]
        assert "is_fixed" not in po, f"body major={adcp_major_version()} pin must get a clean response: {po}"
        assert "rate" not in po

    def test_rest_query_major_pin_gets_clean_response(self):
        """A v3 pin via the QUERY adcp_major_version gets clean v3 (#1512)."""
        from src.core.adcp_version import adcp_major_version

        response = self._post_products_to(
            f"/api/v1/products?adcp_major_version={adcp_major_version()}", {"brief": "ads"}
        )

        assert response.status_code == 200
        po = response.json()["products"][0]["pricing_options"][0]
        assert "is_fixed" not in po, f"query major={adcp_major_version()} pin must get a clean response: {po}"
        assert "rate" not in po

    def test_rest_conflicting_query_and_body_pins_rejected(self):
        """Conflicting query vs body release pins are rejected before any response is built (#1512)."""
        from src.core.adcp_version import supported_adcp_versions

        response = self._post_products_to(
            "/api/v1/products?adcp_version=4.0", {"brief": "ads", "adcp_version": supported_adcp_versions()[0]}
        )

        assert response.status_code == 400
        assert response.json()["adcp_error"]["code"] == "VALIDATION_ERROR"

    def test_rest_matching_query_and_body_pins_pass_clean(self):
        """The same v3 release pinned in BOTH query and body is accepted and served clean (#1512)."""
        from urllib.parse import quote

        from src.core.adcp_version import supported_adcp_versions

        pin = supported_adcp_versions()[0]
        response = self._post_products_to(
            f"/api/v1/products?adcp_version={quote(pin)}", {"brief": "ads", "adcp_version": pin}
        )

        assert response.status_code == 200
        po = response.json()["products"][0]["pricing_options"][0]
        assert "is_fixed" not in po, f"matching v3 pins must get a clean response: {po}"


# ---------------------------------------------------------------------------
# _impl direct: wrapper passes identity correctly
# ---------------------------------------------------------------------------


class TestImplDirectIdentity:
    """Tests that wrappers correctly pass identity to _get_products_impl."""

    def test_mcp_passes_none_identity_when_no_ctx(self):
        """MCP wrapper passes None identity when ctx is not Context type."""
        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ) as mock_impl:
            from src.core.tools.products import get_products

            asyncio.run(get_products(brief="test", ctx=None))

        _, identity = mock_impl.call_args.args
        assert identity is None

    def test_a2a_passes_none_identity_when_not_provided(self):
        """A2A wrapper passes None identity when not explicitly provided."""
        with patch(
            "src.core.tools.products._get_products_impl",
            new_callable=AsyncMock,
            return_value=_mock_response(),
        ) as mock_impl:
            from src.core.tools.products import get_products_raw

            asyncio.run(get_products_raw(brief="test"))

        _, identity = mock_impl.call_args.args
        assert identity is None
