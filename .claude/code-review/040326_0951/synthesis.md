# Code Review Synthesis

**Scope**: Test harness infrastructure (`tests/harness/`), factories (`tests/factories/`), and integration tests (`tests/integration/test_delivery_*_behavioral.py`)
**Date**: 2026-03-04
**Agents**: review-architecture, review-dry, review-testing, review-python-practices

---

## Cross-Agent Findings (Deduplicated)

### Convergence: 3+ agents flagged the same issue

| Issue | Agents | Verdict |
|-------|--------|---------|
| `test_send_webhook_enhanced_catches_db_errors` uses inline `patch` bypassing harness | CR-08, TQ-11, PP-06 | **Confirmed** — integration test patches away its integration point. Should use CircuitBreakerEnv or document why it can't. |
| Global factory `_meta.sqlalchemy_session` mutation is not thread-safe | CR-03, PP-06, DRY-01 | **Confirmed** — safe in current sequential execution but fragile. Add assertion guard per PP-06 recommendation. |

### Convergence: 2 agents flagged the same issue

| Issue | Agents | Verdict |
|-------|--------|---------|
| `identity` property typed `-> Any` instead of `-> ResolvedIdentity` | PP-02, DRY-01 | **Confirmed** — erases type safety for all test callers. |
| Test setup boilerplate (tenant+principal+env) repeated 50+ times | DRY-05, TQ-08 | **Confirmed** — 3-4 lines repeated in every test. Fixture or env option would help. |
| Dead webhook unit file (0 test functions, 96 lines of helpers) | TQ-01, TQ-02 | **Confirmed** — `test_delivery_webhook_behavioral.py` fully migrated, file should be cleaned up. |
| Base class duplication IntegrationEnv / ImplTestEnv | DRY-01, PP-01/PP-02 | **Confirmed** — ~100 lines semantically duplicated. Merge with `use_real_db` flag. |

---

## Validated Findings by Severity

### Critical (2)

**DRY-01: Base classes are semantically duplicate** (DRY agent)
- `_base.py` IntegrationEnv vs `_base_unit.py` ImplTestEnv — ~100 lines duplicated
- Same `__init__`, identical `identity` property, same context manager lifecycle
- Only real difference: DB session binding + patch source (dict vs method)
- **Fix**: Single `BaseTestEnv` with `use_real_db: bool` class attribute

**DRY-02: Integration/unit poll env pair is ~80% identical** (DRY agent)
- `set_adapter_response`, `set_adapter_error`, `call_impl`, `_make_default_adapter_response`, `_adapter_lookup` — all identical across `delivery_poll.py` / `delivery_poll_unit.py`
- Bug fix in one would need replication in the other
- **Fix**: Shared `DeliveryPollMixin` containing fluent API methods

### High (3)

**DRY-03: Webhook env pair ~90% identical** (DRY agent)
- `set_http_status`, `set_http_sequence`, `set_http_error`, `set_url_invalid`, `call_deliver`, `call_impl` — byte-for-byte identical
- **Fix**: `WebhookMixin` with shared methods

**DRY-04: Circuit breaker env pair ~85% identical** (DRY agent)
- `get_service`, `get_breaker`, `set_http_response`, `call_send`, `call_impl` — identical
- **Fix**: `CircuitBreakerMixin` with shared methods

**TQ-11: Integration test patches away its integration point** (Testing agent)
- `test_send_webhook_enhanced_catches_db_errors` in integration file uses inline `with patch(...)` to mock `get_db_session` — the very thing integration tests should exercise
- Also flagged by CR-08 (architecture) and PP-06 (python practices)
- **Fix**: Use CircuitBreakerEnv with `set_db_webhooks()`, or move to unit file

### Medium (10) — Deduplicated

