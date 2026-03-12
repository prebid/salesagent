# AdCP Sales Agent Test Suite

## Test Organization

Our test suite is organized into four main categories:

### 1. Unit Tests (`tests/unit/`)
Fast, isolated tests that verify individual components without external dependencies.

**Run:** `pytest tests/unit/ -v`
**Purpose:** Test business logic, data transformations, and utility functions
**Mocking:** Minimal - only external services
**Runtime:** < 1 second per test

### 2. Integration Tests (`tests/integration/`)
Tests that verify component interactions with real databases and services.

**Run:** `pytest tests/integration/ -v`
**Purpose:** Test database operations, API endpoints, and service integrations
**Mocking:** External APIs only (GAM, Slack, etc.)
**Runtime:** < 5 seconds per test

### 3. End-to-End Tests (`tests/e2e/`)
Complete workflow tests that simulate real user journeys.

**Run:** `pytest tests/e2e/ -v`
**Purpose:** Test complete user workflows from start to finish
**Mocking:** None - uses real services in test mode
**Runtime:** < 30 seconds per test

### 4. Admin UI Tests (`tests/admin/`)
Tests for the Admin UI web interface.

**Run:** `pytest tests/admin/ -v`
**Purpose:** Test page rendering, forms, and user interactions
**Mocking:** Backend services when appropriate
**Runtime:** < 10 seconds per test

## Running Tests

### Quick Test Commands

```bash
# Run all tests
pytest

# Run by category
pytest tests/unit/              # Fast unit tests
pytest tests/integration/       # Integration tests
pytest tests/e2e/              # End-to-end tests

# Run with markers
pytest -m unit                  # Unit tests only
pytest -m integration          # Integration tests only
pytest -m "not slow"           # Skip slow tests
pytest -m requires_db          # Tests requiring database

# Run with coverage
pytest --cov=. --cov-report=html

# Run specific test file
pytest tests/integration/test_main.py -v

# Run specific test function
pytest tests/integration/test_main.py::test_product_catalog -v
```

### Test Markers

We use pytest markers to categorize tests:

- `@pytest.mark.unit` - Fast, isolated unit tests
- `@pytest.mark.integration` - Tests requiring database or services
- `@pytest.mark.e2e` - End-to-end workflow tests
- `@pytest.mark.requires_db` - Tests needing database with tables
- `@pytest.mark.requires_server` - Tests needing running MCP server
- `@pytest.mark.slow` - Tests taking >5 seconds
- `@pytest.mark.ai` - Tests involving AI/LLM features

## Critical Test Coverage

### Must-Have Integration Tests

These tests are critical for preventing regressions:

1. **GAM Configuration Flow** (`test_gam_tenant_setup.py`)
   - Tests OAuth → network code retrieval
   - Tests tenant creation without network code
   - Would have caught the major regression

2. **Database Operations** (`test_database_integration.py`)
   - Tests real database operations (not mocked)
   - Tests SQLite vs PostgreSQL compatibility
   - Tests migration integrity

3. **MCP Server** (`test_main.py`)
   - Tests all MCP tools
   - Tests authentication flow
   - Tests request/response validation

4. **Tenant Management** (`test_tenant_settings_comprehensive.py`)
   - Tests complete tenant setup
   - Tests adapter switching
   - Tests configuration updates

## Writing New Tests

### Guidelines

1. **Choose the right category:**
   - Unit: Testing a single function/class
   - Integration: Testing multiple components
   - E2E: Testing complete workflows
   - UI: Testing web interface

2. **Use appropriate mocking:**
   - Unit tests: Mock all external dependencies
   - Integration tests: Mock only external APIs
   - E2E tests: No mocking (use test mode)

3. **Follow naming conventions:**
   - Test files: `test_feature_name.py`
   - Test classes: `TestFeatureName`
   - Test methods: `test_specific_behavior`

4. **Use fixtures from `conftest.py`:**
   ```python
   def test_with_tenant(test_tenant):
       # test_tenant fixture provides a configured tenant
       assert test_tenant['tenant_id'] == 'test'
   ```

5. **Add proper markers:**
   ```python
   @pytest.mark.integration
   @pytest.mark.requires_db
   def test_database_operation():
       # Test that needs database
       pass
   ```

### Example Test Structure

```python
import pytest
from unittest.mock import Mock, patch

@pytest.mark.integration
@pytest.mark.requires_db
class TestFeatureName:
    """Test suite for specific feature."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup before each test."""
        # Setup code
        yield
        # Teardown code

    def test_happy_path(self, test_db):
        """Test normal successful operation."""
        # Arrange
        data = {"key": "value"}

        # Act
        result = function_under_test(data)

        # Assert
        assert result['status'] == 'success'

    def test_error_handling(self):
        """Test error scenarios."""
        with pytest.raises(ValueError):
            function_under_test(invalid_data)
```

## Test Data Management

### Fixtures

Common fixtures are defined in `tests/conftest.py`:

- `test_db` - Provides test database connection
- `test_tenant` - Creates test tenant
- `test_principal` - Creates test principal
- `mock_gam_client` - Mocked GAM client for testing

### Factories

We use factory patterns for test data:

```python
from tests.fixtures import TenantFactory, PrincipalFactory

def test_with_factory():
    tenant = TenantFactory.create(name="Test Publisher")
    principal = PrincipalFactory.create(tenant_id=tenant['tenant_id'])
```

## Continuous Integration

Tests run automatically on GitHub Actions:

1. **Unit Tests** - Run on every push
2. **Integration Tests** - Run on PRs
3. **E2E Tests** - Run before releases
4. **Coverage Reports** - Generated for main branch

## Common Issues and Solutions

### Issue: Test database not cleaned up
**Solution:** Use pytest fixtures with proper teardown:
```python
@pytest.fixture
def clean_db():
    # Setup
    yield db
    # Teardown - always runs
    cleanup_database()
```

### Issue: Tests pass locally but fail in CI
**Solution:** Check for:
- Environment variable differences
- Database state assumptions
- File system dependencies
- Timezone differences

### Issue: Flaky tests
**Solution:**
- Remove time-dependent assertions
- Use proper test isolation
- Mock external services consistently
- Add retry logic for network calls

## Test Coverage Goals

| Component | Current | Target | Priority |
|-----------|---------|--------|----------|
| Unit Tests | 70% | 85% | Medium |
| Integration Tests | 40% | 80% | High |
| E2E Tests | 5% | 50% | High |
| UI Tests | 30% | 60% | Medium |

## Running Tests in Docker

```bash
# Run tests in container
docker-compose exec adcp-server pytest tests/unit/

# Run with coverage
docker-compose exec adcp-server pytest --cov=. --cov-report=term-missing

# Run specific test file
docker-compose exec adcp-server pytest tests/integration/test_main.py -v
```

## Performance Testing

For performance-critical code:

```python
@pytest.mark.benchmark
def test_performance(benchmark):
    result = benchmark(expensive_function, arg1, arg2)
    assert result < threshold
```

## Security Testing

Security tests are in `tests/security/`:

```bash
# Run security tests
pytest tests/security/ -v

# Test SQL injection protection
pytest tests/security/test_sql_injection.py

# Test auth vulnerabilities
pytest tests/security/test_auth.py
```
