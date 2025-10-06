# Wonderstruck Package Name Generation - Root Cause Analysis

## Summary

Wonderstruck generates generic package names like "None - 1 packages" because **we weren't sending `buyer_ref` values**. This was OUR bug - we were incorrectly auto-generating `buyer_ref` when clients used the legacy format, which violated the AdCP principle that `buyer_ref` is the **buyer's identifier**, not ours to create.

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

## What Publishers Should Do (Fallback Naming)

When `buyer_ref` is not provided (which is valid per spec), publishers should generate meaningful names from other fields:

### Option 1: Use promoted_offering + index
```
Name: "Nike Shoes Q1 - Package 1"
Name: "Nike Shoes Q1 - Package 2"
```

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

**Current Wonderstruck Behavior**: "None - 1 packages" suggests they're not reading `promoted_offering` or any other meaningful fields.

## Changes Made

### Code Changes
1. **schemas.py**: Removed `buyer_ref` auto-generation in legacy format conversion
2. **Test Updates**: Updated tests to expect `buyer_ref = None` when not provided by buyer

### Documentation
This document explains:
- Why we stopped auto-generating `buyer_ref`
- What the spec says about `buyer_ref`
- How publishers should handle missing `buyer_ref`

## Classification

- **Root Cause**: Our implementation bug (auto-generating buyer identifiers)
- **Impact**: Publishers see generic names when buyers don't provide `buyer_ref`
- **Fix**: Stop auto-generating, let buyers provide meaningful identifiers
- **Publisher Action**: Implement fallback naming when `buyer_ref` is absent

---

**Status**: Fixed in this commit
**Priority**: Medium (affects UX but not functionality)
**Spec Compliance**: Now compliant (buyer_ref is optional, buyer-provided)
