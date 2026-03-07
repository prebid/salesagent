"""Integration tests for ProductRepository.

Verifies that ProductRepository provides tenant-scoped, typed access to Product
entities with proper eager loading of pricing_options and related relationships.

Core invariant: All product DB access in _impl functions goes through
ProductRepository; no get_db_session() in business logic for product queries.

beads: salesagent-rn59, salesagent-jfgm
"""

import uuid
from datetime import UTC, datetime

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import PricingOption as PricingOptionModel
from src.core.database.models import Product as ProductModel
from src.core.database.models import Tenant as TenantModel
from src.core.database.repositories.product import ProductRepository
from src.core.database.repositories.uow import ProductUoW
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

        uid = uuid.uuid4().hex[:8]

        with get_db_session() as session:
            tenant = _create_test_tenant(session, uid)
            _create_test_product(session, tenant.tenant_id, f"prod-1-{uid}")
            _create_test_product(session, tenant.tenant_id, f"prod-2-{uid}")
            session.commit()

        with get_db_session() as session:
            repo = ProductRepository(session, f"test-tenant-{uid}")
            products = repo.list_all()

        assert len(products) == 2
        product_ids = {p.product_id for p in products}
        assert f"prod-1-{uid}" in product_ids
        assert f"prod-2-{uid}" in product_ids

    def test_tenant_isolation(self, integration_db):
        """Repository only returns products for its tenant, not other tenants."""

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
            products_a = repo_a.list_all()

        assert len(products_a) == 1
        assert products_a[0].product_id == f"prod-a-{uid}"

    def test_eager_loads_pricing_options(self, integration_db):
        """Repository eager-loads pricing_options so they are accessible outside session."""

        uid = uuid.uuid4().hex[:8]

        with get_db_session() as session:
            tenant = _create_test_tenant(session, uid)
            _create_test_product(session, tenant.tenant_id, f"prod-{uid}", rate=15.50)
            session.commit()

        with get_db_session() as session:
            repo = ProductRepository(session, f"test-tenant-{uid}")
            products = repo.list_all()

        # Access pricing_options OUTSIDE the session — must be eager-loaded
        assert len(products) == 1
        product = products[0]
        assert product.pricing_options is not None
        assert len(product.pricing_options) > 0
        assert product.pricing_options[0].pricing_model == "cpm"
        assert float(product.pricing_options[0].rate) == 15.50

    def test_products_convertible_to_schema(self, integration_db):
        """Products returned by repository can be converted to AdCP schema."""

        uid = uuid.uuid4().hex[:8]

        with get_db_session() as session:
            tenant = _create_test_tenant(session, uid)
            _create_test_product(session, tenant.tenant_id, f"prod-{uid}")
            session.commit()

        with get_db_session() as session:
            repo = ProductRepository(session, f"test-tenant-{uid}")
            products = repo.list_all()

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


@pytest.mark.requires_db
class TestProductRepositoryGetByIdWithPricing:
    """ProductRepository.get_by_id_with_pricing returns a product with pricing_options eagerly loaded."""

    def test_returns_product_with_pricing_loaded(self, integration_db):
        """Product is returned with pricing_options eagerly loaded and accessible outside session."""

        uid = uuid.uuid4().hex[:8]

        with get_db_session() as session:
            tenant = _create_test_tenant(session, uid)
            _create_test_product(session, tenant.tenant_id, f"prod-{uid}", rate=25.00, pricing_model="cpm")
            session.commit()

        with get_db_session() as session:
            repo = ProductRepository(session, f"test-tenant-{uid}")
            product = repo.get_by_id_with_pricing(f"prod-{uid}")

        # Access pricing_options OUTSIDE the session — must be eager-loaded
        assert product is not None
        assert product.product_id == f"prod-{uid}"
        assert product.pricing_options is not None
        assert len(product.pricing_options) == 1
        assert product.pricing_options[0].pricing_model == "cpm"
        assert float(product.pricing_options[0].rate) == 25.00

    def test_returns_none_for_nonexistent_product(self, integration_db):
        """Returns None when product ID does not exist in the tenant."""

        uid = uuid.uuid4().hex[:8]

        with get_db_session() as session:
            _create_test_tenant(session, uid)
            session.commit()

        with get_db_session() as session:
            repo = ProductRepository(session, f"test-tenant-{uid}")
            product = repo.get_by_id_with_pricing("nonexistent")

        assert product is None


