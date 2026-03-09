# DRY Review

**Scope**: Test harness infrastructure (`tests/harness/`), factories (`tests/factories/`), and integration tests (`tests/integration/test_delivery_*_behavioral.py`)
**Date**: 2026-03-04

## Duplication Map

| Pattern | Occurrences | Files | Extractable? |
|---------|------------|-------|-------------|
| Base class duplication (IntegrationEnv vs ImplTestEnv) | 2 | `_base.py`, `_base_unit.py` | Yes -> single parameterized base |
| Integration/unit env pairs with near-identical fluent API | 6 (3 pairs) | `delivery_poll.py`/`delivery_poll_unit.py`, `delivery_webhook.py`/`delivery_webhook_unit.py`, `delivery_circuit_breaker.py`/`delivery_circuit_breaker_unit.py` | Partially -> shared mixin or composition |
| `set_adapter_response()` method duplicated across integration/unit poll envs | 2 | `delivery_poll.py:80-113`, `delivery_poll_unit.py:135-168` | Yes -> shared method body |
| `_adapter_lookup` / `_adapter_side_effect` identical logic | 2 | `delivery_poll.py:71-78`, `delivery_poll_unit.py:84-92` | Yes -> shared method |
| `_make_default_adapter_response()` identical | 2 | `delivery_poll.py:150-161`, `delivery_poll_unit.py:205-216` | Yes -> shared constant |
| Webhook `set_http_status/sequence/error/url_invalid` identical across integration/unit | 2 | `delivery_webhook.py:68-96`, `delivery_webhook_unit.py:70-103` | Yes -> shared mixin |
| CircuitBreaker `get_service/get_breaker/set_http_response` identical | 2 | `delivery_circuit_breaker.py:74-88`, `delivery_circuit_breaker_unit.py:84-103` | Yes -> shared mixin |
| `call_impl` kwarg assembly (poll env) identical | 2 | `delivery_poll.py:119-148`, `delivery_poll_unit.py:179-203` | Yes -> shared method |
| `call_deliver` method body identical | 2 | `delivery_webhook.py:98-123`, `delivery_webhook_unit.py:105-129` | Yes -> shared method |
| `call_send` method body identical | 2 | `delivery_circuit_breaker.py:90-113`, `delivery_circuit_breaker_unit.py:126-148` | Yes -> shared method |
| Test boilerplate: `tenant + principal + env` repeated 50+ times | ~52 | `test_delivery_poll_behavioral.py` | Partially -> pytest fixture |
| Inline `from tests.factories import ...` inside test methods | 57 | `test_delivery_poll_behavioral.py` | Yes -> module-level import |
| Custom adapter response construction bypassing `set_adapter_response` | 4 | `test_delivery_poll_behavioral.py:820,892,955,1425` | Partially -> `env.set_custom_adapter_response()` |
| Identity property duplicated verbatim | 2 | `_base.py:61-79`, `_base_unit.py:58-76` | Yes -> shared mixin |

## Findings

### DRY-01: Base classes are semantically duplicate
- **Severity**: Critical
- **Category**: Test (infrastructure)
- **Occurrences**: 2 files, ~100 lines duplicated semantically
- **Files**:
  - `tests/harness/_base.py:23-147` -- `IntegrationEnv`
  - `tests/harness/_base_unit.py:20-126` -- `ImplTestEnv`
- **Description**: Both base classes implement the same contract: `__init__` (identical signature and body), `identity` property (identical, lines 61-79 vs 58-76), `call_impl` (identical abstract), `_configure_mocks`/`_configure_defaults` (identical abstract), context manager protocol (`__enter__`/`__exit__` with same patch lifecycle). The only real difference is that `IntegrationEnv` binds factory_boy to a SQLAlchemy session and uses `EXTERNAL_PATCHES` dict, while `ImplTestEnv` uses `_patch_targets()` method and has no DB session management. This is a configuration difference, not a structural one.
- **Proposed extraction**: Single `BaseTestEnv` class with a `use_real_db: bool` class attribute. When `True`, `__enter__` creates a SQLAlchemy session and binds factories (current `IntegrationEnv` behavior). When `False`, no DB setup (current `ImplTestEnv` behavior). Patch targets come from `EXTERNAL_PATCHES` dict in both cases (the `_patch_targets()` method adds no value over a dict). Removes ~70 lines.

