# Architecture Patterns Reference

This document maps every key pattern in the codebase to its **canonical implementation** — the file you should read and follow. It also identifies **known anti-patterns** that exist in the codebase as tracked technical debt. New code must follow the canonical pattern, not the anti-pattern, even when the anti-pattern appears in surrounding code.

> **Why this document exists:** The codebase has two eras of code — legacy code written before the current architecture was established, and current code that follows the patterns described here. Most code by volume is legacy. If you pattern-match from surrounding code, you will likely follow a legacy pattern. This document tells you which files represent the target architecture.

## 1. Repository Pattern

All database access goes through repository classes. `_impl` functions never contain raw `select()` calls, `session.scalars()`, `session.add()`, or direct model imports for data access.

### Canonical example

**Repository:** [`src/core/database/repositories/media_buy.py`](../../src/core/database/repositories/media_buy.py)

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
- Constructor takes `session` and `tenant_id` — tenant scoping is automatic
- All queries include `tenant_id` in the WHERE clause
- Write methods add to session but never commit — the UoW handles that
- Returns ORM model instances, not dicts

### Anti-pattern (exists in codebase as tracked debt)

```python
# WRONG: raw select() inside _impl function
from src.core.database.models import CurrencyLimit

currency_stmt = select(CurrencyLimit).where(
    CurrencyLimit.tenant_id == tenant["tenant_id"],
    CurrencyLimit.currency_code == request_currency,
)
currency_limit = session.scalars(currency_stmt).first()
```

This bypasses tenant isolation guarantees, scatters data access logic across business functions, and is caught by the `test_architecture_no_raw_select.py` structural guard.

### Adding a new repository

1. Create `src/core/database/repositories/your_model.py` following the `media_buy.py` pattern
2. Add it to `src/core/database/repositories/__init__.py`
3. Wire it into the appropriate UoW class (see next section)
4. The `test_architecture_no_raw_select.py` guard will enforce that new code uses it

## 2. Unit of Work (UoW)

The UoW manages session lifecycle: creates on entry, commits on clean exit, rolls back on exception. It provides tenant-scoped repositories.

### Canonical example

**UoW:** [`src/core/database/repositories/uow.py`](../../src/core/database/repositories/uow.py)

```python
class MediaBuyUoW(BaseUoW):
    media_buys: MediaBuyRepository | None
    currency_limits: CurrencyLimitRepository | None

    def _init_repos(self) -> None:
        assert self._session is not None
        self.media_buys = MediaBuyRepository(self._session, self._tenant_id)
        self.currency_limits = CurrencyLimitRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.media_buys = None
        self.currency_limits = None
```

Usage in `_impl`:
```python
with MediaBuyUoW(tenant["tenant_id"]) as uow:
    media_buy = uow.media_buys.get_by_id(req.media_buy_id)
    currency_limit = uow.currency_limits.get_for_currency("USD")
```

### `uow.session` is deprecated

`BaseUoW.session` emits a `DeprecationWarning` at runtime:

```
uow.session is deprecated — use repository methods instead of raw session access.
```

If you see `session = uow.session` in existing code, that is tracked debt (`FIXME(salesagent-9f2)`), not a pattern to follow. If you need data access that no repository method provides, **add a repository method** — don't use the raw session.

### Anti-pattern

```python
# WRONG: accessing deprecated session property
session = uow.session
result = session.scalars(select(Model).where(...)).first()
```

## 3. Structural Guards and Allowlists

AST-scanning tests enforce architecture invariants on every `make quality` run. See [`docs/development/structural-guards.md`](structural-guards.md) for the full inventory.

### Core rule: allowlists only shrink

Every guard has a set of known violations (pre-existing code). The allowlist tracks this debt.

- **New code that introduces a violation fails CI immediately** — no exceptions
- **Allowlists are never expanded** — if your change shifts line numbers, fix violations to compensate, don't renumber the list
- **Every allowlisted violation has a `# FIXME(salesagent-xxxx)`** comment at the source location linking to a tracked issue
- **Removing a FIXME without fixing the underlying issue is not acceptable** — the FIXME is a contract that the debt is known and tracked

