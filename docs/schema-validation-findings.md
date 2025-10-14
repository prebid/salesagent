# Schema Validation Findings: Manual vs Generated

## Summary

Comparison of manually-maintained schemas (`src.core.schemas`) vs auto-generated schemas from AdCP JSON Schema (`src.core.schemas_generated`).

## GetProductsRequest

### ✅ FIXED: Removed Non-Spec Fields
- `webhook_url` - Removed (not in AdCP spec)
- `min_exposures` - Removed (not in AdCP spec)
- `strategy_id` - Removed (not in AdCP spec)

### Remaining Type Mismatches (Expected)

1. **`brand_manifest`**:
   - Manual: `dict[str, Any] | None`
   - Generated: `BrandManifest | BrandManifest8 | AnyUrl | None`
   - **Impact**: We lose type safety by using `dict`
   - **Fix**: Use strong types from generated schemas

2. **`filters`**:
   - Manual: `ProductFilters | None`
   - Generated: `Filters | Filters1 | None`
   - **Impact**: Name difference, but both are structured filters
   - **Fix**: Align on generated schema types

3. **`promoted_offering` / `brand_manifest` requirement**:
   - Manual: Both optional
   - Generated: Variant 1 requires `promoted_offering`, Variant 2 requires `brand_manifest` (oneOf)
   - **Impact**: Generated schema is more strict (spec-compliant)
   - **Fix**: Accept that oneOf creates variants with different requirements

## GetProductsResponse

### 🚨 CRITICAL: `message` Field

**Status**: Field exists in manual schema, NOT in AdCP spec

**Current Usage**:
```python
# In src/core/main.py:
base_message = f"Found {len(modified_products)} matching products"
final_message = f"{base_message}. {pricing_message}" if pricing_message else base_message
return GetProductsResponse(products=modified_products, message=final_message, status=status)
```

**Analysis**:
- We added `message` to provide human-readable descriptions
- Example: "Found 5 products. Please connect through an authorized buying agent for pricing data"
- This is used to communicate with users via MCP

**MCP/A2A Pattern**:
- **MCP**: Human-readable messages belong in `content` field at protocol layer (not in response schema)
- **A2A**: Response messages are part of A2A task state structure (not AdCP schema)
- **Conclusion**: `message` should be handled at protocol layer, not in AdCP schema

**Recommendation**:
1. **Remove `message` from GetProductsResponse schema** (not AdCP-compliant)
2. **For MCP**: Return human-readable message via MCP `content` field
3. **For A2A**: Use A2A task status messages
4. **Protocol-agnostic**: Log important info instead of embedding in response

### Other Type Mismatches (Minor)

1. **`products`**:
   - Manual: `list[Product]`
   - Generated: `list[Products | Products1]` (oneOf variants)
   - **Impact**: Generated has variant types for product structure
   - **Fix**: Use generated types

2. **`status`**:
   - Manual: `Literal["completed", "working", "submitted"] | None`
   - Generated: `Status | None` (enum)
   - **Impact**: Generated uses proper enum type
   - **Fix**: Use generated `Status` enum

3. **`errors`**:
   - Manual: `list[Error] | None`
   - Generated: `list[Error] | None`
   - **Impact**: Both same, type comparison showing false difference
   - **Fix**: None needed

## Action Items

### High Priority

1. ✅ **Remove non-spec fields** from GetProductsRequest:
   - `webhook_url`, `min_exposures`, `strategy_id` - **DONE**

2. 🚨 **Address `message` field** in GetProductsResponse:
   - [ ] Remove `message` field from schema
   - [ ] Handle human-readable messages at MCP/A2A protocol layer
   - [ ] Update code that constructs GetProductsResponse

3. **Use strong types** instead of `dict[str, Any]`:
   - [ ] `brand_manifest`: Use `BrandManifest | BrandManifest8 | AnyUrl`
   - [ ] `filters`: Use generated `Filters | Filters1`

### Medium Priority

4. **Align enum types**:
   - [ ] Use generated `Status` enum instead of `Literal`
   - [ ] Use generated `Products | Products1` types

### Migration Strategy

**Option A: Gradual Migration (Recommended)**
1. Keep both manual and generated schemas for now
2. Fix schema differences one by one
3. Add validation tests (already done!)
4. Migrate code to use generated schemas incrementally
5. Remove manual schemas only when all code migrated

**Option B: Adapter Layer with Strong Types**
1. Keep adapter pattern
2. Make adapters use generated types (not `dict[str, Any]`)
3. Adapters handle oneOf complexity + backward compatibility
4. Full type safety with ergonomic API

**Option C: Generated Schemas + Helpers (Cleanest)**
1. Use generated schemas directly everywhere
2. Create helper functions for construction (hide oneOf complexity)
3. Maximum type safety, minimal wrapper code
4. Accept some API complexity for correctness

## Recommendation

Start with **Option A** (gradual migration):
1. Fix critical issues (message field)
2. Add strong types incrementally
3. Validate with tests at each step
4. Only fully migrate once all differences resolved

This approach minimizes risk and allows us to catch issues early.
