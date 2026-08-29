"""Tests for REST API /api/v1/products endpoint.

Validates the first REST transport for get_products:
- Endpoint exists and returns 200
- Response has 'products' field
- Auth-optional (discovery skill)
- The body declares, and the route forwards, every field the request object carries
- Error responses use AdCPError format

MCP-side schema grading for get_products lives in tests/unit/test_mcp_tool_schemas.py,
not here.
"""

from unittest.mock import patch

import pytest
from adcp import BuyingMode
from starlette.testclient import TestClient

from src.app import app
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import GetProductsResponse
from tests.helpers import assert_envelope_shape

_MOCK_IDENTITY = ResolvedIdentity(
    principal_id="test-principal",
    tenant_id="default",
    tenant={"tenant_id": "default"},
    auth_token="test-token",
    protocol="rest",
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

client = TestClient(app)


# ---------------------------------------------------------------------------
# Route Existence
# ---------------------------------------------------------------------------


class TestRESTProductsEndpoint:
    """Verify POST /api/v1/products endpoint."""

    @pytest.fixture
    def stub_impl(self):
        """Patch _get_products_impl to the fixed sentinel every happy path drives.

        Only _get_products_impl is patched here — not resolve_identity — so the
        no-auth discovery test can share this fixture while still exercising the
        real unauthenticated identity path. Tests that need a resolved identity add
        the resolve_identity patch as a decorator.
        """
        with patch("src.core.tools.products._get_products_impl") as mock_impl:
            mock_impl.return_value = GetProductsResponse(products=[], message="test")
            yield mock_impl

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({"brief": "video ads"}, id="no-buying-mode-current-leniency"),
            *(
                pytest.param({"brief": "video ads", "buying_mode": mode.value}, id=f"with-{mode.value}")
                for mode in BuyingMode
            ),
        ],
    )
    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    def test_endpoint_accepts_spec_valid_body(self, mock_resolve, stub_impl, body):
        """Every adcp.BuyingMode member is accepted at 200, and none of them reaches the request.

        The rows are generated from ``adcp.BuyingMode`` rather than hand-listed, so this
        is the non-vacuity oracle for the REST field being typed to the SDK enum: retype
        GetProductsBody.buying_mode to a narrower hand-written Literal and the dropped
        member's row reddens with HTTP 400. Under the dev/CI extra="forbid" policy an
        undeclared field is rejected outright, which is why REST has to declare it at all.

        ``no-buying-mode-current-leniency`` is a characterization row, not a conformance
        one: get-products-request.json@3.1.1 lists buying_mode in its ``required`` array,
        so an omitting client is spec-invalid. This route accepts it anyway; the row pins
        that leniency as today's behaviour, with enforcement deferred to #1730.

        The ``req.buying_mode is None`` assertion pins accept-and-ignore as a tested
        decision rather than an unobserved side effect — the route declares the field so a
        conformant client is not rejected, but no transport acts on the value yet. It
        reddens the day buying_mode is threaded, forcing a deliberate update.

        Deletion oracle: drop ``buying_mode`` from GetProductsBody and every
        ``with-*`` row reddens with HTTP 400 (INVALID_REQUEST, recovery=correctable) —
        not the 422 an earlier version of this docstring claimed. FastAPI's default 422 is
        replaced by the RequestValidationError handler in src/app.py, which maps
        INVALID_REQUEST to 400.
        """
        response = client.post(
            "/api/v1/products",
            json=body,
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200, (
            f"spec-valid body should be accepted, got {response.status_code}: {response.text}"
        )
        assert stub_impl.await_count == 1, "an accepted body must reach _get_products_impl"
        req = stub_impl.await_args.args[0]
        assert req.buying_mode is None, (
            f"buying_mode is accept-and-ignore today — the route must not thread it into "
            f"GetProductsRequest, got {req.buying_mode!r}. Wiring it (#1730) has to update "
            f"this assertion deliberately."
        )

    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    def test_route_forwards_property_list_and_context(self, mock_resolve, stub_impl):
        """property_list and context reach GetProductsRequest, as they do on MCP and A2A.

        property_list is not decorative: ``_get_products_impl`` resolves it and filters the
        returned set through ``filter_products_by_property_list``. While GetProductsBody
        omitted the field, FastAPI dropped it before the route ran, so a REST buyer that
        sent property_list received an UNFILTERED catalog at HTTP 200 — a wrong answer with
        no error — while the MCP tool and the A2A skill handler both forwarded it.

        Deletion oracle: remove either field from GetProductsBody, or stop passing it into
        create_get_products_request, and this reddens. The declaration half is additionally
        pinned by test_architecture_rest_body_completeness.py, which now pairs
        GetProductsBody with get_products_raw.
        """
        response = client.post(
            "/api/v1/products",
            json={
                "brief": "video ads",
                "property_list": {"agent_url": "https://props.example.com", "list_id": "list-1"},
                "context": {"campaign_id": "camp-1"},
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200, f"expected 200, got {response.status_code}: {response.text}"

        req = stub_impl.await_args.args[0]
        assert req.property_list is not None, "REST dropped property_list; MCP and A2A forward it"
        assert req.property_list.list_id == "list-1"
        assert str(req.property_list.agent_url).rstrip("/") == "https://props.example.com"
        assert req.context is not None, "REST dropped context; MCP and A2A forward it"
        assert req.context.model_dump()["campaign_id"] == "camp-1"

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({"brief": "video ads", "buying_mode": "garbage"}, id="out-of-enum-buying-mode"),
            pytest.param({"brief": "video ads", "not_a_real_field": "x"}, id="undeclared-field"),
        ],
    )
    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    def test_spec_invalid_body_is_rejected_with_the_adcp_envelope(self, mock_resolve, stub_impl, body):
        """A spec-invalid body 400s with the two-layer AdCP envelope, not FastAPI's 422.

        Mirrors the accept parametrize above: both rejection shapes share one oracle
        (400 + INVALID_REQUEST/correctable envelope), differing only in the request body.

        out-of-enum-buying-mode: get-products-request.json@3.1.1 defines buying_mode as
        enum ["brief","wholesale","refine"], and the UC-001 storyboard grades the
        out-of-enum case as an error (@T-UC-001-partition-buying-mode: unknown_value →
        "buying_mode must be one of enum values"; @T-UC-001-boundary-buying-mode:
        buying_mode='auction' → invalid). Typing the REST field to that Literal makes
        the boundary reject a non-spec value rather than accept it at 200 — this row
        is the non-vacuity oracle for that narrowing.

        undeclared-field: the other half of the buying_mode case and the reason it
        cannot pass vacuously — it proves extra="forbid" is actually engaged on this
        route. Without it, a GetProductsBody that had silently fallen back to
        extra="ignore" would accept buying_mode for the wrong reason and the positive
        test would still be green. It also backs this module's docstring claim that
        error responses use the AdCPError format.

        Both surface through the same RequestValidationError handler as any structural
        rejection, so the wire code is INVALID_REQUEST/correctable (the value-vs-structural
        code split is a repo-wide follow-up, not this PR).
        """
        response = client.post(
            "/api/v1/products",
            json=body,
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 400, f"expected 400, got {response.status_code}: {response.text}"
        assert_envelope_shape(response.json(), "INVALID_REQUEST", recovery="correctable")
        stub_impl.assert_not_called()  # rejected at the boundary — the impl is never reached

    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    def test_response_has_products_field(self, mock_resolve, stub_impl):
        """Response must contain 'products' list."""
        response = client.post(
            "/api/v1/products",
            json={"brief": "video ads"},
            headers={"Authorization": "Bearer test-token"},
        )
        body = response.json()
        assert "products" in body
        assert isinstance(body["products"], list)

    def test_works_without_auth(self, stub_impl):
        """get_products is a discovery skill — should work without auth."""
        response = client.post(
            "/api/v1/products",
            json={"brief": "video ads"},
        )
        # Should return 200, not 401 — discovery skill allows unauthenticated access
        assert response.status_code == 200, f"Discovery skill should work without auth, got {response.status_code}"

    def test_endpoint_not_404(self):
        """POST /api/v1/products must exist (not 404)."""
        response = client.post(
            "/api/v1/products",
            json={"brief": "test"},
        )
        assert response.status_code != 404, "REST endpoint /api/v1/products should exist"
