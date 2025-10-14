# Validator Analysis: Which Can Be Fixed in Schema?

## Summary

After analyzing all 16 custom validators, here's what can be moved to JSON Schema:

**✅ Can Move to Schema (3 validators - after correction):**
1. ~~BrandManifest.validate_required_fields~~ - ✅ **ALREADY CORRECT** (uses `anyOf`, not `oneOf`)
2. GetProductsRequest.validate_brand_or_offering - `oneOf` constraint (✅ DONE)
3. ~~AdCPPackageUpdate.validate_oneOf_constraint~~ - ✅ **ALREADY IN SCHEMA**
4. ~~UpdateMediaBuyRequest.validate_oneOf_constraint~~ - ✅ **ALREADY IN SCHEMA**
5. PricingOption.validate_pricing_option - Conditional `required` fields
6. Creative.validate_creative_fields - Mutual exclusivity (`oneOf`)

**❌ Must Stay in Code (9 validators):**
- 5x validate_timezone_aware - Runtime Python check
- 2x Backward compatibility transforms
- 2x Data normalization transforms

**⚠️ Borderline (1 validator):**
- Product.validate_pricing_fields - Can be done but complex

---

## Detailed Analysis

### ✅ CAN MOVE TO SCHEMA

#### 1. BrandManifest.validate_required_fields [Line 2263]

**Status:** ✅ **ALREADY CORRECT IN SCHEMA**

**Current Python:**
```python
@model_validator(mode="after")
def validate_required_fields(self) -> "BrandManifest":
    """Ensure at least one of url or name is present."""
    if not self.url and not self.name:
        raise ValueError("BrandManifest requires at least one of: url, name")
    return self
```

**Schema Already Has:**
```json
{
  "$id": "/schemas/v1/core/brand-manifest.json",
  "anyOf": [
    {"required": ["url"]},
    {"required": ["name"]}
  ]
}
```

**Why anyOf (not oneOf):** The Python validator uses "at least one" logic (`if not self.url and not self.name`), which correctly maps to `anyOf`. Using `oneOf` would incorrectly reject brand manifests that have BOTH url and name. The schema already correctly uses `anyOf`.

**Action:** ✅ None needed - Python validator matches schema constraint. Can remove Python validator.

---

#### 2. GetProductsRequest.validate_brand_or_offering [Line 1376]

**Status:** ✅ **ALREADY FIXED** - You just did this!

---

#### 3. AdCPPackageUpdate.validate_oneOf_constraint [Line 2917]

**Status:** ✅ **ALREADY IN SCHEMA**

**Current Python:**
```python
@model_validator(mode="after")
def validate_oneOf_constraint(self):
    """Validate that either package_id OR buyer_ref is provided (AdCP oneOf constraint)."""
    if not self.package_id and not self.buyer_ref:
        raise ValueError("Either package_id or buyer_ref must be provided")
    return self
```

**Schema Already Has:**
```json
{
  "items": {
    "properties": {
      "package_id": {"type": "string", "description": "Publisher's ID of package to update"},
      "buyer_ref": {"type": "string", "description": "Buyer's reference for the package to update"}
    },
    "oneOf": [
      {"required": ["package_id"]},
      {"required": ["buyer_ref"]}
    ]
  }
}
```

**Note:** Both `package_id` and `buyer_ref` are **seller-provided identifiers** that the buyer uses to reference packages:
- `package_id`: Publisher's ID (from create_media_buy response)
- `buyer_ref`: Buyer's own reference (from packages[].buyer_ref in create request)

The buyer chooses which identifier to use when updating. The validator logic should be `anyOf` (at least one), not strict `oneOf`, since providing both is redundant but not incorrect. However, the AdCP spec uses `oneOf`.

**Action:** ✅ Schema is correct. Python validator can be removed (relies on schema).

---

#### 4. UpdateMediaBuyRequest.validate_oneOf_constraint [Line 2952]

**Status:** ✅ **ALREADY IN SCHEMA**

**Current Python:**
```python
@model_validator(mode="after")
def validate_oneOf_constraint(self):
    """Validate AdCP oneOf constraint: either media_buy_id OR buyer_ref."""
    if not self.media_buy_id and not self.buyer_ref:
        raise ValueError("Either media_buy_id or buyer_ref must be provided")
    if self.media_buy_id and self.buyer_ref:
        raise ValueError("Cannot provide both media_buy_id and buyer_ref (AdCP oneOf constraint)")
    return self
```

