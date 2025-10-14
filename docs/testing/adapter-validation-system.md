# Adapter Schema Validation System - Implementation Plan

## Problem Statement

**Issue**: When we update adapter schemas (e.g., changing `deliveries` to `media_buy_deliveries`), we don't always update all the call sites in the implementation code.

**Example Bug**:
```python
# Adapter definition (src/core/schema_adapters.py)
class GetMediaBuyDeliveryResponse(BaseModel):
    media_buy_deliveries: list[Any]  # Correct field name

# Implementation code (src/core/main.py)
return GetMediaBuyDeliveryResponse(
    deliveries=[...]  # ❌ Wrong field name!
)
```

**Current Detection**: E2E tests catch this, but only in CI (slow feedback loop)

**Goal**: Static validation at pre-commit time (fast feedback loop)

## Solution Design

### Architecture Overview

```
Pre-commit Hook
    ↓
scripts/validate_adapter_usage.py
    ↓
┌─────────────────┬──────────────────┐
│  Extract Schema │  Find Usage Sites │
│  Definitions    │  in main.py       │
│  (AST parsing)  │  (AST parsing)    │
└────────┬────────┴─────────┬─────────┘
         │                  │
         ↓                  ↓
    Field Registry    Constructor Calls
         │                  │
         └────────┬─────────┘
                  ↓
            Validate Match
                  ↓
           Report Errors
```

### Key Components

#### 1. Schema Definition Extraction

**Input**: `src/core/schema_adapters.py`
**Output**: Dictionary mapping class names to required/optional fields

**Method**: Parse Python AST to find:
- Classes inheriting from `BaseModel`
- Field definitions with type annotations
- Required vs optional fields (based on `Optional[]`, `| None`, or default values)

#### 2. Usage Site Detection

**Input**: `src/core/main.py`
**Output**: List of all response constructor calls with field names

**Method**: Parse Python AST to find:
- `ClassName(field1=value1, field2=value2)` patterns
- Extract field names from keyword arguments
- Record file location (line, column) for error reporting

#### 3. Validation Logic

**Input**: Schema definitions + constructor calls
**Output**: List of validation errors

**Checks**:
- Unknown fields (likely typos or outdated field names)
- Missing required fields
- Levenshtein distance for typo suggestions

#### 4. Error Reporting

**Output**: Human-friendly error messages with actionable suggestions

**Example**:
```
❌ Adapter Schema Validation Failed (2 errors)

src/core/main.py:4707:12 - GetMediaBuyDeliveryResponse
  Unknown field: 'deliveries'
  Did you mean: 'media_buy_deliveries'?

src/core/main.py:4877:12 - GetMediaBuyDeliveryResponse
  Unknown field: 'deliveries'
  Did you mean: 'media_buy_deliveries'?
```

## Implementation Phases

### Phase 1: Core Script Implementation (Week 1)

**Goal**: Working validation script that can be run manually

**Tasks**:
1. Create `scripts/validate_adapter_usage.py`
2. Implement AST parsing for schema extraction
3. Implement AST parsing for usage detection
4. Implement validation logic with typo suggestions
5. Implement error formatting

**Deliverables**:
- ✅ Script can be run: `python scripts/validate_adapter_usage.py`
- ✅ Catches known issues (e.g., `deliveries` vs `media_buy_deliveries`)
- ✅ Clear error messages with suggestions

### Phase 2: Unit Tests (Week 1)

**Goal**: Comprehensive test coverage for validation logic

**Tests**:
```python
tests/unit/test_adapter_validation.py
├── test_extract_required_fields()
├── test_extract_optional_fields()
├── test_extract_fields_with_pydantic_field()
├── test_find_constructor_calls()
├── test_validate_unknown_field()
├── test_validate_missing_required_field()
├── test_typo_suggestion()
└── test_real_files_integration()
```

**Deliverables**:
- ✅ >90% code coverage
- ✅ Tests for edge cases (inheritance, Field() defaults, etc.)
- ✅ Integration tests with real files

