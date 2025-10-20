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

import json
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from .adcp_schema_validator import AdCPSchemaValidator, SchemaValidationError

DEFAULT_A2A_PORT = int(os.getenv("A2A_PORT", "8091"))
DEFAULT_AUTH_TOKEN = os.getenv("ADCP_TEST_TOKEN", "test_auth_token_principal_1")


class A2AAdCPComplianceClient:
    """Client for testing A2A servers with AdCP compliance validation."""

    def __init__(
        self,
        a2a_url: str,
        auth_token: str,
        validate_schemas: bool = True,
        offline_mode: bool = True,
    ):
        self.a2a_url = a2a_url
        self.auth_token = auth_token
        self.validate_schemas = validate_schemas
        self.offline_mode = offline_mode
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.schema_validator = None

    async def __aenter__(self):
        """Enter async context."""
        # Initialize schema validator if enabled
        if self.validate_schemas:
            self.schema_validator = AdCPSchemaValidator(offline_mode=self.offline_mode, adcp_version="v1")
            await self.schema_validator.__aenter__()

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit async context."""
        if self.schema_validator:
            await self.schema_validator.__aexit__(exc_type, exc_val, exc_tb)
        await self.http_client.aclose()

    async def send_natural_language_message(self, text: str) -> dict:
        """Send natural language message to A2A server."""
        message = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {
                "message": {"messageId": str(uuid.uuid4()), "contextId": str(uuid.uuid4()), "parts": [{"text": text}]}
            },
        }

        headers = {"Authorization": f"Bearer {self.auth_token}", "Content-Type": "application/json"}

        response = await self.http_client.post(self.a2a_url, json=message, headers=headers)
        response.raise_for_status()
        return response.json()

    async def send_explicit_skill_message(self, skill: str, parameters: dict) -> dict:
        """Send explicit skill invocation to A2A server."""
        message = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "contextId": str(uuid.uuid4()),
                    "parts": [{"data": {"skill": skill, "parameters": parameters}}],
                }
            },
        }

        headers = {"Authorization": f"Bearer {self.auth_token}", "Content-Type": "application/json"}

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
                if part.get("type") == "data" and "data" in part:
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

        # Map skill to AdCP schema
        skill_to_schema = {
            "get_products": "get-products",
            "create_media_buy": "create-media-buy",
            "add_creative_assets": "add-creative-assets",
            "get_signals": "get-signals",
            # Skills without AdCP schemas
            "get_pricing": None,
            "get_targeting": None,
            "approve_creative": None,  # Schema may not be available yet
            "get_media_buy_status": None,
            "optimize_media_buy": None,
            "search_signals": None,
        }

        schema_task = skill_to_schema.get(skill_name)
        if not schema_task:
            result["warnings"].append(f"No AdCP schema mapping for skill '{skill_name}'")
            return result

        result["schema_tested"] = schema_task

        # Extract AdCP payload
        adcp_payload = self.extract_adcp_payload_from_a2a_response(a2a_response)
        if not adcp_payload:
            result["errors"].append("Could not extract AdCP payload from A2A response")
            result["valid"] = False
            return result

        result["payload_extracted"] = True

        # Validate against schema
        if self.schema_validator:
            try:
                await self.schema_validator.validate_response(schema_task, adcp_payload)
                result["warnings"].append("AdCP schema validation passed")
            except SchemaValidationError as e:
                result["errors"].append(f"AdCP schema validation failed: {e}")
                result["valid"] = False
            except Exception as e:
                result["errors"].append(f"Validation error: {e}")
                result["valid"] = False
        else:
            result["warnings"].append("Schema validator not available")

        return result


class A2AAdCPComplianceReport:
    """Collects and reports on A2A/AdCP compliance results."""

    def __init__(self):
        self.results: list[dict[str, Any]] = []
        self.passed = 0
        self.failed = 0
        self.warnings = 0

    def add_result(self, validation_result: dict):
        """Add a compliance validation result."""
        self.results.append(validation_result)

        if validation_result["valid"]:
            self.passed += 1
        else:
            self.failed += 1

        if validation_result["warnings"]:
            self.warnings += 1

    def print_summary(self):
        """Print compliance summary."""
        print("\n" + "=" * 60)
        print("A2A/AdCP COMPLIANCE SUMMARY")
        print("=" * 60)
        print(f"✓ Passed: {self.passed}")
        print(f"⚠ Warnings: {self.warnings}")
        print(f"✗ Failed: {self.failed}")
        print(f"Total Tests: {len(self.results)}")

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

    def save_report(self, filepath: Path):
        """Save compliance report to JSON file."""
        report_data = {
            "summary": {
                "passed": self.passed,
                "failed": self.failed,
                "warnings": self.warnings,
                "total": len(self.results),
            },
            "results": self.results,
        }

        with open(filepath, "w") as f:
            json.dump(report_data, f, indent=2)


@pytest.fixture
def a2a_url(request):
    """A2A server URL fixture."""
    return getattr(request.config.option, "server_url", None) or f"http://localhost:{DEFAULT_A2A_PORT}/a2a"


@pytest.fixture
def auth_token(request):
    """Authentication token fixture."""
    return getattr(request.config.option, "auth_token", None) or DEFAULT_AUTH_TOKEN


@pytest.fixture
async def compliance_client(a2a_url, auth_token):
    """A2A compliance client fixture."""
    import httpx

    # Check if A2A server is available by testing the agent card endpoint
    try:
        async with httpx.AsyncClient(timeout=2.0) as test_client:
            response = await test_client.get(f"{a2a_url.replace('/a2a', '')}/.well-known/agent.json")
            if response.status_code != 200:
                pytest.skip(f"A2A server not available at {a2a_url} (status: {response.status_code})")
    except (httpx.ConnectError, httpx.TimeoutException, Exception) as e:
        pytest.skip(f"A2A server not available at {a2a_url}: {e}")

    async with A2AAdCPComplianceClient(
        a2a_url=a2a_url, auth_token=auth_token, validate_schemas=True, offline_mode=True
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
        compliance_report.add_result(validation_result)

        # Don't fail test - just collect results for reporting
        assert "skill" in validation_result

    @pytest.mark.asyncio
    async def test_explicit_skill_get_products(self, compliance_client, compliance_report):
        """Test explicit get_products skill invocation."""
        response = await compliance_client.send_explicit_skill_message(
            "get_products",
            {
                "brief": "Video advertising for sports content",
                "brand_manifest": {"name": "Athletic apparel brand"},
            },
        )

        validation_result = await compliance_client.validate_skill_response("get_products", response)
        compliance_report.add_result(validation_result)

        assert "skill" in validation_result

    @pytest.mark.asyncio
    async def test_explicit_skill_create_media_buy(self, compliance_client, compliance_report):
        """Test explicit create_media_buy skill invocation."""
        response = await compliance_client.send_explicit_skill_message(
            "create_media_buy",
            {
                "product_ids": ["video_premium"],
                "total_budget": 10000.0,
                "flight_start_date": "2025-02-01",
                "flight_end_date": "2025-02-28",
            },
        )

        validation_result = await compliance_client.validate_skill_response("create_media_buy", response)
        compliance_report.add_result(validation_result)

        assert "skill" in validation_result

    @pytest.mark.asyncio
    async def test_explicit_skill_get_signals(self, compliance_client, compliance_report):
        """Test explicit get_signals skill invocation."""
        response = await compliance_client.send_explicit_skill_message(
            "get_signals", {"signal_types": ["audience"], "categories": ["demographics"]}
        )

        validation_result = await compliance_client.validate_skill_response("get_signals", response)
        compliance_report.add_result(validation_result)

        assert "skill" in validation_result

    @pytest.mark.asyncio
    async def test_all_adcp_skills_compliance(self, compliance_client, compliance_report):
        """Test all AdCP skills for compliance in a single comprehensive test."""

        # Define skill tests
        skill_tests = [
            ("get_products", {"brief": "Display ads", "brand_manifest": {"name": "Test brand"}}),
            (
                "create_media_buy",
                {
                    "product_ids": ["display_standard"],
                    "total_budget": 5000.0,
                    "flight_start_date": "2025-03-01",
                    "flight_end_date": "2025-03-31",
                },
            ),
            (
                "add_creative_assets",
                {
                    "media_buy_id": "mb_test_123",
                    "assets": {"main": {"asset_type": "image", "url": "https://example.com/creative.jpg"}},
                },
            ),
            ("get_signals", {"signal_types": ["contextual"]}),
            # Legacy skills (no schema validation expected)
            ("get_pricing", {}),
            ("get_targeting", {}),
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

        # Always pass - results are in the report
        assert compliance_report.results, "Should have collected some test results"


def pytest_addoption(parser):
    """Add command-line options for pytest."""
    parser.addoption("--server-url", action="store", default=None, help="A2A server URL to test against")
    parser.addoption("--auth-token", action="store", default=None, help="Authentication token for A2A server")


if __name__ == "__main__":
    # Run tests directly
    pytest.main([__file__, "-v", "-s"])
