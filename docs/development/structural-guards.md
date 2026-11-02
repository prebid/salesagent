# Structural guards

Automated architecture enforcement tests that run on every `make quality`.
Each guard uses AST scanning and introspection to detect violations at the
source level — no runtime execution of business logic needed.

Before you read the inventory, read the decision framework. It governs
whether a guard should exist at all, and most structural concerns are better
served by something that is not a guard. The guard inventory is expected to
shrink as invariants move into types and lint rules.

## Contents

- [Decide whether a guard should exist](#decide-whether-a-guard-should-exist) — the three-question framework; work through it before adding to the inventory
- [Refactoring checks are scaffolding, not guards](#refactoring-checks-are-scaffolding-not-guards) — completion checks get a deletion condition, not a permanent slot
- [What guards catch](#what-guards-catch) — the one defect class that justifies a guard
- [Guard design rules](#guard-design-rules) — shrinking allowlists and the other invariants every guard follows
- [Guard inventory](#guard-inventory) — every active guard, with how it works, its tests, and its known violations:
  - [Transport-boundary guards](#transport-boundary-guards)
  - [Schema inheritance guard](#schema-inheritance-guard)
  - [Boundary completeness guard](#boundary-completeness-guard)
  - [Query type safety guard](#query-type-safety-guard)
  - [No model_dump() in _impl guard](#no-model_dump-in-_impl-guard)
  - [Repository pattern guard](#repository-pattern-guard)
  - [BDD step quality guards](#bdd-step-quality-guards)
  - [Single migration head guard](#single-migration-head-guard)
  - [Hook-relocation guards](#hook-relocation-guards)
- [Add a guard](#add-a-guard) — the procedure, and the shared helpers to build on
- [Symbol subjects and shape subjects](#symbol-subjects-and-shape-subjects) — bind a symbol, allowlist a shape
- [Run the guards](#run-the-guards)
- [Relationship to other quality mechanisms](#relationship-to-other-quality-mechanisms) — where guards sit among types, lint, and hooks

## Decide whether a guard should exist

A guard is the last resort, not the first move. Work through these three
questions in order, and stop at the first one that resolves the concern.

### First: can the wrong thing be made unrepresentable?

Correct architecture beats any test, because it removes the ability to
express the mistake instead of detecting it afterwards. There are unbounded
ways to write code wrongly, and a guard can only enumerate the ways someone
thought of. An architecture that makes the wrong call impossible to write is
worth more than a test that reports it.

Concrete substitutions, each backed by a mechanism this repository already
runs:

- **A guard that checks a caller passes certain parameters** → make the
  parameters typed and required. A missing argument is then a mypy error,
  not a test failure. `make quality-ci` runs
  `mypy src/ --config-file=mypy.ini` on every change.
- **A guard that forbids calls to the wrong one of several similar
  functions** → mypy, and ruff's banned-api rule (`TID251`), exist for
  exactly this. `ruff-egress.toml` bans every raw HTTP import across `src/`
  and `scripts/` with a per-import message that names the sanctioned seam,
  and `make quality-ci` runs that check with `--ignore-noqa` so a file
  cannot exempt itself.
- **A guard that watches for growing complexity** → ruff's complexity rules
  already fail the build: `C901` (mccabe, `max-complexity = 10`), `PLR0912`
  (too many branches), and `PLR0915` (too many statements) are
  count-ratcheted against `.ruff-complexity-baseline` by
  `.pre-commit-hooks/check_ruff_complexity_count.py` in `make quality-ci`.
- **A guard that keeps business logic away from raw sessions** → you can
  scan for `.session`, but the better answer is a design where the
  repository and the Unit of Work are the easy path. Make the correct call
  the convenient one, and the wrong call stops being written. The
  repository-pattern guard in the inventory is the backstop while its
  allowlist shrinks, not the primary defense.

### Second: does this need a check at all?

Not every structural concern deserves an artifact. Ask what is actually at
risk if no check exists. A pattern that nothing in future work plausibly
reintroduces, or whose violation is loud on its own — a crash, a type
error, a failing behavioral test — needs no guard on top.

### Last: write a guard

Write a guard only when both earlier answers are no: the wrong thing cannot
be made unrepresentable, and the risk is real. The rest of this document
describes how to write one well and inventories the guards that met that
bar.

## Refactoring checks are scaffolding, not guards

A refactoring check and a permanent invariant are different artifacts.

When the task is "refactor these classes", a check that fails before the
change and passes after it is a legitimate way to know the work is
complete. That is its whole purpose. It does not follow that the check
belongs in the codebase afterwards: it was scaffolding for a finished task.
Delete it when the refactor lands.

A guard earns permanence only if it protects an invariant that future work
could plausibly violate again. "It proved I finished the refactor" is not
that.

## What guards catch

The guards that remain after the framework cover one class of defect:
structural invariants whose violation looks correct in review and surfaces
only as a silent runtime failure. Examples:

- A schema class copies fields from the adcp library instead of inheriting,
  then drifts out of sync when the library changes a field type.
- An MCP wrapper accepts a parameter but doesn't pass it through to the
  shared `_impl` function — callers can set the value, and the wrapper
  silently discards it.
- A database query filters an Integer PK column with string values from
  JSON, returning 0 rows instead of raising an error.

None of these can be made unrepresentable with the types the boundary
offers, and none of them fail loudly on their own. The guards make them
machine-checkable.

## Guard design rules

**Allowlists shrink, never grow.** Every guard has a set of known violations
(existing code that predates the guard). New code that introduces a violation
fails CI immediately. When an existing violation is fixed, the stale-allowlist
test forces you to remove the entry.

**FIXME comments link to a GitHub issue/PR.** Every allowlisted violation has a
corresponding `# FIXME(#<gh-issue>)` comment at the source location, linking to
a tracked GitHub issue/PR. Use the GitHub number, never a local beads id — beads
ids don't resolve for outside contributors reading the code.

**AST scanning, not runtime execution.** Guards parse Python source with the
`ast` module. They don't import or execute business logic, so they run fast
and can't be affected by runtime state.

**Introspection for type hierarchies.** Where AST alone is insufficient (for
example, checking a class MRO), guards use `inspect` and `importlib` on the
already-imported modules.

**Shared helpers, not re-implemented traversal.** A guard uses the helpers in
`tests/unit/_architecture_helpers.py` rather than writing its own AST
machinery. See [Add a guard](#add-a-guard).

## Guard inventory

### Transport-boundary guards

| Test file | What it enforces |
|-----------|-----------------|
| `ruff-boundary.toml` (TID251) | Nothing under `src/` imports `ToolError` except the two edge modules that mint and render it. Replaced an AST scan over a hand-written list of 14 files, which was blind to every module not on it. Proven live by `tests/unit/test_ruff_boundary_bans.py`. |
| `test_transport_agnostic_impl.py` | `_impl` functions have zero transport imports (no fastmcp, a2a, starlette) |
| `ToolImpl` protocol (`src/core/tools/registry.py`, mypy) + `.ast-grep/rules/impl-signature-is-request-and-identity.yml` | Every implementation is exactly `(req: <DTO>, identity: ResolvedIdentity)` (protected) or `(req: <DTO>, identity: PublicIdentity)` (public), the DTO matching its registry row; the annotation is the row's credential policy, derived by `ToolSpec.requires_credential`. Replaced `test_impl_resolved_identity.py` and `test_architecture_boundary_completeness.py`, which read the same fact off `inspect.signature`. |
| `.ast-grep/rules/resolved-identity-constructed-only-by-its-owners.yml` | `ResolvedIdentity(...)` and `PublicIdentity(...)` are constructed only by the resolver and `PrincipalFactory`. Replaced two AST guards (a zero cap over A2A test files and a per-file cap dict over the rest); ruff cannot take it because TID251 bans the import, which forty modules need for annotations. |
| `ruff-boundary.toml` (TID251 on `AdCPAuthRequiredError` / `AdCPAuthenticationError`) | The auth refusals are minted by the resolver alone: a protected tool's `ResolvedIdentity` carries principal and tenant by type, so nothing downstream re-checks; the seller policy that makes `get_products` need a caller is asked by the resolver (`ToolSpec.requires_credential(tenant)`) once it holds the tenant row. Replaced `test_architecture_no_handrolled_identity_guard.py`, which modelled the `if identity is None: raise` shapes and missed the two helper sites the ban found on landing. |
| `ruff-boundary.toml` (TID251 on `src.core.database.repositories.account_lookup`) | The account a request names is resolved by the resolver alone, which builds the identity with it inside (`AccountIdentity` / `ResolvedIdentity.account`); the lookup module can be imported nowhere else under `src/` and `scripts/`. |
| `ruff-ownership.toml` (TID251 on `repositories.principal`, `repositories.principal_lookup`, `auth_utils`, `repositories.account`, `uow.AccountUoW`) + `ruff-boundary.toml` (TID251 on `models.Principal`) | A tool obtains its principal, tenant and account from the identity and nowhere else. A third config rather than more rows in `ruff-boundary.toml` because ruff exempts a whole rule per path: the admin UI and the setup scripts, which manage principals and accounts as data, are exempt from the ownership bans and from nothing in the boundary config. The ORM `Principal` model, whose only exemption is the four repository modules that query it, is banned in the boundary config so the admin tree cannot import it. Proven live by `tests/unit/test_ruff_boundary_bans.py`, which reads both tables. |
| `ruff-boundary.toml` (TID251 on `adcp.types.ContextObject`) + `.ast-grep/rules/context-is-written-by-the-boundary-alone.yml` + `AdcpResponse`'s two refusals | The buyer's `context` object is written by `_boundary._served` alone. The import ban stops business logic naming the type (the schemas and the boundary are exempt), the rule stops a `context=` keyword at any call site outside the boundary, and the response class refuses the field on construction and on assignment, so a dict-splat cannot slip past the rule. Proven by `tests/unit/test_ast_grep_identity_rules.py`, `test_ruff_boundary_bans.py` and `test_response_context_is_boundary_owned.py`. |

These guards enforce Critical Pattern #5: shared `_impl` functions are
transport-agnostic. They don't know whether they're called from MCP, A2A, or
a REST endpoint.

### Schema inheritance guard

**File:** `tests/unit/test_architecture_schema_inheritance.py`

**What it enforces:** A local schema class that redeclares a field of its
adcp library parent must keep the parent's shape: same annotation (or a
subclass), no added nullability, `is_required()` not relaxed, metadata a
superset, no introduced default. A redeclaration that reshapes or weakens a
field needs an allowlist row naming the weakened axis.

**How it works:** The redefinition rule
(`test_no_field_redefinition_in_subclasses`) walks the live MRO of each
local schema class and tests `__module__` to find fields redeclared over a
library parent, so it consults no import spelling. The companion
`test_all_library_types_have_local_subclass` discovers `Library*` aliases in
`src/core/schemas` and asserts that the local class with the unprefixed name
inherits from the library type; that half is alias-keyed, and an import
under a different alias goes unexamined by it.
This guard is the only grader of redeclarations. The alignment suite that
used to be the backstop — `test_pydantic_schema_alignment.py`, which graded
declared fields and `model_dump` survival against the pinned schema — was
deleted in full ([Building a tool](building-tools.md)),
because a design in which the DTO IS the pinned model minus a declared
omission leaves it nothing to compare.

### Implementation signature

**Where:** the `ToolImpl` protocol typing `ToolSpec.impl` in `src/core/tools/registry.py`,
graded by mypy, plus `.ast-grep/rules/impl-signature-is-request-and-identity.yml`.

**What it enforces:** An `_impl` function declares exactly `(req: <DTO>,
identity: ResolvedIdentity)`, and the DTO is the one its registry row names.

**Why it matters:** Every transport reaches an implementation through
`src/core/tools/_boundary.py`, which calls it as `impl(req=..., identity=...)` and nothing
else. A third parameter can never be filled, so it silently takes its default on every call;
an `identity` declared Optional or defaulted describes a state the boundary never produces
and invites a `None` branch that re-derives what the resolver decided.

#### How it works

The generic `ToolSpec[Req]` ties `dto: type[Req]` to `impl: ToolImpl[Req]`, so mypy checks
each row's implementation against that row's DTO and against `ResolvedIdentity`, and rejects
an extra parameter with no default. A protocol accepts a callable that takes MORE than it
asks for, so the ast-grep rule matches the parameter list exactly: no Optional, no default,
no third parameter.

#### What it replaced

`test_impl_resolved_identity.py` and `test_architecture_boundary_completeness.py` read the
same signature off `inspect.signature` at test time. The latter is the cautionary one: it
used to scan each `*_raw` wrapper for the arguments it forwarded, and its wrapper lookup
returned `None` — "nothing to check" — when it found no wrapper, so the day the wrappers were
deleted it went green while grading nothing.

### Query type safety guard

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

| Test | What it checks |
|------|---------------|
| `test_no_in_queries_on_integer_pk_with_wrong_type` | No new `.in_()` calls on Integer PK columns without review |
| `test_no_string_literals_in_filter_by_for_integer_pks` | No `filter_by(id="string")` patterns |
| `test_known_violations_still_exist` | Allowlisted violations haven't been fixed (stale entry detection) |

#### Current known violations (1)

| File | Pattern | Tracked by |
|------|---------|------------|
| `media_buy_delivery.py` | `PricingOption.id.in_(string_list)` | salesagent-mq3n |

### internal_detail is an exception

**Files:** `.ast-grep/rules/internal-detail-is-an-exception.yml`

**What it enforces:** `AdCPSalesAgentError.internal_detail` takes the caught exception
and nothing else. The rule matches the keyword argument `internal_detail=` and the
attribute assignment `x.internal_detail =` whose value is an authored string in any
spelling: a string or f-string literal, an implicitly concatenated string, a `+` with a
string on either side, `%` formatting of a string, `.format(...)` on a string, `str(...)`,
a conditional expression with a string arm, or any of those in parentheses, nested to any
depth, under `src/`, `scripts/` and `tests/`. The parameter is typed
`BaseException | None`, so mypy already refuses a string under `src/`; the rule is what
covers the harness and the tests, which mypy does not check.

**Why it matters:** An authored sentence there says nothing the error code, the
exception class and the typed details do not already say, and it reopens the door
ADR-010 closed: the wire fields are functions of the code. A fact (an id, a count, a
status) belongs on the declared details class. The boundary's `record_boundary_error`
writes one record per failure, with the traceback attached when the error has a
`__cause__` or an `internal_detail`; there is no second log record for `internal_detail`
(salesagent-3cs7o.24, proven by `tests/unit/test_tool_error_logging.py`).

### Serialize only at the edges

**Files:** `ruff-serialization.toml` and `.ast-grep/rules/serialize-only-at-the-edges.yml`

**What it enforces:** a model is serialized at a named edge and nowhere else. The
ruff config bans the two bare-call spellings that are importable names,
`pydantic_core.to_jsonable_python` and `pydantic_core.to_json`, everywhere under
`src/` and `scripts/`, with per-file-ignores for exactly the edge modules. The
ast-grep rule matches the method-call spellings, `.model_dump(...)` and
`.model_dump_json(...)`, under `src/` with the same edge list as its `ignores`,
because a method call on an instance imports nothing and ruff cannot see it. The
edge list is the definition of "edge"; it lives in those two files and nowhere else.

**Why it matters:** When business logic serializes, it takes on the serialization
format (JSON mode, aliases, exclude rules) and produces a second representation of
the value that drifts from the model. The model is handed through; the edge that
owns the format serializes it once.

#### What it replaced

An AST guard under `tests/unit/` walked from `*_impl` entrypoints under
`src/core/tools/` and saw only calls named `model_dump`. A call
reached through a helper outside that walk, or spelled `to_jsonable_python`, was a
hole, and `src/core/tools/creatives/_validation.py` escaped it that way. Its
allowlist was already empty. The ruff table is proven live by
`tests/unit/test_ruff_boundary_bans.py`; the ast-grep rule by a manual fire probe,
recorded on salesagent-3cs7o.12.

### Repository pattern guard

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

This guard is the backstop for the framework's fourth substitution: the
primary defense is a repository and Unit of Work layer that is easier to
call than a raw session. The guard holds the line while the allowlist
shrinks.

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

| Test | What it checks |
|------|---------------|
| `test_no_new_get_db_session_in_impl` | No new `get_db_session()` calls outside the allowlist |
| `test_allowlist_entries_still_exist` (impl) | Stale allowlist detection for impl violations |
| `test_no_new_session_add_in_tests` | No new `session.add()` calls outside the allowlist |
| `test_allowlist_entries_still_exist` (tests) | Stale allowlist detection for test violations |

#### Current known violations

- **27 `get_db_session()` calls** across 10 production files (media_buy_create, update, delivery, list, products, creatives, task_management, admin blueprints)
- **58 `session.add()` calls** across 10 integration test files

All tracked by `salesagent-qo8a`.

### BDD step quality guards

Five AST-scanning guards enforce step definition quality in `tests/bdd/steps/`.
They prevent the most common LLM-generated BDD anti-patterns.

#### No-op Then steps

**File:** `tests/unit/test_architecture_bdd_no_pass_steps.py`

Catches three failure modes in `@then` step functions:
1. **Empty body** — `pass`, ellipsis, or docstring-only
2. **No code** — no assert, call, or raise at all
3. **No-op delegation** — body has zero `assert` statements and only delegates to
   non-assertion helpers (like `_pending(ctx, step)`). Catches any LLM-invented
   placeholder by structure, not by name.

A call counts as "meaningful" only if the function name starts with `assert_`,
`_assert_`, `check_`, `_check_`, `verify_`, `_verify_`, or is `pytest.skip/xfail/fail`,
or is `env.*` (harness method).

**Current known violations:** 41 Then steps in `uc004_delivery.py` using `_pending()`.

#### Trivial assertions

**File:** `tests/unit/test_architecture_bdd_no_trivial_assertions.py`

Catches `@then` steps that only use bare truthiness checks (`assert x`) without
comparisons (`==`, `!=`, `in`, `not in`, `is`, `isinstance`).

#### No dict in registry

**File:** `tests/unit/test_architecture_bdd_no_dict_registry.py`

Catches `@given` steps that store raw dict literals in `ctx["registry_formats"]`
instead of `FormatFactory.build()` objects.

#### No duplicate step bodies

**File:** `tests/unit/test_architecture_bdd_no_duplicate_steps.py`

Catches groups of 3+ step functions with identical normalized bodies (after
stripping docstrings). Threshold of 2 is tolerated for partition/boundary pairs.

#### No silent env degradation

**File:** `tests/unit/test_architecture_bdd_no_silent_env.py`

Catches two "No Quiet Failures" violations:
1. **`ctx.get("env")`** — returns `None` instead of `KeyError` when harness is missing.
   Canonical: `ctx["env"]` (guaranteed by autouse fixture).
2. **`hasattr(env, "method")`** — probes harness at runtime instead of using typed
   protocols. If env lacks a method, xfail the scenario rather than silently skip.

**Current known violations:** 17 `ctx.get("env")` + 22 `hasattr(env, ...)` in `uc004_delivery.py`.

### Single migration head guard

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

| Test | What it checks |
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

### No request-supplied session default guard

**File:** `tests/unit/test_architecture_no_request_session_default.py`

**What it enforces:** A Flask session lookup — `session.get`, `session.pop` or
`session.setdefault` with a string key, anywhere under `src/` — never defaults
(positionally or by `default=`) to a value the request supplied: a `request.*`
expression, a parameter of the enclosing function (route arguments arrive that
way), or a name bound from `request.*` in that function. Nor is such a lookup
followed by `or <any of those>`.

**Why it matters:** The default is taken exactly when the request carries no
session, which is the request that must not be trusted.
`session.pop("gam_oauth_tenant_id", state)` in the GAM OAuth callback was the
whole of [#2205](https://github.com/prebid/salesagent/issues/2205): with no
session, the tenant came from the attacker-controlled `state` parameter and
that tenant's ad-server credential was overwritten. The line read as a
harmless fallback in review and failed nothing at runtime.

#### How it works

For every function the guard collects its parameters and every name assigned
from an expression rooted at `request`. It then walks the module with
`walk_with_enclosing_function` and flags two shapes. First, a call to
`.get`/`.pop`/`.setdefault` on `session`, `flask_session` or `<module>.session`
whose first argument is a string constant (which is what separates the Flask
session from SQLAlchemy's `Session.get(Model, pk)`) and whose default — the
second positional argument or the `default=` keyword, which Flask's session
accepts on `pop` and `setdefault` — mentions one of those names or is itself
rooted at `request`. Second, an `or` expression in which such a lookup is
followed by an operand that mentions one of those names or is rooted at
`request`.
A default computed from the session itself — `session.get("user_name",
email.split("@")[0].title())` with `email = session["user"]` — is not flagged.

#### Tests

| Test | What it checks |
|------|---------------|
| `test_no_request_supplied_session_default` | No violation under `src/` beyond `KNOWN_VIOLATIONS` (empty) |
| `test_detector_catches_known_bad` | The detector fires on a route-parameter default, a name bound from `request.args`, a `request.args.get(...)` default, a `default=` keyword, `setdefault`, the `or request...` form, `or` with a request-bound name or a route parameter, and the `flask_session` alias |
| `test_detector_ignores_session_derived_defaults_and_orm_sessions` | Literal and session-derived defaults, `session.pop(...) or session.get(...)`, and `db_session.get(Tenant, pk)` are not flagged |

#### No allowlist

`KNOWN_VIOLATIONS` is empty and stays empty: the one violation was the bug
the guard was written for.

### Hook-relocation guards

These guards run via `pytest -m arch_guard` or as part of `make quality`.
They replaced grep-based pre-commit hooks (issue #1234).

| Test file | Replaces hook | What it enforces |
|-----------|---------------|------------------|
| `test_architecture_no_tenant_config.py` | `no-tenant-config` | No `tenant.config` / `tenant["config"]` in `src/` |
| `test_architecture_jsontype_columns.py` | `enforce-jsontype` | JSON columns use `JSONType`, not plain `JSON` |
| `test_architecture_no_defensive_rootmodel.py` | `check-rootmodel-access` | No `hasattr(x, "root")` without `# noqa: rootmodel` |
| `test_architecture_import_usage.py` | `check-import-usage` | Tree-wide import usage check for `src/` |
| `test_architecture_query_type_safety.py` | `enforce-sqlalchemy-2-0` (partial) | `test_no_legacy_session_query`, `test_models_use_mapped_not_column` |
| `test_architecture_pre_commit_hook_count.py` | — | Commit-stage hook count ≤12 (D27) |
| `test_architecture_pre_commit_no_additional_deps.py` | — | No `additional_dependencies` in pre-commit config |
| `test_architecture_ci_bdd_shard_manifest.py` | — | BDD CI shards partition suite; matrix matches `SHARD_COUNTS` |
| `test_architecture_repo_invariants.py` | `repo-invariants` (partial) | Self-tests for `.fn()` detection in consolidated hook |

Guards use the `@pytest.mark.arch_guard` marker (distinct from the
entity-marker `architecture`).

Each of these guards includes a **known-bad self-test** (inline snippet or tmp
fixture) so a narrowed detector fails CI instead of passing green silently.

CI-only hook enforcement runs in `make quality-ci`: duplication, GAM auth
support, response attribute access, roundtrip tests. See
`.pre-commit-coverage-map.yml`.

## Add a guard

Before step 1, work through
[Decide whether a guard should exist](#decide-whether-a-guard-should-exist).
If the invariant can live in a type signature, a mypy check, or a ruff rule,
put it there and stop. If the check only proves a refactor is finished, write
it, use it, and delete it with the refactor — don't add it here.

1. Create `tests/unit/test_architecture_{name}.py`
2. Use AST scanning (not `inspect.getsource()` — it's banned by lint rules)
3. Build on the shared helpers in `tests/unit/_architecture_helpers.py`
   instead of re-implementing traversal (see the following section)
4. Include an allowlist for pre-existing violations
5. Include a stale-allowlist test that fails when a violation is fixed but the
   entry remains
6. Add FIXME comments at each violation site: `# FIXME(#<gh-issue>): description` (GitHub issue/PR number, never a beads id)
7. Document the guard in this file

### Use the shared helpers

`tests/unit/_architecture_helpers.py` centralizes the machinery guards need,
so a guard states its rule and borrows everything else. The pieces most
guards use:

- `repo_root()` / `rel()` — the repo-root anchor and repo-relative paths for
  stable allowlist keys.
- `parse_module()` — AST parsing with an mtime-keyed cache shared across
  guards; `safe_parse()` is the variant that returns `None` for a missing or
  unparseable file.
- `iter_module_trees()` — yields `(tree, repo_relative_path)` for every
  `.py` file under the given directories, through the shared cache, and
  raises on an unparseable file instead of silently dropping it.
- `src_python_files()` and `iter_call_expressions()` — source-file iteration
  and call-node filtering.
- `walk_with_enclosing_function()` — yields each node with the name of its
  enclosing function, for rules scoped to `_impl` or step functions.
- `assert_violations_match_allowlist()` — the standard
  new-violation/stale-entry comparison.
- `format_failure()` — the standard failure-message formatter.
- `assert_detector_catches_ast_snippets()` — feeds known-bad inline snippets
  to your detector and fails if any go unflagged.

`assert_detector_catches_ast_snippets` matters more than it looks: a
detector nobody proved can fire is a guard that passes vacuously. A green
run then reports "no violations" over a tree the detector cannot actually
read, which is indistinguishable from a clean tree. Every guard's detector
gets a known-bad self-test.

### Write a guard with the helpers

A complete guard, with each helper doing the job it exists for. The rule here
is "no `datetime.utcnow()` in `src/`", because it returns a naive datetime;
substitute your own predicate and the rest of the shape holds.

```python
"""Guard: no naive `datetime.utcnow()` in src/."""

import ast

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    assert_violations_match_allowlist,
    iter_module_trees,
    repo_root,
    walk_with_enclosing_function,
)

# Pre-existing violations, keyed on (path, enclosing function). Never a line
# number: it moves when anything above it is edited, and the entry goes stale
# without the violation being fixed.
KNOWN_VIOLATIONS: set[tuple[str, str]] = set()


def _violations(tree: ast.Module):
    """Yield each `.utcnow()` call with the function that encloses it."""
    for node, enclosing in walk_with_enclosing_function(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "utcnow"
        ):
            yield node, enclosing


def _lineno_violations(tree: ast.Module) -> list[int]:
    """The same detector, as the line-number list the self-test expects."""
    return [node.lineno for node, _ in _violations(tree)]


def test_detector_catches_known_bad() -> None:
    """Prove the detector fires. Without this the scan below proves nothing."""
    assert_detector_catches_ast_snippets(
        _lineno_violations,
        snippets={
            "module level": "import datetime\ndatetime.datetime.utcnow()\n",
            "inside a function": (
                "import datetime\n"
                "def f():\n"
                "    return datetime.datetime.utcnow()\n"
            ),
        },
    )


def test_no_naive_utcnow() -> None:
    found = {
        (path, enclosing)
        for tree, path in iter_module_trees([repo_root() / "src"])
        for _, enclosing in _violations(tree)
    }
    assert_violations_match_allowlist(
        found,
        KNOWN_VIOLATIONS,
        fix_hint="Use datetime.now(UTC); utcnow() returns a naive datetime.",
    )
```

What each helper is doing, and what goes wrong without it:

- **`repo_root()`** anchors the scan. Do not write `Path(__file__).parents[2]`
  or a relative `Path("src")`. The hand-rolled spellings in this repository
  disagree with each other — 55 modules say `parents[2]` and 19 say
  `parents[1]` — so at least one family is anchored somewhere unintended, and
  a relative path resolves against whatever directory pytest was started
  from. A guard pointed at a directory that does not exist finds nothing and
  passes.
- **`iter_module_trees()`** yields `(tree, repo_relative_path)` for every
  `.py` file under the directories you give it, through a shared mtime-keyed
  cache, and raises on a file it cannot parse rather than skipping it. Skipping
  an unparseable file is how a guard silently stops covering it.
- **`walk_with_enclosing_function()`** gives you the enclosing function name
  alongside each node, which is what lets the allowlist key on
  `(path, function)` instead of a line number.
- **`assert_violations_match_allowlist()`** compares both directions: a new
  violation fails, and so does an allowlist entry whose violation is gone. The
  second direction is what stops the allowlist accumulating entries nobody can
  retire.
- **`assert_detector_catches_ast_snippets()`** runs the detector against
  known-bad input. This is the test that matters most, and the one most often
  omitted: a detector that has quietly stopped matching reports "no
  violations" over a tree it cannot read, which is indistinguishable from a
  clean tree. Include a snippet for each spelling the rule must catch — an
  attribute call, an aliased import, a call inside a nested function.

Two further helpers cover cases the example does not: `parse_module()` when
you need a single file rather than a tree walk, and `iter_call_expressions()`
when the rule is about calls to one named function. `format_failure()` renders
a failure with a summary, a fix hint, and a documentation link, for guards
that assert directly rather than through the allowlist comparison.

## Symbol subjects and shape subjects

A guard's subject is either a **symbol** — a function, class or constant that
exists in `src/` or the pinned SDK — or a **shape**: a code pattern with no name
to import, like "a bare `except` placed ahead of a specific one".

The rule:

> **If the subject is a symbol, BIND it — import or resolve it in the guard
> module, so a rename fails here. If the subject is a shape, prose is correct;
> there is nothing to import.**

The sorting question is not "does the constant hold an identifier?" It is:

> **If the subject were renamed, does this guard go SILENT or LOUD?**

Bind the silent ones. A string-matching guard whose subject is renamed keeps
passing over a codebase that no longer contains what it scans for — it reports
clean because it finds nothing, which is indistinguishable from finding nothing
wrong. That is the failure mode binding removes: an unresolvable import cannot
be green.

Two things are worth knowing before you write one:

- A **module-level** import buys a collection failure, but it aborts the whole
  unit run and masks every other result. For a heavy module, use
  `importlib.import_module` inside the test — a rename still reddens
  `make quality`, as a failure rather than a collection error.
- Prefer **containment over derivation**. Asserting the guard's vocabulary is a
  subset of production's catches production losing a member. Deriving the
  vocabulary FROM production makes the guard track whatever production says,
  which is the opposite of a guard.

This page does not list which guards are which kind. Such a list is prose about
symbols, which is exactly the artifact that goes stale without anything noticing
— the reason the rule above exists.

## Run the guards

```bash
# All guards (part of make quality)
make quality

# Just the architecture guards
uv run pytest tests/unit/test_architecture_*.py tests/unit/test_*impl*.py -v

# Single guard
uv run pytest tests/unit/test_architecture_schema_inheritance.py -v
```

## Relationship to other quality mechanisms

```
Types + lint (mypy, ruff TID251,   ← make the wrong thing unrepresentable,
complexity ratchet)                  or fail it at the import/signature level
    │
    ▼
Pre-commit hooks                   ← catch formatting, route conflicts, star imports
    │
    ▼
Structural guards                  ← catch architecture violations with allowlists (THIS FILE)
    │
    ▼
Unit tests (~2950)                 ← catch behavior bugs
    │
    ▼
Integration tests (PostgreSQL)     ← catch data layer bugs
    │
    ▼
E2E tests (Docker stack)           ← catch deployment/wiring bugs
```

Guards sit between pre-commit hooks (syntactic) and unit tests (behavioral).
They enforce structural properties that are invisible to both — and only
those. A property expressible at the top of this ladder belongs there, not
here.

**ast-grep scan rules** (`.ast-grep/rules/`) provide fast first-line defense at
commit time for simple BDD patterns (`ctx.get("env")`, `hasattr(env, ...)`,
error fabrication). Python AST guards manage the allowlists for existing
violations and handle complex cross-file analysis.