### Phase 3: Fix Existing Violations (Week 1)

**Goal**: Clean codebase before enabling hook

**Process**:
1. Run script manually: `python scripts/validate_adapter_usage.py`
2. Fix all reported violations
3. Commit fixes
4. Verify: `python scripts/validate_adapter_usage.py` → no errors

**Expected violations**: ~5-10 based on recent bugs

### Phase 4: Add as Manual Pre-Commit Hook (Week 2)

**Goal**: Optional validation developers can run

**Configuration** (`.pre-commit-config.yaml`):
```yaml
- id: validate-adapter-schemas
  name: Validate Adapter Schema Usage
  entry: python scripts/validate_adapter_usage.py
  language: python
  pass_filenames: false
  files: ^(src/core/main\.py|src/core/schema_adapters\.py)$
  stages: [manual]  # Only run with --hook-stage manual
```

**Usage**:
```bash
pre-commit run validate-adapter-schemas --hook-stage manual --all-files
```

**Deliverables**:
- ✅ Hook can be run manually
- ✅ Documentation for manual usage
- ✅ Team notified to test

### Phase 5: Enable Automatic Validation (Week 3)

**Goal**: Block commits with adapter validation errors

**Change**: Remove `stages: [manual]` from hook configuration

**Monitoring**:
- Track false positives (should be zero)
- Track bypasses (`git commit --no-verify`)
- Collect developer feedback

**Rollback criteria**: >5% false positive rate OR >20% bypass rate

## Performance Targets

**Requirement**: <2 seconds for pre-commit hook

**Expected Performance**:
- Parse `schema_adapters.py` (~150 lines): <100ms
- Parse `main.py` (~8000 lines): <500ms
- Walk AST and extract data: <300ms
- Validation logic: <100ms
- Format output: <10ms
- **Total**: ~1 second ✅

**Optimization strategies** (if needed):
1. Cache parsed schemas when only main.py changed
2. Use incremental parsing (pre-commit provides changed files)
3. Parallelize if we add more files to validate

## Edge Cases to Handle

### 1. Dynamic Field Construction

```python
response = GetMediaBuyDeliveryResponse(**data_dict)
```

**Solution**: Ignore `**kwargs` patterns with comment marker:
```python
response = GetMediaBuyDeliveryResponse(**data_dict)  # noqa: adapter-validation
```

### 2. Conditional Fields

```python
if dry_run:
    response = GetMediaBuyDeliveryResponse(
        media_buy_deliveries=deliveries,
        dry_run=True  # Optional field
    )
```

**Solution**: Validation handles optional fields (no error if missing)

### 3. Inherited Fields

```python
class GetMediaBuyDeliveryResponse(BaseResponse):
    media_buy_deliveries: list[Any]
    # buyer_ref inherited from BaseResponse
```

**Solution**: Walk inheritance chain to collect all fields

### 4. Pydantic Field() Defaults

```python
class MyResponse(BaseModel):
    optional_field: str = Field(default="")
```

**Solution**: Detect `Field()` calls and treat as optional

### 5. Type Aliases

```python
from typing import Optional as Opt
field: Opt[str] = None
```

**Solution**: Track imports and resolve aliases

### 6. False Positives from Local Variables

```python
def helper():
    GetMediaBuyDeliveryResponse = some_other_class()
    return GetMediaBuyDeliveryResponse(field1=value)
```

**Solution**: Track scope and skip if name is shadowed (keep simple, accept some false negatives)

## Success Metrics

**Required for Phase 5 (automatic validation)**:
- ✅ Zero false positives in 1 week of manual usage
- ✅ >95% detection rate for field name mismatches
- ✅ <2s average execution time
- ✅ Clear, actionable error messages
- ✅ <5% bypass rate (`--no-verify`)

**Optional improvements**:
- Detect inheritance issues
- Detect Field() alias mismatches
- Validate against AdCP spec directly (double validation)

