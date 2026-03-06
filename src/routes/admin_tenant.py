"""Tenant Admin API — FastAPI router for per-tenant operations.

Manages the full publisher lifecycle: adapter config, currency limits,
property tags, authorized properties, inventory discovery, inventory profiles,
products, principals, and creative formats.
Auth: Authorization: Bearer <admin_token> (per-tenant admin token).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from src.core.admin_auth import require_tenant_admin
from src.core.admin_schemas import (
    AdapterConfigRequest,
    AuthorizedPropertyRequest,
    BulkPropertyUploadRequest,
    CreatePrincipalRequest,
    CreateProductRequest,
    CurrencyLimitRequest,
    GAMAdvertiserSearchRequest,
    InventoryProfileRequest,
    PropertyTagRequest,
    UpdateAuthorizedPropertyRequest,
    UpdateCurrencyLimitRequest,
    UpdateInventoryProfileRequest,
    UpdatePrincipalRequest,
    UpdateProductRequest,
)
from src.core.database.models import Tenant
from src.services.adapter_config import AdapterConfigService
from src.services.authorized_property import AuthorizedPropertyService
from src.services.currency_limit import CurrencyLimitService
from src.services.inventory_discovery import InventoryDiscoveryService
from src.services.inventory_profile import InventoryProfileService
from src.services.principal_admin import PrincipalAdminService
from src.services.product_admin import ProductAdminService
from src.services.property_tag import PropertyTagService

logger = logging.getLogger(__name__)

TenantAdmin = Annotated[Tenant, Depends(require_tenant_admin)]

router = APIRouter(prefix="/api/v1/admin/{tenant_id}", tags=["tenant-admin"])

_adapter_svc = AdapterConfigService()
_currency_svc = CurrencyLimitService()
_tag_svc = PropertyTagService()
_property_svc = AuthorizedPropertyService()
_inventory_svc = InventoryDiscoveryService()
_profile_svc = InventoryProfileService()
_product_svc = ProductAdminService()
_principal_svc = PrincipalAdminService()


# ---------------------------------------------------------------------------
# Adapter Configuration
# ---------------------------------------------------------------------------


@router.get("/adapter")
async def get_adapter_config(tenant_id: str, _tenant: TenantAdmin) -> dict[str, Any]:
    """Get adapter configuration for a tenant."""
    return _adapter_svc.get_adapter_config(tenant_id)


@router.put("/adapter")
async def save_adapter_config(
    tenant_id: str,
    body: AdapterConfigRequest,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Save adapter configuration."""
    return _adapter_svc.save_adapter_config(tenant_id, body.adapter_type, body.config)


@router.post("/adapter/test-connection")
async def test_adapter_connection(tenant_id: str, _tenant: TenantAdmin) -> dict[str, Any]:
    """Test the adapter connection."""
    return _adapter_svc.test_connection(tenant_id)


@router.get("/adapter/capabilities")
async def get_adapter_capabilities(tenant_id: str, _tenant: TenantAdmin) -> dict[str, Any]:
    """Get adapter capabilities (pricing models, targeting support, etc.)."""
    config = _adapter_svc.get_adapter_config(tenant_id)
    return _adapter_svc.get_capabilities(config["adapter_type"])


# ---------------------------------------------------------------------------
# Currency Limits
# ---------------------------------------------------------------------------


@router.get("/currency-limits")
async def list_currency_limits(tenant_id: str, _tenant: TenantAdmin) -> list[dict[str, Any]]:
    """List all currency limits for a tenant."""
    return _currency_svc.list_limits(tenant_id)


