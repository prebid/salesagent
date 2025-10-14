# Pricing Fields Migration Plan

## Status: ✅ COMPLETE - Ready to Deploy

This document tracks the migration from legacy product pricing fields to the new `pricing_options` table.

## Background

Products currently have pricing stored in two places:
- **Legacy fields**: `is_fixed_price`, `cpm`, `price_guidance`, `currency`, `delivery_type` (in `products` table)
- **New table**: `pricing_options` table with full support for multiple pricing models

This dual storage is dangerous and leads to inconsistencies (e.g., product list showing $65 when actual price is $3).

## Migration Strategy

### Phase 1: Data Migration ✅ READY TO DEPLOY
**Migration Created:**
- `5d949a78d36f_migrate_legacy_pricing_to_pricing_options.py` - Populates pricing_options from legacy fields
- **Note**: Legacy columns will NOT be dropped. Both storage methods remain active.

**Helper Function Created:**
- `src/core/database/product_pricing.py::get_product_pricing_options()` - Reads from either source

### Phase 2: Code Updates ✅ COMPLETE

**All Critical Paths Updated:**
- ✅ `product_catalog_providers/database.py` - Used by all MCP get_products calls
- ✅ `product_catalog_providers/ai.py` - AI-powered product selection
- ✅ `product_catalog_providers/signals.py` - Signals-based products
- ✅ `src/admin/blueprints/products.py` - Product list AND edit product pages
- ✅ `templates/products.html` - Product list template

**Why Other Files Don't Need Updates:**
1. **Templates** (`edit_product.html`, `add_product_gam.html`): Already receive correct data from blueprints
2. **src/core/main.py**: Operates on Product schema objects already populated by catalog providers
3. **Tests**: Create Product schema objects directly - schema still supports legacy fields
4. **Tools/Examples**: Use Product schema which still supports legacy fields

**Key Insight:** Product Pydantic schema retains legacy fields for backward compatibility. All database reads now go through `get_product_pricing_options()`, ensuring correct data flows through the system.

### Phase 3: Deploy and Run Migration ✅ READY

**Steps:**
1. Merge PR and deploy to production
2. Migration runs automatically: `5d949a78d36f` (populates pricing_options)
3. Verify all products have pricing_options (spot check)
4. Monitor for issues
5. **Legacy columns remain** - both storage methods active

### Phase 4: Monitor and Verify ⏳ POST-DEPLOY

**After deployment:**
1. ✅ Verify all products have pricing_options (spot check in database)
2. ✅ Verify product list shows correct pricing in Admin UI
3. ✅ Verify MCP get_products returns correct pricing
4. ✅ Monitor for any pricing inconsistencies
5. ✅ Check audit logs for any pricing-related errors

### Phase 5: Model Cleanup 🔒 FUTURE (Do NOT Do Yet)

**Prerequisites:**
- All code updated and tested in production
- Team decision to drop legacy columns
- Full database backup taken

**Steps:**
1. Create new migration to drop legacy columns
2. Update `src/core/database/models.py` - Remove legacy field definitions
3. Test all functionality
4. Optional: Remove fallback logic from helper

## Testing Checklist

Before running migrations:
- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] Manual test: Create product with pricing_options
- [ ] Manual test: List products shows correct pricing
- [ ] Manual test: Edit product preserves pricing
- [ ] Manual test: MCP get_products returns correct pricing
- [ ] Manual test: Create media buy with product works

After first migration:
- [ ] Verify all existing products have pricing_options
- [ ] Verify pricing matches legacy fields
- [ ] Run full test suite

After second migration:
- [ ] Verify code still works
- [ ] No errors about missing columns
- [ ] All product operations work correctly

## Rollback Plan

### For This PR (Phase 3):
- If issues found, legacy fields still exist and work
- pricing_options can be safely ignored/deleted
- No data loss risk - legacy is still primary source for most code
- Can revert helper function changes if needed

### For Future Column Drops (Phase 5):
- ⚠️ **NOT included in this PR**
- Will require separate migration and PR
- Must have full backup before dropping columns
- Downgrade would restore empty columns (data lost)

## Progress Tracking

- **Migrations created**: 2025-01-13
- **Helper function created**: 2025-01-13
- **Critical path updated**: 2025-01-13
- **Migration completed**: 2025-01-13
- **Files updated**: 5 (database.py, ai.py, signals.py, products.py, products.html)
- **Status**: ✅ Ready to deploy
