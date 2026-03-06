"""Integration tests for ProductRepository.

Verifies that ProductRepository provides tenant-scoped, typed access to Product
entities with proper eager loading of pricing_options and related relationships.

Core invariant: All product DB access in _impl functions goes through
ProductRepository; no get_db_session() in business logic for product queries.

beads: salesagent-rn59
"""

import uuid
from datetime import UTC, datetime

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import PricingOption as PricingOptionModel
from src.core.database.models import Product as ProductModel
from src.core.database.models import Tenant as TenantModel
from src.core.product_conversion import convert_product_model_to_schema
from src.core.schemas import Product as ProductSchema


def _create_test_tenant(session, unique_id: str) -> TenantModel:
    """Create a minimal test tenant."""
    now = datetime.now(UTC)
    tenant = TenantModel(
        tenant_id=f"test-tenant-{unique_id}",
        name=f"Test Tenant {unique_id}",
        subdomain=f"test-{unique_id}",
        virtual_host=f"test-{unique_id}.example.com",
        is_active=True,
        ad_server="mock",
        created_at=now,
        updated_at=now,
    )
    session.add(tenant)
    session.flush()
    return tenant


def _create_test_product(
    session,
    tenant_id: str,
    product_id: str,
    *,
    name: str = "Test Product",
    delivery_type: str = "guaranteed",
    with_pricing: bool = True,
    pricing_model: str = "cpm",
    rate: float = 10.00,
    currency: str = "USD",
) -> ProductModel:
    """Create a test product with optional pricing."""
    product = ProductModel(
        tenant_id=tenant_id,
        product_id=product_id,
        name=name,
        description=f"Description for {name}",
        format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
        targeting_template={},
        delivery_type=delivery_type,
        property_tags=["all_inventory"],
        delivery_measurement={"provider": "publisher", "notes": "Test measurement"},
    )
    session.add(product)
    session.flush()

    if with_pricing:
        pricing = PricingOptionModel(
            tenant_id=tenant_id,
            product_id=product_id,
            pricing_model=pricing_model,
            rate=rate,
            currency=currency,
            is_fixed=True,
        )
        session.add(pricing)
        session.flush()

    return product


@pytest.mark.requires_db
class TestProductRepositoryGetAllForTenant:
    """ProductRepository.get_all_for_tenant returns all products with eager-loaded relationships."""

    def test_returns_all_products_for_tenant(self, integration_db):
        """Repository returns all products belonging to the tenant."""
        from src.core.database.repositories.product import ProductRepository

        uid = uuid.uuid4().hex[:8]

        with get_db_session() as session:
            tenant = _create_test_tenant(session, uid)
            _create_test_product(session, tenant.tenant_id, f"prod-1-{uid}")
            _create_test_product(session, tenant.tenant_id, f"prod-2-{uid}")
            session.commit()

        with get_db_session() as session:
            repo = ProductRepository(session, f"test-tenant-{uid}")
            products = repo.get_all_for_tenant()

        assert len(products) == 2
        product_ids = {p.product_id for p in products}
        assert f"prod-1-{uid}" in product_ids
        assert f"prod-2-{uid}" in product_ids

    def test_tenant_isolation(self, integration_db):
        """Repository only returns products for its tenant, not other tenants."""
        from src.core.database.repositories.product import ProductRepository

        uid = uuid.uuid4().hex[:8]
        tenant_a_id = f"test-tenant-{uid}-a"
        tenant_b_id = f"test-tenant-{uid}-b"

        with get_db_session() as session:
            _create_test_tenant(session, f"{uid}-a")
            _create_test_tenant(session, f"{uid}-b")
            _create_test_product(session, tenant_a_id, f"prod-a-{uid}")
            _create_test_product(session, tenant_b_id, f"prod-b-{uid}")
            session.commit()

        with get_db_session() as session:
            repo_a = ProductRepository(session, tenant_a_id)
            products_a = repo_a.get_all_for_tenant()

        assert len(products_a) == 1
        assert products_a[0].product_id == f"prod-a-{uid}"

    def test_eager_loads_pricing_options(self, integration_db):
        """Repository eager-loads pricing_options so they are accessible outside session."""
        from src.core.database.repositories.product import ProductRepository

        uid = uuid.uuid4().hex[:8]

        with get_db_session() as session:
            tenant = _create_test_tenant(session, uid)
            _create_test_product(session, tenant.tenant_id, f"prod-{uid}", rate=15.50)
            session.commit()

        with get_db_session() as session:
            repo = ProductRepository(session, f"test-tenant-{uid}")
            products = repo.get_all_for_tenant()

        # Access pricing_options OUTSIDE the session — must be eager-loaded
        assert len(products) == 1
        product = products[0]
        assert product.pricing_options is not None
        assert len(product.pricing_options) > 0
        assert product.pricing_options[0].pricing_model == "cpm"
        assert float(product.pricing_options[0].rate) == 15.50

    def test_products_convertible_to_schema(self, integration_db):
        """Products returned by repository can be converted to AdCP schema."""
        from src.core.database.repositories.product import ProductRepository

        uid = uuid.uuid4().hex[:8]

        with get_db_session() as session:
            tenant = _create_test_tenant(session, uid)
            _create_test_product(session, tenant.tenant_id, f"prod-{uid}")
            session.commit()

        with get_db_session() as session:
            repo = ProductRepository(session, f"test-tenant-{uid}")
            products = repo.get_all_for_tenant()

        # Should convert to Pydantic schema without errors
        schema = convert_product_model_to_schema(products[0])
        assert isinstance(schema, ProductSchema)
        assert schema.product_id == f"prod-{uid}"
        assert len(schema.pricing_options) > 0


@pytest.mark.requires_db
class TestProductRepositoryGetById:
    """ProductRepository.get_by_id returns a single product with eager loading."""

    def test_returns_product_by_id(self, integration_db):
        """Repository returns the requested product."""
        from src.core.database.repositories.product import ProductRepository

        uid = uuid.uuid4().hex[:8]

        with get_db_session() as session:
            tenant = _create_test_tenant(session, uid)
            _create_test_product(session, tenant.tenant_id, f"prod-{uid}")
            session.commit()

        with get_db_session() as session:
            repo = ProductRepository(session, f"test-tenant-{uid}")
            product = repo.get_by_id(f"prod-{uid}")

        assert product is not None
        assert product.product_id == f"prod-{uid}"

    def test_returns_none_for_nonexistent(self, integration_db):
        """Repository returns None when product doesn't exist."""
        from src.core.database.repositories.product import ProductRepository

        uid = uuid.uuid4().hex[:8]

        with get_db_session() as session:
            _create_test_tenant(session, uid)
            session.commit()

        with get_db_session() as session:
            repo = ProductRepository(session, f"test-tenant-{uid}")
            product = repo.get_by_id("nonexistent")

        assert product is None

    def test_tenant_isolation_on_get_by_id(self, integration_db):
        """Repository does not return products from other tenants."""
        from src.core.database.repositories.product import ProductRepository

        uid = uuid.uuid4().hex[:8]
        tenant_a_id = f"test-tenant-{uid}-a"
        tenant_b_id = f"test-tenant-{uid}-b"

        with get_db_session() as session:
            _create_test_tenant(session, f"{uid}-a")
            _create_test_tenant(session, f"{uid}-b")
            _create_test_product(session, tenant_b_id, f"prod-{uid}")
            session.commit()

        with get_db_session() as session:
            repo_a = ProductRepository(session, tenant_a_id)
            product = repo_a.get_by_id(f"prod-{uid}")

        assert product is None  # Product belongs to tenant_b, not tenant_a
