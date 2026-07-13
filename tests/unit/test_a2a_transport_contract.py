"""A2A Transport Contract Tests — Phase 0 regression gate for handler migration.

These tests verify the HTTP boundary shape for all A2A skills:
- Route existence (not 404)
- Auth contract (discovery vs auth-required)
- JSON-RPC protocol correctness
- Response field presence (shape, not values)

They use TestClient (in-process ASGI) with mocked _impl functions.
No Docker required. This is the regression gate between every Phase 2 step.

beads: salesagent-b61l.17
"""

import json
import uuid
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from src.a2a_server.adcp_a2a_server import DISCOVERY_SKILLS as _PROD_DISCOVERY_SKILLS
from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.app import app
from tests.utils.a2a_helpers import make_test_a2a_identity

_TEST_IDENTITY = make_test_a2a_identity()

# ---------------------------------------------------------------------------
# Every A2A skill in the production dispatch registry (adcp_a2a_server.py
# ``_skill_handler_map``). Kept in bijection with production by
# test_transport_inventory_matches_production_registry — a skill added to the
# registry without a wire test here fails the build.
# ---------------------------------------------------------------------------
ALL_SKILLS = sorted(AdCPRequestHandler()._skill_handler_map().keys())

# Discovery (no-auth) skills come from the production frozenset so this never drifts.
DISCOVERY_SKILLS = sorted(_PROD_DISCOVERY_SKILLS)

AUTH_REQUIRED_SKILLS = [s for s in ALL_SKILLS if s not in DISCOVERY_SKILLS]

# Registered skills deliberately NOT advertised on the agent card: unimplemented
# creative stubs (validate params, then return UNSUPPORTED_FEATURE). The agent
# should not advertise capabilities it does not offer.
UNADVERTISED_SKILLS = {"create_creative", "assign_creative"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_jsonrpc(skill: str, params: dict | None = None, request_id: str | None = None) -> dict:
    """Build a JSON-RPC 2.0 SendMessage request with explicit skill invocation."""
    return {
        "jsonrpc": "2.0",
        "id": request_id or str(uuid.uuid4()),
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "ROLE_USER",
                "parts": [{"data": {"skill": skill, "parameters": params or {}}}],
            }
        },
    }


def _extract_jsonrpc_result(response) -> dict:
    """Extract the result from a JSON-RPC success response."""
    body = response.json()
    assert "result" in body, f"Expected JSON-RPC result, got: {json.dumps(body, indent=2)[:500]}"
    return body["result"]


def _extract_jsonrpc_error(response) -> dict:
    """Extract the error from a JSON-RPC error response."""
    body = response.json()
    assert "error" in body, f"Expected JSON-RPC error, got: {json.dumps(body, indent=2)[:500]}"
    return body["error"]


def _extract_artifact_data(result: dict) -> dict:
    """Extract data from the first artifact's DataPart.

    a2a-sdk 1.0 protobuf format: result is {"task": {...}} or {"message": {...}}.
    Parts use oneof: {"data": {...}} or {"text": "..."} (no "kind" field).
    """
    # Unwrap task envelope if present
    task = result.get("task", result)
    artifacts = task.get("artifacts", [])
    if not artifacts:
        return {}
    for part in artifacts[0].get("parts", []):
        if "data" in part:
            return part["data"]
    return {}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """TestClient for the unified FastAPI app."""
    c = TestClient(app, raise_server_exceptions=False)
    yield c
    c.close()


@pytest.fixture
def auth_headers():
    """Headers with a valid Bearer token."""
    return {"Authorization": "Bearer test-transport-token", "Content-Type": "application/json", "A2A-Version": "1.0"}


@pytest.fixture
def no_auth_headers():
    """Headers without authentication."""
    return {"Content-Type": "application/json", "A2A-Version": "1.0"}


# ---------------------------------------------------------------------------
# Route Existence
# ---------------------------------------------------------------------------


