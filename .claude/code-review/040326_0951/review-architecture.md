# Architecture Review

**Scope**: Test harness infrastructure for integration testing
- `tests/harness/` -- all files (IntegrationEnv base, DeliveryPollEnv, WebhookEnv, CircuitBreakerEnv, unit variants, meta-tests)
- `tests/factories/` -- factory_boy SQLAlchemy model factories
- `tests/integration/test_delivery_poll_behavioral.py` -- integration tests using DeliveryPollEnv
- `tests/integration/test_delivery_webhook_behavioral.py` -- integration tests using WebhookEnv
- `tests/integration/test_delivery_service_behavioral.py` -- integration tests using CircuitBreakerEnv

**Date**: 2026-03-04

---

## Findings

### CR-01: Unit CircuitBreakerEnv mocks get_db_session at wrong patch target
- **Severity**: Medium
- **Pattern**: CP-3 (Repository Pattern)
- **File**: `tests/harness/delivery_circuit_breaker_unit.py:61`
- **Description**: The unit variant patches `src.core.database.database_session.get_db_session` (the definition site), while the integration variant patches `src.services.webhook_delivery_service.httpx.Client` etc. at the module's import site. For `get_db_session`, the correct patch target depends on how `webhook_delivery_service.py` imports it. If `webhook_delivery_service.py` uses `from src.core.database.database_session import get_db_session`, then patching the definition site (`src.core.database.database_session.get_db_session`) might not intercept calls from the service module -- you should patch at the import site (`src.services.webhook_delivery_service.get_db_session`). This is inconsistent with how all other patches in the same file target `{self.MODULE}.*` but the db patch uses the definition site.
- **Reproduction**: `grep -n "get_db_session" tests/harness/delivery_circuit_breaker_unit.py`
- **Recommended fix**: Change the patch target to `f"{self.MODULE}.get_db_session"` or verify that the service module imports via the module path being patched. If the service imports `get_db_session` directly, the current patch may not actually intercept calls.

### CR-02: Integration WebhookEnv does not mock get_db_session -- potential table dependency
- **Severity**: Low
- **Pattern**: CP-3 (Repository Pattern)
- **File**: `tests/harness/delivery_webhook.py:52-56`
- **Description**: The integration `WebhookEnv` correctly does not mock `get_db_session` (relying on real DB). However, the docstring at line 5 says "Real: get_db_session for delivery record tracking (fails silently if no table)." The phrase "fails silently if no table" suggests the delivery record tracking table may not exist in the test schema. If `integration_db` creates all tables via `Base.metadata.create_all()`, this should work. But if the delivery record tracking model is not registered in `Base.metadata`, the integration tests would silently skip delivery record persistence without the test knowing. This is not a violation per se, but a fragility risk.
- **Reproduction**: Check if `deliver_webhook_with_retry` attempts to write delivery records and whether the relevant model is in `Base.metadata`
- **Recommended fix**: Confirm delivery record model is included in `Base.metadata.create_all()`. If delivery record tracking is a planned feature, add a note or TODO. If it exists, remove the "fails silently" caveat from the docstring.

### CR-03: IntegrationEnv directly mutates factory_boy Meta internals
- **Severity**: Medium
- **Pattern**: N/A (Test infrastructure robustness)
- **File**: `tests/harness/_base.py:118,133`
- **Description**: `IntegrationEnv.__enter__` sets `f._meta.sqlalchemy_session` directly on every factory in `ALL_FACTORIES`, and `__exit__` sets it to `None`. This is a global mutation -- if two IntegrationEnv contexts were accidentally nested or used in parallel (e.g., via threading or parametrize), the second `__exit__` would unbind the session for the still-active first context. This is safe in current sequential usage but fragile. The `_meta.sqlalchemy_session` is shared class-level state; factory_boy's own documentation recommends `cls._meta.sqlalchemy_session` for this purpose, but the global mutation pattern means test isolation depends on execution order.
- **Reproduction**: `grep -n "_meta.sqlalchemy_session" tests/harness/_base.py`
- **Recommended fix**: No immediate fix needed given current sequential test execution. Document the constraint: "IntegrationEnv contexts must not be nested or run concurrently." Consider using a `contextvars.ContextVar` for session binding if parallelism is ever needed.

