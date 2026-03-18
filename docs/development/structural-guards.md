# Structural Guards

Automated architecture enforcement tests that run on every `make quality`.
Each guard uses AST scanning and introspection to detect violations at the
source level — no runtime execution of business logic needed.

## Why These Exist

During the adcp 3.2 → 3.6 migration, several classes of bugs appeared that
shared a common trait: they were invisible at review time and only surfaced
as silent runtime failures. Examples:

- A schema class copied fields from the adcp library instead of inheriting,
  then drifted out of sync when the library updated a field type
- An MCP wrapper accepted a new parameter but forgot to pass it through to
  the shared `_impl` function — callers could set the value but it was silently
  discarded
- A database query filtered an Integer PK column with string values from JSON,
  returning 0 rows instead of raising an error

These failures are difficult to catch in code review because the code _looks_
correct. The guards make these structural invariants machine-checkable.

## Design Principles

**Allowlists shrink, never grow.** Every guard has a set of known violations
(existing code that predates the guard). New code that introduces a violation
fails CI immediately. When an existing violation is fixed, the stale-allowlist
test forces you to remove the entry.

**FIXME comments link to beads tasks.** Every allowlisted violation has a
corresponding `# FIXME(salesagent-xxxx)` comment at the source location,
linking to a tracked issue.

**AST scanning, not runtime execution.** Guards parse Python source with the
`ast` module. They don't import or execute business logic, so they run fast
and can't be affected by runtime state.

**Introspection for type hierarchies.** Where AST alone is insufficient (e.g.,
checking class MRO), guards use `inspect` and `importlib` on the already-imported
modules.

## Guard Inventory

### Pre-existing Guards

| Test File | What It Enforces |
|-----------|-----------------|
| `test_no_toolerror_in_impl.py` | `_impl` functions raise `AdCPError`, never `ToolError` from FastMCP |
| `test_transport_agnostic_impl.py` | `_impl` functions have zero transport imports (no fastmcp, a2a, starlette) |
| `test_impl_resolved_identity.py` | `_impl` functions accept `ResolvedIdentity`, not `Context`/`ToolContext` |

These three guards enforce Critical Pattern #5: shared `_impl` functions are
transport-agnostic. They don't know whether they're called from MCP, A2A, or
a REST endpoint.

### Schema Inheritance Guard

**File:** `tests/unit/test_architecture_schema_inheritance.py`

**What it enforces:** Every Pydantic schema in `src/core/schemas.py` that has
a corresponding adcp library type must inherit from it.

**Why it matters:** The codebase follows Critical Pattern #1 — extend library
schemas via inheritance, never duplicate fields. If someone copies fields
instead of inheriting, the local copy drifts when adcp updates the field type,
default, or validator.

#### How it works

The guard scans `schemas.py` for imports using the `Library*` alias convention:

```python
from adcp.types import Product as LibraryProduct
from adcp.types import Signal as LibrarySignal
```

For each `LibraryX` import, it expects a local class `X` that has `LibraryX`
in its MRO (method resolution order):

```python
class Product(LibraryProduct):                # CORRECT: inherits
    implementation_config: dict | None = None  # internal-only field

class Product(SalesAgentBaseModel):           # WRONG: copied, will drift
    name: str
    channels: list[Channel]
```

#### Tests

| Test | What It Checks |
|------|---------------|
| `test_all_library_types_have_local_subclass` | Local class inherits from its `Library*` counterpart (via `inspect.getmro`) |
| `test_no_field_redefinition_in_subclasses` | Local class doesn't redeclare fields that already exist on the parent |

#### Field redefinition and known overrides

Even with correct inheritance, a subclass can accidentally redeclare a parent
field. This is usually a copy-paste error — the field is inherited anyway.
The guard uses AST to find fields declared directly in each class body (not
inherited), then flags any overlap with the parent's `model_fields`.

Some redeclarations are intentional. Critical Pattern #4 (nested serialization)
requires parent models to re-declare list fields using local subclass types:

```python
class Signal(LibrarySignal):
    # Intentional override: local SignalDeployment has extra fields
    deployments: list[SignalDeployment] = []  # overrides LibrarySignal.deployments

    # New internal field (not an override)
    tenant_id: str = Field(exclude=True)
```

