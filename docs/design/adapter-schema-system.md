# Adapter Schema System Design

## Summary

Introduce a strongly-typed, schema-driven system for adapter configurations. Each adapter declares Pydantic schemas for its connection, product, and inventory configurations. The admin UI renders forms dynamically based on these schemas.

## Problem

Currently, adapter configurations are:
- **Loosely typed** - Stored as dicts, extracted with `.get()` calls
- **Hardcoded in database** - `AdapterConfig` has GAM-specific columns (`gam_network_code`, `gam_trafficker_id`, etc.)
- **Hardcoded in UI** - Separate templates per adapter (`add_product_gam.html`, `add_product_mock.html`)
- **Inconsistent** - Each adapter handles config differently

This makes it difficult to:
- Add new adapters (requires database migrations, new templates)
- Validate configurations (no schema enforcement)
- Generate admin UI dynamically

## Solution

Each adapter declares three Pydantic schemas:

| Schema | Purpose | Storage Location |
|--------|---------|------------------|
| `ConnectionConfig` | API credentials, network IDs | `AdapterConfig.config_json` |
| `ProductConfig` | Product-level settings | `Product.implementation_config` |
| `InventoryConfig` | Inventory profile structure | `InventoryProfile.inventory_config` |

Plus a capabilities declaration for UI feature flags.

## Detailed Design

### 1. Base Schema Classes

```python
# src/adapters/base_schemas.py
from pydantic import BaseModel, Field
from typing import Any

class BaseConnectionConfig(BaseModel):
    """Base class for adapter connection configurations."""

    class Config:
        extra = "forbid"  # Strict validation in dev

    @classmethod
    def get_ui_schema(cls) -> dict[str, Any]:
        """Return JSON schema with UI hints for form generation."""
        return cls.model_json_schema()

class BaseProductConfig(BaseModel):
    """Base class for adapter product configurations."""

    class Config:
        extra = "forbid"

class BaseInventoryConfig(BaseModel):
    """Base class for adapter inventory configurations."""

    class Config:
        extra = "forbid"
```

### 2. Adapter-Specific Schemas

```python
# src/adapters/broadstreet/schemas.py
from pydantic import Field
from src.adapters.base_schemas import (
    BaseConnectionConfig,
    BaseProductConfig,
    BaseInventoryConfig
)

class BroadstreetConnectionConfig(BaseConnectionConfig):
    """Broadstreet API connection credentials."""

    network_id: str = Field(
        ...,
        description="Broadstreet network ID",
        json_schema_extra={"ui_order": 1}
    )
    api_key: str = Field(
        ...,
        description="Broadstreet API access token",
        json_schema_extra={"secret": True, "ui_order": 2}
    )

class BroadstreetProductConfig(BaseProductConfig):
    """Broadstreet product-level configuration."""

    targeted_zone_ids: list[str] = Field(
        default_factory=list,
        description="Zone IDs to target for this product",
        json_schema_extra={"ui_component": "zone_selector"}
    )
    cost_type: str = Field(
        default="CPM",
        description="Pricing model",
        json_schema_extra={"enum": ["CPM", "FLAT_RATE"]}
    )
    ad_format: str = Field(
        default="display",
        description="Creative format",
        json_schema_extra={"enum": ["display", "html", "text"]}
    )

class BroadstreetInventoryConfig(BaseInventoryConfig):
    """Broadstreet inventory profile structure."""

    zones: list[str] = Field(
        default_factory=list,
        description="Zone IDs included in this profile",
        json_schema_extra={"ui_component": "zone_selector"}
    )
```

### 3. Adapter Capabilities

```python
# src/adapters/base.py
from dataclasses import dataclass
from typing import Type

@dataclass
class AdapterCapabilities:
    """Declares what features an adapter supports."""

    # Inventory
    supports_inventory_sync: bool = False
    supports_inventory_profiles: bool = False
    inventory_entity_label: str = "Items"  # "Zones", "Ad Units", etc.

    # Targeting
    supports_custom_targeting: bool = False
    supports_geo_targeting: bool = True

    # Products
    supports_dynamic_products: bool = False  # Signals-based variants

    # Pricing
    supported_pricing_models: list[str] = None  # ["cpm", "flat_rate", ...]

    # Reporting
    supports_webhooks: bool = False
    supports_realtime_reporting: bool = False
```

### 4. Adapter Declaration

```python
# src/adapters/broadstreet/adapter.py
from src.adapters.base import AdServerAdapter, AdapterCapabilities
from src.adapters.broadstreet.schemas import (
    BroadstreetConnectionConfig,
    BroadstreetProductConfig,
    BroadstreetInventoryConfig,
)

class BroadstreetAdapter(AdServerAdapter):
    adapter_name = "broadstreet"

    # Schema declarations
    CONNECTION_CONFIG = BroadstreetConnectionConfig
    PRODUCT_CONFIG = BroadstreetProductConfig
    INVENTORY_CONFIG = BroadstreetInventoryConfig

    # Capability declaration
    CAPABILITIES = AdapterCapabilities(
        supports_inventory_sync=True,
        supports_inventory_profiles=True,
        inventory_entity_label="Zones",
        supports_custom_targeting=False,
        supports_dynamic_products=False,
        supported_pricing_models=["cpm", "flat_rate"],
        supports_webhooks=False,
        supports_realtime_reporting=True,
    )
```

