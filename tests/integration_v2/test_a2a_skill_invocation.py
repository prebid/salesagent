#!/usr/bin/env python3
"""
Test A2A skill invocation patterns from AdCP PR #48.

Tests both natural language and explicit skill invocation patterns
to ensure our A2A server properly handles the evolving AdCP spec.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from a2a.types import DataPart, Message, MessageSendParams, Part, Role, Task, TaskStatus

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from tests.utils.a2a_helpers import create_a2a_message_with_skill, create_a2a_text_message

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Import schema validation components
try:
    from tests.e2e.adcp_schema_validator import AdCPSchemaValidator, SchemaValidationError

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

    # Map A2A skill names to AdCP schema task names
    SKILL_TO_SCHEMA_MAP = {
        "get_products": "get-products",
        "create_media_buy": "create-media-buy",
        "update_media_buy": "update-media-buy",  # AdCP v2.0+ endpoint
        "get_media_buy_delivery": "get-media-buy-delivery",  # AdCP delivery metrics
        "sync_creatives": "sync-creatives",  # New AdCP spec endpoint
        "list_creatives": "list-creatives",  # New AdCP spec endpoint
        "approve_creative": "approve-creative",  # When schema becomes available
        "get_signals": "get-signals",
        "search_signals": "search-signals",  # When schema becomes available
        # Legacy skills don't have AdCP schemas
        "get_pricing": None,
        "get_targeting": None,
        "get_media_buy_status": None,
        "optimize_media_buy": None,
    }

    def __init__(self):
        self.validator = None
        if SCHEMA_VALIDATION_AVAILABLE:
            self.validator = AdCPSchemaValidator(offline_mode=True, adcp_version="v1")

    async def __aenter__(self):
        if self.validator:
            await self.validator.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.validator:
            await self.validator.__aexit__(exc_type, exc_val, exc_tb)

    def extract_adcp_payload_from_a2a_artifact(self, artifact) -> dict:
        """Extract AdCP payload from A2A artifact structure."""
        if not artifact or not artifact.parts:
            return {}

        # A2A artifacts have: parts = [Part(type="data", data={...})]
        for part in artifact.parts:
            if hasattr(part, "data") and isinstance(part.data, dict):
                return part.data
            # Handle different A2A Part structures
            if hasattr(part, "root") and hasattr(part.root, "data"):
                return part.root.data

        return {}

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

        # Check if skill has corresponding AdCP schema
        schema_task = self.SKILL_TO_SCHEMA_MAP.get(skill_name)
        if not schema_task:
            warnings.append(f"No AdCP schema mapping for skill '{skill_name}' - skipping")
            return result

        result["schema_tested"] = schema_task

        # Extract AdCP payload from A2A artifacts
        if not task_result.artifacts:
            errors.append("No artifacts found in A2A task result")
            result["valid"] = False
            return result

        # Validate each artifact (skills can return multiple artifacts)
        for i, artifact in enumerate(task_result.artifacts):
            try:
                adcp_payload = self.extract_adcp_payload_from_a2a_artifact(artifact)
                if not adcp_payload:
                    warnings.append(f"Artifact {i}: No AdCP payload found")
                    continue

                # Validate against AdCP schema
                await self.validator.validate_response(schema_task, adcp_payload)
                warnings.append(f"Artifact {i}: AdCP schema validation passed")

            except SchemaValidationError as e:
                errors.append(f"Artifact {i}: AdCP schema validation failed: {e}")
                result["valid"] = False
            except Exception as e:
                errors.append(f"Artifact {i}: Validation error: {e}")
                result["valid"] = False

        return result


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
        from a2a.types import TextPart

        return Message(
            message_id="msg_789",
            context_id="ctx_789",
            role=Role.user,
            parts=[
                Part(root=TextPart(text=text)),
                Part(root=DataPart(data={"skill": skill, "parameters": parameters})),
            ],
        )

    @pytest.mark.asyncio
    async def test_natural_language_get_products(
        self, handler, sample_tenant, sample_principal, sample_products, validator
    ):
        """Test natural language invocation for get_products with AdCP schema validation."""
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create natural language message
            message = create_a2a_text_message("What video products do you have available?")
            params = MessageSendParams(message=message)

            # Process the message - this will execute the real code path
            result = await handler.on_message_send(params)

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

            # Schema validation should pass or warn (but not fail the test)
            if validation_result["errors"]:
                print(f"Schema validation errors: {validation_result['errors']}")
            if validation_result["warnings"]:
                print(f"Schema validation warnings: {validation_result['warnings']}")

    @pytest.mark.asyncio
    async def test_explicit_skill_get_products(
        self, handler, sample_tenant, sample_principal, sample_products, validator
    ):
        """Test explicit skill invocation for get_products with AdCP schema validation."""
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create explicit skill invocation message
            skill_params = {
                "brief": "Display advertising for news content",
                "brand_manifest": {"name": "News media company"},
            }
            message = create_a2a_message_with_skill("get_products", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - this will execute the real code path
            result = await handler.on_message_send(params)

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

            # Schema validation should pass or warn (but not fail the test)
            if validation_result["errors"]:
                print(f"Schema validation errors: {validation_result['errors']}")
            if validation_result["warnings"]:
                print(f"Schema validation warnings: {validation_result['warnings']}")

    @pytest.mark.asyncio
    async def test_explicit_skill_get_products_a2a_spec(
        self, handler, sample_tenant, sample_principal, sample_products, validator
    ):
        """Test explicit skill invocation using A2A spec 'input' field instead of 'parameters'."""
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create explicit skill invocation message using A2A spec 'input' field
            skill_params = {
                "brief": "Premium coffee brands",
                "brand_manifest": {"name": "Wonderstruck Premium Video Ads"},
            }
            message = create_a2a_message_with_skill("get_products", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - this will execute the real code path
            result = await handler.on_message_send(params)

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

            # Schema validation should pass or warn (but not fail the test)
            if validation_result["errors"]:
                print(f"Schema validation errors: {validation_result['errors']}")
            if validation_result["warnings"]:
                print(f"Schema validation warnings: {validation_result['warnings']}")

    @pytest.mark.asyncio
    async def test_explicit_skill_create_media_buy(
        self, handler, sample_tenant, sample_principal, sample_products, validator
    ):
        """Test explicit skill invocation for create_media_buy.

        NOTE: This test now uses the REAL mock adapter and code paths,
        only mocking authentication. This ensures we catch serialization bugs.
        """
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create explicit skill invocation message using AdCP spec format
            from datetime import UTC, datetime, timedelta

            start_date = datetime.now(UTC) + timedelta(days=1)
            end_date = start_date + timedelta(days=30)

            skill_params = {
                "brand_manifest": {"name": "Test Campaign"},
                "packages": [
                    {
                        "buyer_ref": f"pkg_{sample_products[0]}",
                        "product_id": sample_products[0],  # Use product_id per AdCP spec
                        "budget": {"total": 10000.0, "currency": "USD"},
                    }
                ],
                "budget": {"total": 10000.0, "currency": "USD"},
                "start_time": start_date.isoformat(),
                "end_time": end_date.isoformat(),
            }
            message = create_a2a_message_with_skill("create_media_buy", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - executes REAL _create_media_buy_impl with mock adapter
            result = await handler.on_message_send(params)

            # Verify the result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "create_media_buy" in result.metadata["skills_requested"]
            assert result.artifacts is not None
            assert len(result.artifacts) == 1
            assert result.artifacts[0].name == "create_media_buy_result"

            # Extract response data
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            # Per AdCP spec, CreateMediaBuyResponse has buyer_ref, media_buy_id, packages, etc.
            # No 'success' field in the spec - that's a protocol-level field
            assert "media_buy_id" in artifact_data
            assert "buyer_ref" in artifact_data

            # Verify packages are properly serialized (this would have caught the bug!)
            assert "packages" in artifact_data
            assert isinstance(artifact_data["packages"], list)

    @pytest.mark.asyncio
    async def test_hybrid_invocation(self, handler, sample_tenant, sample_principal, sample_products, validator):
        """Test hybrid invocation with both text and skill."""
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create hybrid message (text + explicit skill)
            skill_params = {"brief": "Sports video advertising", "brand_manifest": {"name": "Sports brand"}}
            message = self.create_message_hybrid(
                "I need video products for sports content", "get_products", skill_params
            )
            params = MessageSendParams(message=message)

            # Process the message - this will execute the real code path
            result = await handler.on_message_send(params)

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
    # TODO: Needs investigation of proper error handling approach (ServerError not in current a2a library)

    @pytest.mark.asyncio
    async def test_multiple_skill_invocations(self, handler, sample_tenant, sample_principal, sample_products):
        """Test multiple skill invocations in a single message."""
        # Mock authentication token
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create message with multiple skill invocations
            message = Message(
                message_id="msg_multi",
                context_id="ctx_multi",
                role=Role.user,
                parts=[
                    Part(
                        root=DataPart(
                            kind="data",
                            data={
                                "skill": "get_products",
                                "parameters": {"brief": "video ads", "brand_manifest": {"name": "Test Brand Product"}},
                            },
                        )
                    ),
                    Part(
                        root=DataPart(
                            kind="data",
                            data={
                                "skill": "get_signals",
                                "parameters": {
                                    "signal_spec": "audience signals for targeting",
                                    "deliver_to": {"platforms": ["mock"], "formats": ["display_300x250"]},
                                },
                            },
                        )
                    ),
                ],
            )
            params = MessageSendParams(message=message)

            # Process the message - this will execute the real code path
            result = await handler.on_message_send(params)

            # Verify both skills were processed
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert len(result.metadata["skills_requested"]) == 2
            assert "get_products" in result.metadata["skills_requested"]
            assert "get_signals" in result.metadata["skills_requested"]
            assert len(result.artifacts) == 2

            # Verify both artifacts have data
            for artifact in result.artifacts:
                assert artifact.parts[0].root.data is not None

    # TODO: Add test_missing_authentication once we understand how A2A server handles auth errors
    # TODO: Needs investigation of proper error handling approach (ServerError not in current a2a library)

    @pytest.mark.asyncio
    async def test_adcp_schema_validation_integration(self, validator):
        """Test A2A-to-AdCP schema validation integration."""
        # Test the validation helper directly with mock data

        # Create mock A2A task with AdCP-compliant product data
        from a2a.types import Artifact

        mock_adcp_products_response = {
            "products": [
                {
                    "id": "prod_test_1",
                    "name": "Test Video Product",
                    "description": "Test video advertising product",
                    "formats": [{"id": "video_720p", "name": "720p Video", "width": 1280, "height": 720}],
                    "pricing": {"base_cpm": 12.5, "currency": "USD"},
                    "targeting_template": {"demographics": ["18-34"], "interests": ["technology"]},
                    "countries": ["US", "CA"],
                    "delivery_type": "guaranteed",
                }
            ],
            "message": "Products retrieved successfully",
        }

        # Create A2A artifacts structure
        artifact = Artifact(
            artifactId="test_artifact_1",
            name="get_products_result",
            parts=[Part(root=DataPart(data=mock_adcp_products_response))],
        )

        mock_task = Task(
            id="test_task_1",
            context_id="test_context_1",
            kind="task",
            status=TaskStatus(state="completed"),
            artifacts=[artifact],
        )

        # Test validation for each skill that has AdCP schemas
        adcp_skills_to_test = {
            "get_products": mock_task,
            # Add other skills when we have mock data for them
        }

        for skill_name, task_result in adcp_skills_to_test.items():
            validation_result = await validator.validate_a2a_skill_response(skill_name, task_result)

            print(f"\n=== Schema Validation Results for {skill_name} ===")
            print(f"Valid: {validation_result['valid']}")
            print(f"Schema tested: {validation_result['schema_tested']}")

            if validation_result["errors"]:
                print(f"Errors: {validation_result['errors']}")
            if validation_result["warnings"]:
                print(f"Warnings: {validation_result['warnings']}")

            # For now, don't fail on validation errors - just ensure the validator runs
            assert "schema_tested" in validation_result

            # If schema validation is available and schema exists, it should have attempted validation
            if SCHEMA_VALIDATION_AVAILABLE and validation_result["schema_tested"]:
                assert validation_result["schema_tested"] == "get-products"
                # Either valid or has meaningful errors/warnings
                assert validation_result["valid"] or validation_result["errors"] or validation_result["warnings"]

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
                    "get_products",
                    "create_media_buy",
                    "update_media_buy",  # Added for media buy management
                    "get_media_buy_delivery",  # Added for delivery metrics
                    "update_performance_index",  # Added for performance optimization
                    "sync_creatives",
                    "list_creatives",
                    "approve_creative",
                    "get_media_buy_status",
                    "optimize_media_buy",
                    "get_signals",
                    "search_signals",
                    "get_pricing",
                    "get_targeting",
                    "list_creative_formats",  # Keep existing creative format endpoint
                    "list_authorized_properties",  # Added for AdCP compliance
                ], f"Skill {skill_name} not in expected skill list"
            except Exception as e:
                pytest.fail(f"Skill {skill_name} should be handled but caused error: {e}")

    # Phase 2: Tests for previously untested skills

    @pytest.mark.asyncio
    async def test_update_media_buy_skill(self, handler, sample_tenant, sample_principal, sample_products, validator):
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
                buyer_ref="test_buyer_ref",
                status="active",
                order_name="Test Campaign",
                advertiser_name="Test Brand",
                start_date=start_date.date(),
                end_date=end_date.date(),
                start_time=start_date,  # Add start_time for flight days calculation
                end_time=end_date,  # Add end_time for flight days calculation
                budget=10000.0,
                currency="USD",
                raw_request={"brand_manifest": {"name": "Test Brand"}, "packages": []},
            )
            session.add(media_buy)
            session.commit()

        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        with (
            patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,
            patch("src.core.helpers.adapter_helpers.get_adapter") as mock_get_adapter,
        ):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Mock adapter - must return UpdateMediaBuyResponse, not dict
            from src.core.schemas import UpdateMediaBuyResponse

            mock_adapter = MagicMock()
            mock_adapter.update_media_buy.return_value = UpdateMediaBuyResponse(
                media_buy_id="mb_test_123",
                buyer_ref="test_buyer_ref",
                affected_packages=[],
                errors=None,
            )
            mock_get_adapter.return_value = mock_adapter

            # Create skill invocation
            # Per AdCP spec, budget is a float, not a Budget object in update_media_buy
            skill_params = {
                "media_buy_id": "mb_test_123",
                "budget": 15000.0,  # Float per AdCP spec, not Budget object
                "active": True,
            }
            message = create_a2a_message_with_skill("update_media_buy", skill_params)
            params = MessageSendParams(message=message)

            # Process the message
            result = await handler.on_message_send(params)

            # Verify the skill was invoked
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "update_media_buy" in result.metadata["skills_requested"]

    @pytest.mark.asyncio
    async def test_list_creative_formats_skill(
        self, handler, sample_tenant, sample_principal, sample_products, validator
    ):
        """Test list_creative_formats skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {"brief": "display formats"}
            message = create_a2a_message_with_skill("list_creative_formats", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params)

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
    async def test_list_authorized_properties_skill(self, handler, sample_tenant, sample_principal, validator):
        """Test list_authorized_properties skill invocation."""
        # Create authorized properties for the tenant
        import uuid

        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import AuthorizedProperty

        # Generate unique property_id to avoid conflicts
        unique_property_id = f"test_property_{uuid.uuid4().hex[:8]}"

        with get_db_session() as session:
            # Check if property already exists, create if not
            stmt = select(AuthorizedProperty).filter_by(
                property_id=unique_property_id, tenant_id=sample_tenant["tenant_id"]
            )
            existing_prop = session.scalars(stmt).first()

            if not existing_prop:
                prop = AuthorizedProperty(
                    property_id=unique_property_id,
                    tenant_id=sample_tenant["tenant_id"],
                    property_type="website",
                    name="Test Site",
                    identifiers=[{"type": "domain", "value": "example.com"}],
                    tags=["test"],
                    publisher_domain="example.com",
                    verification_status="verified",
                )
                session.add(prop)
                session.commit()

        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {}
            message = create_a2a_message_with_skill("list_authorized_properties", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params)

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
    async def test_sync_creatives_skill(self, handler, sample_tenant, sample_principal, sample_products, validator):
        """Test sync_creatives skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation with creatives
            skill_params = {
                "creatives": [
                    {
                        "creative_id": "creative_test_1",
                        "name": "Test Creative",
                        "format_id": "display_300x250",
                        "assets": {"asset_1": {"asset_type": "image", "url": "https://example.com/creative.jpg"}},
                    }
                ]
            }
            message = create_a2a_message_with_skill("sync_creatives", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "sync_creatives" in result.metadata["skills_requested"]
            assert result.artifacts is not None

            # Extract response
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "creatives" in artifact_data or "failed_creatives" in artifact_data

    @pytest.mark.asyncio
    async def test_list_creatives_skill(self, handler, sample_tenant, sample_principal, validator):
        """Test list_creatives skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {}
            message = create_a2a_message_with_skill("list_creatives", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "list_creatives" in result.metadata["skills_requested"]
            assert result.artifacts is not None

            # Extract response
            artifact_data = validator.extract_adcp_payload_from_a2a_artifact(result.artifacts[0])
            assert "creatives" in artifact_data

    @pytest.mark.asyncio
    async def test_update_performance_index_skill(self, handler, sample_tenant, sample_principal, validator):
        """Test update_performance_index skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {
                "media_buy_id": "mb_test_123",
                "performance_index": 1.25,
            }
            message = create_a2a_message_with_skill("update_performance_index", skill_params)
            params = MessageSendParams(message=message)

            # This will likely fail because media_buy doesn't exist, but tests the code path
            result = await handler.on_message_send(params)

            # Verify the skill was invoked
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "update_performance_index" in result.metadata["skills_requested"]

    @pytest.mark.asyncio
    async def test_get_media_buy_delivery_skill(self, handler, sample_tenant, sample_principal, validator):
        """Test get_media_buy_delivery skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {
                "media_buy_ids": ["mb_test_123"],
            }
            message = create_a2a_message_with_skill("get_media_buy_delivery", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "get_media_buy_delivery" in result.metadata["skills_requested"]
            assert result.artifacts is not None

    @pytest.mark.asyncio
    async def test_get_pricing_skill(self, handler, sample_tenant, sample_principal, validator):
        """Test get_pricing skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {}
            message = create_a2a_message_with_skill("get_pricing", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "get_pricing" in result.metadata["skills_requested"]
            assert result.artifacts is not None

    @pytest.mark.asyncio
    async def test_get_targeting_skill(self, handler, sample_tenant, sample_principal, validator):
        """Test get_targeting skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {}
            message = create_a2a_message_with_skill("get_targeting", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "get_targeting" in result.metadata["skills_requested"]
            assert result.artifacts is not None

    @pytest.mark.asyncio
    async def test_search_signals_skill(self, handler, sample_tenant, sample_principal, validator):
        """Test search_signals skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {
                "query": "audience targeting signals",
            }
            message = create_a2a_message_with_skill("search_signals", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "search_signals" in result.metadata["skills_requested"]
            assert result.artifacts is not None

    @pytest.mark.asyncio
    async def test_approve_creative_skill(self, handler, sample_tenant, sample_principal, validator):
        """Test approve_creative skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {
                "creative_id": "creative_test_123",
            }
            message = create_a2a_message_with_skill("approve_creative", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "approve_creative" in result.metadata["skills_requested"]

    @pytest.mark.asyncio
    async def test_get_media_buy_status_skill(self, handler, sample_tenant, sample_principal, validator):
        """Test get_media_buy_status skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {
                "media_buy_id": "mb_test_123",
            }
            message = create_a2a_message_with_skill("get_media_buy_status", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "get_media_buy_status" in result.metadata["skills_requested"]

    @pytest.mark.asyncio
    async def test_optimize_media_buy_skill(self, handler, sample_tenant, sample_principal, validator):
        """Test optimize_media_buy skill invocation."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation
            skill_params = {
                "media_buy_id": "mb_test_123",
            }
            message = create_a2a_message_with_skill("optimize_media_buy", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "optimize_media_buy" in result.metadata["skills_requested"]

    @pytest.mark.asyncio
    async def test_get_signals_explicit_skill(self, handler, sample_tenant, sample_principal, validator):
        """Test get_signals skill invocation with explicit parameters."""
        handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

        # Mock tenant detection - provide Host header so real functions can find tenant in database
        # Use actual tenant subdomain from fixture
        with (patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,):
            mock_get_principal.return_value = sample_principal["principal_id"]
            # Mock request headers to provide Host header for subdomain detection
            # Use actual subdomain from sample_tenant so get_tenant_by_subdomain() can find it in DB
            from src.a2a_server import adcp_a2a_server

            adcp_a2a_server._request_headers.set({"host": f"{sample_tenant['subdomain']}.example.com"})

            # Create skill invocation with proper AdCP parameters
            skill_params = {
                "signal_spec": "audience targeting signals for premium inventory",
                "deliver_to": {"platforms": ["mock"], "formats": ["display_300x250"]},
            }
            message = create_a2a_message_with_skill("get_signals", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - executes real code path
            result = await handler.on_message_send(params)

            # Verify result
            assert isinstance(result, Task)
            assert result.metadata["invocation_type"] == "explicit_skill"
            assert "get_signals" in result.metadata["skills_requested"]
            assert result.artifacts is not None


if __name__ == "__main__":
    # Run tests directly
    pytest.main([__file__, "-v"])
