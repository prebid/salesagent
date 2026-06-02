# Test Review Patterns

Recurring patterns extracted from test code review history. Follow these
when writing or modifying tests.

## Vacuous / Tautological Assertions

Every assertion must be capable of failing when the code is wrong.

### OR Short-Circuit
```python
# WRONG — when X is True, Y never evaluates
assert not isinstance(response, CreateMediaBuyError) or all(
    pkg.status == "active" for pkg in response.packages
)

# CORRECT — split into two asserts
assert not isinstance(response, CreateMediaBuyError)
assert all(pkg.status == "active" for pkg in response.packages)
```

### hasattr on Concrete Object
```python
# WRONG — always True; method exists unconditionally on the class
assert hasattr(adapter, "get_products")

# CORRECT — assert something meaningful about behavior
result = adapter.get_products(brief="test")
assert result is not None
```

### Happy Path With Zero Assertions
```python
# WRONG — no assertion on the response
def test_create_media_buy():
    result = create_media_buy_impl(req)
    # ... nothing checked

# CORRECT — at minimum assert success
def test_create_media_buy():
    result = create_media_buy_impl(req)
    assert not isinstance(result, CreateMediaBuyError)
    assert result.media_buy_id is not None
```

### Always-True Inequality
```python
# WRONG — Pydantic model != dict is always True in Python
assert response != {"status": "active"}

# CORRECT — compare same representations
assert response.model_dump(mode="json") == {"status": "active"}
# or
assert response == ExpectedModel(status="active")
```

## Test DRY — Use Shared Helpers

When a helper exists in `tests/utils/` or `tests/helpers/`, use it.
Don't re-implement the same logic inline.

```python
# WRONG — hand-rolling setup that a factory already provides
tenant = Tenant(id="t1", name="test", ...)
principal = Principal(id="p1", tenant_id="t1", ...)

# CORRECT — use the shared factory
from tests.factories import TenantFactory, PrincipalFactory
tenant = TenantFactory()
principal = PrincipalFactory(tenant=tenant)
```

When adding a new test factory or fixture, check that existing tests in
the same file aren't still hand-rolling the same setup.

## BDD / Integration Assertion Strength

Don't weaken assertions when modifying BDD steps.

```python
# WRONG — OR where Gherkin implies AND
# Gherkin: "Then response contains error message AND field reference"
assert "error" in response or "field" in response  # OR lets one slide

# CORRECT — both conditions
assert "error" in response
assert "field" in response
```

- Don't replace field-identity checks (`response_names == registered_names`)
  with count checks (`len(response_names) == len(registered_names)`)
- Don't remove guards (`len(x) > 0`) before asserting on contents

## Error Tests — Assert on Wire Envelope, Not Reconstructed Exceptions

The test harness reconstructs `AdCPError` from wire responses, but this
reconstruction is lossy. Assert on the wire envelope as the primary authority.

```python
# WRONG — tests reconstruction, not the buyer-facing wire contract
assert isinstance(result.error, AdCPValidationError)
assert result.error.error_code == "VALIDATION_ERROR"

# CORRECT — tests actual wire shape
assert result.is_error
assert_envelope_shape(
    result.wire_error_envelope,
    "VALIDATION_ERROR",
    recovery="correctable",
)
```

Always verify the `recovery` field (`transient`, `correctable`, `terminal`)
— it drives buyer-agent retry semantics.

**Exception:** `_impl`-level unit tests (no wire involved) may use
`isinstance()` and `.error_code` since they test the raise site directly.
