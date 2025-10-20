"""Test standardized session management and JSON validation."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

# Import our new utilities
from src.core.database.database_session import DatabaseManager, get_db_session, get_or_404, get_or_create
from src.core.database.models import Context, Principal, Product, Tenant, WorkflowStep
from src.core.json_validators import (
    CommentModel,
    CreativeFormatModel,
    PlatformMappingModel,
    ensure_json_array,
    ensure_json_object,
)
from tests.utils.database_helpers import create_tenant_with_timestamps


# Test fixtures
@pytest.fixture
def test_db(integration_db):
    """Use PostgreSQL test database for session management tests."""
    # integration_db fixture provides the database, just return the engine
    from src.core.database.database_session import get_engine

    yield get_engine()


class TestSessionManagement:
    """Test standardized session management patterns."""

    def test_context_manager_pattern(self, test_db):
        """Test the get_db_session context manager."""
        # Create a tenant using context manager and helper
        with get_db_session() as session:
            tenant = create_tenant_with_timestamps(
                tenant_id="test_tenant",
                name="Test Tenant",
                subdomain="test",
                authorized_emails=["admin@test.com"],
                authorized_domains=["test.com"],
                auto_approve_formats=["display_300x250"],
                policy_settings={"enabled": True},
            )
            session.add(tenant)
            session.commit()

        # Verify it was created
        with get_db_session() as session:
            retrieved = session.scalars(select(Tenant).filter_by(tenant_id="test_tenant")).first()
            assert retrieved is not None
            assert retrieved.name == "Test Tenant"
            assert retrieved.authorized_emails == ["admin@test.com"]

    def test_database_manager_class(self, test_db):
        """Test the DatabaseManager class pattern."""

        class TestManager(DatabaseManager):
            def create_tenant(self, tenant_id: str, name: str) -> Tenant:
                now = datetime.now(UTC)
                tenant = Tenant(
                    tenant_id=tenant_id,
                    name=name,
                    subdomain=tenant_id.lower(),
                    authorized_emails=[],
                    policy_settings={},
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(tenant)
                return tenant

        # Use as context manager
        with TestManager() as manager:
            _ = manager.create_tenant("test2", "Test 2")
            # Commit happens automatically on exit

        # Verify creation
        with get_db_session() as session:
            retrieved = session.scalars(select(Tenant).filter_by(tenant_id="test2")).first()
            assert retrieved is not None
            assert retrieved.name == "Test 2"

    def test_get_or_404(self, test_db):
        """Test the get_or_404 helper function."""
        # Create a tenant
        now = datetime.now(UTC)
        with get_db_session() as session:
            tenant = Tenant(
                tenant_id="test3",
                name="Test 3",
                subdomain="test3",
                authorized_emails=[],
                policy_settings={},
                created_at=now,
                updated_at=now,
            )
            session.add(tenant)
            session.commit()

        # Test successful retrieval
        with get_db_session() as session:
            found = get_or_404(session, Tenant, tenant_id="test3")
            assert found.name == "Test 3"

        # Test 404 case
        with get_db_session() as session:
            with pytest.raises(ValueError, match="Tenant not found"):
                get_or_404(session, Tenant, tenant_id="nonexistent")

    def test_get_or_create(self, test_db):
        """Test the get_or_create helper function."""
        with get_db_session() as session:
            # First call should create
            tenant1, created1 = get_or_create(
                session,
                Tenant,
                defaults={"name": "Created Tenant", "authorized_emails": [], "policy_settings": {}},
                tenant_id="test4",
                subdomain="test4",
            )
            session.commit()
            assert created1 is True
            assert tenant1.name == "Created Tenant"

        with get_db_session() as session:
            # Second call should retrieve
            tenant2, created2 = get_or_create(
                session, Tenant, defaults={"name": "Should Not Use This"}, tenant_id="test4", subdomain="test4"
            )
            assert created2 is False
            assert tenant2.name == "Created Tenant"  # Original name


class TestJSONValidation:
    """Test JSON field validation."""

    def test_comment_model_validation(self):
        """Test comment validation with Pydantic."""
        # Valid comment
        comment = CommentModel(user="testuser", text="This is a comment")
        assert comment.user == "testuser"
        assert comment.text == "This is a comment"
        assert isinstance(comment.timestamp, datetime)

        # Invalid - empty text
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="String should have at least 1 character"):
            CommentModel(user="test", text="")

        # Invalid - empty user
        with pytest.raises(ValidationError, match="String should have at least 1 character"):
            CommentModel(user="", text="test")

    def test_platform_mapping_validation(self):
        """Test platform mapping validation."""
        # Valid - at least one platform
        mapping = PlatformMappingModel(google_ad_manager={"advertiser_id": "123"}, mock={"test": True})
        assert mapping.google_ad_manager == {"advertiser_id": "123"}

        # Invalid - no platforms
        with pytest.raises(ValueError, match="At least one platform mapping"):
            PlatformMappingModel()

    def test_creative_format_validation(self):
        """Test creative format validation."""
        # Valid format
        fmt = CreativeFormatModel(
            format_id="display_300x250",
            name="Display Banner",
            type="display",
            description="Standard banner",
            width=300,
            height=250,
        )
        assert fmt.width == 300
        assert fmt.type == "display"

        # Legacy format type - should be mapped to standard type
        legacy_fmt = CreativeFormatModel(
            format_id="banner_728x90",
            name="Banner",
            type="banner",  # Legacy type, should be mapped to "display"
            description="Legacy banner format",
            width=728,
            height=90,
        )
        assert legacy_fmt.type == "display"  # Should be mapped from "banner" to "display"

        # Unknown format type - should default to "display"
        unknown_fmt = CreativeFormatModel(
            format_id="unknown_format",
            name="Unknown Format",
            type="some_unknown_type",  # Unknown type, should default to "display"
        )
        assert unknown_fmt.type == "display"  # Should default to "display"

        # Empty type should raise error
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="String should have at least 1 character"):
            CreativeFormatModel(format_id="test", name="Test", type="", description="Test")  # Empty type

    def test_ensure_json_helpers(self):
        """Test JSON helper functions."""
        # Test ensure_json_array
        assert ensure_json_array(None) == []
        assert ensure_json_array([1, 2, 3]) == [1, 2, 3]
        assert ensure_json_array('["a", "b"]') == ["a", "b"]

        with pytest.raises(ValueError, match="Invalid JSON"):
            ensure_json_array("not json")

        with pytest.raises(ValueError, match="must be a list"):
            ensure_json_array({"key": "value"})

        # Test ensure_json_object
        assert ensure_json_object(None) == {}
        assert ensure_json_object({"key": "value"}) == {"key": "value"}
        assert ensure_json_object('{"key": "value"}') == {"key": "value"}

        with pytest.raises(ValueError, match="must be a dictionary"):
            ensure_json_object([1, 2, 3])

    def test_model_json_validation(self, test_db):
        """Test JSON validation in SQLAlchemy models."""
        with get_db_session() as session:
            # Test Tenant with valid JSON fields
            now = datetime.now(UTC)
            tenant = Tenant(
                tenant_id="json_test",
                name="JSON Test",
                subdomain="jsontest",
                authorized_emails=["test@example.com"],  # Valid array
                authorized_domains=["example.com"],  # Valid array
                auto_approve_formats=["display_300x250"],  # Valid array
                policy_settings={"enabled": True},  # Valid object
                created_at=now,
                updated_at=now,
            )
            session.add(tenant)
            session.commit()

            # Retrieve and verify
            retrieved = session.scalars(select(Tenant).filter_by(tenant_id="json_test")).first()
            assert retrieved.authorized_emails == ["test@example.com"]
            # PolicySettingsModel adds default values
            assert retrieved.policy_settings["enabled"] is True
            assert "require_approval" in retrieved.policy_settings  # Default added
            assert "blocked_categories" in retrieved.policy_settings  # Default added

    def test_principal_platform_mappings(self, test_db):
        """Test Principal platform_mappings validation."""
        with get_db_session() as session:
            # Create tenant first
            now = datetime.now(UTC)
            tenant = Tenant(
                tenant_id="test_tenant",
                name="Test",
                subdomain="test",
                authorized_emails=[],
                policy_settings={},
                created_at=now,
                updated_at=now,
            )
            session.add(tenant)

            # Valid principal with platform mappings
            principal = Principal(
                tenant_id="test_tenant",
                principal_id="test_principal",
                name="Test Principal",
                access_token="token123",
                platform_mappings={"mock": {"enabled": True}},
                created_at=now,
            )
            session.add(principal)
            session.commit()

            # Retrieve and verify
            retrieved = session.scalars(select(Principal).filter_by(principal_id="test_principal")).first()
            assert retrieved.platform_mappings == {"mock": {"enabled": True}}

    def test_workflow_step_comments(self, test_db):
        """Test WorkflowStep comments validation."""
        with get_db_session() as session:
            # Create context first
            context = Context(context_id="ctx_test", tenant_id="test", principal_id="test", conversation_history=[])
            session.add(context)

            # Create workflow step with comments
            step = WorkflowStep(
                step_id="step_test",
                context_id="ctx_test",
                step_type="approval",
                owner="principal",
                status="pending",
                comments=[{"user": "admin", "timestamp": datetime.now(UTC).isoformat(), "text": "Please review"}],
            )
            session.add(step)
            session.commit()

            # Retrieve and verify
            retrieved = session.scalars(select(WorkflowStep).filter_by(step_id="step_test")).first()
            assert len(retrieved.comments) == 1
            assert retrieved.comments[0]["text"] == "Please review"


class TestIntegration:
    """Test integration of session management with JSON validation."""

    def test_full_workflow(self, test_db):
        """Test a complete workflow with proper session management and JSON validation."""

        # Use DatabaseManager for complex operations
        class WorkflowManager(DatabaseManager):
            def setup_tenant_with_products(self):
                # Create tenant with validated JSON fields
                now = datetime.now(UTC)
                tenant = Tenant(
                    tenant_id="workflow_test",
                    name="Workflow Test",
                    subdomain="workflow",
                    authorized_emails=["admin@workflow.com"],
                    authorized_domains=["workflow.com"],
                    auto_approve_formats=["display_300x250", "video_16x9"],
                    policy_settings={"enabled": True, "require_approval": False, "max_daily_budget": 10000.0},
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(tenant)

                # Create product with validated formats
                product = Product(
                    tenant_id="workflow_test",
                    product_id="prod_1",
                    name="Test Product",
                    description="Test product description",
                    formats=[
                        {
                            "format_id": "display_300x250",
                            "name": "Display Banner",
                            "type": "display",
                            "description": "Standard banner",
                            "assets": {},
                            "delivery_options": {},
                        }
                    ],
                    targeting_template={"geo_targets": ["US", "CA"], "device_targets": ["desktop", "mobile"]},
                    delivery_type="guaranteed",
                    is_fixed_price=True,
                    cpm=10.0,
                    countries=["US", "CA"],
                )
                self.session.add(product)

                # Create principal
                principal = Principal(
                    tenant_id="workflow_test",
                    principal_id="buyer_1",
                    name="Test Buyer",
                    access_token="buyer_token_123",
                    platform_mappings={"google_ad_manager": {"advertiser_id": "12345"}, "mock": {"test_mode": True}},
                    created_at=now,
                )
                self.session.add(principal)

                return tenant, product, principal

        # Execute the workflow
        with WorkflowManager() as manager:
            tenant, product, principal = manager.setup_tenant_with_products()

        # Verify everything was created with proper JSON
        with get_db_session() as session:
            # Check tenant
            t = session.scalars(select(Tenant).filter_by(tenant_id="workflow_test")).first()
            assert t is not None
            assert t.auto_approve_formats == ["display_300x250", "video_16x9"]
            assert t.policy_settings["max_daily_budget"] == 10000.0

            # Check product
            p = session.scalars(select(Product).filter_by(product_id="prod_1")).first()
            assert p is not None
            assert len(p.formats) == 1
            assert p.formats[0] == "display_300x250"  # Format now stored as string ID
            assert p.targeting_template["geo_targets"] == ["US", "CA"]

            # Check principal
            pr = session.scalars(select(Principal).filter_by(principal_id="buyer_1")).first()
            assert pr is not None
            assert "google_ad_manager" in pr.platform_mappings
            assert pr.platform_mappings["mock"]["test_mode"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
