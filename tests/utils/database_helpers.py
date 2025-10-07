"""Database helper utilities for tests.

Provides consistent patterns for creating test database objects with proper
timestamp handling and field validation to prevent common test issues.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete

from src.core.database.models import Principal, Product, Tenant


def get_utc_now():
    """Get current UTC datetime for consistent timestamp creation."""
    return datetime.now(UTC)


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
    tenant_id: str, product_id: str, name: str, description: str, formats: list[str] = None, **kwargs: Any
) -> Product:
    """Create a Product object with sensible test defaults.

    This helper ensures Product objects are created with all required
    fields and sensible defaults for testing.

    Args:
        tenant_id: Associated tenant ID
        product_id: Unique product identifier
        name: Product name
        description: Product description
        formats: List of format IDs (defaults to display formats)
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
    if formats is None:
        formats = ["display_300x250", "display_728x90"]

    # Set sensible defaults for required fields
    kwargs.setdefault("targeting_template", {"geo": ["US"], "device": ["desktop", "mobile"]})
    kwargs.setdefault("delivery_type", "non_guaranteed")
    kwargs.setdefault("is_fixed_price", False)
    kwargs.setdefault("cpm", 5.0)
    kwargs.setdefault("min_spend", 1000.0)
    kwargs.setdefault("is_custom", False)

    return Product(
        tenant_id=tenant_id, product_id=product_id, name=name, description=description, formats=formats, **kwargs
    )


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
