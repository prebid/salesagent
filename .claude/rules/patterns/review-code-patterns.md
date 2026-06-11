# Code Review Patterns

Recurring patterns extracted from code review history. Follow these when
writing or modifying code.

## Hoisting — No Inline Imports Without Circular-Dep Justification

Move imports to module level unless a circular dependency requires deferral.

```python
# WRONG — inline import without circular dep
def process_order(order_id: str) -> None:
    from src.core.schemas import Order  # no circular dep exists
    ...

# CORRECT — module-level import
from src.core.schemas import Order

def process_order(order_id: str) -> None:
    ...

# ACCEPTABLE — circular dep requires deferral
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.schemas import Order
```

## Type Refinement — No `Any` Where a Concrete Type Is Known

If a function only ever receives or returns one specific type, annotate it.

```python
# WRONG — Any hides the real type
def get_errors(result: Any) -> list[Any]:
    return result.errors

# CORRECT — concrete types
def get_errors(result: CreateMediaBuyResult) -> list[Error]:
    return result.errors
```

Common violations:
- `product: Any` that only accesses `.product_id` → `Product | None`
- `list[Any]` that only contains `Error` objects → `list[Error]`
- `-> Any` on a function that always returns a `BaseModel` subclass → type it
- Use `TYPE_CHECKING` guard if the concrete type would cause a circular import

## Create/Update Path Parity

If a validator runs in `_create_*_impl`, it must also run in `_update_*_impl`
(and vice versa) unless the asymmetry is intentional and documented.

Asymmetric validation lets buyers bypass checks via the update path.

```python
# WRONG — create validates budget, update doesn't
async def _create_media_buy_impl(req, identity):
    validate_budget(req.budget, identity)
    validate_targeting(req.targeting)
    ...

async def _update_media_buy_impl(req, identity):
    validate_targeting(req.targeting)
    # Missing: validate_budget — buyer can bypass via update
    ...

# CORRECT — both paths validate
async def _update_media_buy_impl(req, identity):
    validate_budget(req.budget, identity)
    validate_targeting(req.targeting)
    ...
```

Also check: if the create path guards on `identity.principal_id` and
`identity.tenant_id`, the update/list/sync paths must do the same.

## Raw Dicts → Typed Constructors

When a model field is typed (e.g., `list[Error]`), pass typed instances,
not raw dicts.

```python
# WRONG — raw dict to a typed field
errors=[{"code": "FOO", "message": "bar"}]

# CORRECT — typed constructor
errors=[Error(code="FOO", message="bar")]
```

Also: both sides of a comparison must use the same representation.
DB-hydrated Pydantic models vs serialized dicts will always be `!=`.
Use `.model_dump(mode="json")` on both sides, or compare model-to-model.

## Dead / Unreachable Code

Remove code that can never execute:

1. **Unreachable exception branch**: `except SomeError` where `SomeError`
   cannot be raised by anything in the `try` block. Common: catching
   `ToolError` around TypeAdapter calls (which raise `ValidationError`).

2. **No-op transformation**: `json.loads(json.dumps(x))` where `x` is
   already plain JSON types — the round-trip changes nothing.

3. **Import of renamed/deleted symbol**: `from module import name` where
   `name` no longer exists in `module`.

## Redundant DB Queries

Don't query the same data twice on hot paths:

```python
# WRONG — two queries with same WHERE clause
count = repo.count_by_tenant(tenant_id)
items = repo.get_by_tenant(tenant_id)

# CORRECT — single query
items = repo.get_by_tenant(tenant_id, limit=expected + 1)
count = len(items)
```

Watch for `get_adapter()` and similar factory methods where a called
helper re-queries what the caller already loaded.

## `_impl` Functions — Raise Typed Exceptions, Don't Build Wire Shape

`_impl` functions raise typed `AdCPError` subclasses. Boundary translators
build the wire envelope via `build_two_layer_error_envelope()`.

```python
# WRONG — _impl building wire shape
def _create_impl(req):
    return {"errors": [{"code": "VALIDATION_ERROR", "message": msg}]}

# WRONG — raw ValueError where a typed subclass exists
def _create_impl(req):
    raise ValueError("budget must be positive")

# CORRECT — typed subclass with correct metadata
def _create_impl(req):
    raise AdCPBudgetTooLowError("budget must be positive")
```

Use the semantic subclass instead of `AdCPError(message, error_code="...")`
with an inline code string. The structural guard tests cap these sites.

## Project Conventions

- FIXME comments must carry a GitHub issue reference — `FIXME(#1234)`, not
  bare `FIXME` and not internal tool IDs (e.g. beads). Reviewers ask for the
  issue-tagged form so the revisit condition is trackable.
- No `# noqa: F401` on imports that are actually used
- No `--link` in Docker commands — use named networks
- Check `subprocess.run()` result `returncode` when the result drives a gate
