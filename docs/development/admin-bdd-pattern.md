# Admin UI BDD Pattern

How to write BDD tests for admin UI features (Flask blueprints).

## Architecture Overview

Admin BDD tests follow the same Gherkin pattern as API BDD tests but with a
fundamentally different transport layer:

| Aspect | API BDD (UC-xxx) | Admin BDD (T-ADMIN-xxx) |
|--------|-------------------|-------------------------|
| Transport | MCP / A2A / REST / IMPL | Flask test_client / requests.Session |
| Auth | ResolvedIdentity | Flask session cookies |
| Response | Pydantic models | HTML pages + JSON API |
| Parametrize | 4 API transports | Not parametrized (single transport) |
| Harness | IntegrationEnv subclasses | AdminAccountEnv |

## File Structure

```
tests/bdd/
├── features/
│   └── BR-ADMIN-ACCOUNTS.feature    # Gherkin scenarios
├── steps/domain/
│   └── admin_accounts.py            # Step definitions
├── test_admin_accounts.py           # scenarios() binding
└── conftest.py                      # T-ADMIN- tag handling

tests/harness/
└── admin_accounts.py                # AdminAccountEnv (dual transport)
```

## Adding a New Admin Feature

### 1. Write the Gherkin Feature File

Create `tests/bdd/features/BR-ADMIN-<FEATURE>.feature`:

```gherkin
# Hand-authored feature — not compiled from adcp-req

Feature: BR-ADMIN-<FEATURE> Admin <Feature> Management
  As a Tenant Admin
  I want to manage <feature> through the admin web interface
  So that ...

  Background:
    Given an admin user is authenticated for tenant "test-tenant"
    And the tenant "test-tenant" exists in the database

  @T-ADMIN-<FEAT>-001 @list @main-flow
  Scenario: List <items>
    ...
```

**Conventions:**
- First line: `# Hand-authored feature — not compiled from adcp-req`
- Tag prefix: `@T-ADMIN-<FEAT>-NNN` (e.g., `@T-ADMIN-ACCT-001`)
- Background: authenticate admin + ensure tenant
- Add traceability entries to `docs/test-obligations/bdd-traceability.yaml`

### 2. Create Step Definitions

Create `tests/bdd/steps/domain/admin_<feature>.py`:

```python
from pytest_bdd import given, parsers, then, when

@given(parsers.parse('an admin user is authenticated for tenant "{tenant_id}"'))
def given_admin_authenticated(ctx, tenant_id):
    env = ctx["env"]
    env.authenticate(env.tenant_id)

@when("the admin navigates to the <feature> list page")
def when_navigate_list(ctx):
    ctx["response"] = ctx["env"].get_list_page()

@then(parsers.parse("the page returns status {code:d}"))
def then_status(ctx, code):
    assert ctx["response"].status_code == code
```

### 3. Register in conftest.py

Add to `pytest_plugins` list in `tests/bdd/conftest.py`:
```python
pytest_plugins = [
    ...
    "tests.bdd.steps.domain.admin_<feature>",
]
```

### 4. Create scenarios() Binding

Create `tests/bdd/test_admin_<feature>.py`:
```python
from pytest_bdd import scenarios
scenarios("features/BR-ADMIN-<FEATURE>.feature")
```

### 5. Create or Extend the Harness

The `AdminAccountEnv` in `tests/harness/admin_accounts.py` demonstrates the
pattern. Key features:

- **Dual transport**: `integration` (Flask test_client) and `e2e` (requests.Session)
- **Auto-selection**: `ADCP_SALES_PORT` env var switches to e2e mode
- **Response wrapper**: `_AdminResponse` normalizes Flask vs requests responses
- **Auth helpers**: `authenticate()` / `clear_auth()` for both transports
- **Data setup**: Direct DB for integration, HTTP POST for e2e

## Transport Details

### Integration (default)

Uses Flask `test_client` — fast, in-process, no Docker needed.
Auth via `session_transaction()` to inject test user data.
Available in `tox -e bdd`.

### E2E (Docker)

Uses `requests.Session` against running Docker stack.
Auth via `POST /test/auth` with form data (not JSON).
Available when `ADCP_SALES_PORT` is set (e.g., via `./run_all_tests.sh`).

## How conftest.py Handles Admin Scenarios

1. **No API transport parametrize**: `pytest_generate_tests` detects `T-ADMIN-`
   tags and skips the 4-transport parametrize.
2. **Harness auto-wire**: `_harness_env` detects `ADMIN` UC and provides
   `AdminAccountEnv` from `tests/harness/admin_accounts.py`.
3. **Entity marker**: Admin scenarios get `pytest.mark.admin` automatically.
4. **xfail for @pending**: Scenarios tagged `@pending` are xfailed until
   step definitions are implemented.
