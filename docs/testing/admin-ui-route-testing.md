# Admin UI Route Testing

## Overview

We maintain **100% test coverage** for all Admin UI GET routes to ensure:
- Routes don't crash with template errors
- Database schema changes don't break pages
- Authentication and authorization work correctly
- Multi-tenancy is properly enforced

## Test Files

### Comprehensive Route Tests
**File**: `tests/integration/test_admin_ui_routes_comprehensive.py`

Contains **56 tests** covering all testable GET routes, organized by blueprint:

- ✅ **Core Routes** (7 tests): /, /health, /metrics, /mcp-test, etc.
- ✅ **Public Routes** (4 tests): /signup, /signup/start, etc.
- ✅ **Auth Routes** (3 tests): /login, /logout, etc.
- ✅ **Tenant Routes** (6 tests): Dashboard, settings, media buys, etc.
- ✅ **Products Routes** (2 tests): List, create
- ✅ **Principals Routes** (2 tests): List, create
- ✅ **Authorized Properties Routes** (4 tests): List, create, upload, tags
- ✅ **Inventory Routes** (5 tests): Browser, orders, targeting, sync
- ✅ **Operations Routes** (5 tests): Orders, reporting, targeting, webhooks, workflows
- ✅ **Workflows Routes** (1 test): List workflows
- ✅ **Policy Routes** (2 tests): Index, rules
- ✅ **Schema Routes** (5 tests): AdCP schema endpoints
- ✅ **API Routes** (4 tests): Health, OAuth status, revenue, suggestions
- ✅ **Activity Stream Routes** (3 tests): Activity, events, list
- ✅ **Settings Routes** (1 test): Settings index
- ✅ **Error Routes** (2 tests): 404 handling

### Original Route Tests
**File**: `tests/integration/test_admin_ui_pages.py`

Contains **17 tests** with additional validation:
- Authentication requirements
- Tenant isolation
- Permission checks

## Running the Tests

### With PostgreSQL (Required)
```bash
# Start PostgreSQL container first
docker run -d \
  --name adcp-test-postgres \
  -e POSTGRES_USER=adcp_user \
  -e POSTGRES_PASSWORD=test_password \
  -e POSTGRES_DB=adcp_test \
  -p 5433:5432 \
  postgres:15

# Run tests
export ADCP_TEST_DB_URL="postgresql://adcp_user:test_password@localhost:5433/adcp_test"
uv run pytest tests/integration/test_admin_ui_routes_comprehensive.py -v

# Or use the test script
./run_all_tests.sh ci
```

### Expected Results
```
56 passed, 7 warnings in ~12 seconds
```

## Pre-Commit Hook

### Automatic Coverage Enforcement
A pre-commit hook ensures **100% coverage** is maintained:

**Hook**: `admin-route-coverage`
**Script**: `scripts/check_admin_route_coverage.py`

This hook:
1. Extracts all GET routes from `src/admin/blueprints/*.py`
2. Checks if each route has a corresponding test
3. Reports missing tests
4. **Blocks commit** if coverage drops below 100%

### Running Manually
```bash
pre-commit run admin-route-coverage --all-files
```

### Example Output
```
📊 Found 91 GET routes in admin blueprints
📋 89 routes should have tests
✅ Found 24 routes with tests
✅ All 89 testable GET routes have tests!
Coverage: 100% (89/89 routes)
```

## Adding New Routes

When you add a new GET route to any admin blueprint:

### 1. Add the Route
```python
# src/admin/blueprints/my_blueprint.py
@my_bp.route("/tenant/<tenant_id>/new-feature", methods=["GET"])
@require_tenant_access()
def new_feature(tenant_id):
    return render_template("new_feature.html", tenant_id=tenant_id)
```

### 2. Add a Test
```python
# tests/integration/test_admin_ui_routes_comprehensive.py
class TestMyBlueprintRoutes:
    def test_new_feature_page(self, authenticated_admin_session, test_tenant_with_data):
        """Test new feature page renders."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(
            f"/tenant/{tenant_id}/new-feature",
            follow_redirects=True
        )
        assert response.status_code == 200
```

### 3. Pre-commit Hook Validates
The hook will **automatically verify** your test exists when you commit.

If you forget the test:
```bash
❌ Missing tests for 1 routes:
================================================================================
  my_blueprint.py                /tenant/<tenant_id>/new-feature

Please add tests for these routes to:
  - tests/integration/test_admin_ui_routes_comprehensive.py
```

## Route Categories

