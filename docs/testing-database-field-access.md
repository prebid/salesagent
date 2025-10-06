# Database Field Access Testing Guide

This document describes the comprehensive testing strategy implemented to prevent database field access bugs like the `'Product' object has no attribute 'pricing'` error that reached production (Issue #161).

## Problem Statement

The original bug occurred because:
1. **Over-mocking**: Tests mocked database layers extensively, hiding real field access issues
2. **Schema-Database Misalignment**: No validation that Pydantic schema fields map to actual database columns
3. **Missing Integration Tests**: No tests exercising actual database-to-schema conversion
4. **Unsafe Field Access Patterns**: Code accessed database fields without validation

## Solution: Comprehensive Test Coverage

### 1. Database Integration Tests

**File**: `tests/integration/test_get_products_database_integration.py`

**Purpose**: Test actual database-to-schema transformation with real ORM models.

**Key Features**:
- Tests real database queries without mocking
- Validates ORM object field access patterns
- Tests conversion from SQLAlchemy models to Pydantic schemas
- Handles JSON/JSONB field conversion (PostgreSQL vs SQLite)
- Tests NULL value handling and type conversion

**Example Test**:
```python
def test_database_model_to_schema_conversion_without_mocking(self, test_tenant_id, sample_product_data):
    """Test actual ORM model to Pydantic schema conversion with real database."""
    with get_db_session() as session:
        db_product = ProductModel(tenant_id=test_tenant_id, **sample_product_data)
        session.add(db_product)
        session.commit()
        session.refresh(db_product)

        # This would catch 'pricing' attribute errors
        assert hasattr(db_product, 'cpm')
        assert not hasattr(db_product, 'pricing')  # Would have caused the bug
```

### 2. Schema-Database Field Mapping Validation

**File**: `tests/integration/test_schema_database_mapping.py`

**Purpose**: Validate that all Pydantic schema fields have corresponding database fields.

**Key Features**:
- Compares schema fields against database columns
- Identifies missing database fields for schema fields
- Tests safe vs unsafe field access patterns
- Validates field type compatibility
- Tests schema validation with real database data

**Field Categories**:
- **Required Fields**: Must exist in both schema and database
- **Internal Fields**: Database-only (tenant_id, targeting_template)
- **Computed Fields**: Schema-only (brief_relevance, format_ids)
- **Forbidden Fields**: Should not exist (pricing, cost_basis)

### 3. A2A Real Data Flow Tests

**File**: `tests/integration/test_a2a_real_data_flow.py`

**Purpose**: End-to-end A2A tests with real database and minimal mocking.

**Key Features**:
- Tests complete A2A request pipeline
- Uses real database queries and schema conversion
- Validates AdCP protocol compliance
- Tests authentication and error handling
- Regression prevention for field access bugs

**Regression Prevention**:
```python
async def test_a2a_field_access_regression_prevention(self, test_tenant_setup):
    """Specific test to prevent the 'pricing' field access regression."""
    response = await handler._handle_get_products_skill(...)

    # If we get here without AttributeError, the bug is prevented
    for product in response["products"]:
        forbidden_fields = ["pricing", "cost_basis", "margin"]
        for field in forbidden_fields:
            assert field not in product
```

### 4. Pre-commit Hook Validation

**File**: `scripts/validate_schema_database_alignment.py`

**Purpose**: Automated validation of schema-database alignment before commits.

**Features**:
- Validates Product and Principal schema alignment
- Checks for unsafe field access patterns in code
- Prevents problematic field names (pricing, cost_basis, etc.)
- Runs automatically on schema/model file changes

**Usage**:
```bash
# Manual run
python scripts/validate_schema_database_alignment.py

# With code pattern checking
python scripts/validate_schema_database_alignment.py --check-code

# Pre-commit hook (automatic)
pre-commit run schema-database-alignment
```

## Testing Best Practices

### Do: Integration Testing Patterns

1. **Use Real Database Sessions**:
```python
with get_db_session() as session:
    product = ProductModel(...)
    session.add(product)
    session.commit()
    # Test with real ORM object
```

2. **Test Field Access Directly**:
```python
# Test that safe fields exist
assert hasattr(db_product, 'cpm')
assert hasattr(db_product, 'min_spend')

# Test that unsafe fields don't exist
assert not hasattr(db_product, 'pricing')
with pytest.raises(AttributeError):
    _ = db_product.pricing
```

3. **Validate Complete Conversion Pipeline**:
```python
# Test database → schema conversion
catalog = DatabaseProductCatalog()
products = await catalog.get_products(...)

for product in products:
    assert isinstance(product, ProductSchema)
    product_dict = product.model_dump()
    assert "pricing" not in product_dict
```

### Don't: Over-Mocking Anti-Patterns

1. **❌ Mock Database Sessions Unnecessarily**:
```python
# Avoid this - hides real database issues
@patch('src.core.database.database_session.get_db_session')
def test_product_conversion(mock_session):
    mock_session.return_value.query.return_value.all.return_value = [mock_product]
```

2. **❌ Mock ORM Objects**:
```python
# Avoid this - mock objects don't enforce real field constraints
mock_product = Mock()
mock_product.pricing = "fake_value"  # Real ORM would raise AttributeError
```

3. **❌ Skip Field Access Validation**:
```python
# Avoid this - assumes fields exist without testing
def convert_product(db_product):
    return {
        "pricing": db_product.pricing  # Could fail if field doesn't exist
    }
```

## Field Access Safety Patterns

### Safe Patterns

1. **Explicit Field Mapping**:
```python
product_data = {
    "product_id": product_obj.product_id,
    "name": product_obj.name,
    "cpm": product_obj.cpm,
    # Only access known, validated fields
}
```

2. **hasattr Checks**:
```python
if hasattr(product_obj, 'cpm'):
    cpm = product_obj.cpm
else:
    cpm = None
```

3. **getattr with Defaults**:
```python
cpm = getattr(product_obj, 'cpm', None)
min_spend = getattr(product_obj, 'min_spend', 0.0)
```

### Unsafe Patterns to Avoid

1. **❌ Direct Access to Unvalidated Fields**:
```python
pricing = product_obj.pricing  # May not exist
```

2. **❌ Generic ORM-to-Dict Conversion**:
```python
# This could access non-existent fields
product_dict = {col: getattr(product_obj, col) for col in schema_fields}
```

3. **❌ Assuming Schema Fields Exist in Database**:
```python
# Schema properties may not be database columns
format_ids = product_obj.format_ids  # This is a computed property
```

## Running the Tests

### Individual Test Files
```bash
# Database integration tests
uv run pytest tests/integration/test_get_products_database_integration.py -v

# Schema-database mapping validation
uv run pytest tests/integration/test_schema_database_mapping.py -v

# A2A real data flow tests
uv run pytest tests/integration/test_a2a_real_data_flow.py -v
```

### All Field Access Tests
```bash
# Run all database field access related tests
uv run pytest tests/integration/test_*database* tests/integration/test_*schema* tests/integration/test_*a2a* -v
```

### Pre-commit Validation
```bash
# Run schema-database alignment validation
pre-commit run schema-database-alignment

# Run all validation hooks
pre-commit run --all-files
```

## Success Criteria

This testing strategy successfully addresses the original issue by:

- ✅ **Database Integration Tests**: Catch field access bugs with real ORM objects
- ✅ **Schema-Database Mapping Validation**: Prevent field mismatches at development time
- ✅ **Real Data Flow Tests**: Test complete A2A pipeline without excessive mocking
- ✅ **Pre-commit Hook**: Automated validation prevents regression
- ✅ **Reduced Mocking**: Focus mocking on external dependencies, not internal data boundaries

### Metrics
- **Before**: 0 tests caught the `pricing` field access bug
- **After**: 15+ tests specifically validate field access patterns
- **Coverage**: All critical field access paths now tested with real database
- **Prevention**: Pre-commit hook blocks commits with schema-database mismatches

## Maintenance

### Adding New Models
When adding new Pydantic schemas or database models:

1. Add validation to `validate_schema_database_alignment.py`
2. Create integration tests in appropriate test files
3. Update field mapping documentation
4. Verify pre-commit hooks pass

### Debugging Field Access Issues
1. Run schema-database alignment validation: `python scripts/validate_schema_database_alignment.py --check-code`
2. Check test failures in integration tests
3. Verify field exists in database model before accessing in code
4. Use safe access patterns (hasattr, getattr) for optional fields

This comprehensive testing approach ensures that database field access bugs like the original `pricing` AttributeError cannot reach production undetected.
