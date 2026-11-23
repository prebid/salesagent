# Prebid Sales Agent development guide

## 🤖 For Claude (AI assistant)

This guide surfaces the repository-specific rules for the Prebid Sales Agent codebase (maintained under Prebid.org). The 11 critical patterns and the structural guards that enforce them are non-negotiable; the § Documentation list at the end says where the depth lives.

**Never push directly to main.** Commits and PR titles use Conventional Commits prefixes — `feat` / `fix` / `docs` / `refactor` / `perf` / `chore` — enforced by `.github/workflows/pr-title-check.yml`; release-please builds the changelog from them, so an unprefixed commit ships undocumented.

### Common task patterns
- **Adding a new AdCP tool**: Extend library schema → Add `_impl()` controller → Add a `ToolSpec` row to `src/core/tools/registry.py` → Add tests. MCP registration, the A2A card and the REST route are all generated from the row.
- **Fixing a route issue**: Check for conflicts with `grep -r "@.*route.*your/path"` → Use `url_for()` in Python, `scriptRoot` in JavaScript
- **Modifying schemas**: Verify against AdCP spec → Update Pydantic model → Run `pytest tests/unit/test_adcp_contract.py`
- **Database changes**: Reach the data through a repository inside a Unit of Work → Use `JSONType` for JSON columns → Create migration with `alembic revision`
- **Any feature or bug fix**: TDD — the failing test comes first, and every change is gated by `make quality` (full cycles in `.claude/rules/workflows/tdd-workflow.md` and `bug-reporting.md`)
- **Refactorings**: additionally run `tox -e integration` and verify imports with `uv run python -c "from module import thing"` — pre-commit hooks can't catch import errors
- **Scope and review**: read [engineering-standards.md § Keep the change to that outcome](docs/development/engineering-standards.md#keep-the-change-to-that-outcome) and [§ Review the change](docs/development/engineering-standards.md#review-the-change) before opening or reviewing a pull request

### Key files to know
- `src/core/main.py` — MCP server and tool registration
- `src/core/tools/` — tool `_impl()` controllers and services (package)
- `src/core/tools/registry.py` — `TOOLS`: the one declaration every transport derives from
- `src/core/tools/_boundary.py` — the one path from a validated request to a response
- `src/core/schemas/` — Pydantic models, AdCP-compliant (package)
- `src/adapters/base.py` — adapter interface
- `src/adapters/gam/` — GAM implementation
- `tests/unit/test_adcp_contract.py` — schema compliance tests

### DRY (Don't Repeat Yourself) — a non-negotiable invariant

**DRY is a correctness requirement, equivalent to type safety or test integrity — not "premature optimization," and not "refactoring beyond what was asked."**

- If you write a block of logic that is structurally similar to a block already in the codebase (same pattern, different variables), you **MUST** extract a shared helper function. Pre-existing duplicates elsewhere are paid down through `.duplication-baseline`, not folded into an unrelated change.
- If you are asked to refactor duplicated code, that is a **bug fix**, not an "improvement"
- **NEVER** cite "avoid over-engineering" or "keep it simple" to justify leaving duplicated logic in place
- Duplicated code is a defect. It means the next person who fixes a bug in one copy misses the other copy, and it has caused real bugs in this codebase
- **Enforced by:** `check_code_duplication.py` in `make quality` (pylint R0801, ratcheting baseline in `.duplication-baseline`)

**What DRY is NOT:**
- It is not an excuse to create deep abstraction hierarchies for one-time code
- It is not about collapsing two genuinely different operations that happen to look similar today
- It applies when the same **logical operation** is repeated with only parameter substitution
- A worked example of extracting the shared pattern: [patterns-reference.md §8](docs/development/patterns-reference.md)

### Structural guards (automated architecture enforcement)
AST-scanning tests enforce architecture invariants on every `make quality` run. New violations fail the build immediately.

**The following table is a representative subset, not the full set.** There are over 150 guard tests (`tests/unit/test_architecture_*.py`, plus a handful of boundary guards like `test_transport_agnostic_impl.py`); `ls tests/unit/test_architecture_*.py` prints the current set, which is the only count that cannot go stale. See [docs/development/structural-guards.md](docs/development/structural-guards.md) for design rationale (its written inventory covers only a subset).

| Guard | Enforces |
|-------|----------|
| No ToolError anywhere but the edge | Business logic raises `AdCPSalesAgentError`; `ToolError` only on the way out |
| Transport-agnostic `_impl` | Zero transport imports in `_impl` |
| `_impl` signature | `(req: <DTO>, identity: ResolvedIdentity\|AccountIdentity\|PublicIdentity)` — annotation IS credential policy |
| One identity constructor | Identity built only in the resolver and `PrincipalFactory` |
| Principal rows are repository-private | `Principal` importable only by its four repository modules |
| Principal/account from identity alone | Auth repos importable only by resolver, repos, admin, setup scripts |
| Auth refusals by resolver alone | `AdCPAuthRequiredError`/`AdCPAuthenticationError` raised only by the resolver |
| Account resolved by resolver alone | `account_lookup` importable only by the resolver |
| Context written by boundary alone | No `context=` or `ContextObject` outside boundary and schemas |
| Query type safety | DB queries use types matching column definitions |
| Serialize only at the edges | No `.model_dump()` etc. outside named edge modules |
| Environment read once | No `os.environ`/`os.getenv` outside settings loader |
| Test flags never reach production | No `adcp_testing` read outside settings loader (GH #2255) |
| `internal_detail` is an exception | Never an authored string — typed `BaseException \| None` |
| No direct DB access | No `get_db_session()`/`session.add()` outside repos/UoW |
| Migration completeness | Non-empty `upgrade()` and `downgrade()` |
| No raw MediaPackage select | All access through repository |
| No import-time filesystem I/O | `src/` and `scripts/` touch no files at import |
| No raw select outside repos | ORM queries only through repositories |
| No raw egress | All outbound HTTP through `outbound_http.py` |
| No destination rewrite | No URL/netloc/scheme rewrite ahead of the gateway |
| BDD step quality (5 guards) | Then steps assert; assertions reachable; Given uses factories; no duplicate steps; no silent env |
| Code duplication (DRY) | Duplicate block count cannot increase |
| Workflow tenant isolation | Queries join DBContext for tenant scoping |
| No split mock assertions | `assert_called_once_with()`, not `assert_called_once()` + `call_args` |
| Single migration head | One Alembic head |
| Pre-commit discipline | No `additional_dependencies`; commit-stage hooks ≤12 |
| No tenant.config access | Per-field columns, not legacy `tenant.config` |
| JSONType columns | `JSONType`, not plain `JSON` |
| No defensive RootModel | No `hasattr(x, "root")` without `# noqa: rootmodel` |
| Import usage | Classes/functions used in `src/` must be imported |

**Rules for guards:**
- Allowlists can only shrink — never add new violations, fix them instead
- Every allowlisted violation has a `# FIXME(#<gh-issue>)` comment at the source location — reference a GitHub issue/PR number, never a local beads id (beads ids don't resolve for outside contributors)
- When you fix a violation, remove it from the allowlist (the stale-entry test reminds you)

---

## AdCP spec version

This project targets AdCP through the `adcp` Python SDK, pinned exactly in
`pyproject.toml`. The SDK reports the spec version it targets —
`adcp.get_adcp_spec_version()`, reached in Python through
`scripts.audit.storyboard_spec.pinned_version(repo)` — and that is the only
authority. **No number is written here**: a version restated in prose rots, and
no document in this repository states one.

See [docs/adcp-spec-version.md](docs/adcp-spec-version.md) for how to resolve the
pin and for the bump procedure. The CI guard at
`tests/unit/test_adcp_spec_version.py` fails on pin drift.

### Spec-grounding gate (MANDATORY before implementing protocol behavior)

**Any change to AdCP/protocol BEHAVIOR** — a tool's request/response contract, error emission, idempotency, governance, or capabilities — **must cite, before you write the code, the authoritative spec section + version that mandates it, plus the conformance storyboard step that grades it** (or note "ungraded"). Record the citation in the PR description and/or the planning note.

- **Which version is authoritative:** the version the repo PINS — *unless* there is active work to comply with a different target version (a bump/migration in flight), in which case that TARGET version is the pin. Confirm which applies first.
- **Where the spec lives** (`github.com/adcontextprotocol/adcp`): the written spec at `docs/building/by-layer/L<0-4>/*.mdx`, read at the pinned tag (idempotency, auth and error taxonomy are all in `L1/security.mdx`) — there is NO `dist/docs/<version>/` for the pinned version, `dist/docs/` stops at `3.1.0`; the graded, executable contract does keep a version-numbered root, at `dist/compliance/<version>/<area>/*.yaml` (`domains/`, `protocols/`, `universal/`, `test-kits/` — there are no yaml files directly under `<version>/`). That writing is NOT vendored — the venv carries only `_schemas/` — so read it at the tag the INSTALLED SDK reports, derived in the same command rather than typed:

    SPEC=$(uv run python -c 'import adcp; print(adcp.get_adcp_spec_version())')
    gh api "repos/adcontextprotocol/adcp/contents/docs/building/by-layer/L1/security.mdx?ref=v$SPEC" --jq '.content' | base64 -d

**Never type a version into the ref or a dist/ path segment** — derive it from the installed SDK. The SDK is a CROSS-CHECK, not the authority; it can diverge from the spec.
- **Why:** grounding protocol behavior in downstream artifacts instead of the written spec has produced features built inverse to the spec.
- **Enforcement:** reviewers reject protocol-behavior changes that don't cite the spec. Background: [docs/adcp-spec-version.md](docs/adcp-spec-version.md).

---

## 🚨 Critical architecture patterns

### 1. AdCP schema: extend library schemas
**MANDATORY**: Use `adcp` library schemas via inheritance, never duplicate.

```python
from adcp.types import Product as LibraryProduct  # Library* alias convention

class Product(LibraryProduct):
    """Extends library Product with internal-only fields."""
    implementation_config: dict[str, Any] | None = Field(default=None, exclude=True)
```

**Rules:**
- Import library types with `Library*` alias: `from adcp.types import X as LibraryX`
- Extend with inheritance — don't copy fields from the parent class
- Only redeclare a parent field to narrow it to a local subclass (Pattern #4 re-serializes the instance)
- Mark internal-only fields with `exclude=True`
- Run `pytest tests/unit/test_adcp_contract.py` before commit
- **A FIELD YOU ADD MUST HAVE A DECLARED SURFACE.** A field on a **request DTO is ADVERTISED** (`_register_tool` derives the announced shape from the DTO). A field on a **response model is ON THE WIRE** unless `Field(exclude=True)`. **`exclude=True` means gone, not internal** — it strips from the wire, persistence and the idempotency hash alike.
- **No structural guard — graded by behaviour instead:** `tests/unit/test_adcp_contract.py` before commit, and BDD scenarios across all transports.

### 2. Flask: prevent route conflicts
**Pre-commit hook detects duplicate routes** — run it manually: `uv run python .pre-commit-hooks/check_route_conflicts.py`

When you add a route, search for conflicts first (`grep -r "@.*route.*your/path"`), and deprecate properly with early return, not comments.

### 3. Database: repository pattern + ORM-first
The database is PostgreSQL, in every environment including tests.

**ORM-first access (MANDATORY):**
- All DB reads and writes go through SQLAlchemy ORM models via repository classes
- Never construct ORM models with raw kwargs scattered in `_impl` functions — use model factory methods or repository `create_from_*()` methods
- Never pass `json.dumps()` to `JSONType` columns — the column type handles serialization
- Use SQLAlchemy relationships and cascading — they exist to manage parent/child persistence atomically
- Use `JSONType` for all JSON columns (not plain `JSON`)
- Inside a repository, use SQLAlchemy 2.0 patterns: `select()` + `scalars()`, never `query()`
- Outside a repository, `select()` on an ORM model is banned. It bypasses tenant scoping and the business rules the repository owns
- Cast IDs at the boundary: JSON gives you strings, but Integer primary-key columns need `int` values. Write `int(x)` before passing to `.in_()` or `filter_by()`
- All tests require PostgreSQL: `./run_all_tests.sh` runs Docker + tox (JSON reports in `test-results/`)
- **Exception:** Bulk imports and complex reporting queries may use Core SQL/raw SQL for performance. Regular CRUD operations are never an exception.
- **Enforced by:** `test_architecture_query_type_safety.py`, `test_architecture_repository_pattern.py`, `test_architecture_no_raw_select.py`

**Unit of Work:**
```python
# The UoW owns the session: it opens one on entry, commits on a clean exit,
# and rolls back if the block raises. Repositories hang off it.
with MediaBuyUoW(identity.tenant_id) as uow:
    media_buy = uow.media_buys.get_by_id(req.media_buy_id)
```

`BaseUoW` and per-domain classes live in `src/core/database/repositories/uow.py`. `uow.session` raises a `DeprecationWarning` — add a repository method instead.

See [patterns-reference.md](docs/development/patterns-reference.md) §§1–2 for both patterns in full.

### 4. Pydantic: one serializer per model, and serialization only at the edges
A model is serialized in three ways — `model_dump()`, `model_dump_json()`, and
`pydantic_core.to_json` (which the JSON column type and every nested parent use) — and
only a `@model_serializer` runs on all three. A `def model_dump(self, **kwargs)` override
runs on one, so a shape written there exists on one path and not the others. **Never
override `model_dump`.** And a wire model does not shape its own output at all: it
conforms BY INHERITANCE. The library parent is the pinned schema, so a model that inherits
its fields and declares its internal ones `Field(exclude=True)` serializes to the spec shape
on every path with no per-class hook:

```python
class Product(LibraryProduct):
    implementation_config: dict[str, Any] | None = Field(default=None, exclude=True)  # never on the wire

class SyncCreativesResponse(NestedModelSerializerMixin, LibrarySyncCreativesSuccess, AdcpResponse):
    creatives: list[SyncCreativeResult]   # a local subclass: re-dumped through its own serializer
```

**Rules:**
- Never override `model_dump` — only a `@model_serializer` runs on all three serialization paths
- `WireSerializerMixin` (`src/core/schemas/_base.py`) handles two concerns: `NestedModelSerializerMixin` re-serializes children by instance type; required-nullable retention keeps `None` on the wire under `exclude_none`
- A field off the wire is `Field(exclude=True)` at its declaration, nowhere else
- Business logic never calls `model_dump()` — a model is the value; serialization happens at named edges only
- A receiving model adopts a sibling class via `from_attributes=True` or a `mode="before"` validator, never by a dump in the caller
- `Field(exclude=True)` strips from wire, persistence and idempotency hash — a field that must persist belongs on a repository-owned carrier
- **Enforced by:** `ruff-serialization.toml` + `.ast-grep/rules/serialize-only-at-the-edges.yml`. The edge list lives in those two files.

### 5. Transport boundary: one path to every implementation
All tools have two layers: a **transport** (MCP, A2A, REST) and **business logic** (`_impl` functions). Between them sits ONE seam — `src/core/tools/_boundary.py`.

```python
# _impl: transport-agnostic business logic
async def _create_media_buy_impl(
    req: CreateMediaBuyRequest,
    identity: ResolvedIdentity,
) -> CreateMediaBuyResult: ...

# Transport: one call to the boundary
response = await serve("create_media_buy", payload, request.headers, TransportProtocol.REST)
```

`serve` validates the payload, resolves the identity once, resolves the account, honours `idempotency_key`, stamps `context` and the served version, and calls the implementation.

**Rules for `_impl` functions:**
- Accept `ResolvedIdentity` (protected), `AccountIdentity` (protected + DTO requires `account`), or `PublicIdentity` (public — branch on `identity.principal is None`), never `Context`, raw headers, or a token
- Raise `AdCPSalesAgentError` subclasses, never `ToolError`
- Zero imports from `fastmcp`, `a2a`, `starlette`, or `fastapi`
- A controller never calls another controller — call the SERVICE when one tool needs another's work

**Rules for transports:**
- Call `serve(tool_name, payload, headers, protocol)` — never resolve an identity or call an implementation directly

**Substituting an implementation in a test** patches the registry ROW (`stub_impl`, `registry_impl` from `tests/helpers/capture_wrapper_req.py`), not a module attribute.

**Enforced by:** `test_transport_agnostic_impl.py`, `ToolImpl` protocol (mypy), `.ast-grep/rules/impl-signature-is-request-and-identity.yml`, `ruff-boundary.toml` TID251 bans.

Worked examples: `.claude/rules/patterns/mcp-patterns.md` and [patterns-reference.md §6](docs/development/patterns-reference.md).

### 6. JavaScript: use request.script_root
**All JavaScript must support reverse-proxy deployments:**

```javascript
const scriptRoot = '{{ request.script_root }}' || '';  // for example, '/admin' or ''
const apiUrl = scriptRoot + '/api/endpoint';
fetch(apiUrl, { credentials: 'same-origin' });
```

Never hardcode `/api/endpoint` — it breaks behind an nginx prefix.

### 7. Schema validation: environment-based
- **Production**: `extra="ignore"` (forward compatible). Production is any one of the three
  spellings a deployment sets: `PRODUCTION=true`, `ENVIRONMENT=production`, or a Fly app name
  (`FLY_APP_NAME`). `RuntimeSettings.is_production` in `src/core/config.py` is the one predicate;
  nothing compares the environment string itself.
- **Development/CI**: Default → `extra="forbid"` (strict validation)

**THE DTO IS THE ACCEPTED SHAPE. `additionalProperties: true` IN THE PIN DOES NOT WIDEN IT.**

`deep_strip_to_schema` (`src/core/schemas/_accepted_shape.py`) strips every field the DTO does
not declare, and it **ignores `additionalProperties` on purpose**. These three are identical and
all three are correct:

```python
{"properties": {"a": {}}, "additionalProperties": True}   # -> rejects "/x"
{"properties": {"a": {}}, "additionalProperties": False}  # -> rejects "/x"
{"properties": {"a": {}}}                                 # -> rejects "/x"
```

This seller accepts **only** parameters explicitly defined in the DTO, because that is what
keeps the internal models predictable. A pinned schema saying `additionalProperties: true` is
the SPEC permitting a sender to add fields; it is not an instruction to this seller to carry
them inward. The dev/prod split is deliberate and is the whole mechanism: in dev an
undeclared field is a HARD REJECTION so a spec field this seller has not implemented is loud,
and in production it is silently dropped so a newer buyer is served rather than refused.

**Do not "fix" this.** If a scenario sends a pin-permitted-but-undeclared field and fails with `INVALID_REQUEST` in dev:
- if you SHOULD accept the field, declare it on the DTO;
- if you should NOT, the scenario is wrong. **Reads take no idempotency key** — a read is idempotent by construction.

### 8. Test fixtures: factory-based, not inline
**MANDATORY for new integration tests:** Use `factory-boy` factories for test data, not inline `session.add()` boilerplate.

**Rules:**
- Shared fixtures (tenant, principal, products) defined once in `conftest.py` using factories
- Test-specific data uses factory overrides, not copy-pasted setup blocks
- Factories live in `tests/factories/` — ORM factories and Pydantic schema factories
- Never `session.add()` in test bodies — use factories or fixtures that use factories
- Never call `get_db_session()` in test bodies — test data setup belongs in factory fixtures
- **DO NOT match pre-existing broken patterns.** If the test file you're adding to already uses
  `get_db_session()` or `session.add()`, those are pre-existing debt in the allowlist. Your new
  code must use factories regardless. The structural guard (`test_architecture_repository_pattern.py`)
  catches new violations immediately at `make quality`. Pre-existing violations are allowlisted
  and tracked with FIXME comments — they shrink over time, never grow.

Worked correct/wrong examples: [patterns-reference.md §5](docs/development/patterns-reference.md) and `tests/CLAUDE.md` § Factory system.

### 9. Outbound HTTP: the application implements no SSRF protection

**Every outbound request goes through `src/core/security/outbound_http.py` (`send` / `asend`).**

Do not add URL validation, private-IP checks, metadata blocklists, resolve-then-check, or
redirect re-validation anywhere else. If you find yourself writing `ipaddress`,
`socket.gethostbyname`, or a hostname blocklist in `src/`, stop — that logic is owned elsewhere.

```python
from src.core.security.outbound_http import asend

result = await asend(url, json=payload)
```

`ruff-egress.toml`, run by `make quality-ci`, fails the build on a raw HTTP client or a
hand-written address check. A `# noqa` does not silence it.

- **The rule, what it refuses, and how to add a call:** [docs/security/outbound-egress.md](docs/security/outbound-egress.md)
- **What the `adcp` SDK owns and what this repo carries:** [docs/design/egress-sdk-boundary.md](docs/design/egress-sdk-boundary.md)

### 10. Errors: one subclass per code, one declared details class

**Raise an `AdCPSalesAgentError` subclass bound to the code, and pass the `ErrorDetails`
subclass its type parameter declares. That is the whole API.**

```python
raise AdCPBudgetExceededError(details=BudgetDetails(requested_budget="500", budget_limit="100"))
```

**Rules (mypy-enforced — the base is generic in its details type):**
- Never pass a dict or foreign details class — parameterize the type annotation
- Never wrap the boundary's `failure_response` in a `try` — make the raise impossible upstream
- A code with no subclass is a gap — add the subclass, don't name the code on the base
- No raise site authors text: `message`, `recovery`, `suggestion`, `status_code` are read-only over `CODE_TABLE`
- `internal_detail` takes an exception, not a string

**Full rules:** [docs/design/error-architecture.md](docs/design/error-architecture.md). Read it before adding or changing a raise site.

### 11. Test flags never reach production behavior

**A production path that branches on `adcp_testing` is a bug.** Give the behavior a real input (a tenant column, an `AdapterConfig` row, a settings field) and let the test seed it. The same binds the test side — see [tests/CLAUDE.md](tests/CLAUDE.md) § Which kind of test.

**Enforced by:** `.ast-grep/rules/test-flag-never-reaches-production.yml` + `test_test_flag_properties_only_shrink.py` (GH #2255).

---

## Project overview

The Prebid Sales Agent is a Python application with the following components:
- **MCP Server**: FastMCP tools for AI agents (via nginx at `/mcp/`)
- **Admin UI**: Google OAuth secured interface (via nginx at `/admin/` or `/tenant/<name>`)
- **A2A Server**: python-a2a agent-to-agent communication (via nginx at `/a2a`)
- **Multi-Tenant**: Database-backed isolation. A request names its tenant in one of two ways, and a
  request that names neither is refused with `CONFIGURATION_ERROR`: the `Host`, matched against the
  `virtual_host` the tenant declares, or an explicit `x-adcp-tenant` header carrying the tenant id.
  Every tenant declares a `virtual_host`; the column refuses `NULL`, and no code derives a host from
  a subdomain.
- **PostgreSQL**: Production-ready with Docker deployment
- All services are accessed through the nginx proxy at **http://localhost:8000**

---

## Common operations

### Run locally
Migrations run automatically on container startup. The local-run walkthrough, access points, and
test login are in [docs/quickstart.md](docs/quickstart.md); MCP client usage and the
`uvx adcp ... list_tools` smoke test are in `.claude/rules/patterns/mcp-patterns.md`.

### Testing
Test orchestration uses **tox** (with tox-uv): `uv tool install tox --with tox-uv`.
The full command reference — quick checks, full suite, manual Docker lifecycle,
coverage, targeted runs — is `.claude/rules/patterns/testing-patterns.md`. The essentials:

```bash
make quality              # Format + lint + typecheck + unit tests (before every commit)
tox -e integration        # Real-PostgreSQL integration tests (after refactorings)
./run_all_tests.sh        # Full suite: Docker up → all suites via tox -p → Docker down
scripts/run-test.sh tests/integration/test_foo.py -x   # One test, iterating (starts agent-db Postgres)
```

Reports: `test-results/<ddmmyy_HHmm>/*.json` (last 10 runs kept). Coverage: `htmlcov/index.html`.
**Pre-commit hooks can't catch import errors** — you must run tests for refactorings!

### Database migrations
```bash
uv run python scripts/ops/migrate.py            # Run migrations locally
uv run alembic revision -m "description"        # Create migration

# In Docker (migrations run automatically, but can be run manually):
docker compose exec admin-ui python scripts/ops/migrate.py
```

**A migration changes the structure in `upgrade()` and reverts it in `downgrade()`.** Do not preserve data across a migration. Never stack a revision to fix one that has not shipped — edit or delete it (`git cat-file -e main:alembic/versions/<file>` checks). If a revision you are editing has children on the branch, re-point the child's `down_revision` in the same change. A migration that has shipped is immutable.

### Tenant setup dependencies
```
Tenant → CurrencyLimit (USD required for budget validation)
      → PropertyTag ("all_inventory" required for property_tags references)
      → Products (require BOTH)
```

---

## Testing guidelines

Test organization (unit/integration/e2e/admin/bdd/ui suites and what each needs), database fixtures,
quality rules (max 10 mocks per file), entity markers, and the
infrastructure decision tree are in `.claude/rules/patterns/testing-patterns.md` — read it before writing
or running tests. Test authoring with the harness (environments, factories, wire assertions): `tests/CLAUDE.md`.

### Error verification
**New error-path tests must assert on the wire envelope, not reconstructed exceptions.**
The test harness reconstructs `AdCPSalesAgentError` from wire responses, but this reconstruction is lossy.
Use `assert_envelope_shape(result.wire_error_envelope, code, recovery=...)` as the primary authority.
See `tests/CLAUDE.md` § "Error Verification Policy" for the full policy and helpers.

### Minimal code discipline

You are a lazy senior developer. Lazy means efficient, not careless. The best code is the code never written.

Before writing any code, stop at the first rung that holds:

1. Does this need to be built at all? (YAGNI)
2. Does it already exist in this codebase? Reuse the helper, utility, or pattern that's already here — don't rewrite it.
3. Does the standard library already do this? Use it.
4. Does a built-in platform feature cover it? Use it.
5. Does an already-installed dependency solve it? Use it.
6. Can this be one line? Make it one line.

Only then: write the minimum code that works. The ladder runs after you understand the problem, not instead of it: read the task and the code it touches, trace the real flow end to end, then climb.

Bug fix = root cause, not symptom: a report names a symptom. Grep every caller of the function you touch and fix the shared function once — one guard there is a smaller diff than one per caller, and patching only the path the ticket names leaves a sibling caller still broken.

Follow these rules:

- No abstractions that weren't explicitly requested. Caveat: allow shared helpers and the architecture abstractions already in this codebase.
- No new dependency if it can be avoided.
- No boilerplate nobody asked for.
- Deletion over addition. Boring over clever. Fewest files possible.
- Shortest working diff wins, but only once you understand the problem. The smallest change in the wrong place isn't lazy, it's a second bug.
- Question complex requests: "Do you actually need X, or does Y cover it?"
- Pick the edge-case-correct option when two stdlib approaches are the same size — lazy means less code, not the flimsier algorithm.
- Mark deliberate simplifications that cut a real corner with a known ceiling (global lock, O(n²) scan, naive heuristic): comment naming the ceiling and upgrade path.
- Not lazy about: understanding the problem (read it fully and trace the real flow before picking a rung — a small diff you don't understand is laziness dressed up as efficiency), input validation at trust boundaries, error handling that prevents data loss, security, accessibility, the calibration real hardware needs (the platform is never the spec ideal: a clock drifts, a sensor reads off), anything explicitly requested. Lazy code without its check is unfinished: non-trivial logic leaves ONE runnable check behind, the smallest thing that fails if the logic breaks (an assert-based demo/self-check or one small test file; fewer frameworks, fewer fixtures). Trivial one-liners need no test.

### Test integrity policy — ZERO TOLERANCE

**This is non-negotiable. Every rule below is a HARD STOP.**

1. **NEVER skip, ignore, deselect, or exclude failing tests.** Do not use `--ignore`, `-k "not test_name"`, `--deselect`, `pytest.mark.skip`, or `pytest.mark.xfail` to work around failures.
2. **NEVER rationalize failures.** Do not classify failures as "pre-existing", "infrastructure issue", "misplaced test", "needs a running server", or "was deselected in the full run". A failing test is a failing test — fix it or report it to the user as a blocker.
3. **Start the right infrastructure.** If a test needs Docker (integration, e2e, admin), start Docker. The tooling exists — the decision tree in `.claude/rules/patterns/testing-patterns.md` § Test Integrity says which command starts what. When in doubt, `./run_all_tests.sh`.
4. **If infrastructure is broken, STOP.** Do not skip tests and report success. Tell the user the infrastructure is broken and either fix it or ask the user to fix it.
5. **Test results are saved as JSON** in `test-results/<ddmmyy_HHmm>/`. Review these instead of re-running the full suite. Background processes may crash and lose output — the JSON reports are the resilient record.

## Configuration

Local secrets live in `.env` (required for a working stack: Gemini, Google OAuth,
GAM OAuth, super-admin emails, Approximated);
[Secrets configuration](docs/security.md#secrets-configuration) says which file each Docker
Compose file reads.
[docs/deployment/environment-variables.md](docs/deployment/environment-variables.md) documents
every variable — read it before touching credentials or auth configuration.

### Database schema
- **Core**: tenants, principals, products, media_buys, creatives, audit_logs
- **Workflow**: workflow_steps, object_workflow_mapping (human-in-the-loop approvals)
- **Note**: there are no `tasks` or `human_tasks` tables in the schema — don't reference them

## Adapter support

Adapters are registered in `src/adapters/__init__.py` and selected per tenant.
Registry keys: `gam`/`google_ad_manager`, `broadstreet`, `kevel`, `triton`/`triton_digital`, `mock`.
Maturity varies — GAM is by far the most complete. (`creative_engine` in the
registry is a creative-processing base class, not an ad-server adapter.)
The Broadstreet integration lives at `src/adapters/broadstreet/`.

### GAM adapter
**Supported pricing**: CPM, VCPM, CPC, FLAT_RATE

- Automatic line item type selection based on pricing + guarantees
- FLAT_RATE → SPONSORSHIP with CPD translation
- VCPM → STANDARD only (GAM requirement); compatibility matrix in `docs/adapters/`

### Mock adapter
**Supported**: all AdCP pricing models (CPM, VCPM, CPCV, CPP, CPC, CPV, FLAT_RATE) and all
currencies, with simulated metrics. Use it for testing and development.

## Documentation

Rules files for day-to-day work (read the one matching your task before starting):
- `.claude/rules/patterns/code-patterns.md` — writing code: SQLAlchemy 2.0 form, JSONType, absolute imports, no quiet failures, code style, mypy/type checking
- `.claude/rules/patterns/testing-patterns.md` — running and writing tests: tox commands, suite organization, fixtures, quality rules, full test-integrity policy, infrastructure decision tree
- `.claude/rules/patterns/mcp-patterns.md` — MCP/A2A work: client usage, CLI testing, transport-boundary examples, access points
- `.claude/rules/workflows/` — TDD cycle, quality gates, beads workflow, bug reporting, session completion
- `tests/CLAUDE.md` — authoring tests with the harness: environments, factories, wire-envelope assertions

Detailed documentation lives in `/docs`:
- `development/engineering-standards.md` — the standards this codebase holds code to; read before writing a change
- `development/architecture-principles.md` — the governing principles behind the layering
- `development/architecture.md` — system architecture
- `development/request-lifecycle.md` — how a request reaches business logic
- `development/building-tools.md` — adding or changing a tool: the registry row, the DTO, the identity type, what the boundary does once, and how each transport is derived
- `development/patterns-reference.md` — repository, Unit of Work, harness, and boundary patterns in full
- `development/structural-guards.md` — structural-guard design and inventory
- `development/GETTING_STARTED.md` — initial setup guide
- `development/README.md` — the map of development documentation
- `adapters/creating-an-adapter.md` — building an ad server adapter
- `development/e2e-testing.md` — end-to-end testing
- `development/troubleshooting.md` — common issues
- `security.md` — security guidelines
- `security/outbound-egress.md` — outbound HTTP and SSRF
- `design/error-architecture.md` — the one code table, the raised and advisory lanes, and why neither authors text
- `design/request-signing-subset.md` — the RFC 9421 subset this seller implements: where a counterparty's keys may live, and the two narrowings a graded scenario refuses
- `design/bdd-harness-architecture.md` — one scenario on every transport: what a scenario names and what the harness derives
- `design/webhook-testing-architecture.md` — the local HTTP and MCP origins, the shared TLS material, the delivery envs, and the e2e capture service
- `quickstart.md` — local run walkthrough
- `deployment/` — deployment guides (including `environment-variables.md`)
- `adapters/` — adapter-specific documentation

Test examples live in `/tests`; adapter implementations in `/src/adapters`. File issues on the GitHub repository.

## Language and register

### Banned words and phrases (do not use, ever)
"load-bearing," "hand-waving," "reflexive hedging," "honest framing,"
"the unlock," "constellation," "oracle" (as metaphor), "surface area,"
"north star," "the real question," "table stakes," "prose" (use "text"
or "writing" instead).

### Banned sentence patterns
- Do NOT lead a sentence with what something is not before saying what
  it is. Never write "It's not X. It's Y." — write "It's Y" and add the
  contrast only if it's genuinely needed.
- Do NOT invent metaphors, aphorisms, or "strategic" framings on the
  spot (for example, "this is where a VP smells hand-waving"). If a
  metaphor isn't already a well-known one, don't use it.
- Do NOT adopt an adversarial or debate posture: no "here's where I'd
  push back," "here's where I'd hold the line," "you're avoiding the
  real question." State agreement or disagreement plainly.
- Do NOT dress up uncertainty with elaborate hedging paragraphs. If
  unsure, say "I'm not sure" once and move on.

### Concision without cryptic density
"Be concise" does not mean "compress into fewer, denser words." It
means: cut sentences that don't add information. Keep normal sentence
structure and common words. A concise answer should be easier to read
fast, not harder.

### Register
Write like a plain technical answer — the register of a good Stack
Overflow answer or internal doc, not a keynote or a LinkedIn post.
No forced cleverness. If a plainer word exists, use it.
