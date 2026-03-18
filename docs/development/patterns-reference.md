# Architecture Patterns Reference

This document maps every key pattern to its **canonical implementation file** and identifies **known anti-patterns** that exist as tracked debt. New code must follow the canonical pattern, not the anti-pattern, even when the anti-pattern appears in surrounding code.

> **Why this document exists:** The codebase has two eras — legacy code and current architecture. Most code by volume is legacy. If you pattern-match from surrounding code, you will likely follow a legacy pattern. This document tells you which files represent the target architecture.

These patterns are machine-enforced by 8 review agents (`.claude/agents/review-*.md`) and 14+ structural guard tests (`tests/unit/test_architecture_*.py`).

## 1. Repository Pattern (CP-3)

All database access goes through repository classes. `_impl` functions never contain raw `select()`, `session.scalars()`, `session.add()`, or direct model imports for data access.

**Canonical file:** [`src/core/database/repositories/media_buy.py`](../../src/core/database/repositories/media_buy.py)

```python
class MediaBuyRepository:
    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def get_by_id(self, media_buy_id: str) -> MediaBuy | None:
        return self._session.scalars(
            select(MediaBuy).where(
                MediaBuy.tenant_id == self._tenant_id,
                MediaBuy.media_buy_id == media_buy_id,
            )
        ).first()
```

Key properties:
- Constructor takes `session` and `tenant_id` — tenant scoping is automatic in every query
- Write methods (`create_from_request`, `update_fields`, etc.) add to session but never commit — the UoW handles that
- Returns ORM model instances, not dicts

**Anti-pattern** (exists in codebase, tracked by `FIXME(salesagent-9f2)`):
```python
# WRONG: raw select() inside _impl function
currency_stmt = select(CurrencyLimit).where(
    CurrencyLimit.tenant_id == tenant["tenant_id"],
    CurrencyLimit.currency_code == request_currency,
)
currency_limit = session.scalars(currency_stmt).first()
```

**Adding a new repository:** Create `src/core/database/repositories/your_model.py` following `media_buy.py`, add to `__init__.py`, wire into the appropriate UoW. The `test_architecture_no_raw_select.py` guard catches raw `select()` calls outside repository files.

**Enforced by:** `review-architecture` (CP-3), `review-execution-excellence` (Repository+UoW), `review-layering` (_impl → Repository leaks), `test_architecture_no_raw_select.py`, `test_architecture_repository_pattern.py`

## 2. Unit of Work (UoW)

The UoW manages session lifecycle: creates on entry, commits on clean exit, rolls back on exception.

**Canonical file:** [`src/core/database/repositories/uow.py`](../../src/core/database/repositories/uow.py)

```python
class MediaBuyUoW(BaseUoW):
    media_buys: MediaBuyRepository | None

    def _init_repos(self) -> None:
        assert self._session is not None
        self.media_buys = MediaBuyRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.media_buys = None
```

Usage in `_impl`:
```python
with MediaBuyUoW(tenant["tenant_id"]) as uow:
    media_buy = uow.media_buys.get_by_id(req.media_buy_id)
```

### `uow.session` is deprecated

`BaseUoW.session` emits a `DeprecationWarning` at runtime:

```
uow.session is deprecated — use repository methods instead of raw session access. See salesagent-9f2.
```

If you see `session = uow.session` in existing code, that is tracked debt. If you need data access that no repository method provides, **add a repository method** — don't use the raw session.

**Enforced by:** `review-execution-excellence` (Repository+UoW pattern), `review-layering` (_impl → Repository leaks)

## 3. Structural Guards and Allowlists

AST-scanning tests enforce architecture invariants on every `make quality` run. See [`docs/development/structural-guards.md`](structural-guards.md) for the full inventory.

### Core rules

- **New code that introduces a violation fails CI immediately** — no exceptions
- **Allowlists track pre-existing debt and only shrink** — never add new entries
- **Every allowlisted violation has a `# FIXME(salesagent-xxxx)`** comment linking to a tracked issue
- **Removing a FIXME without fixing the underlying issue is not acceptable** — the FIXME is a contract

