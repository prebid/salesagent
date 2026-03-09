# Testing Quality Review

**Scope**: Test harness infrastructure (`tests/harness/`), factory-boy model factories (`tests/factories/`), integration behavioral tests (`tests/integration/test_delivery_*_behavioral.py`), unit behavioral tests (`tests/unit/test_delivery_*_behavioral.py`), and harness meta-tests.

**Date**: 2026-03-04

## Test Suite Shape

| Category | Count | Notes |
|----------|-------|-------|
| Integration tests (delivery poll) | 60 | Real PostgreSQL via DeliveryPollEnv |
| Integration tests (webhook) | 15 | Real PostgreSQL via WebhookEnv |
| Integration tests (circuit breaker) | 4 | Real PostgreSQL via CircuitBreakerEnv |
| Unit tests (delivery poll) | 13 | Remaining after migration; helpers + inline patches |
| Unit tests (webhook) | 0 | Fully migrated (file has helpers + comment) |
| Unit tests (delivery service) | 15 | CircuitBreaker state machine + inline patches |
| Meta-tests (harness) | 21 | 8 poll + 7 webhook + 6 circuit breaker |
| **Total** | **128** | |
| xfail tests (integration poll) | ~17 | All with documented reasons |
| xfail tests (integration webhook) | 2 | All with documented reasons |
| xfail tests (unit service) | 3 | Mixed strict/non-strict |
| xfail tests (unit poll) | 3 | All with documented reasons |
| Assertion-free tests | 0 | Every test has meaningful assertions |
| High-mock tests (>5 patches) | 1 | `test_delivery_marked_reporting_delayed_when_circuit_open` (4 patches, borderline) |

## Harness Architecture Assessment

### HN-01: Clean integration/unit split via dual base classes
- **Rating**: Positive
- **Files**: `_base.py` (IntegrationEnv), `_base_unit.py` (ImplTestEnv)
- **Assessment**: The two-base-class design is well-executed. `IntegrationEnv` binds factory_boy to a real PostgreSQL session via `get_engine()` and only patches `EXTERNAL_PATCHES`. `ImplTestEnv` patches everything via `_patch_targets()` for fast unit tests. The `_commit_factory_data()` call before `call_impl()` in integration envs is a critical detail that prevents stale-read bugs.

### HN-02: Factory_boy factories are well-designed
- **Rating**: Positive
- **Files**: `tests/factories/*.py`
- **Assessment**: Factories use `sqlalchemy_session = None` with dynamic binding -- this is the correct pattern for shared factories that work across integration envs. `TenantFactory` auto-creates the required `CurrencyLimit` via `RelatedFactory`, preventing the most common test setup failure. `MediaBuyFactory` correctly chains `SubFactory(PrincipalFactory, tenant=factory.SelfAttribute("..tenant"))` to maintain referential integrity. All factories use `sqlalchemy_session_persistence = "commit"` which is necessary for production code to see the data through separate sessions.

### HN-03: Context manager cleanup is correct
- **Rating**: Positive
- **Files**: `_base.py:129-146`, `_base_unit.py:120-125`
- **Assessment**: `__exit__` correctly unbinds factories, closes the factory session, and stops patches in reverse order. The `return False` ensures exceptions propagate. Factory unbinding (`f._meta.sqlalchemy_session = None`) prevents session leaks between tests.

## Findings

### TQ-01: Unit webhook test file is empty shell
- **Severity**: Low
- **Anti-pattern**: Dead code
- **File**: `tests/unit/test_delivery_webhook_behavioral.py:96`
- **Description**: The file contains 96 lines of imports, helpers (`_make_identity`, `_make_buy`, `_make_adapter_response`), and a comment "All test classes migrated to tests/integration/test_delivery_webhook_behavioral.py" but zero test functions. These helpers are also duplicated in the unit poll and unit service test files.
- **Evidence**: `grep -c "def test_" tests/unit/test_delivery_webhook_behavioral.py` returns 0.
- **Impact**: Low -- this is technical debt, not false confidence. The helpers are unused dead code.
- **Recommended fix**: Delete the file entirely, or strip it to just the migration comment. The helpers are duplicated in sibling unit test files anyway.

