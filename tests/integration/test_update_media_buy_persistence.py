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
from src.core.main import _update_media_buy_impl, _verify_principal
from src.core.schemas import UpdateMediaBuyResponse


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
            auto_approve_formats=[],
            policy_settings={},
        )
        session.add(tenant)

        # Create principal
        principal = ModelPrincipal(
            tenant_id=tenant_id,
            principal_id=principal_id,
            name="Test Advertiser Persist",
            access_token=token,
            platform_mappings={"mock_ad_server": {"advertiser_id": "adv_persist"}},
        )
        session.add(principal)

        # Create currency limit (required for budget validation)
        currency_limit = CurrencyLimit(
            tenant_id=tenant_id,
            currency_code="USD",
            max_daily_spend=10000.0,
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
def test_verify_principal_finds_media_buy_in_database(test_tenant_setup):
    """Test _verify_principal finds media buy in database (not in-memory dict)."""
    tenant_id = test_tenant_setup["tenant_id"]
    principal_id = test_tenant_setup["principal_id"]
    token = test_tenant_setup["token"]

    # Create media buy directly in database (bypassing in-memory dict)
    media_buy_id = "buy_verify_test_001"
    today = date.today()

    with get_db_session() as session:
        media_buy = MediaBuy(
            tenant_id=tenant_id,
            principal_id=principal_id,
            media_buy_id=media_buy_id,
            buyer_ref="verify_test_ref",
            status="active",
            start_time=today,
            end_time=today + timedelta(days=30),
            total_budget=1000.0,
            currency="USD",
            config={},
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

    # Test: _verify_principal should find media buy in database
    try:
        _verify_principal(media_buy_id, context)
        # Should not raise - success!
    except ValueError as e:
        pytest.fail(f"_verify_principal raised ValueError: {e}")
    except PermissionError as e:
        pytest.fail(f"_verify_principal raised PermissionError: {e}")


@pytest.mark.requires_db
def test_verify_principal_rejects_wrong_principal(test_tenant_setup):
    """Test _verify_principal rejects access from wrong principal."""
    tenant_id = test_tenant_setup["tenant_id"]
    principal_id = test_tenant_setup["principal_id"]

    # Create second principal (attacker)
    attacker_id = "test_attacker_persist"
    attacker_token = "test_token_attacker_123"

    with get_db_session() as session:
        attacker = ModelPrincipal(
            tenant_id=tenant_id,
            principal_id=attacker_id,
            name="Test Attacker Persist",
            access_token=attacker_token,
            platform_mappings={"mock_ad_server": {"advertiser_id": "adv_attacker"}},
        )
        session.add(attacker)

        # Create media buy owned by original principal
        media_buy_id = "buy_security_test_001"
        today = date.today()
        media_buy = MediaBuy(
            tenant_id=tenant_id,
            principal_id=principal_id,  # Owned by original principal
            media_buy_id=media_buy_id,
            buyer_ref="security_test_ref",
            status="active",
            start_time=today,
            end_time=today + timedelta(days=30),
            total_budget=1000.0,
            currency="USD",
            config={},
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

    # Create mock context for attacker
    context = MockContext(tenant_id, attacker_id, attacker_token)

    # Test: _verify_principal should reject attacker
    with pytest.raises(PermissionError, match="does not own media buy"):
        _verify_principal(media_buy_id, context)

    # Cleanup attacker principal
    with get_db_session() as session:
        session.query(ModelPrincipal).filter_by(principal_id=attacker_id).delete()
        session.commit()


@pytest.mark.requires_db
def test_verify_principal_raises_on_nonexistent_media_buy(test_tenant_setup):
    """Test _verify_principal raises ValueError for non-existent media buy."""
    tenant_id = test_tenant_setup["tenant_id"]
    principal_id = test_tenant_setup["principal_id"]
    token = test_tenant_setup["token"]

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

    # Test: _verify_principal should raise ValueError
    with pytest.raises(ValueError, match="Media buy.*not found"):
        _verify_principal("buy_nonexistent_999", context)


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
            status="active",
            start_time=today,
            end_time=today + timedelta(days=30),
            total_budget=1000.0,
            currency="USD",
            config={},
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
        context=context,
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
    """Test update_media_buy raises error when media_buy_id is None."""
    # Create minimal mock context
    context = MagicMock()
    context.headers = {"x-adcp-auth": "test_token"}

    # Note: Pydantic requires at least one of media_buy_id or buyer_ref
    # So this test actually validates Pydantic's validation, not our code
    # We pass buyer_ref to satisfy Pydantic's oneOf constraint
    with pytest.raises(ValueError, match="media_buy_id is required"):
        _update_media_buy_impl(
            media_buy_id=None,
            buyer_ref="test_ref",
            context=context,
        )