### 5. Database Changes

Add flexible JSON column, keep legacy columns during migration:

```python
# Migration
class AdapterConfig(Base):
    # NEW: Flexible JSON storage
    config_json: Mapped[dict] = mapped_column(
        JSONType,
        nullable=False,
        server_default=text("'{}'::jsonb")
    )

    # LEGACY: Keep during migration period
    gam_network_code: ...  # Deprecated
    kevel_api_key: ...     # Deprecated
```

### 6. Admin UI Integration

#### API Endpoint for Schema

```python
# src/admin/blueprints/adapters.py
@bp.route("/api/adapters/<adapter_type>/schema")
def get_adapter_schema(adapter_type: str):
    """Return adapter schemas for UI form generation."""
    adapter_class = get_adapter_class(adapter_type)

    return jsonify({
        "connection": adapter_class.CONNECTION_CONFIG.model_json_schema(),
        "product": adapter_class.PRODUCT_CONFIG.model_json_schema(),
        "inventory": adapter_class.INVENTORY_CONFIG.model_json_schema(),
        "capabilities": asdict(adapter_class.CAPABILITIES),
    })
```

#### Dynamic Form Rendering

```javascript
// Static JS that renders forms from schema
async function renderAdapterForm(adapterType, formType, containerId) {
    const response = await fetch(`/api/adapters/${adapterType}/schema`);
    const schemas = await response.json();
    const schema = schemas[formType];  // "connection", "product", or "inventory"

    const container = document.getElementById(containerId);
    renderSchemaForm(container, schema, schemas.capabilities);
}

function renderSchemaForm(container, schema, capabilities) {
    // Iterate over schema.properties
    // Render appropriate input for each field type
    // Respect json_schema_extra hints (secret, ui_component, ui_order)
    // Hide sections based on capabilities
}
```

### 7. Validation Flow

```python
# src/core/helpers/adapter_helpers.py
def get_adapter(principal: Principal, dry_run: bool = False):
    """Get adapter with validated config."""

    with get_db_session() as session:
        config_row = session.scalars(
            select(AdapterConfig).filter_by(tenant_id=tenant_id)
        ).first()

        adapter_class = get_adapter_class(config_row.adapter_type)

        # Validate config against schema
        try:
            config = adapter_class.CONNECTION_CONFIG.model_validate(
                config_row.config_json
            )
        except ValidationError as e:
            raise AdapterConfigurationError(f"Invalid config: {e}")

        return adapter_class(config=config, principal=principal, dry_run=dry_run)
```

## Migration Plan

### Phase 1: Add Schema Infrastructure
- Create base schema classes
- Add `config_json` column to AdapterConfig
- Create schemas for Broadstreet (new adapter, clean slate)

### Phase 2: Migrate Existing Adapters
- Create schemas for GAM, Kevel, Mock, Triton
- Populate `config_json` from legacy columns
- Update `get_adapter()` to use schemas

### Phase 3: Update Admin UI
- Add schema endpoint
- Create generic form renderer
- Update settings page to use dynamic forms
- Update product form to use dynamic forms

### Phase 4: Cleanup
- Remove legacy columns from AdapterConfig
- Remove hardcoded templates (keep as fallback initially)
- Update tests

## Files to Create/Modify

### New Files
- `src/adapters/base_schemas.py` - Base Pydantic classes
- `src/adapters/broadstreet/schemas.py` - Broadstreet schemas
- `src/adapters/gam/schemas.py` - GAM schemas
- `src/adapters/kevel/schemas.py` - Kevel schemas
- `static/js/schema-form-renderer.js` - Dynamic form renderer

### Modified Files
- `src/adapters/base.py` - Add `AdapterCapabilities`, schema class attributes
- `src/adapters/broadstreet/adapter.py` - Declare schemas
- `src/adapters/gam/adapter.py` - Declare schemas
- `src/core/database/models.py` - Add `config_json` column
- `src/core/helpers/adapter_helpers.py` - Use schema validation
- `src/admin/blueprints/adapters.py` - Add schema endpoint
- `src/admin/blueprints/settings.py` - Use dynamic forms
- `src/admin/blueprints/products.py` - Use dynamic forms

## Benefits

1. **Type Safety** - Pydantic validation catches config errors early
2. **Self-Documenting** - Schemas include descriptions, UI hints
3. **Extensible** - New adapters don't require database migrations
4. **Consistent** - All adapters follow same pattern
5. **UI Generation** - Forms rendered from schemas, less duplication
6. **Testable** - Schemas can be unit tested independently

## Open Questions

1. **Schema versioning** - How to handle schema changes over time?
2. **Complex UI components** - How to handle GAM's inventory browser (not just a simple form)?
3. **Migration timing** - Big bang or incremental?

## Acceptance Criteria

- [ ] Base schema classes created
- [ ] At least one adapter (Broadstreet) uses new schema system
- [ ] Admin UI can render connection config from schema
- [ ] Admin UI can render product config from schema
- [ ] Validation errors shown in UI
- [ ] Capabilities control which UI sections are shown
- [ ] Tests for schema validation
- [ ] Migration path documented