### Anti-pattern

```python
# WRONG: shifting allowlist line numbers to accommodate new code
KNOWN_VIOLATIONS = {
    ("media_buy_update.py", 197),  # was 186 — shifted by new constant block
    ("media_buy_update.py", 223),  # was 212
    ...
}
```

If new code shifts existing violations, fix some violations to offset the churn. The allowlist should be smaller after your PR, not the same size with different numbers.

## 4. Writing Unit Tests

### Test harness: `standard_mocks` fixture

For testing `_impl` functions, use the shared fixture pattern instead of rebuilding mock scaffolding in every test.

**Canonical example:** [`tests/unit/test_update_media_buy_behavioral.py`](../../tests/unit/test_update_media_buy_behavioral.py) lines 84–155

```python
@pytest.fixture
def standard_mocks():
    """Shared mock scaffolding for _update_media_buy_impl tests.

    Patches MediaBuyUoW to provide a mock session and repository,
    plus adapter, principal, and context manager mocks.
    """
    mock_session, mock_cm = _make_mock_db_session()

    mock_uow = MagicMock()
    mock_uow.session = mock_session
    mock_uow.media_buys = MagicMock()
    mock_uow.currency_limits = MagicMock()
    # ... wired into patches
```

Tests using this fixture are concise and focused on behavior:

```python
def test_extreme_budget_rejected(standard_mocks):
    req = UpdateMediaBuyRequest(media_buy_id="mb_1", budget=Budget(total=999999999, currency="USD"))
    result = _update_media_buy_impl(req=req, identity=_make_identity())
    assert isinstance(result, UpdateMediaBuyError)
    assert result.errors[0].code == "budget_ceiling_exceeded"
```

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

If the fixture doesn't exist for the function you're testing, create one following the `standard_mocks` pattern. Don't copy-paste mock setup across tests.

### Testing Flask endpoints

For testing Flask routes, use Flask's test client — not boolean logic reconstruction.

**Canonical example:** [`tests/unit/test_signup_flow_session.py`](../../tests/unit/test_signup_flow_session.py)

```python
from src.admin.app import create_app

app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
with app.test_client() as client:
    with client.session_transaction() as sess:
        sess["authenticated"] = True
    response = client.post("/test/auth", data={...})
    assert response.status_code == 302
```

### Anti-pattern: testing logic instead of behavior

```python
# WRONG: reconstructing application logic and asserting the boolean
env_test_mode = os.environ.get("ADCP_AUTH_TEST_MODE", "").lower() == "true"
tenant_setup_mode = False
should_abort = not env_test_mode or not tenant_setup_mode
assert should_abort is True  # This tests Python, not your application
```

This test passes regardless of what the actual endpoint does. If the gate logic changes in `auth.py`, this test still passes because it never calls the code.

## 5. Factory Fixtures for Integration Tests

Integration tests use `factory-boy` factories, not inline `session.add()` boilerplate.

### Canonical example

**Factories:** [`tests/factories/`](../../tests/factories/)

```python
from tests.factories import TenantFactory, MediaBuyFactory

@pytest.fixture
def sample_tenant(integration_db):
    return TenantFactory.create_sync()

@pytest.fixture
def sample_media_buy(sample_tenant, sample_principal):
    return MediaBuyFactory.create_sync(
        tenant_id=sample_tenant.tenant_id,
        principal_id=sample_principal.principal_id,
    )
```

### Anti-pattern

```python
# WRONG: 20 lines of manual model construction
with get_db_session() as session:
    tenant = Tenant(tenant_id="test", name="Test", subdomain="test", ...)
    session.add(tenant)
    session.commit()
```

The `test_architecture_repository_pattern.py` guard catches new `session.add()` calls in integration tests.

## 6. Transport Boundary (Critical Pattern #5)

All tools have two layers: transport wrappers (MCP, A2A) and business logic (`_impl` functions).