## Maintenance Plan

### When Adding New Adapters

**No action needed** - script automatically picks up new response classes

### When Changing Adapter Patterns

**Example**: Switch from `BaseModel` to custom base class

**Action**: Update `_inherits_from_basemodel()` detection logic

### When Adding New Files to Validate

**Example**: Validate `src/core/impl/*.py` in addition to `main.py`

**Action**:
1. Update `files:` regex in `.pre-commit-config.yaml`
2. Add integration test for new file
3. Fix any violations found

### When Script Needs Updates

**Process**:
1. Update `scripts/validate_adapter_usage.py`
2. Add/update tests in `tests/unit/test_adapter_validation.py`
3. Run manually to verify: `python scripts/validate_adapter_usage.py`
4. Commit changes
5. Pre-commit runs automatically on next commit

## Alternative Approaches (Rejected)

### 1. Runtime Validation

**Pros**: Can catch dynamic field names
**Cons**: Too late (in CI/production), slower feedback

### 2. mypy Plugin

**Pros**: Integrated with type checking
**Cons**: Complex to implement, requires mypy expertise, harder to customize

### 3. Pytest Test Generator

**Pros**: Leverages existing test infrastructure
**Cons**: Requires running tests (slow), doesn't block commit

### 4. Manual Code Review Checklist

**Pros**: Simple, no tooling needed
**Cons**: Manual, error-prone, not enforced

**Chosen approach**: AST-based static validation (fast, accurate, maintainable)

## Related Systems

### Schema Sync Validation

**Location**: `scripts/check_schema_sync.py`
**Purpose**: Validates cached schemas match live AdCP registry
**Overlap**: Both ensure schema correctness, but different layers:
- Schema sync: External spec → local cache
- Adapter validation: Local schema → implementation code

### AdCP Contract Tests

**Location**: `tests/unit/test_adcp_contract.py`
**Purpose**: Validates Pydantic models match AdCP spec
**Overlap**: Both validate schema compliance, but different scope:
- Contract tests: Schema definitions
- Adapter validation: Schema usage in code

### MCP/A2A Shared Implementation

**Location**: `src/core/main.py` (\_impl functions)
**Purpose**: Ensures MCP and A2A use same business logic
**Integration**: Adapter validation checks both MCP wrapper and A2A raw functions

## Documentation Updates

When implemented, update:
- ✅ `docs/testing/pre-push-workflow.md` - Add adapter validation step
- ✅ `CLAUDE.md` - Add to "Testing Guidelines" section
- ✅ `README.md` - Add to "Development Workflow" section
- ✅ `.pre-commit-config.yaml` - Hook configuration with comments

## Open Questions

1. **Should we validate A2A raw functions separately?**
   - Current: They use same `_impl` functions, so same validation
   - Future: If A2A diverges, need separate validation

2. **Should we extend to adapter implementations (GAM, Mock, etc.)?**
   - Current: Only validate main.py (core implementation)
   - Future: Could validate adapter method signatures match base class

3. **Should we validate against AdCP spec directly?**
   - Current: Validate against adapter definitions (Python)
   - Future: Could validate against JSON schemas (double validation)
   - Trade-off: More comprehensive but slower

## Next Steps

1. **Review this plan** - Team feedback on design
2. **Approve implementation** - Get go-ahead for Phase 1
3. **Create implementation issue** - Track in GitHub
4. **Assign developer** - Who will implement?
5. **Set timeline** - Target completion date

**Recommendation**: Implement Phase 1-3 in current sprint (1 week), Phase 4-5 in next sprint

---

## References

- **Python AST module**: https://docs.python.org/3/library/ast.html
- **Pydantic field validation**: https://docs.pydantic.dev/latest/concepts/fields/
- **Pre-commit hooks**: https://pre-commit.com/
- **Related bug**: GetMediaBuyDeliveryResponse field name issue (commit 3fa80f2b)