@router.post("/currency-limits", status_code=201)
async def create_currency_limit(
    tenant_id: str,
    body: CurrencyLimitRequest,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Create a new currency limit."""
    return _currency_svc.create_limit(tenant_id, body.model_dump())


@router.put("/currency-limits/{currency_code}")
async def update_currency_limit(
    tenant_id: str,
    currency_code: str,
    body: UpdateCurrencyLimitRequest,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Update a currency limit."""
    return _currency_svc.update_limit(tenant_id, currency_code, body.model_dump(exclude_unset=True))


@router.delete("/currency-limits/{currency_code}")
async def delete_currency_limit(
    tenant_id: str,
    currency_code: str,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Delete a currency limit."""
    return _currency_svc.delete_limit(tenant_id, currency_code)


# ---------------------------------------------------------------------------
# Property Tags
# ---------------------------------------------------------------------------


@router.get("/property-tags")
async def list_property_tags(tenant_id: str, _tenant: TenantAdmin) -> list[dict[str, Any]]:
    """List all property tags for a tenant (auto-creates 'all_inventory' if missing)."""
    return _tag_svc.list_tags(tenant_id)


@router.post("/property-tags", status_code=201)
async def create_property_tag(
    tenant_id: str,
    body: PropertyTagRequest,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Create a new property tag."""
    return _tag_svc.create_tag(tenant_id, body.model_dump())


@router.delete("/property-tags/{tag_id}")
async def delete_property_tag(
    tenant_id: str,
    tag_id: str,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Delete a property tag (cannot delete 'all_inventory')."""
    return _tag_svc.delete_tag(tenant_id, tag_id)


# ---------------------------------------------------------------------------
# Authorized Properties
# ---------------------------------------------------------------------------


@router.get("/properties")
async def list_properties(tenant_id: str, _tenant: TenantAdmin) -> dict[str, Any]:
    """List authorized properties with verification status counts."""
    return _property_svc.list_properties(tenant_id)


@router.post("/properties", status_code=201)
async def create_property(
    tenant_id: str,
    body: AuthorizedPropertyRequest,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Create a new authorized property."""
    return _property_svc.create_property(tenant_id, body.model_dump())


@router.put("/properties/{property_id}")
async def update_property(
    tenant_id: str,
    property_id: str,
    body: UpdateAuthorizedPropertyRequest,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Update an authorized property (resets verification status)."""
    return _property_svc.update_property(tenant_id, property_id, body.model_dump(exclude_unset=True))


@router.delete("/properties/{property_id}")
async def delete_property(
    tenant_id: str,
    property_id: str,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Delete an authorized property."""
    return _property_svc.delete_property(tenant_id, property_id)


@router.post("/properties/bulk")
async def bulk_upload_properties(
    tenant_id: str,
    body: BulkPropertyUploadRequest,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Bulk create/update authorized properties."""
    return _property_svc.bulk_upload(tenant_id, [p.model_dump() for p in body.properties])


@router.post("/properties/verify")
async def verify_properties(tenant_id: str, _tenant: TenantAdmin) -> dict[str, Any]:
    """Trigger verification of all pending properties."""
    return _property_svc.verify_properties(tenant_id)


# ---------------------------------------------------------------------------
# Inventory Discovery
# ---------------------------------------------------------------------------


@router.get("/inventory")
async def get_inventory(
    tenant_id: str,
    _tenant: TenantAdmin,
    inventory_type: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = Query(default=500, le=1000),
) -> dict[str, Any]:
    """Get synced inventory items (ad units, placements)."""
    return _inventory_svc.get_inventory(
        tenant_id, inventory_type=inventory_type, status=status, search=search, limit=limit
    )


@router.get("/inventory/sizes")
async def get_inventory_sizes(tenant_id: str, _tenant: TenantAdmin) -> dict[str, Any]:
    """Get available ad sizes from synced inventory."""
    return _inventory_svc.get_sizes(tenant_id)


@router.get("/targeting")
async def get_targeting(tenant_id: str, _tenant: TenantAdmin) -> dict[str, Any]:
    """Get targeting data (custom keys, audiences, labels)."""
    return _inventory_svc.get_targeting(tenant_id)


@router.get("/targeting/{key_id}/values")
async def get_targeting_values(
    tenant_id: str,
    key_id: str,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Get values for a specific targeting key."""
    return _inventory_svc.get_targeting_values(tenant_id, key_id)


# ---------------------------------------------------------------------------
# Inventory Profiles
# ---------------------------------------------------------------------------


@router.get("/inventory-profiles")
async def list_inventory_profiles(tenant_id: str, _tenant: TenantAdmin) -> dict[str, Any]:
    """List inventory profiles with product counts."""
    return _profile_svc.list_profiles(tenant_id)


@router.post("/inventory-profiles", status_code=201)
async def create_inventory_profile(
    tenant_id: str,
    body: InventoryProfileRequest,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Create a new inventory profile."""
    return _profile_svc.create_profile(tenant_id, body.model_dump())


@router.put("/inventory-profiles/{profile_id}")
async def update_inventory_profile(
    tenant_id: str,
    profile_id: str,
    body: UpdateInventoryProfileRequest,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Update an inventory profile."""
    return _profile_svc.update_profile(tenant_id, profile_id, body.model_dump(exclude_unset=True))


@router.delete("/inventory-profiles/{profile_id}")
async def delete_inventory_profile(
    tenant_id: str,
    profile_id: str,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Delete an inventory profile (fails if referenced by products)."""
    return _profile_svc.delete_profile(tenant_id, profile_id)


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


@router.get("/products")
async def list_products(tenant_id: str, _tenant: TenantAdmin) -> dict[str, Any]:
    """List all products for a tenant."""
    return _product_svc.list_products(tenant_id)


@router.post("/products", status_code=201)
async def create_product(
    tenant_id: str,
    body: CreateProductRequest,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Create a new product."""
    return _product_svc.create_product(tenant_id, body.model_dump())


@router.get("/products/{product_id}")
async def get_product(
    tenant_id: str,
    product_id: str,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Get product details."""
    return _product_svc.get_product(tenant_id, product_id)


@router.put("/products/{product_id}")
async def update_product(
    tenant_id: str,
    product_id: str,
    body: UpdateProductRequest,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Update a product."""
    return _product_svc.update_product(tenant_id, product_id, body.model_dump(exclude_unset=True))


@router.delete("/products/{product_id}")
async def delete_product(
    tenant_id: str,
    product_id: str,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Delete a product."""
    return _product_svc.delete_product(tenant_id, product_id)


@router.get("/creative-formats")
async def list_creative_formats(tenant_id: str, _tenant: TenantAdmin) -> dict[str, Any]:
    """List available creative formats for product creation."""
    return _product_svc.list_creative_formats(tenant_id)


# ---------------------------------------------------------------------------
# Principals (Advertisers)
# ---------------------------------------------------------------------------


@router.get("/principals")
async def list_principals(tenant_id: str, _tenant: TenantAdmin) -> dict[str, Any]:
    """List all principals (advertisers) with media buy counts."""
    return _principal_svc.list_principals(tenant_id)


@router.post("/principals", status_code=201)
async def create_principal(
    tenant_id: str,
    body: CreatePrincipalRequest,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Create a new principal (advertiser). Returns access_token."""
    return _principal_svc.create_principal(tenant_id, body.model_dump())


@router.get("/principals/{principal_id}")
async def get_principal(
    tenant_id: str,
    principal_id: str,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Get principal details."""
    return _principal_svc.get_principal(tenant_id, principal_id)


@router.put("/principals/{principal_id}")
async def update_principal(
    tenant_id: str,
    principal_id: str,
    body: UpdatePrincipalRequest,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Update a principal."""
    return _principal_svc.update_principal(tenant_id, principal_id, body.model_dump(exclude_unset=True))


@router.delete("/principals/{principal_id}")
async def delete_principal(
    tenant_id: str,
    principal_id: str,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Delete a principal."""
    return _principal_svc.delete_principal(tenant_id, principal_id)


@router.post("/principals/{principal_id}/regenerate-token")
async def regenerate_principal_token(
    tenant_id: str,
    principal_id: str,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Regenerate access token for a principal."""
    return _principal_svc.regenerate_token(tenant_id, principal_id)


@router.post("/gam/advertisers")
async def search_gam_advertisers(
    tenant_id: str,
    body: GAMAdvertiserSearchRequest,
    _tenant: TenantAdmin,
) -> dict[str, Any]:
    """Search GAM advertisers for principal platform mapping."""
    return _principal_svc.search_gam_advertisers(tenant_id, body.model_dump())