### Canonical example

**`_impl` function:** accepts `ResolvedIdentity`, raises `AdCPError`, has zero transport imports.

```python
async def _create_media_buy_impl(
    req: CreateMediaBuyRequest,
    identity: ResolvedIdentity,  # NOT Context, NOT ToolContext
) -> CreateMediaBuyResult:
    ...
```

**Transport wrapper:** resolves identity, forwards ALL params to `_impl`.

```python
@mcp.tool()
async def create_media_buy(ctx: Context, ...) -> CreateMediaBuyResponse:
    identity = resolve_identity(ctx.http.headers, protocol="mcp")
    return await _create_media_buy_impl(req=req, identity=identity)
```

### Anti-pattern (exists in `task_management.py`)

```python
# WRONG: business logic function accepts Context directly
async def list_tasks(
    context: Context | None = None,  # Should be ResolvedIdentity
    identity: ResolvedIdentity | None = None,
) -> dict:
    if identity is None and context is not None:
        identity = await context.get_state("identity")  # Auth resolution in _impl
```

This is tracked debt — the functions should be renamed to `_list_tasks_impl` etc. with proper wrappers.

**Enforced by:** `test_transport_agnostic_impl.py`, `test_impl_resolved_identity.py`, `test_no_toolerror_in_impl.py`, `test_architecture_boundary_completeness.py`

## 7. Shared Validation (DRY)

When the same validation logic applies to multiple code paths (e.g., create and update), extract a shared validator.

### Canonical example

**Shared validators:** [`src/core/tools/financial_validation.py`](../../src/core/tools/financial_validation.py)

```python
def validate_min_package_budget(
    *, package_budget: Decimal, min_package_budget: Decimal, currency: str,
) -> str | None:
    """Returns error message if validation fails, None if OK."""
    if package_budget < min_package_budget:
        return f"Package budget ({package_budget} {currency}) does not meet ..."
    return None
```

Properties:
- Pure function — no session, no transport, no side effects
- Returns `str | None` — caller decides how to wrap the error
- Used by both create and update paths

### Anti-pattern

```python
# WRONG: duplicated validation in two files with slightly different messages
# media_buy_create.py
if package_budget < package_min_spend:
    raise ValueError(f"Package budget ... does not meet minimum ...")

# media_buy_update.py (different message, same logic)
if budget_amount < min_package_budget:
    return UpdateMediaBuyError(errors=[Error(code="budget_below_minimum", ...)])
```

## Quick Reference: Where to Look

| When you need to... | Read this file |
|---------------------|---------------|
| Add a repository | `src/core/database/repositories/media_buy.py` |
| Wire a repo into UoW | `src/core/database/repositories/uow.py` |
| Write an `_impl` function | `src/core/tools/media_buy_delivery.py` (cleanest) |
| Write a behavioral unit test | `tests/unit/test_update_media_buy_behavioral.py` |
| Write a Flask endpoint test | `tests/unit/test_signup_flow_session.py` |
| Create a factory | `tests/factories/media_buy.py` |
| Add a structural guard | `docs/development/structural-guards.md` |
| Understand the allowlist rules | This document, Section 3 |
| Add shared validation | `src/core/tools/financial_validation.py` |

## Legacy Code Awareness

The following files contain significant legacy patterns that are being incrementally migrated. **Do not follow patterns from these files for new code:**

| File | Legacy patterns present | Tracked by |
|------|------------------------|------------|
| `src/core/tools/media_buy_update.py` | 16 raw `session.scalars/add/delete/flush` calls, `uow.session` usage | `FIXME(salesagent-9f2)` |
| `src/core/tools/media_buy_create.py` | Scattered `"USD"` defaults, inline validation | Being extracted to `financial_validation.py` |
| `src/core/tools/task_management.py` | Accepts `Context` instead of `ResolvedIdentity`, no `_impl` separation | Follow-up refactor needed |

When working in these files: follow the patterns in this document, not the surrounding code.
