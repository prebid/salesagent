# Code Review: Adapter Layer

## Summary

The adapter layer has been substantially updated for the adcp 3.2.0 to 3.6.0 migration. The primary changes are:

- **V3 pricing model**: `is_fixed` flag removed from AdCP Pydantic schema; `rate` renamed to `fixed_price` (fixed pricing) or `floor_price` (auction). The internal `pricing_info` dict used between the core layer and adapters still uses `rate` + `is_fixed` keys (sourced from the ORM `PricingOption.rate` column), which is correct and consistent.
- **V3 brand field**: `brand_manifest` on `CreateMediaBuyRequest` replaced by `brand` (a `BrandReference` / dict).
- **Channel names**: V3 renames `video` to `olv`, `native` to `social`, and adds `streaming_audio`.
- **Naming utilities**: Refactored to `src/core/utils/naming.py` with AI support; legacy copy survives in `src/adapters/gam/utils/naming.py`.

Two real bugs were found (CRIT-1). Three maintenance/correctness issues at High and Medium severity.

## Files Reviewed

1. `src/adapters/base.py`
2. `src/adapters/base_workflow.py`
3. `src/adapters/google_ad_manager.py`
4. `src/adapters/gam/client.py`
5. `src/adapters/gam/managers/orders.py`
6. `src/adapters/gam/managers/creatives.py`
7. `src/adapters/gam/managers/workflow.py`
8. `src/adapters/gam/utils/health_check.py`
9. `src/adapters/gam/utils/naming.py`
10. `src/adapters/gam_inventory_discovery.py`
11. `src/adapters/gam_orders_discovery.py`
12. `src/adapters/mock_ad_server.py`
13. `src/adapters/broadstreet/managers/workflow.py`
14. `src/adapters/xandr.py`
15. `src/adapters/test_scenario_parser.py`
16. `src/core/helpers/adapter_helpers.py`
17. `src/core/helpers/creative_helpers.py`
18. `src/services/dynamic_pricing_service.py`
19. `src/services/delivery_webhook_scheduler.py`
20. `src/services/policy_check_service.py`

## Critical Issues

### CRIT-1: `request.brand.domain` attribute access on a plain dict -- `AttributeError` at runtime

**Confidence: 95**

**Files and lines:**
- `src/adapters/mock_ad_server.py` line 466
- `src/adapters/broadstreet/managers/workflow.py` line 86

**Explanation:**

`CreateMediaBuyRequest.brand` is declared in `src/core/schemas.py` as `dict[str, Any] | None` (line 1484, with an explicit `# type: ignore[assignment]` annotation). Pydantic does NOT coerce this dict to a `BrandReference` object -- it stays as a plain Python `dict`. Both call sites access `.domain` as an attribute, which will raise `AttributeError` whenever `brand` is present in the request.

```python
# src/adapters/mock_ad_server.py:464-466 -- BROKEN
if request.brand:
    # BrandReference.domain is a str
    test_message = str(request.brand.domain) if request.brand.domain else None
    #                       ^^^^^^ AttributeError: 'dict' object has no attribute 'domain'

# src/adapters/broadstreet/managers/workflow.py:86 -- BROKEN
brand_name = (request.brand.domain if request.brand is not None else None) or "Unknown Brand"
#                         ^^^^^^ same crash
```

The canonical pattern is already defined in `src/core/utils/naming.py:_extract_brand_name` (lines 53-57):

```python
brand = request.brand
if hasattr(brand, "domain"):
    return brand.domain
elif isinstance(brand, dict):
    return brand.get("domain")
return None
```

**Impact:** Any `create_media_buy` call that provides a `brand` field will crash in the Mock adapter at keyword detection (line 464-466), and in Broadstreet at manual workflow step creation (line 86). This affects all real traffic and all integration tests that set `brand`.

**Fix:**