These intentional overrides are listed in `KNOWN_OVERRIDES` inside the test file.
Currently 27 entries, mostly for nested serialization.

### Boundary Completeness Guard

**File:** `tests/unit/test_architecture_boundary_completeness.py`

**What it enforces:** When an `_impl` function accepts a parameter, both its
MCP wrapper and A2A wrapper must pass that parameter at the call site.

**Why it matters:** The codebase follows Critical Pattern #5 — every tool has
a shared `_impl` function called by both MCP and A2A wrappers. If a wrapper
doesn't forward a parameter, that transport layer silently loses access to
the functionality.

#### How it works

The guard maintains a registry of all `_impl` functions:

```python
IMPL_REGISTRY = [
    ("src.core.tools.media_buy_create", "_create_media_buy_impl"),
    ("src.core.tools.creatives._sync", "_sync_creatives_impl"),
    # ... 13 total
]
```

For each `_impl`:

1. **Get the signature** via `inspect.signature()` to find all parameter names
2. **Derive wrapper names** from the `_impl` name:
   - `_create_media_buy_impl` → MCP: `create_media_buy`, A2A: `create_media_buy_raw`
3. **Parse the wrapper file's AST** to find the wrapper function, then locate
   the `_impl(...)` call inside it
4. **Extract the keyword arguments** actually passed at the call site
5. **Flag any `_impl` parameter** not present in the call arguments

#### Example of what it catches

```python
# _impl accepts push_notification_config:
async def _create_media_buy_impl(
    req, push_notification_config=None, identity=None, context_id=None
): ...

# MCP wrapper forgets to pass it:
@mcp.tool()
async def create_media_buy(...):
    return await _create_media_buy_impl(
        req=req,
        identity=identity,
        context_id=context_id,
        # push_notification_config is MISSING — MCP callers can never use it
    )
```

#### Tests

| Test | What It Checks |
|------|---------------|
| `test_mcp_wrappers_pass_all_impl_params` | Every MCP wrapper passes all `_impl` parameters |
| `test_a2a_wrappers_pass_all_impl_params` | Every A2A wrapper passes all `_impl` parameters |
| `test_known_violations_are_still_violations` | Allowlisted violations haven't been fixed (stale entry detection) |

#### Current known violations (3)

| Wrapper | Missing Parameter | Tracked By |
|---------|------------------|------------|
| `create_media_buy` (MCP) | `push_notification_config` | salesagent-v0kb |
| `create_media_buy_raw` (A2A) | `context_id` | salesagent-v0kb |
| `update_media_buy_raw` (A2A) | `context_id` | salesagent-v0kb |

### Query Type Safety Guard

**File:** `tests/unit/test_architecture_query_type_safety.py`

**What it enforces:** Database queries must use Python types matching the
SQLAlchemy column type. Specifically: don't pass string values to Integer PK
columns.

**Why it matters:** When JSON data arrives at the API boundary, IDs are strings
(`"42"`). If these strings are passed directly to `.in_()` or `filter_by()` on
an Integer column, the behavior is database-dependent — PostgreSQL may do an
implicit cast, but some paths return 0 rows silently.

#### How it works

The guard catalogs all models with Integer primary keys:

```python
INTEGER_PK_MODELS = {
    "PricingOption": "id",
    "SyncJob": "sync_id",
    "AuditLog": "log_id",
    # ... 18 total
}
```

It then scans 12 source files for two AST patterns:

1. **`.in_()` on Integer PK columns:** `PricingOption.id.in_(some_list)` — the
   argument type can't be verified statically, so every occurrence is flagged
   for review
2. **String literals in `filter_by()`:** `filter_by(id="42")` — this is always
   a bug

#### Example of what it catches

```python
def _get_pricing_options(pricing_option_ids: list[Any]):
    # pricing_option_ids come from JSON — they're strings like ["42", "99"]
    # PricingOption.id is an Integer column
    stmt = select(PricingOption).where(
        PricingOption.id.in_(pricing_option_ids)  # FLAGGED: strings → Integer column
    )
```

The fix is to cast at the boundary: `[int(x) for x in pricing_option_ids]`.

#### Tests

