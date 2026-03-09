# Python Practices Review

**Scope**: Test harness infrastructure (`tests/harness/`), factory_boy factories (`tests/factories/`), and integration tests using the harness (`tests/integration/test_delivery_*_behavioral.py`)
**Date**: 2026-03-04

## Findings

### PP-01: Use of private `_patch` type from `unittest.mock`
- **Severity**: Medium
- **Category**: Types
- **File**: `tests/harness/_base_unit.py:17`, `tests/harness/_base.py:20`
- **Description**: Both base classes import `_patch` from `unittest.mock` for type-annotating `self._patchers`. The leading underscore marks this as a CPython implementation detail, not a public API. It could change or disappear in future Python versions without deprecation notice.
- **Reproduction**: `grep -rn "_patch" tests/harness/_base*.py`
- **Recommended fix**: Use `contextlib.AbstractContextManager` or just `Any` for the patcher list type. Alternatively, since these are test files, `list[Any]` is adequate:
  ```python
  self._patchers: list[Any] = []
  ```

### PP-02: `identity` property returns `Any` instead of `ResolvedIdentity`
- **Severity**: Medium
- **Category**: Types
- **File**: `tests/harness/_base_unit.py:58`, `tests/harness/_base.py:62`
- **Description**: The `identity` property is typed `-> Any` and the backing field `_identity` is `Any`. Since it always constructs a `ResolvedIdentity`, the return type should say so. This erases type safety for every test that accesses `env.identity` -- callers get no autocomplete or type checking.
- **Reproduction**: `grep -n "def identity.*Any" tests/harness/_base*.py`
- **Recommended fix**: Use `TYPE_CHECKING` import to avoid runtime import overhead while getting type safety:
  ```python
  from __future__ import annotations
  from typing import TYPE_CHECKING
  if TYPE_CHECKING:
      from src.core.resolved_identity import ResolvedIdentity

  @property
  def identity(self) -> ResolvedIdentity:
      ...
  ```

### PP-03: `_session` field typed as `Any` instead of `Session | None`
- **Severity**: Low
- **Category**: Types
- **File**: `tests/harness/_base.py:58`
- **Description**: `self._session: Any = None` loses type information. Since it's always set to `SASession(bind=engine)` in `__enter__`, it should be `Session | None` for better IDE support and type checking.
- **Recommended fix**:
  ```python
  from typing import TYPE_CHECKING
  if TYPE_CHECKING:
      from sqlalchemy.orm import Session as SASession
  self._session: SASession | None = None
  ```

### PP-04: `_db_session` attribute set in `_configure_defaults` without declaration in `__init__`
- **Severity**: Medium
- **Category**: Types
- **File**: `tests/harness/delivery_circuit_breaker_unit.py:82`
- **Description**: The unit variant's `CircuitBreakerEnv._configure_defaults()` assigns `self._db_session = mock_session` but this attribute is never declared in `__init__`. This means accessing `self._db_session` before `__enter__` raises `AttributeError`. It also means type checkers and IDE autocomplete don't see it.
- **Reproduction**: `python -c "from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv; e = CircuitBreakerEnv(); e._db_session"` (will raise AttributeError)
- **Recommended fix**: Declare `self._db_session: MagicMock | None = None` in `__init__`, and add a guard in `set_db_webhooks`:
  ```python
  def __init__(self, **kwargs):
      super().__init__(**kwargs)
      self._service = None
      self._db_session: MagicMock | None = None
  ```

### PP-05: `call_impl` and `call_send` return `Any` where concrete types are known
- **Severity**: Low
- **Category**: Types
- **File**: `tests/harness/delivery_circuit_breaker.py:100,115`, `tests/harness/delivery_circuit_breaker_unit.py:136,150`, `tests/harness/delivery_webhook.py:125`, `tests/harness/delivery_webhook_unit.py:131`
- **Description**: Several `call_impl` and `call_send` methods return `-> Any` even though the return type is known (e.g., `tuple[bool, dict[str, Any]]` for webhook, or the concrete service return type for circuit breaker). The poll variants correctly type their returns as `-> GetMediaBuyDeliveryResponse`. This inconsistency means callers of webhook/circuit-breaker envs get no type checking on return values.
- **Recommended fix**: Add concrete return types matching the actual function signatures.

### PP-06: Global mutation of factory `_meta.sqlalchemy_session` is not thread-safe
- **Severity**: Medium
- **Category**: SQLAlchemy
- **File**: `tests/harness/_base.py:118,134`
- **Description**: `IntegrationEnv.__enter__` mutates every factory's `_meta.sqlalchemy_session` class attribute. This is global state -- if two `IntegrationEnv` instances were used concurrently (e.g., in parallel test workers), they'd clobber each other's session bindings. While pytest typically runs tests sequentially within a process, this is a latent correctness issue that would surface with `pytest-xdist` or any parallel execution.

  The `__exit__` always sets `sqlalchemy_session = None` regardless of whether another context is still active -- there's no reference counting or stack.
