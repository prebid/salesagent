# Test architecture

This file is the authoritative guide to writing tests in this project.
**Agents must read this before writing any test code.**

## Contents

- [The harness system (use this)](#the-harness-system-use-this) — environments, capabilities, and multi-transport dispatch
- [Test types](#test-types) — unit, integration, BDD, E2E, and admin suites
- [Factory system (use this)](#factory-system-use-this) — ORM and Pydantic factories, the identity helper, session binding
- [Obligation tests](#obligation-tests) — the rules that bind any test tagged `Covers:`
- [HOWTO: The three steps of a test](#howto-the-three-steps-of-a-test) — one recipe each for setting state, checking a response field, and validating an error
- [Quick reference: Write a new test](#quick-reference-write-a-new-test) — copyable skeletons for integration, unit, and BDD tests
- [Error verification policy](#error-verification-policy) — assert on the wire envelope, not reconstructed exceptions
- [Infrastructure](#infrastructure) — which command starts what

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

A direct call to `_impl` is not a transport and verifies nothing on the wire; `Transport.IMPL`
is legacy and is removed by #1721 — do not write new tests against it.

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
recovery, message.

**The call:** `assert_envelope_shape()` from
`tests/helpers/envelope_assertions.py`, on `result.wire_error_envelope`:

```python
from tests.helpers import assert_envelope_shape

result = env.call_via(transport, **bad_request)
assert result.is_error
assert_envelope_shape(
    result.wire_error_envelope,
    "VALIDATION_ERROR",
    recovery="correctable",
    message_substr="budget must be positive",
)
```

`recovery` is required — it asserts the buyer-facing retry semantics. BDD
steps asserting a rejection that names a request field use
`assert_wire_rejection(ctx, code, recovery=..., field=...)` from
`tests/bdd/steps/_outcome_helpers.py`; step definitions never parse
envelopes themselves.

**The pitfall:** the harness also reconstructs a typed `AdCPError` from the
wire (`result.error`). Asserting on that object — `isinstance(...)`,
`.error_code` — verifies the reconstruction rather than the real JSON
response, and the reconstruction is lossy. Full policy: § Error verification
policy.

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

The test harness reconstructs `AdCPError` subclasses from wire responses so tests can
use `isinstance()` and `.error_code`. This reconstruction is **lossy** — for example,
`AdCPAuthenticationError` and `AdCPAuthorizationError` both map to `AUTH_REQUIRED` on
the wire, so reconstruction always produces `AdCPAuthenticationError`. Tests that assert
on reconstructed exceptions verify the reconstruction layer, not the actual wire envelope.

**New error-path tests MUST assert on the wire error envelope** as the primary authority.
The wire envelope is the buyer-facing contract — it is what the AdCP spec defines and
what storyboard runners parse.

### How to assert on the wire envelope

Use `assert_envelope_shape()` from `tests/helpers/envelope_assertions.py` on
`result.wire_error_envelope` — the recipe, with a worked example, is in
§ "How to validate an error response".

### What to assert

`recovery` is a **required** keyword argument — every call asserts the buyer-facing
retry semantics (`correctable` / `transient` / `terminal`). Omitting it is a
`TypeError`, not a soft default: silent drift between a typed exception's recovery
and the wire is exactly the regression this helper exists to catch.

| Layer | What to check | How |
|-------|--------------|-----|
| Wire structure | Two-layer envelope structure | `assert_envelope_shape(envelope, code, recovery="correctable")` |
| HTTP status | REST status code | `assert result.envelope["status_code"] == 400` |
| Error code | Machine-readable wire code | `assert_envelope_shape(envelope, "VALIDATION_ERROR", recovery="correctable")` |
| Message | Human-readable content | `assert_envelope_shape(envelope, code, recovery=..., message_substr="...")` |
| Recovery | Buyer retry semantics | `assert_envelope_shape(envelope, code, recovery="correctable")` |

Assertions on the reconstructed exception — `isinstance(error, ...)`,
`error.error_code == ...`, `error.recovery == ...` — verify the
reconstruction layer, not the wire. Never write one. Tests that predate this
policy still assert on reconstructed exceptions; migrate them to the envelope
when touched.

### `TransportResult.wire_error_envelope`

`TransportResult` exposes `wire_error_envelope: dict | None` — the two-layer
error envelope captured at the transport boundary, from the transport's real
wire bytes. Populated on error; `None` on success. This is the canonical
field for error verification.

**Authenticity per transport (matters for what regressions the field catches):**

| Transport | `wire_error_envelope` source                                          | Catches a regression in...                                |
|-----------|-----------------------------------------------------------------------|-----------------------------------------------------------|
| REST      | HTTP response body (real wire)                                        | exception handler + envelope serialization + HTTP framing |
| MCP       | JSON string in `ToolError`, else the real envelope stored on the reconstructed error by `_envelope_to_adcp_error` — never synthesized | `_handle_tool_exception` + `build_two_layer_error_envelope` |
| A2A       | Failed Task's artifact DataPart, stored by `_envelope_to_adcp_error` | `on_message_send` + `_serialize_for_a2a` + envelope build |

Every transport captures the envelope that actually came back; none
synthesizes one. A synthesized value would either be redundant or hide a lost
capture — an assertion on it would verify the harness rebuilding an envelope
from the exception it had just caught, which passes whether or not production
emitted anything. The invariant that MCP stores its real envelope rather than
synthesizing one is enforced by
`tests/unit/test_harness_mcp_never_synthesizes.py`.

`result.error` (the reconstructed exception) exists for tests that predate
this policy. Reconstruction is lossy — assert on `result.error_envelope()`,
which returns the captured wire envelope and raises when there is none,
rather than letting a dead wire path pass on a rebuilt envelope;
`error_envelope_or_none()` is the sibling for callers that branch on envelope
presence as control flow (an MCP dispatch can fail with a `ToolError` that is
genuinely not an AdCP envelope).

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
`_run_mcp_client`** (which store the wire); `None` on error. Legacy
`_run_mcp_wrapper` and the direct `*_raw` wrappers do not store it, so
`wire_response` is `None` there too. `CreativeFormatsEnv` and
`CreativeListEnv` read it. Read it through `result.require_wire()`, which
raises on a success with no stored body instead of falling through to a
harness-side reconstruction. Use it to assert the **actual serialized
structure** a buyer receives (for example, the v3.1 `format_id`
`{agent_url, id}` federation contract on `list_creative_formats`) rather than
the typed `payload`, whose fields are already coerced to their declared types
and so cannot catch a serialization regression.

**Authenticity per transport:**

| Transport | `wire_response` source | Notes |
|-----------|------------------------|-------|
| REST | HTTP JSON body (`response.json()`) | Real wire; equals `raw_response.json()`. |
| MCP  | `ToolResult.structured_content` (real wire) | Stored by `_run_mcp_client`. |
| A2A  | Full artifact DataPart (real wire), or a `model_dump()` proxy for envs blocked on a documented real-dispatch bug | Stored by `_run_a2a_handler` before the `message`/`success` strip (real wire), so top-level envelope fields are present. Envs that instead call the `_raw()` wrapper directly (e.g. `CreativeSyncEnv`) set `envelope["wire_response_is_proxy"] = True` — check that flag before treating `wire_response` as a genuine A2A-framing check. |
| IMPL | `None` (no wire by definition) | Serialize the typed `payload` (`model_dump(mode="json")`) — exercises the production serializer, not transport framing. |

See
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
