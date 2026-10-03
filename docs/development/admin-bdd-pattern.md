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
| What a Then reads | The wire envelope, through the guarded helpers in `tests/bdd/steps/_outcome_helpers.py` | The `AdminResponse` the When stored under `ctx["admin_page"]` |
| Harness | A domain `IntegrationEnv` subclass | `AdminAccountEnv` (`tests/harness/admin_accounts.py`) |

`AdminTransport` is a `StrEnum` and is deliberately **not** a member of
`tests.harness.transport.Transport` (`tests/harness/admin_accounts.py:26-46`):
every `Transport` member maps to an AdCP protocol, and the admin UI has none —
so a member there needs a fabricated protocol. Because `StrEnum` members are
`str`, a value that leaks into `dispatch_request` misses the transport map and
raises loudly instead of dispatching somewhere wrong.

## File structure

```
tests/bdd/
├── features/
│   └── BR-ADMIN-ACCOUNTS.feature    # Gherkin scenarios
├── steps/domain/
│   └── admin_accounts.py            # step definitions
├── test_admin_accounts.py           # scenarios() binding
└── conftest.py                      # T-ADMIN- parametrization, marker, ENV_ROUTES row

tests/harness/
├── admin_client.py                  # AdminClient, AdminResponse: the two transports, once
└── admin_accounts.py                # AdminTransport, AdminAccountEnv

tests/helpers/
└── admin_session.py                 # admin_auth_session() for the Flask client
```

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
`post_status_change`, …) so both transports run the same step. The env sends it
through `AdminClient.request` (`tests/harness/admin_client.py`), the one place
that knows the two transports. `AdminResponse` normalizes the two response
objects and folds headers case-insensitively (`_CaseInsensitiveHeaders`).
`dict(response.headers)` threw that property away, and six scenarios read every
redirect as "no redirect happened" the first time they ran over real HTTP.

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

`AdminAccountEnv` (`tests/harness/admin_accounts.py:48`) is the pattern to
follow:

- **The caller names the transport; the env never infers it** (`:61-94`, with
  the two refusals at `:81-86`). The constructor takes
  `mode="integration" | "e2e"` and, for e2e, a required `base_url`; it rejects
  any other mode and rejects `mode="e2e"` without an address. An env that
  guesses its transport from a process-global cannot tell "my caller wants e2e"
  from "the container publishes a port for unrelated reasons", and a global
  cannot carry a different address per xdist worker at all.
- **`__enter__` calls `__exit__` if it raises** (`:100-111`), so a failure in
  tenant setup does not strand the Flask client or the `requests` session for
  the rest of the process.
- **One method builds every route** (`_url`), and each page gets a method
  returning `AdminResponse`.
- **The transport lives in `AdminClient`, never in the env.** The env opens one
  `AdminClient` (`None` for the Flask client, the live server's URL otherwise)
  and sends every request through it. `AdminClient.authenticate()` is the one
  auth entry point: `admin_auth_session` writes the session on the Flask client,
  and `authenticate_http_session` sets a signed session cookie for the live
  stack. `PublisherAuthorizationEnv` drives its admin routes through the same
  client.
- **Seeding goes through the production write path.** `create_account`
  (`:180-226`) uses `AccountUoW` → `AccountRepository.create()`, so a
  harness-seeded row obeys the invariants a production-created one does — above
  all the natural-key collision refusal. It propagates `NaturalKeyConflict`
  deliberately. Seeding straight into the table is the one seam through which a
  test can establish a state production forbids, and a test asserting on an
  impossible state proves nothing.
- **Read-backs are env methods too** (`get_account_from_db`,
  `accounts_on_natural_key`, `accounts_with_brand_domain`, `:228-278`), so a
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
own (`authenticate_http_session`). pytest collects it only when
`BDD_E2E_ENABLED=true`, and the per-worker address comes from the `e2e_stack`
fixture by way of `_build_admin_env`.

## How conftest handles admin scenarios

1. **Admin transports instead of AdCP ones.** `pytest_generate_tests` sees a
   `T-ADMIN-` tag and parametrizes `ctx` over `AdminTransport.INTEGRATION` plus
   `AdminTransport.E2E`, through the same `_parametrize_ctx` helper the AdCP
   branch uses (`tests/bdd/conftest.py:4379-4387`, `:4104-4135`). It derives the
   pytest ids from the enum values, because `tox.ini`'s `-k` selectors match on
   them.
   Every admin scenario gets both legs. When the live stack cannot realize a Given
   as it stands, the harness or the stack is extended until it can:
   `local-publisher-authorization.feature` serves a publisher's adagents.json from a
   TLS origin in the runner (`publisher.adcp-e2e.dev`, the runner's alias on the
   stack's non-private subnet) and sends "the seller is deployed in production" to
   the stack's production server (`adcp-server-production`).
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