Guards use `(file_path, function_name)` tuples in their allowlists, not line numbers. This makes them resilient to line shifts from unrelated changes.

**Enforced by:** `review-architecture` (references all 14 guards), CI (`make quality`)

## 4. Writing Tests — The Test Harness

The project has a **test harness** at [`tests/harness/`](../../tests/harness/) that provides domain-specific test environments. These environments handle mock wiring, identity creation, UoW setup, and multi-transport dispatch so tests focus purely on behavior.

**Base class:** [`tests/harness/_base.py`](../../tests/harness/_base.py) — `BaseTestEnv` (unit) and `IntegrationEnv` (real DB)

### How it works

Each domain has an environment class that subclasses `BaseTestEnv`:

```python
class DeliveryPollEnv(DeliveryPollMixin, BaseTestEnv):
    MODULE = "src.core.tools.media_buy_delivery"
    EXTERNAL_PATCHES = {
        "uow": f"{MODULE}.MediaBuyUoW",
        "principal": f"{MODULE}.get_principal_object",
        "adapter": f"{MODULE}.get_adapter",
        "pricing": f"{MODULE}._get_pricing_options",
        "circuit_open": f"{MODULE}._is_circuit_breaker_open",
    }

    def _configure_mocks(self) -> None: ...   # Wire happy-path defaults
    def add_buy(self, media_buy_id, ...) -> MagicMock: ...  # Fluent data API
    def call_impl(self, **kwargs) -> Any: ...  # Call production _impl
```

**Test using the harness** (from [`tests/unit/test_delivery_poll_behavioral.py`](../../tests/unit/test_delivery_poll_behavioral.py)):

```python
from tests.harness.delivery_poll_unit import DeliveryPollEnv

def test_only_completed_buys_returned(self):
    """Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-02"""
    with DeliveryPollEnv() as env:
        env.add_buy(media_buy_id="mb_completed", start_date=date(2025, 1, 1), end_date=date(2025, 6, 30))
        env.add_buy(media_buy_id="mb_active", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        env.set_adapter_response("mb_completed", impressions=5000, spend=250.0)

        response = env.call_impl(status_filter="completed")

        returned_ids = [d.media_buy_id for d in response.media_buy_deliveries]
        assert returned_ids == ["mb_completed"]
```

No mock wiring. No MagicMock scaffolding. The test is pure behavior.

### Available harness environments

| Environment | Domain | Unit | Integration |
|-------------|--------|------|-------------|
| `ProductEnv` | `_get_products_impl` | `product_unit.py` | `product.py` |
| `DeliveryPollEnv` | `_get_media_buy_delivery_impl` | `delivery_poll_unit.py` | `delivery_poll.py` |
| `CreativeSyncEnv` | Creative sync | — | `creative_sync.py` |
| `CreativeListEnv` | Creative listing | — | `creative_list.py` |
| `CreativeFormatsEnv` | Creative formats | — | `creative_formats.py` |
| `CircuitBreakerEnv` | Delivery circuit breaker | `delivery_circuit_breaker_unit.py` | `delivery_circuit_breaker.py` |
| `WebhookEnv` | Delivery webhooks | `delivery_webhook_unit.py` | `delivery_webhook.py` |

Supporting modules: `_mixins.py` (shared fluent APIs), `_mock_uow.py` (UoW mock builder), `_identity.py` (identity factory), `assertions.py` (shared assertion helpers), `dispatchers.py` (transport dispatch), `transport.py` (Transport enum + TransportResult).

### Multi-transport testing

The harness dispatches the same test through multiple transports:

```python
@pytest.mark.parametrize("transport", [Transport.IMPL, Transport.A2A, Transport.REST])
def test_something(self, integration_db, transport):
    with CreativeSyncEnv() as env:
        result = env.call_via(transport, creatives=[...])
        assert result.is_success
```

### Adding a new harness environment

