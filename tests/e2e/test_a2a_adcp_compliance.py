#!/usr/bin/env python3
"""
Comprehensive A2A/AdCP Compliance Integration Test

This test validates that A2A skill invocations return AdCP-compliant data
that passes schema validation against the official AdCP specification.

Key features:
- Tests all A2A skill invocations against AdCP schemas
- Validates both natural language and explicit skill invocation patterns
- Provides detailed compliance reporting
- Can run against any A2A server implementation

Usage:
    pytest tests/e2e/test_a2a_adcp_compliance.py -v
    pytest tests/e2e/test_a2a_adcp_compliance.py --server-url=https://example.com/a2a
"""

import os

import httpx
import pytest

from tests.e2e._compliance_report import ComplianceReportBase
from tests.e2e.adcp_request_builder import build_a2a_message_send
from tests.helpers.a2a_adcp_validation import validate_a2a_skill_payload
from tests.helpers.adcp_schema_validator import AdCPSchemaValidator

from .conftest import e2e_host

DEFAULT_AUTH_TOKEN = os.getenv("ADCP_TEST_TOKEN", "ci-test-token")


class A2AAdCPComplianceClient:
    """Client for testing A2A servers with AdCP compliance validation."""

    def __init__(
        self,
        a2a_url: str,
        auth_token: str,
        tenant: str | None = None,
        validate_schemas: bool = True,
    ):
        self.a2a_url = a2a_url
        self.auth_token = auth_token
        self.tenant = tenant
        self.validate_schemas = validate_schemas
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.schema_validator = None

    async def __aenter__(self):
        """Enter async context."""
        # Initialize schema validator if enabled
        if self.validate_schemas:
            self.schema_validator = AdCPSchemaValidator()
            await self.schema_validator.__aenter__()

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit async context."""
        if self.schema_validator:
            await self.schema_validator.__aexit__(exc_type, exc_val, exc_tb)
        await self.http_client.aclose()

    async def send_natural_language_message(self, text: str) -> dict:
        """Send natural language message to A2A server."""
        message = build_a2a_message_send(text=text)

        headers = {"Authorization": f"Bearer {self.auth_token}", "Content-Type": "application/json"}
        if self.tenant:
            headers["x-adcp-tenant"] = self.tenant

        response = await self.http_client.post(self.a2a_url, json=message, headers=headers)
        response.raise_for_status()
        return response.json()

    async def send_explicit_skill_message(self, skill: str, parameters: dict) -> dict:
        """Send explicit skill invocation to A2A server."""
        message = build_a2a_message_send(skill=skill, parameters=parameters)

        headers = {"Authorization": f"Bearer {self.auth_token}", "Content-Type": "application/json"}
        if self.tenant:
            headers["x-adcp-tenant"] = self.tenant

        response = await self.http_client.post(self.a2a_url, json=message, headers=headers)
        response.raise_for_status()
        return response.json()

    def extract_adcp_payload_from_a2a_response(self, a2a_response: dict) -> dict | None:
        """Extract AdCP payload from A2A JSON-RPC response."""
        try:
            # A2A JSON-RPC response structure: {"result": {"artifacts": [...]}}
            result = a2a_response.get("result", {})
            artifacts = result.get("artifacts", [])

            if not artifacts:
                return None

            # Extract data from first artifact
            artifact = artifacts[0]
            parts = artifact.get("parts", [])

            for part in parts:
                if part.get("kind") == "data" and "data" in part:
                    return part["data"]

            return None

        except (KeyError, IndexError, TypeError):
            return None

    async def validate_skill_response(self, skill_name: str, a2a_response: dict) -> dict:
        """Validate A2A skill response against AdCP schemas."""
        result = {
            "skill": skill_name,
            "valid": True,
            "errors": [],
            "warnings": [],
            "schema_tested": None,
            "payload_extracted": False,
        }

        # Extraction is the only transport-specific step (JSON-RPC dict here,
        # protobuf artifact in the in-process validator); everything after it
        # is the shared helper, so the two cannot drift again.
        adcp_payload = self.extract_adcp_payload_from_a2a_response(a2a_response)
        if not adcp_payload:
            result["errors"].append("Could not extract AdCP payload from A2A response")
            result["valid"] = False
            return result

        result["payload_extracted"] = True

        if not self.schema_validator:
            result["warnings"].append("Schema validator not available")
            return result

        outcome = await validate_a2a_skill_payload(self.schema_validator, skill_name, adcp_payload)
        result["valid"] = outcome["valid"]
        result["errors"] = outcome["errors"]
        result["warnings"] = outcome["warnings"]
        result["schema_tested"] = outcome["schema_tested"]

        return result


class A2AAdCPComplianceReport(ComplianceReportBase):
    """Collects and reports on A2A/AdCP compliance results."""

    title = "A2A/AdCP COMPLIANCE SUMMARY"

    def add_result(self, validation_result: dict):
        """Add a compliance validation result."""
        self.results.append(validation_result)

        if validation_result["valid"]:
            self.passed += 1
        else:
            self.failed += 1

        if validation_result["warnings"]:
            self.warnings += 1

    def _print_details(self):
        print("\nDETAILED RESULTS:")
        for result in self.results:
            skill = result["skill"]
            valid = "✓" if result["valid"] else "✗"
            schema = result["schema_tested"] or "N/A"
            print(f"  {valid} {skill} (schema: {schema})")

            if result["errors"]:
                for error in result["errors"]:
                    print(f"    ERROR: {error}")

            if result["warnings"]:
                for warning in result["warnings"]:
                    print(f"    WARNING: {warning}")


@pytest.fixture
def a2a_url(request, docker_services_e2e):
    """A2A server URL fixture. Depends on docker_services_e2e to ensure CI init runs."""
    if custom_url := getattr(request.config.option, "server_url", None):
        return custom_url
    port = docker_services_e2e["a2a_port"]
    return f"http://{e2e_host()}:{port}/a2a"


@pytest.fixture
def auth_token(request):
    """Authentication token fixture."""
    return getattr(request.config.option, "auth_token", None) or DEFAULT_AUTH_TOKEN


@pytest.fixture
async def compliance_client(a2a_url, auth_token):
    """A2A compliance client fixture."""
    import httpx

    # Check if A2A server is available by testing the agent card endpoint.
    # src/app.py registers /.well-known/agent-card.json (hyphenated) — NOT
    # agent.json — so probing the wrong path always 404s and silently skips
    # every test in this module (#1838 review).
    try:
        async with httpx.AsyncClient(timeout=2.0) as test_client:
            response = await test_client.get(f"{a2a_url.replace('/a2a', '')}/.well-known/agent-card.json")
            if response.status_code != 200:
                pytest.skip(f"A2A server not available at {a2a_url} (status: {response.status_code})")
    # Exactly the two failures that mean 'no server is listening'. A bare
    # Exception used to sit in this tuple, so ANY instrument failure -- a bug in
    # the fixture, a bad URL, an import error -- became a green skip and the whole
    # module reported success while grading nothing.
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        pytest.skip(f"A2A server not available at {a2a_url}: {e}")

    async with A2AAdCPComplianceClient(
        a2a_url=a2a_url, auth_token=auth_token, tenant="ci-test", validate_schemas=True
    ) as client:
        yield client


@pytest.fixture
def compliance_report():
    """Compliance report collector fixture."""
    report = A2AAdCPComplianceReport()
    yield report
    report.print_summary()


class TestA2AAdCPCompliance:
    """Test suite for A2A/AdCP compliance validation."""

    @pytest.mark.asyncio
    async def test_natural_language_get_products(self, compliance_client, compliance_report):
        """Test natural language get_products invocation."""
        response = await compliance_client.send_natural_language_message(
            "What video advertising products do you have available?"
        )

        validation_result = await compliance_client.validate_skill_response("get_products", response)
        # Deliberately no assertion here: collect results for reporting only.
        # validate_skill_response sets "skill" on every return path, so
        # `assert "skill" in validation_result` was always-true dead weight
        # (#1868 review), not a real check.
        compliance_report.add_result(validation_result)

    @pytest.mark.asyncio
    async def test_explicit_skill_get_products(self, compliance_client, compliance_report):
        """Test explicit get_products skill invocation."""
        response = await compliance_client.send_explicit_skill_message(
            "get_products",
            {
                "brief": "Video advertising for sports content",
                "brand": {"domain": "testbrand.com"},
                "context": {"e2e": "get_products"},
            },
        )

        validation_result = await compliance_client.validate_skill_response("get_products", response)
        compliance_report.add_result(validation_result)

        assert "skill" in validation_result
        # Verify context echoed
        payload = compliance_client.extract_adcp_payload_from_a2a_response(response)
        assert payload and payload.get("context") == {"e2e": "get_products"}
        # Must be a real product listing, not an error envelope masquerading
        # as a payload (an AUTH_REQUIRED error also echoes context and would
        # otherwise pass this assertion silently — #1838 review).
        assert "adcp_error" not in payload, f"Expected products, got an error envelope: {payload}"
        assert "products" in payload

    @pytest.mark.asyncio
    async def test_explicit_skill_create_media_buy(self, compliance_client, compliance_report):
        """Test explicit create_media_buy skill invocation.

        Note: This sends intentionally incomplete parameters to test the server's
        error handling. The server should return a structured error response (not crash),
        which is valid AdCP behavior.
        """
        response = await compliance_client.send_explicit_skill_message(
            "create_media_buy",
            {
                "product_ids": ["video_premium"],
                "total_budget": 10000.0,
                "flight_start_date": "2025-02-01",
                "flight_end_date": "2025-02-28",
                "context": {"e2e": "create_media_buy"},
            },
        )

        validation_result = await compliance_client.validate_skill_response("create_media_buy", response)
        compliance_report.add_result(validation_result)

        # Verify response is structured (payload extracted), even if it's a validation error
        payload = compliance_client.extract_adcp_payload_from_a2a_response(response)
        assert payload is not None, "Server should return structured response, not crash"

    @pytest.mark.asyncio
    async def test_all_adcp_skills_compliance(self, compliance_client, compliance_report):
        """Test all AdCP skills for compliance in a single comprehensive test.

        Every entry here must be a request that genuinely SUCCEEDS against the
        real CI-seeded stack — the point is to exercise real schema-compliant
        responses, not error paths (those are covered by the dedicated
        test_explicit_skill_create_media_buy). create_media_buy and
        add_creative_assets were previously listed here with a legacy request
        shape (product_ids/total_budget/flight_start_date, and a skill name
        that no longer exists in the A2A dispatch table) that could never
        pass schema validation — masked entirely by the assertion below only
        checking that SOME results were collected (#1838 review).

        list_creatives was tried here too and pulled back out: its format_id
        serialized as a bare string over the A2A wire instead of the spec's
        {agent_url, id} object. Root-caused and fixed: the A2A success path used
        to reconstruct a typed response FROM the same dict about to be sent on
        the wire (purely to generate the human-readable text part), and
        Creative.validate_format_id's @model_validator(mode="before") mutated its
        input dict in place — pydantic-core hands list-item dicts to
        before-validators by reference, so this corrupted artifact_data itself,
        and _dict_to_value's json.dumps(default=str) fallback then silently
        stringified the resulting live FormatId object. Fixed on both sides: the
        validator (and 3 siblings with the same hazard) defensively copy their
        input, and the outbound round trip was deleted — the TextPart is now read
        from the payload's already-stamped ``message``.
        """
        # Note: signals skills removed - should come from dedicated signals agents
        skill_tests = [
            ("get_products", {"brief": "Display ads", "brand": {"domain": "testbrand.com"}}),
            ("list_creatives", {"limit": 10}),
        ]

        for skill_name, params in skill_tests:
            try:
                response = await compliance_client.send_explicit_skill_message(skill_name, params)
                validation_result = await compliance_client.validate_skill_response(skill_name, response)
                compliance_report.add_result(validation_result)

                print(f"Tested {skill_name}: {'✓' if validation_result['valid'] else '✗'}")

            except Exception as e:
                # Record failure
                error_result = {
                    "skill": skill_name,
                    "valid": False,
                    "errors": [f"Request failed: {e}"],
                    "warnings": [],
                    "schema_tested": None,
                    "payload_extracted": False,
                }
                compliance_report.add_result(error_result)
                print(f"Failed to test {skill_name}: {e}")

        assert compliance_report.failed == 0, (
            f"{compliance_report.failed} of {len(skill_tests)} skill(s) failed AdCP schema compliance: "
            f"{[r for r in compliance_report.results if not r['valid']]}"
        )


def pytest_addoption(parser):
    """Add command-line options for pytest."""
    parser.addoption("--server-url", action="store", default=None, help="A2A server URL to test against")
    parser.addoption("--auth-token", action="store", default=None, help="Authentication token for A2A server")


if __name__ == "__main__":
    # Run tests directly
    pytest.main([__file__, "-v", "-s"])
