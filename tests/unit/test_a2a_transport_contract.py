"""A2A Transport Contract Tests — Phase 0 regression gate for handler migration.

These tests verify the HTTP boundary shape for all A2A skills:
- Route existence (not 404)
- Auth contract (discovery vs auth-required)
- JSON-RPC protocol correctness
- Response field presence (shape, not values)

They use TestClient (in-process ASGI) with mocked _impl functions.
No Docker required. This is the regression gate between every Phase 2 step.

"""

import json
import uuid

import pytest
from starlette.testclient import TestClient

from src.app import app
from src.core.tools.registry import TOOLS
from tests.factories.principal import PrincipalFactory
from tests.helpers.credentials import credential_headers

# ``protocol="a2a"`` and the redundant ``tenant={...}`` are gone: the identity names no
# transport since commit a1b79d22d took the testing-hook channel and the protocol off it,
# and the factory builds the TenantContext for ``tenant_id`` itself (a dict is refused at
# construction). What the A2A cases need from the identity is only that it IS an
# authenticated caller, which is what ``make_identity`` means.
_MOCK_IDENTITY = PrincipalFactory.make_identity(
    principal_id="test-principal",
    tenant_id="test-tenant",
)


# ---------------------------------------------------------------------------
# The A2A skills, read from the server rather than copied.
#
# This was a hand-maintained list, and it was the THIRD copy of the same set -- the
# dispatch map and the agent card being the other two. Deleting the non-spec skills from
# those two left this one stale, which is the whole failure mode: a copy does not know it
# is out of date. The agent card is the artifact a buyer actually reads, so it is the one
# worth reading here.
# ---------------------------------------------------------------------------
def _advertised_skills() -> list[str]:
    # _derived_skills, not the rendered card: the card needs a resolved tenant now,
    # and the skills never did -- they are derived from TOOLS for every tenant alike.
    from src.a2a_server.adcp_a2a_server import _derived_skills

    return [s.name for s in _derived_skills()]


ALL_SKILLS = _advertised_skills()

# Derived, not hand-kept. ``ToolSpec.requires_credential()`` is the one place a tool says
# whether it needs a caller, and the boundary asks the same method -- so a list written here
# could only ever agree with the gate by coincidence, and this one did not: it claimed
# ``list_accounts`` was auth-optional, while tests/integration/test_list_accounts.py graded
# the opposite behaviour ("unauthenticated list_accounts raises AUTH_REQUIRED") from the same
# BR-RULE-055. The pin settles it -- account/list-accounts-request.json describes "accounts
# accessible to the authenticated agent" -- and the registry agreed all along.
#
# The ``auth="required"|"optional"`` literal this read is gone (commit 4a57d38be): the policy
# is DERIVED from the implementation's identity annotation, and the row carries no second
# statement of it. Asked with no tenant, the answer is the annotation's alone -- which is the
# right question here, because these cases present no seller-specific brand policy.
DISCOVERY_SKILLS = [s for s in ALL_SKILLS if not TOOLS[s].requires_credential()]

AUTH_REQUIRED_SKILLS = [s for s in ALL_SKILLS if TOOLS[s].requires_credential()]

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
    """TestClient for the unified FastAPI app.

    No card precondition: the agent-card tests that needed one are gone. A card now
    describes a resolved tenant, which a unit test has no database to provide, so the
    card is graded where a tenant exists -- the BDD lane and the live e2e paths -- and
    what is left here is A2A's JSON-RPC framing, which needs no tenant at all.
    """
    c = TestClient(app, raise_server_exceptions=False)
    yield c
    c.close()


@pytest.fixture
def auth_headers():
    """Headers with a valid Bearer token."""
    return {
        **credential_headers(token="test-transport-token"),
        "Content-Type": "application/json",
        "A2A-Version": "1.0",
    }


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


# ---------------------------------------------------------------------------
# Auth Contract
# ---------------------------------------------------------------------------


# (Deleted) TestA2AAuthContract asserted "discovery skills accept no auth, auth-required
# skills reject no auth" over A2A alone, from a unit test. BDD grades that same contract on
# the wire across mcp/a2a/rest -- AUTH_MISSING appears in 18 feature files -- so this was one
# transport's copy of a three-transport obligation, and the copy is what lets a transport
# drift. Credential handling is not a unit test's subject.


class TestA2AJsonRpcProtocol:
    """Verify JSON-RPC protocol compliance."""

    def test_invalid_method_returns_error(self, client, auth_headers):
        """Unknown JSON-RPC method should return method-not-found error."""
        payload = {"jsonrpc": "2.0", "id": "test-1", "method": "nonexistent/method", "params": {}}
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()
        assert "error" in body, "Unknown method should return JSON-RPC error"

    def test_unknown_skill_returns_error(self, client, auth_headers):
        """Unknown skill name should return error (not crash)."""
        payload = _build_jsonrpc("nonexistent_skill", {})
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()
        assert "error" in body, "Unknown skill should return JSON-RPC error"

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


class TestA2AStubHandlers:
    """Verify stub handlers return JSON-RPC responses (not crashes)."""

    STUB_SKILLS = ["approve_creative", "get_media_buy_status", "optimize_media_buy"]

    @pytest.mark.parametrize("skill", STUB_SKILLS)
    def test_stub_handler_returns_jsonrpc_response(self, client, auth_headers, skill):
        """Stub handlers must return valid JSON-RPC (result or error), not crash."""
        payload = _build_jsonrpc(skill, {})
        response = client.post("/a2a", json=payload, headers=auth_headers)
        body = response.json()

        assert "result" in body or "error" in body, f"Stub skill '{skill}' must return JSON-RPC result or error"
        assert body.get("jsonrpc") == "2.0"


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
