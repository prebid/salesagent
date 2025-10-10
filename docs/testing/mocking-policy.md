# Testing & Mocking Policy

## Philosophy: Mock External I/O, Not Internal Logic

**Golden Rule**: Mock at the boundaries (external APIs, database connections), test everything else with real implementations.

## What to Mock ✅

### 1. External HTTP APIs
```python
@patch("requests.get")
@patch("requests.post")
def test_external_api_call(mock_post, mock_get):
    # Good: Mocking external API we don't control
    mock_get.return_value.json.return_value = {"data": "test"}
```

### 2. Database Connection Context
```python
@patch("src.core.database.database_session.get_db_session")
def test_database_error_handling(mock_session):
    # Good: Testing error handling when database is down
    mock_session.side_effect = DatabaseError("Connection failed")
```

### 3. Ad Server Adapters (External Systems)
```python
@patch("src.adapters.get_adapter")
def test_adapter_failure(mock_adapter):
    # Good: Mocking GAM/Kevel/external ad servers
    mock_adapter.return_value.create_line_item.side_effect = AdapterError()
```

### 4. Authentication/Authorization Lookups
```python
@patch("src.core.auth_utils.get_principal_from_token")
def test_unauthorized_access(mock_auth):
    # Good: Mocking auth lookup (external concern)
    mock_auth.return_value = None
```

### 5. Cloud Services (AWS, GCP, etc.)
```python
@patch("boto3.client")
def test_s3_upload(mock_boto):
    # Good: Mocking cloud provider SDKs
    pass
```

## What NOT to Mock ❌

### 1. Internal `_impl` Functions
```python
# ❌ BAD - Mocking our own business logic
@patch("src.core.main._create_media_buy_impl")
def test_create_media_buy(mock_impl):
    mock_impl.return_value = MediaBuyResponse(...)
    # This tests NOTHING - just that we can call a mock!
```

### 2. Database Queries (Use Real Database Instead)
```python
# ❌ BAD - Mocking database queries
@patch("session.execute")
def test_get_tenant(mock_execute):
    mock_execute.return_value = [Mock(tenant_id="test")]
    # Doesn't test SQL correctness, schema alignment, etc.

# ✅ GOOD - Use real database in integration test
def test_get_tenant(integration_db):
    tenant = session.scalars(select(Tenant).filter_by(tenant_id="test")).first()
    assert tenant.tenant_id == "test"
```

### 3. Pydantic Schema Validation
```python
# ❌ BAD - Mocking schema validation
mock_response = Mock(spec=CreateMediaBuyResponse)
mock_response.media_buy_id = "test"

# ✅ GOOD - Use real Pydantic object
response = CreateMediaBuyResponse(
    media_buy_id="test",
    status="active",
    # ... all required fields
)
```

### 4. Internal Helper Functions
```python
# ❌ BAD
@patch("src.core.utils.calculate_total_budget")
def test_budget_calculation(mock_calc):
    pass

# ✅ GOOD - Just call it!
from src.core.utils import calculate_total_budget
result = calculate_total_budget(packages)
```

## Test Organization

### Unit Tests (`tests/unit/`)
**Purpose**: Test pure logic, algorithms, formatting, validation

**Characteristics**:
- Fast (<1ms per test)
- No external dependencies
- No database
- No network calls
- Mock only external I/O boundaries

**Good candidates**:
- Pydantic schema validation
- Business logic calculations
- String formatting
- Error message generation
- Enum/constant definitions
- Type validation
- Edge case handling

**Examples**:
- `test_adcp_contract.py` - Schema validation (NO mocking)
- `test_datetime_string_parsing.py` - Date parsing logic (NO mocking)
- `test_database_health.py` - Health check formatting (mocks DB connection)
- `test_webhook_delivery_service.py` - Circuit breaker logic (mocks HTTP)

### Integration Tests (`tests/integration/`)
**Purpose**: Test components working together with real infrastructure

**Characteristics**:
- Slower (100ms-1s per test)
- Uses real PostgreSQL database
- Mock only external APIs (GAM, Kevel, etc.)
- Tests SQL queries, database schema, transactions

**Good candidates**:
- MCP tool roundtrips
- Database queries and schema
- Multi-component workflows
- Admin UI endpoints
- A2A skill handlers

