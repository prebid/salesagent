"""Database helper utilities for tests.

Provides consistent patterns for creating test database objects with proper
timestamp handling and field validation to prevent common test issues.
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete

from src.core.database.models import (
    CurrencyLimit,
    Principal,
    Product,
    Tenant,
)


@contextmanager
def _bind_factories_to_session(session):
    """Temporarily bind factory_boy factories to the given session.

    Lets module-level helpers below use factories outside the IntegrationEnv
    harness without touching global state permanently. Factories are unbound
    on exit so subsequent harness contexts (which assert sqlalchemy_session is
    None) don't see leaked bindings.
    """
    from tests.factories import ALL_FACTORIES

    previous = [f._meta.sqlalchemy_session for f in ALL_FACTORIES]
    for f in ALL_FACTORIES:
        f._meta.sqlalchemy_session = session
    try:
        yield
    finally:
        for f, prev in zip(ALL_FACTORIES, previous, strict=True):
            f._meta.sqlalchemy_session = prev


def get_utc_now():
    """Get current UTC datetime for consistent timestamp creation."""
    return datetime.now(UTC)


def future_iso_date_range(days_ahead: int = 1, duration_days: int = 30) -> tuple[str, str]:
    """Return (start, end) ISO 8601 timestamps for tests that need future dates.

    Targets the ``YYYY-MM-DDTHH:MM:SSZ`` shape AdCP request validators accept.
    Starts at ``days_ahead`` from now and runs ``duration_days``.
    """
    start = datetime.now(UTC) + timedelta(days=days_ahead)
    end = start + timedelta(days=duration_days)
    return start.strftime("%Y-%m-%dT00:00:00Z"), end.strftime("%Y-%m-%dT23:59:59Z")


def create_tenant_with_timestamps(
    tenant_id: str, name: str, subdomain: str, billing_plan: str = "test", **kwargs: Any
) -> Tenant:
    """Create a Tenant object with proper timestamp fields.

    This helper ensures all Tenant objects are created with required
    created_at and updated_at fields, preventing NotNullViolation errors
    in tests.

    Args:
        tenant_id: Unique tenant identifier
        name: Human-readable tenant name
        subdomain: Subdomain for tenant routing
        billing_plan: Billing plan type (defaults to "test")
        **kwargs: Additional fields to pass to Tenant constructor

    Returns:
        Tenant object with proper timestamp fields

    Example:
        tenant = create_tenant_with_timestamps(
            tenant_id="test_tenant_001",
            name="Test Tenant",
            subdomain="test-tenant"
        )
    """
    now = datetime.now(UTC)

    # Ensure we have required timestamp fields
    kwargs.setdefault("created_at", now)
    kwargs.setdefault("updated_at", now)

    return Tenant(tenant_id=tenant_id, name=name, subdomain=subdomain, billing_plan=billing_plan, **kwargs)


def create_principal_with_platform_mappings(
    tenant_id: str,
    principal_id: str,
    name: str,
    access_token: str,
    platform_mappings: dict[str, Any] = None,
    **kwargs: Any,
) -> Principal:
    """Create a Principal object with valid platform mappings.

    This helper ensures Principal objects are created with proper
    platform_mappings that pass validation, using sensible defaults.

    Args:
        tenant_id: Associated tenant ID
        principal_id: Unique principal identifier
        name: Human-readable principal name
        access_token: Authentication token
        platform_mappings: Platform adapter mappings (defaults to mock adapter)
        **kwargs: Additional fields to pass to Principal constructor

    Returns:
        Principal object with valid platform mappings

    Example:
        principal = create_principal_with_platform_mappings(
            tenant_id="test_tenant_001",
            principal_id="test_principal_001",
            name="Test Principal",
            access_token="test_token_123"
        )
    """
    if platform_mappings is None:
        # Default to mock adapter with test advertiser
        platform_mappings = {"mock": {"advertiser_id": "test_advertiser"}}

    return Principal(
        tenant_id=tenant_id,
        principal_id=principal_id,
        name=name,
        access_token=access_token,
        platform_mappings=platform_mappings,
        **kwargs,
    )


def create_test_product(
    tenant_id: str, product_id: str, name: str, description: str, format_ids: list[dict] = None, **kwargs: Any
) -> Product:
    """Create a Product object with sensible test defaults.

    This helper ensures Product objects are created with all required
    fields and sensible defaults for testing.

    Args:
        tenant_id: Associated tenant ID
        product_id: Unique product identifier
        name: Product name
        description: Product description
        format_ids: List of FormatId dicts (defaults to display formats)
        **kwargs: Additional fields to pass to Product constructor

    Returns:
        Product object with test defaults

    Example:
        product = create_test_product(
            tenant_id="test_tenant_001",
            product_id="test_product_001",
            name="Test Display Product",
            description="Display advertising for testing"
        )
    """
    if format_ids is None:
        format_ids = [
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
        ]

    # Set sensible defaults for required fields
    kwargs.setdefault("targeting_template", {"geo": ["US"], "device": ["desktop", "mobile"]})
    kwargs.setdefault("delivery_type", "non_guaranteed")
    kwargs.setdefault("is_custom", False)

    return Product(
        tenant_id=tenant_id, product_id=product_id, name=name, description=description, format_ids=format_ids, **kwargs
    )


def seed_targeting_test_tenant(
    session,
    tenant_id: str,
    *,
    tenant_name: str = "Targeting Test Publisher",
    subdomain: str = "targeting-test",
    principal_id: str = "test_adv",
    principal_name: str = "Test Advertiser",
    access_token: str = "test_token_targeting",
    max_daily_package_spend: Decimal = Decimal("50000.00"),
    currency_code: str = "USD",
) -> None:
    """Seed the canonical targeting-test tenant: Tenant + PropertyTag + CurrencyLimit + Principal.

    Uses factory-boy factories per tests/CLAUDE.md (Pattern #8). Binds the passed
    session to factories for the duration of the call so callers outside the
    IntegrationEnv harness (e.g., bare get_db_session() blocks) can use it.
    Caller is responsible for adding products, pricing options, and committing.
    """
    from tests.factories import (
        CurrencyLimitFactory,
        PrincipalFactory,
        PropertyTagFactory,
        TenantFactory,
    )

    with _bind_factories_to_session(session):
        # TenantFactory's RelatedFactory creates a default USD CurrencyLimit, but we
        # need a different max_daily_package_spend, so build the tenant without the
        # auto-related currency and add our own.
        tenant = TenantFactory(
            tenant_id=tenant_id,
            name=tenant_name,
            subdomain=subdomain,
            ad_server="mock",
        )
        session.flush()

        # Replace the auto-created CurrencyLimit with one that has the configured cap.
        session.execute(
            delete(CurrencyLimit).where(
                CurrencyLimit.tenant_id == tenant_id,
                CurrencyLimit.currency_code == currency_code,
            )
        )
        CurrencyLimitFactory(
            tenant=tenant,
            tenant_id=tenant_id,
            currency_code=currency_code,
            max_daily_package_spend=max_daily_package_spend,
        )
        PropertyTagFactory(
            tenant=tenant,
            tenant_id=tenant_id,
            tag_id="all_inventory",
            name="All Inventory",
            description="All inventory",
        )
        PrincipalFactory(
            tenant=tenant,
            tenant_id=tenant_id,
            principal_id=principal_id,
            name=principal_name,
            access_token=access_token,
            platform_mappings={"mock": {"advertiser_id": "mock_adv_1"}},
        )


def add_targeting_test_product(
    session,
    tenant_id: str,
    product_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    property_targeting_allowed: bool = False,
    rate: Decimal = Decimal("10.00"),
    currency: str = "USD",
) -> Product:
    """Add a Product + PricingOption pair sized for targeting integration tests.

    Uses factory-boy factories per tests/CLAUDE.md (Pattern #8). Caller must commit.
    """
    from sqlalchemy import select

    from tests.factories import PricingOptionFactory, ProductFactory

    with _bind_factories_to_session(session):
        # Look up existing tenant so SubFactory doesn't try to create a new one.
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        product = ProductFactory(
            tenant=tenant,
            tenant_id=tenant_id,
            product_id=product_id,
            name=name or f"Product {product_id}",
            description=description or f"Test product {product_id}",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            delivery_type="guaranteed",
            targeting_template={},
            implementation_config={},
            property_tags=["all_inventory"],
            property_targeting_allowed=property_targeting_allowed,
        )
        session.flush()

        PricingOptionFactory(
            product=product,
            tenant_id=tenant_id,
            product_id=product_id,
            pricing_model="cpm",
            rate=rate,
            currency=currency,
            is_fixed=True,
        )
        return product


def seed_media_buy_with_package(
    session,
    *,
    tenant_id: str,
    principal_id: str,
    product_id: str,
    media_buy_id: str = "mb_test",
    package_id: str = "pkg_test",
    budget: Decimal = Decimal("5000.00"),
) -> str:
    """Insert a MediaBuy + MediaPackage pair sized for update_media_buy tests.

    Uses factory-boy factories per tests/CLAUDE.md (Pattern #8). Caller must commit.
    Returns the media_buy_id for chaining.
    """
    from sqlalchemy import select

    from tests.factories import MediaBuyFactory, MediaPackageFactory

    with _bind_factories_to_session(session):
        # Look up existing tenant + principal so SubFactory doesn't try to create them.
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        principal = session.scalars(select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)).first()

        media_buy = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id=media_buy_id,
            tenant_id=tenant_id,
            principal_id=principal_id,
            order_name=f"Order {media_buy_id}",
            advertiser_name="Test Advertiser",
            start_date=(datetime.now(UTC) + timedelta(days=1)).date(),
            end_date=(datetime.now(UTC) + timedelta(days=30)).date(),
            budget=budget,
            currency="USD",
            status="pending_creatives",
            raw_request={"test": True},
        )
        MediaPackageFactory(
            media_buy=media_buy,
            media_buy_id=media_buy_id,
            package_id=package_id,
            budget=budget,
            package_config={"product_id": product_id},
        )
    return media_buy_id


def cleanup_test_data(session, tenant_id: str, principal_id: str = None):
    """Clean up test data for a tenant and optionally principal.

    This helper provides a consistent pattern for cleaning up test data
    in the correct order to avoid foreign key constraint violations.

    Args:
        session: Database session
        tenant_id: Tenant ID to clean up
        principal_id: Optional principal ID to clean up specifically

    Example:
        cleanup_test_data(session, "test_tenant_001", "test_principal_001")
    """
    # Clean up in reverse dependency order
    if principal_id:
        session.execute(delete(Product).where(Product.tenant_id == tenant_id))
        session.execute(
            delete(Principal).where(Principal.tenant_id == tenant_id, Principal.principal_id == principal_id)
        )
    else:
        session.execute(delete(Product).where(Product.tenant_id == tenant_id))
        session.execute(delete(Principal).where(Principal.tenant_id == tenant_id))

    session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))
    session.commit()
