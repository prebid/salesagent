# Schema Migration Impact Analysis

## Executive Summary

**Question**: What breaks if we switch from manual schemas to auto-generated schemas?

**Answer**: **13 models with custom validators, 4 internal fields, and all import paths change.**

The good news: Most schemas can be safely migrated. The challenge: A handful of models have custom business logic.

---

## Key Findings

### ✅ Can Migrate Safely (~90%)
Most schemas have NO custom logic and can switch to generated schemas immediately:
- `Budget`, `Targeting`, `Format`, `Frequency`, `Measurement`
- Most Request/Response models
- Enum types

### ⚠️ Need Wrapper Classes (~10%)
13 models have custom validators that must be preserved:
- `CreateMediaBuyRequest` - backward compatibility validator
- `Product` - pricing validation
- `BrandManifest` - required fields validator
- `PricingOption` - oneOf validation
- 9 others (see full list below)

### 🔴 Internal Fields (Minor)
4 fields in `CreateMediaBuyRequest` not in AdCP spec:
- `webhook_url` - used internally for notifications
- `webhook_auth_token` - notification auth
- `campaign_name` - display name (optional)
- `currency` - derived from pricing_options

**Impact**: These fields won't be in generated schemas. Need to handle separately.

---

## Detailed Breakdown

### 1. Models with Custom Validators (MUST preserve)

```python
# ❌ WILL BREAK if we just switch to generated

AdCPPackageUpdate
  - validate_oneOf_constraint() - ensures at least one update field

AssignCreativeRequest
  - Validator logic

BrandManifest
  - validate_required_fields() - ensures url OR name present

CreateMediaBuyRequest
  - handle_legacy_format() - backward compat for promoted_offering
  - validate_timezone_aware() - datetime validation

Creative
  - Validator logic

CreativeAssignment
  - Validator logic

ListAuthorizedPropertiesRequest
  - Validator logic

ListCreativesRequest
  - Validator logic

PricingOption
  - oneOf validation for different pricing models

Product
  - validate_pricing_fields() - pricing options validation

UpdateMediaBuyRequest
  - Validator logic

UpdateMediaBuyResponse
  - Validator logic
```

### 2. Custom Methods (Mostly Pydantic Defaults)

**Finding**: The "custom methods" are mostly Pydantic v1 methods that are now deprecated in v2:
- `parse_obj()`, `parse_raw()`, `parse_file()` - Old v1 methods
- `dict()`, `json()` - Now `model_dump()`, `model_dump_json()`
- `schema()`, `schema_json()` - Now `model_json_schema()`

**Real Custom Methods** (actually matter):
- `model_dump_internal()` - Used in ~10 models for database serialization
- `model_dump_adcp_compliant()` - Product model only
- `get_product_ids()`, `get_total_budget()` - CreateMediaBuyRequest helpers

**Impact**: MEDIUM - These 3 methods need to be preserved in wrapper classes.

### 3. Internal Fields

```python
# CreateMediaBuyRequest internal fields NOT in AdCP spec
class CreateMediaBuyRequest(BaseModel):
    # AdCP spec fields
    buyer_ref: str
    brand_manifest: BrandManifest | str
    packages: list[Package]
    # ...

    # ❌ Internal fields - NOT in AdCP spec
    webhook_url: str | None = None  # Used for push notifications
    webhook_auth_token: str | None = None  # Webhook auth
    campaign_name: str | None = None  # Display name
    currency: str | None = None  # Derived from pricing_options
```

**Impact**: HIGH for internal code that uses these fields.

**Solution**: Create internal extension:
```python
from src.core.schemas_generated._schemas_v1_media_buy_create_media_buy_request_json import CreateMediaBuyRequest as CreateMediaBuyRequestBase

class CreateMediaBuyRequest(CreateMediaBuyRequestBase):
    # Add internal fields
    webhook_url: str | None = None
    webhook_auth_token: str | None = None
    campaign_name: str | None = None
    currency: str | None = None

    # Preserve custom validators
    @model_validator(mode="before")
    @classmethod
    def handle_legacy_format(cls, values):
        # ... backward compat logic
        return values
```

---

## Migration Strategy

### Phase 1: Use Generated as Validation Reference (NOW)
- Keep manual schemas
- Run both through AdCP contract tests
- Use generated schemas to catch drift

### Phase 2: Migrate Simple Models (NEXT)
Migrate these models first (no custom logic):

**Core Data Models**:
- `Budget`
- `Targeting`
- `Format`
- `Measurement`
- `Frequency`

**Enum Types**:
- All enums (Pacing, Status types, etc.)

**Simple Request/Response**:
- `GetProductsRequest`
- `GetProductsResponse`
- `ListCreativeFormatsRequest`
- `ListCreativeFormatsResponse`

### Phase 3: Wrap Complex Models (LATER)
For models with custom logic:

```python
# Example: CreateMediaBuyRequest

from src.core.schemas_generated._schemas_v1_media_buy_create_media_buy_request_json import (
    CreateMediaBuyRequest as GeneratedRequest
)

class CreateMediaBuyRequest(GeneratedRequest):
    """
    Extends generated schema with custom validators and internal fields.
    """
    # Add internal fields
    webhook_url: str | None = None
    webhook_auth_token: str | None = None
    campaign_name: str | None = None
    currency: str | None = None

    # Preserve custom validators
    @model_validator(mode="before")
    @classmethod
    def handle_legacy_format(cls, values):
        if "promoted_offering" in values and not values.get("brand_manifest"):
            values["brand_manifest"] = {"name": values["promoted_offering"]}
        return values

    @model_validator(mode="after")
    def validate_timezone_aware(self):
        # ... validation logic
        return self

    # Custom helper methods
    def get_product_ids(self) -> list[str]:
        # ... helper logic
        pass

    def get_total_budget(self) -> float:
        # ... helper logic
        pass
```

---

## What Actually Breaks

### Code That Will Break

1. **Import Statements** (EVERYWHERE)
```python
# ❌ OLD
from src.core.schemas import CreateMediaBuyRequest

# ✅ NEW
from src.core.schemas_generated._schemas_v1_media_buy_create_media_buy_request_json import CreateMediaBuyRequest
```

2. **Internal Field Access** (15-20 places)
```python
# ❌ BREAKS - field doesn't exist
req.webhook_url

# ✅ FIX - Use internal extension or separate model
```

3. **Custom Validator Logic** (13 models)
```python
# ❌ BREAKS - validator not in generated schema
req.handle_legacy_format()  # Method doesn't exist

# ✅ FIX - Use wrapper class with custom validators
```

4. **Custom Helper Methods** (3 methods)
```python
# ❌ BREAKS - method not in generated schema
req.get_product_ids()

# ✅ FIX - Re-implement in wrapper class
```

### Code That Works Fine

1. **Basic Field Access** (MOST code)
```python
# ✅ WORKS - same fields, same types
req.buyer_ref
req.brand_manifest
req.packages
```

2. **Validation** (WORKS BETTER)
```python
# ✅ WORKS - stricter validation
CreateMediaBuyRequest(buyer_ref="test", brand_manifest={"name": "Test"}, ...)
```

3. **Serialization** (WORKS)
```python
# ✅ WORKS - model_dump() / model_dump_json()
req.model_dump()
req.model_dump_json()
```

---

## Effort Estimate

### Phase 1: Validation Reference (DONE ✅)
- **Effort**: Complete
- **Risk**: None - no code changes

### Phase 2: Migrate Simple Models
- **Effort**: 1-2 days
- **Risk**: Low
- **Changes**: ~50 import statements, no logic changes
- **Models**: ~80 simple models

### Phase 3: Wrap Complex Models
- **Effort**: 2-3 days
- **Risk**: Medium
- **Changes**: 13 wrapper classes, ~30 import statements
- **Models**: 13 models with custom logic

### Total Migration
- **Effort**: 3-5 days
- **Risk**: Medium
- **Benefit**: Always in sync with AdCP spec, better type safety

---

## Recommendation

**START**: Phase 2 - Migrate simple models
**PRIORITIZE**: Most commonly used models first:
1. `Budget` (used everywhere)
2. `Package` (media buy core)
3. `Targeting` (frequently accessed)
4. Enums (low risk)

**DEFER**: Complex models with custom logic until Phase 3

**TEST**: Run full AdCP contract test suite after each migration

---

## Testing Strategy

### Before Migration
```bash
# Baseline - all tests should pass
pytest tests/unit/test_adcp_contract.py -v
```

### During Migration (per model)
```bash
# 1. Update imports
# 2. Run unit tests
pytest tests/unit/test_adcp_contract.py::test_<model_name> -v

# 3. Run integration tests
pytest tests/integration/ -k <model_name> -v

# 4. Verify no breakage
pytest tests/unit/ tests/integration/ -v
```

### After Complete Migration
```bash
# Full validation
./run_all_tests.sh ci
```

---

## Rollback Plan

If migration causes issues:

1. **Immediate Rollback**
```bash
git revert <migration-commit>
```

2. **Gradual Rollback**
```python
# Revert specific model
from src.core.schemas import CreateMediaBuyRequest  # Use manual version
```

3. **Hybrid Approach**
```python
# Keep both
from src.core.schemas import CreateMediaBuyRequest as ManualRequest
from src.core.schemas_generated._schemas_v1_media_buy_create_media_buy_request_json import CreateMediaBuyRequest as GeneratedRequest

# Use manual for now
CreateMediaBuyRequest = ManualRequest
```

---

## Success Criteria

✅ All AdCP contract tests pass
✅ All integration tests pass
✅ No runtime errors in production
✅ Generated schemas match AdCP spec exactly
✅ Custom validators preserved in wrapper classes
✅ Internal fields handled separately
✅ Type checking passes (mypy)

---

## Open Questions

1. **Q**: Should we deprecate manual schemas entirely?
   **A**: No - keep wrapper classes for custom logic.

2. **Q**: What about TypeScript generation?
   **A**: Same JSON schemas work for both Python and TypeScript generation!

3. **Q**: How often to regenerate?
   **A**: When AdCP spec updates (check monthly or watch GitHub releases).

4. **Q**: What about breaking changes in AdCP spec?
   **A**: Generated schemas will catch them immediately in tests.