class TestA2ARouteExistence:
    """Verify A2A routes exist (not 404)."""

    def test_a2a_endpoint_exists(self, client):
        """POST /a2a should not return 404."""
        payload = _build_jsonrpc("get_products", {"brief": "test"})
        response = client.post("/a2a", json=payload)
        assert response.status_code != 404, "A2A endpoint /a2a should exist"

    def test_agent_card_endpoint_exists(self, client):
        """GET /.well-known/agent-card.json should return 200."""
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 200

    def test_agent_card_has_required_fields(self, client):
        """Agent card must have name, supportedInterfaces, skills, capabilities."""
        response = client.get("/.well-known/agent-card.json")
        card = response.json()
        for field in ["name", "supportedInterfaces", "skills", "capabilities"]:
            assert field in card, f"Agent card missing '{field}'"
        assert card["name"] == "Prebid Sales Agent"
        # a2a-sdk 1.0: URL is inside supportedInterfaces, not top-level
        assert len(card["supportedInterfaces"]) > 0
        assert "url" in card["supportedInterfaces"][0]


# ---------------------------------------------------------------------------
# Auth Contract
# ---------------------------------------------------------------------------


class TestA2AAuthContract:
    """Verify auth boundary: discovery vs auth-required skills."""

    @pytest.mark.parametrize("skill", DISCOVERY_SKILLS)
    def test_discovery_skills_accept_no_auth(self, client, no_auth_headers, skill):
        """Discovery skills should NOT return auth error without token."""
        payload = _build_jsonrpc(skill, {})
        response = client.post("/a2a", json=payload, headers=no_auth_headers)
        body = response.json()
        # Should not get an auth error
        if "error" in body:
            error_msg = body["error"].get("message", "").lower()
            # Check for explicit auth rejection (not just "authorized" in property names)
            auth_rejection_phrases = [
                "authentication token required",
                "missing authentication token",
                "bearer token required",
            ]
            for phrase in auth_rejection_phrases:
                assert phrase not in error_msg, (
                    f"Discovery skill '{skill}' rejected unauthenticated request: {body['error']}"
                )

    @pytest.mark.parametrize("skill", AUTH_REQUIRED_SKILLS)
    def test_auth_required_skills_reject_no_auth(self, client, no_auth_headers, skill):
        """Auth-required skills MUST reject requests without token."""
        payload = _build_jsonrpc(skill, {})
        response = client.post("/a2a", json=payload, headers=no_auth_headers)
        body = response.json()
        assert "error" in body, f"Auth-required skill '{skill}' should return error without token"
        error_msg = body["error"].get("message", "").lower()
        assert "auth" in error_msg or "token" in error_msg, (
            f"Error for '{skill}' should mention auth/token: {body['error']['message']}"
        )


# ---------------------------------------------------------------------------
# JSON-RPC Protocol
# ---------------------------------------------------------------------------


