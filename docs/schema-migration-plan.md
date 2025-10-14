# Schema Migration Plan: Manual → Auto-Generated

## Current Situation (The Problem)

We have **TWO complete schema systems**:

1. **Manual** (`src/core/schemas.py`) - 3,528 lines, 126 classes
   - Used everywhere in the codebase
   - Frequently out of sync with AdCP spec
   - Causes test failures when spec changes
   - Duplicates schema logic with Python validators

2. **Auto-generated** (`src/core/schemas_generated/`) - 8,114 lines, 78 files
   - **NOT USED ANYWHERE**
   - Always in sync with AdCP spec
   - Auto-regenerated from JSON schemas
   - Single source of truth

**The core issue**: Every time the AdCP spec changes, we have to manually update `schemas.py`, which leads to:
- Schema drift and bugs
- Test failures
- Wasted time on manual updates
- Inconsistencies between our schemas and the spec

## Goal

**Use auto-generated schemas for all AdCP protocol models**, keeping only custom/internal models in `schemas.py`.

## Strategy

### Phase 1: Identify What Stays Manual

**Models that MUST stay in `schemas.py`** (have custom logic we can't auto-generate):

#### A. Models with Custom Validators (that can't be in JSON Schema)
- `CreateMediaBuyRequest` - `validate_timezone_aware` (runtime check)
- `Product` - `validate_pricing_fields` (business logic)
- `Creative` - `validate_creative_fields` (complex logic)
- `PricingOption` - `validate_pricing_option` (conditional logic - could move to schema)
- ~9 other models with timezone validators

#### B. Models with Custom Methods
- `CreateMediaBuyResponse` - `model_dump_internal()` (database vs protocol)
- `GetProductsResponse` - `model_dump_internal()` (database vs protocol)
- `Product` - `model_dump_adcp_compliant()` (filters internal fields)
- `Package` - `model_dump_internal()` (database vs protocol)
- ~10 other models with dump methods

#### C. Internal-Only Models (not in AdCP spec)
- `Principal` - Our authentication/tenant model
- `MediaBuyInternal` - Database model
- `ProductInternal` - Database model
- `AdapterPackageDelivery` - Adapter-specific
- All `*Internal` and `Adapter*` models

### Phase 2: Migration Approach

**Option A: Big Bang (Risky)**
- Replace all imports at once
- High risk of breaking everything
- ❌ Not recommended

**Option B: Gradual Migration (Recommended)**
1. Create adapter layer in `schemas.py` that re-exports generated schemas
2. Gradually migrate imports file by file
3. Add custom methods/validators as mixins on top of generated schemas
4. Remove adapters when all imports migrated

**Option C: Hybrid (Best)**
1. Keep `schemas.py` but make it THIN - just imports + custom logic
2. For AdCP protocol models: Import from generated, add custom logic
3. For internal models: Keep in `schemas.py`
4. Result: Single import point, but schemas are actually generated

## Implementation Plan

### Step 1: Reorganize `schemas.py` (No Breaking Changes)

```python
# src/core/schemas.py

# ============================================================================
# PART 1: AdCP Protocol Schemas (Auto-Generated + Custom Logic)
# ============================================================================

# Import core AdCP models from generated schemas
from src.core.schemas_generated._schemas_v1_media_buy_create_media_buy_request_json import (
    CreateMediaBuyRequest as _GeneratedCreateMediaBuyRequest
)

# Add custom logic on top of generated schemas
class CreateMediaBuyRequest(_GeneratedCreateMediaBuyRequest):
    """AdCP CreateMediaBuyRequest with custom validation."""

    @model_validator(mode="after")
    def validate_timezone_aware(self):
        """Custom validator for timezone checking (can't be in JSON Schema)."""
        # ... existing logic

# ============================================================================
# PART 2: Internal Models (Keep Here)
# ============================================================================

class Principal(BaseModel):
    """Internal authentication model."""
    # ... stays exactly as is

class MediaBuyInternal(BaseModel):
    """Internal database model."""
    # ... stays exactly as is
```

### Step 2: Update Imports Gradually

No changes needed! All imports still work:
```python
from src.core.schemas import CreateMediaBuyRequest  # Works!
```

### Step 3: Remove Redundant Code

Once migrated, remove from `schemas.py`:
- Field definitions (use generated)
- Validators that moved to JSON Schema
- Any logic now in generated schemas

### Step 4: Add Regeneration to CI

```yaml
# .github/workflows/test.yml
- name: Regenerate schemas
  run: uv run python scripts/generate_schemas.py

- name: Check for schema drift
  run: git diff --exit-code src/core/schemas_generated/
```

## Benefits After Migration

✅ **Always in sync** - Schemas auto-regenerate from AdCP spec
✅ **Fewer bugs** - Can't drift from spec
✅ **Less maintenance** - No manual schema updates
✅ **Single source of truth** - JSON Schema is authoritative
✅ **Cross-platform** - Same schemas for TypeScript/Python/etc
✅ **Faster updates** - Run script, done
✅ **Better tests** - Tests validate against real spec

## Migration Checklist

### Phase 1: Preparation
- [x] Audit what's in schemas.py vs schemas_generated/
- [ ] Identify all models with custom validators/methods
- [ ] Document which validators can move to JSON Schema
- [ ] Create list of internal-only models

### Phase 2: Create Hybrid System
- [ ] Import generated schemas in schemas.py
- [ ] Add custom logic as class extensions
- [ ] Verify imports still work
- [ ] Run full test suite

### Phase 3: Clean Up
- [ ] Remove redundant field definitions
- [ ] Remove validators now in JSON Schema
- [ ] Document what stays manual and why
- [ ] Update docs/CLAUDE.md with new pattern

### Phase 4: Automation
- [ ] Add schema regeneration to pre-commit
- [ ] Add drift detection to CI
- [ ] Document regeneration process

## Risk Mitigation

**Risk**: Generated schemas break existing code
**Mitigation**: Use hybrid approach - generated schemas are imported INTO schemas.py, all existing imports still work

**Risk**: Custom validators lost
**Mitigation**: Add them as mixins on top of generated schemas

**Risk**: Internal fields missing
**Mitigation**: Only use generated schemas for AdCP protocol models, keep internal models manual

**Risk**: Import paths change
**Mitigation**: Re-export from schemas.py, no import path changes needed

## Timeline

- **Week 1**: Audit and planning (✅ DONE)
- **Week 2**: Create hybrid system
- **Week 3**: Migrate + test
- **Week 4**: Clean up + CI automation

## Success Metrics

- [ ] Zero manual schema updates needed when AdCP spec changes
- [ ] Schema regeneration script runs < 5 seconds
- [ ] All tests pass with generated schemas
- [ ] CI fails if schemas drift from generated
- [ ] Documentation updated

## Next Steps

1. Complete audit of custom validators/methods
2. Test hybrid approach with 1-2 models
3. Verify all tests still pass
4. Expand to all AdCP protocol models
5. Add CI automation
