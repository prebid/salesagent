# Custom Validator Analysis: Schema vs Code

## Objective
Determine which custom validators should be replaced with JSON Schema constraints vs staying in Python code.

---

## Validators Found

### 1. PricingOption.validate_pricing_option() [Line 150]

**What it does**: Validates pricing option fields

**Can be in schema?** ⚠️ PARTIALLY
- Basic field validation: YES
- Complex business rules: NO

**Recommendation**: **KEEP IN CODE** - Likely has business logic

---

### 2. Product.validate_pricing_fields() [Line 1118]

**What it does**: Validates product pricing configuration

**Can be in schema?** ⚠️ PARTIALLY
- Field presence: YES
- Pricing model constraints: MAYBE

**Recommendation**: **REVIEW** - Check if it's just field validation

---

### 3. GetProductsRequest.validate_brand_or_offering() [Line 1376]

**What it does**: Ensures either promoted_offering OR brand_manifest

**Can be in schema?** ✅ YES!
- This is a `oneOf` constraint
- JSON Schema supports this natively

**Recommendation**: **MOVE TO SCHEMA** ✅
```json
{
  "oneOf": [
    {"required": ["promoted_offering"]},
    {"required": ["brand_manifest"]}
  ]
}
```

**Status**: ✅ ALREADY DONE - You just fixed this!

---

### 4. Creative.validate_creative_fields() [Line 1768]

**What it does**: Validates creative asset fields

**Can be in schema?** ⚠️ DEPENDS
- Field requirements: YES
- Cross-field validation: MAYBE

**Recommendation**: **REVIEW** - Check the logic

---

### 5-7. *.validate_timezone_aware() [Lines 1832, 2018, 2124, 2512, 2962]

**What it does**: Ensures datetime fields have timezone info

**Can be in schema?** ❌ NO
- JSON Schema doesn't validate timezone presence
- Runtime Python check required

**Recommendation**: **KEEP IN CODE** - Python-specific validation

---

### 8. BrandManifest.validate_required_fields() [Line 2263]

**What it does**: Ensures at least one of `url` OR `name` is present

**Can be in schema?** ✅ YES!
- This is a `oneOf` constraint
- JSON Schema supports this

**Recommendation**: **MOVE TO SCHEMA** ✅
```json
{
  "oneOf": [
    {"required": ["url"]},
    {"required": ["name"]}
  ]
}
```

---

### 9. BrandManifestRef.parse_manifest_ref() [Line 2284]

**What it does**: Parses brand manifest reference (string vs object)

**Can be in schema?** ⚠️ PARTIALLY
- Union type: YES (`string | object`)
- Parsing logic: NO

**Recommendation**: **KEEP IN CODE** - Transformation logic

---

### 10. CreateMediaBuyRequest.handle_legacy_format() [Line 2437]

**What it does**: Backward compatibility for promoted_offering → brand_manifest

**Can be in schema?** ❌ NO
- This is transformation, not validation
- Needs to modify input data

**Recommendation**: **KEEP IN CODE** - Backward compatibility logic

**Status**: ✅ ALREADY DONE - You just fixed this!

---

### 11-12. AdCPPackageUpdate & UpdateMediaBuyRequest.validate_oneOf_constraint() [Lines 2917, 2952]

**What it does**: Validates `oneOf` constraints:
- AdCPPackageUpdate: `package_id` OR `buyer_ref`
- UpdateMediaBuyRequest: `media_buy_id` OR `buyer_ref`

**Can be in schema?** ✅ YES!
- Both are `oneOf` constraints
- JSON Schema supports this natively

**Recommendation**: **MOVE TO SCHEMA** ✅

**AdCPPackageUpdate:**
```json
{
  "oneOf": [
    {"required": ["package_id"]},
    {"required": ["buyer_ref"]}
  ]
}
```

**UpdateMediaBuyRequest:**
```json
{
  "oneOf": [
    {"required": ["media_buy_id"]},
    {"required": ["buyer_ref"]}
  ]
}
```

---

### 13. SignalDeliverTo.validate_accounts_structure() [Line 3286]

**What it does**: Validates signal delivery target structure

**Can be in schema?** ⚠️ DEPENDS
- Need to see the actual logic

**Recommendation**: **REVIEW**

---

### 14. PropertyTagMetadata.normalize_tags() [Line 3505]