### TQ-02: Duplicate helper functions across unit test files
- **Severity**: Low
- **Anti-pattern**: DRY violation in test infrastructure
- **Files**: `tests/unit/test_delivery_poll_behavioral.py:51-110`, `tests/unit/test_delivery_webhook_behavioral.py:34-93`, `tests/unit/test_delivery_service_behavioral.py:52-111`
- **Description**: `_make_identity()`, `_make_buy()`, and `_make_adapter_response()` are copy-pasted across all three unit test files. These are effectively the unit-test equivalent of what the harness's `ImplTestEnv` already encapsulates.
- **Impact**: Low -- test code duplication is tolerable but creates maintenance burden.
- **Recommended fix**: Could extract into a shared `tests/unit/_helpers.py` or consolidate into the unit harness classes (which already have `add_buy()` doing the same thing).

### TQ-03: Some integration tests only assert isinstance and type presence
- **Severity**: Medium
- **Anti-pattern**: Assertion-light tests / testing the framework
- **File**: `tests/integration/test_delivery_poll_behavioral.py`
- **Tests**:
  - `TestProtocolEnvelopeStatusCompleted::test_successful_query_returns_response_type` (line 1305) -- only asserts `isinstance(response, GetMediaBuyDeliveryResponse)`. This will pass for any valid response, even one with wrong data.
  - `TestProtocolEnvelopeStatusCompleted::test_successful_query_has_required_envelope_fields` (line 1357) -- asserts `is not None` on four fields. Since these are required Pydantic fields, they literally cannot be None in a valid response object.
  - `TestDeliveryMetricsFieldPresence::test_totals_include_video_completions_field` (line 1812) -- `assert hasattr(delivery.totals, "video_completions")` tests the schema definition, not the business logic.
- **Evidence**: If you changed the adapter response to return 0 impressions or swapped media_buy_ids, these tests would still pass. They verify Pydantic model structure, not business behavior.
- **Recommended fix**: These tests map to specific obligation IDs (UC-004-MAIN-12, UC-004-MAIN-19) and may be intentionally lightweight "schema existence" tests. If so, they belong in unit tests, not integration tests that spin up PostgreSQL. Otherwise, add value-level assertions (e.g., verify specific impressions values or that the right media_buy_id appears).

### TQ-04: xfail tests without strict=True allow silent regressions
- **Severity**: Medium
- **Anti-pattern**: Assertion decay
- **Files**: `tests/integration/test_delivery_poll_behavioral.py` lines 36, 77, 119, 160, 1931, 1963, 2068, 2134, 2201, 2330, 2396, 2430; `tests/unit/test_delivery_service_behavioral.py` line 332
- **Description**: 12+ xfail tests lack `strict=True`. Without `strict=True`, if the production code is fixed and the test starts passing, pytest silently marks it as `xpass` instead of failing. This means implemented features could go undetected and the xfail marker never gets cleaned up.
- **Evidence**: The first four xfails (lines 36, 77, 119, 160) for WEBHOOK-PUSH-REPORTING-03 through -06 have no `strict` parameter. Compare with the well-marked ones at lines 1714 (`strict=True`), 1840 (`strict=True`), etc.
- **Recommended fix**: Add `strict=True` to all xfail markers. When a feature is implemented, the test should fail (xpass), forcing developers to remove the xfail and confirm the test now passes.

### TQ-05: Integration webhook tests still only mock external services -- good
- **Severity**: N/A (Positive finding)
- **Files**: `tests/integration/test_delivery_webhook_behavioral.py`, `tests/harness/delivery_webhook.py`
- **Assessment**: The WebhookEnv integration variant mocks only `requests.post`, `WebhookURLValidator.validate_webhook_url`, and `time.sleep`. These are genuinely external (HTTP calls, URL validation against DNS, and time delays). The unit variant additionally mocks `get_db_session` -- that's the key difference. The integration tests exercise the real DB path for delivery record tracking. This is the correct boundary.