1. Create `tests/harness/your_domain_unit.py` subclassing `BaseTestEnv`
2. Define `EXTERNAL_PATCHES` — the dependencies to mock
3. Implement `_configure_mocks()` — wire happy-path defaults
4. Implement `call_impl(**kwargs)` — construct request and call production code
5. Add fluent helpers (`add_buy()`, `set_adapter_response()`, etc.)
6. Export from `tests/harness/__init__.py`

### Anti-pattern: rebuilding mock scaffolding per test

```python
# WRONG: 15 lines of MagicMock setup duplicated in every test function
def test_something():
    mock_uow = MagicMock()
    mock_uow.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow.__exit__ = MagicMock(return_value=False)
    mock_uow.media_buys = MagicMock()
    mock_uow.session = MagicMock()
    # ... same 15 lines in the next test
```

This exists in older test files as tracked debt. New tests must use the harness or create a new environment.

### Testing Flask endpoints

For Flask routes, use Flask's test client — not boolean logic reconstruction.

**Canonical file:** [`tests/unit/test_signup_flow_session.py`](../../tests/unit/test_signup_flow_session.py)

```python
from src.admin.app import create_app

app = create_app()
app.config["TESTING"] = True
app.config["SECRET_KEY"] = "test-secret"

with app.test_client() as client:
    response = client.post("/test/auth", data={...})
    assert response.status_code == 302
```

**Anti-pattern:**
```python
# WRONG: reimplementing the gate logic and asserting the boolean
should_abort = not env_test_mode or not tenant_setup_mode
assert should_abort is True  # Tests Python arithmetic, not your endpoint
```

**Enforced by:** `review-testing` (Anti-Pattern 1: Mock Echo, Anti-Pattern 2: Assertion-Free, Anti-Pattern 5: Happy Path Only)

## 5. Factory Fixtures for Integration Tests

Integration tests use `factory-boy` factories, not inline `session.add()`.

**Canonical directory:** [`tests/factories/`](../../tests/factories/)

```python
from tests.factories import TenantFactory, MediaBuyFactory

tenant = TenantFactory(tenant_id="t1")
buy = MediaBuyFactory(tenant=tenant)
```

The API is standard factory-boy: `Factory(...)` or `Factory.create(...)`. Factories auto-commit via `sqlalchemy_session_persistence = "commit"`.

**Anti-pattern:**
```python
# WRONG: manual model construction in tests
with get_db_session() as session:
    tenant = Tenant(tenant_id="test", name="Test", ...)
    session.add(tenant)
    session.commit()
```

**Enforced by:** `review-execution-excellence` (Factory Fixtures), `test_architecture_repository_pattern.py` (catches `session.add()` in integration tests)

## 6. Transport Boundary (CP-5)

All tools have two layers: transport wrappers (MCP, A2A, REST) and business logic (`_impl` functions).

**`_impl` rules:** Accept `ResolvedIdentity` (not `Context`). Raise `AdCPError` subclasses (not `ToolError`). Zero imports from `fastmcp`/`a2a`/`starlette`/`fastapi`.

**Transport wrapper rules:** Call `resolve_identity()` first. Forward every `_impl` parameter. Translate `AdCPError` to transport-specific format.

**Anti-pattern** (exists in `task_management.py`):
```python
# WRONG: business logic function accepts Context directly
async def list_tasks(
    context: Context | None = None,  # Should be ResolvedIdentity only
    identity: ResolvedIdentity | None = None,
) -> dict:
    if identity is None and context is not None:
        identity = await context.get_state("identity")  # Auth resolution in _impl
```

This is tracked debt — functions should be split into `_list_tasks_impl` + transport wrappers.

**Enforced by:** `review-architecture` (CP-5), `review-layering` (Transport → _impl leaks), `test_transport_agnostic_impl.py`, `test_impl_resolved_identity.py`, `test_no_toolerror_in_impl.py`, `test_architecture_boundary_completeness.py`

## 7. Error Hierarchy