| Test | What It Checks |
|------|---------------|
| `test_no_in_queries_on_integer_pk_with_wrong_type` | No new `.in_()` calls on Integer PK columns without review |
| `test_no_string_literals_in_filter_by_for_integer_pks` | No `filter_by(id="string")` patterns |
| `test_known_violations_still_exist` | Allowlisted violations haven't been fixed (stale entry detection) |

#### Current known violations (1)

| File | Pattern | Tracked By |
|------|---------|------------|
| `media_buy_delivery.py` | `PricingOption.id.in_(string_list)` | salesagent-mq3n |

### No model_dump() in _impl Guard

**File:** `tests/unit/test_architecture_no_model_dump_in_impl.py`

**What it enforces:** `_impl` functions must not call `.model_dump()` or
`.model_dump_internal()`. Serialization is the transport wrapper's job.

**Why it matters:** When business logic calls `model_dump()`, it takes on
responsibility for serialization format (JSON mode, aliases, exclude rules).
This couples the _impl layer to a specific output format. The transport
wrapper should receive a model object and decide how to serialize it.

#### How it works

The guard scans all `*_impl()` functions under `src/core/tools/` using AST,
looking for method calls where the method name is `model_dump` or
`model_dump_internal`.

#### Tests

| Test | What It Checks |
|------|---------------|
| `test_no_new_model_dump_violations` | No new `.model_dump()` calls beyond the allowlist |
| `test_known_violations_not_stale` | Allowlisted violations haven't been fixed (stale entry detection) |
| `test_violation_count_documented` | Total count matches allowlist (catches both directions) |

#### Current known violations (29)

| File | Count | Primary Use |
|------|-------|-------------|
| `media_buy_update.py` | 23 | `response_data=X.model_dump()` for workflow step storage |
| `media_buy_create.py` | 4 | `raw_request=req.model_dump()` for DB storage + workflow |
| `products.py` | 1 | `filters.model_dump()` in logging |
| `creatives/listing.py` | 1 | `filters.model_dump()` for dict conversion |

20 of the 29 violations are `response_data=response.model_dump(mode="json")`
calls that serialize workflow step responses for DB storage. These should be
replaced with typed repository methods that accept model objects directly.

### Repository Pattern Guard

**File:** `tests/unit/test_architecture_repository_pattern.py`

**What it enforces:** Two invariants:

1. **No `get_db_session()` in business logic.** Functions in `_impl` files must
   not call `get_db_session()` directly — data access belongs in repository classes.
2. **No `session.add()` in integration tests.** Test functions must not construct
   ORM objects inline — use polyfactory-based fixtures instead.

**Why it matters:** When business logic directly opens database sessions, it
becomes impossible to test without a real database, impossible to swap storage
backends, and impossible to enforce consistent transaction boundaries. Similarly,
when tests scatter `session.add()` calls through test bodies, fixture setup is
duplicated, brittle, and hard to maintain.

#### How it works

The guard scans 14 production files and 10 integration test files using AST:

**Invariant 1** finds function definitions that contain `get_db_session()` calls
(both `get_db_session()` and `module.get_db_session()` forms):

```python
# FLAGGED: business logic opens its own session
async def _create_media_buy_impl(req, identity):
    with get_db_session() as session:   # ← violation
        media_buy = MediaBuy(...)
        session.add(media_buy)

# CORRECT: repository encapsulates data access
async def _create_media_buy_impl(req, identity, repo: MediaBuyRepository):
    media_buy = repo.create_from_request(req, identity)
```

**Invariant 2** finds test functions/fixtures that call `session.add()`,
`db_session.add()`, or similar patterns:

```python
# FLAGGED: inline fixture setup
def test_something(integration_db):
    with get_db_session() as session:
        tenant = Tenant(name="test")
        session.add(tenant)             # ← violation

# CORRECT: factory-based fixture
def test_something(integration_db, sample_tenant):
    # sample_tenant created by polyfactory fixture
    pass
```

#### Tests

| Test | What It Checks |
|------|---------------|
| `test_no_new_get_db_session_in_impl` | No new `get_db_session()` calls outside the allowlist |
| `test_allowlist_entries_still_exist` (impl) | Stale allowlist detection for impl violations |
| `test_no_new_session_add_in_tests` | No new `session.add()` calls outside the allowlist |
| `test_allowlist_entries_still_exist` (tests) | Stale allowlist detection for test violations |

#### Current known violations