class TestA2AJsonRpcProtocol:
    """Verify JSON-RPC protocol compliance."""

    def test_invalid_method_returns_error(self, client, auth_headers):
        """Unknown JSON-RPC method should return method-not-found error."""
        payload = {"jsonrpc": "2.0", "id": "test-1", "method": "nonexistent/method", "params": {}}
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()
        assert "error" in body, "Unknown method should return JSON-RPC error"

    @patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY)
    def test_unknown_skill_returns_failed_task_not_transport_error(self, mock_resolve, client, auth_headers):
        """Unknown skill name returns a failed Task with UNSUPPORTED_FEATURE, not JSON-RPC.

        The JSON-RPC method (message/send) is valid; routing failed *inside*
        skill dispatch, which is an application-layer failure. Per AdCP
        3.1.0-beta.3 transport-errors.mdx "Layer Separation" it belongs in the
        task body as a failed Task carrying a two-layer envelope — JSON-RPC
        MethodNotFoundError is reserved for unknown JSON-RPC *methods*
        (see test_invalid_method_returns_error). Identity is mocked so the
        request reaches skill dispatch.
        """
        payload = _build_jsonrpc("nonexistent_skill", {})
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()

        assert "error" not in body, f"unknown skill must not be a JSON-RPC error: {body.get('error')}"
        assert "result" in body, f"expected a failed-Task result, got: {json.dumps(body)[:400]}"
        data = _extract_artifact_data(body["result"])
        assert data.get("adcp_error", {}).get("code") == "UNSUPPORTED_FEATURE", (
            f"unknown skill must surface UNSUPPORTED_FEATURE in the task body: {data}"
        )
        assert data.get("adcp_error", {}).get("recovery") == "correctable", data
        assert "nonexistent_skill" in data["errors"][0]["message"], data

    def test_response_echoes_request_id(self, client, auth_headers):
        """JSON-RPC response must echo the request id."""
        payload = _build_jsonrpc("get_products", {"brief": "test"}, request_id="echo-test-42")
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()
        assert body.get("id") == "echo-test-42", "Response must echo request id"

    def test_response_has_jsonrpc_field(self, client, auth_headers):
        """Response must have jsonrpc: '2.0' field."""
        payload = _build_jsonrpc("get_products", {"brief": "test"})
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()
        assert body.get("jsonrpc") == "2.0", "Response must have jsonrpc: '2.0'"

    def test_numeric_request_id_handled(self, client, auth_headers):
        """Numeric JSON-RPC id should be handled (middleware converts to string)."""
        payload = {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "SendMessage",
            "params": {"message": {"messageId": "msg-1", "role": "ROLE_USER", "parts": [{"text": "hello"}]}},
        }
        response = client.post("/a2a", json=payload, headers=auth_headers)
        # Should not crash with TypeError
        assert response.status_code != 500 or b"TypeError" not in response.content


# ---------------------------------------------------------------------------
# Response Shape — Key Skills
# ---------------------------------------------------------------------------


