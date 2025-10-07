# GAM Order Naming System

## Current Status (2025-10-06) - IMPLEMENTED

**✅ PLATFORM FEATURE**: Publishers can now configure their own GAM order/line item naming conventions through the admin UI.

**What We Built**:
- ✅ Configurable naming templates with variable substitution
- ✅ Fallback syntax for optional fields (e.g., `{campaign_name|promoted_offering}`)
- ✅ Publisher-specific naming conventions instead of hardcoded format
- ✅ Admin UI for easy configuration

**Why**: Publishers have their own naming conventions. Some want dates, others want advertiser names, others want brief summaries. This should be configurable, not hardcoded.

## Code Locations Fixed

### 1. `src/adapters/google_ad_manager.py` (line 298)
**Before:**
```python
order_id = self.orders_manager.create_order(
    order_name=f"{request.campaign_name} - {len(packages)} packages",  # ❌ campaign_name can be None
    ...
)
```

**After:**
```python
campaign_identifier = request.campaign_name or request.promoted_offering
order_id = self.orders_manager.create_order(
    order_name=f"{campaign_identifier} - {len(packages)} packages",  # ✅ Always has value
    ...
)
```

### 2. `src/adapters/gam/managers/workflow.py` (lines 144, 152, 158, 199)
**Before:**
```python
"campaign_name": request.campaign_name,  # ❌ Can be None
f"Set order name to: {request.campaign_name}",  # ❌ Can show "None"
```

**After:**
```python
campaign_identifier = request.campaign_name or request.promoted_offering
"campaign_name": campaign_identifier,  # ✅ Always has value
f"Set order name to: {campaign_identifier}",  # ✅ Shows actual campaign name
```

## Why This Matters

**AdCP Spec Design**:
- `promoted_offering`: **Required** field - "Description of advertiser and what is being promoted"
- `campaign_name`: **Optional** field - "Campaign name for display purposes"

**Best Practice**: Always use required fields for critical operations like naming. Optional fields should only be used as enhancements when present.

---

## Historical Context: buyer_ref Auto-Generation Bug (Also Fixed)

Previously, Wonderstruck generated generic package names like "None - 1 packages" because **we weren't sending `buyer_ref` values**. This was OUR bug - we were incorrectly auto-generating `buyer_ref` when clients used the legacy format, which violated the AdCP principle that `buyer_ref` is the **buyer's identifier**, not ours to create.

## Root Cause: Our Bug

**The Real Issue**: In `src/core/schemas.py`, when converting legacy `product_ids` format to packages, we were auto-generating `buyer_ref` values:

```python
# WRONG - What we were doing (schemas.py:1917-1930)
if not values.get("buyer_ref"):
    values["buyer_ref"] = f"buy_{uuid.uuid4().hex[:8]}"  # ❌ Auto-generating

for i, pid in enumerate(product_ids):
    packages.append({
        "buyer_ref": f"pkg_{i}_{package_uuid}",  # ❌ Auto-generating
        "products": [pid]
    })
```

**Why This is Wrong**: `buyer_ref` is defined in the AdCP spec as "Buyer's reference identifier" - it's the **buyer's responsibility** to provide this, not ours to auto-generate.

## The Fix

We removed the auto-generation logic:

```python
# CORRECT - What we do now
# Do NOT auto-generate buyer_ref - it's optional and must come from buyer
for i, pid in enumerate(product_ids):
    packages.append({
        # buyer_ref is NOT included unless explicitly provided by buyer
        "products": [pid],
        "status": "draft"
    })
```

## Impact on Wonderstruck

