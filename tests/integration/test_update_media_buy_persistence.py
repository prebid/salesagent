"""Integration tests for update_media_buy with database persistence.

Tests the fix for the issue where update_media_buy failed with "Media buy not found"
when the media buy existed in the database but not in the in-memory media_buys dictionary.

This verifies that _verify_principal() queries the database instead of checking
the in-memory dictionary.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    CurrencyLimit,
    MediaBuy,
    Tenant,
)
from src.core.database.models import (
    Principal as ModelPrincipal,
)
from src.core.schemas import UpdateMediaBuyResponse
from src.core.tools.media_buy_update import _update_media_buy_impl

# Note: _verify_principal is now internal to _update_media_buy_impl
# Tests that used _verify_principal directly will need to test through the public API


class MockContext:
    """Mock FastMCP Context for testing."""

    def __init__(self, tenant_id: str, principal_id: str, token: str):
        self.headers = {
            "x-adcp-auth": token,
            "host": f"{tenant_id}.test.com",
        }
        self.meta = {
            "headers": {
                "x-adcp-auth": token,
                "host": f"{tenant_id}.test.com",
            }
        }


@pytest.fixture
def test_tenant_setup(integration_db):
    """Create test tenant with principal and currency limit."""
    tenant_id = "test_update_persist"
    principal_id = "test_adv_persist"
    token = "test_token_persist_123"

    with get_db_session() as session:
        # Create tenant
        tenant = Tenant(
            tenant_id=tenant_id,
            name="Test Update Persist Tenant",
            subdomain="test-update-persist",
            ad_server="mock",
            is_active=True,
            human_review_required=False,
            auto_approve_format_ids=[],
            policy_settings={},
        )
        session.add(tenant)

        # Create principal
        principal = ModelPrincipal(
            tenant_id=tenant_id,
            principal_id=principal_id,
            name="Test Advertiser Persist",
            access_token=token,
            platform_mappings={"mock": {"id": "adv_persist"}},
        )
        session.add(principal)

        # Create currency limit (required for budget validation)
        currency_limit = CurrencyLimit(
            tenant_id=tenant_id,
            currency_code="USD",
            max_daily_package_spend=10000.0,
        )
        session.add(currency_limit)

        session.commit()

    yield {
        "tenant_id": tenant_id,
        "principal_id": principal_id,
        "token": token,
    }

    # Cleanup
    with get_db_session() as session:
        session.query(MediaBuy).filter_by(tenant_id=tenant_id).delete()
        session.query(CurrencyLimit).filter_by(tenant_id=tenant_id).delete()
        session.query(ModelPrincipal).filter_by(tenant_id=tenant_id).delete()
        session.query(Tenant).filter_by(tenant_id=tenant_id).delete()
        session.commit()


@pytest.mark.requires_db
def test_update_media_buy_with_database_persisted_buy(test_tenant_setup):
    """Test update_media_buy works with database-persisted media buy.

    This is the main integration test that verifies the fix for the original issue.
    """
    tenant_id = test_tenant_setup["tenant_id"]
    principal_id = test_tenant_setup["principal_id"]
    token = test_tenant_setup["token"]

    # Create media buy directly in database (bypassing in-memory dict)
    media_buy_id = "buy_integration_test_001"
    today = date.today()

    with get_db_session() as session:
        media_buy = MediaBuy(
            tenant_id=tenant_id,
            principal_id=principal_id,
            media_buy_id=media_buy_id,
            buyer_ref="original_ref",
            order_name="Test Order",
            advertiser_name="Test Advertiser",
            status="active",
            start_date=today,
            end_date=today + timedelta(days=30),
            start_time=today,
            end_time=today + timedelta(days=30),
            budget=1000.0,
            currency="USD",
            raw_request={},
        )
        session.add(media_buy)
        session.commit()

    # Set tenant context
    from src.core.config_loader import set_current_tenant

    set_current_tenant(
        {
            "tenant_id": tenant_id,
            "name": "Test Update Persist Tenant",
            "subdomain": "test-update-persist",
            "ad_server": "mock",
            "is_active": True,
        }
    )

    # Create mock context
    context = MockContext(tenant_id, principal_id, token)

    # Test: Call update_media_buy (should not raise "Media buy not found")
    response = _update_media_buy_impl(
        media_buy_id=media_buy_id,
        buyer_ref="updated_ref",
        ctx=context,
    )

    # Verify response
    assert isinstance(response, UpdateMediaBuyResponse)
    assert response.media_buy_id == media_buy_id
    # Note: buyer_ref update may not be reflected in response immediately
    # due to async workflow, but the key test is that it doesn't raise


@pytest.mark.requires_db
def test_update_media_buy_requires_context():
    """Test update_media_buy raises error when context is None."""
    # Note: This will first hit Pydantic validation if buyer_ref is also provided
    # So we only provide media_buy_id to avoid the oneOf constraint
    with pytest.raises(ValueError, match="Context is required"):
        _update_media_buy_impl(
            media_buy_id="buy_test_123",
            context=None,
        )


@pytest.mark.requires_db
def test_update_media_buy_requires_media_buy_id():
    """Test update_media_buy raises error when buyer_ref lookup fails."""
    # Create minimal mock context
    context = MagicMock()
    context.headers = {"x-adcp-auth": "test_token", "host": "test-tenant.test.com"}

    # Set tenant context (required by get_current_tenant)
    from src.core.config_loader import set_current_tenant

    set_current_tenant(
        {
            "tenant_id": "test_tenant_no_mb",
            "name": "Test Tenant",
            "subdomain": "test-tenant",
            "ad_server": "mock",
            "is_active": True,
        }
    )

    # Note: When media_buy_id is None and buyer_ref is provided,
    # we try to look it up in the database. If not found, we raise ValueError.
    # This tests the buyer_ref lookup path when the media buy doesn't exist.
    with pytest.raises(ValueError, match="Media buy with buyer_ref 'nonexistent_ref' not found"):
        _update_media_buy_impl(
            media_buy_id=None,
            buyer_ref="nonexistent_ref",
            ctx=context,
        )