class TestA2AResponseShape:
    """Verify response field shapes for representative skills.

    These tests mock _impl functions to return known responses,
    testing the full transport chain: middleware → dispatch → serialization.
    """

    @patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY)
    @patch("src.core.tools.products._get_products_impl")
    def test_get_products_response_shape(self, mock_impl, mock_resolve, client, auth_headers):
        """get_products response must contain 'products' list."""
        from src.core.schemas import GetProductsResponse

        mock_impl.return_value = GetProductsResponse(products=[], message="test")

        payload = _build_jsonrpc("get_products", {"brief": "test"})
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()

        if "result" in body:
            result = body["result"]
            assert "task" in result, "SendMessage result must contain 'task'"
            data = _extract_artifact_data(result)
            assert "products" in data, "get_products response must have 'products' field"
            assert isinstance(data["products"], list)

    @patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY)
    @patch("src.core.tools.media_buy_create._create_media_buy_impl")
    def test_create_media_buy_response_shape(self, mock_impl, mock_resolve, client, auth_headers):
        """create_media_buy response must have media_buy_id and buyer_ref."""
        from adcp.types.aliases import CreateMediaBuySuccessResponse

        mock_impl.return_value = CreateMediaBuySuccessResponse(
            media_buy_id="mb-test-1",
            packages=[],
        )

        payload = _build_jsonrpc(
            "create_media_buy",
            {
                "brand": {"domain": "testbrand.com"},
                "packages": [{"product_id": "p1", "budget": 1000.0, "pricing_option_id": "cpm"}],
                "start_time": "2026-03-01T00:00:00Z",
                "end_time": "2026-03-31T00:00:00Z",
                "idempotency_key": "unit-test-key-a2a-shape-0001",
            },
        )
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()

        if "result" in body:
            data = _extract_artifact_data(body["result"])
            assert "media_buy_id" in data, "create_media_buy response must have 'media_buy_id'"
            # buyer_ref removed from CreateMediaBuySuccess in adcp 3.12

    def test_error_format_is_jsonrpc(self, client, auth_headers):
        """Error responses must use JSON-RPC error envelope, not {success: false}."""
        # Send a request that will fail (unknown skill)
        payload = _build_jsonrpc("nonexistent_skill", {})
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()

        # Must be JSON-RPC format
        assert "error" in body or "result" in body, "Response must be JSON-RPC format"
        if "error" in body:
            assert "code" in body["error"], "JSON-RPC error must have 'code'"
            assert "message" in body["error"], "JSON-RPC error must have 'message'"

    @patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY)
    @patch("src.a2a_server.adcp_a2a_server.core_sync_creatives_tool")
    def test_sync_creatives_response_shape(self, mock_impl, mock_resolve, client, auth_headers):
        """sync_creatives response must contain 'creatives' or 'synced_creatives'."""
        from src.core.schemas import SyncCreativesResponse

        mock_impl.return_value = SyncCreativesResponse(creatives=[], failed_creatives=[])

        payload = _build_jsonrpc("sync_creatives", {"creatives": []})
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()

        if "result" in body:
            data = _extract_artifact_data(body["result"])
            assert "creatives" in data or "synced_creatives" in data, (
                "sync_creatives response must have 'creatives' field"
            )

    @patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY)
    @patch("src.a2a_server.adcp_a2a_server.core_list_creatives_tool")
    def test_list_creatives_response_shape(self, mock_impl, mock_resolve, client, auth_headers):
        """list_creatives response must contain 'creatives' list."""
        from src.core.schemas import ListCreativesResponse

        # adcp 3.6.0: Pagination uses cursor-based pagination (has_more, total_count, cursor)
        mock_impl.return_value = ListCreativesResponse(
            creatives=[],
            pagination={"has_more": False, "total_count": 0},
            query_summary={"filters_applied": [], "returned": 0, "total_matching": 0},
        )

        payload = _build_jsonrpc("list_creatives", {})
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()

        if "result" in body:
            data = _extract_artifact_data(body["result"])
            assert "creatives" in data, "list_creatives response must have 'creatives' field"
            assert isinstance(data["creatives"], list)

    @patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY)
    @patch("src.a2a_server.adcp_a2a_server.core_update_media_buy_tool")
    def test_update_media_buy_response_shape(self, mock_impl, mock_resolve, client, auth_headers):
        """update_media_buy response must have media_buy_id."""
        from adcp.types.aliases import UpdateMediaBuySuccessResponse

        mock_impl.return_value = UpdateMediaBuySuccessResponse(
            media_buy_id="mb-test-1",
            affected_packages=[],
        )

        payload = _build_jsonrpc("update_media_buy", {"media_buy_id": "mb-test-1", "paused": False})
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()

        if "result" in body:
            data = _extract_artifact_data(body["result"])
            assert "media_buy_id" in data, "update_media_buy response must have 'media_buy_id'"

    @patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY)
    @patch("src.a2a_server.adcp_a2a_server.core_get_media_buy_delivery_tool")
    def test_get_media_buy_delivery_response_shape(self, mock_impl, mock_resolve, client, auth_headers):
        """get_media_buy_delivery response must have 'deliveries' or 'media_buys'."""
        from src.core.schemas import GetMediaBuyDeliveryResponse

        mock_impl.return_value = GetMediaBuyDeliveryResponse(
            media_buy_deliveries=[],
            aggregated_totals={"impressions": 0, "clicks": 0, "spend": 0.0, "media_buy_count": 0},
            currency="USD",
            reporting_period={"start": "2026-03-01T00:00:00Z", "end": "2026-03-31T00:00:00Z", "granularity": "daily"},
        )

        payload = _build_jsonrpc("get_media_buy_delivery", {"media_buy_ids": ["mb-1"]})
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()

        if "result" in body:
            data = _extract_artifact_data(body["result"])
            assert "media_buy_deliveries" in data or "deliveries" in data, (
                "get_media_buy_delivery response must have 'media_buy_deliveries' field"
            )

    @patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY)
    @patch("src.a2a_server.adcp_a2a_server.core_update_performance_index_tool")
    def test_update_performance_index_response_shape(self, mock_impl, mock_resolve, client, auth_headers):
        """update_performance_index response must have acknowledgment fields."""
        from src.core.schemas import UpdatePerformanceIndexResponse

        mock_impl.return_value = UpdatePerformanceIndexResponse(
            status="updated",
            detail="Performance index updated for mb-test-1",
        )

        payload = _build_jsonrpc(
            "update_performance_index",
            {"media_buy_id": "mb-test-1", "performance_data": [{"product_id": "p1", "performance_index": 1.2}]},
        )
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()

        if "result" in body:
            data = _extract_artifact_data(body["result"])
            assert "media_buy_id" in data or "status" in data, (
                "update_performance_index response must have 'media_buy_id' or 'status'"
            )