- **Recommended fix**: Document the single-threaded constraint prominently. For future-proofing, consider using `threading.local()` or a context variable to make the binding per-context rather than global. At minimum, add an assertion:
  ```python
  def __enter__(self):
      for f in ALL_FACTORIES:
          assert f._meta.sqlalchemy_session is None, (
              f"Factory {f.__name__} already bound to a session -- "
              "nested IntegrationEnv not supported"
          )
          f._meta.sqlalchemy_session = self._session
  ```

### PP-07: `_commit_factory_data` does not handle the case where session persistence is already "commit"
- **Severity**: Low
- **Category**: SQLAlchemy
- **File**: `tests/harness/_base.py:97-100`
- **Description**: All factories set `sqlalchemy_session_persistence = "commit"`, which means factory_boy already calls `session.commit()` after each factory invocation. The `_commit_factory_data()` method in `call_impl` does an additional `session.commit()`. This is harmless (double-committing an already-committed session is a no-op), but it's misleading -- it suggests that factory data isn't committed until `call_impl` is called, which contradicts the `"commit"` persistence setting. The docstring says "Ensure all factory-created data is committed before production code reads it" but the data is already committed.
- **Recommended fix**: Add a comment clarifying this is a safety net, or change persistence to `"flush"` if you actually want deferred commits (giving tests the ability to roll back factory data).

### PP-08: `__exit__` does not ensure cleanup on exception during cleanup
- **Severity**: Medium
- **Category**: Resources
- **File**: `tests/harness/_base.py:129-146`
- **Description**: In `IntegrationEnv.__exit__`, if `patcher.stop()` raises (which is rare but possible if patch state is corrupted), subsequent patchers won't be stopped. Similarly, if session close fails, patches won't be stopped. The cleanup steps are not wrapped in try/finally.
- **Reproduction**: Unlikely to trigger in normal use, but if a test corrupts mock state, the teardown could leak patches into subsequent tests.
- **Recommended fix**: Wrap each cleanup phase in try/finally:
  ```python
  def __exit__(self, *exc):
      errors = []
      try:
          for f in ALL_FACTORIES:
              f._meta.sqlalchemy_session = None
      except Exception as e:
          errors.append(e)
      try:
          if self._session:
              self._session.close()
              self._session = None
      except Exception as e:
          errors.append(e)
      for patcher in reversed(self._patchers):
          try:
              patcher.stop()
          except Exception as e:
              errors.append(e)
      self._patchers.clear()
      self.mock.clear()
      return False
  ```

### PP-09: `make_mock_uow` uses `MagicMock` and `Mock` inconsistently for context manager protocol
- **Severity**: Low
- **Category**: Types
- **File**: `tests/harness/_mock_uow.py:44-45`
- **Description**: `__enter__` and `__exit__` are set via `Mock(return_value=...)` while the parent is `MagicMock`. This mixing is not wrong but is inconsistent. More importantly, `MagicMock` already implements `__enter__` and `__exit__` automatically, so the explicit assignment is technically overriding built-in MagicMock behavior. This is intentional (to control return values), but the use of `Mock` vs `MagicMock` is inconsistent with the rest of the codebase.
- **Recommended fix**: Use `MagicMock` consistently:
  ```python
  mock_uow.__enter__ = MagicMock(return_value=mock_uow)
  mock_uow.__exit__ = MagicMock(return_value=False)
  ```

### PP-10: `WebhookEnv.call_deliver` uses `None` coalescing that creates mutable default in disguise
- **Severity**: Low
- **Category**: Collections
- **File**: `tests/harness/delivery_webhook.py:114`, `tests/harness/delivery_webhook_unit.py:120`
- **Description**: `payload or {"event": "delivery.update", "media_buy_id": "mb_001"}` and `headers or {"Content-Type": "application/json"}` create new dicts each call, which is correct. However, the pattern `payload: dict[str, Any] | None = None` followed by `payload or {...}` means passing an empty dict `{}` would be treated as falsy and replaced with the default. This is unlikely but is a subtle logical bug if someone explicitly passes `payload={}`.
- **Recommended fix**: Use `if payload is None:` instead of `or`:
  ```python
  payload = payload if payload is not None else {"event": "delivery.update", ...}
  ```

## Summary

- Critical: 0
- High: 0
- Medium: 5 (PP-01, PP-02, PP-04, PP-06, PP-08)
- Low: 5 (PP-03, PP-05, PP-07, PP-09, PP-10)

## Overall Assessment

The harness code is well-structured and follows good patterns:
- Correct use of `LazyFunction(lambda: ...)` in factories avoids mutable defaults
- No SQLAlchemy 1.x patterns detected
- No Pydantic v1 API usage
- No async/sync issues
- Clean context manager lifecycle with proper patch cleanup in reverse order
- Good separation between unit (full mock) and integration (real DB) variants

The main area for improvement is **type safety** -- several properties and methods use `Any` where concrete types are available. The global factory session mutation pattern (PP-06) is the most architecturally significant finding, though it's mitigated by pytest's default single-process execution model.
