# GAM Product Configuration Guide

Complete guide for configuring Google Ad Manager (GAM) product trafficking settings in the Prebid Sales Agent.

---

## Table of Contents
1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [Configuration Workflow](#configuration-workflow)
4. [Field Reference](#field-reference)
5. [Migration Guide](#migration-guide)
6. [Testing](#testing)

---

## Overview

### What This Is
The GAM configuration system separates user-facing AdCP product fields from internal GAM trafficking fields required for line item creation.

### Key Concepts
- **AdCP Fields**: Price, formats, countries (visible to buyers)
- **GAM Fields**: Priority, inventory targeting, creative placeholders (internal only)
- **implementation_config**: JSONB field storing all GAM-specific settings

### Architecture
```
Product Creation → Smart Defaults → GAM Config UI → Validation → Media Buy → GAM Line Item
```

---

## Quick Start

### For New Products
1. Create product in Admin UI (name, price, delivery type, formats)
2. Click **"GAM Config"** button after creation
3. Use inventory picker to select ad units/placements
4. Adjust priority and other settings if needed
5. Save configuration
6. Create media buy - GAM line items will be created automatically

### For Existing Products
Products are automatically migrated with smart defaults based on delivery type. Use the GAM Config UI to customize.

---

## Configuration Workflow

### 1. Smart Default Generation

When a product is created, defaults are auto-generated based on delivery type:

**Guaranteed Products:**
```json
{
  "line_item_type": "STANDARD",
  "priority": 6,
  "primary_goal_type": "DAILY",
  "creative_placeholders": [...]
}
```

**Non-Guaranteed Products:**
```json
{
  "line_item_type": "PRICE_PRIORITY",
  "priority": 10,
  "primary_goal_type": "NONE",
  "creative_placeholders": [...]
}
```

### 2. GAM Configuration UI

**Location**: Admin UI → Products → GAM Config button

**Features:**
- Searchable inventory picker (ad units & placements)
- Priority slider (1-16)
- Frequency cap configuration
- Creative placeholder management
- Custom targeting options

**UI Components:**
- Blue badge UI for selected inventory
- Real-time search with debouncing
- One-click removal of selections
- Validation before saving

### 3. Validation

**At Configuration Time:**
- Required fields present
- Priority in valid range (1-16)
- Creative placeholders match formats
- Line item type appropriate for delivery type

**At Media Buy Creation:**
- Configuration exists for all products
- Configuration is complete and valid
- Clear error messages if validation fails

---

## Field Reference

### Required Fields

#### Line Item Type
- **Field**: `line_item_type`
- **Type**: String enum
- **Values**: `STANDARD`, `PRICE_PRIORITY`, `SPONSORSHIP`, `NETWORK`, `BULK`, `HOUSE`
- **Defaults**:
  - Guaranteed → `STANDARD`
  - Non-guaranteed → `PRICE_PRIORITY`

#### Priority
- **Field**: `priority`
- **Type**: Integer (1-16, where 1 is highest)
- **Defaults**:
  - Guaranteed → 6
  - Non-guaranteed → 10
- **Guidelines**:
  - 1-4: Reserved for emergency/critical campaigns
  - 4-6: Guaranteed inventory
  - 8-12: Non-guaranteed/price priority
  - 16: House ads

#### Creative Placeholders
- **Field**: `creative_placeholders`
- **Type**: Array of objects
- **Structure**:
  ```json
  [
    {
      "width": 300,
      "height": 250,
      "expected_creative_count": 1,
      "is_native": false
    }
  ]
  ```
- **Auto-generated**: Based on product formats

#### Primary Goal Type
- **Field**: `primary_goal_type`
- **Type**: String enum
- **Values**: `DAILY`, `LIFETIME`, `NONE`
- **Defaults**:
  - Guaranteed → `DAILY`
  - Non-guaranteed → `NONE`

### Optional Fields

#### Inventory Targeting
- **Fields**:
  - `targeted_ad_unit_ids` (array of strings)
  - `targeted_placement_ids` (array of strings)
- **Behavior**: If not specified, uses network root ad unit
- **Best Practice**: Always specify for production campaigns

#### Frequency Caps
- **Field**: `frequency_caps`
- **Type**: Array of objects
- **Structure**:
  ```json
  [
    {
      "max_impressions": 3,
      "num_time_units": 1,
      "time_unit": "DAY"
    }
  ]
  ```
- **Time Units**: `DAY`, `WEEK`, `MONTH`, `LIFETIME`

#### Roadblocking
- **Field**: `roadblocking_type`
- **Values**: `ONLY_ONE`, `AS_MANY_AS_POSSIBLE`, `ALL_ROADBLOCK`, `CREATIVE_SET`
- **Default**: `ONLY_ONE`

#### Creative Rotation
- **Field**: `creative_rotation_type`
- **Values**: `EVEN`, `OPTIMIZED`, `WEIGHTED`, `SEQUENTIAL`
- **Default**: `OPTIMIZED`

#### Custom Targeting
- **Field**: `custom_targeting_keys`
- **Type**: JSON object
- **Example**:
  ```json
  {
    "sport": ["football", "basketball"],
    "geo": ["us-ny", "us-ca"]
  }
  ```

---

## Migration Guide

### Running the Migration Script

**Dry-Run (Safe - No Changes):**
```bash
python scripts/migrate_product_configs.py
```

**Apply Changes:**
```bash
python scripts/migrate_product_configs.py --apply
```

**Specific Tenant:**
```bash
python scripts/migrate_product_configs.py --tenant tenant_id --apply
```

### Migration Behavior

**What It Does:**
- Examines all products in database
- Skips products that already have `implementation_config`
- Generates smart defaults based on delivery type and formats
- Validates all generated configs
- Applies changes (if `--apply` flag used)

**What It Doesn't Do:**
- Never overwrites existing configurations
- Never modifies user-facing product fields
- Never changes products with valid configs

### Safety Features
- ✅ Dry-run by default
- ✅ Confirmation prompt before applying
- ✅ Detailed logging of every operation
- ✅ Validation before saving
- ✅ Transaction safety with flush/commit
- ✅ Tenant-specific targeting

---

## Testing

### Test GAM Configuration

**In Development:**
```bash
# Start Docker services
docker compose up -d

# Access Admin UI
open http://localhost:8000

# Navigate to Products → GAM Config
# Test inventory picker, priority slider, save/validation
```

**Test Migration:**
```bash
# Dry-run to see what would change
PYTHONPATH=. uv run python scripts/migrate_product_configs.py

# Apply if output looks correct
PYTHONPATH=. uv run python scripts/migrate_product_configs.py --apply
```

### Verify Configuration

**Via Database:**
```sql
SELECT
  product_id,
  name,
  delivery_type,
  implementation_config->>'line_item_type' as line_item_type,
  implementation_config->>'priority' as priority
FROM products
WHERE implementation_config IS NOT NULL;
```

**Via Admin UI:**
1. Navigate to Products
2. Click "GAM Config" on any product
3. Verify fields are populated
4. Make changes and save
5. Reload page - changes should persist

### Test Media Buy Creation

**With Valid Config:**
- Create media buy with configured product
- Should succeed and create GAM line item

**With Invalid Config:**
- Remove required fields from `implementation_config`
- Attempt to create media buy
- Should fail with clear validation error

---

## Troubleshooting

### "GAM configuration validation failed"
- **Cause**: Required fields missing from `implementation_config`
- **Fix**: Click "GAM Config" button and complete all required fields

### Changes Not Persisting
- **Cause**: JSONB field mutations not tracked by SQLAlchemy
- **Fix**: Code now uses `attributes.flag_modified()` - should not occur

### Inventory Picker Not Loading
- **Cause**: No inventory synced for tenant
- **Fix**: Run inventory sync: Admin UI → Inventory → Sync button

### Migration Errors
- **Cause**: Format field inconsistencies
- **Fix**: Migration script handles both dict and string formats
- **Verify**: Check `product.formats` field structure in database

---

## API Reference

### GAM Configuration Service

```python
from src.services.gam_product_config_service import GAMProductConfigService

service = GAMProductConfigService()

# Generate defaults
config = service.generate_default_config(
    delivery_type="guaranteed",
    formats=["display_300x250"]
)

# Validate config
is_valid, error_msg = service.validate_config(config)

# Parse form data
impl_config = service.parse_form_config(request.form)
```

### Inventory API Endpoint

```http
GET /api/tenant/{tenant_id}/inventory-list
  ?type=ad_unit
  &search=sports
  &status=ACTIVE
```

**Response:**
```json
{
  "items": [
    {
      "id": "123456",
      "name": "Sports Homepage",
      "path": "/Sports/Homepage",
      "type": "ad_unit"
    }
  ]
}
```

---

## Best Practices

### Priority Guidelines
- **Emergency/Critical**: 1-4 (use sparingly)
- **Guaranteed**: 4-6 (standard guaranteed delivery)
- **Non-Guaranteed**: 8-12 (price priority/house)
- **House Ads**: 16 (lowest priority)

### Inventory Targeting
- Always specify ad units or placements (don't rely on root fallback)
- Use placements for grouped inventory
- Use ad units with `include_descendants` for hierarchies
- Test targeting in GAM before production

### Creative Placeholders
- Match product formats exactly
- Set `expected_creative_count` appropriately
- Use `is_native: true` for native formats
- Don't over-specify - keep it simple

### Frequency Caps
- Set reasonable caps (3-5 per day typical)
- Use `LIFETIME` caps for awareness campaigns
- Test impact on delivery before production
- Monitor fill rate after enabling caps

---

## Related Documentation
- [GAM Testing Setup](testing-setup.md) - OAuth and test environment setup
- [Adapters Overview](../README.md) - Adapter documentation
- [Development Guide](../../development/README.md) - Development and migrations

---

## Support

**For Issues:**
- Check validation error messages first
- Review logs: `docker-compose logs adcp-server`
- Verify database: `implementation_config` field in products table
- Test migration in dry-run mode first

**For Questions:**
- See examples in migration script output
- Review generated defaults for similar products
- Check GAM API documentation for field specifications
