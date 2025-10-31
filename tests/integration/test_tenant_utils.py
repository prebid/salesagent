"""Integration tests for tenant serialization utilities.

These tests focus on testing OUR serialize_tenant_to_dict() function's business logic,
specifically the JSON deserialization behavior via safe_json_loads().

Tests that were deleted (they only tested dict structure/assignment):
- test_serialize_tenant_includes_all_expected_fields: Just checked keys exist
- test_serialize_tenant_field_values: Tested assignment (result["name"] == tenant.name)
- test_serialize_tenant_model_column_coverage: Duplicate of first test

Per CLAUDE.md: "Test YOUR code's logic and behavior, not Python/SQLAlchemy."
"""

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant
from src.core.utils.tenant_utils import serialize_tenant_to_dict


@pytest.mark.requires_db
def test_serialize_tenant_json_fields_are_deserialized(integration_db):
    """Test JSON fields are returned as Python objects (lists/dicts), not strings.

    This tests the critical safe_json_loads() behavior in serialize_tenant_to_dict().
    JSONType columns should automatically deserialize, but this verifies the contract.
    """
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id="test",
            name="Test Tenant",
            subdomain="test_json",
            authorized_emails=["admin@test.com", "user@test.com"],
            authorized_domains=["test.com", "example.com"],
            auto_approve_formats=["display_300x250", "video_640x480"],
            policy_settings={"enabled": True},
            signals_agent_config={"endpoint": "https://api.example.com", "timeout": 10},
        )
        session.add(tenant)
        session.flush()

        result = serialize_tenant_to_dict(tenant)

        # TEST: safe_json_loads() returns Python objects, not JSON strings
        assert isinstance(result["authorized_emails"], list)
        assert result["authorized_emails"] == ["admin@test.com", "user@test.com"]

        assert isinstance(result["authorized_domains"], list)
        assert result["authorized_domains"] == ["test.com", "example.com"]

        assert isinstance(result["auto_approve_formats"], list)
        assert result["auto_approve_formats"] == ["display_300x250", "video_640x480"]

        assert isinstance(result["policy_settings"], dict)
        assert result["policy_settings"]["enabled"] is True
        # Note: policy_settings just stores the dict, we don't need to check specific keys

        assert isinstance(result["signals_agent_config"], dict)
        assert result["signals_agent_config"]["endpoint"] == "https://api.example.com"


@pytest.mark.requires_db
def test_serialize_tenant_nullable_fields_have_defaults(integration_db):
    """Test nullable fields get appropriate defaults when not provided.

    This tests the safe_json_loads() default parameter behavior.
    """
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id="test",
            name="Test Tenant",
            subdomain="test_nullable",
            # All nullable fields omitted
        )
        session.add(tenant)
        session.flush()

        result = serialize_tenant_to_dict(tenant)

        # TEST: safe_json_loads() provides default empty lists for array fields
        assert result["authorized_emails"] == []  # Default empty list
        assert result["authorized_domains"] == []  # Default empty list
        assert result["auto_approve_formats"] == []  # Default empty list

        # TEST: Nullable scalar fields are None (no default)
        assert result["virtual_host"] is None
        assert result["slack_webhook_url"] is None
        assert result["admin_token"] is None