### CR-04: Factory `sqlalchemy_session_persistence = "commit"` auto-commits each factory call
- **Severity**: Low
- **Pattern**: CP-3 (Repository Pattern)
- **File**: `tests/factories/core.py:20`, `tests/factories/principal.py:16`, etc.
- **Description**: Every factory uses `sqlalchemy_session_persistence = "commit"`. This means every `TenantFactory()`, `PrincipalFactory()`, etc. individually commits. Combined with `RelatedFactory` cascading (e.g., `TenantFactory` auto-creates `CurrencyLimitFactory`), a single `TenantFactory()` call may issue 2+ commits. This is architecturally sound for integration tests (production code needs committed data visible via separate sessions), but means tests cannot batch multiple factory creations in a single transaction for rollback. The `IntegrationEnv._commit_factory_data()` method at `_base.py:97-100` is therefore mostly a safety net (data is already committed by factory_boy). This is acceptable but should be documented clearly.
- **Reproduction**: `grep -n "sqlalchemy_session_persistence" tests/factories/*.py`
- **Recommended fix**: Add a brief comment in `_base.py:_commit_factory_data()` noting that this is a safety net since factories auto-commit. No functional change needed.

### CR-05: DeliveryPollEnv (integration) bypasses repository pattern for adapter mock wiring
- **Severity**: Low
- **Pattern**: CP-5 (Transport Boundary)
- **File**: `tests/harness/delivery_poll.py:57-58`
- **Description**: The integration `DeliveryPollEnv` only patches `get_adapter` (the adapter factory function). This is correct -- the adapter is the external boundary. The `_impl` function's internal use of `MediaBuyUoW` and `get_principal_object` runs against real DB, which is exactly the design intent. No violation here. The integration harness correctly separates external mocks (adapter) from internal DB access.
- **Reproduction**: `grep -n "EXTERNAL_PATCHES" tests/harness/delivery_poll.py`
- **Recommended fix**: None. This is the correct architecture.

### CR-06: Unit WebhookEnv patches get_db_session -- integration WebhookEnv does not
- **Severity**: Low
- **Pattern**: CP-3 (Repository Pattern), architectural consistency
- **File**: `tests/harness/delivery_webhook_unit.py:51` vs `tests/harness/delivery_webhook.py:52-56`
- **Description**: The unit variant mocks `get_db_session` while the integration variant lets it hit real DB. This is the intended design difference between unit and integration harnesses. The separation is clean: unit env mocks everything (external + DB), integration env mocks only external (HTTP, URL validation, timing). No violation.
- **Reproduction**: `diff tests/harness/delivery_webhook_unit.py tests/harness/delivery_webhook.py`
- **Recommended fix**: None. Correct by design.

### CR-07: Integration tests use `from tests.harness import ...` inside test methods
- **Severity**: Low
- **Pattern**: N/A (Style convention)
- **File**: `tests/integration/test_delivery_poll_behavioral.py:46-47`, multiple test classes
- **Description**: Imports of factories and harness envs are done inside test methods rather than at module level. For example: `from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory` and `from tests.harness import DeliveryPollEnv` appear inside each test method. This is a conscious choice to avoid import-time failures if DB dependencies are not available, and to make each test self-contained. However, it adds redundancy and ~2 lines of import boilerplate per test.
- **Reproduction**: `grep -n "from tests.factories import" tests/integration/test_delivery_poll_behavioral.py | head -10`
- **Recommended fix**: Consider moving these imports to module level with a `pytest.importorskip` guard or behind `TYPE_CHECKING`. The `@pytest.mark.requires_db` marker already gates test execution, so import-time safety may be unnecessary. This is a style preference, not a pattern violation.