### Testable GET Routes (89 routes)
Routes that:
- Accept GET requests
- Render HTML templates
- Return JSON data
- Can be tested without complex setup

### Skipped Routes (2 routes)
Routes excluded from coverage requirements:
- `/auth/google/callback` - OAuth redirect
- `/auth/gam/callback` - GAM OAuth redirect
- `/test/auth` - Test-only endpoint

These are tested through integration flows, not direct GET requests.

### POST-Only Routes (59 routes)
Routes that only accept POST/PUT/DELETE:
- Form submissions
- API mutations
- Delete operations

These are tested through functional tests, not route rendering tests.

## Test Fixtures

### Key Fixtures Used
- `integration_db` - PostgreSQL database with full schema
- `admin_client` - Flask test client (unauthenticated)
- `authenticated_admin_session` - Authenticated super admin session
- `test_tenant_with_data` - Pre-created tenant for testing

### Test Patterns

**Public Routes (no auth):**
```python
def test_public_page(self, admin_client):
    response = admin_client.get("/public-page")
    assert response.status_code == 200
```

**Authenticated Routes:**
```python
def test_protected_page(self, authenticated_admin_session):
    response = authenticated_admin_session.get("/protected")
    assert response.status_code == 200
```

**Tenant-Specific Routes:**
```python
def test_tenant_page(self, authenticated_admin_session, test_tenant_with_data):
    tenant_id = test_tenant_with_data["tenant_id"]
    response = authenticated_admin_session.get(f"/tenant/{tenant_id}/page")
    assert response.status_code == 200
```

**Not-Yet-Implemented Routes:**
```python
def test_future_feature(self, authenticated_admin_session, test_tenant_with_data):
    response = authenticated_admin_session.get("/future-feature")
    # Accept 200 (implemented) or 501 (not implemented)
    assert response.status_code in [200, 501]
```

## Troubleshooting

### Tests Fail After Schema Change
If admin UI tests fail after a database schema change:

1. **Check migration**: Ensure Alembic migration ran successfully
2. **Check models**: Verify SQLAlchemy models match schema
3. **Check fixtures**: Update `tests/integration/conftest.py` if models changed
4. **Run locally**: Test with `./run_all_tests.sh ci` before pushing

### Pre-commit Hook Fails
If the coverage hook reports missing tests:

1. **Add the test**: Follow the "Adding New Routes" section above
2. **Or mark as skipped**: If the route truly can't be tested, add it to `should_skip_route()` in `scripts/check_admin_route_coverage.py`

### Template Errors
If a test fails with a template error:

1. **Check template exists**: Verify the template file exists in `templates/`
2. **Check variables**: Ensure all template variables are passed correctly
3. **Check inheritance**: Verify template extends correct base template
4. **Run locally**: Use `docker-compose up` to test manually

## Benefits

### For Developers
- ✅ Confidence that schema changes don't break UI
- ✅ Fast feedback (12 seconds for 56 tests)
- ✅ No need to manually test every page
- ✅ Pre-commit hook catches issues before push

### For the Project
- ✅ 100% GET route coverage guaranteed
- ✅ No regressions slip through
- ✅ Database schema changes are safe
- ✅ Template errors caught early

### For Production
- ✅ Routes actually work with real database
- ✅ Authentication is properly configured
- ✅ Multi-tenancy isolation is enforced
- ✅ Error pages render correctly

## Scripts

### Analysis Script
**File**: `scripts/analyze_routes.py`

Analyzes all admin routes and categorizes them:
```bash
uv run python scripts/analyze_routes.py
```

Output:
- Total routes found
- Testable GET routes
- Auth routes
- API endpoints
- POST-only routes
- Requires-data routes

### Coverage Checker
**File**: `scripts/check_admin_route_coverage.py`

Verifies test coverage:
```bash
uv run python scripts/check_admin_route_coverage.py
```

Exit codes:
- `0`: 100% coverage ✅
- `1`: Missing tests ❌

## Maintenance

### When to Update Tests
- Adding new routes → Add corresponding test
- Changing route paths → Update test path
- Changing authentication → Update fixture usage
- Removing routes → Remove test (hook will pass)

### Quarterly Review
Every quarter, run the analysis script to ensure:
- Test organization is still logical
- No routes have become orphaned
- Coverage remains at 100%

## See Also

- `docs/testing/integration-testing.md` - Integration testing patterns
- `docs/testing/fixtures.md` - Test fixture documentation
- `tests/integration/conftest.py` - Fixture definitions
- `.pre-commit-config.yaml` - Pre-commit hook configuration
