# Admin UI BDD pattern

Admin UI features (Flask blueprints) get their own BDD pattern. The admin UI
serves HTML forms and a small JSON API rather than AdCP tools, so it has its
own transports, its own env, and its own `ctx` key.

## How admin scenarios differ from AdCP scenarios

| Aspect | AdCP BDD (UC-xxx) | Admin BDD (T-ADMIN-xxx) |
|---|---|---|
| Transport | MCP, A2A, REST (plus `e2e_rest`) | Flask `test_client`, or `requests.Session` against the live stack |
| Parametrized over | `Transport.A2A`, `Transport.MCP`, `Transport.REST`, plus the e2e members when `BDD_E2E_ENABLED=true` (`pytest_generate_tests` in `tests/bdd/conftest.py`) | `AdminTransport.INTEGRATION`, plus `AdminTransport.E2E` when `BDD_E2E_ENABLED=true` (the `T-ADMIN-` branch of `pytest_generate_tests`) |
| Caller identity | A credential headers dict; production's resolver builds the identity from it | A Flask session cookie, set through the env's `authenticate()` |
| What a Then reads | The wire envelope, through the guarded helpers in `tests/bdd/steps/_outcome_helpers.py` | The `AdminResponse` the When stored under `ctx["admin_page"]` |
| Harness | A domain `IntegrationEnv` subclass | `AdminAccountEnv` (`tests/harness/admin_accounts.py`), or a domain env that sends its admin requests through `AdminClient` |

`AdminTransport` is a `StrEnum` and is deliberately **not** a member of
`tests.harness.transport.Transport` (see its docstring in
`tests/harness/admin_accounts.py`):
every `Transport` member maps to an AdCP protocol, and the admin UI has none —
so a member there needs a fabricated protocol. Because `StrEnum` members are
`str`, a value that leaks into `dispatch_request` misses the transport map and
raises loudly instead of dispatching somewhere wrong.

## File structure

```
tests/bdd/
├── features/
│   ├── BR-ADMIN-ACCOUNTS.feature                      # account pages
│   ├── BR-ADMIN-INVENTORY-PROFILES.feature            # inventory-profile modal
│   ├── BR-ADMIN-INVENTORY-PROFILE-publishers.feature  # inventory-profile and product forms
│   ├── BR-ADMIN-OPERATOR-REFUSALS.feature             # expected refusals
│   ├── BR-ADMIN-TENANT-SCOPING.feature                # tenant-scoped route authorization (#2203, #2204)
│   ├── BR-ADMIN-UNEXPECTED-ERRORS.feature             # unexpected failures
│   └── local-publisher-authorization.feature          # publisher pages
├── steps/domain/
│   ├── admin_accounts.py                # account steps and the shared admin page steps
│   ├── admin_inventory_profiles.py      # inventory-profile and product form steps
│   ├── admin_operator_refusals.py       # expected-refusal steps
│   ├── admin_tenant_scoping.py          # tenant-scoping steps
│   ├── admin_unexpected_errors.py       # unexpected-failure steps
│   └── publisher_authorization.py       # publisher steps
├── test_admin_accounts.py               # scenarios() bindings, one module per feature
├── test_admin_inventory_profiles.py
├── test_admin_inventory_profile_publishers.py
├── test_admin_operator_refusals.py
├── test_admin_tenant_scoping.py
├── test_admin_unexpected_errors.py
├── test_local_publisher_authorization.py
└── conftest.py                          # T-ADMIN- parametrization, marker, ENV_ROUTES rows

tests/harness/
├── admin_client.py                  # AdminClient, AdminResponse: the two transports, once
├── admin_accounts.py                # AdminTransport, AdminAccountEnv
├── admin_inventory_profile.py       # AdminInventoryProfileEnv
├── admin_tenant_scoping.py          # AdminTenantScopingEnv (AdminAccountEnv subclass)
└── publisher_authorization.py       # PublisherAuthorizationEnv

tests/helpers/
└── admin_session.py                 # admin_test_app(), the session payloads, and the one writer per transport
```

