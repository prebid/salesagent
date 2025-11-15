# AXE Segment Targeting (AdCP 3.0.3)

## Overview

AXE (Audience Exchange) segment targeting allows buyers to target specific audience segments using AdCP's `axe_include_segment` and `axe_exclude_segment` fields. This feature integrates seamlessly with the existing GAM custom targeting infrastructure.

## How It Works

### Two-Step Configuration

**Step 1: Sync Inventory** → **Step 2: Select AXE Key**

This two-step approach gives publishers full control over which existing GAM custom targeting key represents AXE segments.

### 1. Sync Inventory from GAM

Publishers first sync their inventory to discover all custom targeting keys:

1. Navigate to **Admin UI → Inventory Section**
2. Click **"Sync Inventory"** button
3. System discovers all custom targeting keys, values, ad units, etc.
4. Custom targeting keys are stored in database

**What gets discovered:**
- All custom targeting keys and their types (FREEFORM, PREDEFINED)
- Ad units and placements
- Audience segments
- Labels

### 2. Select AXE Key from Synced Targeting Data

After syncing, publishers map their AXE segments to a custom targeting key:

1. Navigate to **Browse Targeting** page
2. Find the **AXE Segment Targeting** section (below sync info, before tabs)
3. Dropdown shows all synced custom targeting keys
4. Select the key that contains AXE segments (e.g., `audience_id`, `axe_segment`, `segment_key`)
5. Or choose "Enter manually" to type a key name
6. Click **Save AXE Configuration**

**Key Benefits:**
- Use existing custom targeting keys - no need to create new ones
- Full control over which key represents AXE segments
- Works with any GAM custom targeting key
- Publisher decides the mapping, not the system

### 3. Media Buy Creation

When buyers create media buys with AXE segment targeting:

```json
{
  "targeting": {
    "axe_include_segment": "x8dj3k",
    "axe_exclude_segment": "y9kl4m"
  }
}
```

The GAM adapter translates these to custom targeting:

- **Include**: `axe_segment = "x8dj3k"` (positive targeting)
- **Exclude**: `NOT_axe_segment = "y9kl4m"` (negative targeting with NOT_ prefix)

### 4. Targeting Picker UI

Publishers can select AXE segments through the existing targeting picker:

1. Navigate to Inventory → Custom Targeting
2. Find the AXE key (e.g., "axe_segment")
3. Browse available segment values
4. Select segments for products or media buys

**No special UI needed** - AXE segments are just custom targeting values!

## Architecture

### Database Schema

**Field Added**: `adapter_config.gam_axe_custom_targeting_key`
- Type: String(100)
- Nullable: True
- Default: `"axe_segment"`
- Migration: `986aa36f9589_add_gam_axe_custom_targeting_key_to_.py`

### Code Components

**1. GAM Targeting Manager** (`src/adapters/gam/managers/targeting.py`)
- Translates `axe_include_segment` → `customTargeting[axe_segment]`
- Translates `axe_exclude_segment` → `customTargeting[NOT_axe_segment]`
- Currently uses hardcoded default "axe_segment" (TODO: read from config in build_targeting)

**2. GAM Inventory Manager** (`src/adapters/gam/managers/inventory.py`)
- `_get_axe_key_name()`: Reads configured key name from adapter config
- `ensure_axe_custom_targeting_key()`: Creates key in GAM if it doesn't exist
- `sync_all_inventory()`: Automatically ensures AXE key exists before syncing

**3. Admin UI**
- Template: `templates/tenant_settings.html` (Ad Server section)
- Backend: `src/admin/blueprints/settings.py` (update_adapter route)

## Usage Examples

### Example 1: Default Configuration

**Setup:**
1. Publisher configures GAM adapter (OAuth, network code)
2. Runs inventory sync
3. System auto-creates `axe_segment` key in GAM
4. Segments appear in targeting picker

**Buyer Request:**
```json
{
  "targeting": {
    "geo_country_any_of": ["US"],
    "axe_include_segment": "high_value_shoppers"
  }
}
```

**GAM Line Item Targeting:**
```
Country: US
Custom Targeting: axe_segment = high_value_shoppers
```