**Examples**:
- `test_creative_lifecycle_mcp.py` - Full creative workflow with real DB
- `test_mcp_tool_roundtrip_minimal.py` - MCP protocol with real DB
- `test_virtual_host_integration.py` - Tenant routing with real DB

### E2E Tests (`tests/e2e/`)
**Purpose**: Test complete user journeys

**Characteristics**:
- Slowest (1s-10s per test)
- Real database + real services
- Mock only external ad servers
- Full protocol compliance testing

## Current State Assessment

### By the Numbers
- **Unit tests**: 56 files
  - Pure logic (no mocking): 27 files (48%) ✅
  - Schema/validation: 4 files (7%) ✅
  - External I/O mocked: 1 file (2%) ✅
  - Database connection mocked: 7 files (13%) ⚠️
  - Other: 17 files (30%)

### The 7 "Questionable" Files

#### Keep as Unit Tests (Testing Pure Logic)
1. **test_dashboard_service.py** - Service caching logic, error handling
2. **test_database_health.py** - Health report formatting, error messages
3. **test_webhook_delivery_service.py** - Circuit breaker, retry logic

#### Should Be Integration Tests (Need Real Database)
4. **test_format_resolver.py** - Queries custom_formats table ⚠️
5. **test_property_verification_service.py** - Queries properties table ⚠️
6. **test_signals_discovery_provider.py** - Queries signals data ⚠️
7. **test_virtual_host_edge_cases.py** - Tests SQL queries ⚠️

**Note**: Files 6 and 7 have integration equivalents that test happy paths. The unit tests test edge cases (malformed input, SQL injection attempts, unicode handling). Both are valuable! The unit tests should be renamed to reflect they're testing **input validation and error handling**, not database functionality.

## The Bug That Proved the Point

### What Happened
The `list_creatives` tool had a schema mismatch:
- Implementation returned: `{creatives, total_count, page, limit, has_more}`
- Schema required: `{creatives, query_summary, pagination}`

### Why Tests Didn't Catch It

1. **Unit test passed** ✅ (but only tested schema construction, not actual tool)
2. **Integration tests skipped** ❌ (PostgreSQL not running in `quick` mode)
3. **Integration tests had bugs** ❌ (checked `response.total_count` instead of `response.query_summary.total_matching`)

### The Fix

1. ✅ Fixed implementation to return correct schema
2. ✅ Fixed integration test assertions
3. 🔄 Need to enforce CI mode before push

## Enforcement

### Pre-Commit Hook (`check_test_mocking.py`)
Automatically prevents:
- Mocking `_impl` functions
- Mocking internal implementation details
- Over-mocking (>10 mocks per file)

### Pre-Push Hook
**TODO**: Should run CI mode tests (with PostgreSQL) not just quick mode

## Recommendations

### Immediate Actions
1. ✅ Fix integration test assertions (DONE)
2. 🔄 Rename 4 "questionable" unit tests to clarify they test input validation
3. 🔄 Update pre-push hook to run CI mode tests
4. 🔄 Document when service layer mocking is appropriate

### Long-Term
1. Make PostgreSQL CI mode easier to run locally
2. Consider providing lightweight test database fixtures
3. Review and consolidate overlapping unit/integration tests
4. Add more E2E tests for critical user journeys

## FAQ

### Q: Should I mock `get_db_session()` in unit tests?
**A**: Only if you're testing **error handling** or **service layer caching logic**. If you're testing database queries, use an integration test with real PostgreSQL.

### Q: When is it okay to mock internal functions?
**A**: Almost never. If you need to mock an internal function, it's usually a sign that:
1. The function should be broken into smaller pieces
2. You should write an integration test instead
3. The function is doing too much (violates Single Responsibility)

### Q: What about testing authentication/authorization?
**A**: Mock the auth **lookup** (getting principal from token), but use real auth **validation** (checking permissions).

### Q: How do I run integration tests locally?
```bash
./run_all_tests.sh ci    # Starts PostgreSQL container automatically
```

### Q: My integration test is slow. Should I mock the database?
**A**: No. Make the test faster by:
1. Using smaller test datasets
2. Reusing fixtures across tests
3. Running fewer assertions per test
4. Using database transactions that rollback

## Summary

**Good mocking** = Mocking things outside our control (APIs, cloud services, external systems)

**Bad mocking** = Mocking our own code to avoid writing real tests

**Best approach** = Use real PostgreSQL for integration tests, mock only external APIs