`BR-ADMIN-TENANT-SCOPING` is claimed by a predicate `ENV_ROUTES` row ahead of the
`ADMIN` bucket (`tag="admin-tenant-scoping"`) because it needs its own harness. Like
`BR-ADMIN-ACCOUNTS`, it runs on both admin transports: both rows build their env through
`_admin_env_builder`, which returns the in-process env for `admin_integration` and the
live-stack env at `e2e_config.base_url` for `e2e_admin`.
`tests/unit/test_bdd_admin_transport_parametrization.py` takes each row's builder from
`ENV_ROUTES` and checks it on both transports. The env enters `no_configured_super_admins()`
(`tests/helpers/admin_session.py`) for the scenario, so the member it logs in as is never an
ambient super admin; T-ADMIN-SCOPE-014 states one explicitly, with the two session keys
`is_super_admin()` caches. The caller's active row in the target tenant holds the `manager`
role, the least role that may send a write, unless the scenario names another.
T-ADMIN-SCOPE-011 and T-ADMIN-SCOPE-012 name one, because the OIDC test route asks for
`admin`. The three rows the membership lookup must not match (the target tenant's other
member, the caller's row in another tenant, and the caller's inactive row) hold `admin`,
which passes the role check of every route in the harness's `ROUTES`, so a refusal on those
rows is the lookup's. When it exits, the env deletes the target tenant and the home tenant
it created. The feature file is the one statement of
the contract: the pytest twins that once ran the same assertions in-process and against the
Docker stack were deleted once `AdminTransport.E2E` carried the Gherkin, as
BR-ADMIN-ACCOUNTS' own `test_admin_bdd_e2e.py` had been.

## Add an admin feature

### 1. Write the feature file

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

Follow these conventions:

- Start the file with `# Hand-authored feature — not compiled from adcp-req`.
- Tag every scenario `@T-ADMIN-<FEAT>-NNN`. `_ADMIN_TAG_PREFIX` in
  `tests/bdd/conftest.py` routes the scenario to the admin transports and the
  admin marker, so the prefix is the wiring, not a label.
- Authenticate the admin and ensure the tenant in the `Background`.
- Name the transports the feature runs on in a header comment, as the
  `# Transports:` block at the top of `BR-ADMIN-ACCOUNTS.feature` does.
- Add traceability entries to `docs/test-obligations/bdd-traceability.yaml`.

### 2. Write the step definitions

Create `tests/bdd/steps/domain/admin_<feature>.py`. A When stores the response
under `ctx["admin_page"]` and a Then reads it back through one accessor:

```python
from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import _require


def _require_admin_page(ctx: dict) -> object:
    return _require(ctx, "admin_page", hint="The admin page request may have errored instead.")


@given(parsers.parse('an admin user is authenticated for tenant "{tenant_id}"'))
def given_admin_authenticated(ctx, tenant_id):
    env = ctx["env"]
    env.authenticate(env.tenant_id)


@when("the admin navigates to the <feature> list page")
def when_navigate_list(ctx):
    ctx["admin_page"] = ctx["env"].get_list_page()


@then(parsers.parse("the page returns status {code:d}"))
def then_status(ctx, code):
    response = _require_admin_page(ctx)
    assert response.status_code == code, f"Expected status {code}, got {response.status_code}"
```

**Do not write `ctx["response"]`.** Check E of
`tests/unit/test_architecture_bdd_wire_discipline.py` bans that
key outright with a permanently empty allowlist, because a provenance-stripped
copy of a payload cannot tell a Then whether it holds a wire fact or an
in-process reconstruction. An admin page is not an AdCP payload either. Sharing
one key made a Flask response indistinguishable from a protocol payload to
anything that read it, so the admin steps own `ctx["admin_page"]`
(`_require_admin_page` in `tests/bdd/steps/domain/admin_accounts.py`).

Every HTTP call goes through an env method (`get_list_page`, `post_create`,
`post_status_change`, …) so both transports run the same step. The env sends it
through `AdminClient.request` (`tests/harness/admin_client.py`), the one place
that knows the two transports. `AdminResponse` normalizes the two response
objects and folds headers case-insensitively (`_CaseInsensitiveHeaders`).
`dict(response.headers)` threw that property away, and six scenarios read every
redirect as "no redirect happened" the first time they ran over real HTTP.

### 3. Register the step module

Add it to the `pytest_plugins` list in `tests/bdd/conftest.py`:

```python
pytest_plugins = [
    ...
    "tests.bdd.steps.domain.admin_<feature>",
]
```

### 4. Bind the feature

Create `tests/bdd/test_admin_<feature>.py`:

```python
from pytest_bdd import scenarios

scenarios("features/BR-ADMIN-<FEATURE>.feature")
```

Nothing collects a feature file that has no binding module, so this step is what
makes the scenarios run at all.

### 5. Extend the harness

`AdminAccountEnv` in `tests/harness/admin_accounts.py` is the pattern to
follow:

- **The caller names the transport; the env never infers it** (`__init__`,
  which holds the two refusals). The constructor takes
  `mode="integration" | "e2e"` and, for e2e, a required `base_url`; it rejects
  any other mode and rejects `mode="e2e"` without an address. An env that
  guesses its transport from a process-global cannot tell "my caller wants e2e"
  from "the container publishes a port for unrelated reasons", and a global
  cannot carry a different address per xdist worker at all.