`_impl` functions raise `AdCPError` subclasses (defined in `src/core/exceptions.py`). Transport wrappers catch these and translate to transport-appropriate format.

**Canonical file:** [`src/core/exceptions.py`](../../src/core/exceptions.py)

```
AdCPError
├── AdCPValidationError      (400)
├── AdCPAuthenticationError   (401)
├── AdCPAuthorizationError    (403)
├── AdCPNotFoundError         (404)
├── AdCPRateLimitError        (429)
└── AdCPAdapterError          (502)
```

**Anti-pattern: returning error response objects instead of raising**

`media_buy_update.py` has 22 instances where validation failures are returned as `UpdateMediaBuyError` objects instead of raising `AdCPValidationError`. This is tracked debt — the dominant pattern in that file but not the target architecture.

```python
# WRONG (exists as debt): returning error response from _impl
if total_budget <= 0:
    return UpdateMediaBuyError(errors=[Error(code="invalid_budget", message=...)])

# CORRECT: raise AdCPError, let transport wrapper format the response
if total_budget <= 0:
    raise AdCPValidationError("Budget must be positive")
```

**Anti-pattern: using ValueError/RuntimeError instead of AdCPError**

```python
# WRONG: generic Python exceptions
raise ValueError(f"Media buy '{media_buy_id}' not found.")

# CORRECT: domain exception
raise AdCPNotFoundError(f"Media buy '{media_buy_id}' not found.")
```

**Enforced by:** `review-execution-excellence` (AdCPError Hierarchy), `review-python-practices` (Error Handling), `test_no_toolerror_in_impl.py`

## 8. DRY — Shared Validation

When the same validation logic applies to multiple code paths (create and update), extract a shared validator. Both create and update should call the same validation function.

**Anti-pattern** (exists in codebase):
```python
# media_buy_create.py — inline validation
if package_budget < package_min_spend:
    raise ValueError(f"Package budget does not meet minimum ...")

# media_buy_update.py — same logic, different code path
if budget_amount < min_package_budget:
    return UpdateMediaBuyError(errors=[Error(code="budget_below_minimum", ...)])
```

Same check, two implementations, different error handling. When the validation rule changes, one gets updated and the other doesn't.

**Enforced by:** `review-dry` (Category 4: Database Query Patterns, Category 3: Error Handling), `check_code_duplication.py` (pre-commit + make quality)

## Quick Reference: Where to Look

| When you need to... | Read this file |
|---------------------|---------------|
| Add a repository | `src/core/database/repositories/media_buy.py` |
| Wire a repo into UoW | `src/core/database/repositories/uow.py` |
| Write an `_impl` function | `src/core/tools/products.py` (cleanest layering) |
| Write a test for an `_impl` | `tests/harness/_base.py` → domain env like `tests/harness/delivery_poll_unit.py` |
| See tests using the harness | `tests/unit/test_delivery_poll_behavioral.py` |
| Write a Flask endpoint test | `tests/unit/test_signup_flow_session.py` |
| Create a test factory | `tests/factories/media_buy.py` |
| Understand error hierarchy | `src/core/exceptions.py` |
| Add a structural guard | `docs/development/structural-guards.md` |
| Understand the review agents | `.claude/agents/review-*.md` (8 agents) |

## Legacy Code Awareness

These files contain significant legacy patterns. **Do not follow patterns from these files for new code:**

| File | Legacy patterns | Tracked by |
|------|----------------|------------|
| `src/core/tools/media_buy_update.py` | 16 raw `session.*` calls, 22 returned error objects instead of raised, deprecated `uow.session` usage | `FIXME(salesagent-9f2)` |
| `src/core/tools/media_buy_create.py` | Scattered `"USD"` defaults, inline validation duplicating update path | Incremental migration |
| `src/core/tools/task_management.py` | Accepts `Context` instead of `ResolvedIdentity`, no `_impl` separation | Follow-up refactor needed |

When working in these files: follow the patterns in this document, not the surrounding code.
