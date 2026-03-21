---
name: derive-tests
description: >
  Derive obligation tests using the test harness. First checks for harness
  availability, then generates compact tests using domain-specific test
  environments. Hard gate: no harness -> no tests (stops immediately).
  Replaces obligation-test for harness-enabled domains.
args: <obligation-ids-or-prefix> [--count N]
---

# Harness-Based Obligation Test Derivation

Write one behavioral test per obligation using the test harness (`tests/harness/`).
Tests use domain-specific test environments instead of inline `@patch` decorators,
producing 10-15 line tests instead of 40-50 line tests.

## Args

```
/derive-tests UC-004-MAIN-01 UC-004-MAIN-02
/derive-tests UC-004 --count 10
/derive-tests UC-001-MAIN --count 15
```

**Direct IDs**: Space-separated obligation IDs.
**Prefix mode**: Auto-selects uncovered obligations matching the prefix.

## Available Harness Environments

| Domain | Env Class | Tests | Production Function |
|--------|-----------|-------|---------------------|
| Delivery poll | `DeliveryPollEnv` | `test_delivery_poll_behavioral.py` | `_get_media_buy_delivery_impl` |
| Webhook delivery | `WebhookEnv` | `test_delivery_webhook_behavioral.py` | `deliver_webhook_with_retry` |
| Circuit breaker | `CircuitBreakerEnv` | `test_delivery_service_behavioral.py` | `WebhookDeliveryService`, `CircuitBreaker` |
| Creative sync | `CreativeSyncEnv` | `test_creative_sync_behavioral.py` | `_sync_creatives_impl` |
| Creative list | `CreativeListEnv` | `test_creative_list_behavioral.py` | `_list_creatives_impl` |
| Creative formats | `CreativeFormatsEnv` | `test_creative_formats_behavioral.py` | `_list_creative_formats_impl` |