**Before the fix**: We were sending auto-generated `buyer_ref` values that Wonderstruck may have been ignoring (correctly, since they weren't meaningful buyer identifiers).

**After the fix**: We no longer send `buyer_ref` unless the buyer explicitly provides it, which is spec-compliant behavior.

**For Wonderstruck**: They should:
1. Use `buyer_ref` when present (buyer-provided identifier)
2. Fall back to `promoted_offering` + package index when `buyer_ref` is absent
3. Example: "Nike Shoes Q1 - Package 1" instead of "None - 1 packages"

## AdCP Spec Compliance

From the AdCP Package Schema:

```yaml
Package:
  type: object
  properties:
    buyer_ref:
      type: string
      description: "Buyer's reference identifier for this package"
      # This is OPTIONAL and should only be set by the buyer
```

Key points:
- ✅ `buyer_ref` is optional (not required)
- ✅ It's the buyer's identifier (not server-generated)
- ✅ Servers should respect it when provided, but not fabricate it

## What Buyers Should Do

If buyers want meaningful package names in Wonderstruck (or any other platform), they should provide `buyer_ref`:

```json
{
  "promoted_offering": "Nike Shoes Q1",
  "packages": [
    {
      "buyer_ref": "nike_q1_display_728x90",  // ✅ Buyer provides this
      "products": ["prod_1"]
    },
    {
      "buyer_ref": "nike_q1_video_preroll",   // ✅ Buyer provides this
      "products": ["prod_2"]
    }
  ]
}
```

## What Publishers Should Do (Fallback Naming Best Practices)

When `buyer_ref` is not provided (which is valid per spec), publishers should generate meaningful names from other fields:

### Option 1: Use promoted_offering + index (✅ Now implemented)
```python
campaign_identifier = request.campaign_name or request.promoted_offering
order_name = f"{campaign_identifier} - {len(packages)} packages"
```
Result: "Nike Shoes Q1 - 2 packages"

### Option 2: Use product names
```
Name: "Nike Shoes Q1 - Display 728x90"
Name: "Nike Shoes Q1 - Video Pre-roll"
```

### Option 3: Use package_id (server-generated)
```
Name: "pkg_0_abc123"
Name: "pkg_1_def456"
```

## Changes Made

### Code Changes
1. **google_ad_manager.py** (line 298): Use `promoted_offering` with fallback to `campaign_name`
2. **gam/managers/workflow.py** (lines 144, 152, 158, 199): Same fix for workflow steps
3. **schemas.py** (previously): Removed `buyer_ref` auto-generation

---

## Summary

## Naming Template System

### Configuration

Publishers configure naming templates in **Admin UI → Settings → Ad Server → Naming Templates**:

**Order Name Template** (default: `{campaign_name|promoted_offering} - {date_range}`):
- Variables: `{campaign_name}`, `{promoted_offering}`, `{buyer_ref}`, `{date_range}`, `{month_year}`, `{package_count}`
- Fallback syntax: `{var1|var2}` uses var1 if present, otherwise var2
- Examples:
  - `"{campaign_name} - {month_year}"` → "Q1 Launch - Oct 2025"
  - `"{promoted_offering} - {date_range}"` → "Nike Shoes - Oct 7-14, 2025"
  - `"{buyer_ref|campaign_name}"` → Uses buyer's ref or campaign name

**Line Item Name Template** (default: `{product_name}`):
- Variables: `{product_name}`, `{order_name}`, `{package_index}`
- Examples:
  - `"{product_name}"` → "Display 300x250"
  - `"{order_name} - {product_name}"` → "Q1 Launch - Video Pre-roll"

### Implementation

**Template Processing** (`src/adapters/gam/utils/naming.py`):
- `apply_naming_template(template, context)` - Variable substitution with fallbacks
- `build_order_name_context()` - Build variables from request data
- `format_date_range()` - Smart date formatting (same month, different months, different years)

**Database Schema** (`adapter_config` table):
- `gam_order_name_template` (String, 500 chars)
- `gam_line_item_name_template` (String, 500 chars)

**Migration**: `ede76bc258af_add_naming_templates_to_adapter_config.py`

### Benefits

1. **Publisher Control**: Each publisher defines their own naming convention
2. **Consistency**: Templates ensure consistent naming across all orders
3. **Flexibility**: Support for multiple naming strategies (dates, advertiser, brief)
4. **Spec Compliant**: Works with all AdCP fields (required and optional)

---

**Two Issues Resolved:**
1. ✅ Using optional `campaign_name` instead of required `promoted_offering` → Fixed with configurable templates
2. ✅ Auto-generating `buyer_ref` values (previous fix) → Still fixed

**Status**: Platform feature complete. Publishers can configure their own naming conventions.