### TQ-06: Circuit breaker unit tests test real logic, not mocks
- **Severity**: N/A (Positive finding)
- **File**: `tests/unit/test_delivery_service_behavioral.py:119-314`
- **Assessment**: The `TestCircuitBreakerOpensAfterRetriesExhausted` and `TestCircuitBreakerHalfOpenProbe` test classes instantiate real `CircuitBreaker` objects and test state transitions (CLOSED -> OPEN -> HALF_OPEN -> CLOSED) through the actual `record_failure()`, `record_success()`, and `can_attempt()` methods. No mocks needed for state machine logic. These are exemplary behavioral tests.

### TQ-07: DeliveryPollEnv adapter mock has a fallback that could mask bugs
- **Severity**: Medium
- **Anti-pattern**: Overly forgiving mock
- **File**: `tests/harness/delivery_poll.py:71-78`
- **Description**: The `_adapter_lookup` method falls back to `next(iter(self._adapter_responses.values()))` when a media_buy_id is not found in the configured responses dict. This means if a test creates 3 media buys but only configures 1 adapter response, all 3 will silently get the same response data. A test could assert "3 different deliveries returned" and pass, even though the adapter mock returned identical data for all three.
- **Evidence**: `DeliveryPollEnv._adapter_lookup` line 76: `return next(iter(self._adapter_responses.values()))`.
- **Recommended fix**: Either raise an error when an unconfigured media_buy_id is requested, or at least log a warning. The current fallback silently masks misconfigured tests. The unit variant (`delivery_poll_unit.py:84-92`) has the same issue.

### TQ-08: Integration test data setup is verbose and repetitive
- **Severity**: Low
- **Anti-pattern**: Boilerplate
- **File**: `tests/integration/test_delivery_poll_behavioral.py` (throughout)
- **Description**: Nearly every test method starts with the same 5-line pattern:
  ```python
  tenant = TenantFactory(tenant_id="t1")
  principal = PrincipalFactory(tenant=tenant, principal_id="p1")
  MediaBuyFactory(tenant=tenant, principal=principal, ...)
  env.set_adapter_response(...)
  ```
  This is repeated 50+ times across the 60 test functions. The `DeliveryPollEnv` constructor takes `tenant_id` and `principal_id` but does not auto-create the corresponding DB records.
- **Impact**: Low -- verbosity, not false confidence. Tests are correct.
- **Recommended fix**: Consider adding a `setup_standard_tenant()` or `create_tenant_with_principal()` convenience method to `DeliveryPollEnv` that creates the tenant + principal and returns them. This would reduce each test by 2-3 lines without hiding important setup.

### TQ-09: Meta-tests validate harness contract but miss error paths
- **Severity**: Medium
- **Anti-pattern**: Happy path only (for harness infrastructure)
- **Files**: `tests/harness/test_harness_delivery_poll.py`, `test_harness_delivery_webhook.py`, `test_harness_circuit_breaker.py`
- **Description**: The meta-tests verify happy paths (default env returns valid response, multiple buys work, adapter error propagates) but do not test:
  1. What happens if factory creation fails mid-env (e.g., duplicate tenant_id)
  2. What happens if `__exit__` is called without `__enter__` (misuse protection)
  3. What happens if the env is used after `__exit__` (use-after-close)
  4. Whether factory session unbinding works when an exception occurs during the test body
  5. Whether `_commit_factory_data()` works correctly when the session was already closed