**Schema Already Has:**
```json
{
  "properties": {
    "media_buy_id": {"type": "string", "description": "Publisher's ID of the media buy to update"},
    "buyer_ref": {"type": "string", "description": "Buyer's reference for the media buy to update"}
  },
  "oneOf": [
    {"required": ["media_buy_id"]},
    {"required": ["buyer_ref"]}
  ]
}
```

**Note:** Both `media_buy_id` and `buyer_ref` are **seller-provided identifiers** that the buyer uses to reference media buys:
- `media_buy_id`: Publisher's ID (from create_media_buy response)
- `buyer_ref`: Buyer's own reference (from create_media_buy request)

The buyer chooses which identifier to use when updating. Similar to package updates, the validator logic might work as `anyOf` (at least one), but the AdCP spec uses strict `oneOf` to enforce exactly one identifier.

**Action:** ✅ Schema is correct. Python validator can be removed (relies on schema).

---

#### 5. PricingOption.validate_pricing_option [Line 150]

**Current Python:**
```python
@model_validator(mode="after")
def validate_pricing_option(self) -> "PricingOption":
    if self.is_fixed and self.rate is None:
        raise ValueError("rate is required when is_fixed=true")
    if not self.is_fixed and self.price_guidance is None:
        raise ValueError("price_guidance is required when is_fixed=false")
    return self
```

**JSON Schema Fix:**
```json
{
  "type": "object",
  "properties": {
    "is_fixed": {"type": "boolean"},
    "rate": {"type": "number"},
    "price_guidance": {"type": "object"}
  },
  "oneOf": [
    {
      "properties": {"is_fixed": {"const": true}},
      "required": ["rate"]
    },
    {
      "properties": {"is_fixed": {"const": false}},
      "required": ["price_guidance"]
    }
  ]
}
```

**Why this works:** Conditional requirements based on field value

**Action:** Add to pricing-option schema (need to check if it exists)

---

#### 6. Creative.validate_creative_fields [Line 1768]

**Current Python:**
```python
@model_validator(mode="after")
def validate_creative_fields(self) -> "Creative":
    has_media = bool(self.media_url or (self.url and not self._is_html_snippet(self.url)))
    has_snippet = bool(self.snippet)

    if has_media and has_snippet:
        raise ValueError("Creative cannot have both media content and snippet")

    if self.snippet and not self.snippet_type:
        raise ValueError("snippet_type is required when snippet is provided")

    if self.snippet_type and not self.snippet:
        raise ValueError("snippet is required when snippet_type is provided")

    return self
```

**JSON Schema Fix:**
```json
{
  "type": "object",
  "properties": {
    "media_url": {"type": "string"},
    "url": {"type": "string"},
    "snippet": {"type": "string"},
    "snippet_type": {"enum": ["html", "javascript"]}
  },
  "oneOf": [
    {
      "properties": {
        "media_url": {"type": "string"},
        "snippet": {"not": {}}
      }
    },
    {
      "required": ["snippet", "snippet_type"],
      "properties": {
        "media_url": {"not": {}},
        "url": {"not": {}}
      }
    }
  ]
}
```

**Why this might not work:** The `_is_html_snippet()` method call makes this complex

**Action:** **REVIEW** - Might be too complex for pure schema

---

###❌ MUST STAY IN CODE

#### 7-11. *.validate_timezone_aware (5 instances)

**Why:** JSON Schema can't validate timezone presence in datetime strings
- Python-specific runtime check
- Validates tzinfo is not None

**Keep in code:** ✅ YES

---

#### 12. BrandManifestRef.parse_manifest_ref [Line 2284]

**Why:** This transforms data (string → object)
- Not validation, but normalization
- Modifies input

**Keep in code:** ✅ YES

---

#### 13. CreateMediaBuyRequest.handle_legacy_format [Line 2437]

**Why:** Backward compatibility transformation
- Converts promoted_offering → brand_manifest
- Modifies input for legacy clients

**Keep in code:** ✅ YES (already fixed!)

---

#### 14. PropertyTagMetadata.normalize_tags [Line 3505]

