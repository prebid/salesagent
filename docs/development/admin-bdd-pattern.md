# Admin UI BDD pattern

Admin UI features (Flask blueprints) get their own BDD pattern. The admin UI
serves HTML forms and a small JSON API rather than AdCP tools, so it has its
own transports, its own env, and its own `ctx` key.

## How admin scenarios differ from AdCP scenarios

| Aspect | AdCP BDD (UC-xxx) | Admin BDD (T-ADMIN-xxx) |
|---|---|---|
| Transport | MCP, A2A, REST (plus `e2e_rest`) | Flask `test_client`, or `requests.Session` against the live stack |
| Parametrized over | `Transport.A2A`, `Transport.MCP`, `Transport.REST`, plus the e2e members when `BDD_E2E_ENABLED=true` (`tests/bdd/conftest.py:4432-4435`) | `AdminTransport.INTEGRATION`, plus `AdminTransport.E2E` when `BDD_E2E_ENABLED=true` (`tests/bdd/conftest.py:4383-4387`) |
| Caller identity | A credential headers dict; production's resolver builds the identity from it | A Flask session cookie, set through the env's `authenticate()` |
| What a Then reads | The wire envelope, through the guarded helpers in `tests/bdd/steps/_outcome_helpers.py` | The `_AdminResponse` the When stored under `ctx["admin_page"]` |
| Harness | A domain `IntegrationEnv` subclass | `AdminAccountEnv` (`tests/harness/admin_accounts.py`) |

`AdminTransport` is a `StrEnum` and is deliberately **not** a member of
`tests.harness.transport.Transport` (`tests/harness/admin_accounts.py:29-49`):
every `Transport` member maps to an AdCP protocol, and the admin UI has none —
so a member there needs a fabricated protocol. Because `StrEnum` members are
`str`, a value that leaks into `dispatch_request` misses the transport map and
raises loudly instead of dispatching somewhere wrong.

## File structure

```
tests/bdd/
├── features/
│   ├── BR-ADMIN-ACCOUNTS.feature          # Gherkin scenarios
│   └── BR-ADMIN-TENANT-SCOPING.feature    # tenant-scoped route authorization (#2203)
├── steps/domain/
│   ├── admin_accounts.py            # step definitions
│   └── admin_tenant_scoping.py      # step definitions
├── test_admin_accounts.py           # scenarios() binding
├── test_admin_tenant_scoping.py     # scenarios() binding
└── conftest.py                      # T-ADMIN- parametrization, marker, ENV_ROUTES rows

tests/harness/
├── admin_accounts.py                # AdminTransport, AdminAccountEnv, _AdminResponse
└── admin_tenant_scoping.py          # AdminTenantScopingEnv (AdminAccountEnv subclass)

tests/helpers/
├── admin_session.py                   # admin_auth_session() for the Flask client
└── admin_tenant_scoping_contract.py   # #2203 contract classes, written once

tests/admin/
└── test_tenant_scoped_routes_auth.py  # collects them in-process

tests/e2e/
└── test_admin_tenant_scoping_e2e.py   # the same classes over the live stack
```

`BR-ADMIN-TENANT-SCOPING` is claimed by a predicate `ENV_ROUTES` row ahead of the
`ADMIN` bucket (`tag="admin-tenant-scoping"`, pinned in `EXPECTED_WIRED_ROUTES`) because
it needs its own harness. It differs from `BR-ADMIN-ACCOUNTS` in one way: it is
parametrized on the integration transport only, and its live-stack leg is the separate
`tests/e2e` module above, written before `AdminTransport.E2E` existed. Converting it to
the transport axis — and deleting that module, as BR-ADMIN-ACCOUNTS' own
`test_admin_bdd_e2e.py` was deleted — is open work.

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
- Tag every scenario `@T-ADMIN-<FEAT>-NNN`. `_ADMIN_TAG_PREFIX`
  (`tests/bdd/conftest.py:4094`) routes the scenario to the admin transports
  and the admin marker, so the prefix is the wiring, not a label.
- Authenticate the admin and ensure the tenant in the `Background`.
- Name the transports the feature runs on in a header comment, as
  `BR-ADMIN-ACCOUNTS.feature:13-15` does.
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
`tests/unit/test_architecture_bdd_wire_discipline.py` (`:992` onward) bans that
key outright with a permanently empty allowlist, because a provenance-stripped
copy of a payload cannot tell a Then whether it holds a wire fact or an
in-process reconstruction. An admin page is not an AdCP payload either. Sharing
one key made a Flask response indistinguishable from a protocol payload to
anything that read it, so the admin steps own `ctx["admin_page"]`
(`tests/bdd/steps/domain/admin_accounts.py:51-58`).

