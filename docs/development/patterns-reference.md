# Architecture patterns reference

This document maps every key pattern to its **canonical implementation file** and names the **anti-patterns** that remain in the tree as tracked debt. Follow the canonical pattern, not the anti-pattern, even when the anti-pattern appears in surrounding code.

> **Why this document exists:** The codebase has two eras — legacy code and the target architecture. If you pattern-match from surrounding code, you may follow a legacy pattern. This document tells you which files represent the target architecture.

Structural guard tests under `tests/unit/test_architecture_*.py`, five TID251 ruff configs, the `.ast-grep/rules/` structural rules, and mypy enforce these patterns on every `make quality` run; the standards in [Engineering standards](engineering-standards.md) restate the ones a reviewer applies. [Architecture principles](architecture-principles.md) states the principles behind them.

## Contents

- [1. Repository pattern (CP-3)](#1-repository-pattern-cp-3)
- [2. Unit of Work (UoW)](#2-unit-of-work-uow)
- [3. Structural guards and allowlists](#3-structural-guards-and-allowlists)
- [4. The test harness](#4-the-test-harness)
- [5. Factory fixtures for integration tests](#5-factory-fixtures-for-integration-tests)
- [6. Transport boundary (CP-5)](#6-transport-boundary-cp-5)
- [7. Error hierarchy](#7-error-hierarchy)
- [8. DRY — shared validation](#8-dry--shared-validation)
- [Quick reference: where to look](#quick-reference-where-to-look) — pattern-to-file lookup table
- [Legacy code awareness](#legacy-code-awareness) — the files not to pattern-match from

## 1. Repository pattern (CP-3)

All database access goes through repository classes. `_impl` functions never contain raw `select()`, `session.scalars()`, `session.add()`, or direct model imports for data access.

**Canonical file:** [`src/core/database/repositories/media_buy.py`](../../src/core/database/repositories/media_buy.py)

```python
class MediaBuyRepository:
    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def get_by_id(self, media_buy_id: str) -> MediaBuy | None:
        return self._session.scalars(
            select(MediaBuy).where(
                MediaBuy.tenant_id == self._tenant_id,
                MediaBuy.media_buy_id == media_buy_id,
            )
        ).first()
```

Every repository shares these properties:

- The constructor takes `session` and `tenant_id`, so every query scopes to the tenant.
- Write methods (`create_from_request`, `update_fields`, and so on) add to the session but never commit — the UoW commits.
- A method returns ORM model instances, not dicts.
- A lookup that every caller follows with the same guard gets a `*_or_raise` sibling (`get_by_id_or_raise` raises `AdCPMediaBuyNotFoundError`), so the guard exists once.

### Effects a rollback cannot reach

A repository that owns effects mixes in `SessionEffectsMixin` ([`repositories/effects.py`](../../src/core/database/repositories/effects.py)); `CreativeRepository` is the only repository that does. Use it instead of an `if dry_run` at the call site:

```python
# The result is not read inline (a queued job, a notification): defer it.
# It runs only if this transaction commits; a preview's rollback drops the queue.
repo.after_commit(lambda: notify(buyer), label="buyer notification")

# The result builds the response and cannot be deferred: suppress it for a preview.
render = repo.outbound(lambda: creative_agent.render(asset), preview_result=None)

# A savepoint that owns its effects — both are discarded if it rolls back.
with repo.savepoint():
    ...
```

**Anti-pattern** (exists in codebase, allowlisted under `FIXME(#1119)` in `tests/unit/test_architecture_no_raw_select.py`):
```python
# WRONG: raw select() inside _impl function — src/core/tools/media_buy_create.py:2331
currency_stmt = select(CurrencyLimit).where(
    CurrencyLimit.tenant_id == tenant.tenant_id,
    CurrencyLimit.currency_code == request_currency,
)
currency_limit = session.scalars(currency_stmt).first()
```
`CurrencyLimitRepository` (`repositories/currency_limit.py`) already answers this query.

**Anti-pattern: inline session management and raw model construction in business logic:**
```python
# WRONG
def _create_media_buy_impl(req, identity):
    with get_db_session() as session:
        mb = MediaBuy(
            media_buy_id=generate_id(),
            buyer_ref=req.buyer_ref,          # manual field plucking
            tenant_id=identity.tenant_id,
            status="pending_approval",         # magic string
            ...
        )
        session.add(mb)
```
Use model factory methods or repository `create_from_*()` methods instead, and reach the session through a UoW. A `get_db_session()` call outside a repository, a UoW, or the infrastructure modules is banned.

**Add a repository:** Create `src/core/database/repositories/your_model.py` modeled on `media_buy.py`, mix in `SessionEffectsMixin` if it owns effects, add it to `repositories/__init__.py`, and wire it into the appropriate UoW. `test_architecture_no_raw_select.py` catches raw `select()` calls outside repository files.

**Enforced by:** `test_architecture_no_raw_select.py`, `test_architecture_repository_pattern.py`, `test_architecture_query_type_safety.py`, `test_architecture_no_raw_media_package_select.py`

## 2. Unit of Work (UoW)

The UoW owns the session and the transaction: it opens one on entry, commits on a clean exit, and rolls back if the block raises. It exposes the repositories, and it is the only sanctioned place that creates a session — which is what keeps `get_db_session()` out of business logic.

**Canonical file:** [`src/core/database/repositories/uow.py`](../../src/core/database/repositories/uow.py)

```python
class MediaBuyUoW(BaseUoW):
    media_buys: MediaBuyRepository | None

    def _init_repos(self) -> None:
        assert self._session is not None
        self.media_buys = MediaBuyRepository(self._session, self._tenant_id)

    def _clear_repos(self) -> None:
        self.media_buys = None
```

Use it inside an `_impl`:
```python
with MediaBuyUoW(identity.tenant_id) as uow:
    media_buy = uow.media_buys.get_by_id(req.media_buy_id)
```

### `dry_run` is a preview, not a second write path

`BaseUoW(tenant_id, dry_run=True)` makes the whole unit a preview. **The identical write path runs**, every read inside the block sees its own uncommitted writes, and on a clean exit the UoW rolls the transaction back instead of committing it. Preview and live parity therefore holds by construction: no simulated write path exists to diverge from the live one. A shadow preview state machine reintroduces that class of bug every time it drifts.

```python
# The results the caller built describe exactly what a live run would have persisted.
with MediaBuyUoW(identity.tenant_id, dry_run=req.dry_run) as uow:
    ...
```

A rollback disposes of the transaction only. Route the effects it cannot reach — an outbound call, a queued job, a notification — through the same boundary instead of gating them at their call sites: register each one with `repo.after_commit(...)` or `repo.outbound(...)`, which [Repository pattern](#1-repository-pattern-cp-3) shows. **A call site inside the transaction never asks whether it is a preview.**

### `uow.session` is deprecated

`BaseUoW.session` emits a `DeprecationWarning` at runtime:

```
uow.session is deprecated — use repository methods instead of raw session access.
```

A `session = uow.session` read in the codebase is tracked debt. If you need data access that no repository method provides, **add a repository method** instead of reaching for the raw session.

**Enforced by:** `test_architecture_repository_pattern.py`

## 3. Structural guards and allowlists

Four kinds of mechanism check the architecture invariants mechanically on every `make quality` run. Reach for them in this order, because a mechanism that refuses a shape is stronger than one that scans for it:

| Mechanism | Use it for | Where it lives |
|-----------|-----------|----------------|
| A load-time refusal in the code itself | An invariant the code can check once, at import or construction: `ToolSpec.__post_init__`, `_register_tool`, `__init_subclass__` on `AdCPSalesAgentError`, the import-time totality check on `PYDANTIC_KEYWORD_MAP` | The module that owns the invariant |
| TID251 in a ruff config | "This name is importable only here": the resolver, the `_impl` names, `ToolError`, the auth errors, `ContextObject`, the serializers, `os.environ`, every HTTP client | `ruff-boundary.toml`, `ruff-ownership.toml`, `ruff-serialization.toml`, `ruff-environment.toml`, `ruff-egress.toml` |
| An ast-grep rule | Structural shapes ruff and mypy cannot express: an exact parameter list, "constructed only here", "this argument is never a string literal" | `.ast-grep/rules/*.yml`, run by `ast-grep scan --config sgconfig.yml` |
| An AST-scanning pytest guard | Everything else, especially test-suite discipline | `tests/unit/test_architecture_*.py` (157 files) |

### Core rules

- **New code that introduces a violation fails `make quality` immediately** — no exceptions
- **Allowlists track pre-existing debt and only shrink** — never add new entries
- **Every allowlisted violation cites a GitHub issue** (`# FIXME(#1119)`), never a local beads id — beads ids do not resolve for outside contributors, and `check_fixme_citation_count.py` ratchets that spelling to zero
- **Removing a FIXME without fixing the underlying issue is not acceptable** — the FIXME is a contract
- **A path exempts a ban; a comment never does.** `ruff-egress.toml` runs with `--ignore-noqa`, so only a `per-file-ignores` row exempts a file — and that row is greppable and reviewable
- **Prove that a guard grades before you trust it.** Reintroduce the defect it targets and watch it fail; a passing guard is not yet a grading guard

Pytest guards key their allowlists on `(file_path, function_name)` tuples, not line numbers, so they survive line shifts from unrelated changes. Each carries a stale-entry test, so a fixed violation left on the list fails too.

### Ratchet counters

A counter tracks the debt that no shrink-only list can express, against a committed baseline that may only go down: type-ignores, mypy untyped defs, ruff complexity, code duplication (`.duplication-baseline`), admin raw sessions (`.admin-raw-session-baseline`), and beads ids in FIXMEs (baseline zero). The fast counters run in `make quality`; the two too slow for it (mypy `--check-untyped-defs`, pylint duplication) run in the `quality` tox env.

Read [Structural guards](structural-guards.md) for the design rationale, and run `ls tests/unit/test_architecture_*.py` for the full pytest inventory.

**Enforced by:** `make quality` (which runs `make quality-ci`, then `pytest tests/unit/ tests/harness/`)

## 4. The test harness

The **test harness** at [`tests/harness/`](../../tests/harness/) provides domain-specific test environments. These environments handle mock wiring, credential construction, UoW setup, database session binding, and multi-transport dispatch, so a test states behavior and nothing else.

**Base classes:** [`tests/harness/_base.py`](../../tests/harness/_base.py) — `BaseTestEnv` (unit, mocked DB) and `IntegrationEnv` (real PostgreSQL).

The authoritative environment list is the table in [Test architecture](../../tests/CLAUDE.md) § "The harness system", plus the symbol index at `.agent-index/harness/`. This section covers the patterns, not the inventory. [BDD harness architecture](../design/bdd-harness-architecture.md) describes how one Gherkin scenario comes to run on every transport: the parametrization, the address table, and the layer that owns each decision.

### How it works

Each domain has an environment class that subclasses `BaseTestEnv` or `IntegrationEnv`:

```python
class DeliveryPollEnv(DeliveryPollMixin, BaseTestEnv):
    MODULE = "src.core.tools.media_buy_delivery"
    EXTERNAL_PATCHES = {
        "uow": f"{MODULE}.MediaBuyUoW",
        "adapter": f"{MODULE}.get_adapter",
        "pricing": f"{MODULE}._get_pricing_options",
        "circuit_open": f"{MODULE}._is_circuit_breaker_open",
    }

    def _configure_mocks(self) -> None: ...   # Wire happy-path defaults
    def add_buy(self, media_buy_id, ...) -> MagicMock: ...  # Fluent data API
    def call_impl(self, **kwargs) -> Any: ...  # Call production _impl
```

**A test that uses the harness** (from [`tests/unit/test_delivery_poll_behavioral.py`](../../tests/unit/test_delivery_poll_behavioral.py)):

```python
from tests.harness.delivery_poll_unit import DeliveryPollEnv

def test_only_completed_buys_returned(self):
    """Covers: UC-004-ALT-STATUS-FILTERED-DELIVERY-02"""
    with DeliveryPollEnv() as env:
        env.add_buy(media_buy_id="mb_completed", start_date=date(2025, 1, 1), end_date=date(2025, 6, 30))
        env.add_buy(media_buy_id="mb_active", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        env.set_adapter_response("mb_completed", impressions=5000, spend=250.0)

        response = env.call_impl(status_filter="completed")

        returned_ids = [d.media_buy_id for d in response.media_buy_deliveries]
        assert returned_ids == ["mb_completed"]
```

The test states behavior alone: it carries no mock wiring and no MagicMock scaffolding.

Supporting modules: `_mixins.py` (shared fluent APIs, including the webhook and circuit-breaker seams), `_mock_uow.py` (UoW mock builder), `_identity.py` (identity factory), `_realize.py` (`@realize_e2e`, one method that dispatches in-process or against the live stack), `assertions.py`, `client.py` + `address_table.py` + `dispatchers.py` (dispatch), and `transport.py` (`Transport`, `TransportResult`, `DeliverResult`).

### Multi-transport testing

Dispatch goes through `env.call_via(transport, **kwargs)`. The transport is an argument, so the same test body runs on every wire:

```python
from tests.harness.transport import Transport

@pytest.mark.parametrize("transport", [Transport.A2A, Transport.MCP, Transport.REST])
def test_something(self, integration_db, transport):
    with CreativeSyncEnv() as env:
        result = env.call_via(transport, creatives=[...])
        assert result.is_success
```

Three facts govern this:

- **The credential is a headers dict, never an identity object.** `env.credential()` returns the env principal's token plus `x-adcp-tenant`; `credential(token=None)` presents nothing, `credential={}` sends no headers at all. Every leg dispatches headers, and the production resolver builds the identity from them — so the test exercises the real resolution path.
- **Every `Transport` member is a real wire.** The harness holds no `Transport.IMPL`, no `ImplDispatcher`, and no synthesized error envelope. Modeling a direct in-process call as a transport gave every "assert on the wire" rule a way around it. A direct call stays correct where the obligation is about what `_impl` returns or raises: call `env.call_impl(...)` and assert against the returned DTO, or use `pytest.raises` against the error class.
- **Assertions read the bytes the transport produced.** On a failure, `result.assert_wire_error(code, ...)` reads `result.wire_error_envelope` (REST's ≥400 body, MCP's `ToolError` JSON, A2A's failed-Task artifact) and forwards to `assert_envelope_shape`. When nothing crossed a wire the envelope is `None`, and the assertion raises with a diagnosis instead of accepting a rebuilt exception. `result.error` is a `WireError` carrier, deliberately **not** an `AdCPSalesAgentError` subclass, so an `isinstance` assertion against a production class fails loudly instead of passing quietly. On success, `require_wire()` raises if the harness captured no wire body.

### Add a harness environment

1. Create `tests/harness/your_domain.py` (or `_unit.py`) as a subclass of `IntegrationEnv` or `BaseTestEnv`.
2. Define `EXTERNAL_PATCHES` — the dependencies to mock — and `ASYNC_PATCHES` for the ones that need `AsyncMock`.
3. Implement `_configure_mocks()` to wire the happy-path defaults.
4. Implement `call_impl(**kwargs)` to construct the request and call production code.
5. Add fluent helpers (`add_buy()`, `set_adapter_response()`, and so on). If a helper must mean the same thing against the live stack, decorate it with `@realize_e2e` and give it an e2e realization; declare an intent the live stack cannot express with `e2e_unsupported(reason)` instead of an entry in a nodeid ledger.
6. Export the environment from `tests/harness/__init__.py`.

**Do not override `call_mcp` or `call_a2a`.** `BaseTestEnv` defines them once as `deliver_*(**kwargs).payload`. An env that needs custom routing overrides `deliver_mcp` or `deliver_a2a`, and a shrink-only allowlist counts every such override. One delivery returns one `DeliverResult(payload, wire_response)`, so no per-env wire stash exists for a second writer to write, and `test_architecture_harness_single_dispatch.py` pins that absence.

### Anti-pattern: mock scaffolding rebuilt in every test

```python
# WRONG: 15 lines of MagicMock setup duplicated in every test function
def test_something():
    mock_uow = MagicMock()
    mock_uow.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow.__exit__ = MagicMock(return_value=False)
    mock_uow.media_buys = MagicMock()
    mock_uow.session = MagicMock()
    # ... same 15 lines in the next test
```

Some test files carry this shape as tracked debt. In a test you write, use the harness or add an environment for your domain.

### Anti-pattern: a patch on a name nothing calls

```python
# WRONG: TOOLS holds the function OBJECT, so this renames something nothing consults —
# the test goes green having exercised the real implementation.
with patch("src.core.tools.products._get_products_impl", stub):
    ...

# CORRECT: patch the registry ROW
from tests.helpers.capture_wrapper_req import registry_impl, stub_impl
```

### Anti-pattern: a patch on the outbound transport

A webhook or egress test composes `LocalOriginMixin` (`tests/harness/_mixins.py`) instead of patching `httpx` or `requests`. The mixin stands up a real programmable HTTP origin over real TLS on an ephemeral loopback port (`tests/helpers/local_http_origin.py`), and the test asserts on what arrived: how many requests, with which headers, carrying which bytes. Those assertions mean the same thing whichever client library the egress seam uses. For the full guide, read [Webhook testing architecture](../design/webhook-testing-architecture.md).

### Test a Flask endpoint

For a Flask route, use Flask's test client rather than reconstructing the route's boolean logic.

**Canonical file:** [`tests/unit/test_signup_flow_session.py`](../../tests/unit/test_signup_flow_session.py)

```python
from src.admin.app import create_app

app = create_app()
app.config["TESTING"] = True
app.config["SECRET_KEY"] = "test-secret"

with app.test_client() as client:
    response = client.post("/test/auth", data={...})
    assert response.status_code == 302
```

**Anti-pattern:**
```python
# WRONG: reimplementing the gate logic and asserting the boolean
should_abort = not env_test_mode or not tenant_setup_mode
assert should_abort is True  # Tests Python arithmetic, not your endpoint
```

**Enforced by:** `test_architecture_harness_single_dispatch.py`, `test_architecture_bdd_wire_discipline.py`, `test_architecture_weak_mock_assertions.py`, `test_architecture_bdd_dispatch_entries.py`

## 5. Factory fixtures for integration tests

Integration tests use `factory-boy` factories, not inline `session.add()`.

**Canonical directory:** [`tests/factories/`](../../tests/factories/)

```python
from tests.factories import TenantFactory, MediaBuyFactory

tenant = TenantFactory(tenant_id="t1")
buy = MediaBuyFactory(tenant=tenant)
```

The API is standard factory-boy: `Factory(...)` or `Factory.create(...)`. Factories auto-commit via `sqlalchemy_session_persistence = "commit"`, and the harness binds the session for them, so a factory call inside an env lands in the env's database.

**Anti-pattern:**
```python
# WRONG: manual model construction in tests
with get_db_session() as session:
    tenant = Tenant(tenant_id="test", name="Test", ...)
    session.add(tenant)
    session.commit()
```

**Anti-pattern: a fixture derived from what the request selects.** If the test computes the expected set from the same seeded data the filter reads, deleting the filter changes nothing and the test cannot fail. Seed something the request should *not* select, and assert that it is absent.

**Enforced by:** `test_architecture_repository_pattern.py` (catches `session.add()` and `get_db_session()` in test bodies), `test_architecture_bdd_no_dict_registry.py`

## 6. Transport boundary (CP-5)

All tools have two layers: a **transport** (MCP, A2A, REST) that parses a request and writes a response, and **business logic** (`_impl` and the services it calls). Between them sits ONE seam, [`src/core/tools/_boundary.py`](../../src/core/tools/_boundary.py), and there are no per-tool wrappers at all.

**Transport rules:** Hand the raw payload and the request headers to `serve(tool_name, payload, headers, protocol)`; never resolve an identity and never call an implementation directly. Catch `AdcpFailure`, serialize its response with `to_wire`, and add only the transport's own failure marker.

```python
# Every transport, one call. The registry says which function runs and whether
# the credential must verify; the boundary validates and resolves the caller.
response = await serve("create_media_buy", payload, request.headers, TransportProtocol.REST)
```

**`_impl` rules:** Declare exactly `(req: <DTO>, identity: <one of the three identity types>)` — the boundary supplies nothing else, and the annotation IS the credential policy. Raise `AdCPSalesAgentError` subclasses, never `ToolError`. Import nothing from `fastmcp`, `a2a`, `starlette`, or `fastapi`. Do no auth extraction, tenant or account resolution, idempotency, version negotiation, or context echo — the boundary owns all of those.

```python
# Protected: the type carries the boundary's decision, so there is no None to check.
async def _create_media_buy_impl(req: CreateMediaBuyRequest, identity: AccountIdentity) -> CreateMediaBuyResult: ...

# Public: branch on identity.principal is None yourself.
async def _get_products_impl(req: GetProductsRequest, identity: PublicIdentity) -> GetProductsResponse: ...
```

`AccountIdentity` is required exactly when the DTO requires `account`, and the registry refuses a row at load whose annotation disagrees (`ToolSpec.__post_init__`).

### Controller and service

An `_impl` is a **controller**: it establishes who is calling and then delegates. The work belongs in a **service** that takes an already-resolved caller and asks nothing about transports, auth, or idempotency.

**Canonical file:** [`src/core/tools/creatives/_sync.py`](../../src/core/tools/creatives/_sync.py)

```python
def _sync_creatives_impl(req: SyncCreativesRequest, identity: AccountIdentity) -> SyncCreativesResponse:
    """The CONTROLLER: thin by construction."""
    return sync_creatives(req, identity=identity, principal_id=identity.principal.principal_id, tenant=identity.tenant)


def sync_creatives(req, *, identity: ResolvedIdentity, principal_id: str, tenant: TenantContext) -> SyncCreativesResponse:
    """THE SERVICE. Takes a resolved caller and does the work."""
```

**Anti-pattern: a controller that calls another controller.**
```python
# WRONG: re-runs an auth check that already passed, and drags the outer request's
# idempotency_key into a function with no business seeing it.
_sync_creatives_impl(req=sync_req, identity=identity)

# CORRECT: call the service
sync_creatives(sync_req, identity=identity, principal_id=..., tenant=...)
```
`sync_creatives` is the one extracted service; extract the next one when a second tool needs it, not before.

**Anti-pattern: business logic that reads the caller off a transport object.**
```python
# WRONG
async def list_tasks(context: Context) -> dict:
    identity = await context.get_state("identity")  # identity resolution in _impl
```
The resolver is private to the boundary and `ruff-boundary.toml` bans importing it — and bans importing each `_impl` name outside `registry.py` — so this shape fails the build rather than review.

**Enforced by:** `test_transport_agnostic_impl.py`; the `ToolImpl` protocol typing `ToolSpec.impl` (mypy); `.ast-grep/rules/impl-signature-is-request-and-identity.yml`; `.ast-grep/rules/resolved-identity-constructed-only-by-its-owners.yml`; `.ast-grep/rules/context-is-written-by-the-boundary-alone.yml`; `ruff-boundary.toml`'s TID251 bans (the resolver, the `_impl` names, `ToolError`, the two auth errors, `ContextObject`), proven live by `tests/unit/test_ruff_boundary_bans.py` and `tests/unit/test_ast_grep_identity_rules.py`

[Building a tool](building-tools.md) covers adding a tool to the registry, end to end.

## 7. Error hierarchy

`_impl` functions raise `AdCPSalesAgentError` subclasses. A raise site names a **code** and supplies **structured facts**; it never authors buyer-facing text.

**Canonical files:** [`src/core/exceptions.py`](../../src/core/exceptions.py) (the hierarchy), [`src/core/errors/codes.py`](../../src/core/errors/codes.py) (`CODE_TABLE`), [`src/core/errors/details.py`](../../src/core/errors/details.py) (declared detail classes), [`src/core/errors/issues.py`](../../src/core/errors/issues.py) (`issues[]`)

This section is the raise-site pattern: what to write, and what the WRONG half costs. [Error architecture](../design/error-architecture.md) describes the mechanism behind it — how `CODE_TABLE` is composed, why `recovery` is the one closed vocabulary, the two lanes an error can travel in, and how to choose between them.

```
AdCPSalesAgentError[DetailsT]              # generic in its details class
├── AdCPValidationError                    VALIDATION_ERROR
│   ├── AdCPInvalidRequestError            INVALID_REQUEST
│   └── AdCPUrlNotAllowedError             VALIDATION_ERROR
├── AdCPAuthenticationError                AUTH_INVALID      ← minted only by the resolver
│   └── AdCPAuthRequiredError              AUTH_MISSING      ← minted only by the resolver
├── AdCPAuthorizationError                 PERMISSION_DENIED
│   └── AdCPPolicyViolationError           POLICY_VIOLATION
├── AdCPNotFoundError[D]                   REFERENCE_NOT_FOUND
│   ├── AdCPMediaBuyNotFoundError          MEDIA_BUY_NOT_FOUND
│   ├── AdCPCreativeNotFoundError          CREATIVE_NOT_FOUND
│   └── ...                                (one per pinned not-found code)
├── AdCPConflictError                      CONFLICT
│   └── AdCPIdempotencyConflictError       IDEMPOTENCY_CONFLICT
├── AdCPRateLimitError                     RATE_LIMITED
├── AdCPAdapterError                       SERVICE_UNAVAILABLE
└── AdCPInternalError                      INTERNAL_ERROR (a platform code)
```

Four rules follow from that shape:

**1. There is no `message` parameter.** The constructor is keyword-only:
`error_code`, `details`, `issues`, `field`, `retry_after`, `internal_detail`.
`message`, `recovery`, `suggestion`, and `status_code` are read-only properties
that resolve from `CODE_TABLE` at every read, so no instance carries a value
that disagrees with the pin.

```python
# WRONG: there is no message parameter, and no per-class status
raise AdCPNotFoundError(f"Media buy '{media_buy_id}' not found.")

# CORRECT: name the code by its class, supply the FACTS
# (src/core/database/repositories/media_buy.py:89)
raise AdCPMediaBuyNotFoundError(details=EntityRefDetails(media_buy_id=media_buy_id))
```

**2. `details` is a declared class, never a dict.** The base is generic in its
detail type, so each subclass names its exact details class once and mypy
rejects a dict or a foreign class at every raise site. A list of problems is
`ErrorProblem` entries carrying `code`, `subject_type`, `subject_id`, `field`,
`rejected_value`, and `accepted_values` — the class has no free-text slot for a
sentence to move into.

```python
# WRONG: a free-form dict, with structured facts interpolated into strings
raise AdCPValidationError(details={"creative_errors": [f"{r.creative_id}: {err.message}"]})

# CORRECT: the code IS the fact; the sentence is derivable from it
# (src/core/tools/media_buy_update.py:958)
raise AdCPAdapterError(
    details=AdapterFailureDetails(
        problems=[
            ErrorProblem(code=err.code, subject_type="creative", subject_id=r.creative_id)
            for r in failed_creatives
            for err in r.errors or []
        ]
    )
)
```

**3. `internal_detail` is a caught exception, never a sentence.** It is typed
`BaseException | None`, never serializes, and `record_boundary_error` puts it in
the server-side record with the traceback attached.
`.ast-grep/rules/internal-detail-is-an-exception.yml` refuses a string there in
every spelling — literal, f-string, `+`, `%`, `.format`, `str()`, and a
conditional with a string branch — across `src/`, `scripts/`, and `tests/`.

```python
# WRONG
except GoogleAdsError as e:
    raise AdCPAdapterError(internal_detail=f"GAM rejected order {order_id}: {e}")

# CORRECT: the exception itself, with the facts on the details class
# (src/adapters/gam_reporting_service.py:356 is the bare form of this)
except GoogleAdsError as e:
    raise AdCPAdapterError(
        details=AdapterFailureDetails(line_item_id=order_id),
        internal_detail=e,
    ) from e
```

**4. Field-level rejections go to `issues[]`, not into a sentence.** `ErrorIssue`
takes an RFC 6901 pointer and a JSON Schema keyword and DERIVES its message
from the keyword; it has no `message` parameter. The error derives `field` from
`issues[0].pointer` when the caller passes none, so every reader of the error
sees one value.

**Anti-pattern: an error response object returned instead of raised.**
```python
# WRONG: a returned error is a SUCCESS as far as the boundary is concerned —
# it is cached as an idempotent replay and never reaches record_boundary_error.
if total_budget <= 0:
    return UpdateMediaBuyError(errors=[...])

# CORRECT
if total_budget <= 0:
    raise AdCPBudgetTooLowError(field=PACKAGES_FIELD)
```
Every implementation raises on failure, and a raise never reaches the idempotency save — that is how "errors are never cached" is a property of control flow rather than a check.

**Anti-pattern: `ValueError` / `RuntimeError` from business logic.** The boundary normalizes an untyped exception (`adcp_error_for`), but the mapping is coarse: a `ValueError` becomes `VALIDATION_ERROR` and anything else `INTERNAL_ERROR`, with no details and no `issues[]`. Raise the typed error instead.

**Advisory errors inside a success response** use `Error.of(code, ...)` or `Error.from_exception(exc)` (`src/core/schemas/_base.py`) — never a literal `Error(code=...)`, which the guard with the empty cap dict refuses. [Error architecture § Which lane a failure takes](../design/error-architecture.md#which-lane-a-failure-takes) says which failures belong in that lane rather than in a raise.

**Enforced by:** `__init_subclass__` and `__new__` on `AdCPSalesAgentError` (load-time refusals); mypy on the generic detail type; `.ast-grep/rules/internal-detail-is-an-exception.yml`; `tests/unit/test_architecture_no_error_construction_in_impl.py` (empty cap dict); `tests/unit/test_tool_error_logging.py`; `ruff-boundary.toml`'s TID251 bans on `fastmcp.exceptions.ToolError` and on the two auth errors outside the resolver, proven live by `tests/unit/test_ruff_boundary_bans.py`

## 8. DRY — shared validation

When the same validation logic applies to multiple code paths (create and update), extract a shared validator. Both paths call the same function.

**Anti-pattern: the same rule expressed twice.**
```python
# media_buy_create.py — inline arithmetic
if total_budget <= 0:
    raise AdCPBudgetTooLowError(field=PACKAGES_FIELD)

# media_buy_update.py — same rule, second expression
if budget_amount <= 0:
    raise AdCPBudgetTooLowError(field=package_field_path("budget", pkg_index))
```

Same check, two implementations. When the rule changes, someone updates one and misses the other — and that has happened here. The update path's positivity check lived on a campaign-level branch that AdCP 3.1.1 does not define. Deleting that branch took the only positivity guard with it, and the surrounding `if pkg_update.budget:` skipped `0.0` as falsy, so a zero budget surfaced later as `INVALID_STATE`. The comment at `media_buy_update.py:760` records it.

**Canonical file:** [`src/core/tools/financial_validation.py`](../../src/core/tools/financial_validation.py) holds the financial rules both paths apply, and both paths call it:

```python
# src/core/tools/media_buy_create.py:2123 and media_buy_update.py:765 — one rule, one expression
budget_err = validate_budget_positive(total_budget, field=PACKAGES_FIELD)
if budget_err:
    raise AdCPBudgetTooLowError(field=PACKAGES_FIELD)
```

The validator returns the reason rather than raising, so each caller keeps its own pointer into its own request shape while the rule itself exists once.

**How to apply DRY correctly** — extract the shared pattern instead of copy-pasting with variable substitution. The live example is the format-id filters in `src/core/tools/creative_formats.py`:

```python
# WRONG: copy-paste with variable substitution
if req.output_format_ids:
    requested = {format_id_identity(fid) for fid in req.output_format_ids}
    formats = [f for f in formats if f.output_format_ids and {format_id_identity(fid) for fid in f.output_format_ids} & requested]
if req.input_format_ids:
    requested = {format_id_identity(fid) for fid in req.input_format_ids}
    formats = [f for f in formats if f.input_format_ids and {format_id_identity(fid) for fid in f.input_format_ids} & requested]

# CORRECT: one expression of the rule, parameterized by the attribute it reads
for req_ids, attr in (
    (req.output_format_ids, "output_format_ids"),
    (req.input_format_ids, "input_format_ids"),
):
    if req_ids:
        requested = {format_id_identity(fid) for fid in req_ids}
        formats = [
            f
            for f in formats
            if getattr(f, attr) and {format_id_identity(fid) for fid in getattr(f, attr)} & requested
        ]
```

The same reasoning applies one level up. When a fact otherwise appears in two places, derive the second statement from the first, or refuse the disagreement at load. `ToolSpec.requires_credential()` derives the credential policy from the implementation's identity annotation instead of repeating it as an `auth=` literal, and `ToolSpec.__post_init__` refuses a row whose annotation and DTO disagree about the account.

**Enforced by:** `check_code_duplication.py` in `make quality` (pylint R0801 against the shrink-only `.duplication-baseline`)

## Quick reference: where to look

| When you need to... | Read this file |
|---------------------|---------------|
| Add a repository | `src/core/database/repositories/media_buy.py` |
| Wire a repo into a UoW | `src/core/database/repositories/uow.py` |
| Defer or suppress an effect | `src/core/database/repositories/effects.py` |
| Add an AdCP tool | `src/core/tools/registry.py` + [Building a tool](building-tools.md) |
| Write an `_impl` controller and its service | `src/core/tools/creatives/_sync.py` |
| Write a public tool's `_impl` | `src/core/tools/products.py` |
| See what a transport is allowed to do | `src/routes/api_v1.py` (REST), `src/core/main.py` (MCP) |
| Raise the right error | `src/core/exceptions.py`, `src/core/errors/codes.py`, `src/core/errors/details.py` |
| Take or return an adapter carrier | `src/adapters/base.py` |
| Make an outbound call | `src/core/security/outbound_http.py` |
| Write a test for an `_impl` | `tests/harness/_base.py` → a domain env like `tests/harness/delivery_poll_unit.py` |
| See tests that use the harness | `tests/unit/test_delivery_poll_behavioral.py` |
| Substitute an implementation in a test | `tests/helpers/capture_wrapper_req.py` |
| Assert on a wire error | `tests/harness/transport.py`, `tests/helpers/envelope_assertions.py` |
| Write a Flask endpoint test | `tests/unit/test_signup_flow_session.py` |
| Create a test factory | `tests/factories/media_buy.py` |
| Add a structural guard | [Structural guards](structural-guards.md) |
| Know the standards a change is held to | [Engineering standards](engineering-standards.md) |

## Legacy code awareness

These files carry the most remaining debt. **Do not copy their patterns into code you write:**

| File | Legacy patterns | Tracked by |
|------|----------------|------------|
| `src/core/tools/media_buy_create.py` | 4079 lines; raw `select()` inside `_create_media_buy_impl` and `execute_approved_media_buy`; the deprecated `uow.session` reached through `assert uow.session is not None` at eight sites; scattered `"USD"` defaults | `FIXME(#1119)`, `test_architecture_no_raw_select.py`'s allowlist (two entries for this file) |
| `src/core/tools/media_buy_update.py` | Two remaining `uow.session` reads | `FIXME(#1119)` |
| `src/core/tools/media_buy_delivery.py` | Repositories constructed from `uow.session` rather than reached off the UoW; one `session.add()` for an audit row | `FIXME(#1119)` |
| `src/admin/blueprints/*` | 189 `get_db_session()` and 43 `session.add()` sites | `.admin-raw-session-baseline` (shrink-only) |

When you work in these files, follow the patterns in this document, not the surrounding code. The fix for one of these reads is a repository method you add, never a second copy of the read.

Two items have **left** this list, in case you are reading an earlier note: `media_buy_update.py` raises instead of returning error response objects (zero sites remain), and `task_management.py` takes `ResolvedIdentity` like every other protected tool.