- **`__enter__` calls `__exit__` if it raises**, so a failure in
  tenant setup does not strand the Flask client or the `requests` session for
  the rest of the process.
- **One method builds every route** (`_url`), and each page gets a method
  returning `AdminResponse`.
- **The transport lives in `AdminClient`, never in the env.** The env opens one
  `AdminClient` (`None` for the Flask client, the live server's URL otherwise)
  and sends every request through it. `AdminClient.state_session()` states a
  session through the transport's one writer in `tests/helpers/admin_session.py`,
  and both writers replace the session the client held: `state_flask_session`
  clears and rewrites the Flask client's session, and `state_http_session` swaps
  every `session` cookie for one signed the way the server signs its own.
  `authenticate()` states the super-admin session through it.
  `PublisherAuthorizationEnv` drives its admin routes through the same client.
- **Seeding goes through the production write path.** `create_account`
  uses `AccountUoW` → `AccountRepository.create()`, so a
  harness-seeded row obeys the invariants a production-created one does — above
  all the natural-key collision refusal. It propagates `NaturalKeyConflict`
  deliberately. Seeding straight into the table is the one seam through which a
  test can establish a state production forbids, and a test asserting on an
  impossible state proves nothing.
- **Read-backs are env methods too** (`get_account_from_db`,
  `accounts_on_natural_key`, `accounts_with_brand_domain`), so a
  lookup follows whichever database the transport selected instead of a step
  opening its own session.

## Transport details

### Integration (`admin_integration`)

Flask `test_client` against `admin_test_app()` (`tests/helpers/admin_session.py`,
the one composition every admin harness uses), in process, no Docker, opened by
`AdminClient`. Auth injects the session through
`tests/helpers/admin_session.py`. Runs in `tox -e bdd`.

### Live stack (`e2e_admin`)

`requests.Session` against the address the caller supplied, opened by
`AdminClient`. Auth sets a session cookie signed the way the server signs its
own (`state_http_session`). pytest collects it only when
`BDD_E2E_ENABLED=true`, and the per-worker address comes from the `e2e_stack`
fixture by way of `_admin_env_builder`.

## How conftest handles admin scenarios

1. **Admin transports instead of AdCP ones.** `pytest_generate_tests` sees a
   `T-ADMIN-` tag and parametrizes `ctx` over `AdminTransport.INTEGRATION` plus
   `AdminTransport.E2E`, through the same `_parametrize_ctx` helper the AdCP
   branch uses (both in `tests/bdd/conftest.py`). It derives the pytest ids from
   the enum values, because `tox.ini`'s `-k` selectors match on them.
   Every admin scenario gets both legs. When the live stack cannot realize a Given
   as it stands, the harness or the stack is extended until it can:
   `local-publisher-authorization.feature` serves a publisher's adagents.json from the
   stack's `publisher-origin` service, which the live server reaches on 443 through
   tls-proxy (`tests/harness/publisher_authorization.py`).
2. **A registry row builds the env.** The `ADMIN` row of `_UC_BUCKET_ROUTES`
   in `tests/bdd/conftest.py` and the `admin-tenant-scoping` predicate row of
   `ENV_ROUTES`, which claims `T-ADMIN-SCOPE-*` ahead of it, each name their
   env class to `_admin_env_builder`. `_uc_bucket_rows()` appends the `ADMIN`
   row to `ENV_ROUTES`, and `_run_env_route` is the single consumer. That
   builder is the only one that passes `base_url=` rather than `e2e_config=`,
   because the admin env needs the address and nothing else from `E2EConfig`.
   `tests/unit/test_bdd_admin_transport_parametrization.py` checks that every
   row an admin scenario resolves to, and that builds an `AdminAccountEnv`,
   builds the env its transport names.
   An admin feature that needs a different env adds its own `ENV_ROUTES` row,
   which wins over the `ADMIN` row: `admin-inventory-profile-publishers` builds
   `AdminInventoryProfileEnv`, and `pubauth` builds `PublisherAuthorizationEnv`.
3. **The conftest applies the `admin` entity marker automatically** to any
   scenario carrying a `T-ADMIN-` tag, so `pytest tests/bdd/ -m admin` selects
   the admin scenarios. `make test-entity` does not collect `tests/bdd`.
4. **A missing step binding fails the run; there is no `@pending` tag.**
   Only scenarios already recorded in the shrink-only
   `tests/bdd/dormant_scenarios.txt` are reported as dormant instead of failing
   (`_record_dormancy`, called by the `pytest_runtest_makereport` hook in
   `tests/bdd/conftest.py`). Declare an intent the live stack
   cannot serve at the env method with `e2e_unsupported(...)` instead
   (defined in `tests/harness/_realize.py`); it arrives as a non-strict xfail
   carrying its reason, set by the same hook.
