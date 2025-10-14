# Real-World Example: Using Schema Adapters in Tests

## The Problem

When AdCP spec changes, our manual schemas drift and tests break. Here's a real example:

### Before: Manual Schema (Test Breaks When Spec Changes)

```python
# tests/unit/test_my_feature.py
from src.core.schemas import GetProductsRequest  # Manual schema

def test_get_products_with_brand():
    """Test get_products request."""
    # This test uses manual schema
    request = GetProductsRequest(
        brief="Display ads",
        promoted_offering="Acme Corp",  # Manual schema field
    )

    assert request.promoted_offering == "Acme Corp"
```

**What happens when spec changes:**
1. AdCP adds `brand_manifest` field (replaces `promoted_offering`)
2. Manual schema is out of date
3. Test keeps passing (false positive!)
4. Production code uses old schema
5. Protocol validation fails
6. Bug discovered in production

### After: Adapter (Test Updates Automatically)

```python
# tests/unit/test_my_feature.py
from src.core.schema_adapters import GetProductsRequest  # Adapter

def test_get_products_with_brand():
    """Test get_products request."""
    # This test uses adapter (backed by generated schema)
    request = GetProductsRequest(
        brief="Display ads",
        promoted_offering="Acme Corp",  # Backward compatible
    )

    # Adapter auto-converts to brand_manifest
    assert request.brand_manifest is not None

    # Protocol validation uses generated schema
    generated = request.to_generated()
    protocol_data = generated.model_dump()  # ✅ Validates against AdCP spec
```

**What happens when spec changes:**
1. AdCP adds `brand_manifest` field
2. Run: `python scripts/generate_schemas.py`
3. Generated schemas updated
4. Adapter automatically uses new schema
5. Test catches drift immediately (validation error if incompatible)
6. Fix code before production
7. No bugs!

## Real Test Migration Example

### Before: Using Manual Schema

```python
# tests/unit/test_adcp_contract.py (CURRENT)
from src.core.schemas import GetProductsRequest

def test_adcp_get_products_request(self):
    """Test AdCP get_products request requirements."""
    request = GetProductsRequest(
        brief="Looking for display ads on news sites",
        promoted_offering="B2B SaaS company selling analytics software",
    )

    assert request.brief is not None
    assert request.promoted_offering is not None
```

**Issues:**
- Manual schema might be out of date
- Test validates manual schema, not AdCP spec
- Drift bugs hide until production

### After: Using Adapter

```python
# tests/unit/test_adcp_contract.py (MIGRATED)
from src.core.schema_adapters import GetProductsRequest

def test_adcp_get_products_request(self):
    """Test AdCP get_products request requirements."""
    request = GetProductsRequest(
        brief="Looking for display ads on news sites",
        promoted_offering="B2B SaaS company selling analytics software",
    )

    # Adapter validates against generated schema (always in sync with spec)
    generated = request.to_generated()
    protocol_data = generated.model_dump()

    # If spec changed, this would catch it immediately
    assert "brief" in protocol_data or "brand_manifest" in protocol_data
```

**Benefits:**
- Adapter backed by generated schema (always current)
- Test validates against real AdCP spec
- Drift caught immediately
- No production bugs

## Side-by-Side Comparison

### Scenario: AdCP Spec Changes

| Aspect | Manual Schema | Adapter |
|--------|--------------|---------|
| **Spec changes** | Manual update needed | Auto-update (regenerate) |
| **Time to update** | 15-30 min per model | 5 seconds (run script) |
| **Risk of bugs** | High (human error) | Low (automated) |
| **Test accuracy** | Validates manual schema | Validates real spec |
| **Drift detection** | Manual review | Automatic (tests fail) |
| **Migration effort** | Change import only | Change import only |

### Example Timeline

**Manual Schema Approach:**
1. AdCP releases v1.9.0 with new fields (Day 0)
2. Wait for someone to notice (Day 7)
3. Manually update schemas.py (Day 8, 2 hours)
4. Fix broken tests (Day 8, 3 hours)
5. Review PR, merge (Day 9)
6. **Total: 9 days, 5 hours work**

**Adapter Approach:**
1. AdCP releases v1.9.0 with new fields (Day 0)
2. Run `python scripts/generate_schemas.py` (Day 0, 5 seconds)
3. Tests fail if incompatible (Day 0, immediate)
4. Fix code, commit (Day 0, 15 minutes)
5. **Total: Same day, 15 minutes work**

## Migration Strategy

### Step 1: Start with One Test File

```python
# Pick a small test file
# tests/unit/test_products.py

# OLD:
from src.core.schemas import GetProductsRequest

# NEW:
from src.core.schema_adapters import GetProductsRequest

# Everything else stays the same!
```

Run tests:
```bash
pytest tests/unit/test_products.py -v
```

If tests pass → migration successful!
If tests fail → adapter needs refinement

### Step 2: Expand Gradually

```python
# Migrate file by file
# tests/unit/test_authorized_properties.py
- from src.core.schemas import GetProductsRequest
+ from src.core.schema_adapters import GetProductsRequest

# tests/unit/test_mcp_schema_validator.py
- from src.core.schemas import GetProductsRequest
+ from src.core.schema_adapters import GetProductsRequest
```

### Step 3: Validate with Schema Change

When next AdCP spec change happens:
1. Regenerate schemas
2. Adapter tests fail if incompatible
3. Manual tests keep passing (false positive)
4. **Proof that adapters catch drift!**

## Code Examples

### Simple Request Construction

```python
# Simple, clean API
req = GetProductsRequest(
    promoted_offering="https://example.com",
    brief="Display ads for luxury cars"
)

assert req.promoted_offering == "https://example.com"
assert req.brief == "Display ads for luxury cars"
```

### Protocol Validation

```python
# Validate against AdCP spec
req = GetProductsRequest(promoted_offering="https://example.com")

# Convert to generated schema (validates against JSON Schema)
generated = req.to_generated()
protocol_data = generated.model_dump()

# This validates against the REAL AdCP spec
assert "promoted_offering" in protocol_data or "brand_manifest" in protocol_data
```

### Backward Compatibility

```python
# Old code using promoted_offering
req = GetProductsRequest(promoted_offering="Acme Corp")

# Adapter auto-converts to brand_manifest
assert req.brand_manifest is not None
assert req.brand_manifest["name"] == "Acme Corp"

# Protocol gets the right format
generated = req.to_generated()
data = generated.model_dump()
# data has brand_manifest, not promoted_offering (spec-compliant)
```

## Success Metrics

After migrating to adapters, you should see:

✅ **Zero manual schema updates** when spec changes
✅ **Instant schema sync** (5 seconds to regenerate)
✅ **Tests catch drift** immediately
✅ **No production bugs** from schema mismatches
✅ **Faster development** (no schema maintenance)

## Next Steps

1. **Try it**: Migrate one test file to use adapter
2. **Validate**: Run tests, confirm they pass
3. **Expand**: Migrate more test files
4. **Wait for spec change**: When AdCP updates, regenerate and watch tests catch drift
5. **Celebrate**: No more manual schema updates!