**What it does**: Normalizes tag data

**Can be in schema?** ❌ NO
- This is transformation, not validation
- Modifies input data

**Recommendation**: **KEEP IN CODE** - Data transformation

---

## Summary

| Validator | Can be Schema? | Recommendation |
|-----------|---------------|----------------|
| PricingOption.validate_pricing_option | ⚠️ Partial | **REVIEW** |
| Product.validate_pricing_fields | ⚠️ Partial | **REVIEW** |
| GetProductsRequest.validate_brand_or_offering | ✅ Yes | ✅ **DONE** |
| Creative.validate_creative_fields | ⚠️ Depends | **REVIEW** |
| *.validate_timezone_aware (5x) | ❌ No | **KEEP** (Python-specific) |
| BrandManifest.validate_required_fields | ✅ Yes | **MOVE TO SCHEMA** |
| BrandManifestRef.parse_manifest_ref | ❌ No | **KEEP** (transformation) |
| CreateMediaBuyRequest.handle_legacy_format | ❌ No | **KEEP** (compat) |
| AdCPPackageUpdate.validate_oneOf_constraint | ✅ Yes | **MOVE TO SCHEMA** |
| UpdateMediaBuyRequest.validate_oneOf_constraint | ✅ Yes | **MOVE TO SCHEMA** |
| SignalDeliverTo.validate_accounts_structure | ⚠️ Depends | **REVIEW** |
| PropertyTagMetadata.normalize_tags | ❌ No | **KEEP** (transformation) |

---

## Action Plan

### Phase 1: Easy Wins (oneOf constraints)

These can be moved to JSON Schema immediately:

1. **BrandManifest.validate_required_fields**
   - Add `oneOf: [{"required": ["url"]}, {"required": ["name"]}]` to schema
   - Remove Python validator

2. **AdCPPackageUpdate.validate_oneOf_constraint**
   - Add `oneOf` to schema
   - Remove Python validator

3. **UpdateMediaBuyRequest.validate_oneOf_constraint**
   - Add `oneOf` to schema
   - Remove Python validator

### Phase 2: Review Complex Validators

Need to examine the actual logic:

1. **PricingOption.validate_pricing_option**
2. **Product.validate_pricing_fields**
3. **Creative.validate_creative_fields**
4. **SignalDeliverTo.validate_accounts_structure**

### Phase 3: Keep These (Runtime/Transform logic)

These MUST stay in Python code:

1. **validate_timezone_aware** (5 instances) - Runtime check
2. **BrandManifestRef.parse_manifest_ref** - Transformation
3. **CreateMediaBuyRequest.handle_legacy_format** - Backward compat
4. **PropertyTagMetadata.normalize_tags** - Transformation

---

## Benefits of Moving to Schema

✅ **Spec Compliance** - Validation rules in official spec
✅ **Auto-Generated** - Future schema updates include validation
✅ **Cross-Language** - Works for TypeScript, Python, etc.
✅ **Documentation** - Validation rules visible in schema
✅ **Client-Side** - Can validate before sending requests

---

## Implementation Strategy

### Step 1: Update JSON Schemas

Add `oneOf` constraints to these schemas:
- `/schemas/v1/core/brand-manifest.json`
- `/schemas/v1/media-buy/update-media-buy-request.json`
- (Need to check if package update has separate schema)

### Step 2: Regenerate Pydantic Models

```bash
python scripts/generate_schemas.py
```

### Step 3: Remove Python Validators

Only remove validators that are now in the schema.

### Step 4: Test

```bash
pytest tests/unit/test_adcp_contract.py -v
```

---

## Open Questions

1. **Do these schemas exist in AdCP spec or are they internal?**
   - AdCPPackageUpdate
   - SignalDeliverTo
   - PropertyTagMetadata

2. **Can we propose oneOf constraints to AdCP maintainers?**
   - If missing from spec, file PR to add them

3. **What's the actual logic in the "REVIEW" validators?**
   - Need to read each one to determine if schema-capable

---

## Next Steps

1. ✅ Read each "REVIEW" validator to understand logic
2. Update JSON schemas with `oneOf` constraints
3. File PR with AdCP if constraints missing from spec
4. Regenerate Pydantic models
5. Remove redundant Python validators
6. Test thoroughly
