# Test Architecture

This file is the authoritative guide to writing tests in this project.
**Agents must read this before writing any test code.**

## The Harness System (Use This)

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
- **`env.call_a2a()`**: Call through A2A transport wrapper
- **`env.call_mcp()`**: Call through MCP transport wrapper
- **`env.get_rest_client()`**: Get a Starlette `TestClient` for REST calls
- **`env.call_via(transport, **kwargs)`**: Dispatch through any transport

### Transport dispatching

Every `_impl` function is wrapped by MCP, A2A, and REST transports. Tests should verify
behavior across all transports. The `Transport` enum has four values:

```python
from tests.harness.transport import Transport

for transport in [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST]:
    result = env.call_via(transport, media_buy_ids=[buy.media_buy_id])
    assert result.is_success
```

BDD tests do this automatically via `pytest_generate_tests()` parametrization.

### Symbol index

Check `.agent-index/harness/` for quick lookup of all harness classes and methods:
- `base.pyi` — BaseTestEnv, IntegrationEnv interfaces
- `transport.pyi` — Transport enum, TransportResult, dispatchers
- `envs.pyi` — Domain-specific env classes with methods

## Test Types

### Unit Tests (`tests/unit/`)

Fast, isolated. No database. External deps mocked via harness `BaseTestEnv` or direct `unittest.mock`.

```bash
make quality          # Runs unit tests as part of quality gates
tox -e unit           # Unit tests only
```

### Integration Tests (`tests/integration/`)

Real PostgreSQL. Use `IntegrationEnv` subclasses or the `integration_db` fixture.
Factory-boy factories create test data — the harness binds sessions automatically.

```bash
tox -e integration
scripts/run-test.sh tests/integration/test_foo.py -x   # Single test with auto-DB
```

### BDD Tests (`tests/bdd/`)

Behavioral tests from AdCP requirements. Feature files are auto-generated from spec.
Step definitions are organized in two layers:

- **`tests/bdd/steps/generic/`** — Reusable steps (auth, entity setup, assertions)
- **`tests/bdd/steps/domain/`** — Use-case-specific steps (delivery, creative formats)

Every BDD scenario is automatically parametrized across all 4 transports (IMPL, A2A, MCP, REST)
unless tagged with a specific transport. The `ctx` fixture is a mutable dict shared across steps,
with `ctx["env"]` holding the harness environment.

```bash
tox -e bdd
```

### E2E Tests (`tests/e2e/`)

Full Docker stack (app + nginx + Postgres). No mocking.

```bash
./run_all_tests.sh    # Full suite including e2e
```

### Admin Tests (`tests/admin/`)

Admin UI tests against the Docker stack.

## Factory System (Use This)

**All test data must be created via factory-boy factories in `tests/factories/`.**

### ORM Factories (for database entities)

```python
from tests.factories import TenantFactory, PrincipalFactory, MediaBuyFactory

tenant = TenantFactory(tenant_id="t1")                    # Creates Tenant ORM model in DB
principal = PrincipalFactory(tenant=tenant)                # Auto-links to tenant
buy = MediaBuyFactory(tenant=tenant, principal=principal)  # Full media buy with defaults
```

### Pydantic Factories (for non-ORM models)

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

You do NOT manage sessions. `IntegrationEnv.__enter__()` creates a session and binds it
to all factories automatically. Just use factories inside a `with env:` block.

## Obligation Tests

Tests tagged with `Covers: <obligation-id>` verify behavioral contracts from `docs/test-obligations/`.

### Six hard rules

1. MUST import from `src.*`
2. MUST call a production function (not just import it)
3. MUST assert on production output
4. MUST have `Covers:` tag in docstring
5. MUST use factory-boy factories for data setup
6. MUST NOT be mock-echo only (asserting mock return values)

### Enforced by structural guards

- `test_architecture_obligation_coverage.py` — every behavioral obligation has a test
- `test_architecture_obligation_test_quality.py` — obligation tests actually call production code

## Anti-Patterns in This Codebase

The following patterns exist in older code but **MUST NOT be used in new tests**.
Structural guards (`test_architecture_repository_pattern.py`) catch new violations.

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

## Why these anti-patterns exist

These are **pre-existing debt**, not established patterns. They predate the harness system
and factory-boy migration. They are tracked in guard allowlists with `FIXME` comments and
shrink over time. The structural guard `test_architecture_repository_pattern.py` has an
allowlist of files permitted to use `get_db_session()` — **new files are never added**.

**When you see existing tests using these patterns: do not copy them.** Use the harness
and factories regardless of what the surrounding tests do.

## Quick Reference: Writing a New Test

### Integration test with harness

```python
import pytest
from tests.factories import TenantFactory, PrincipalFactory, MediaBuyFactory

@pytest.mark.requires_db
class TestDeliveryReturnsMetrics:
    """Delivery poll returns adapter metrics for active media buys.

    Covers: UC-004-MAIN-POLL-01
    """

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
@then(parsers.parse('the response contains {count:d} formats'))
def then_response_has_formats(ctx, count):
    env = ctx["env"]
    result = ctx["result"]
    assert len(result.payload.get("formats", [])) == count
```

## Infrastructure

| What you need | Command |
|---|---|
| Unit tests only | `make quality` |
| One integration test | `scripts/run-test.sh tests/integration/test_foo.py -x` |
| Full suite (all 5 envs) | `./run_all_tests.sh` |
| BDD only | `tox -e bdd` |
| Entity-scoped | `make test-entity ENTITY=delivery` |