- **27 `get_db_session()` calls** across 10 production files (media_buy_create, update, delivery, list, products, creatives, task_management, admin blueprints)
- **58 `session.add()` calls** across 10 integration test files

All tracked by `salesagent-qo8a`.

### Obligation Test Quality Guard

**File:** `tests/unit/test_architecture_obligation_test_quality.py`

**What it enforces:** Every test tagged with `Covers: <obligation-id>` must
actually CALL production code from `src.*`, not just import it.

**Why it matters:** A test with a `Covers:` tag claims to verify a behavioral
contract. If the test body only imports a function without calling it, it
inflates coverage metrics without providing assurance. This catches the gaming
pattern: `from src.core.tools import _impl  # noqa: F401` with no actual call.

#### How it works

The guard scans obligation-tagged test files using AST:

1. Finds all `test_*` functions whose docstring contains `Covers: <id>`
2. For non-xfail tests: checks for `ast.Call` nodes that invoke production
   names (imported from `src.*`, `tests.harness.*`, `tests.helpers.*`, or
   `tests.factories.*`). Transitivity is handled — calling a helper that
   calls production code counts.
3. For xfail tests (stubs): weaker check — must at least import from `src.*`
   to show intent, but doesn't need to call it (the function may not exist yet).

#### Tests

| Test | What It Checks |
|------|---------------|
| `test_no_new_sham_tests` | No new obligation-tagged tests that don't call production code |
| `test_allowlist_entries_still_violations` | Stale allowlist detection |
| `test_violation_count_tracked` | Allowlist size matches actual violation count |

#### Current known violations

Tracked in `obligation_test_quality_allowlist.json`. Allowlist can only shrink.
Tracked by `salesagent-9q5g`.

### Single Migration Head Guard

**File:** `tests/unit/test_architecture_single_migration_head.py`

**What it enforces:** The Alembic migration graph must have exactly one head
revision at all times.

**Why it matters:** When two PRs each create a migration branching from the
same parent and both merge to main, the migration DAG forks into multiple
heads. This makes `alembic upgrade head` fail, `alembic downgrade -1`
ambiguous, and `alembic revision` error without `--head`. The problem is
invisible to PR authors because neither has the other's migration locally.

#### How it works

The guard parses every migration file's AST to extract `revision` (string)
and `down_revision` (string, tuple, or None). It handles both `ast.Assign`
and `ast.AnnAssign` styles. It then builds the set of all revisions and the
set of all revisions pointed to by a `down_revision`. Heads are revisions
not pointed to by any other migration. The test asserts exactly one head.

#### Tests

| Test | What It Checks |
|------|---------------|
| `test_single_migration_head` | Exactly one head exists in the migration graph |

#### No allowlist

Zero tolerance. If multiple heads exist, you must create a merge migration
before your PR merges:

```bash
uv run alembic merge -m "Merge migration heads" heads
```

The smoke test in `tests/smoke/test_database_migrations.py` also checks this,
providing coverage in the CI smoke-tests job before unit tests run.

## Adding a New Guard

1. Create `tests/unit/test_architecture_{name}.py`
2. Use AST scanning (not `inspect.getsource()` — it's banned by lint rules)
3. Include an allowlist for pre-existing violations
4. Include a stale-allowlist test that fails when a violation is fixed but the
   entry remains
5. Add FIXME comments at each violation site: `# FIXME(salesagent-xxxx): description`
6. Document the guard in this file

## Running Guards

```bash
# All guards (part of make quality)
make quality

# Just the architecture guards
uv run pytest tests/unit/test_architecture_*.py tests/unit/test_*impl*.py -v

# Single guard
uv run pytest tests/unit/test_architecture_schema_inheritance.py -v
```

## Relationship to Other Quality Mechanisms

```
Pre-commit hooks (40)          ← catch formatting, route conflicts, star imports
    │
    ▼
Structural guards (17)         ← catch architecture violations (THIS FILE)
    │
    ▼
Unit tests (~2950)             ← catch behavior bugs
    │
    ▼
Integration tests (PostgreSQL) ← catch data layer bugs
    │
    ▼
E2E tests (Docker stack)       ← catch deployment/wiring bugs
```

Guards sit between pre-commit hooks (syntactic) and unit tests (behavioral).
They enforce structural properties that are invisible to both.