### Example 2: Custom Key Name

**Setup:**
1. Publisher goes to Ad Server Settings → Advanced
2. Sets `gam_axe_custom_targeting_key` to `"audience_segment"`
3. Runs inventory sync
4. System creates `audience_segment` key in GAM

**Buyer Request:**
```json
{
  "targeting": {
    "axe_include_segment": "sports_fans",
    "axe_exclude_segment": "existing_customers"
  }
}
```

**GAM Line Item Targeting:**
```
Custom Targeting:
  audience_segment = sports_fans
  NOT_audience_segment = existing_customers
```

### Example 3: Combine with Other Targeting

**Buyer Request:**
```json
{
  "targeting": {
    "geo_country_any_of": ["US", "CA"],
    "device_type_any_of": ["mobile"],
    "axe_include_segment": "premium_audience",
    "custom": {
      "gam": {
        "key_values": {
          "interest": "technology",
          "income": "high"
        }
      }
    }
  }
}
```

**GAM Line Item Targeting:**
```
Geography: US, CA
Device Type: Mobile
Custom Targeting:
  axe_segment = premium_audience
  interest = technology
  income = high
```

## Testing

### Unit Tests

**Targeting Translation** (`tests/unit/test_gam_axe_segment_targeting.py`):
- Include segment translation
- Exclude segment translation (NOT_ prefix)
- Both include and exclude together
- Combination with other custom targeting
- Optional AXE segments

**Inventory Sync Integration** (`tests/unit/test_gam_axe_inventory_sync.py`):
- Auto-creation of AXE key during sync
- Reading custom key name from config
- Handling existing keys
- Error handling and graceful degradation
- Custom key name usage

### Running Tests

```bash
# All AXE tests
uv run pytest tests/unit/test_gam_axe*.py -v

# Targeting translation only
uv run pytest tests/unit/test_gam_axe_segment_targeting.py -v

# Inventory sync integration only
uv run pytest tests/unit/test_gam_axe_inventory_sync.py -v
```

## Deployment Checklist

1. ✅ Run database migration: `uv run python migrate.py`
2. ✅ Verify migration applied: Check `adapter_config.gam_axe_custom_targeting_key` column exists
3. ✅ Run inventory sync for existing tenants
4. ✅ Verify AXE key created in GAM (check Custom Targeting in GAM UI)
5. ✅ Test media buy creation with `axe_include_segment`
6. ✅ Verify line item has correct custom targeting

## Troubleshooting

### Issue: AXE key not appearing in targeting picker

**Solution:**
1. Check adapter config has `gam_axe_custom_targeting_key` set (or uses default)
2. Run inventory sync: Admin UI → Inventory → Sync Inventory
3. Check logs for "AXE custom targeting key" messages
4. Verify key exists in GAM: Admin → Inventory → Custom targeting

### Issue: Targeting not applied to line items

**Solution:**
1. Check GAMTargetingManager logs for "Adding AXE" messages
2. Verify buyer request includes `axe_include_segment` or `axe_exclude_segment`
3. Check line item in GAM UI for custom targeting
4. Ensure key name in config matches key in GAM

### Issue: Permission errors during key creation

**Solution:**
1. Verify GAM service account or OAuth user has Trafficker role
2. Check Custom Targeting permissions in GAM
3. Test connection in Admin UI → Ad Server Settings
4. Review GAM API logs for permission errors

## Future Enhancements

1. **Dynamic Config Access**: Update GAMTargetingManager to read key name from adapter config at runtime
2. **Segment Value Validation**: Optionally validate segment IDs against discovered values
3. **Segment Metadata**: Store segment descriptions, targeting recommendations in database
4. **Bulk Segment Import**: Allow CSV upload of segment definitions
5. **Analytics Integration**: Track segment performance across campaigns

## Related Documentation

- [AdCP 3.0.3 Specification](https://adcontextprotocol.org/docs/)
- [GAM Custom Targeting Guide](docs/gam/custom-targeting.md)
- [Inventory Sync Architecture](docs/architecture/inventory-sync.md)
- [Targeting Browser UI](docs/ui/targeting-browser.md)