Every HTTP call goes through an env method (`get_list_page`, `post_create`,
`post_status_change`, …) so both transports run the same step.
`_AdminResponse` (`tests/harness/admin_accounts.py:78-120`) normalizes the two
response objects and folds headers case-insensitively
(`_CaseInsensitiveHeaders`, `:51-76`). `dict(response.headers)` threw that
property away, and six scenarios read every redirect as "no redirect happened"
the first time they ran over real HTTP.

### 3. Register the step module

Add it to `pytest_plugins` in `tests/bdd/conftest.py:57-94`:

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

`AdminAccountEnv` (`tests/harness/admin_accounts.py:124`) is the pattern to
follow:

- **The caller names the transport; the env never infers it** (`:137-175`, with
  the two refusals at `:157-162`). The constructor takes
  `mode="integration" | "e2e"` and, for e2e, a required `base_url`; it rejects
  any other mode and rejects `mode="e2e"` without an address. An env that
  guesses its transport from a process-global cannot tell "my caller wants e2e"
  from "the container publishes a port for unrelated reasons", and a global
  cannot carry a different address per xdist worker at all.
- **`__enter__` calls `__exit__` if it raises** (`:182-197`), so a failure in
  tenant setup does not strand the Flask client or the `requests` session for
  the rest of the process.
- **One method builds every route** (`_url`, `:282-284`), and each page gets a
  method returning `_AdminResponse`.
- **Auth has one entry point**, `authenticate()` (`:237-243`), branching to
  `admin_auth_session` for the Flask client or a `POST /test/auth` form for the
  live stack.
- **Seeding goes through the production write path.** `create_account`
  (`:339-385`) uses `AccountUoW` → `AccountRepository.create()`, so a
  harness-seeded row obeys the invariants a production-created one does — above
  all the natural-key collision refusal. It propagates `NaturalKeyConflict`
  deliberately. Seeding straight into the table is the one seam through which a
  test can establish a state production forbids, and a test asserting on an
  impossible state proves nothing.
- **Read-backs are env methods too** (`get_account_from_db`,
  `accounts_on_natural_key`, `accounts_with_brand_domain`, `:387-430`), so a
  lookup follows whichever database the transport selected instead of a step
  opening its own session.

## Transport details

### Integration (`admin_integration`)

Flask `test_client` against `create_app()`, in process, no Docker
(`:214-222`). Auth injects the session through
`tests/helpers/admin_session.py`. Runs in `tox -e bdd`.

### Live stack (`e2e_admin`)

`requests.Session` against the address the caller supplied (`:224-235`). Auth
posts form data to `/test/auth` and keeps the session cookie; anything but `200`
or `302` raises (`:251-265`). pytest collects it only when
`BDD_E2E_ENABLED=true`, and the per-worker address comes from the `e2e_stack`
fixture by way of `_build_admin_env`.

## How conftest handles admin scenarios

1. **Admin transports instead of AdCP ones.** `pytest_generate_tests` sees a
   `T-ADMIN-` tag and parametrizes `ctx` over `AdminTransport.INTEGRATION` plus
   `AdminTransport.E2E`, through the same `_parametrize_ctx` helper the AdCP
   branch uses (`tests/bdd/conftest.py:4379-4387`, `:4104-4135`). It derives the
   pytest ids from the enum values, because `tox.ini`'s `-k` selectors match on
   them.
2. **One registry row builds the env.** `ENV_ROUTES["ADMIN"]`
   (`tests/bdd/conftest.py:5061`) names `_build_admin_env` (`:4782-4806`), and
   `_run_env_route` is the single consumer. That builder is the only one that
   passes `base_url=` rather than `e2e_config=`, because the admin env needs the
   address and nothing else from `E2EConfig`;
   `tests/unit/test_bdd_admin_transport_parametrization.py` machine-checks that
   asymmetry.
3. **The conftest applies the `admin` entity marker automatically** to any
   scenario carrying a `T-ADMIN-` tag (`tests/bdd/conftest.py:3833-3834`), so
   `make test-entity ENTITY=admin` picks it up.
4. **A missing step binding xfails at runtime — there is no `@pending` tag.**
   pytest-bdd reports the missing definition and the conftest converts the
   failure, so the code is the source of truth and no metadata goes stale
   (`tests/bdd/conftest.py:96-103`). A scenario that falls dormant still fails,
   since `tests/bdd/dormant_scenarios.txt` is a shrink-only baseline enforced
   per test. Declare an intent the live stack cannot serve at the env method
   with `e2e_unsupported(...)` instead (`tests/harness/_realize.py:94-105`); it
   arrives as a non-strict xfail carrying its reason
   (`tests/bdd/conftest.py:320-326`).