```python
# mock_ad_server.py
if request.brand:
    brand = request.brand
    domain = brand.get("domain") if isinstance(brand, dict) else getattr(brand, "domain", None)
    test_message = str(domain) if domain else None

# broadstreet/managers/workflow.py
brand = request.brand
brand_domain = (
    brand.get("domain") if isinstance(brand, dict) else getattr(brand, "domain", None)
)
brand_name = brand_domain or "Unknown Brand"
```

Alternatively, the root fix is to change `CreateMediaBuyRequest.brand` from `dict[str, Any] | None` to the library `BrandReference | None` type so Pydantic coerces the dict to the proper model.

## High Issues

### HIGH-1: Duplicated `build_order_name_context` in `gam/utils/naming.py` silently omits `{auto_name}` template support

**Confidence: 82**

**Files:**
- `src/adapters/gam/utils/naming.py` lines 102-142 -- older copy, no `{auto_name}` key in returned dict
- `src/core/utils/naming.py` lines 223-273 -- canonical version, has `{auto_name}` key

**Explanation:**

The V3 migration added AI-generated order naming via the `{auto_name}` template variable, implemented in `src/core/utils/naming.py`. However, `src/adapters/gam/utils/naming.py` still contains a duplicated `build_order_name_context` that does NOT include `auto_name` in its context dict. If a tenant configures a GAM order name template with `{auto_name}` and something imports from the wrong module, the variable silently resolves to empty string (template expansion falls back to `""`), rather than calling the AI service.

Currently the active call sites (`google_ad_manager.py:614`, `gam/managers/workflow.py:153`) both correctly import from `src.core.utils.naming`, so no bug is hit today. But the stale duplicate in `gam/utils/naming.py` is a maintenance hazard during this active migration period.

The `gam/utils/naming.py` module also has a different signature for `build_line_item_name_context` (positional `package_name`, `package_index`) vs the canonical version in `src/core/utils/naming.py` (keyword-only). This has already diverged.

**Fix:** Remove `build_order_name_context` from `src/adapters/gam/utils/naming.py`. Keep `apply_naming_template`, `truncate_name_with_suffix`, and `build_line_item_name_context` since they are directly imported by `gam/managers/orders.py` and use GAM-specific argument shapes.

### HIGH-2: `package_pricing_info` docstring uses V3 field names inconsistently with the actual runtime dict

**Confidence: 85**

**Files:**
- `src/adapters/base.py` line 265
- `src/adapters/mock_ad_server.py` line 444
- `src/adapters/google_ad_manager.py` line 367
- `src/adapters/gam/managers/orders.py` line 367

**Explanation:**

All four docstrings say:
```
Maps package_id -> {pricing_model, rate, currency, is_fixed, bid_price}
```

This matches the **actual runtime dict** (built in `media_buy_create.py` from ORM `PricingOption.rate` and `PricingOption.is_fixed` columns). This is correct at runtime.

However, the V3 AdCP Pydantic schema (`PricingOption` in `schemas.py`) renamed these to `fixed_price`/`floor_price` and removed `is_fixed`. A developer working on adapter code who looks at the V3 schema and then reads this docstring will be confused about whether the dict contains `rate` or `fixed_price`.

The V3 AdCP schema change did NOT change the internal adapter dict contract. This is by design -- the ORM model retains `rate` and `is_fixed` as DB columns -- but the docstrings need to clarify that this is an internal dict, distinct from the external V3 API schema.

**Fix:** Update all four docstrings to note the internal vs external distinction:

```python
# package_pricing_info: Optional pricing info per package (internal dict format, not V3 wire format)
#   Maps package_id -> {
#     "pricing_model": str,      # e.g. "cpm", "vcpm", "flat_rate"
#     "rate": float | None,      # Fixed rate (ORM column; V3 AdCP wire format uses "fixed_price")
#     "currency": str,           # ISO 4217
#     "is_fixed": bool,          # True=fixed-rate, False=auction (V3 AdCP infers from field presence)
#     "bid_price": float | None, # Buyer auction bid
#   }
```

## Medium Issues

### MED-1: New code in workflow managers uses deprecated `tenant_gemini_key` argument

**Confidence: 80**

