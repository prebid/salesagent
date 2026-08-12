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

from src.a2a_server.adcp_a2a_server import create_agent_card
from tests.helpers.adcp_schema_validator import AdCPSchemaValidator


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
        async with AdCPSchemaValidator() as validator:
            # Load official AdCP schema
            schema = await validator.get_schema("media-buy/update-media-buy-request.json")

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

    # Skills this agent ships for which the pinned index has no task at all.
    # Shrink-only: when the spec adds a schema for one of these, remove it
    # here — never add an entry. A newly-added skill with no schema is a real
    # failure, not something to allowlist.
    _KNOWN_MISSING_SCHEMA_SKILLS = frozenset(
        {
            "approve_creative",
            "get_media_buy_status",
            "optimize_media_buy",
            "list_authorized_properties",
            "update_performance_index",
        }
    )

    @pytest.mark.asyncio
    async def test_all_adcp_skills_have_schemas(self):
        """
        Every skill this agent advertises resolves to a pinned request AND response schema.

        The roster comes from ``create_agent_card().skills`` — production's own
        and only skill declaration — so a skill added there is graded the same
        day with no test edit. The previous version iterated a hand-maintained
        test-side map, which had already gone stale in both directions: it
        listed a skill production had deleted and omitted five it ships, four
        of which have resolvable schemas that nothing was checking.

        The task name is derived (``_`` -> ``-``) at use rather than stored.
        Uses ``_find_schema_ref_for_task`` (searches every index section)
        rather than a hardcoded 'media-buy/' path, so a skill whose schema
        lives elsewhere (e.g. sync_creatives, under creative/) is found rather
        than silently treated as missing.
        """
        async with AdCPSchemaValidator() as validator:
            missing_schemas = []
            newly_resolved = []

            for skill in sorted(s.id for s in create_agent_card().skills):
                task_name = skill.replace("_", "-")
                refs = {
                    direction: await validator._find_schema_ref_for_task(task_name, direction)
                    for direction in ("request", "response")
                }

                if skill in self._KNOWN_MISSING_SCHEMA_SKILLS:
                    if any(ref is not None for ref in refs.values()):
                        newly_resolved.append(skill)
                    continue

                for direction, ref in refs.items():
                    if ref is None:
                        missing_schemas.append(f"{skill} ({direction})")
                    else:
                        assert await validator.get_schema(ref) is not None, (
                            f"{direction} schema resolved but failed to load for {skill}"
                        )

            assert not missing_schemas, (
                f"Advertised skill(s) have no schema anywhere in the pinned index: {missing_schemas}"
            )
            assert not newly_resolved, (
                f"Skill(s) in _KNOWN_MISSING_SCHEMA_SKILLS now HAVE a schema — shrink the allowlist: {newly_resolved}"
            )

    @pytest.mark.asyncio
    async def test_get_media_buy_delivery_request_schema(self):
        """
        Test that get_media_buy_delivery uses correct parameter names.

        Validates the request accepts AdCP-compliant field names.
        """
        async with AdCPSchemaValidator() as validator:
            schema = await validator.get_schema("media-buy/get-media-buy-delivery-request.json")

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
