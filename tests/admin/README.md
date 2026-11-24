# Admin UI tests

This directory holds the tests for the Admin UI, the Flask application in `src/admin`.

## What the suite covers

Most modules test one blueprint. Their tests drive the real admin application through a Flask test client, against a real PostgreSQL database.

`test_sell_readiness_browser.py` drives a Chromium browser through Playwright against a running stack. It carries the `slow` marker, so `tox -e admin` leaves it out.

The behavior-driven scenarios for the Admin UI are in `tests/bdd`. See [Admin UI BDD pattern](../../docs/development/admin-bdd-pattern.md).

## Sign in from a test

The Admin UI has no login route for tests and no test password. A test states the session it needs with a helper from [`tests/helpers/admin_session.py`](../helpers/admin_session.py):

- `admin_auth_session(client, tenant_id)` writes a super admin session to a Flask test client.
- `authenticate_http_session(session, base_url, tenant_id)` sets a signed session cookie on a `requests.Session` for a running stack.
- `admin_session_cookie(tenant_id)` returns the signed cookie value for any other HTTP client, such as a browser.

To test a route that requires a single sign-on (SSO) login, pass `auth_method="oidc"`.

The following test creates a tenant, signs in, and requests a route. It is a shortened copy of a test in `test_format_search_unknown_tenant.py`:

```python
import pytest

from tests.factories import TenantFactory
from tests.helpers.admin_session import admin_auth_session

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]


@pytest.fixture
def tenant(factory_session):
    return TenantFactory(tenant_id="example_tenant", use_default_creative_agent=False)


def test_a_known_tenant_gets_its_catalog(admin_client, tenant):
    admin_auth_session(admin_client, tenant.tenant_id)

    response = admin_client.get(f"/api/formats/list?tenant_id={tenant.tenant_id}")

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json() == {"agents": {}, "total_formats": 0}
```

In a new module, use the `admin_client` fixture from [`conftest.py`](conftest.py) and import the session helpers from `tests.helpers.admin_session`. The fixture wraps `admin_test_app()` from the same helper module. That function builds the application from the settings the test process holds, so a setting that a test patched also applies to the application. Some older modules build their own application and keep a private `_auth_session` copy. Do not copy that pattern.

## Run the tests

The suite needs PostgreSQL, and the browser tests need the running application. The following table lists the commands and what each one does with the Docker test stack.

| Command | Scope | Docker test stack |
|---|---|---|
| `./run_all_tests.sh admin` | The whole suite, without the `slow` tests | Starts the stack and stops it afterward |
| `scripts/run-test.sh tests/admin/test_accounts_blueprint.py -x` | One module | Starts the stack, or reuses a running one |
| `source .test-stack.env && tox -e admin` | The whole suite, without the `slow` tests | Expects the stack that `make test-stack-up` started |

To stop a stack that stays up, run `make test-stack-down`.

`make test-entity ENTITY=admin` runs every test that carries the `admin` marker in the unit, integration, end-to-end, and admin suites.
