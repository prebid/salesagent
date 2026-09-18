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

- If you write a block of logic that is structurally similar to a block already in the codebase (same pattern, different variables), you **MUST** extract a shared helper function
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

| Guard | Enforces | Test file |
|-------|----------|-----------|
| Schema inheritance | Redeclarations are inherited unless reshaped or weakened | `test_architecture_schema_inheritance.py` |
| No ToolError anywhere but the edge | Business logic raises AdCPSalesAgentError; ToolError is minted only on the way out | `ruff-boundary.toml` (TID251 over `src/` + `scripts/`, in `make quality`) + `test_ruff_boundary_bans.py` |
| Transport-agnostic _impl | `_impl` has zero transport imports | `test_transport_agnostic_impl.py` |
| `_impl` signature | Every implementation is exactly `(req: <DTO>, identity: ResolvedIdentity)` for a protected tool, `identity: AccountIdentity` when the DTO requires `account`, or `identity: PublicIdentity` for a public one; the DTO matches the registry row, and the identity annotation IS the row's credential policy (`ToolSpec.requires_credential` derives it; the registry refuses `AccountIdentity` on a DTO whose `account` is optional) | `ToolImpl` protocol on `ToolSpec.impl` (mypy) + `.ast-grep/rules/impl-signature-is-request-and-identity.yml` |
| One identity constructor | `ResolvedIdentity(...)` / `AccountIdentity(...)` / `PublicIdentity(...)` only in the resolver and `PrincipalFactory`; the resolver builds the identity once, account inside, and nothing calls `model_copy` on one | `.ast-grep/rules/resolved-identity-constructed-only-by-its-owners.yml` |
| Principal rows are repository-private | `src.core.database.models.Principal` is importable only by the four repository modules that query it; a tool reads `identity.principal`, an admin view uses `PrincipalRepository` | `ruff-boundary.toml` (TID251) |
| Principal and account obtained from the identity alone | `repositories.principal`, `repositories.principal_lookup`, `auth_utils`, `repositories.account`, and `uow.AccountUoW` are importable only by the resolver, the repositories, the admin UI, the setup scripts, and the two account-management tools; a tool reads `identity.principal` / `identity.account` | `ruff-ownership.toml` (TID251) |
| Auth refusals minted by the resolver alone | `AdCPAuthRequiredError` / `AdCPAuthenticationError` raised only by the resolver; a protected tool's `ResolvedIdentity` carries principal and tenant by type, so nothing downstream re-checks (a seller's `require_auth` brand policy is asked by the resolver through `requires_credential(tenant)`) | `ruff-boundary.toml` (TID251) |
| Account resolved by the resolver alone | `src.core.database.repositories.account_lookup` is importable only by the resolver, which builds the identity with the request's account inside; tools read `identity.account` | `ruff-boundary.toml` (TID251) |
| Context written by the boundary alone | No `context=` keyword and no `ContextObject` import outside the boundary and the schemas; `AdcpResponse` refuses the field on construction and assignment | `ruff-boundary.toml` (TID251) + `.ast-grep/rules/context-is-written-by-the-boundary-alone.yml` + `test_response_context_is_boundary_owned.py` |
| Query type safety | DB queries use types matching column definitions | `test_architecture_query_type_safety.py` |
| Serialize only at the edges | No `.model_dump()`, `.model_dump_json()`, `pydantic_core.to_json` or `to_jsonable_python` outside the named edge modules; a tool hands the model through | `ruff-serialization.toml` (TID251) + `.ast-grep/rules/serialize-only-at-the-edges.yml` |
| Environment read once | No `os.environ` / `os.getenv` under `src/` or `scripts/` outside the settings loader (`src/core/config.py`) and the two writes of variables another library reads; a composition root calls `load_settings()` and everything else reads a named fact off the object | `ruff-environment.toml` (TID251) + `test_ruff_boundary_bans.py` |
| Test flags never reach production | No read of `adcp_testing` under `src/` outside the settings loader that declares it; a behavior that differs under test reads a real input (a tenant column, an `AdapterConfig` row, a settings field) that the test seeds. The loader's seven properties that still fork on it are pinned shrink-only, not exempted (GH #2255) | `.ast-grep/rules/test-flag-never-reaches-production.yml` + `test_ast_grep_test_flag_ban.py` + `test_test_flag_properties_only_shrink.py` |
| internal_detail is an exception | `internal_detail=` (and `x.internal_detail =`) never takes an authored string in any spelling (literal, f-string, `+`, `%`, `.format`, `str()`, conditional), under `src/`, `scripts/` or `tests/`; the parameter is typed `BaseException \| None`, and the boundary writes one record per failure with the traceback attached when the error has a `__cause__` or an `internal_detail` | `.ast-grep/rules/internal-detail-is-an-exception.yml` + mypy on `src/` + `test_tool_error_logging.py` |
| No direct DB access | No `get_db_session()` or `session.add()` anywhere outside repositories/UoW/infrastructure | `test_architecture_repository_pattern.py` |
| Migration completeness | Every migration has non-empty `upgrade()` and `downgrade()` | `test_architecture_migration_completeness.py` |
| No raw MediaPackage select | All MediaPackage access goes through repository, not raw `select()` | `test_architecture_no_raw_media_package_select.py` |
| No import-time filesystem I/O | `src/` and `scripts/` modules touch no files while being imported | `test_architecture_no_import_time_fs_io.py` |
| No raw select outside repos | All ORM model queries go through repositories, not raw `select()` | `test_architecture_no_raw_select.py` |
| No raw egress | All outbound HTTP goes through `src/core/security/outbound_http.py`; the gateway modules bind their imports privately so re-export is an ImportError | `ruff-egress.toml` (TID251 over `src/` + `scripts/`, `--ignore-noqa`, in `make quality-ci`) + `test_ruff_egress_bans.py` |
| No destination rewrite | Nothing under `src/` rebuilds a URL or swaps its netloc/scheme ahead of the gateway | `test_architecture_no_destination_rewrite.py` |
| BDD no-op Then steps | Then steps must assert, not delegate to `_pending()`-like no-ops | `test_architecture_bdd_no_pass_steps.py` |
| BDD assertion reachability | A Then must not be able to RETURN without executing a meaningful assertion — presence is not enough | `test_architecture_bdd_no_trivial_assertions.py` |
| BDD no dict registry | Given steps must use factories, not raw dicts | `test_architecture_bdd_no_dict_registry.py` |
| BDD no duplicate steps | No 3+ step functions with identical bodies | `test_architecture_bdd_no_duplicate_steps.py` |
| BDD no silent env | No `ctx.get("env")` or `hasattr(env, ...)` in step functions | `test_architecture_bdd_no_silent_env.py` |
| Code duplication (DRY) | Duplicate block count in src/ and tests/ cannot increase | `check_code_duplication.py` (make quality) |
| Workflow tenant isolation | WorkflowRepository queries join DBContext for tenant scoping | `test_architecture_workflow_tenant_isolation.py` |
| No split mock assertions | Tests use `assert_called_once_with()`, not `assert_called_once()` + `call_args` | `test_architecture_weak_mock_assertions.py` |
| Single migration head | Alembic migration graph has exactly one head | `test_architecture_single_migration_head.py` |
| Pre-commit no additional_deps | No `additional_dependencies` in `.pre-commit-config.yaml` (ADR-001) | `test_architecture_pre_commit_no_additional_deps.py` |
| Pre-commit hook count | Commit-stage hooks stay within D27 ceiling (≤12) | `test_architecture_pre_commit_hook_count.py` |
| No tenant.config access | Per-field tenant columns, not legacy `tenant.config` | `test_architecture_no_tenant_config.py` |
| JSONType columns | JSON DB columns use `JSONType`, not plain `JSON` | `test_architecture_jsontype_columns.py` |
| No defensive RootModel | No `hasattr(x, "root")` without `# noqa: rootmodel` | `test_architecture_no_defensive_rootmodel.py` |
| Import usage in src/ | Classes/functions used in `src/` must be imported | `test_architecture_import_usage.py` |