# ---------------------------------------------------------------------------
# Stub Handlers (approve_creative, get_media_buy_status, optimize_media_buy)
# ---------------------------------------------------------------------------


class TestA2AStubHandlers:
    """Unimplemented-but-registered skills surface UNSUPPORTED_FEATURE in the task body.

    Table-driven wire assertions across the dispatch registry: a recognized-but-
    unimplemented skill is an application-layer failure, so it must return a
    failed Task carrying a two-layer ``UNSUPPORTED_FEATURE``/``correctable``
    envelope — NOT a JSON-RPC ``UnsupportedOperationError`` (-32004). Reserving
    JSON-RPC for transport faults is the AdCP 3.1.0-beta.3 "Layer Separation"
    contract; these stubs are advertised on the agent card, so a buyer must get
    a structured, recoverable AdCP error rather than a transport exception.
    """

    # Every registered stub that terminates in AdCPCapabilityNotSupportedError,
    # mapped to params that reach that terminal branch. create_creative and
    # assign_creative validate required params first, so empty params would stop
    # at VALIDATION_ERROR — supply valid params so the wire assertion actually
    # exercises the unsupported branch (Finding: reach each stub's terminal branch).
    UNSUPPORTED_SKILLS = {
        "approve_creative": {},
        "get_media_buy_status": {},
        "optimize_media_buy": {},
        "create_creative": {"format_id": "display_300x250", "content_uri": "https://ex/c.jpg", "name": "c"},
        "assign_creative": {"media_buy_id": "mb-1", "package_id": "pkg-1", "creative_id": "cr-1"},
    }

    @pytest.mark.parametrize("skill", sorted(UNSUPPORTED_SKILLS))
    @patch("src.core.resolved_identity.resolve_identity", return_value=_TEST_IDENTITY)
    def test_unsupported_skill_returns_failed_task_not_transport_error(self, mock_resolve, client, auth_headers, skill):
        """Each unimplemented skill returns a failed Task with UNSUPPORTED_FEATURE, not JSON-RPC."""
        payload = _build_jsonrpc(skill, self.UNSUPPORTED_SKILLS[skill])
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()

        assert "error" not in body, f"'{skill}' must not be a JSON-RPC error: {body.get('error')}"
        assert "result" in body, f"'{skill}' expected a failed-Task result, got: {json.dumps(body)[:400]}"
        data = _extract_artifact_data(body["result"])
        assert data.get("adcp_error", {}).get("code") == "UNSUPPORTED_FEATURE", (
            f"'{skill}' must surface UNSUPPORTED_FEATURE in the task body: {data}"
        )
        assert data.get("adcp_error", {}).get("recovery") == "correctable", f"'{skill}': {data}"