| ID | Finding | Agent | Action |
|----|---------|-------|--------|
| CR-01 | Unit CB env patches `get_db_session` at definition site instead of import site | Architecture | Verify import path; change to `f"{self.MODULE}.get_db_session"` |
| PP-01 | Use of private `_patch` type from `unittest.mock` | Python | Use `list[Any]` instead |
| PP-02 | `identity` property returns `Any` instead of `ResolvedIdentity` | Python | Add `TYPE_CHECKING` import, type return |
| PP-04 | `_db_session` attribute set without `__init__` declaration | Python | Declare in `__init__` |
| PP-08 | `__exit__` cleanup not wrapped in try/finally | Python | Add error collection pattern |
| DRY-05 | Test setup boilerplate repeated 50+ times | DRY | Fixture or env `auto_create_fixtures` option |
| DRY-07 | 4 tests bypass `set_adapter_response` for multi-package | DRY | Extend API to accept `by_package` list |
| TQ-04 | xfails missing `strict=True` | Testing | Add `strict=True` to catch silent fixes |
| TQ-07 | Adapter mock returns default even for unknown media_buy_ids | Testing | Return error/None for unregistered IDs |
| TQ-09 | No harness error path meta-tests | Testing | Test env behavior when factory creation fails |

### Low (10) — Deduplicated

| ID | Finding | Agent |
|----|---------|-------|
| PP-03 | `_session` typed `Any` instead of `Session \| None` | Python |
| PP-05 | `call_impl`/`call_send` return `Any` where concrete types known | Python |
| PP-07 | `_commit_factory_data` redundant with `"commit"` persistence | Python |
| PP-09 | `Mock` vs `MagicMock` inconsistency in `_mock_uow.py` | Python |
| PP-10 | `payload or {}` treats empty dict as falsy | Python |
| CR-02 | WebhookEnv docstring says "fails silently if no table" | Architecture |
| CR-04 | `sqlalchemy_session_persistence = "commit"` auto-commits each call | Architecture |
| DRY-06 | Inline imports in test methods (57 occurrences) | DRY |
| DRY-08 | Meta-test structural patterns repeated | DRY |
| TQ-03 | Some schema tests are assertion-light | Testing |

---

## False Positives / Not Actionable

| ID | Finding | Why Not Actionable |
|----|---------|-------------------|
| CR-05 | DeliveryPollEnv bypasses repository pattern | Correct by design — only patches adapter |
| CR-06 | Unit WebhookEnv patches get_db_session, integration doesn't | Correct by design — that's the point |
| CR-09 | Production _impl imports Context at module level | Out of scope — pre-existing, noted only |
| TQ-10 | Sprawling xfail test | Subjective — test is comprehensive, not sprawling |
| DRY-08 | Meta-test structural patterns | Intentional per-env contract testing |
| DRY-06 | Inline imports | May be intentional for obligation test generator self-containment |

---

## Recommended Action Plan

### Phase 1: DRY refactoring (removes ~270 lines)
1. Merge `IntegrationEnv` + `ImplTestEnv` into single `BaseTestEnv` (DRY-01)
2. Extract `DeliveryPollMixin`, `WebhookMixin`, `CircuitBreakerMixin` (DRY-02/03/04)
3. Integration/unit envs become thin subclasses differing only in patch targets + DB mode

### Phase 2: Quick wins (type safety + correctness)
4. Type `identity` property as `-> ResolvedIdentity` (PP-02)
5. Replace `_patch` with `list[Any]` (PP-01)
6. Add `__init__` declaration for `_db_session` (PP-04)
7. Add assertion guard for nested IntegrationEnv (PP-06/CR-03)
8. Add `strict=True` to xfails (TQ-04)
9. Fix adapter mock to return error for unknown IDs (TQ-07)

### Phase 3: Test quality
10. Move or rewrite `test_send_webhook_enhanced_catches_db_errors` (TQ-11/CR-08)
11. Clean up dead webhook unit file (TQ-01/TQ-02)
12. Extend `set_adapter_response` for multi-package (DRY-07)
13. Add harness error path meta-tests (TQ-09)

### Phase 4: Nice-to-haves
14. Wrap `__exit__` cleanup in try/finally (PP-08)
15. Add concrete return types to `call_impl`/`call_send` (PP-05)
16. Evaluate test setup fixture (DRY-05)

---

## Summary Counts (Deduplicated)

| Severity | Count |
|----------|-------|
| Critical | 2 |
| High | 3 |
| Medium | 10 |
| Low | 10 |
| Not Actionable | 6 |
| **Total unique findings** | **25** |

No security vulnerabilities, no data integrity risks, no production code issues.
The harness is architecturally sound — findings are about DRY, type safety, and test quality refinements.