**Why:** Data normalization/transformation

**Keep in code:** ✅ YES

---

### ⚠️ BORDERLINE

#### 15. Product.validate_pricing_fields [Line 1118]

**Current Python:**
```python
@model_validator(mode="after")
def validate_pricing_fields(self) -> "Product":
    has_pricing_options = self.pricing_options is not None and len(self.pricing_options) > 0
    has_legacy_pricing = self.is_fixed_price is not None

    if not has_pricing_options and not has_legacy_pricing:
        raise ValueError("Product must have either pricing_options or legacy pricing fields")

    return self
```

**JSON Schema Fix (Possible but Complex):**
```json
{
  "oneOf": [
    {
      "required": ["pricing_options"],
      "properties": {
        "pricing_options": {
          "type": "array",
          "minItems": 1
        }
      }
    },
    {
      "required": ["is_fixed_price"]
    }
  ]
}
```

**Why borderline:** Works but represents backward compat, might be clearer in code

**Recommendation:** **CAN MOVE** but consider keeping for clarity

---

## Action Plan

### ✅ Already Done (No Action Needed)

1. ~~**BrandManifest**~~ - Already uses `anyOf` in schema (correct)
2. ~~**UpdateMediaBuyRequest**~~ - Already has `oneOf` in schema
3. ~~**AdCPPackageUpdate**~~ - Already has `oneOf` in schema
4. **GetProductsRequest** - ✅ Fixed in PR #364

### Remaining Actions

1. **Check if pricing-option.json exists in AdCP spec**
   - If yes: Add conditional required constraint
   - If no: Keep Python validator

2. **Review Creative.validate_creative_fields**
   - Determine if complex logic can be expressed in JSON Schema
   - Probably too complex - keep in code

3. **Remove redundant Python validators** (once schema validation is trusted):
   - BrandManifest.validate_required_fields (relies on anyOf)
   - AdCPPackageUpdate.validate_oneOf_constraint (relies on oneOf)
   - UpdateMediaBuyRequest.validate_oneOf_constraint (relies on oneOf)

---

## Benefits (Updated After Review)

✅ **3-4 validators already in schema** (can remove Python code)
✅ **1-2 validators could move** (pending spec check)
✅ **9 validators stay in code** (legitimately need Python)
✅ **Net result:** ~25-30% reduction in custom validators
✅ **Spec compliance:** Most validation already in schema!
✅ **Cross-platform:** Schema validation works everywhere!

---

## Files to Update

### AdCP JSON Schemas (propose changes)
- `/schemas/v1/core/brand-manifest.json` - Add oneOf
- `/schemas/v1/media-buy/update-media-buy-request.json` - Add oneOf
- `/schemas/v1/core/pricing-option.json` - Add conditional required (if exists)
- Check if package-update schema exists

### After Schema Updates
1. Run `python scripts/generate_schemas.py`
2. Remove redundant Python validators
3. Update tests

---

## Summary Table (Corrected)

| Validator | Move to Schema? | Status | Action |
|-----------|----------------|--------|--------|
| BrandManifest.validate_required_fields | ✅ ALREADY DONE | Uses anyOf | Remove Python validator |
| GetProductsRequest.validate_brand_or_offering | ✅ DONE | Uses oneOf | ✅ Complete (PR #364) |
| AdCPPackageUpdate.validate_oneOf_constraint | ✅ ALREADY DONE | Uses oneOf | Remove Python validator |
| UpdateMediaBuyRequest.validate_oneOf_constraint | ✅ ALREADY DONE | Uses oneOf | Remove Python validator |
| PricingOption.validate_pricing_option | ⚠️ PENDING | Medium | Check if schema exists |
| Creative.validate_creative_fields | ⚠️ MAYBE | Hard | Review (complex logic) |
| Product.validate_pricing_fields | ⚠️ MAYBE | Medium | Optional |
| validate_timezone_aware (5x) | ❌ NO | N/A | Keep (Python runtime) |
| Transforms/Compat (4x) | ❌ NO | N/A | Keep (data transforms) |

**Total Already in Schema: 3-4 validators (can remove Python code)**
**Total Moveable: 1-2 validators (pending review)**
**Must Keep: 9 validators (legitimately need Python)**
**Reduction: ~25-35%**