### CR-08: CircuitBreakerEnv (integration) test creates service outside env context
- **Severity**: Medium
- **Pattern**: CP-3 (Repository Pattern)
- **File**: `tests/integration/test_delivery_service_behavioral.py:148-163`
- **Description**: In `test_send_webhook_enhanced_catches_db_errors`, a `WebhookDeliveryService()` is instantiated outside the `CircuitBreakerEnv` context manager, and `get_db_session` is patched directly via `with patch(...)` inline. This bypasses the harness pattern -- the whole point of CircuitBreakerEnv is to centralize mock management. The inline `patch` is the old pattern the harness was designed to replace.
- **Reproduction**: `grep -n "with patch" tests/integration/test_delivery_service_behavioral.py`
- **Recommended fix**: Either wrap this in a `CircuitBreakerEnv` and use `env.mock["client"]` etc., or document why this specific test needs to bypass the harness (perhaps it's testing a scenario the harness doesn't support).

### CR-09: Production _impl imports Context and ToolContext at module level
- **Severity**: N/A (Pre-existing, known)
- **Pattern**: CP-5 (Transport Boundary)
- **File**: `src/core/tools/media_buy_delivery.py:17-22`
- **Description**: `_get_media_buy_delivery_impl` module imports `from fastmcp.server.context import Context` and `from src.core.tool_context import ToolContext` at the top level. However, these imports are used by the transport wrappers in the same module, not by the `_impl` function itself. The structural guard `test_transport_agnostic_impl.py` likely allows this since the _impl function body doesn't reference them. Not a harness issue -- noted for completeness.
- **Reproduction**: `head -25 src/core/tools/media_buy_delivery.py`
- **Recommended fix**: Out of scope for this review. Flagged for awareness only.

---

## Summary of Answers to Key Questions

### 1. Does IntegrationEnv correctly separate external mocks from real DB?
**Yes.** `IntegrationEnv` (`_base.py`) creates a non-scoped SQLAlchemy session for factory_boy, separate from production code's `scoped_session` via `get_db_session()`. Both point to the same PostgreSQL test database (set up by `integration_db` fixture). The `EXTERNAL_PATCHES` dict is the only thing mocked. This is architecturally sound.

### 2. Do the domain envs follow CLAUDE.md patterns?
**Yes.** Integration variants (`delivery_poll.py`, `delivery_webhook.py`, `delivery_circuit_breaker.py`) only mock external services. No transport boundary violations found. No `ToolError`, `Context`, or transport imports in the harness code. Tests use factory_boy instead of inline `session.add()`. All three integration envs inherit from `IntegrationEnv` and follow the same pattern.

### 3. Is the factory session management architecturally sound?
**Mostly yes, with caveats.** The `sqlalchemy_session_persistence = "commit"` ensures factory-created data is visible to production code's separate session. The `_commit_factory_data()` method in `IntegrationEnv` is a safety net. The global mutation of `_meta.sqlalchemy_session` (CR-03) is fragile under parallelism but safe in current sequential test execution.

### 4. Are there transport boundary violations in the harness or tests?
**No.** Zero imports from `fastmcp`, `a2a`, `starlette`, or `fastapi` in any harness or integration test file. Tests call `_impl` functions directly via the harness, correctly bypassing the transport layer. `ResolvedIdentity` is used (not `Context`) -- exactly per CP-5.

### 5. Does the harness follow the repository pattern or bypass it?
**Correctly follows it.** Integration envs let `_impl` functions use their normal repository/UoW patterns against real DB. The only mock is the adapter (external I/O boundary). One exception: `test_send_webhook_enhanced_catches_db_errors` (CR-08) uses inline `patch` outside the harness, which is the old pattern the harness was designed to replace.

---

## Counts

- Critical: 0
- High: 0
- Medium: 3 (CR-01, CR-03, CR-08)
- Low: 5 (CR-02, CR-04, CR-05, CR-06, CR-07)