- **Impact**: Medium -- the harness is infrastructure that all 79 integration tests depend on. An undetected harness bug would cause false passes across the board.
- **Recommended fix**: Add meta-tests for:
  - Double-enter protection (or document that it's not supported)
  - Exception during test body still cleans up (factories unbound, patches stopped)
  - Calling `call_impl()` without any factory data setup (should produce specific error, not crash)

### TQ-10: Unit test for auth failure recovery is sprawling with inline patches
- **Severity**: Medium
- **Anti-pattern**: Over-complex test with mixed unit/integration behavior
- **File**: `tests/unit/test_delivery_service_behavioral.py:331-448`
- **Test**: `test_auth_failure_blocks_delivery_until_credentials_reconfigured`
- **Description**: This is a 115-line test with 4 `patch()` context managers, creates `WebhookDelivery` objects inline, instantiates `CircuitBreaker` directly, and deliberately raises `AssertionError` at the end. While marked `xfail(strict=False)`, the `strict=False` combined with the explicit `raise AssertionError` makes this test's pass/fail behavior confusing. `strict=False` means it passes whether the production code implements the feature or not.
- **Evidence**: Line 339: `strict=False` -- test passes silently regardless of production behavior.
- **Recommended fix**: Either make it `strict=True` (which would correctly fail when the feature is implemented, forcing cleanup), or simplify the test to just verify the gap exists. The current 115-line version does extensive setup for a test that's expected to fail.

### TQ-11: Integration circuit breaker test uses inline patch (negating integration benefit)
- **Severity**: High
- **Anti-pattern**: Integration test without integration
- **File**: `tests/integration/test_delivery_service_behavioral.py:147-164`
- **Test**: `TestWebhookFailureNoSyncError::test_send_webhook_enhanced_catches_db_errors`
- **Description**: This test is in the integration test file and takes `integration_db` fixture, but it immediately patches `get_db_session` with `side_effect=Exception("DB connection refused")`. This completely bypasses the real database that `integration_db` provides. The test would produce identical results as a unit test.
- **Evidence**: Line 153: `patch("src.core.database.database_session.get_db_session", side_effect=Exception(...))` overrides the real DB.
- **Impact**: High -- this test provides the illusion of integration coverage while actually testing with a mock. It would be more honest in the unit test file.
- **Recommended fix**: Move to unit tests, or redesign to actually test the DB error path (e.g., stop the DB container, or corrupt the session). If testing that `_send_webhook_enhanced` catches exceptions is the goal, a unit test with explicit patch is the correct approach -- just don't put it in the integration file.

### TQ-12: Integration poll tests create real DB records but adapter data is still mocked
- **Severity**: Low (design choice, not a bug)
- **Anti-pattern**: Partial integration
- **File**: `tests/integration/test_delivery_poll_behavioral.py` (all tests)
- **Description**: Integration tests exercise real PostgreSQL for tenant/principal/media_buy resolution and real UoW/repository queries, but delivery metrics always come from the mocked adapter. This means the "adapter -> response -> aggregation" path is tested with synthetic adapter data. The DB integration proves that `MediaBuyUoW.get_by_principal()` works with real SQL, that tenant isolation is enforced, and that not-found errors are correctly generated from DB lookups.
- **Impact**: Low -- this is the correct integration boundary per the project architecture (adapters are external services). The adapter mock is explicitly documented in the harness. The alternative (mocking the adapter at the HTTP level with responses.mock) would provide marginal benefit for significant complexity.
- **Assessment**: This is a good design choice, not a flaw.

### TQ-13: xfail reasons are consistently well-documented
- **Severity**: N/A (Positive finding)
- **Files**: All files with xfail markers
- **Assessment**: Every xfail has a multi-line `reason=` string that explains: (1) what the production code currently does, (2) what the obligation requires, and (3) why there's a gap. Examples:
  - Line 37: "Production code does not auto-set notification_type based on delivery trigger."
  - Line 1716: "BUG salesagent-mq3n: _get_pricing_options casts string pricing_option_id to int"
  - Line 516: "Production code at media_buy_delivery.py catches adapter exceptions and logs via logger.error() but does NOT write to the AuditLog database table"
  These reasons serve as living documentation of known gaps and bugs. This is excellent practice.

### TQ-14: Integration tests are genuinely better than their unit predecessors
- **Severity**: N/A (Positive finding -- answers key review question)
- **Evidence by comparison**:
  - **Unit poll tests** (`tests/unit/test_delivery_poll_behavioral.py`): 13 tests remaining. These test lower-level functions (`_get_target_media_buys`, `_resolve_delivery_status_filter`) with inline `MagicMock` repos. They're appropriate as unit tests for pure logic.
  - **Integration poll tests** (`tests/integration/test_delivery_poll_behavioral.py`): 60 tests. These call `_get_media_buy_delivery_impl` with real DB records created via factories. They verify that the full chain works: factory -> ORM -> UoW -> business logic -> adapter call -> response assembly.
  - **Key difference**: The unit `_make_buy()` helper creates `MagicMock()` with manually wired attributes. If the ORM model adds a new required field, unit tests silently pass (MagicMock auto-creates attributes). Integration tests with `MediaBuyFactory` would fail if the model changes incompatibly. This is real additional coverage.
  - **Webhook migration**: All webhook tests moved from unit to integration. The integration versions test the same behaviors (retry backoff, HMAC signing, 401 no-retry) but through the real `deliver_webhook_with_retry` with real WebhookDelivery objects rather than through mocked-out functions. The key assertions (retry counts, sleep patterns, response codes) are preserved.

## Positive Examples

Tests that exemplify good testing practices:

- `tests/integration/test_delivery_poll_behavioral.py::TestMultipleMediaBuyDelivery::test_three_media_buys_returns_all_deliveries_and_aggregated_totals` (line 625) -- Creates 3 real DB records with distinct data, verifies each one returns correct individual metrics AND that aggregated totals sum correctly. Would catch real aggregation bugs.

- `tests/integration/test_delivery_poll_behavioral.py::TestPartialMediaBuyIdsNotFound::test_partial_ids_returns_found_buy_and_not_found_error` (line 242) -- Tests a realistic scenario (mixed valid/invalid IDs), verifies both the happy path (found buy has data) and error path (missing ID has error), and documents a spec conflict between BR-RULE-030 and ext-c.

- `tests/integration/test_delivery_webhook_behavioral.py::TestWebhook503RetryBackoff::test_503_triggers_retries_with_exponential_backoff` (line 218) -- Verifies retry count (4), backoff pattern (1s, 2s, 4s via `assert_has_calls`), HTTP call count (4), and sleep count (3). This test would catch changes to retry logic, backoff formula, or off-by-one errors.

- `tests/unit/test_delivery_service_behavioral.py::TestCircuitBreakerHalfOpenProbe` (line 222) -- Five focused state machine tests covering: timeout transition, pre-timeout stay-open, probe success path, probe failure reopens, and end-to-end lifecycle. Pure behavioral tests with zero mocks.

- `tests/harness/test_harness_delivery_webhook.py::TestWebhookEnvContract::test_signing_secret_flows_through` (line 82) -- Meta-test that verifies the harness correctly passes signing_secret through to the production function by checking the actual HTTP call args. Validates that the harness doesn't silently drop parameters.

- `tests/integration/test_delivery_poll_behavioral.py::TestInvalidDateRangeDoesNotFetchDeliveryData::test_invalid_date_range_does_not_call_adapter` (line 398) -- Beyond asserting error response, also verifies `adapter.get_media_buy_delivery.assert_not_called()`. This proves the read-only invariant -- invalid input should not cause any side effects.

## Summary

- Critical: 0
- High: 1 (integration test that mocks its integration point)
- Medium: 4 (assertion-light schema tests, missing strict=True on xfails, adapter mock fallback masking bugs, missing harness error path meta-tests)
- Low: 3 (dead webhook unit file, helper duplication, verbose test setup)

**Overall assessment**: The test harness is well-designed and the migration from unit to integration tests represents a genuine quality improvement. The integration tests exercise real PostgreSQL for data resolution while correctly mocking only the external adapter boundary. Assertions are predominantly meaningful -- they verify specific data values, error codes, retry counts, and aggregation math rather than just testing that "something returned." The xfail documentation is excellent and serves as a living spec-gap tracker. The main concerns are: (1) add `strict=True` to all xfail markers to prevent silent regressions, (2) the adapter mock fallback behavior could mask misconfigured tests, and (3) one integration test (`test_send_webhook_enhanced_catches_db_errors`) patches away its integration point and belongs in the unit suite.