@pytest.mark.requires_db
class TestProductRepositoryListByIds:
    """ProductRepository.list_by_ids returns products matching the given IDs."""

    def test_returns_matching_products(self, integration_db):
        """Returns only the products whose IDs are in the input list."""

        uid = uuid.uuid4().hex[:8]

        with get_db_session() as session:
            tenant = _create_test_tenant(session, uid)
            _create_test_product(session, tenant.tenant_id, f"prod-1-{uid}")
            _create_test_product(session, tenant.tenant_id, f"prod-2-{uid}")
            _create_test_product(session, tenant.tenant_id, f"prod-3-{uid}")
            session.commit()

        with get_db_session() as session:
            repo = ProductRepository(session, f"test-tenant-{uid}")
            products = repo.list_by_ids([f"prod-1-{uid}", f"prod-3-{uid}"])

        assert len(products) == 2
        product_ids = {p.product_id for p in products}
        assert f"prod-1-{uid}" in product_ids
        assert f"prod-3-{uid}" in product_ids
        assert f"prod-2-{uid}" not in product_ids

    def test_returns_empty_list_for_empty_input(self, integration_db):
        """Returns empty list immediately when given an empty ID list."""

        uid = uuid.uuid4().hex[:8]

        with get_db_session() as session:
            tenant = _create_test_tenant(session, uid)
            _create_test_product(session, tenant.tenant_id, f"prod-{uid}")
            session.commit()

        with get_db_session() as session:
            repo = ProductRepository(session, f"test-tenant-{uid}")
            products = repo.list_by_ids([])

        assert products == []

    def test_returns_empty_list_for_nonexistent_ids(self, integration_db):
        """Returns empty list when none of the IDs exist."""

        uid = uuid.uuid4().hex[:8]

        with get_db_session() as session:
            _create_test_tenant(session, uid)
            session.commit()

        with get_db_session() as session:
            repo = ProductRepository(session, f"test-tenant-{uid}")
            products = repo.list_by_ids(["nonexistent-1", "nonexistent-2"])

        assert products == []


@pytest.mark.requires_db
class TestProductRepositoryCreate:
    """ProductRepository.create persists products with tenant validation."""

    def test_roundtrip_create_and_read_back(self, integration_db):
        """Create a product via UoW, read it back in a fresh session."""
        uid = uuid.uuid4().hex[:8]
        tenant_id = f"test-tenant-{uid}"

        with get_db_session() as session:
            _create_test_tenant(session, uid)
            session.commit()

        with ProductUoW(tenant_id) as uow:
            product = ProductModel(
                tenant_id=tenant_id,
                product_id=f"prod-create-{uid}",
                name="Created Product",
                description="Test creation",
                format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
                targeting_template={},
                delivery_type="guaranteed",
                property_tags=["all_inventory"],
                delivery_measurement={"provider": "publisher", "notes": "Test"},
            )
            result = uow.products.create(product)
            assert result is product

        with get_db_session() as session:
            repo = ProductRepository(session, tenant_id)
            fetched = repo.get_by_id(f"prod-create-{uid}")

        assert fetched is not None
        assert fetched.product_id == f"prod-create-{uid}"
        assert fetched.name == "Created Product"

    def test_tenant_mismatch_raises_valueerror(self, integration_db):
        """Creating a product with wrong tenant_id raises ValueError."""
        uid = uuid.uuid4().hex[:8]
        tenant_a_id = f"test-tenant-{uid}-a"
        tenant_b_id = f"test-tenant-{uid}-b"

        with get_db_session() as session:
            _create_test_tenant(session, f"{uid}-a")
            _create_test_tenant(session, f"{uid}-b")
            session.commit()

        with pytest.raises(ValueError, match="Tenant mismatch"):
            with ProductUoW(tenant_a_id) as uow:
                product = ProductModel(
                    tenant_id=tenant_b_id,
                    product_id=f"prod-mismatch-{uid}",
                    name="Mismatched",
                    format_ids=[],
                    targeting_template={},
                    delivery_type="guaranteed",
                    property_tags=[],
                    delivery_measurement={"provider": "publisher", "notes": "Test"},
                )
                uow.products.create(product)

    def test_tenant_isolation_on_create(self, integration_db):
        """Product created in tenant A is not visible from tenant B."""
        uid = uuid.uuid4().hex[:8]
        tenant_a_id = f"test-tenant-{uid}-a"
        tenant_b_id = f"test-tenant-{uid}-b"

        with get_db_session() as session:
            _create_test_tenant(session, f"{uid}-a")
            _create_test_tenant(session, f"{uid}-b")
            session.commit()

        with ProductUoW(tenant_a_id) as uow:
            product = ProductModel(
                tenant_id=tenant_a_id,
                product_id=f"prod-iso-{uid}",
                name="Isolated Product",
                format_ids=[],
                targeting_template={},
                delivery_type="guaranteed",
                property_tags=[],
                delivery_measurement={"provider": "publisher", "notes": "Test"},
            )
            uow.products.create(product)

        with get_db_session() as session:
            repo_b = ProductRepository(session, tenant_b_id)
            assert repo_b.get_by_id(f"prod-iso-{uid}") is None