**Multi-transport support:** Creative envs support `call_via(transport)` dispatch
across IMPL, A2A, and REST transports. See [Multi-Transport Pattern](#multi-transport-pattern).

## Protocol

### Step 0: Resolve obligation IDs

If args look like a prefix (no trailing `-NN` sequence number):

```bash
python3 -c "
import json
al = json.loads(open('tests/unit/obligation_coverage_allowlist.json').read())
matches = sorted(oid for oid in al if oid.startswith('{prefix}'))
print(' '.join(matches[:N]))
print(f'Total matching: {len(matches)}, selected: {min(N, len(matches))}')
"
```

### Step 1: Harness gate (HARD STOP)

Check if a harness exists for the target domain:

```bash
ls tests/harness/*.py
```

Map obligation prefix to required harness. For the complete, auto-generated list
of all env classes with their methods, read `.agent-index/harness/envs.pyi`.

**If harness missing: STOP IMMEDIATELY.** Print:
```
No harness found for {domain}. Create tests/harness/{domain}.py first.
See tests/harness/_base.py for the IntegrationEnv pattern.
```

Do NOT fall back to inline mocking. Generating tests without a harness
produces architecturally poor code that wastes tokens.

**If harness exists: CONTINUE.**

### Step 2: Read harness API + gold standard

1. **Read the harness class** for available methods:
   ```bash
   # Example for delivery poll:
   head -60 tests/harness/delivery_poll.py
   ```
   Extract: class docstring, fluent API methods, call_impl signature.

2. **Read the meta-test file** for usage examples:
   ```bash
   # Example:
   cat tests/harness/test_harness_delivery_poll.py
   ```
   These are your gold standard — tests use the exact same pattern.

3. **Read 1-2 converted tests** in the target file for style reference:
   ```bash
   # Look for tests already using the harness:
   grep -A 15 "DeliveryPollEnv\|WebhookEnv\|CircuitBreakerEnv" tests/unit/test_delivery_*_behavioral.py
   ```

### Step 3: Research obligation

For each obligation:

1. **Read the scenario** from `docs/test-obligations/`:
   ```bash
   grep -A 10 "{OID}" docs/test-obligations/*.md
   ```
   Extract: Given/When/Then, business rule, priority, layer.

2. **Translate Given/When/Then directly into the test**:
   - **Given** → test setup (fixtures, env configuration)
   - **When** → action (call production function)
   - **Then** → assertions (expected output/state)

   The BDD spec is the **sole source** of expected behavior. Do NOT derive
   assertions from what the production code currently does. If the spec says
   "Then a SyncCreativesSubmitted is returned," assert `isinstance(result,
   SyncCreativesSubmitted)` — even if the code currently returns something
   else or doesn't implement the behavior at all.

3. **Check if production code implements it**: Locate the `_impl` function.
   - **Implemented** → test should PASS. If it doesn't, the code has a bug.
   - **Not implemented** → test MUST still assert spec behavior, marked with
     `@pytest.mark.xfail(strict=True, reason="<what's missing>")`.
     Never write a test that asserts current (wrong) behavior just because
     the spec behavior doesn't exist yet. That legitimizes the gap.

### Step 4: Write test

Write ONE test following all 7 hard rules:

| # | Rule | Check |
|---|------|-------|
| 1 | Import from `src.` | `from src.` in test file |
| 2 | Call production function | Test calls `env.call_impl()` or production function |
| 3 | Assert production output | Assertion checks a value from the production call |
| 4 | `Covers: {OID}` tag | Docstring contains exactly `Covers: {OID}` |
| 5 | Use harness env | Test uses harness context manager, not inline `@patch` |
| 6 | Not mock-echo only | Does more than verify `mock.called` |
| 7 | **No inline @patch** | MUST NOT define `@patch` decorators or inline UoW setup |

**Test template (delivery poll — integration, preferred):**

```python
import pytest
from tests.factories import TenantFactory, PrincipalFactory, MediaBuyFactory

@pytest.mark.requires_db
class TestObligationName:
    """Short description of what's being tested.

    Covers: {OID}
    """

    def test_specific_behavior(self, integration_db):
        """What specific behavior is verified.

        Covers: {OID}
        """
        from tests.harness import DeliveryPollEnv

        with DeliveryPollEnv() as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            buy = MediaBuyFactory(tenant=tenant, principal=principal)
            env.set_adapter_response(buy.media_buy_id, impressions=5000)
            response = env.call_impl(media_buy_ids=[buy.media_buy_id])
            assert response.some_field == expected_value
```

**Test template (delivery poll — unit, backward compat):**

```python
class TestObligationName:
    """Short description.

    Covers: {OID}
    """

    def test_specific_behavior(self):
        """What is verified.

        Covers: {OID}
        """
        from tests.harness.delivery_poll_unit import DeliveryPollEnv

        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_001", ...)
            env.set_adapter_response("mb_001", impressions=5000)
            response = env.call_impl(media_buy_ids=["mb_001"])
            assert response.some_field == expected_value
```

**Test template (webhook):**

```python
class TestObligationName:
    """Short description.

    Covers: {OID}
    """

    def test_specific_behavior(self):
        """What is verified.

        Covers: {OID}
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(503, "Unavailable")
            success, result = env.call_deliver(max_retries=3)
            assert success is False
            assert result["attempts"] == 3
```

**Test template (circuit breaker):**

```python
class TestObligationName:
    """Short description.

    Covers: {OID}
    """

    def test_specific_behavior(self):
        """What is verified.

        Covers: {OID}
        """
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            breaker = env.get_breaker(failure_threshold=3)
            for _ in range(3):
                breaker.record_failure()
            assert breaker.state == CircuitState.OPEN
```

### Step 5: Run and fix

```bash
uv run pytest <test_file>::<TestClass>::<test_method> -x -v
```

- PASS or XFAIL = proceed
- ERROR = fix (import/name errors, wrong harness method)
- If the harness doesn't expose what you need, use `env.mock["name"]`
  to access the underlying mock directly

### Step 6: Verify

Six mechanical checks:

1. `grep "from src\." <file>` > 0
2. `grep "Covers: {OID}" tests/` count == 1
3. Test runs without ERROR
4. `make quality` passes
5. No duplicate Covers tags
6. If test PASSES: remove OID from allowlist, add file to `_UNIT_ENTITY_FILES`
   if needed, run obligation guard

### Step 7: Commit

```bash
git add <test_file> tests/unit/obligation_coverage_allowlist.json
git add tests/unit/test_architecture_obligation_coverage.py  # if changed
git commit -m "test: add obligation test for {OID}"
```

## Multi-Transport Pattern

When an obligation has transport variants in upstream BDD (e.g., both
`T-UC-006-main-rest` and `T-UC-006-main-mcp`), generate ONE parametrized test
instead of two separate tests.

**Decision rule:**
1. Check if upstream BDD duplicates the scenario across REST/MCP transports
2. If yes → parametrize with `ALL_TRANSPORTS`
3. If no → use single transport (`Transport.IMPL`)

**Template (multi-transport — creative domains):**

```python
import pytest
from tests.factories import TenantFactory, PrincipalFactory
from tests.harness import CreativeSyncEnv, Transport, assert_envelope

ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.REST]

@pytest.mark.requires_db
class TestObligationName:
    """Short description.

    Covers: T-UC-006-xxx-rest, T-UC-006-xxx-mcp
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_specific_behavior(self, integration_db, transport):
        """What is verified.

        Covers: T-UC-006-xxx-rest, T-UC-006-xxx-mcp
        """
        with CreativeSyncEnv() as env:
            # SHARED FIXTURE
            TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant_id="test_tenant", principal_id="test_principal")

            # TRANSPORT DISPATCH
            result = env.call_via(transport, creatives=[...])

        # ENVELOPE (transport-specific)
        if transport == Transport.REST:
            assert_envelope(result, Transport.REST)

        # PAYLOAD (shared — identical for all transports)
        assert result.is_success
        assert result.payload.creatives[0].action == expected_action
```

**Transport-specific behaviors** (e.g., REST returns 401, MCP raises ToolError)
get separate non-parametrized tests — don't mix them into the shared test.

### Creative Harness API

```python
from tests.harness import CreativeSyncEnv, CreativeListEnv, CreativeFormatsEnv, Transport

# Sync creatives (multi-transport)
with CreativeSyncEnv() as env:
    env.set_registry_formats([...])
    result = env.call_via(Transport.REST, creatives=[...], dry_run=True)
    # result.is_success, result.payload, result.envelope

# List creatives (multi-transport)
with CreativeListEnv() as env:
    result = env.call_via(Transport.A2A, media_buy_id="mb_001")

# List formats (multi-transport)
with CreativeFormatsEnv() as env:
    env.set_registry_formats([...])
    result = env.call_via(Transport.IMPL)
```

All three envs support: `call_via(transport)`, `call_impl()`, `call_a2a()`,
`build_rest_body()`, `parse_rest_response()`.

## Test File Selection

| Domain | File |
|--------|------|
| UC-004 delivery poll | `test_delivery_poll_behavioral.py` |
| UC-004 webhook | `test_delivery_webhook_behavioral.py` |
| UC-004 service/CB | `test_delivery_service_behavioral.py` |
| UC-002 | `test_create_media_buy_behavioral.py` |
| UC-003 | `test_update_media_buy_behavioral.py` |
| UC-006 sync (multi-transport) | `test_creative_sync_transport.py` |
| UC-006 sync (impl only) | `test_creative_sync_behavioral.py` |
| UC-006 list (impl only) | `test_creative_list_behavioral.py` |
| UC-006 formats (impl only) | `test_creative_formats_behavioral.py` |

New files must be added to `_UNIT_ENTITY_FILES` in
`tests/unit/test_architecture_obligation_coverage.py`.

## Harness API Quick Reference

### DeliveryPollEnv

```python
from tests.harness import DeliveryPollEnv

with DeliveryPollEnv(principal_id="p1", tenant_id="t1") as env:
    # Add mock media buys to the repository
    env.add_buy(media_buy_id="mb_001", buyer_ref="ref_001",
                start_date=date(2025,1,1), end_date=date(2027,12,31),
                budget=10000.0, currency="USD", raw_request={...})

    # Configure adapter response for a media buy
    env.set_adapter_response("mb_001", impressions=5000, spend=250.0,
                             package_id="pkg_001", clicks=100)

    # Or make the adapter fail
    env.set_adapter_error(Exception("Adapter unavailable"))

    # Configure pricing options
    env.set_pricing_options({"1": mock_pricing_option})

    # Call the production function
    response = env.call_impl(media_buy_ids=["mb_001"],
                             buyer_refs=["ref_001"],
                             start_date="2025-01-01",
                             end_date="2025-12-31",
                             status_filter=["active"])

    # Access underlying mocks if needed
    env.mock["uow"]        # MediaBuyUoW class mock
    env.mock["principal"]   # get_principal_object mock
    env.mock["adapter"]     # get_adapter mock
    env.mock["pricing"]     # _get_pricing_options mock
```

### WebhookEnv

```python
from tests.harness import WebhookEnv

with WebhookEnv() as env:
    env.set_http_status(200)                              # Single response
    env.set_http_sequence([(503, "Err"), (200, "OK")])    # Sequence
    env.set_http_error(ConnectionError("refused"))        # Exception
    env.set_url_invalid("Private IP")                     # SSRF check fail

    success, result = env.call_deliver(
        webhook_url="https://example.com/hook",
        payload={"event": "delivery.update"},
        signing_secret="secret",
        max_retries=3,
    )

    env.mock["post"]       # requests.post mock
    env.mock["validate"]   # URL validator mock
    env.mock["sleep"]      # time.sleep mock
    env.mock["db"]         # get_db_session mock
```

### CircuitBreakerEnv

```python
from tests.harness import CircuitBreakerEnv
from src.services.webhook_delivery_service import CircuitState

with CircuitBreakerEnv() as env:
    breaker = env.get_breaker(failure_threshold=5, timeout_seconds=60)
    service = env.get_service()
    env.set_http_response(200)
    env.set_db_webhooks([env.make_webhook_config(url="https://...")])
    result = env.call_send(media_buy_id="mb_001", impressions=1000)

    env.mock["client"]     # httpx.Client mock
    env.mock["sleep"]      # time.sleep mock
    env.mock["random"]     # random.uniform mock
    env.mock["db"]         # get_db_session mock
```

## xfail Policy

When production code doesn't implement the tested behavior:

```python
@pytest.mark.xfail(strict=True, reason="<what's missing> (salesagent-xxxx)")
def test_name(self):
    """... Covers: {OID} ..."""
    # Assert the SPEC-DEFINED behavior, not current behavior
```

Always use `strict=True` — when someone implements the feature, the xfail
will break the build and remind them to remove the marker.

## Iron Rule: BDD Spec Is the Sole Source of Assertions

**The Given/When/Then from the obligation spec IS the test.** Period.

- **Given** → test setup
- **When** → call production function
- **Then** → assertions

Do NOT adapt assertions to match current implementation. If the spec says
"Then a SyncCreativesSubmitted is returned" but the code returns
SyncCreativesResponse, assert SyncCreativesSubmitted and mark xfail.
A test that asserts current (wrong) behavior is WORSE than no test — it
legitimizes the gap and makes it invisible.

**The only valid reason to read production code** is to understand HOW to
set up the test (what parameters to pass, what mocks to configure). The
production code NEVER determines WHAT to assert — only the spec does.

## Anti-Patterns

- Don't skip the harness gate — inline mocking wastes tokens
- Don't use `@patch` decorators when a harness env is available
- Don't copy helper functions (_make_buy, _make_adapter_response) — use env methods
- Don't assert `mock.called` as the primary assertion (Rule 6)
- Don't drop obligations because "the code doesn't do this" — use xfail
- **Don't write tests that mirror current behavior instead of spec behavior** —
  this is the most dangerous anti-pattern. It makes gaps invisible by
  producing passing tests that verify the system "behaves somehow" rather
  than "behaves correctly"
- Don't exclude obligations as "not implemented" — that IS the gap to surface

## When to Use This vs /obligation-test

Use `/derive-tests` when:
- A harness exists for the target domain (check `tests/harness/*.py`)
- You want compact, architecturally clean tests

Use `/obligation-test` when:
- No harness exists for the target domain
- The obligation tests a helper function directly (not via _impl)
- The test needs patches not covered by any harness

## See Also

- `.agent-index/harness/envs.pyi` — all domain env classes with methods (auto-generated)
- `.agent-index/harness/base.pyi` — BaseTestEnv + IntegrationEnv interface (auto-generated)
- `.agent-index/factories.pyi` — all factory_boy factories + helpers (auto-generated)
- `tests/harness/_base.py` — BaseTestEnv + IntegrationEnv base classes
- `tests/harness/_mixins.py` — Domain mixins (DeliveryPollMixin, WebhookMixin, CircuitBreakerMixin)
- `tests/harness/test_harness_*.py` — Gold standard usage examples
- `/obligation-test` — Fallback for domains without harness