### DRY-02: Integration/unit poll env pair is ~80% identical
- **Severity**: Critical
- **Category**: Test (infrastructure)
- **Occurrences**: 2 files
- **Files**:
  - `tests/harness/delivery_poll.py:43-161` -- Integration `DeliveryPollEnv`
  - `tests/harness/delivery_poll_unit.py:39-216` -- Unit `DeliveryPollEnv`
- **Description**: Both classes implement identical methods: `set_adapter_response` (same body, both build `AdapterGetMediaBuyDeliveryResponse` identically), `set_adapter_error` (same body), `call_impl` (same kwarg assembly + request construction), `_make_default_adapter_response` (identical static method), and the adapter lookup side_effect function (identical logic). The unit variant adds `add_buy()` and `set_pricing_options()` which are unit-only helpers. The integration variant adds `_commit_factory_data()` call. A bug fix to `set_adapter_response` would need to be replicated in both files.
- **Proposed extraction**: Shared `DeliveryPollMixin` containing `set_adapter_response`, `set_adapter_error`, `_adapter_lookup`, `_make_default_adapter_response`, and the `call_impl` kwarg assembly. Both env classes compose this mixin. Removes ~60 lines.

### DRY-03: Integration/unit webhook env pair is ~90% identical
- **Severity**: High
- **Category**: Test (infrastructure)
- **Occurrences**: 2 files
- **Files**:
  - `tests/harness/delivery_webhook.py:36-128` -- Integration `WebhookEnv`
  - `tests/harness/delivery_webhook_unit.py:33-134` -- Unit `WebhookEnv`
- **Description**: `set_http_status`, `set_http_sequence`, `set_http_error`, `set_url_invalid`, `call_deliver`, and `call_impl` are all identical in both files. The only difference is the unit version mocks `get_db_session` (one extra patch target) and wires a mock context manager for it. The integration version lets DB calls hit the real database. The fluent API methods are byte-for-byte identical.
- **Proposed extraction**: `WebhookMixin` with `set_http_status`, `set_http_sequence`, `set_http_error`, `set_url_invalid`, `call_deliver`. Removes ~50 lines.

### DRY-04: Integration/unit circuit breaker env pair is ~85% identical
- **Severity**: High
- **Category**: Test (infrastructure)
- **Occurrences**: 2 files
- **Files**:
  - `tests/harness/delivery_circuit_breaker.py:40-117` -- Integration `CircuitBreakerEnv`
  - `tests/harness/delivery_circuit_breaker_unit.py:39-153` -- Unit `CircuitBreakerEnv`
- **Description**: `get_service`, `get_breaker`, `set_http_response`, `call_send`, and `call_impl` are identical. The unit version adds `set_db_webhooks` and `make_webhook_config` helpers (these could exist in both). The difference is one extra mock (`db`) and its `_configure_defaults` wiring.
- **Proposed extraction**: `CircuitBreakerMixin` with the shared methods. Removes ~40 lines.

### DRY-05: Repeated tenant/principal/env setup in poll integration tests
- **Severity**: Medium
- **Category**: Test (setup)
- **Occurrences**: ~52 test methods
- **Files**:
  - `tests/integration/test_delivery_poll_behavioral.py` -- nearly every test method
- **Description**: The following 3-4 line block appears in virtually every test:
  ```python
  with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
      tenant = TenantFactory(tenant_id="t1")
      principal = PrincipalFactory(tenant=tenant, principal_id="p1")
  ```
  This appears 52+ times. The `tenant_id="t1"` / `principal_id="p1"` must be synchronized between the env constructor and the factory calls -- a subtle coupling that could cause bugs if only one side is changed.
- **Proposed extraction**: A pytest fixture or class-level setup that creates the env+tenant+principal triple. Alternatively, `DeliveryPollEnv.__enter__` could auto-create the tenant/principal via factories when `auto_create_fixtures=True`. This would reduce ~150 lines of setup boilerplate and eliminate the synchronization risk.

