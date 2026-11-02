# Test architecture

This file is the authoritative guide to writing tests in this project.
**Agents must read this before writing any test code.**

## THE AUTHORITY ORDER — read this before changing any expectation

**A test is truthful to the SPEC, not to production.** When a test and production
disagree, the question is never "which one do I edit to get green" — it is "which one
does the spec say is right". Resolve it in this order, and stop at the first level that
answers:

> **1. the pinned spec  →  2. the storyboard  →  3. the SDK  →  4. production**

The pin is whatever `adcp==<version>` currently ships in
`.venv/.../adcp/_schemas/<spec>/` (see [AdCP spec version](../docs/adcp-spec-version.md)).
Where the spec is silent or ambiguous, the graded storyboard decides; where that is
silent, the SDK; production is the last word only when the three above say nothing.
Production being long-standing, reviewer-approved, or cited in an old ticket does not
move it up this list.

**Corollaries, each of which has been got wrong here:**

- **A generated `.feature` file is EDITABLE and gets corrected in place.** It mirrors
  upstream, but a scenario asserting a code the pinned enum contradicts is simply wrong,
  and "wait for an upstream regen" is not a plan — there may never be one. Fix it now,
  in this repo, and mirror the diff upstream if you can. Do not ledger a scenario as a
  "spec-production gap" when the SPEC agrees with production and only the scenario is
  stale.
- **A test that passes because it encodes production's bug is a defect, not coverage.**
  Four UC-003 scenarios asserted `CREATIVE_REJECTED` for three conditions the enum codes
  differently; they passed for years because production emitted the same wrong code.
- **Reverting a spec-correct change to restore consistency with a spec-incorrect one
  makes the tree worse.** Half-compliant beats uniformly non-compliant. Finish the
  change instead.
- **Cite the level you used.** "The enum says X" is an argument. "#1417 decided X",
  "production emits X", "the scenario expects X" are not — those are levels 4 and below,
  and they are what you are checking, not what you are checking against.

## WHICH KIND OF TEST — decide this before picking a harness

There is a preference order, and it is not a matter of taste. Answer in order and stop at the
first that fits:

> **1. BDD  →  2. integration, only if BDD cannot  →  3. unit, only for a pure function**

**BDD is the default and covers behavior.** A behavior is anything a buyer can observe: a
response field, an error code, a status, a webhook. One scenario runs on a2a, mcp, rest and
e2e_rest against real production and a real database, so it grades the seller rather than a
seat inside it.

**Integration is for a behavior BDD genuinely cannot reach** — and the bar is two-part, both
halves required:

1. BDD cannot express it (no wire, no transport, or the subject is a repository or migration
   contract rather than a buyer-visible outcome); **and**
2. **it is a testable behavior at all.** A test that patches one layer in order to watch
   another layer's defensive code run is testing the patch. If the state the layer reads
   cannot occur, the answer is to make it unrepresentable in the design, not to fabricate it
   with a mock. That is the single most common reason a test here should not exist.

**Unit is for exactly one thing: a pure function.** Input in, value out, no database, no
transport, no time, no identity. Nothing behavioral. A "unit test" of a controller, a
boundary, or an error path is a behavioral test wearing the wrong suite's clothes, and it is
graded by mocks it set up itself.

### A lacking harness is not a licence to monkey-patch

If the harness cannot express the state a scenario needs, **the harness is the thing to
change** — an env method, a factory, a `realize_e2e` branch. Patching production out to make
a scenario runnable is how a suite comes to grade a seller no deployment runs, and it is
specifically forbidden by rule 1 below: a patch that exists on three transports and not the
fourth makes the transport the variable rather than the constant.

This is not hypothetical. `validate_setup_complete` was in `MediaBuyCreateEnv.EXTERNAL_PATCHES`
stubbed to return `None`, while the live e2e_rest server enforced it — one scenario, two
productions. Removing the patch left 96 of 180 UC-002 scenarios failing `CONFIGURATION_ERROR`;
none was a production defect, all were a half-seeded tenant. Seeding the rows the gate
actually grades, all 180 pass and the gate is real on every transport.

The only defensible patch target is a system **we do not own and a test cannot call** — the ad
server, an external creative agent, Slack, an LLM. Our own code is never one, and neither is
our own database.

**Related:** root [CLAUDE.md](../CLAUDE.md) pattern 11 — a production path that branches on a
test flag is the same defect one layer down, and is build-failing.

## Contents