class TestA2ARegistryBijection:
    """The transport suite's skill inventory must equal the production registry."""

    def test_transport_inventory_matches_production_registry(self):
        """No registered skill may lack a wire test, and no test may name a ghost skill.

        ``ALL_SKILLS`` is derived from ``_skill_handler_map`` so dispatch tests cover
        every skill; this pins the inverse — every registered skill is classified as
        either an implemented dispatch target or a known UNSUPPORTED stub. A skill
        added to production without a home here fails the build (Finding: the oracle
        omitted 5 of 18 registered skills).
        """
        registry = set(AdCPRequestHandler()._skill_handler_map())
        unsupported = set(TestA2AStubHandlers.UNSUPPORTED_SKILLS)
        implemented = registry - unsupported

        assert set(ALL_SKILLS) == registry, "ALL_SKILLS drifted from the production registry"
        assert unsupported <= registry, f"UNSUPPORTED_SKILLS names non-registered skills: {unsupported - registry}"
        # Every registered skill is accounted for: implemented (dispatch-tested) or an unsupported stub.
        assert implemented | unsupported == registry


# ---------------------------------------------------------------------------
# All Skills Dispatch
# ---------------------------------------------------------------------------


class TestA2AAllSkillsDispatch:
    """Verify all 13 skills are reachable through the transport layer."""

    @pytest.mark.parametrize("skill", ALL_SKILLS)
    def test_skill_dispatches_not_404(self, client, auth_headers, skill):
        """Every registered skill must be dispatched (not 404 or method-not-found)."""
        payload = _build_jsonrpc(skill, {})
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()

        # Skill should be found (not method-not-found error)
        if "error" in body:
            error_msg = body["error"].get("message", "")
            assert "Unknown skill" not in error_msg, f"Skill '{skill}' not found in dispatch map: {error_msg}"

    @pytest.mark.parametrize("skill", ALL_SKILLS)
    def test_all_skills_return_valid_jsonrpc(self, client, auth_headers, skill):
        """Every skill must return valid JSON-RPC (result or error with code+message)."""
        payload = _build_jsonrpc(skill, {})
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()

        assert body.get("jsonrpc") == "2.0", f"Skill '{skill}' must return jsonrpc: '2.0'"
        assert "result" in body or "error" in body, f"Skill '{skill}' must return result or error"
        if "error" in body:
            assert "code" in body["error"], f"Error for '{skill}' must have 'code'"
            assert "message" in body["error"], f"Error for '{skill}' must have 'message'"


# ---------------------------------------------------------------------------
# Agent Card Contract
# ---------------------------------------------------------------------------


class TestAgentCardContract:
    """Verify agent card advertises all skills and has required structure."""

    def test_agent_card_skills_match_dispatch(self, client):
        """Agent card advertises every dispatchable skill except the deliberate stubs.

        Every registered skill must be advertised EXCEPT the unimplemented creative
        stubs in ``UNADVERTISED_SKILLS`` (the agent should not advertise capabilities
        it doesn't offer). Pinning the exact exclusion catches both a skill that
        silently stops being advertised and a stub that gets advertised by accident.
        """
        response = client.get("/.well-known/agent-card.json")
        card = response.json()
        advertised_skills = {s["name"] for s in card.get("skills", [])}

        expected = set(ALL_SKILLS) - UNADVERTISED_SKILLS
        missing = expected - advertised_skills
        assert not missing, f"dispatchable skills not advertised in agent card: {sorted(missing)}"
        wrongly_advertised = UNADVERTISED_SKILLS & advertised_skills
        assert not wrongly_advertised, f"unimplemented stubs should not be advertised: {sorted(wrongly_advertised)}"

    def test_agent_card_url_no_trailing_slash(self, client):
        """Agent card URL must not have trailing slash (causes redirects)."""
        response = client.get("/.well-known/agent-card.json")
        card = response.json()
        url = card.get("url", "")
        assert not url.endswith("/"), f"Agent card URL has trailing slash: {url}"

    def test_agent_card_has_adcp_extension(self, client):
        """Agent card must include AdCP extension in capabilities."""
        response = client.get("/.well-known/agent-card.json")
        card = response.json()
        extensions = card.get("capabilities", {}).get("extensions", [])
        adcp_uris = [e.get("uri", "") for e in extensions]
        assert any("adcp-extension" in uri for uri in adcp_uris), "Agent card must have AdCP extension in capabilities"
