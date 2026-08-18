#!/usr/bin/env python3
"""
Test A2A skill invocation patterns from AdCP PR #48.

Tests both natural language and explicit skill invocation patterns
to ensure our A2A server properly handles the evolving AdCP spec.
"""

import logging
import uuid
from unittest.mock import MagicMock, patch

import pytest
from a2a.types import Message, Part, Role, SendMessageRequest, Task, TaskState
from adcp.types import AccountReference as LibraryAccountReference

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from tests.factories.creative_asset import build_assets, image_spec
from tests.helpers.a2a_adcp_validation import validate_a2a_skill_payload
from tests.utils.a2a_helpers import (
    assert_delivery_forwarded_account,
    create_a2a_message_with_skill,
    create_a2a_text_message,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Import schema validation components
try:
    from tests.helpers.adcp_schema_validator import AdCPSchemaValidator, SchemaValidationError

    SCHEMA_VALIDATION_AVAILABLE = True
except ImportError:
    SCHEMA_VALIDATION_AVAILABLE = False
    AdCPSchemaValidator: type[AdCPSchemaValidator] | None = None  # type: ignore[no-redef]
    SchemaValidationError: type[SchemaValidationError] | None = None  # type: ignore[no-redef]

# Configure logging for tests
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class A2AAdCPValidator:
    """Helper class to validate A2A responses against AdCP schemas."""

    def __init__(self):
        self.validator = None
        if SCHEMA_VALIDATION_AVAILABLE:
            self.validator = AdCPSchemaValidator()

    async def __aenter__(self):
        if self.validator:
            await self.validator.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.validator:
            await self.validator.__aexit__(exc_type, exc_val, exc_tb)

    def extract_adcp_payload_from_a2a_artifact(self, artifact) -> dict:
        """Extract AdCP payload from A2A artifact structure.

        In a2a-sdk 1.0, Part.data is a protobuf Value, not a plain dict.
        """
        from tests.utils.a2a_helpers import extract_data_from_artifact

        return extract_data_from_artifact(artifact)

    async def validate_a2a_skill_response(
        self, skill_name: str, task_result: Task
    ) -> dict[str, bool | list[str] | str | None]:
        """
        Validate A2A skill response against AdCP schemas.

        Args:
            skill_name: The A2A skill name (e.g., "get_products")
            task_result: The A2A Task result containing artifacts

        Returns:
            Dict with validation results: {"valid": bool, "errors": list[str], "warnings": list[str], "schema_tested": str | None}
        """
        # Initialize with properly typed lists
        errors: list[str] = []
        warnings: list[str] = []
        result: dict[str, bool | list[str] | str | None] = {
            "valid": True,
            "errors": errors,
            "warnings": warnings,
            "schema_tested": None,
        }

        # Check if schema validation is available
        if not SCHEMA_VALIDATION_AVAILABLE or not self.validator:
            warnings.append("Schema validation not available - skipping")
            return result

        # Extract AdCP payload from A2A artifacts
        if not task_result.artifacts:
            errors.append("No artifacts found in A2A task result")
            result["valid"] = False
            return result

        # Validate each artifact (skills can return multiple artifacts).
        # Extraction is transport-specific and stays here; everything after it
        # is the shared helper, so this and the e2e client cannot drift.
        for i, artifact in enumerate(task_result.artifacts):
            adcp_payload = self.extract_adcp_payload_from_a2a_artifact(artifact)
            if not adcp_payload:
                warnings.append(f"Artifact {i}: No AdCP payload found")
                continue

            outcome = await validate_a2a_skill_payload(self.validator, skill_name, adcp_payload)
            result["schema_tested"] = outcome["schema_tested"]
            errors.extend(f"Artifact {i}: {msg}" for msg in outcome["errors"])
            warnings.extend(f"Artifact {i}: {msg}" for msg in outcome["warnings"])
            if not outcome["valid"]:
                result["valid"] = False

        return result

    def assert_schema_valid(self, validation_result: dict, skill_name: str) -> None:
        """Print validation errors/warnings, then assert the response is schema-valid."""
        if validation_result["errors"]:
            print(f"Schema validation errors: {validation_result['errors']}")
        if validation_result["warnings"]:
            print(f"Schema validation warnings: {validation_result['warnings']}")
        assert validation_result["valid"] is True, (
            f"{skill_name} response should be schema-valid but wasn't: {validation_result['errors']}"
        )


@pytest.mark.requires_db
class TestA2ASkillInvocation:
    """Test both natural language and explicit skill invocation patterns."""

    @pytest.fixture
    def handler(self):
        """Create an AdCP request handler for testing."""
        return AdCPRequestHandler()

    @pytest.fixture
    async def validator(self):
        """Create an A2A/AdCP validator for testing."""
        async with A2AAdCPValidator() as v:
            yield v

    @pytest.fixture
    def mock_auth_token(self):
        """Mock authentication token for testing."""
        return "test_bearer_token_123"

    def create_message_hybrid(self, text: str, skill: str, parameters: dict) -> Message:
        """Create a message with both text and skill invocation."""
        from tests.utils.a2a_helpers import _dict_to_value

        msg = Message(
            message_id="msg_789",
            context_id="ctx_789",
            role=Role.ROLE_USER,
        )
        msg.parts.append(Part(text=text))
        msg.parts.append(Part(data=_dict_to_value({"skill": skill, "parameters": parameters})))
        return msg

    @pytest.mark.asyncio
    async def test_natural_language_get_products(
        self, handler, sample_tenant, sample_principal, sample_products, mock_identity, validator
    ):
        """Test natural language invocation for get_products with AdCP schema validation."""
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create natural language message
            message = create_a2a_text_message("What video products do you have available?")
            params = SendMessageRequest(message=message)

            # Process the message - this will execute the real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify the result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "natural_language"
            assert result.artifacts is not None
            assert len(result.artifacts) == 1
            assert result.artifacts[0].name == "product_catalog"

            # Extract products from response
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "products" in artifact_data
            products = artifact_data["products"]

            # Verify we got products from database (should match non_guaranteed_video)
            assert len(products) > 0

            # Validate against AdCP schemas
            validation_result = await validator.validate_a2a_skill_response("get_products", result)
            print(f"Natural language get_products validation: {validation_result}")

            validator.assert_schema_valid(validation_result, "get_products")

    @pytest.mark.asyncio
    async def test_explicit_skill_get_products(
        self, handler, sample_tenant, sample_principal, sample_products, mock_identity, validator
    ):
        """Test explicit skill invocation for get_products with AdCP schema validation."""
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create explicit skill invocation message
            skill_params = {
                "brief": "Display advertising for news content",
                "brand": {"domain": "testbrand.com"},
            }
            message = create_a2a_message_with_skill("get_products", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message - this will execute the real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify the result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "get_products" in result.metadata["skills_requested"]
            assert result.artifacts is not None
            assert len(result.artifacts) == 1
            assert result.artifacts[0].name == "get_products_result"

            # Extract products from response
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "products" in artifact_data
            products = artifact_data["products"]

            # Verify we got products from database (should match display product)
            assert len(products) > 0

            # Validate against AdCP schemas
            validation_result = await validator.validate_a2a_skill_response("get_products", result)
            print(f"Explicit skill get_products validation: {validation_result}")

            validator.assert_schema_valid(validation_result, "get_products")

    @pytest.mark.asyncio
    async def test_explicit_skill_get_products_a2a_spec(
        self, handler, sample_tenant, sample_principal, sample_products, mock_identity, validator
    ):
        """Test explicit skill invocation using A2A spec 'input' field instead of 'parameters'."""
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create explicit skill invocation message using A2A spec 'input' field
            skill_params = {
                "brief": "Premium coffee brands",
                "brand": {"domain": "testbrand.com"},
            }
            message = create_a2a_message_with_skill("get_products", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message - this will execute the real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify the result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "get_products" in result.metadata["skills_requested"]
            assert result.artifacts is not None
            assert len(result.artifacts) == 1
            assert result.artifacts[0].name == "get_products_result"

            # Extract products from response
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "products" in artifact_data
            products = artifact_data["products"]

            # Verify we got products from database
            assert len(products) > 0

            # Validate against AdCP schemas
            validation_result = await validator.validate_a2a_skill_response("get_products", result)
            print(f"A2A spec 'input' field get_products validation: {validation_result}")

            validator.assert_schema_valid(validation_result, "get_products")

    @pytest.mark.asyncio
    async def test_explicit_skill_create_media_buy(
        self, handler, sample_tenant, sample_principal, sample_products, mock_identity, validator
    ):
        """Test explicit skill invocation for create_media_buy.

        NOTE: This test now uses the REAL mock adapter and code paths,
        only mocking authentication. This ensures we catch serialization bugs.
        """
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create explicit skill invocation message using AdCP spec format
            from datetime import UTC, datetime, timedelta

            start_date = datetime.now(UTC) + timedelta(days=1)
            end_date = start_date + timedelta(days=30)

            skill_params = {
                "brand": {"domain": "testbrand.com"},
                "idempotency_key": f"int-key-{uuid.uuid4().hex}",
                "packages": [
                    {
                        "product_id": sample_products[0],  # Use product_id per AdCP spec
                        "budget": 10000.0,  # Float only per AdCP v2.2.0, currency from pricing_option
                        "pricing_option_id": "cpm_usd_fixed",  # Required in adcp 2.5.0
                    }
                ],
                "start_time": start_date.isoformat(),
                "end_time": end_date.isoformat(),
            }
            message = create_a2a_message_with_skill("create_media_buy", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message - executes REAL _create_media_buy_impl with mock adapter
            result = await handler.on_message_send(params, context=ctx)

            # Verify the result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "create_media_buy" in result.metadata["skills_requested"]
            assert result.artifacts is not None
            assert len(result.artifacts) == 1
            assert result.artifacts[0].name == "create_media_buy_result"

            # Extract response data
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            # Per AdCP spec, CreateMediaBuyResponse has media_buy_id, packages, etc.
            # No 'success' field in the spec - that's a protocol-level field
            assert "media_buy_id" in artifact_data

            # Verify packages are properly serialized (this would have caught the bug!)
            assert "packages" in artifact_data
            assert isinstance(artifact_data["packages"], list)

    @pytest.mark.asyncio
    async def test_explicit_skill_create_media_buy_manual_approval(
        self, handler, sample_tenant, sample_principal, sample_products, mock_identity, validator
    ):
        """Test create_media_buy returns status=submitted when manual approval required."""
        # Update tenant to require manual approval
        from src.core.database.database_session import get_db_session
        from src.core.database.models import Tenant

        with get_db_session() as session:
            tenant = session.get(Tenant, sample_tenant["tenant_id"])
            tenant.human_review_required = True
            session.commit()

        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock identity resolution
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create explicit skill invocation message
            from datetime import UTC, datetime, timedelta

            start_date = datetime.now(UTC) + timedelta(days=1)
            end_date = start_date + timedelta(days=30)

            skill_params = {
                "brand": {"domain": "testbrand.com"},
                "idempotency_key": f"int-key-{uuid.uuid4().hex}",
                "packages": [
                    {
                        "product_id": sample_products[0],
                        "budget": 10000.0,
                        "pricing_option_id": "cpm_usd_fixed",
                    }
                ],
                "start_time": start_date.isoformat(),
                "end_time": end_date.isoformat(),
            }
            message = create_a2a_message_with_skill("create_media_buy", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message
            result = await handler.on_message_send(params, context=ctx)

            # Verify the result has status=submitted (manual approval required)
            assert isinstance(result, Task)
            assert result.status.state == TaskState.TASK_STATE_SUBMITTED
            # Per A2A spec, tasks requiring approval should not have artifacts until approved
            # (protobuf uses empty repeated field [] instead of None)
            assert not result.artifacts

    @pytest.mark.asyncio
    async def test_hybrid_invocation(
        self, handler, sample_tenant, sample_principal, mock_identity, sample_products, validator
    ):
        """Test hybrid invocation with both text and skill."""
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create hybrid message (text + explicit skill)
            skill_params = {"brief": "Sports video advertising", "brand": {"domain": "testbrand.com"}}
            message = self.create_message_hybrid(
                "I need video products for sports content", "get_products", skill_params
            )
            params = SendMessageRequest(message=message)

            # Process the message - this will execute the real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify explicit skill took precedence
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "get_products" in result.metadata["skills_requested"]
            assert "video products for sports" in result.metadata["request_text"]

            # Extract products from response
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "products" in artifact_data
            products = artifact_data["products"]

            # Verify we got products from database
            assert len(products) > 0

    # TODO: Add test_unknown_skill_error once we understand how A2A server handles unknown skills
    # TODO: Needs investigation of proper error handling approach (A2AError not in current a2a library)

    @pytest.mark.asyncio
    async def test_multiple_skill_invocations(
        self, handler, sample_tenant, sample_principal, mock_identity, sample_products
    ):
        """Test multiple skill invocations in a single message."""
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create message with multiple skill invocations
            # Note: get_signals removed - should come from dedicated signals agents
            from tests.utils.a2a_helpers import _dict_to_value

            message = Message(
                message_id="msg_multi",
                context_id="ctx_multi",
                role=Role.ROLE_USER,
            )
            message.parts.append(
                Part(
                    data=_dict_to_value(
                        {
                            "skill": "get_products",
                            "parameters": {"brief": "video ads", "brand": {"domain": "testbrand.com"}},
                        }
                    )
                )
            )
            message.parts.append(
                Part(
                    data=_dict_to_value(
                        {
                            "skill": "list_creative_formats",
                            "parameters": {},
                        }
                    )
                )
            )
            params = SendMessageRequest(message=message)

            # Process the message - this will execute the real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify both skills were processed
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert len(result.metadata["skills_requested"]) == 2
            assert "get_products" in result.metadata["skills_requested"]
            assert "list_creative_formats" in result.metadata["skills_requested"]
            assert len(result.artifacts) == 2

            # Verify both artifacts have data (parts may have TextPart before DataPart)
            for artifact in result.artifacts:
                data_part_found = False
                for part in artifact.parts:
                    # a2a-sdk 1.0 protobuf: Part uses oneof 'content' with text/raw/url/data
                    if part.HasField("data"):
                        data_part_found = True
                        break
                assert data_part_found, "Expected DataPart in artifact.parts"

    @pytest.mark.asyncio
    async def test_artifact_text_part_is_the_data_part_message(
        self, handler, sample_tenant, sample_principal, sample_products, mock_identity
    ):
        """The artifact's TextPart carries exactly the DataPart's ``message``, verbatim.

        The human-readable text is READ from the payload, not re-derived from
        it: ``_stamp_a2a_protocol_fields`` stamps ``str(response)`` onto
        ``message`` at serialization time, and ``on_message_send`` copies that
        string into the TextPart. Equality is the whole contract — a future
        change that rebuilds a response model from the outbound dict to call
        ``__str__()`` again would hand pydantic before-validators a reference
        to the dict about to go on the wire (the mechanism behind the
        list_creatives format_id bare-string defect), and any drift between
        the two parts would show up here first.
        """
        from tests.utils.a2a_helpers import extract_data_from_artifact

        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity):
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})
            message = create_a2a_message_with_skill(
                "get_products", {"brief": "video ads", "brand": {"domain": "testbrand.com"}}
            )
            result = await handler.on_message_send(SendMessageRequest(message=message), context=ctx)

        assert isinstance(result, Task)
        assert len(result.artifacts) == 1, "get_products produces exactly one artifact"
        artifact = result.artifacts[0]

        text_parts = [p.text for p in artifact.parts if p.HasField("text")]
        assert len(text_parts) == 1, f"expected exactly one TextPart, got {len(text_parts)}"

        data = extract_data_from_artifact(artifact)
        assert data["message"], "the DataPart must carry a non-empty stamped message"
        assert text_parts[0] == data["message"], (
            "the TextPart must be the stamped message verbatim, not a value re-derived "
            f"from the payload — TextPart={text_parts[0]!r} DataPart.message={data['message']!r}"
        )

    # TODO: Add test_missing_authentication once we understand how A2A server handles auth errors
    # TODO: Needs investigation of proper error handling approach (A2AError not in current a2a library)

    # test_adcp_schema_validation_integration removed (#1838 review): it built a
    # hand-rolled mock A2A Task/Artifact instead of exercising the real production path
    # — pure mocking in a file whose whole point is DB-backed integration coverage — and
    # its "assert valid or errors or warnings" could never fail regardless of outcome.
    # test_natural_language_get_products and test_explicit_skill_get_products below
    # already cover skill->schema resolution + validator invocation through the real
    # handler and a real database-backed product, which is strictly better coverage of
    # the same concept. (#1838 review: their assertions were non-vacuous
    # placeholders here too; production's get_products response is now AdCP
    # schema-valid, so `assert validation_result["valid"] is True` below actually
    # grades the response — not just "or errors or warnings".)

    def test_skill_handler_mapping(self, handler):
        """Test that all advertised skills have handlers."""
        # Get skills from agent card
        from src.a2a_server.adcp_a2a_server import create_agent_card

        agent_card = create_agent_card()

        # Verify all skills have handlers
        expected_skills = {skill.name for skill in agent_card.skills}

        # Test that _handle_explicit_skill can handle all advertised skills
        for skill_name in expected_skills:
            # This should not raise an exception for any advertised skill
            try:
                # We can't easily test the actual execution without full setup,
                # but we can at least verify the skill name is recognized
                assert skill_name in [
                    "get_adcp_capabilities",  # AdCP v3 discovery endpoint
                    "get_products",
                    "create_media_buy",
                    "update_media_buy",  # Added for media buy management
                    "get_media_buy_delivery",  # Added for delivery metrics
                    "get_creative_delivery",  # Added for creative-level delivery metrics
                    "update_performance_index",  # Added for performance optimization
                    "sync_creatives",
                    "list_creatives",
                    "approve_creative",
                    "get_media_buy_status",
                    "optimize_media_buy",
                    "list_creative_formats",  # Keep existing creative format endpoint
                    "list_authorized_properties",  # Added for AdCP compliance
                    "get_media_buys",
                    "list_accounts",  # Added for account management (UC-011)
                    "sync_accounts",  # Added for account sync (UC-011)
                ], f"Skill {skill_name} not in expected skill list"
            except Exception as e:
                pytest.fail(f"Skill {skill_name} should be handled but caused error: {e}")

    # Phase 2: Tests for previously untested skills

    @pytest.mark.asyncio
    async def test_update_media_buy_skill(
        self, handler, sample_tenant, sample_principal, mock_identity, sample_products, validator
    ):
        """Test update_media_buy skill invocation."""
        # Create a media buy in database first
        from datetime import UTC, datetime, timedelta

        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy

        start_date = datetime.now(UTC) + timedelta(days=1)
        end_date = start_date + timedelta(days=30)

        with get_db_session() as session:
            media_buy = MediaBuy(
                media_buy_id="mb_test_123",
                tenant_id=sample_tenant["tenant_id"],
                principal_id=sample_principal["principal_id"],
                status="active",
                order_name="Test Campaign",
                advertiser_name="Test Brand",
                start_date=start_date.date(),
                end_date=end_date.date(),
                start_time=start_date,  # Add start_time for flight days calculation
                end_time=end_date,  # Add end_time for flight days calculation
                budget=10000.0,
                currency="USD",
                raw_request={"brand": {"domain": "testbrand.com"}, "packages": []},
            )
            session.add(media_buy)
            session.commit()

        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock identity resolution and adapter
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
            patch("src.core.helpers.adapter_helpers.get_adapter") as mock_get_adapter,
        ):
            # Mock request headers to provide Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Mock adapter — must return what real adapters return: our defaulted
            # UpdateMediaBuySuccess subclass. adcp 6.6 (spec 3.1.1) made status/revision
            # required on the raw library UpdateMediaBuySuccessResponse; every production
            # construction site (mock/GAM/kevel adapters, the _impl, and the A2A server
            # coercion) routes through this subclass which defaults status='completed'
            # and revision, so the wire response is always spec-valid.
            from src.core.schemas import UpdateMediaBuySuccess

            mock_adapter = MagicMock()
            mock_adapter.update_media_buy.return_value = UpdateMediaBuySuccess(
                media_buy_id="mb_test_123",
                affected_packages=[],  # adcp 2.5.0 field (replaces packages/errors)
            )
            mock_get_adapter.return_value = mock_adapter

            # Create skill invocation
            # Per AdCP spec, budget is a float, not a Budget object in update_media_buy
            skill_params = {
                "media_buy_id": "mb_test_123",
                "budget": 15000.0,  # Float per AdCP spec, not Budget object
            }
            message = create_a2a_message_with_skill("update_media_buy", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message
            result = await handler.on_message_send(params, context=ctx)

            # Verify the skill was invoked
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "update_media_buy" in result.metadata["skills_requested"]

            # adcp 6.6 (spec 3.1.1) guard: the A2A update_media_buy wire response must
            # carry the now-required status/revision fields. This proves the defaulted
            # UpdateMediaBuySuccess subclass reaches the wire (not the raw library type),
            # which is the whole point of du92.
            # Value-presence guard: status/revision must reach the wire. (Numbers are
            # doubles on the A2A protobuf-Struct transport, so 1 arrives as 1.0 — a
            # transport-wide representation detail, not du92's concern; assert on value.)
            assert result.artifacts, "update_media_buy skill returned no artifacts"
            payload = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert payload["status"] == "completed", f"missing/incorrect status on wire: {payload!r}"
            assert payload["revision"] == 1, f"missing/incorrect revision on wire: {payload!r}"

    @pytest.mark.asyncio
    async def test_list_creative_formats_skill(
        self, handler, sample_tenant, sample_principal, sample_products, mock_identity, validator
    ):
        """Test list_creative_formats skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {"brief": "display formats"}
            message = create_a2a_message_with_skill("list_creative_formats", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "list_creative_formats" in result.metadata["skills_requested"]
            assert result.artifacts is not None
            assert len(result.artifacts) == 1

            # Extract response
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "formats" in artifact_data

    @pytest.mark.asyncio
    async def test_list_authorized_properties_skill(
        self, handler, sample_tenant, sample_principal, mock_identity, validator
    ):
        """Test list_authorized_properties skill invocation."""
        # Create verified publisher partner for the tenant
        import uuid

        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import PublisherPartner

        # Generate unique publisher domain to avoid conflicts
        unique_publisher_domain = f"test-publisher-{uuid.uuid4().hex[:8]}.example.com"

        with get_db_session() as session:
            # Check if publisher already exists, create if not
            stmt = select(PublisherPartner).filter_by(
                publisher_domain=unique_publisher_domain, tenant_id=sample_tenant["tenant_id"]
            )
            existing_publisher = session.scalars(stmt).first()

            if not existing_publisher:
                publisher = PublisherPartner(
                    tenant_id=sample_tenant["tenant_id"],
                    publisher_domain=unique_publisher_domain,
                    display_name="Test Publisher",
                    is_verified=True,  # Must be verified for list_authorized_properties
                    sync_status="success",
                )
                session.add(publisher)
                session.commit()

        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {}
            message = create_a2a_message_with_skill("list_authorized_properties", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "list_authorized_properties" in result.metadata["skills_requested"]
            assert result.artifacts is not None

            # Extract response - per AdCP v2.4 spec, response has publisher_domains
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "publisher_domains" in artifact_data
            assert len(artifact_data["publisher_domains"]) > 0

    @pytest.mark.asyncio
    async def test_sync_creatives_skill(
        self, handler, sample_tenant, sample_principal, mock_identity, sample_products, validator
    ):
        """Test sync_creatives skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation with creatives
            skill_params = {
                "creatives": [
                    {
                        "creative_id": "creative_test_1",
                        "name": "Test Creative",
                        "format_id": "display_300x250",
                        "assets": build_assets(image_spec("asset_1", url="https://example.com/creative.jpg")),
                    }
                ]
            }
            message = create_a2a_message_with_skill("sync_creatives", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "sync_creatives" in result.metadata["skills_requested"]
            assert result.artifacts is not None

            # Extract response
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "creatives" in artifact_data or "failed_creatives" in artifact_data

    @pytest.mark.asyncio
    async def test_list_creatives_skill(self, handler, sample_tenant, sample_principal, mock_identity, validator):
        """Test list_creatives skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {}
            message = create_a2a_message_with_skill("list_creatives", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "list_creatives" in result.metadata["skills_requested"]
            assert result.artifacts is not None

            # Extract response
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "creatives" in artifact_data

    @pytest.mark.asyncio
    async def test_update_performance_index_skill(
        self, handler, sample_tenant, sample_principal, mock_identity, validator
    ):
        """Test update_performance_index skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {
                "media_buy_id": "mb_test_123",
                "performance_index": 1.25,
            }
            message = create_a2a_message_with_skill("update_performance_index", skill_params)
            params = SendMessageRequest(message=message)

            # This will likely fail because media_buy doesn't exist, but tests the code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify the skill was invoked
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "update_performance_index" in result.metadata["skills_requested"]

    @pytest.mark.asyncio
    async def test_get_media_buy_delivery_skill(
        self, handler, sample_tenant, sample_principal, mock_identity, validator
    ):
        """Test get_media_buy_delivery skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
        ):
            # Build ServerCallContext with Host header for subdomain detection
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {
                "media_buy_ids": ["mb_test_123"],
            }
            message = create_a2a_message_with_skill("get_media_buy_delivery", skill_params)
            params = SendMessageRequest(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params, context=ctx)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "get_media_buy_delivery" in result.metadata["skills_requested"]
            assert result.artifacts is not None

    @pytest.mark.asyncio
    async def test_get_media_buy_delivery_skill_forwards_typed_account(
        self, handler, sample_tenant, sample_principal, mock_identity, validator
    ):
        """A valid account survives the real on_message_send dispatch as a typed AccountReference.

        The handler-level unit tests (test_a2a_parameter_mapping.py) prove the skill method
        forwards req.account, and the malformed-account wire test (test_a2a_error_responses.py)
        proves the error path. This is the missing happy-path half: it drives the full
        DataPart-extraction → param-passing pipeline through on_message_send and asserts the
        buyer's account reaches the core tool validated, not as the raw dict that crashed
        resolve_account (account_ref.root on a dict).
        """
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity),
            patch("src.a2a_server.adcp_a2a_server.core_get_media_buy_delivery_tool") as mock_delivery,
        ):
            mock_delivery.return_value = {"media_buys": []}

            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            skill_params = {"media_buy_ids": ["mb_test_123"], "account": {"account_id": "acct-1"}}
            message = create_a2a_message_with_skill("get_media_buy_delivery", skill_params)
            params = SendMessageRequest(message=message)

            result = await handler.on_message_send(params, context=ctx)

            assert isinstance(result, Task)
            assert result.artifacts is not None

            expected = LibraryAccountReference.model_validate({"account_id": "acct-1"})
            assert_delivery_forwarded_account(mock_delivery, expected)

    @pytest.mark.asyncio
    async def test_approve_creative_skill(self, handler, sample_tenant, sample_principal, mock_identity, validator):
        """Test approve_creative skill raises UnsupportedOperationError."""
        from a2a.utils.errors import A2AError

        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity):
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            skill_params = {"creative_id": "creative_test_123"}
            message = create_a2a_message_with_skill("approve_creative", skill_params)
            params = SendMessageRequest(message=message)

            with pytest.raises(A2AError):
                await handler.on_message_send(params, context=ctx)

    @pytest.mark.asyncio
    async def test_get_media_buy_status_skill(self, handler, sample_tenant, sample_principal, mock_identity, validator):
        """Test get_media_buy_status skill raises UnsupportedOperationError."""
        from a2a.utils.errors import A2AError

        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity):
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            skill_params = {"media_buy_id": "mb_test_123"}
            message = create_a2a_message_with_skill("get_media_buy_status", skill_params)
            params = SendMessageRequest(message=message)

            with pytest.raises(A2AError):
                await handler.on_message_send(params, context=ctx)

    @pytest.mark.asyncio
    async def test_optimize_media_buy_skill(self, handler, sample_tenant, sample_principal, mock_identity, validator):
        """Test optimize_media_buy skill raises UnsupportedOperationError."""
        from a2a.utils.errors import A2AError

        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity):
            from tests.a2a_helpers import make_a2a_context

            ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

            skill_params = {"media_buy_id": "mb_test_123"}
            message = create_a2a_message_with_skill("optimize_media_buy", skill_params)
            params = SendMessageRequest(message=message)

            with pytest.raises(A2AError):
                await handler.on_message_send(params, context=ctx)


if __name__ == "__main__":
    # Run tests directly
    pytest.main([__file__, "-v"])
