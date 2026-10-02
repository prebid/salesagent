"""Integration tests for update_media_buy with database persistence.

Tests the fix for the issue where update_media_buy failed with "Media buy not found"
when the media buy existed in the database but not in the in-memory media_buys dictionary.

This verifies that _verify_principal() queries the database instead of checking
the in-memory dictionary.
"""

from datetime import date, timedelta

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
from src.core.exceptions import AdCPMediaBuyNotFoundError
from src.core.resolved_identity import AccountIdentity
from src.core.schemas import UpdateMediaBuyRequest, UpdateMediaBuyResponse, UpdateMediaBuyResult
from src.core.schemas.account import Account
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.factories.principal import PrincipalFactory, plaintext_token_for
from tests.utils.database_helpers import vhost_for

# Note: _verify_principal is now internal to _update_media_buy_impl
# Tests that used _verify_principal directly will need to test through the public API

_ACCOUNT_ID = "acct_test"


def _make_identity(tenant_id: str, principal_id: str) -> AccountIdentity:
    """The caller ``_update_media_buy_impl`` takes: an identity with the account inside.

    ``update-media-buy-request.json`` requires ``account``, so the implementation is
    annotated ``AccountIdentity`` and reads ``identity.account``; the boundary resolved the
    reference before the tool ran.
    """
    return PrincipalFactory.make_account_identity(
        PrincipalFactory.make_identity(principal_id=principal_id, tenant_id=tenant_id, tenant={"tenant_id": tenant_id}),
        Account(account_id=_ACCOUNT_ID, name="Test Account", status="active"),
    )


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
            virtual_host=vhost_for(tenant_id),
            ad_server="mock",
            is_active=True,
            human_review_required=False,
            auto_approve_format_ids=[],
            policy_settings={},
        )
        session.add(tenant)

        # Create principal
        principal = ModelPrincipal.with_token(
            plaintext_token_for(principal_id),
            tenant_id=tenant_id,
            principal_id=principal_id,
            name="Test Advertiser Persist",
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

    # No ambient tenant to set: the tenant travels on the identity.
    identity = _make_identity(tenant_id, principal_id)

    # Test: Call update_media_buy (should not raise "Media buy not found")
    req = UpdateMediaBuyRequest(
        account={"account_id": "acct_test"},
        idempotency_key="test-idem-key-0001",
        media_buy_id=media_buy_id,
    )
    result = _update_media_buy_impl(req=req, identity=identity)

    # Verify response
    assert isinstance(result, UpdateMediaBuyResult)
    response = result  # _impl returns UpdateMediaBuyResult; domain response is on .response
    assert isinstance(response, UpdateMediaBuyResponse)
    assert response.media_buy_id == media_buy_id


# (Retired) test_update_media_buy_requires_context called the implementation with no
# identity at all and expected it to mint AdCPAuthenticationError. Nothing can call it that
# way now: the parameter is required and typed ``AccountIdentity``, so an anonymous caller
# is refused by the resolver before the tool runs -- the refusal has ONE minting site and is
# graded on the wire (BR-UC-003 auth scenarios), not by a tool re-checking it.


@pytest.mark.requires_db
def test_update_media_buy_requires_media_buy_id(test_tenant_setup):
    """Test update_media_buy raises error when media_buy_id is missing."""
    # Use valid authentication from fixture (required after auth ordering fix)
    identity = _make_identity(
        tenant_id=test_tenant_setup["tenant_id"],
        principal_id=test_tenant_setup["principal_id"],
    )

    # media_buy_id that doesn't exist should raise AdCPMediaBuyNotFoundError
    with pytest.raises(AdCPMediaBuyNotFoundError):
        req = UpdateMediaBuyRequest(
            account={"account_id": "acct_test"}, idempotency_key="test-idem-key-0001", media_buy_id="nonexistent_ref"
        )
        _update_media_buy_impl(req=req, identity=identity)