- [Which kind of test](#which-kind-of-test--decide-this-before-picking-a-harness) — BDD, then integration only if it cannot, then unit only for pure functions
- [The harness system (use this)](#the-harness-system-use-this) — environments, capabilities, and multi-transport dispatch
- [Test types](#test-types) — unit, integration, BDD, E2E, and admin suites
- [Factory system (use this)](#factory-system-use-this) — ORM and Pydantic factories, the identity helper, session binding
- [Obligation tests](#obligation-tests) — the rules that bind any test tagged `Covers:`
- [Storyboard conformance](#storyboard-conformance) — the measured AdCP grading run, its ledger, and its guards
- [HOWTO: The three steps of a test](#howto-the-three-steps-of-a-test) — one recipe each for setting state, checking a response field, and validating an error
- [Anti-patterns in this codebase](#anti-patterns-in-this-codebase) — the shapes to recognize and never copy
- [Quick reference: Write a new test](#quick-reference-write-a-new-test) — copyable skeletons for integration, unit, and BDD tests
- [Error verification policy](#error-verification-policy) — assert on the wire envelope, not reconstructed exceptions
- [Infrastructure](#infrastructure) — which command starts what

Each subdirectory carries its own pointer, injected when you edit a file there. They say what
belongs in that directory and point back here for the rules; none of them restates a rule,
because a restated rule drifts from the one it copied:

| Directory | What its pointer covers |
|---|---|
| [unit](unit/CLAUDE.md) | pure functions only, and the narrow structural-guard exception |
| [integration](integration/CLAUDE.md) | the two-part bar for landing here at all, and the fixture debt |
| [bdd](bdd/CLAUDE.md) · [bdd/steps](bdd/steps/CLAUDE.md) | helper-per-situation; what a step author gets wrong, and why a Given never mocks |
| [harness](harness/CLAUDE.md) | what `EXTERNAL_PATCHES` may target, and what an env owns instead |
| [factories](factories/CLAUDE.md) | one factory per model, one seeder for multi-row facts, and the 15 missing |

## The harness system (use this)

The test harness (`tests/harness/`) is the central testing abstraction. It manages mocks,
identity, database sessions, and multi-transport dispatch. **All new tests must use it.**

### How it works

```python
from tests.harness import DeliveryPollEnv

with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
    # env auto-patches external dependencies, creates identity, binds DB session to factories
    tenant = TenantFactory(tenant_id="t1")
    principal = PrincipalFactory(tenant=tenant, principal_id="p1")
    buy = MediaBuyFactory(tenant=tenant, principal=principal)

    env.set_adapter_response(buy.media_buy_id, impressions=5000)
    result = env.call_impl(media_buy_ids=[buy.media_buy_id])

    assert result.deliveries[0].impressions == 5000
```

### Environment hierarchy

| Class | Mode | Domain | File |
|-------|------|--------|------|
| `BaseTestEnv` | Unit (mocked DB) | Base class | `tests/harness/_base.py` |
| `IntegrationEnv` | Integration (real DB) | Base class | `tests/harness/_base.py` |
| `DeliveryPollEnv` | Integration | Delivery metrics | `tests/harness/delivery_poll.py` |
| `DeliveryPollEnvUnit` | Unit | Delivery metrics | `tests/harness/delivery_poll_unit.py` |
| `WebhookEnv` | Integration | Webhook delivery | `tests/harness/delivery_webhook.py` |
| `CircuitBreakerEnv` | Integration | Circuit breaker | `tests/harness/delivery_circuit_breaker.py` |
| `CreativeSyncEnv` | Integration | Creative sync | `tests/harness/creative_sync.py` |
| `CreativeFormatsEnv` | Integration | Format discovery | `tests/harness/creative_formats.py` |
| `CreativeListEnv` | Integration | Creative listing | `tests/harness/creative_list.py` |
| `ProductEnv` | Integration | Product catalog | `tests/harness/product.py` |
| `ProductEnvUnit` | Unit | Product catalog | `tests/harness/product_unit.py` |
| `MediaBuyUpdateEnv` | Unit | Media buy updates | `tests/harness/media_buy_update.py` |

### Key capabilities

- **`EXTERNAL_PATCHES`**: Dict of `{name: patch_target}` — auto-started as `unittest.mock.patch` on `__enter__`
- **`ASYNC_PATCHES`**: Set of names that need `AsyncMock` instead of `MagicMock`
- **`env.mock[name]`**: Access active mocks by name
- **`env.call_impl()`**: Call the `_impl` function directly
- **`env.call_a2a()`**: Call through the A2A transport wrapper
- **`env.call_mcp()`**: Call through the MCP transport wrapper
- **`env.get_rest_client()`**: Get a Starlette `TestClient` for REST calls
- **`env.call_via(transport, **kwargs)`**: Dispatch through any transport

### Transport dispatching

There are three transports: **A2A, MCP, and REST**. Every `_impl` function is
wrapped by all three, each dispatches in-process, and each has an `E2E_*`
variant that dispatches the same call over real HTTP
(`tests/harness/transport.py`). Tests verify behavior across the transports
that cover them:

```python
from tests.harness.transport import Transport

for transport in [Transport.A2A, Transport.MCP, Transport.REST]:
    result = env.call_via(transport, media_buy_ids=[buy.media_buy_id])
    assert result.is_success
```

BDD parametrizes exactly these three, plus `e2e_rest` when the in-network
stack enables it — a scenario verifies AdCP wire conformance, so it must run
where there is a wire.

**There is no IMPL transport.** `Transport.IMPL`, `ImplDispatcher` and
`TransportResult.synthesized_error_envelope` are DELETED — a direct in-process
call is not a transport, and modelling it as one gave every "assert on the wire"
rule an escape hatch. Every `Transport` member dispatches over a real wire, so a
`TransportResult` can only come from one.

Calling `_impl` directly is still correct where the obligation is about what
`_impl` returns or raises: use `env.call_impl(...)` and assert against the
returned DTO or, with `pytest.raises`, the error class. That is the oracle —
`_impl` has no other output form (its return types are annotated and
the serialization rules in `ruff-serialization.toml` and
`.ast-grep/rules/serialize-only-at-the-edges.yml` keep serialization at the edges).
What is gone is the pretence that doing so is a transport.

### Symbol index

Check `.agent-index/harness/` for quick lookup of all harness classes and methods:

- `base.pyi` — BaseTestEnv, IntegrationEnv interfaces
- `transport.pyi` — Transport enum, TransportResult, dispatchers
- `envs.pyi` — Domain-specific env classes with methods

## Test types

### Unit tests (`tests/unit/`)

Fast, isolated. No database. External deps mocked via harness `BaseTestEnv` or direct `unittest.mock`.

```bash
make quality          # Runs unit tests as part of quality gates
tox -e unit           # Unit tests only
```

### Integration tests (`tests/integration/`)

Real PostgreSQL. Use `IntegrationEnv` subclasses or the `integration_db` fixture.
Factory-boy factories create test data — the harness binds sessions automatically.

```bash
tox -e integration
scripts/run-test.sh tests/integration/test_foo.py -x   # Single test with auto-DB
```

### BDD tests (`tests/bdd/`)

Behavioral tests from AdCP requirements. Feature files are auto-generated from the spec.
Step definitions are organized in two layers:

- **`tests/bdd/steps/generic/`** — Reusable steps (auth, entity setup, assertions)
- **`tests/bdd/steps/domain/`** — Use-case-specific steps (delivery, creative formats)

Every BDD scenario is automatically parametrized across the wire transports (A2A, MCP, REST —
plus `e2e_rest` in-network) unless tagged with a specific transport. The `ctx`
fixture is a mutable dict shared across steps, with `ctx["env"]` holding the
harness environment.

```bash
tox -e bdd
```

### BDD authoring discipline (the five rules)

Every new or modified scenario and step definition follows these. Most are guard-enforced;
all of them have caused real defects when skipped.

1. **The scenario is transport-independent by construction.** Given/When/Then never
   mention a transport and never touch wire shapes. The harness parametrizes each
   scenario over a2a/mcp/rest (+e2e); transport-specific logic lives ONLY in the env
   (`env.call_via` → `TransportResult`). A `When` that says "calls the X MCP tool" is a
   defect unless the scenario grades a spec-cited transport-specific behavior (cite it
   in a comment). Every run is a real wire run — assertions got
   stricter for free.

2. **Setup goes through env methods; dispatch goes through `dispatch_request`.**
   Givens realize intent via env-owned methods (on e2e that means seeding the live
   server's DB, or driving the real API — e.g. create_media_buy over HTTP — via
   `realize_e2e`, `tests/harness/_realize.py`). Steps never hand-stash wire data into
   `ctx`; `dispatch_request` (`tests/bdd/steps/generic/_dispatch.py`) is the ONE place
   that writes `ctx["result"]` / `ctx["wire_response"]` / `ctx["wire_error_envelope"]`.

3. **Assert on the wire, through the guarded helpers — never hand-rolled.**
   - errors: `ctx["result"].assert_wire_error(code, ...)`
     (`tests/harness/transport.py`) — recovery defaults to the pinned AdCP enum, so
     the assertion is non-vacuous without per-scenario duplication;
   - success: `wire_field(ctx, "x")` / `wire_dict(ctx)`
     (`tests/bdd/steps/_outcome_helpers.py`) — these raise loudly when the env didn't
     stash the wire. There is NO `model_dump()` fallback: a serializer round-trip
     proves model self-consistency, not what the buyer received. The fallback used
     to exist for an explicit `Transport.IMPL` and went with it, so the
     "is the transport unset, or deliberately no-wire?" question every caller had
     to answer is gone too (GH #1744 was the narrower fix for the same hazard).
     Contract pinned by `tests/harness/test_outcome_helpers_wire_contract.py`
     and `tests/harness/test_wire_bytes_required.py`.

4. **Assertions compare values, not existence.** `assert status` is green for ANY
   status. `assert actual == expected` or it isn't a test. A Then whose text claims a
   value the step doesn't pin is a defect — and a Then you cannot ground in a spec
   citation is a defect to REPORT, not a step to improvise. Guards catch most of this
   (`test_architecture_bdd_no_trivial_assertions` & friends); `/inspect-bdd-steps`
   does the deeper two-pass audit of whether a Then asserts what its text claims.

5. **Prove it executes.** The recurring failure mode: steps written, CI green,
   scenario never ran — it auto-xfailed at fixture setup (missing step defs and
   unwired harness routes xfail silently by design). After touching step files, run
   the touched slice with `-rxX` and READ the output: sub-second wall time and
   "No harness wired" / `StepDefinitionNotFoundError` reasons are the tells. A real
   run measures this for you: `tests/bdd/scenario_liveness.py` emits
   `test-results/bdd_scenario_liveness.json` with `steps_bound` / `harness_wired` /
   `ledgered` per storyboard-tagged scenario, and
   `scripts/audit/scenario_liveness_join.py` joins it against the `ENV_ROUTES`
   registry and the conformance ledger, so a tagged-but-never-executing scenario
   reads as a gap instead of as coverage. Ground scenarios in
   the pinned spec (`docs/adcp-spec-version.md`), cite version+file on any divergence,
   and remember: xfail ledgers and guard allowlists only shrink. The e2e_rest xfail
   ROUTES in the bdd conftest are pinned too (`EXPECTED_XFAIL_ROUTES` in
   `tests/unit/test_architecture_e2e_rest_escape_hatches.py`) — changing a route
   requires updating the pin in the same change, with a justification.

tl;dr: the scenario says WHAT, the env says HOW per transport, the helpers say whether
it's really ON THE WIRE — and the dormancy check says whether it ran at all.

### E2E tests (`tests/e2e/`)

Full Docker stack (app + nginx + Postgres). No mocking.

```bash
./run_all_tests.sh    # Full suite including e2e
```

### Admin tests (`tests/admin/`)

Admin UI tests against the Docker stack.

## Factory system (use this)

**All test data must be created via factory-boy factories in `tests/factories/`.**

### ORM factories (for database entities)

```python
from tests.factories import TenantFactory, PrincipalFactory, MediaBuyFactory

tenant = TenantFactory(tenant_id="t1")                    # Creates Tenant ORM model in DB
principal = PrincipalFactory(tenant=tenant)                # Auto-links to tenant
buy = MediaBuyFactory(tenant=tenant, principal=principal)  # Full media buy with defaults
```

### Pydantic factories (for non-ORM models)

```python
from tests.factories import FormatFactory, FormatIdFactory

fmt = FormatFactory(format_id="display_300x250_image")     # Format Pydantic model
fid = FormatIdFactory(id="display_300x250_image")          # FormatId model
```

### Identity helper

```python
identity = PrincipalFactory.make_identity(tenant_id="t1", principal_id="p1")
```

Single source of truth for `ResolvedIdentity` in tests — never construct it manually.

### Session binding

You do not manage sessions. `IntegrationEnv.__enter__()` creates a session and binds it
to all factories automatically. Use factories inside a `with env:` block.

## Obligation tests

Tests tagged with `Covers: <obligation-id>` verify behavioral contracts.
`docs/test-obligations/` holds curated inputs only
(`storyboard-issue-map.yaml`, `storyboard-wireability.yaml`,
`bdd-traceability.yaml`); there is no committed obligation document to tag
against, so do not add new `Covers:` tags. The following rules bind any test
that carries one.

### Five hard rules

1. MUST import from `src.*`
2. MUST call a production function (not only import it)
3. MUST assert on production output
4. MUST use factory-boy factories for data setup
5. MUST NOT assert only on mock return values

Rule 4 was formerly "MUST have a `Covers:` tag". It went with the generated
obligation documents and the guard that graded it; the rules renumbered.

## Storyboard conformance

What grades protocol behavior against AdCP is not a generated obligation report but a
MEASURED run of the real `@adcp/sdk` storyboard runner (`tests/storyboard/`), surfaced
as ordinary parametrized pytest tests — one per `(protocol, track, storyboard_id,
step_id)` (`tests/storyboard/test_storyboard_conformance.py`). The runner executes once
per PROTOCOL: the agent serves both MCP and A2A, so grading only MCP would let the A2A
surface drift non-conformant with CI green.

```bash
tox -e storyboard    # needs the full Docker stack + tests/storyboard/runner/ npm deps
```

- **Ledger, not skips.** Only a genuine check FAILURE is ledgered, in
  `tests/storyboard/known_failures.txt` (+ `tests/storyboard/conftest.py`) — the same
  discipline as `tests/bdd/e2e_rest_known_failures.txt`. Runner-reported skips
  (`missing_test_controller`, `missing_tool`, `prerequisite_failed`) become native
  `pytest.skip()` calls and never become ledger entries.
- **Curated inputs, not generated reports.** `docs/test-obligations/` holds the three
  judgements a program cannot derive: `storyboard-issue-map.yaml` (which GitHub issue
  tracks a gap), `storyboard-wireability.yaml` (per-`(storyboard, step)` triage of
  whether it can be wired end-to-end), and `bdd-traceability.yaml`.
- **Guards.** `ls tests/unit/test_architecture_storyboard_*.py` is the current list.
  Binding (`test_architecture_storyboard_binding.py`) requires a `@storyboard-v3.1` tag to cite a binding that
  resolves at the pin; wireability and issue-map require every gradable step to carry a
  triage decision; check-index liveness
  (`test_architecture_storyboard_check_index_liveness.py`) separates
  "claimed by a scenario" from "graded by a LIVE scenario", so a tagged scenario with
  zero bound steps stops counting as coverage; ledger guards pin the single owner of the
  check-id grammar in `scripts/audit/ledger.py`.

## HOWTO: The three steps of a test

Every behavioral test performs the same three steps: put the system in a
starting state (Given), run production through a transport (When), and check
what the run produced (Then). Each step has exactly one recipe.

### How to set state in a Given step

**Goal:** starting state lives in two places — database entities, and the
collaborators the env manages (adapter, format registry, HTTP origins).
Configure both through the env.

**The call:** open the domain env, create entities with the factory-boy
factories from `tests/factories`, and configure collaborators with the env's
`set_*` methods:

```python
from tests.factories import TenantFactory, PrincipalFactory, MediaBuyFactory
from tests.harness import DeliveryPollEnv

with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
    tenant = TenantFactory(tenant_id="t1")
    principal = PrincipalFactory(tenant=tenant, principal_id="p1")
    buy = MediaBuyFactory(tenant=tenant, principal=principal)
    env.set_adapter_response(buy.media_buy_id, impressions=5000)
```

`IntegrationEnv.__enter__()` opens the session and binds it to every factory,
so the test manages no session at all — no `get_db_session()`, no
`session.add()`. Each domain env exposes typed setup methods:
`set_adapter_response(...)` / `set_adapter_error(exc)` (delivery),
`set_registry_formats([...])` (`CreativeFormatsEnv`), `set_http_status(...)` /
`set_http_sequence([...])` (webhook local origin), `set_policy_blocked(...)` /
`set_policy_approved()` (`ProductEnv`). `.agent-index/harness/envs.pyi` lists
the full set per env; anything else the env patches is reachable as
`env.mock[name]` — never set up `mock.patch` yourself for a dependency the
env already manages. Identity comes from
`PrincipalFactory.make_identity(tenant_id=..., principal_id=...)`, the single
source of truth for `ResolvedIdentity`.

**The pitfall:** import factories from `tests/factories`, never from
`tests/fixtures` — the dict-based namesakes there return plain dicts, not ORM
models. The structural guard
`tests/unit/test_architecture_repository_pattern.py` fails new
`get_db_session()` / `session.add()` calls in test bodies at
`make quality`, and its allowlist only shrinks; tests that predate the
harness are legacy — do not copy their setup.

### How to check a field in the response

**Goal:** assert the *value* of a field on the response the `When` step
produced, through the transport-independent accessors on `TransportResult`.

**The call:**

```python
result = env.call_via(transport, media_buy_ids=[buy.media_buy_id])
assert result.is_success
assert result.payload.deliveries[0].impressions == 5000
```

Two accessors, two jobs. `result.payload` is the typed response model — the
default for checking values. `result.require_wire()` is the serialized body
the buyer actually received — required when the assertion is about
serialization itself (field names, key presence or absence, wire types),
because `payload` fields are already coerced to their declared types and
cannot catch a serialization regression; it raises on a success with no
stored body instead of falling back to re-serializing the payload. In BDD
steps the same pair is `require_payload(ctx)` and `wire_field(ctx, "field")` /
`wire_dict(ctx)` from `tests/bdd/steps/_outcome_helpers.py`.

**The pitfall — a Then that checks the Given.** The value you check must be
one the `When` produced, after making the full trip Given → production →
response. Three reads that look like assertions but re-read the setup
instead:

- reading the factory object or DB row the Given wrote
  (`assert buy.status == "active"`) — passes even when the When does nothing;
- reading the mock the Given configured
  (`env.mock["adapter"].return_value...`) — hard rule 5: the assertion and
  the setup are the same object;
- recomputing the expected value from `ctx` or env state the Given stored,
  rather than reading the dispatched result through `require_payload(ctx)` or
  `wire_field(ctx, ...)` — the same circular check, one step removed.

The test for an assertion that cannot fail: if the When step were deleted,
could the Then still compute its actual value? Only the `TransportResult`
returned by `call_via` — reached in BDD through `require_payload(ctx)` and
`wire_field(ctx, ...)` — came out of the run. Set a distinctive value in the Given (`impressions=5000`, not a factory
default) and read it back off the result — then the assertion can only pass
if production carried the value through.

### How to validate an error response

**Goal:** assert on the real JSON error envelope the buyer receives — code,
recovery, and the structured positions that name what was rejected.

**The call:** `assert_wire_error()` on the `TransportResult` — the single
harness-provided way to verify an error on the wire. It defaults `recovery`
from the pinned AdCP error-code table, so the assertion is non-vacuous without
per-scenario duplication:

```python
result = env.call_via(transport, **bad_request)
assert result.is_error
result.assert_wire_error(
    "VALIDATION_ERROR",
    recovery="correctable",
    field="total_budget.amount",
)
```

`assert_envelope_shape()` from `tests/helpers/envelope_assertions.py` is the
same check one layer down; reach for it only where there is no
`TransportResult` — a bare envelope dict, or an `AdCPToolError`. BDD steps
asserting a rejection that names a request field use
`assert_wire_rejection(ctx, code, recovery=..., field=...)` from
`tests/bdd/steps/_outcome_helpers.py`; step definitions never parse envelopes
themselves.

**The pitfall:** `result.error` is the exception the transport actually raised,
not a typed production error — a `WireError` carrying the envelope verbatim on
REST and A2A, the raw `ToolError` on MCP. Asserting on that object
(`isinstance(...)`, `.error_code`) does not verify the wire, and on a wire
transport it does not even pass. Full policy: § Error verification policy.

## Anti-patterns in this codebase

The recipes above say what to do; these are the shapes to recognize and never copy.
They exist in older code but **MUST NOT be used in new tests**. Structural guards
(`test_architecture_repository_pattern.py`) catch new violations.

### `session.add()` in test bodies

```python
# WRONG — exists in tests/conftest_db.py and many integration tests
with get_db_session() as session:
    tenant = Tenant(tenant_id="test", name="Test", subdomain="test", ...)
    session.add(tenant)
    session.commit()

# CORRECT — use factories inside harness
with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
    tenant = TenantFactory(tenant_id="t1")
```

### `get_db_session()` in test bodies

```python
# WRONG — exists in 130+ test files
from src.core.database.database_session import get_db_session
with get_db_session() as session:
    result = session.scalars(select(MediaBuy).filter_by(...)).first()

# CORRECT — use harness or integration_db fixture
# The harness manages the session; factories commit via the bound session
```

### Dict-based factories from `tests/fixtures/`

```python
# WRONG — legacy dict factories, returns plain dicts not ORM models
from tests.fixtures import TenantFactory  # This is the WRONG TenantFactory

# CORRECT — factory-boy ORM factories
from tests.factories import TenantFactory  # This is the RIGHT TenantFactory
```

### Raw dict construction instead of factories

```python
# WRONG
tenant_data = {"tenant_id": "test", "name": "Test", "subdomain": "test", ...}

# CORRECT
tenant = TenantFactory(tenant_id="test")
```

### Manual mock setup instead of harness

```python
# WRONG — 15 lines of mock.patch scattered in test body
with patch("src.core.tools.delivery._get_adapter") as mock_adapter:
    with patch("src.core.database.database_session.get_db_session") as mock_db:
        mock_adapter.return_value.get_delivery_metrics.return_value = {...}
        ...

# CORRECT — harness manages all patches
with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
    env.set_adapter_response(buy_id, impressions=5000)
    result = env.call_impl(media_buy_ids=[buy_id])
```

### Why these anti-patterns exist

These are **pre-existing debt**, not established patterns. They predate the harness system
and factory-boy migration. They are tracked in guard allowlists with `FIXME` comments and
shrink over time. The structural guard `test_architecture_repository_pattern.py` has an
allowlist of files permitted to use `get_db_session()` — **new files are never added**.

**When you see existing tests using these patterns: do not copy them.** Use the harness
and factories regardless of what the surrounding tests do.

## Quick reference: Write a new test

### Integration test with harness

```python
import pytest
from tests.factories import TenantFactory, PrincipalFactory, MediaBuyFactory

@pytest.mark.requires_db
class TestDeliveryReturnsMetrics:
    """Delivery poll returns adapter metrics for active media buys."""

    def test_returns_impressions(self, integration_db):
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            buy = MediaBuyFactory(tenant=tenant, principal=principal)

            env.set_adapter_response(buy.media_buy_id, impressions=5000)
            result = env.call_impl(media_buy_ids=[buy.media_buy_id])

            assert result.deliveries[0].impressions == 5000
```

### Unit test (no DB)

```python
class TestFormatResolution:
    def test_unknown_format_raises_not_found(self):
        from tests.harness import CreativeFormatsEnv
        from src.core.exceptions import AdCPNotFoundError

        with CreativeFormatsEnv() as env:
            env.mock["registry"].get_format.return_value = None
            with pytest.raises(AdCPNotFoundError):
                get_format("nonexistent_format")
```

### BDD step definition

```python
from tests.bdd.steps._outcome_helpers import wire_field

@then(parsers.parse('the response contains {count:d} formats'))
def then_response_has_formats(ctx, count):
    assert len(wire_field(ctx, "formats")) == count
```

## Error verification policy

### Principle: Assert on the wire envelope, not reconstructed exceptions

The harness NO LONGER reconstructs `AdCPSalesAgentError` subclasses from wire responses. It used
to, so tests could write `isinstance()` and `.error_code` against a wire error — and
that reconstruction was lossy: it covered 20 of 43 classes, and because
`AdCPAuthenticationError` and `AdCPAuthorizationError` share a wire code, it always
produced the first. A test asserting on the rebuilt object graded the reconstruction,
not the wire.

The map is deleted (`salesagent-3dawm.15`). `result.error` now carries what the
transport actually raised: a `WireError` holding the envelope VERBATIM on REST and A2A
(deliberately not an `AdCPSalesAgentError` subclass, so `isinstance` against a production class
fails loudly instead of quietly passing), and the raw `ToolError` on MCP.

**New error-path tests MUST assert on the wire error envelope** as the primary authority.
The wire envelope is the buyer-facing contract — it is what the AdCP spec defines and
what storyboard runners parse.

### How to assert on the wire envelope

Use `result.assert_wire_error(code, recovery=...)` where you hold a `TransportResult`,
and `assert_envelope_shape()` from `tests/helpers/envelope_assertions.py` only for a
bare envelope or an `AdCPToolError` where no `TransportResult` exists — the recipe,
with a worked example, is in § "How to validate an error response". The method form
wraps the same envelope check and defaults `recovery` from the pinned AdCP error-code
enum, so per-scenario duplication of the recovery value is unnecessary and the
assertion stays non-vacuous.

### What to assert

`recovery` is a **required** keyword argument on `assert_envelope_shape` — every call
asserts the buyer-facing retry semantics (`correctable` / `transient` / `terminal`).
Omitting it is a `TypeError`, not a soft default: silent drift between a typed
exception's recovery and the wire is exactly the regression this helper exists to catch.

| Layer | What to check | How |
|-------|--------------|-----|
| Wire structure | Two-layer envelope structure | `result.assert_wire_error(code, recovery="correctable")` |
| HTTP status | REST status code | `assert result.envelope["status_code"] == 400` |
| Error code | Machine-readable wire code | `result.assert_wire_error("VALIDATION_ERROR", recovery="correctable")` |
| Recovery | Buyer retry semantics | `result.assert_wire_error(code, recovery="correctable")` |
| Field / details | WHICH field was rejected, and structured specifics | `result.assert_wire_error(code, recovery=..., field="start_time")`, or `result.wire_error_details(code)` for non-equality oracles |

**Do not assert the buyer-facing message.** It is a function of the error CODE through
`CODE_TABLE` (`src/core/errors/codes.py`), so asserting the code and the sentence checks
the table against itself. `assert_wire_error` has no `message_substr` parameter at all;
`assert_envelope_shape` still accepts one and documents it as the weakest oracle it
offers. Assert the code, the recovery, the field, the details, and the issues instead.

### What NOT to assert on (in new error tests)

- `isinstance(result.error, AdCPValidationError)` — cannot pass on a wire transport at
  all now (`result.error` is a `WireError`), so it is a broken assertion rather than a
  weak one
- `result.error.error_code == "VALIDATION_ERROR"` — same: reads an attribute the wire
  carrier does not have
- hand-indexing the envelope: `result.wire_error_envelope["errors"][0]["code"]` — go
  through the helpers, which resolve one locator and cannot disagree with each other

There is NO exemption for these. The previous wording made them "acceptable ONLY in
`_impl`-level tests (no wire involved) and in existing tests that predate this policy" —
both halves are now wrong. There is no `_impl` TIER: a direct `env.call_impl(...)` asserts
against the returned DTO or, via `pytest.raises`, the error class, and neither needs
`result.error`. And a grandfather clause for "existing tests" licenses an empty set,
because the reconstruction those tests relied on is gone.

### Migration path

There is nothing left to migrate incrementally. This section used to say ~660 call sites
and ~80 BDD steps asserted on reconstructed exceptions and "continue to work"; both
claims expired when the reconstruction was deleted. Those assertions do not silently
work — on a wire transport they raise, because `result.error` is a `WireError` with no
`.error_code`. The BDD steps were migrated wholesale (`salesagent-3dawm.18`), and the
wire-discipline guard (`tests/unit/test_architecture_bdd_wire_discipline.py`) holds the
line; like every ratchet here, its allowlists only shrink.

What remains is a rule, not a programme: **new error tests** use
`result.assert_wire_error(code, recovery=...)` when you hold a `TransportResult`, and
`assert_envelope_shape(...)` only for a bare envelope or an `AdCPToolError` where no
`TransportResult` exists.

### `TransportResult.wire_error_envelope`

`TransportResult` exposes `wire_error_envelope: dict | None` — the two-layer
error envelope captured at the transport boundary, from the transport's real
wire bytes. Populated on error by every dispatcher — each of them has a real
wire — and `None` on success. This is the canonical field for error verification.

**Authenticity per transport (matters for what regressions the field catches):**

| Transport | `wire_error_envelope` source | Catches a regression in... |
|-----------|------------------------------|---------------------------|
| REST | HTTP response body (real wire) | exception handler + envelope serialization + HTTP framing |
| MCP | JSON string in the raised `ToolError` (real wire) | `RegistryTool.run`'s `AdcpFailure` catch + `AdcpErrorResponse.of` + `to_wire` |
| A2A | Failed Task's artifact DataPart, carried on the raised `WireError` as `.envelope` (`tests/harness/_base.py`) | `on_message_send` + `_dispatch_skill` + `AdcpErrorResponse.of` + `to_wire` |

The synthesized envelope is DELETED. It exposed "what production WOULD emit
at the boundary for this exception", which could not catch a regression in the
production boundary translator: both sides built the same `AdcpErrorResponse`
over the same in-memory exception, so the value moved in lockstep with whatever
the builder produced. A field that cannot fail is not a weak check, it is a
zero-information one — and it was actively harmful, because it stood in for a
missing wire and made a dead transport path indistinguishable from a live one
(salesagent-b2wny is an A2A gap it concealed; on MCP it masked `MediaBuyListEnv`
failing to capture its wire at all, which is the general rule: on a transport that
HAS a wire, a synthesized value is either redundant or it is hiding a lost
capture). Every wire-shape assertion now runs on REST, MCP or A2A, which observe
actual wire bytes. `tests/unit/test_harness_mcp_never_synthesizes.py` pins that MCP
never substitutes.

`result.error` is the raised or captured exception, not a reconstruction: on
REST/A2A it is a `WireError` carrying the envelope verbatim, on MCP the raw
`ToolError`. Assert on the wire, through the helpers above — `assert_wire_error`
for an assertion, or the `error_envelope()` / `error_envelope_or_none()` accessor
pair when a test needs the envelope itself. `error_envelope()` RAISES when no
envelope was captured, rather than letting a dead wire path pass on a rebuilt
shape; `error_envelope_or_none()` is the sibling for callers that branch on
envelope-presence as control flow (an MCP dispatch can fail with a `ToolError`
that is genuinely not an AdCP envelope).

### `TransportResult.has_wire` — declared, never defaulted

`has_wire` is **required and keyword-only**. A default turns omission into a
silent claim — "this transport has no wire" — and omission is the one thing
that must not be silent, so leaving it out is a `TypeError`.

It is declared **at each site that constructs a result, not per dispatcher
class**. A wire dispatcher legitimately builds results for requests that were
never sent: a missing-config guard, or a catch-all firing before anything was
sent. Those are `False` even on REST. The branch where a 2xx arrived and
parsing then threw is `True`, because the bytes crossed the wire.

**`has_wire` governs the success path only — do not branch on it to decide
whether an error envelope exists.** It is `False` on every A2A and MCP error
(a catch-all branch may fire before anything was sent, and cannot tell
which), yet those dispatchers still capture a real envelope when one came
back — branching on `has_wire` would discard it. Read errors through
`error_envelope()` / `error_envelope_or_none()` instead.

### `TransportResult.wire_response` (success-path wire)

`TransportResult` also exposes `wire_response: dict | None` — the **serialized
success-path response body**, the success-path analogue of `wire_error_envelope`.
Populated on success by the REST dispatcher (HTTP body) and by the A2A/MCP
dispatchers **only when the env routes through `_run_a2a_handler` /
`_run_mcp_client`** (which store the wire); `None` on error. Both dispatch paths
that used to bypass the wire are gone: the legacy `_run_mcp_wrapper` is DELETED
(it skipped the FastMCP middleware chain, so an env using it captured no wire
envelope at all; its absence is pinned by
`tests/harness/test_harness_base.py::test_base_env_has_no_run_mcp_wrapper`), and
the direct `*_raw` transport wrappers it dispatched to no longer exist in `src/`
either. Every remaining dispatch path observes real wire bytes.
`CreativeFormatsEnv` and `CreativeListEnv` read it. Read it through `result.require_wire()`, which raises
on a success with no stored body instead of falling through to a harness-side
reconstruction. Use it to assert the **actual serialized structure** a buyer
receives (for example, the v3.1 `format_id` `{agent_url, id}` federation contract
on `list_creative_formats`) rather than the typed `payload`, whose fields are
already coerced to their declared types and so cannot catch a serialization
regression.

**Authenticity per transport:**

| Transport | `wire_response` source | Notes |
|-----------|------------------------|-------|
| REST | HTTP JSON body (`response.json()`) | Real wire; equals `raw_response.json()`. |
| MCP  | `ToolResult.structured_content` (real wire) | Stored by `_run_mcp_client`. |
| A2A  | Full artifact DataPart (real wire) | Stored by `_run_a2a_handler` before the `message`/`success` strip, so top-level envelope fields are present. |

Every transport here observes real wire bytes. See
`tests/integration/test_harness_wire_response.py` (verifies that the field is
real wire, not a payload reconstruction) and
`tests/bdd/steps/domain/uc005_format_id_shape.py` (uses it for the `format_id`
federation contract; reusable by the `roundtrip-from-products` /
`third-party-agent` siblings).

## Infrastructure

| What you need | Command |
|---|---|
| Unit tests only | `make quality` |
| One integration test | `scripts/run-test.sh tests/integration/test_foo.py -x` |
| Full suite (all 5 envs) | `./run_all_tests.sh` |
| BDD only | `tox -e bdd` |
| Entity-scoped | `make test-entity ENTITY=delivery` |

## Reading a Gate's Verdict

A gate answers with an EXIT CODE. Three ways to lose that answer, all observed in one day's work,
all of which report the opposite of the truth.

### Never read a gate through a pipe

```bash
uv run python scripts/audit/compare_payloads.py before after | tail -5   # $? is tail's
```

`$?` is now `tail`'s status, which is 0 whatever the gate said. A gate that correctly REFUSED
reads as a pass — and the mistake looks like diligence, because piping to `tail` is how you keep
output short. Use `${PIPESTATUS[0]}`, or `set -o pipefail`, or just do not pipe.

This is the mirror of the defect these gates were built to remove. We spent a day finding
instruments that printed CLEAN over things they never measured; this one prints a refusal that
nobody reads.

### Never compare across environments

The host/agent-db environment fails ~37% of `bdd_inprocess`; a box run of the same tree fails
~4.5%. Diffing one against the other produces a wall of real differences that are entirely
environment and say nothing about the change. That is not a weak comparison, it is not a
comparison — and it cannot be rescued by tolerance, because the differences are genuine.

BEFORE and AFTER must come from the same environment. If the box is unavailable, take both sides
off-box and say so. Baselines under `test-results/` are BOX numbers; a local run of the same tree
is a different population.

### A green comparison over a dormant subject grades nothing

`compare_payloads` reads a scenario that dispatches nothing as SAME, whatever the seeder does —
its rows are empty on both sides. In a file where most scenarios are xfail-dormant, "the payload
diff is clean" is therefore not a claim about that file.

When the subject is dormant, add a second instrument that does not depend on dispatch: run each
seeder directly against its pre-change self, DB and dispatch stubbed, and compare the produced
dict byte for byte. That is what graded the riskiest sites of the creative-literal migration —
all four of which turned out never to reach the wire at all.

The general rule behind all three: **an instrument's silence is only evidence where the
instrument could have spoken.** State the denominator, and state what the two sides have in
common.
