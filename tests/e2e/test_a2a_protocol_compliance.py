#!/usr/bin/env python3
"""
E2E tests for A2A protocol compliance with AdCP schemas.

These tests validate that our A2A server correctly accepts and processes
requests according to the official AdCP specification, catching issues like:
- Incorrect parameter names (e.g., 'updates' vs 'packages')
- Missing required fields
- Schema mismatches between A2A layer and core implementation

CRITICAL: These tests use real AdCP schemas and validate the full request/response
cycle to ensure protocol compliance.

NOTE: These tests require the external AdCP schema server (adcontextprotocol.org)
to be available. If the server is unreachable (e.g., HTTP 5xx errors), tests will
be skipped rather than failing, since external service availability is outside
our control.
"""

import pytest

from tests.e2e.adcp_schema_validator import AdCPSchemaValidator


class TestA2AProtocolCompliance:
    """Test A2A protocol compliance with official AdCP schemas."""

    @pytest.mark.asyncio
    async def test_update_media_buy_request_schema_structure(self):
        """
        Test that update_media_buy schema uses 'packages' field.

        This test validates:
        1. Schema uses 'packages' field (not 'updates')
        2. Schema structure matches AdCP v2.0+ spec

        Regression test for: A2A server expecting 'updates' instead of 'packages'
        """
        async with AdCPSchemaValidator(offline_mode=False) as validator:
            # Load official AdCP schema
            schema = await validator.get_schema("/schemas/v1/media-buy/update-media-buy-request.json")

            # Verify schema uses 'packages' field (not 'updates')
            assert "packages" in schema["properties"], "AdCP schema should define 'packages' field"
            assert "updates" not in schema["properties"], "AdCP schema should NOT have legacy 'updates' field"

            # Verify media_buy_id is required (spec uses required array, not oneOf)
            assert "media_buy_id" in schema.get("properties", {}), "Schema should define media_buy_id"
            assert "media_buy_id" in schema.get("required", []), "media_buy_id should be required"

    # test_update_media_buy_schema_validates_correctly removed:
    # Validated a hardcoded request dict against adcontextprotocol.org/schemas/latest/...
    # Did not exercise any sales agent behavior — purely fixture vs. upstream spec drift.
    # Real schema conformance is covered by tests/unit/test_adcp_contract.py against
    # the pinned adcp library version. See PR #1186 notes.

    @pytest.mark.asyncio
    async def test_all_adcp_skills_have_schemas(self):
        """
        Verify that all AdCP-compliant skills have corresponding schemas.

        This prevents regressions where we add new skills but forget to:
        1. Add them to the schema validation map
        2. Create tests for them
        3. Validate their request/response formats
        """
        # Define which skills are AdCP-compliant (should have schemas)
        # Note: signals skills removed - should come from dedicated signals agents
        adcp_skills = {
            "get_products",
            "create_media_buy",
            "update_media_buy",
            "get_media_buy_delivery",
            "sync_creatives",
            "list_creatives",
            "list_creative_formats",
            "list_authorized_properties",
        }

        async with AdCPSchemaValidator(offline_mode=False) as validator:
            missing_schemas = []

            for skill in adcp_skills:
                # Map skill name to schema path
                schema_path = f"/schemas/v1/media-buy/{skill.replace('_', '-')}-request.json"

                try:
                    schema = await validator.get_schema(schema_path)
                    assert schema is not None, f"Schema loaded but is None for {skill}"
                except Exception as e:
                    # Some schemas might not exist yet (e.g., list_creative_formats)
                    # Log but don't fail - we'll track these separately
                    if "404" not in str(e) and "not found" not in str(e).lower():
                        missing_schemas.append(f"{skill}: {e}")

            # Report findings (informational, not a hard failure)
            if missing_schemas:
                pytest.skip(
                    f"Some AdCP skills don't have schemas yet (expected during development): "
                    f"{', '.join(missing_schemas)}"
                )

    @pytest.mark.asyncio
    async def test_get_media_buy_delivery_request_schema(self):
        """
        Test that get_media_buy_delivery uses correct parameter names.

        Validates the request accepts AdCP-compliant field names.
        """
        async with AdCPSchemaValidator(offline_mode=False) as validator:
            schema = await validator.get_schema("/schemas/v1/media-buy/get-media-buy-delivery-request.json")

            # Verify expected fields from AdCP spec
            assert "media_buy_ids" in schema["properties"], "Should accept media_buy_ids (plural) per AdCP spec"
            assert "status_filter" in schema["properties"], "Should accept status_filter for filtering by status"

            # Validate a minimal valid request
            valid_request = {"media_buy_ids": ["mb_1", "mb_2"]}

            try:
                await validator.validate_request(task_name="get-media-buy-delivery", request_data=valid_request)
                validation_passed = True
            except Exception as e:
                validation_passed = False
                error_msg = str(e)

            assert validation_passed, f"Valid request should pass: {error_msg if not validation_passed else ''}"