@pytest.mark.requires_db
class TestProductRepositoryUpdateFields:
    """ProductRepository.update_fields updates product attributes with validation."""

    def test_roundtrip_update_fields(self, integration_db):
        """Update a field, read back in fresh session to verify persistence."""
        uid = uuid.uuid4().hex[:8]
        tenant_id = f"test-tenant-{uid}"

        with get_db_session() as session:
            tenant = _create_test_tenant(session, uid)
            _create_test_product(session, tenant.tenant_id, f"prod-{uid}", name="Original")
            session.commit()

        with ProductUoW(tenant_id) as uow:
            result = uow.products.update_fields(f"prod-{uid}", name="Updated Name")
            assert result is not None
            assert result.name == "Updated Name"

        with get_db_session() as session:
            repo = ProductRepository(session, tenant_id)
            fetched = repo.get_by_id(f"prod-{uid}")

        assert fetched.name == "Updated Name"

    def test_nonexistent_returns_none(self, integration_db):
        """Updating a nonexistent product returns None."""
        uid = uuid.uuid4().hex[:8]
        tenant_id = f"test-tenant-{uid}"

        with get_db_session() as session:
            _create_test_tenant(session, uid)
            session.commit()

        with ProductUoW(tenant_id) as uow:
            result = uow.products.update_fields("nonexistent", name="Updated")

        assert result is None

    def test_invalid_attribute_raises_valueerror(self, integration_db):
        """Updating a nonexistent attribute raises ValueError."""
        uid = uuid.uuid4().hex[:8]
        tenant_id = f"test-tenant-{uid}"

        with get_db_session() as session:
            tenant = _create_test_tenant(session, uid)
            _create_test_product(session, tenant.tenant_id, f"prod-{uid}")
            session.commit()

        with pytest.raises(ValueError, match="no attribute"):
            with ProductUoW(tenant_id) as uow:
                uow.products.update_fields(f"prod-{uid}", nonexistent_field="bad")

    def test_tenant_isolation_on_update(self, integration_db):
        """Cannot update a product belonging to another tenant."""
        uid = uuid.uuid4().hex[:8]
        tenant_a_id = f"test-tenant-{uid}-a"
        tenant_b_id = f"test-tenant-{uid}-b"

        with get_db_session() as session:
            _create_test_tenant(session, f"{uid}-a")
            _create_test_tenant(session, f"{uid}-b")
            _create_test_product(session, tenant_a_id, f"prod-{uid}")
            session.commit()

        with ProductUoW(tenant_b_id) as uow:
            result = uow.products.update_fields(f"prod-{uid}", name="Hacked")

        assert result is None

        with get_db_session() as session:
            repo = ProductRepository(session, tenant_a_id)
            fetched = repo.get_by_id(f"prod-{uid}")
        assert fetched.name == "Test Product"


@pytest.mark.requires_db
class TestProductRepositoryListAllWithInventory:
    """ProductRepository.list_all_with_inventory returns products with inventory relationship loaded."""

    def test_returns_products_with_pricing_loaded(self, integration_db):
        """Products returned have pricing_options accessible outside session."""
        uid = uuid.uuid4().hex[:8]

        with get_db_session() as session:
            tenant = _create_test_tenant(session, uid)
            _create_test_product(session, tenant.tenant_id, f"prod-{uid}", rate=12.50)
            session.commit()

        with get_db_session() as session:
            repo = ProductRepository(session, f"test-tenant-{uid}")
            products = repo.list_all_with_inventory()

        assert len(products) == 1
        product = products[0]
        assert len(product.pricing_options) == 1
        assert float(product.pricing_options[0].rate) == 12.50

    def test_tenant_isolation(self, integration_db):
        """Only returns products for the repository's tenant."""
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
            products_a = repo_a.list_all_with_inventory()

        assert len(products_a) == 1
        assert products_a[0].product_id == f"prod-a-{uid}"

    def test_empty_tenant_returns_empty_list(self, integration_db):
        """Returns empty list when tenant has no products."""
        uid = uuid.uuid4().hex[:8]

        with get_db_session() as session:
            _create_test_tenant(session, uid)
            session.commit()

        with get_db_session() as session:
            repo = ProductRepository(session, f"test-tenant-{uid}")
            products = repo.list_all_with_inventory()

        assert products == []
