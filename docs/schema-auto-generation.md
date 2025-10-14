# AdCP Schema Auto-Generation

## Overview

This project now supports **automatic generation of Pydantic models** from the official AdCP JSON schemas. This ensures our Pydantic schemas stay in perfect sync with the AdCP specification.

## How It Works

### 1. Schema Resolution

The generation script handles the AdCP schema structure:
- **Cached Schemas**: Uses locally cached schemas from `tests/e2e/schemas/v1/`
- **Flattened Naming**: Handles the flattened file naming (e.g., `_schemas_v1_core_budget_json.json`)
- **$ref Resolution**: Automatically resolves JSON Schema `$ref` references
- **Auto-Download**: Downloads missing schemas from https://adcontextprotocol.org/schemas/v1/

### 2. Generation Process

```bash
# Generate all Pydantic models
python scripts/generate_schemas.py

# Generated files will be in src/core/schemas_generated/
```

The script:
1. Loads all JSON schemas from `tests/e2e/schemas/v1/`
2. Downloads any missing referenced schemas from AdCP website
3. Resolves all `$ref` references recursively
4. Generates clean Pydantic v2 models using `datamodel-code-generator`
5. Creates a modular structure with 77+ Python files

### 3. Generated Output

**Location**: `src/core/schemas_generated/`

**Structure**:
- 77 Python modules (one per schema)
- ~8,000 lines of clean Pydantic v2 code
- Proper type hints with `Annotated` fields
- Field constraints (ge, pattern, etc.)
- Extra="forbid" for strict validation

**Example Output**:
```python
class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: Annotated[float, Field(description="Total budget amount", ge=0.0)]
    currency: Annotated[str, Field(pattern="^[A-Z]{3}$")]
    pacing: Optional[Pacing] = None
```

## Usage

### Option 1: Use Generated Schemas (Recommended for New Code)

```python
from src.core.schemas_generated._schemas_v1_media_buy_create_media_buy_request_json import CreateMediaBuyRequest

# Guaranteed to match AdCP spec exactly
request = CreateMediaBuyRequest(...)
```

### Option 2: Keep Manual Schemas (For Code with Customizations)

```python
from src.core.schemas import CreateMediaBuyRequest

# Manual schemas can have custom validators, methods, etc.
```

### Option 3: Hybrid Approach

Use generated schemas as base classes:
```python
from src.core.schemas_generated._schemas_v1_core_budget_json import Budget as BudgetBase

class Budget(BudgetBase):
    # Add custom methods or validators
    def validate_minimum(self):
        ...
```

## Maintenance

### When AdCP Spec Updates

1. Delete old generated schemas:
   ```bash
   rm -rf src/core/schemas_generated/
   ```

2. Update cached schemas (or let auto-download handle it):
   ```bash
   # Cached schemas will be auto-updated when running E2E tests
   pytest tests/e2e/test_adcp_compliance.py
   ```

3. Regenerate Pydantic models:
   ```bash
   python scripts/generate_schemas.py
   ```

4. Test compatibility:
   ```bash
   pytest tests/unit/test_adcp_contract.py
   ```

### Adding New Schemas

The script automatically downloads missing schemas. No manual intervention needed.

## Technical Details

### Dependencies

- `datamodel-code-generator>=0.26.0` - Schema to Pydantic conversion
- `jsonref>=1.1.0` - JSON reference resolution (not used directly, but available)
- `httpx` - HTTP client for downloading missing schemas

### Script: `scripts/generate_schemas.py`

**Key Functions**:
- `resolve_refs_in_schema()` - Recursively resolves all `$ref` references
- `download_missing_schema()` - Auto-downloads schemas from AdCP website
- `generate_schemas_from_json()` - Main generation orchestration

**Algorithm**:
1. Load each JSON schema file
2. For each `$ref` found:
   - Convert `/schemas/v1/enums/pacing.json` → `_schemas_v1_enums_pacing_json.json`
   - Load referenced file (download if missing)
   - Recursively resolve nested references
3. Write fully-resolved schemas to temp directory
4. Run `datamodel-codegen` on resolved schemas
5. Generate modular Pydantic code

### Why Not Use jsonref Library?

We implemented custom `$ref` resolution because:
- Need to map AdCP paths to flattened filenames
- Want auto-download of missing schemas
- Need precise control over resolution order
- Better error messages for debugging

## Benefits

### ✅ Always In Sync
Generated models exactly match the official AdCP spec. No drift possible.

### ✅ Type Safety
Full type hints with Pydantic v2 and `Annotated` fields.

### ✅ Validation
Field constraints (patterns, min/max, enums) automatically enforced.

### ✅ Documentation
Field descriptions from JSON schemas become docstrings.

### ✅ Maintainability
Regenerate anytime the spec changes - no manual updates.

## Comparison with Manual Schemas

| Aspect | Manual Schemas | Generated Schemas |
|--------|---------------|-------------------|
| Accuracy | Can drift from spec | Always matches spec |
| Customization | Easy (validators, methods) | Requires wrapper classes |
| Maintenance | Manual updates needed | Auto-regenerate |
| Type Safety | Variable | Excellent |
| Documentation | Manual | Auto from spec |

## Migration Strategy

**Phase 1** (Current):
- Keep manual schemas in `src/core/schemas.py`
- Use generated schemas as validation reference
- Run both through AdCP contract tests

**Phase 2** (Future):
- Migrate simple models to generated schemas
- Wrap complex models with custom behavior
- Update imports gradually

**Phase 3** (Long-term):
- Deprecate manual schemas for request/response models
- Keep only custom business logic in manual code
- Generated schemas become source of truth

## Troubleshooting

### Missing Schema Error
If schema download fails, check:
- Internet connection
- AdCP website is up: https://adcontextprotocol.org/schemas/v1/
- Schema actually exists in spec

### Generation Fails
Common issues:
- Circular references (script handles this)
- Invalid JSON in cached files
- datamodel-codegen version incompatibility

### Type Errors
Generated code uses strict typing:
- Use `Union[X, Y]` or `X | Y` for alternatives
- `Optional[X]` for nullable fields
- `Annotated` for constrained types

## See Also

- `tests/unit/test_adcp_contract.py` - AdCP compliance tests
- `tests/e2e/adcp_schema_validator.py` - Schema validation logic
- `docs/testing/adcp-compliance.md` - Testing patterns
- AdCP Spec: https://adcontextprotocol.org/docs/