**Rules for guards:**
- Allowlists can only shrink — never add new violations, fix them instead
- Every allowlisted violation has a `# FIXME(#<gh-issue>)` comment at the source location — reference a GitHub issue/PR number, never a local beads id (beads ids don't resolve for outside contributors)
- When you fix a violation, remove it from the allowlist (the stale-entry test reminds you)

---

## AdCP spec version

This project targets AdCP spec **3.1.1** via the `adcp==6.6.0` Python SDK. See
[docs/adcp-spec-version.md](docs/adcp-spec-version.md) for the version mapping
and bump procedure. The CI guard at `tests/unit/test_adcp_spec_version.py`
fails on pin drift.

### Spec-grounding gate (MANDATORY before implementing protocol behavior)

**Any change to AdCP/protocol BEHAVIOR** — a tool's request/response contract, error emission, idempotency, governance, or capabilities — **must cite, before you write the code, the authoritative spec section + version that mandates it, plus the conformance storyboard step that grades it** (or note "ungraded"). Record the citation in the PR description and/or the planning note.

- **Which version is authoritative:** the version the repo PINS — *unless* there is active work to comply with a different target version (a bump/migration in flight), in which case that TARGET version is the pin. Confirm which applies first.
- **Where the spec lives** (`github.com/adcontextprotocol/adcp`): the written spec at `docs/building/by-layer/L<0-4>/*.mdx`, read at the pinned tag (idempotency, auth and error taxonomy are all in `L1/security.mdx`) — there is NO `dist/docs/<version>/` for the pinned version, `dist/docs/` stops at `3.1.0`; the graded, executable contract does keep a version-numbered root, at `dist/compliance/<version>/<area>/*.yaml` (`domains/`, `protocols/`, `universal/`, `test-kits/` — there are no yaml files directly under `<version>/`). That writing is NOT vendored — the venv carries only `_schemas/` — so read it with `gh api "repos/adcontextprotocol/adcp/contents/docs/building/by-layer/L1/security.mdx?ref=v3.1.1" --jq '.content' | base64 -d`, which returns the literal text. `WebFetch` on the `raw.githubusercontent.com` URL is the fallback when the gh rate limit is spent, but it summarizes: trust its verbatim quotes, not its interpretation. The installed `adcp` SDK — codes, types, even reference implementations such as `adcp.server.idempotency` — is a CROSS-CHECK, **not** the authority; it can diverge from the spec, and does (see #2215).
- **Why:** grounding protocol behavior in downstream artifacts (an internal contract item, or the mere existence of an SDK error code) instead of the written spec + storyboard has produced an entire feature built inverse to the spec. The spec is the contract; everything else is derived.
- **Enforcement:** reviewers reject protocol-behavior changes that don't cite the spec; this complements the pin-drift guard above. Background: [docs/adcp-spec-version.md](docs/adcp-spec-version.md).

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
- **Enforced by:** `tests/unit/test_architecture_schema_inheritance.py`, which grades
  redeclarations against the library parent. There is deliberately no suite comparing a
  model's field set to the pinned schema: the DTO IS the pinned model minus a declared
  omission, so what it declares is inherited and a comparison only asserts that Python
  inheritance works. See
  [docs/development/building-tools.md](docs/development/building-tools.md) § What a tool
  declares: the DTO.
- The inheritance guard was once deleted on the ground that such a guard "has to
  enumerate how this repo spells its imports — every spelling it does not know is a
  silent hole." The premise was true of the previous implementation and is no longer true of
  this one: the REDEFINITION rule decides membership by walking the live MRO and testing
  `__module__`, so it consults no import spelling and has no spelling to miss. The
  companion `test_all_library_types_have_local_subclass` is still alias-keyed, and is the
  remaining place where a differently-spelled import goes unexamined.
- A redeclaration needs an allowlist row unless it is **neither reshaped nor weakened**:
  same annotation (or a subclass), nullability not added, `is_required()` not relaxed,
  metadata a superset, no default introduced. A redeclaration that keeps the parent's
  type but is *weaker* — dropping a `Ge` or a `MinLen`, going required→optional — needs
  a row NAMING the weakened axis. Do not widen the admission rule to make it pass: a
  hand-written row names itself and can be audited, whereas a derived rule that admits a
  widening is invisible and permanent.

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

`BaseUoW` and the per-domain classes live in `src/core/database/repositories/uow.py`. A UoW
is how an `_impl` gets a repository — it is the only sanctioned place a session is created,
which is what keeps `get_db_session()` out of business logic.

`uow.session` raises a `DeprecationWarning`. If a repository has no method for the data you
need, add one; do not reach past it to the raw session.

See [patterns-reference.md](docs/development/patterns-reference.md) §§1–2 for both patterns in full — canonical repository files, worked correct/wrong examples, and how to add a repository.

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

`WireSerializerMixin` (`src/core/schemas/_base.py`) is the one serializer, and it
carries exactly two concerns: `NestedModelSerializerMixin` re-serializes children by their
INSTANCE rather than the declared (library) type, which is what keeps a local subclass's
extra fields on the wire; and required-nullable retention keeps a required field whose
value is `None` on the wire under `exclude_none`. A per-class "last word" hook
(`_finish_wire`) and a per-class strip set (`_INTERNAL_ONLY_FIELDS`) used to be the third
and fourth. Both are gone: every use was either a redeclaration weakening the library type
and then patching the output back (the fix is to not redeclare), or a strip of a field that
belongs on the wire (`Product.expires_at` is pinned), or bookkeeping that belongs off the
model entirely (an adapter's carrier type, not a wire field). A field that must exist on the
model and not on the wire is `Field(exclude=True)` at its declaration, nowhere else.

**And business logic never calls `model_dump()` at all.** A model is the value; a dict built
from it mid-flow is a second representation that drifts. Serialization happens at named
edges, each with one owner: the wire (`src/core/tools/_wire.py`), outbound HTTP to another
agent or a webhook target (the agent registries, the webhook services, the registration
gate), the idempotency payload hash (`src/core/idempotency_canonical.py`), the persistence
edge, the admin UI's JSON responses (`src/admin/blueprints`), a wire that `to_wire` does
not own, the two error helpers that project issues and details to their wire shape
(`src/core/errors/issues.py`, `src/core/errors/details.py`), and the one schema module that
re-dumps a nested child inside the one serializer (`src/core/schemas/_base.py`). The
edge list itself lives in `ruff-serialization.toml` and
`.ast-grep/rules/serialize-only-at-the-edges.yml`; read it there rather than counting from
here. The persistence edge is
precisely: the engine's JSON serializer registered in
`src/core/database/database_session.py` behind the `JSONType` column (hand the model to
the column and it serializes through that serializer; a tool never calls anything), the
ORM `@validates` hooks in
`src/core/json_validators.py` that normalize a column document, and the documents the
repositories and the context manager compose or compare (`workflow.py`, `media_buy.py`,
`account_serialization.py`, `idempotency_attempt.py`, `context_manager.py`). A tool, a
helper, or a pydantic validator on a wire model may not serialize; "validator" in that
prohibition means a pydantic field or model validator, never the ORM hooks. A receiving
model adopts a sibling generated class by reading its attributes (`from_attributes=True`,
or a `mode="before"` validator reading a `RootModel`'s `root`), never by a dump in the
caller. A field that is off the wire but must persist has no spelling on a wire model:
`Field(exclude=True)` strips it from the wire, from persistence and from the idempotency
hash alike, so such a field belongs on a repository-owned carrier.
**Enforced by:** `ruff-serialization.toml` (TID251 on `pydantic_core.to_jsonable_python` and
`pydantic_core.to_json`, per-file-ignores for exactly the edge modules) and
`.ast-grep/rules/serialize-only-at-the-edges.yml` (`.model_dump(...)` and
`.model_dump_json(...)` outside the same edge list). The edge list lives in those two files.

### 5. Transport boundary: one path to every implementation
All tools have two layers: a **transport** (MCP, A2A, REST) that parses a request and writes
a response, and **business logic** (`_impl` functions). Between them sits ONE seam —
`src/core/tools/_boundary.py` — and there are no per-tool wrappers at all.

**`_impl` functions** (business logic layer):
```python
async def _create_media_buy_impl(
    req: CreateMediaBuyRequest,
    identity: ResolvedIdentity,    # never Context, headers or a token
) -> CreateMediaBuyResult:
    # Business logic only — no transport awareness, no account resolution, no idempotency
    ...
```

**Transports** name a tool and hand over the raw payload and the request headers:
```python
# Every transport, one call. The registry says which function runs and whether the
# credential must verify; the boundary validates the payload and resolves the caller.
response = await serve("create_media_buy", payload, request.headers, TransportProtocol.REST)
```

`serve` validates the payload into the registry row's DTO, resolves the identity once
(`_resolve_identity`, private to the boundary), resolves the account the request names,
honours its `idempotency_key`, stamps the buyer's `context` and the served version onto the
response, and calls the implementation as `impl(req=..., identity=...)`. All of those are
properties of the REQUEST, not steps in the work, and doing them once is what keeps the
transports from disagreeing — the fifteen `*_raw` wrappers this replaced disagreed about
exactly those.

**Controller and service.** An `_impl` is a CONTROLLER: it establishes who is calling and
then delegates. The work itself belongs in a service function that takes an already-resolved
caller and asks nothing about transports, auth, or idempotency:

```python
def _sync_creatives_impl(req, identity: ResolvedIdentity):   # controller
    # The type carries the boundary's decision: principal and tenant are not optional.
    return sync_creatives(req, identity=identity, principal_id=identity.principal.principal_id, tenant=identity.tenant)

def sync_creatives(req, *, identity, principal_id, tenant):   # service
    ...
```

A tool never re-checks what the boundary decided. A protected tool declares
`identity: ResolvedIdentity` and the resolver refuses an anonymous caller before it runs;
a public tool (`get_products`, `list_creative_formats`, `get_adcp_capabilities`) declares
`identity: PublicIdentity` and branches on `identity.principal is None` itself. The registry
derives `requires_credential()` from that annotation, so there is no `auth=` literal on the
row and no `require_principal` helper to call.

**A controller never calls another controller.** When one tool needs another tool's work --
`update_media_buy` uploads a package's inline creatives (`media_buy_update.py:945`) -- it
calls the SERVICE. Calling the other `_impl` re-runs an auth check that already passed,
and drags the outer request's `idempotency_key` into a function with no business seeing it.
`sync_creatives` is the extracted one; extract the next service when a second tool needs it,
not before.

**Rules for `_impl` functions:**
- Accept `ResolvedIdentity` (protected), `AccountIdentity` (protected, and the DTO requires `account`), or `PublicIdentity` (public), never `Context`, raw headers, or a token
- Raise `AdCPSalesAgentError` subclasses, never `ToolError` (that's transport-specific)
- Zero imports from `fastmcp`, `a2a`, `starlette`, or `fastapi`
- No auth extraction, tenant resolution, account resolution, idempotency, or context echo — the boundary's job
- Declare exactly `(req: <DTO>, identity: <one of those three>)`: nothing else can be supplied, and the annotation is the credential policy

**Rules for transports:**
- Hand the raw payload and the request headers to `serve(tool_name, payload, headers, protocol)` — never resolve an identity, never call an implementation directly
- Catch `AdcpFailure`, serialize its response with `to_wire`, and add only the transport's own failure marker (an HTTP status, an MCP tool error, an A2A task state)

**Substituting an implementation in a test** patches the registry ROW, not a module
attribute: `TOOLS` holds the function object, so `patch("...._x_impl")` renames something
nothing consults. Use `tests/helpers/capture_wrapper_req.py` (`stub_impl`, `registry_impl`).

**Enforced by:** `test_transport_agnostic_impl.py`, the `ToolImpl` protocol typing `ToolSpec.impl` in `src/core/tools/registry.py` (mypy) plus `.ast-grep/rules/impl-signature-is-request-and-identity.yml`, and `ruff-boundary.toml`'s TID251 bans on `fastmcp.exceptions.ToolError` and on the two auth errors outside the resolver and `require_*`

Worked transport-boundary and `_impl` examples: `.claude/rules/patterns/mcp-patterns.md` and [patterns-reference.md §6](docs/development/patterns-reference.md).

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

**Do not "fix" this.** It has been mistaken for a bug at least twice. The tells are a scenario
that sends a pin-permitted-but-undeclared field and fails with `INVALID_REQUEST` in dev, or a
storyboard `known_failures.txt` entry for a tolerance check. Neither is a defect in the strip:

- if you SHOULD accept the field, declare it on the DTO — that is the only mechanism;
- if you should NOT, the scenario is wrong and gets fixed or deleted.

Declaring a field only to satisfy a tolerance obligation is its own defect — it invents a spec
field. `ListAccountsRequest.idempotency_key` was added that way once and removed again:
**reads take no idempotency key.** A read is idempotent by construction, so there is no
at-most-once guarantee for a key to carry, and `list-accounts-request.json` does not declare
one. The same holds for every read tool.

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

`AdCPSalesAgentError` is generic in its details type and each of the 48 concrete subclasses
binds one, so **mypy is the enforcement** — it rejects a dict and a foreign details class at
every raise site. Three things follow, and each has been got wrong:

- **Never a runtime check for what the type already refuses.** A `__new__` branch validating
  `details` was added and removed in one day: the one site a dict escaped from was annotated
  `type[AdCPSalesAgentError]` with the parameter dropped, which erases the type variable to
  `Any`. The type system was not consulted, not insufficient. Parameterize the annotation.
- **Never a `try` around the boundary's failure builder.** `failure_response` runs inside the
  boundary's own `except Exception`, so a raise there is past the catch — but the answer is to
  make the raise impossible upstream, not to catch it and answer INTERNAL_ERROR while the
  record says otherwise.
- **A code with no subclass is a gap, not a reason to name it on the base.**
  `AdCPSalesAgentError(error_code="NOT_CANCELLABLE")` is the one way to put a code on the wire
  with nothing bound to it. Add the subclass.

No raise site authors text: `message`, `recovery`, `suggestion` and `status_code` are
read-only properties over `CODE_TABLE`. A diagnostic sentence is logged, never carried —
`internal_detail` takes an exception, and no `ErrorDetails` class has a free-text field.

**The full rules, and the reasoning for each:**
[docs/design/error-architecture.md](docs/design/error-architecture.md). Read it before adding
or changing a raise site; this entry is a pointer and the document is the contract.

### 11. Test flags never reach production behavior

**A production path that branches on `adcp_testing` is a bug, always.**

The flag says a suite is running. It says nothing about the seller being served, so a fork on
it makes the suite grade a seller that no deployment runs — and makes the suite's verdict
conditional on the suite being what ran it. Both fixes are the same shape and neither is the
fork: give the behavior a **real input** a deployment can set (a tenant column, an
`AdapterConfig` row, a settings field) and let the test seed it like any other state.

The same rule binds the test side: patching a production step out so a scenario can run is
how a scenario comes to grade a seller that does not exist. See
[tests/CLAUDE.md](tests/CLAUDE.md) § Which kind of test.

**Enforced by:** `.ast-grep/rules/test-flag-never-reaches-production.yml` (ast-grep, not
ruff — the flag is an attribute read, and TID251 only sees imported names). `src/core/config.py`
is exempt because it declares the field; the seven properties there that read it are the
outstanding violations, pinned shrink-only by
`tests/unit/test_test_flag_properties_only_shrink.py` and owned by GH #2255.

---

## Project overview

The Prebid Sales Agent is a Python application with the following components:
- **MCP Server**: FastMCP tools for AI agents (via nginx at `/mcp/`)
- **Admin UI**: Google OAuth secured interface (via nginx at `/admin/` or `/tenant/<name>`)
- **A2A Server**: python-a2a agent-to-agent communication (via nginx at `/a2a`)
- **Multi-Tenant**: Database-backed isolation with subdomain routing
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

**A migration changes the structure in `upgrade()` and reverts it in `downgrade()`. That is all it does.**

- **Do not preserve data across a migration.** No backup table, no copy-then-restore, no
  conditional rewrite so a downgrade can put rows back. `downgrade()` reverts the structure;
  if data is lost, that is accepted. A backup table created "just in case" is a permanent
  artifact no model declares, and the health check then reports it forever.
- **Never stack a revision to fix a revision that has not shipped.** The rule is not "never
  edit a committed migration", it is never edit a migration that has RUN somewhere. A revision
  that exists only on an unmerged branch has run nowhere: edit it, or delete it. Adding a
  second revision to clean up after a first one you wrote this week is two migrations where the
  problem needed zero.
- **Check before you decide which case you are in:** `git cat-file -e main:alembic/versions/<file>`
  says whether main has it. If a revision you are editing has children on the branch, re-point
  the child's `down_revision` in the same change.
- **A migration that has shipped is immutable.** Then, and only then, a new revision is correct.

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

Local secrets live in `.env.secrets` (required for a working stack: Gemini, Google OAuth,
GAM OAuth, super-admin emails, Approximated).
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