**Files:**
- `src/adapters/gam/managers/workflow.py` line 172
- `src/adapters/mock_ad_server.py` line 677 (same pattern)

**Explanation:**

`build_order_name_context` in `src/core/utils/naming.py` accepts `tenant_gemini_key` as a deprecated kwarg (kept for backward compat). The GAM workflow manager fetches the Gemini key from the `Tenant` model and passes it via the deprecated path:

```python
# gam/managers/workflow.py:169-172
if tenant:
    tenant_gemini_key = tenant.gemini_api_key

naming_context = build_order_name_context(
    request, packages, start_time, end_time, tenant_gemini_key=tenant_gemini_key
)
```

This was added during this migration (the V3 naming refactor). Since it is new code, it should use the `tenant_ai_config` path instead of perpetuating the deprecated pattern.

**Fix:** Fetch the full tenant AI config and pass it as `tenant_ai_config`:

```python
tenant_ai_config = None
if tenant and tenant.gemini_api_key:
    tenant_ai_config = {"provider": "gemini", "api_key": tenant.gemini_api_key}

naming_context = build_order_name_context(
    request, packages, start_time, end_time, tenant_ai_config=tenant_ai_config
)
```

### MED-2: `base_workflow.py:build_packages_summary` labels all rates as `cpm` regardless of pricing model

**Confidence: 80**

**File:** `src/adapters/base_workflow.py` lines 287-293

**Explanation:**

```python
return [
    {
        "name": pkg.name,
        "impressions": pkg.impressions,
        "cpm": pkg.cpm,  # Not CPM for CPC, FLAT_RATE, VCPM, etc.
    }
    for pkg in packages
]
```

This summary is displayed in Slack notifications and the admin workflow dashboard. For a CPC media buy, `pkg.cpm` contains the click rate -- labeling it `cpm` misleads human approvers. For FLAT_RATE, `pkg.cpm` is the total campaign cost used as input to CPD conversion. Same issue in `gam/managers/workflow.py` lines 69, 201, 374 and `broadstreet/managers/workflow.py` lines 119-120.

`MediaPackage.cpm` exists and is valid -- the field name is a legacy artifact. The display label should reflect V3 terminology.

**Fix:** Rename the key in workflow action details summaries to `rate` (generic), or read the pricing model from the package and label accordingly. The minimal fix:

```python
{"name": pkg.name, "impressions": pkg.impressions, "rate": pkg.cpm}
```

## Low Issues

### LOW-1: `xandr.py` `floor_price` comments reference "V3" but adapter has no V3 pricing support

**Confidence: 80**

**File:** `src/adapters/xandr.py` lines 362, 391, 420, 450

**Explanation:**

```python
floor_price=0.50,  # V3: floor moved to top-level
```

These comments appear in stub/test scenario builder methods inside the Xandr adapter and reference "V3" in a way that implies they were updated during this migration. However, the Xandr adapter is incomplete (has a large `NOTE: Xandr adapter needs full refactor` comment at line 29) and still uses `pricing_info["is_fixed"]` and `pricing_info["rate"]` from the internal dict (line 575), which is correct.

The `floor_price` comments are potentially confusing because they refer to the V3 AdCP schema field name, but `floor_price` here is just a hardcoded value in a test scenario builder. No functional issue.

## Pricing Model Compatibility

### V3 Field Transition Map

| Concept | V3 AdCP Wire Format | Internal `pricing_info` Dict | ORM `PricingOption` Column |
|---|---|---|---|
| Fixed rate | `fixed_price: float` | `"rate": float` | `rate: Decimal` |
| Auction floor | `floor_price: float` | not present (stored in `price_guidance`) | `price_guidance["floor"]` |
| Fixed vs auction flag | inferred from `fixed_price` vs `floor_price` presence | `"is_fixed": bool` | `is_fixed: bool` |
| Buyer bid | `bid_price` on `PackageRequest` | `"bid_price": float` | `bid_price: Decimal` on `MediaPackage` |