### DRY-06: Repeated inline imports in test methods
- **Severity**: Low
- **Category**: Test (style)
- **Occurrences**: 57 factory imports + 53 harness imports inside test methods
- **Files**:
  - `tests/integration/test_delivery_poll_behavioral.py` -- inside each test method
- **Description**: Every test method contains `from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory` and `from tests.harness import DeliveryPollEnv`. These could be module-level imports. The inline pattern was likely chosen to make each test self-contained for the obligation test generator, but it adds ~110 lines of pure boilerplate.
- **Proposed extraction**: Move to module-level imports. If self-containment is required (e.g., for test extraction), this is intentional and should be documented as such.
- **Note**: This may be intentional for the obligation test architecture where tests are generated per-obligation. If so, mark as "not duplicate" and document the reason.

### DRY-07: Custom adapter response construction bypasses env fluent API
- **Severity**: Medium
- **Category**: Test (setup)
- **Occurrences**: 4 test methods
- **Files**:
  - `tests/integration/test_delivery_poll_behavioral.py:820-834` -- `test_two_packages_each_have_own_metrics`
  - `tests/integration/test_delivery_poll_behavioral.py:892-906` -- `test_package_breakdowns_include_pacing`
  - `tests/integration/test_delivery_poll_behavioral.py:955-969` -- `test_totals_reflect_sum_of_package_metrics`
  - `tests/integration/test_delivery_poll_behavioral.py:1425-1442` -- `test_cpm_spend_propagated`
- **Description**: Four tests manually construct `AdapterGetMediaBuyDeliveryResponse` objects and directly set `env.mock["adapter"].return_value.get_media_buy_delivery.return_value` -- bypassing `set_adapter_response`. This happens because `set_adapter_response` only supports single-package responses. Each manual construction repeats the same ~12-line pattern of building `ReportingPeriod`, `DeliveryTotals`, and `by_package` list.
- **Proposed extraction**: Extend `set_adapter_response` (or add `set_custom_adapter_response`) to accept a multi-package `by_package` list. The 4 occurrences would shrink to 1-2 lines each, removing ~40 lines.

### DRY-08: Meta-test classes follow identical structural pattern
- **Severity**: Low
- **Category**: Test (meta-tests)
- **Occurrences**: 3 files
- **Files**:
  - `tests/harness/test_harness_delivery_poll.py` -- `TestDeliveryPollEnvContract` (8 tests)
  - `tests/harness/test_harness_delivery_webhook.py` -- `TestWebhookEnvContract` (7 tests)
  - `tests/harness/test_harness_circuit_breaker.py` -- `TestCircuitBreakerEnvContract` (6 tests)
- **Description**: Each meta-test file tests its env's contract: default response works, mock access works, identity flows through, custom params flow through. These are NOT duplicated logic since they test different envs, but the meta-test for identity (`test_custom_identity_flows_through`) and mock access (`test_mock_access`) patterns are structurally identical. This is framework-inherent test boilerplate, not extractable semantic duplication.
- **Not actionable**: This is intentional per-env contract testing. Flagged for completeness only.

## Summary

- Critical: 2 (DRY-01: base class duplication, DRY-02: poll env pair)
- High: 2 (DRY-03: webhook env pair, DRY-04: circuit breaker env pair)
- Medium: 2 (DRY-05: test setup boilerplate, DRY-07: custom adapter response)
- Low: 2 (DRY-06: inline imports, DRY-08: meta-test patterns)
- Total duplicated logic blocks: 8
- Estimated lines removable by extraction: ~270-320

## Recommended Refactoring Priority

1. **Merge base classes** (DRY-01): Single `BaseTestEnv` with `use_real_db` flag. This unblocks all subsequent merges.
2. **Extract shared mixins per domain** (DRY-02, DRY-03, DRY-04): `DeliveryPollMixin`, `WebhookMixin`, `CircuitBreakerMixin` containing the fluent API methods. Integration and unit envs become thin subclasses differing only in patch targets and DB mode.
3. **Extend `set_adapter_response` for multi-package** (DRY-07): Small API change, removes 4 manually-constructed response blocks.
4. **Add fixture for tenant/principal setup** (DRY-05): Either a pytest fixture or `DeliveryPollEnv` option.
5. **Module-level imports** (DRY-06): Only if the obligation test architecture allows it (investigate first).
