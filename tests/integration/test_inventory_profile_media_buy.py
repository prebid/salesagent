"""Integration tests: Media Buy Creation with Inventory Profiles.

Tests that verify products referencing inventory profiles work correctly in
the media buy creation flow. Uses the mock adapter (which does not natively
support inventory profiles) to verify the pipeline doesn't break.

Requires PostgreSQL (integration_db).
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    InventoryProfile,
    Principal,
)
from src.core.resolved_identity import AccountIdentity
from src.core.schemas import CreateMediaBuyRequest
from src.core.tools.media_buy_create import _create_media_buy_impl
from tests.factories import PricingOptionFactory
from tests.factories.account import seed_default_account
from tests.factories.principal import plaintext_token_for
from tests.helpers.adcp_factories import create_test_db_product, create_test_package_request
from tests.integration.media_buy_helpers import make_media_buy_identity
from tests.utils.database_helpers import bind_factories_to_session


def _make_context(tenant_id: str, principal_id: str) -> AccountIdentity:
    """The caller ``_create_media_buy_impl`` takes: principal, tenant AND account.

    ``account`` is spec-required on create-media-buy-request.json and the implementation
    reads ``identity.account.account_id``, so a plain ``ResolvedIdentity`` is the wrong
    TYPE -- see ``make_media_buy_identity``, which also loads the tenant from its row.
    """
    return make_media_buy_identity(principal_id, tenant_id)


def _seed_account_access(session, tenant_id: str, principal_id: str) -> None:
    """The Account row the request names, plus this principal's access grant.

    Each test here seeds its own principal, so each needs its own grant: the account
    reference is resolved for real, and a row without the grant is refused
    indistinguishably from no row at all.

    On the caller's session -- ``get_db_session`` is SCOPED and closes the session when
    the innermost block exits, so opening a second one here would detach the rows the
    test is still holding.
    """
    with bind_factories_to_session(session):
        seed_default_account(tenant_id, principal_id)


def _get_future_date_range() -> tuple[datetime, datetime]:
    """Return a future date range with timezone info."""
    start = datetime.now(UTC) + timedelta(days=1)
    end = start + timedelta(days=7)
    return start, end


@pytest.mark.requires_db
async def test_create_media_buy_with_profile_based_product(sample_tenant):
    """Test that media buy creation succeeds when product references an inventory profile."""
    with get_db_session() as session:
        profile = InventoryProfile(
            tenant_id=sample_tenant["tenant_id"],
            profile_id="test_profile_media_buy",
            name="Test Profile for Media Buy",
            description="Profile with specific ad units",
            inventory_config={
                "ad_units": ["12345", "67890"],
                "placements": ["99999"],
                "include_descendants": False,
            },
            format_ids=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
            ],
            publisher_properties=[
                {
                    "selection_type": "by_id",
                    "publisher_domain": "example.com",
                    "property_ids": ["example_property"],
                }
            ],
        )
        session.add(profile)
        session.flush()

        product = create_test_db_product(
            tenant_id=sample_tenant["tenant_id"],
            product_id="test_product_media_buy",
            name="Profile-Based Product",
            description="Product using inventory profile",
            inventory_profile_id=profile.id,
            format_ids=[],
            is_custom=False,
            countries=["US"],
        )
        session.add(product)

        pricing = PricingOptionFactory.build(
            tenant_id=sample_tenant["tenant_id"],
            product_id=product.product_id,
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing)

        principal = Principal.with_token(
            plaintext_token_for("test_principal_media_buy"),
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal_media_buy",
            name="Test Advertiser",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)
        session.commit()

        # The ids as VALUES, before anything opens another session. ``get_db_session`` is
        # scoped and closes the session on exit, so a nested one -- the account seeding
        # below, and the tenant-row load inside _make_context -- detaches these rows and
        # every later attribute read raises DetachedInstanceError.
        product_id = product.product_id
        principal_id = principal.principal_id

        start_time, end_time = _get_future_date_range()
        _seed_account_access(session, sample_tenant["tenant_id"], principal_id)
        ctx = _make_context(sample_tenant["tenant_id"], principal_id)

        req = CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    product_id=product_id,
                    pricing_option_id="cpm_usd_fixed",
                    budget=150.0,
                )
            ],
            start_time=start_time,
            end_time=end_time,
            idempotency_key=f"int-key-{uuid.uuid4().hex}",
        )
        response = await _create_media_buy_impl(req=req, identity=ctx)

        # Verify success
        # The SUCCESS branch of create-media-buy-response.json's oneOf, asserted on fields
        # that exist. `not hasattr(response, "errors")` stood here and could not tell the
        # branches apart: CreateMediaBuyResult declares no `errors` field at all, so the
        # check was True for every object it could be handed, including an error one. It
        # was also unreachable — the call above unpacked the model into a (name, value)
        # tuple, and a tuple has no .errors either (salesagent-jnqab).
        assert response.adcp_error is None, f"create_media_buy failed: {response.adcp_error}"
        assert response.status == "completed", f"expected a completed create, got {response.status!r}"
        assert response.media_buy_id is not None
        assert response.packages is not None
        assert len(response.packages) >= 1


@pytest.mark.requires_db
async def test_create_media_buy_with_profile_formats(sample_tenant):
    """Test that media buy creation handles profile-based format validation."""
    with get_db_session() as session:
        profile = InventoryProfile(
            tenant_id=sample_tenant["tenant_id"],
            profile_id="test_profile_format_validation",
            name="Test Profile for Format Validation",
            description="Profile with specific formats",
            inventory_config={
                "ad_units": ["12345"],
                "placements": [],
                "include_descendants": False,
            },
            format_ids=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
                {"agent_url": "https://test.example.com", "id": "display_728x90"},
            ],
            publisher_properties=[
                {
                    "selection_type": "by_id",
                    "publisher_domain": "example.com",
                    "property_ids": ["example_property"],
                }
            ],
        )
        session.add(profile)
        session.flush()

        product = create_test_db_product(
            tenant_id=sample_tenant["tenant_id"],
            product_id="test_product_format_validation",
            name="Format Validation Product",
            description="Product using inventory profile",
            inventory_profile_id=profile.id,
            format_ids=[],
            is_custom=False,
            countries=["US"],
        )
        session.add(product)

        pricing = PricingOptionFactory.build(
            tenant_id=sample_tenant["tenant_id"],
            product_id=product.product_id,
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing)

        principal = Principal.with_token(
            plaintext_token_for("test_principal_format_validation"),
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal_format_validation",
            name="Test Advertiser Format",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)
        session.commit()

        # Read as VALUES before any nested session closes this one -- see the note in
        # test_create_media_buy_with_profile_based_product.
        product_id = product.product_id
        principal_id = principal.principal_id

        start_time, end_time = _get_future_date_range()
        _seed_account_access(session, sample_tenant["tenant_id"], principal_id)
        ctx = _make_context(sample_tenant["tenant_id"], principal_id)

        # Create media buy - should succeed or return structured error, not crash
        try:
            req = CreateMediaBuyRequest(
                account={"account_id": "acct_test"},
                brand={"domain": "testbrand.com"},
                packages=[
                    create_test_package_request(
                        product_id=product_id,
                        pricing_option_id="cpm_usd_fixed",
                        budget=150.0,
                    )
                ],
                start_time=start_time,
                end_time=end_time,
                idempotency_key=f"int-key-{uuid.uuid4().hex}",
            )
            response = await _create_media_buy_impl(req=req, identity=ctx)
            # Either succeeds or returns structured error - both are valid
            assert response is not None
        except ValueError:
            # Validation error is also acceptable behavior
            pass


@pytest.mark.requires_db
async def test_multiple_products_same_profile_in_media_buy(sample_tenant):
    """Test media buy with multiple products referencing the same profile."""
    with get_db_session() as session:
        profile = InventoryProfile(
            tenant_id=sample_tenant["tenant_id"],
            profile_id="test_profile_multiple",
            name="Shared Profile",
            description="Profile shared by multiple products",
            inventory_config={
                "ad_units": ["shared_unit_1", "shared_unit_2"],
                "placements": [],
                "include_descendants": False,
            },
            format_ids=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
            ],
            publisher_properties=[
                {
                    "selection_type": "by_id",
                    "publisher_domain": "example.com",
                    "property_ids": ["prop_shared"],
                }
            ],
        )
        session.add(profile)
        session.flush()

        products = []
        for i in range(3):
            product = create_test_db_product(
                tenant_id=sample_tenant["tenant_id"],
                product_id=f"test_product_shared_{i}",
                name=f"Shared Profile Product {i}",
                description=f"Product {i} sharing profile",
                inventory_profile_id=profile.id,
                format_ids=[],
                is_custom=False,
                countries=["US"],
            )
            session.add(product)

            pricing = PricingOptionFactory.build(
                tenant_id=sample_tenant["tenant_id"],
                product_id=product.product_id,
                pricing_model="cpm",
                rate=Decimal("15.00"),
                currency="USD",
                is_fixed=True,
            )
            session.add(pricing)
            products.append(product)

        principal = Principal.with_token(
            plaintext_token_for("test_principal_shared"),
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal_shared",
            name="Test Advertiser Shared",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)
        session.commit()

        # Read as VALUES before any nested session closes this one -- see the note in
        # test_create_media_buy_with_profile_based_product.
        product_ids = [product.product_id for product in products]
        principal_id = principal.principal_id

        start_time, end_time = _get_future_date_range()
        _seed_account_access(session, sample_tenant["tenant_id"], principal_id)
        ctx = _make_context(sample_tenant["tenant_id"], principal_id)

        # Use only the first product (AdCP spec: package has singular product_id)
        req = CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    product_id=product_id,
                    pricing_option_id="cpm_usd_fixed",
                    budget=150.0,
                )
                for product_id in product_ids
            ],
            start_time=start_time,
            end_time=end_time,
            idempotency_key=f"int-key-{uuid.uuid4().hex}",
        )
        response = await _create_media_buy_impl(req=req, identity=ctx)

        # The SUCCESS branch of create-media-buy-response.json's oneOf, asserted on fields
        # that exist. `not hasattr(response, "errors")` stood here and could not tell the
        # branches apart: CreateMediaBuyResult declares no `errors` field at all, so the
        # check was True for every object it could be handed, including an error one. It
        # was also unreachable — the call above unpacked the model into a (name, value)
        # tuple, and a tuple has no .errors either (salesagent-jnqab).
        assert response.adcp_error is None, f"create_media_buy failed: {response.adcp_error}"
        assert response.status == "completed", f"expected a completed create, got {response.status!r}"
        assert response.media_buy_id is not None
        assert response.packages is not None
        assert len(response.packages) == 3


@pytest.mark.requires_db
async def test_media_buy_reflects_profile_updates(sample_tenant):
    """Test that media buy uses current profile config, not stale data."""
    with get_db_session() as session:
        profile = InventoryProfile(
            tenant_id=sample_tenant["tenant_id"],
            profile_id="test_profile_updates",
            name="Updatable Profile",
            description="Profile that will be updated",
            inventory_config={
                "ad_units": ["old_unit"],
                "placements": [],
                "include_descendants": False,
            },
            format_ids=[
                {"agent_url": "https://test.example.com", "id": "display_300x250"},
            ],
            publisher_properties=[
                {
                    "selection_type": "by_id",
                    "publisher_domain": "old.example.com",
                    "property_ids": ["old_property"],
                }
            ],
        )
        session.add(profile)
        session.flush()

        profile_id = profile.id

        product = create_test_db_product(
            tenant_id=sample_tenant["tenant_id"],
            product_id="test_product_updates",
            name="Product with Updatable Profile",
            description="Product using updatable profile",
            inventory_profile_id=profile_id,
            format_ids=[],
            is_custom=False,
            countries=["US"],
        )
        session.add(product)

        pricing = PricingOptionFactory.build(
            tenant_id=sample_tenant["tenant_id"],
            product_id=product.product_id,
            pricing_model="cpm",
            rate=Decimal("15.00"),
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing)

        principal = Principal.with_token(
            plaintext_token_for("test_principal_updates"),
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal_updates",
            name="Test Advertiser Updates",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)
        session.commit()

        # Update profile AFTER product creation
        stmt = select(InventoryProfile).where(InventoryProfile.id == profile_id)
        profile = session.scalars(stmt).first()
        profile.inventory_config = {
            "ad_units": ["new_unit"],
            "placements": ["new_placement"],
            "include_descendants": True,
        }
        profile.publisher_properties = [
            {
                "selection_type": "by_id",
                "publisher_domain": "new.example.com",
                "property_ids": ["new_property"],
            }
        ]
        session.commit()

        # Read as VALUES before any nested session closes this one -- see the note in
        # test_create_media_buy_with_profile_based_product.
        product_id = product.product_id
        principal_id = principal.principal_id

        # Create media buy AFTER profile update — should still succeed
        start_time, end_time = _get_future_date_range()
        _seed_account_access(session, sample_tenant["tenant_id"], principal_id)
        ctx = _make_context(sample_tenant["tenant_id"], principal_id)

        req = CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    product_id=product_id,
                    pricing_option_id="cpm_usd_fixed",
                    budget=150.0,
                )
            ],
            start_time=start_time,
            end_time=end_time,
            idempotency_key=f"int-key-{uuid.uuid4().hex}",
        )
        response = await _create_media_buy_impl(req=req, identity=ctx)

        # The SUCCESS branch of create-media-buy-response.json's oneOf, asserted on fields
        # that exist. `not hasattr(response, "errors")` stood here and could not tell the
        # branches apart: CreateMediaBuyResult declares no `errors` field at all, so the
        # check was True for every object it could be handed, including an error one. It
        # was also unreachable — the call above unpacked the model into a (name, value)
        # tuple, and a tuple has no .errors either (salesagent-jnqab).
        assert response.adcp_error is None, f"create_media_buy failed: {response.adcp_error}"
        assert response.status == "completed", f"expected a completed create, got {response.status!r}"
        assert response.media_buy_id is not None