The migration is internally consistent: V3 AdCP schema enforces the new field names at the API boundary, and the ORM + internal dict layer retains the V2 column names. No data is lost.

### GAM Adapter Pricing Correctness

- `flat_rate` to `CPD`: correctly divides total cost by flight days (`orders.py:804-811`). Minimum 1 day guard at line 806 prevents division by zero for same-day campaigns.
- `vcpm` to `STANDARD` only: enforced by `PricingCompatibility.select_line_item_type` (`pricing_compatibility.py:122-123`).
- `cpc` to goal type `CLICKS`: correctly set at `orders.py:852-855`.
- Auction pricing without `bid_price`: correctly raises `ValueError` with clear message at `orders.py:784-790`. No silent fallback.

### Mock Adapter

Correctly supports all seven pricing models (`cpm`, `vcpm`, `cpcv`, `cpp`, `cpc`, `cpv`, `flat_rate`). The `pricing_info["rate"]` / `pricing_info["is_fixed"]` reads are correct. The `pkg.cpm` fallback at line 753 is safe.

### Broadstreet Adapter

Only uses pricing for display in workflow steps (no actual pricing translation). The `.cpm` field access is the MED-2 issue above.

### Xandr Adapter

Correctly reads `pricing_info["rate"]` and `pricing_info["is_fixed"]` at line 575. The remainder of the adapter is stubbed with an explicit TODO for full refactor.

## Adapter Contract Analysis

All four production adapters (GAM, Mock, Kevel, Triton) read from the `package_pricing_info` dict using the same `rate`/`is_fixed`/`bid_price` keys. The dict is constructed in `media_buy_create.py` from the ORM `PricingOption` model. This contract is internally consistent and was not broken by the V3 migration.

The V3 migration correctly enforces the new schema at the AdCP boundary (Pydantic models in `schemas.py`) while keeping the internal adapter interface stable. The two-layer approach (V3 wire format at the API boundary, V2-style internal dict for adapter consumption) is a deliberate design choice that avoids a large-scale adapter rewrite while maintaining protocol compliance.

Key contract points verified:
- `media_buy_create.py` builds the `pricing_info` dict from ORM columns (`rate`, `is_fixed`, `bid_price`).
- All adapters consume the dict using `pricing_info["rate"]`, `pricing_info["is_fixed"]`, `pricing_info.get("bid_price")`.
- No adapter reads `fixed_price` or `floor_price` from the internal dict (these are V3 AdCP schema fields, not internal dict keys).
- The V3 `PricingOption` Pydantic schema in `schemas.py` correctly auto-computes `is_fixed` from `fixed_price` presence via `validate_pricing_option` model validator.
- The `model_dump()` override on `PricingOption` excludes internal fields (`is_fixed`, `supported`, `unsupported_reason`) from external responses.

## Recommendations

In priority order:

1. **Fix CRIT-1** in `mock_ad_server.py:466` and `broadstreet/managers/workflow.py:86` -- the `.domain` attribute access on a dict will crash in all real-world `create_media_buy` calls that pass a brand field. Use `isinstance(brand, dict)` guard or change `CreateMediaBuyRequest.brand` to the proper `BrandReference` type.

2. **Remove `build_order_name_context` from `gam/utils/naming.py`** -- prevents future divergence from the canonical version in `core/utils/naming.py`. The function is not imported by any active code path from this module.

3. **Update `package_pricing_info` docstrings** in `base.py`, `mock_ad_server.py`, `google_ad_manager.py`, `gam/managers/orders.py` to clarify the `rate` key is the ORM column name, distinct from V3's `fixed_price`. This reduces confusion for future contributors working across the AdCP schema boundary.

4. **Replace `tenant_gemini_key=` with `tenant_ai_config=`** in workflow manager call sites -- new code should not perpetuate deprecated patterns.

5. **Rename `cpm` key to `rate` in workflow action details** across `base_workflow.py`, `gam/managers/workflow.py`, and `broadstreet/managers/workflow.py` to avoid misleading human approvers when pricing model is not CPM.
