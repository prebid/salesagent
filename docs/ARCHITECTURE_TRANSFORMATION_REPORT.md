# Architecture Transformation Report

**Period:** February 16 -- March 9, 2026 (3 weeks)
**Repository:** [prebid/salesagent](https://github.com/prebid/salesagent)
**Author:** Konstantin Mirin
**Methodology source:** [adcp-req](../../../adcp-req/) (requirements derivation repository)

---

## Executive Summary

Over three weeks, the Prebid Sales Agent codebase underwent a fundamental architectural transformation -- from an entangled monolith with no clear layer separation, untraceable tests, and protocol-dependent behavior to a layered, guard-enforced architecture with requirement-grounded test coverage.

**Key metrics:**

| Metric | Before (Feb 16) | After (Mar 9, main) | In-flight branches | Change |
|--------|-----------------|---------------------|-------------------|--------|
| Test functions | 2,846 | 4,157 | 4,759 (product) | +46% / +67% |
| Test files | 303 | 400 | -- | +32% |
| Lines of test code | 93,070 | 132,972 | -- | +43% |
| Unit test functions | 1,895 | 3,088 | -- | +63% |
| Integration test functions | 617 | 721 | -- | +17% |
| Passing tests (runtime) | ~2,500 | 4,719 | -- | +89% |
| Structural guard tests | 0 | 11 | 11 | new |
| Obligation docs (BDD scenarios) | 0 | 16 files, 1,048 obligations | 16 files | new |
| Covered obligations (`Covers:` tags) | 0 | -- | 278 (product), 300 (delivery), 206 (creative) | new |
| Test files with `Covers:` traceability | 0 | -- | 30 files, 675 tags | new |
| Obligation coverage allowlist | N/A | N/A | 451 (product), 466 (delivery), 566 (creative) | shrinking |
| PRs merged | -- | 14 | 4 open | -- |
| Lines changed | -- | +87,000 / -20,000 | net +67,000 | -- |
| Files changed | -- | 856 (across 14 PRs) | -- | -- |

**Three architectural problems were identified and systematically addressed:**

1. **Serialization leaking through layers** -- `model_dump()` called everywhere, models deconstructed and reconstructed ad hoc, making it impossible to add new fields reliably
2. **Transport-dependent behavior** -- same business logic implemented differently for MCP vs A2A, with divergent error handling
3. **Untraceable tests** -- impossible to determine what is tested, what the test verifies, and whether the test expectation is correct

---

## Table of Contents

1. [Assessment: Requirements Derivation Methodology](#1-assessment-requirements-derivation-methodology)
2. [Problem 1: Serialization Everywhere](#2-problem-1-serialization-everywhere)
3. [Problem 2: Transport Entanglement](#3-problem-2-transport-entanglement)
4. [Problem 3: Untraceable Tests](#4-problem-3-untraceable-tests)
5. [Solution: Structural Guards](#5-solution-structural-guards)
6. [Solution: Multi-Transport Test Harness](#6-solution-multi-transport-test-harness)
7. [Solution: Scoped Coverage & Factory Fixtures](#7-solution-scoped-coverage--factory-fixtures)
8. [Protocol Conformance: AdCP v3.6](#8-protocol-conformance-adcp-v36)
9. [Closed Issues & Bugs Fixed](#9-closed-issues--bugs-fixed)
10. [Progression Over Time](#10-progression-over-time)
11. [Before / After: Coverage Baseline](#11-before--after-coverage-baseline)
12. [Remaining Technical Debt](#12-remaining-technical-debt)
13. [Appendix: Complete Evidence Index](#appendix-complete-evidence-index)

---

## 1. Assessment: Requirements Derivation Methodology

Before any code changes, the first step was understanding *what the system should do*. The AdCP protocol specification defines the contract, but there was no formalized mapping from specification to implementation to tests.

### The adcp-req Repository

A dedicated requirements repository ([adcp-req](../../../adcp-req/)) was created to derive, formalize, and track requirements from the AdCP protocol specification (v3.0.0-beta.3 through v3.6.0). The methodology follows a five-phase process documented in `REQUIREMENTS_METHODOLOGY.md` (v4.5):

**Derivation chain:**

```
AdCP JSON Schemas (v3.6.0)
  + salesagent tool implementations (brownfield source)
  + adcp-client interaction patterns
        |
        | Phase 0: Protocol Study (brownfield-specific)
        v
Field Constraint YAMLs (95+ files)
  + Domain Glossary (4 entries)
  + Actor Definitions (3 actors: Buyer, Seller, System)
        |
        | Phase 1: Requirements Capture
        v
Use Case Overviews (13 UCs)
  + Atomic Flow Files (149 files: main, alt, ext)
  + Business Rules (71 rules with invariants)
  + Non-Functional Requirements (4)
        |
        | Phase 2: BDD Derivation (3-pass)
        v
BDD Feature Files (13 Gherkin .feature files)
  with contextgit traceability per scenario
        |
        | Validated by deterministic scripts
        v
Coverage Reports
  postcondition: 100% required
  extension: 100% required
  error_code: 100% required
```

### Why This Matters

Every test obligation in the salesagent codebase traces back through this chain:

1. **Schema says:** `delivery_measurement` is a required field on `Product` (JSON Schema `required` array)
2. **Therefore, use case UC-001 has a postcondition:** POST-S2 -- pricing, formats, and delivery measurement must be present in every returned product
3. **Therefore, business rule BR-RULE-042 states:** Products lacking delivery measurement must not be returned to buyers
4. **Therefore, BDD scenario says:** `Given a product without delivery_measurement, When buyer calls get_products, Then that product is excluded`
5. **Therefore, obligation UC-001-EXT-F-01 exists** in `docs/test-obligations/UC-001-discover-available-inventory.md`
6. **Therefore, test `test_product_without_delivery_measurement_excluded` has `Covers: UC-001-EXT-F-01`**
7. **Therefore, the structural guard confirms** this obligation is covered and will fail the build if coverage is removed

This chain is machine-navigable via `contextgit` frontmatter in every artifact and is validated by 8 deterministic scripts (`req_coverage.py`, `req_lint_error_codes.py`, `req_lint_extensions.py`, etc.).

### Artifact Counts

| Artifact Type | Count | Location |
|---------------|-------|----------|
| Use cases | 13 | `adcp-req/docs/requirements/use-cases/` |
| Atomic flow files | 149 | One per main/alt/ext flow |
| Business rules | 71 | `adcp-req/docs/requirements/business-rules/` |
| Field constraint YAMLs | 95+ | `adcp-req/docs/requirements/constraints/` |
| BDD feature files | 13 | `adcp-req/tests/features/` |
| Validation scripts | 8 | `adcp-req/scripts/` |

---

## 2. Problem 1: Serialization Everywhere

### What Was Wrong

The codebase used Pydantic models, but treated them as glorified dicts. `model_dump()` was called at every layer boundary -- business logic, data access, transport -- and the resulting dicts were modified, merged, and reconstructed throughout the call chain.

**Consequence:** When a new field was added to a schema (e.g., `delivery_measurement` on `Product`), there was no guarantee it would survive from the database to the buyer's response. Any of dozens of `model_dump()` → dict manipulation → reconstruction sites could silently drop it.

### What Was Done

**PR [#1044](https://github.com/prebid/salesagent/pull/1044)** (merged 2026-02-18, commit `c412ce9e`):
- Established the principle: **Pydantic models stay intact across internal layers; `model_dump()` only at system boundaries**
- `SalesAgentBaseModel` now extends `adcp.types.base.AdCPBaseModel` (the protocol library's base)
- All ~90 internal models migrated from `BaseModel`/`AdCPBaseModel` to `SalesAgentBaseModel`
- `_create_media_buy_impl`: replaced 14 untyped parameters with single `CreateMediaBuyRequest` model
- `_sync_creatives_impl`: extracted from a 2,354-line monolith into `creatives/` package with 7 submodules
- Registered `pydantic_core.to_json` as `json_serializer` on the SQLAlchemy engine
- **+5,919 / -4,865 lines across 146 files**

**PR [#1051](https://github.com/prebid/salesagent/pull/1051)** (merged 2026-02-20, commit `5e6815f5`):
- Migrated ~20 schema types to adcp library base classes using `Library*` alias convention
- Removed remaining `model_dump()` calls from internal code paths
- Migrated all DateTime columns to timezone-aware `TIMESTAMPTZ`
- A2A serialization centralized via `_serialize_for_a2a()`
- **+3,236 / -1,526 lines across 101 files**

### How It Is Enforced

**Guard: `test_architecture_no_model_dump_in_impl.py`** -- AST-scans every `_impl` function for `.model_dump()` calls. New violations fail the build immediately. Current allowlist: 25 pre-existing violations (23 in `media_buy_update.py` workflow step serialization, 1 logging, 1 filter conversion), tracked by beads task `salesagent-hr8n`.

**Guard: `test_architecture_schema_inheritance.py`** -- Verifies every schema with a `Library*` counterpart inherits from it (not duplicated). Detects field redeclaration (drift from library). Current allowlist: 30 known overrides (primarily for nested serialization Pattern #4), tracked by beads task `salesagent-v0kb`.

---

## 3. Problem 2: Transport Entanglement

### What Was Wrong

The system supports three protocols (MCP, A2A, REST), but business logic was implemented differently for each. MCP tools had one error handling path, A2A handlers had another, and the REST admin API had yet another. Adding a feature meant implementing it in 2-3 places, and there was no enforcement that behavior would be consistent.

Before the transformation:
- `_impl` functions accepted transport-specific `Context` objects
- `_impl` functions raised `ToolError` (a FastMCP-specific exception)
- A2A handlers had their own auth extraction logic
- There was no REST API (admin UI only)

### What Was Done

**PR [#1066](https://github.com/prebid/salesagent/pull/1066)** (merged 2026-03-02, commit `7d2b1d9e`) -- **the largest single PR**:

**Process unification:**
- Three separate processes (Flask MCP server, Flask Admin, A2A server) → single FastAPI process
- FastMCP v2 upgraded to v3.0.2, mounted at `/mcp` via `http_app()`
- Single pure ASGI `UnifiedAuthMiddleware` replaces 3 separate auth middlewares
- Nginx config: 957 → 472 lines (50% reduction)
- **+15,442 / -8,218 lines across 243 files**

**Identity abstraction -- `ResolvedIdentity`** (`src/core/resolved_identity.py`):
```python
class ResolvedIdentity(BaseModel, frozen=True):
    principal_id: str | None = None
    tenant_id: str | None = None
    tenant: Any = None
    auth_token: str | None = None
    protocol: Literal["mcp", "a2a", "rest"] = "mcp"
```
- Frozen (immutable after creation)
- Created once per request by each transport boundary via `resolve_identity()`
- `_impl` functions accept this instead of `Context`/`ToolContext`

**Error hierarchy -- `AdCPError`** (`src/core/exceptions.py`):
```
AdCPError (500, INTERNAL_ERROR)
  +-- AdCPValidationError (400, VALIDATION_ERROR)
  +-- AdCPAuthenticationError (401, AUTHENTICATION_ERROR)
  +-- AdCPAuthorizationError (403, AUTHORIZATION_ERROR)
  +-- AdCPNotFoundError (404, NOT_FOUND)
  +-- AdCPRateLimitError (429, RATE_LIMIT_EXCEEDED)
  +-- AdCPAdapterError (502, ADAPTER_ERROR)
```
- `_impl` functions raise these exclusively
- Each transport boundary translates to its own format:
  - MCP: `AdCPError` → `ToolError` via `_translate_to_tool_error()`
  - A2A: `AdCPError` → JSON-RPC error via `_adcp_to_a2a_error()`
  - REST: `AdCPError` → HTTP status code + JSON body

**REST API added** (`src/routes/api_v1.py`, 183 lines):
- Full feature parity with MCP and A2A in a single file
- Every endpoint calls the same `_impl` function

**Architecture of a single tool after transformation:**
```
MCP:  Client → FastMCP → MCPAuthMiddleware(resolve_identity) → MCP wrapper → _impl(req, identity)
A2A:  Client → A2A server → on_message_send(resolve_identity)  → *_raw()    → _impl(req, identity)
REST: Client → FastAPI → Depends(_resolve_auth_dep)             → endpoint   → _impl(req, identity)
```

All three paths converge on the same `_impl` function with the same `ResolvedIdentity` type.

### How It Is Enforced

Four structural guards enforce this architecture:

| Guard | What It Scans | Violations |
|-------|--------------|------------|
| `test_transport_agnostic_impl.py` | Zero transport imports in `_impl` (fastmcp, a2a, starlette, fastapi) | 0 |
| `test_impl_resolved_identity.py` | Every `_impl` accepts `identity: ResolvedIdentity`, not `Context` | 0 (all 13 migrated) |
| `test_no_toolerror_in_impl.py` | No `raise ToolError(...)` in any `_impl` function | 0 |
| `test_architecture_boundary_completeness.py` | MCP and A2A wrappers forward every `_impl` parameter | 2 (A2A `context_id`) |

---

## 4. Problem 3: Untraceable Tests

### What Was Wrong

Tests existed, but they answered none of these questions:
- **What is being tested?** Test names were vague (`test_create_media_buy_success`)
- **Why is this the expected behavior?** No reference to specification or business rule
- **Is the test correct?** No way to verify the expectation against an authoritative source
- **What is NOT tested?** No inventory of missing coverage

### What Was Done

**Obligation-grounded testing** was introduced across three PRs:

**PR [#1071](https://github.com/prebid/salesagent/pull/1071)** (merged 2026-03-06, commit `3398aabf`):
- 16 obligation documents created in `docs/test-obligations/`, covering UC-001 through UC-013, business rules, and constraints
- Each obligation is a BDD scenario with a unique machine-parseable ID (e.g., `UC-001-MAIN-01`, `BR-RULE-041-01`, `CONSTR-BRIEF-POLICY-01`)
- Every obligation tagged with `**Layer** behavioral` or `**Layer** schema`
- 336 entity tests mapped to obligation IDs via `Covers:` docstring tags
- Obligation coverage guard added as structural test
- **+49,261 / -3,793 lines across 257 files**

**PR [#1062](https://github.com/prebid/salesagent/pull/1062)** (merged 2026-02-24, commit `52dc2310`):
- Fixed 3 silently skipping unit tests (dead imports, overly broad `except` clause)
- Removed dead signals code from test matrices
- Deleted 4 dead test files, removed stale `--ignore` flags from CI
- Banned `inspect.getsource()` in tests via ruff TID251 rule
- Added 225+ new tests: behavioral snapshot tests (157), response shape tests (37), error format consistency (18), auth consistency (13)
- **+10,218 / -807 lines across 51 files**

### Obligation Format (Example)

From `docs/test-obligations/UC-001-discover-available-inventory.md`:

```markdown
#### Scenario: Authenticated buyer discovers inventory with brief
**Obligation ID** UC-001-MAIN-01
**Layer** behavioral
**Given** an authenticated buyer with a valid principal_id
**When** the buyer calls get_products with a brief
**Then** the system returns products matching the brief
**Business Rule** BR-RULE-001, BR-RULE-041
**Priority** P0 -- core happy path
```

### Test Linking (Example)

From `tests/integration_v2/test_get_products_auth_obligations.py`:

```python
async def test_no_auth_token_returns_unrestricted_products(self, integration_db):
    """No auth token returns all unrestricted products.

    Spec: https://github.com/prebid/adcp/blob/8f26baf3/.../get-products-response.json
    CONFIRMED: products array always present in response
    Covers: BR-RULE-041-01
    """
```

Every test that covers an obligation includes:
1. The obligation ID in `Covers:` tag
2. A spec permalink to the authoritative source (schema commit hash)
3. A `CONFIRMED:` note explaining why this expectation is correct

### Obligation Coverage Guard

`test_architecture_obligation_coverage.py` runs 7 tests:

| Test | Purpose |
|------|---------|
| `test_no_new_uncovered_behavioral_obligations` | Every obligation has a test OR is in the allowlist |
| `test_known_uncovered_are_still_obligations` | Allowlist entries reference real obligation IDs |
| `test_known_uncovered_not_already_covered` | Stale detection: covered obligations must leave allowlist |
| `test_all_scenarios_have_obligation_ids` | Every `#### Scenario:` must have an ID tag |
| `test_no_duplicate_obligation_ids` | IDs are globally unique |
| `test_tests_reference_valid_obligations` | `Covers:` tags reference real IDs |
| `test_obligation_count_documented` | Allowlist size matches uncovered count exactly |

Current allowlist: **452 uncovered obligations** (can only shrink, never grow).

---

## 5. Solution: Structural Guards

Eleven AST-scanning tests enforce architecture invariants on every `make quality` run. They parse Python source with the `ast` module (no runtime execution of business logic), run in < 1 second, and fail the build immediately on new violations.

### Complete Guard Inventory

| # | Guard | Enforces | Violations | Tracked By |
|---|-------|----------|------------|------------|
| 1 | No ToolError in _impl | `_impl` raises AdCPError, never ToolError | 0 | -- |
| 2 | Transport-agnostic _impl | Zero transport imports in `_impl` | 0 | -- |
| 3 | ResolvedIdentity in _impl | `_impl` accepts ResolvedIdentity, not Context | 0 | -- |
| 4 | Schema inheritance | Schemas extend library base types | 30 known overrides | salesagent-v0kb |
| 5 | Boundary completeness | Wrappers forward all `_impl` params | 2 | salesagent-v0kb |
| 6 | Query type safety | DB queries use correct types for PK columns | 1 | salesagent-mq3n |
| 7 | No model_dump in _impl | `_impl` returns models, not dicts | 25 | salesagent-hr8n |
| 8 | Repository pattern | No `get_db_session()` in `_impl`; no `session.add()` in tests | 18 + ~155 | salesagent-qo8a |
| 9 | Migration completeness | Every migration has `upgrade()` and `downgrade()` | 5 legacy | salesagent-t735 |
| 10 | No raw MediaPackage select | MediaPackage access through repository only | 3 | salesagent-rva2 |
| 11 | Obligation coverage | Behavioral obligations have matching tests | 452 in allowlist | -- |

### Design Principles

1. **Allowlists can only shrink** -- new code introducing a violation fails CI immediately
2. **Every allowlisted violation has a `FIXME(salesagent-xxxx)` comment** at the source location linking to a beads tracking task
3. **Stale-entry detection** -- when a violation is fixed, the guard fails until the allowlist entry is removed
4. **AST scanning, not runtime** -- guards parse source with the `ast` module, run fast, unaffected by runtime state

### Evidence of Shrinking

Guards were introduced in PR [#1071](https://github.com/prebid/salesagent/pull/1071) (commit `3398aabf`, 2026-03-06). The three in-progress domain completion PRs each shrink allowlists:

- **PR [#1082](https://github.com/prebid/salesagent/pull/1082)** (Product): 155/155 UC-001 obligations covered, obligation allowlist reduced
- **PR [#1080](https://github.com/prebid/salesagent/pull/1080)** (Creative): Obligation allowlist reduced by 27 entries
- **PR [#1081](https://github.com/prebid/salesagent/pull/1081)** (Delivery): 127+ obligation test stubs for UC-002/003/004

---

## 6. Solution: Multi-Transport Test Harness

### The Problem It Solves

With three transport protocols (MCP, A2A, REST) all calling the same `_impl` function, we need to verify that behavior is identical regardless of transport. Given the amount of historical technical debt, transport-specific deviations are a real risk.

### Architecture (Implemented in Creative Worktree)

The multi-transport test harness lives in `tests/harness/` and consists of:

**`transport.py`** -- Defines `Transport` enum:
```python
class Transport(Enum):
    IMPL = "impl"    # Direct _impl call (no transport layer)
    A2A  = "a2a"     # Through A2A raw function
    REST = "rest"    # Through FastAPI REST endpoint
    MCP  = "mcp"     # Through MCP tool wrapper
```

**`dispatchers.py`** -- Four dispatcher classes (`ImplDispatcher`, `A2ADispatcher`, `RestDispatcher`, `McpDispatcher`), each with a `dispatch(env, **kwargs) -> TransportResult` method. Results separate transport-specific `envelope` (HTTP status, content-type) from shared `payload` (Pydantic response model).

**Environment classes** -- Per-domain harnesses that implement the dispatch hooks:
- `CreativeFormatsEnv` → routes to `_list_creative_formats_impl`
- `CreativeSyncEnv` → routes to `_sync_creatives_impl`
- `CreativeListEnv` → routes to `_list_creatives_impl`

### How Tests Use It

```python
ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.REST, Transport.MCP]

@pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
def test_new_creative_created(self, integration_db, transport):
    with CreativeSyncEnv() as env:
        env.setup_default_data()
        result = env.call_via(transport, creatives=[...])
    assert result.is_success
    assert_envelope(result, transport)
    assert result.payload.creatives[0].creative_id == "c_test"
```

Each test runs **4 times** -- once per transport. The `assert_envelope` helper validates transport-specific details (HTTP status, content-type) while the payload assertions verify business logic is identical.

**Evidence:** PR [#1080](https://github.com/prebid/salesagent/pull/1080) (Creative domain completion) includes 60+ multi-transport integration tests in `test_creative_sync_transport.py`, covering UC-006-MAIN-MCP-04 through -09, UC-006-MAIN-REST-01..03, generative build, format validation, assignments, approval, and async lifecycle.

### Current Status

| Domain | Multi-Transport Harness | Status |
|--------|------------------------|--------|
| Creative | Yes (`CreativeFormatsEnv`, `CreativeSyncEnv`, `CreativeListEnv`) | PR [#1080](https://github.com/prebid/salesagent/pull/1080) |
| Product | Not yet (single-transport `ProductEnv` only) | Planned |
| Delivery | Not yet (`DeliveryPollEnv`, `WebhookEnv` are IMPL-only) | Planned |

---

## 7. Solution: Scoped Coverage & Factory Fixtures

### Scoped Coverage

Per-domain coverage measurement was introduced in the creative worktree to answer: "What percentage of the creative module's source code is exercised by creative-specific tests?"

**Configuration** (`tests/coverage_scopes.yaml` in creative worktree):
```yaml
creative:
  target: 100%
  sources: [11 source files]
  tests: [22 test files]
product:
  target: 90%
  sources: [2 source files]
  tests: [7 test files]
```

**Reported results:**
- Creative domain: >95% scoped coverage
- Delivery domain: 97.9% scoped coverage (per PR [#1081](https://github.com/prebid/salesagent/pull/1081))
- Product domain: 155/155 obligation coverage (per PR [#1082](https://github.com/prebid/salesagent/pull/1082))

### Factory Fixtures

Test data creation migrated from manual `session.add()` boilerplate to factory-boy ORM factories in `tests/factories/`:

| Factory | Model | Notable Defaults |
|---------|-------|-----------------|
| `TenantFactory` | `Tenant` | Auto-creates USD `CurrencyLimit` via `RelatedFactory` |
| `PrincipalFactory` | `Principal` | Links to tenant |
| `ProductFactory` | `Product` | Includes `delivery_measurement` (required by v3.6) |
| `PricingOptionFactory` | `PricingOption` | Links to product |
| `MediaBuyFactory` | `MediaBuy` | Links to tenant and principal |
| `MediaPackageFactory` | `MediaPackage` | Links to media buy |

**Before (scattered manual setup):**
```python
with get_db_session() as session:
    tenant = Tenant(tenant_id="test", name="Test", subdomain="test", ...)
    session.add(tenant)
    currency = CurrencyLimit(tenant_id="test", ...)
    session.add(currency)
    product = Product(tenant_id="test", product_id="p1", ...)
    session.add(product)
    session.commit()
```

**After (factory-based):**
```python
with ProductEnv(tenant_id="test") as env:
    tenant = TenantFactory(tenant_id="test", subdomain="test")
    product = ProductFactory(tenant=tenant, product_id="p1")
    PricingOptionFactory(product=product)
    result = env.call_impl(brief="video")
```

### Test Harness Architecture

The `tests/harness/` module provides domain-specific environment classes:

- **`BaseTestEnv`** (`_base.py`): Context manager that binds factory-boy to DB session, patches external services, provides `call_impl()` API
- **`IntegrationEnv`** (subclass): Uses real PostgreSQL, creates unique DB per test
- **Mixins** (`_mixins.py`): `ProductMixin`, `DeliveryPollMixin`, `WebhookMixin`, `CircuitBreakerMixin` add domain-specific setup and assertion APIs
- **Domain envs**: `ProductEnv`, `DeliveryPollEnv`, `WebhookEnv`, `CircuitBreakerEnv` combine base + mixins

**Dual-mode design:** The same harness works for both integration tests (real DB) and unit tests (mocked DB), sharing mixin APIs and external-service patches.

---

## 8. Protocol Conformance: AdCP v3.6

### What Changed in v3.6

The AdCP protocol v3.6 introduced:
- `delivery_measurement` as a **required** field on `Product`
- `MediaChannel` enum for `channels` field
- `property_targeting_allowed` flag on `Product`
- `CreativeAsset` replacing legacy asset model
- `CreativeGroup` schema
- `property_list` filtering on `GetProductsRequest`
- `buying_mode` enum on `GetProductsRequest`
- Standardized error codes and recovery classification

### How It Was Implemented

**PR [#1071](https://github.com/prebid/salesagent/pull/1071)** (merged 2026-03-06, commit `3398aabf`):
- All Pydantic models extended from adcp library 3.6.0 base types
- Database migration: `delivery_measurement` backfilled with adapter-appropriate defaults, then made `NOT NULL` with server default
- Fixed 16 cross-tenant query leaks across 8 files
- 30 new v3.6 Product field contract tests
- **+49,261 / -3,793 lines across 257 files**

**In-progress domain completions:**

| PR | Domain | Key v3.6 Changes | Tests |
|----|--------|-------------------|-------|
| [#1082](https://github.com/prebid/salesagent/pull/1082) | Product | Schema extraction, `ProductRepository` + UoW, device_platform targeting | 155/155 UC-001 obligations |
| [#1080](https://github.com/prebid/salesagent/pull/1080) | Creative | 38 schemas extracted, `CreativeRepository`, AI provenance, `FormatFetchResult` | 3,300+ tests, 42 new test files |
| [#1081](https://github.com/prebid/salesagent/pull/1081) | Delivery | `DeliveryRepository`, notification_type/sequence_number, circuit breaker | 97.9% scoped coverage |

### Migration Evidence

Migrations on the current branch:
- `6aee724a2d1d` -- Backfill `delivery_measurement` NULL values with adapter-appropriate defaults
- `aa005b733aed` -- Add `NOT NULL` constraint + server default `'{"provider": "publisher"}'::jsonb`
- Commit `df680320` -- Migration tests (upgrade/downgrade/roundtrip) for `delivery_measurement`

---

## 9. Closed Issues & Bugs Fixed

### Architectural Bugs

These bugs were discovered and fixed as a direct consequence of the architecture transformation -- they were latent issues exposed by stricter typing, schema inheritance, and serialization boundary enforcement.

| Issue | Title | Root Cause | Fixed By | Date |
|-------|-------|------------|----------|------|
| [#1077](https://github.com/prebid/salesagent/issues/1077) | FormatId AttributeError crashing Add/Edit Product pages | PR #1051 removed `hasattr` safety checks; `FormatId.get_dimensions()` only existed on custom subclass, not adcp library's `FormatId` | [PR #1079](https://github.com/prebid/salesagent/pull/1079) (`0b22f1e4`) | Mar 4 |
| [#1019](https://github.com/prebid/salesagent/issues/1019) | 'FormatId' object is not subscriptable | Database JSONB deserialization could return raw dicts OR Pydantic `FormatId` objects; code assumed dict subscript access | [PR #1053](https://github.com/prebid/salesagent/pull/1053) (`3e9dcaf4`) | Feb 23 |
| [#1056](https://github.com/prebid/salesagent/issues/1056) | CPM fixed-rate products get PRICE_PRIORITY instead of STANDARD in GAM | `products_map` dict missing `delivery_type` field, so `is_guaranteed` always returned `False` | [PR #1058](https://github.com/prebid/salesagent/pull/1058) (`ff36add6`) | Feb 21 |
| [#1039](https://github.com/prebid/salesagent/issues/1039) | update_media_buy timezone mismatch error | Mixed tz-aware (request parsing) with tz-naive (database) datetimes | [PR #1051](https://github.com/prebid/salesagent/pull/1051) (`5e6815f5`) | Feb 20 |

### Functional Bugs

| Issue | Title | Root Cause | Fixed By | Date |
|-------|-------|------------|----------|------|
| [#1020](https://github.com/prebid/salesagent/issues/1020) | Creative upload uses clickthrough URL as image URL | `_extract_creative_url_and_dimensions()` didn't distinguish media vs clickthrough URLs | [PR #1035](https://github.com/prebid/salesagent/pull/1035) (`56cbc6a2`) | Feb 19 |
| [#1025](https://github.com/prebid/salesagent/issues/1025) | Property sync misses properties authorized via property_ids | Only read inline `agent.properties` arrays, missing `property_ids`/`property_tags` auth types | [PR #1054](https://github.com/prebid/salesagent/pull/1054) (`a188b6aa`) | Feb 20 |
| -- | GAM API v202411 deprecated, media buy approval fails | `googleads` 46.0.0 hardcoded deprecated API version | [PR #1070](https://github.com/prebid/salesagent/pull/1070) (`f6ce2a94`) | Feb 27 |

### Bugs Found in In-Flight Branches (Not Yet Merged)

| Branch | Bug | Commit |
|--------|-----|--------|
| `v3.6-product-completion` | `is_fixed_price` filter incorrectly unwraps PricingOption RootModel | `dbf8cd56` |
| `v3.6-product-completion` | Overly broad `except` clauses in `_get_products_impl` enrichment services hide real errors | `fa5dc7f8` |
| `v3.6-delivery-mb-cleanup` | CPC clicks derivation silently broken -- enum vs string comparison always returns 0 | `35166f48` |
| `v3.6-delivery-mb-cleanup` | `raise e` in delivery per-buy exception handler kills entire delivery loop on single error | `aa3b0bf4` |
| `v3.6-delivery-mb-cleanup` | Adapter validation runs AFTER `dry_run` early-return, skipping it for dry runs | `3b979987` |
| `v3.6-creative-completion` | Creative agent cache keys not canonicalized -- trailing slashes cause misses | `6a74f62e` |
| `v3.6-creative-completion` | Raw strings not coerced to enums in MCP wrapper, causing comparison failures | `1aa4ef3b` |

---

## 10. Progression Over Time

### 10.1 Obligation Coverage Timeline

The obligation system was introduced on Feb 28 and has been progressively addressed. The data below tracks the `obligation_coverage_allowlist.json` file size (number of uncovered behavioral obligations) commit-by-commit.

**Phase 1: Introduction and Classification (Feb 28)**

| Date | Commit | Allowlist Size | Event |
|------|--------|---------------|-------|
| Feb 28 00:39 | `8191c36d` | **1,012** | Guard created. All 1,044 obligations treated as behavioral. 32 had existing `Covers:` tags. |
| Feb 28 01:05 | `b50f951a` | **802** | Reclassification pass 1: 211 obligations moved to schema layer (-210) |
| Feb 28 01:15 | `eae35c02` | **733** | Reclassification pass 2: 69 more moved to schema layer (-69) |
| Mar 1 04:16 | `654d17f0` | **593** | Guard expanded to scan unit tests; found 282 existing `Covers:` tags (-140) |

**Phase 2: Branch-Specific Test Writing (Mar 1 onwards)**

After this point, the three domain branches diverge. Each independently reduces its own copy of the allowlist.

#### Product Branch (`v3.6-product-completion`)

```
Date         Allowlist   Delta   Event
─────────────────────────────────────────────────────────
Mar 1 23:28     554       -39   39 UC-001 obligations reclassified
Mar 2 01:30     481       -73   77 integration tests for UC-001
Mar 6 23:43     475        -6   Auth & identity obligation tests
Mar 6 23:45     465       -10   Policy enforcement obligation tests
Mar 6 23:52     463        -2   Response schema constraint tests
Mar 7 00:09     455        -8   19 unit tests for property list constraints
Mar 7 00:14     451        -4   40 integration tests for property list obligations
─────────────────────────────────────────────────────────
              CURRENT: 451   (278 covered / 729 behavioral = 38.1%)
```

#### Delivery Branch (`v3.6-delivery-mb-cleanup`)

```
Date         Allowlist   Delta   Event
─────────────────────────────────────────────────────────
Mar 2 01:09     576       -17   Fill UC-004 delivery obligation stubs
Mar 2 01:30     466      -110   Consolidate allowlist
Mar 2 20:47     523       +57   Remove batch-generated tests (quality issue)
Mar 2 20:57     516        -7   UC-004 delivery batch 1
Mar 2 21:16     506       -10   UC-004 webhook batch 2
Mar 2 23:04     501        -5   UC-004 obligation tests
Mar 3 00:32     496        -5   UC-004 batch 3
Mar 3 01:32     491        -5   UC-004 webhook batch 4
Mar 3 01:48     486        -5   UC-004 webhook batch 5
Mar 3 02:38     476       -10   UC-004 delivery batch 6
Mar 3 02:53     471        -5   UC-004 batch 7
Mar 3 11:35     466        -5   UC-004 batch 8
─────────────────────────────────────────────────────────
              CURRENT: 466   (300 covered / 766 behavioral = 39.2%)
```

#### Creative Branch (`v3.6-creative-completion`)

```
Date         Allowlist   Delta   Event
─────────────────────────────────────────────────────────
Mar 2 10:23     582       -11   Cover 89 UC-006 creative obligations
Mar 7 11:33     574        -8   Expand guard scanner to include integration_v2
Mar 7 11:48     567        -7   96 multi-transport integration tests for UC-005
Mar 7 11:54     566        -1   UC-005-EXT-A-01 tenant resolution tests
─────────────────────────────────────────────────────────
              CURRENT: 566   (206 covered / 772 behavioral = 26.7%)
```

**Visualization data (for chart):**

```
Date        Product  Delivery  Creative  Notes
────────────────────────────────────────────────
Feb 28      1012     1012      1012      All branches same (guard introduced)
Feb 28      802      802       802       Reclassification pass 1
Feb 28      733      733       733       Reclassification pass 2
Mar 1       593      593       593       Unit test scanning
Mar 2       481      466       582       Branches diverge, independent work
Mar 3       481      466       582       Delivery batches complete
Mar 7       451      466       566       Product + Creative polish
Mar 9       451      466       566       Current state
```

### 10.2 Structural Guard Introduction Timeline

All 11 guards were introduced within a 4-day window (Feb 24--28), establishing the enforcement framework before any domain-specific work began.

```
                              ┌─ Guard introduced
                              │     ┌─ Initial violations
                              │     │    ┌─ Current violations
                              ▼     ▼    ▼
Feb 24  ■ No ToolError in _impl        0 →  0
Feb 24  ■ Transport-agnostic _impl     0 →  0
Feb 24  ■ ResolvedIdentity in _impl    0 →  0   (all 13 _impl functions migrated BEFORE guard)
Feb 26  ■ Schema inheritance          27 → 30   (intentional overrides, not violations)
Feb 26  ■ Boundary completeness        8 →  2   ▼ 75% reduction
Feb 26  ■ Query type safety            1 →  1
Feb 27  ■ No model_dump in _impl      29 → 25   ▼ 14% reduction
Feb 27  ■ Repository pattern (impl)   26 → 17   ▼ 35% reduction
Feb 27  ■ Repository pattern (tests)  57 → ~155  ▲ grew as more tests discovered
Feb 27  ■ Migration completeness       5 →  5
Feb 27  ■ No raw MediaPackage select   3 →  3
Feb 28  ■ Obligation coverage       1012 → 451   ▼ 55% reduction
```

**Key observations:**
- Guards 1-3 were introduced with **zero violations** because the underlying refactoring (ResolvedIdentity, AdCPError, transport imports) was completed first, then the guard was added to prevent regression
- Guard 5 (boundary completeness) saw the best ratio reduction: 8 → 2 (75%)
- Guard 7 (no model_dump) went from 29 → 25, with 4 violations fixed by migrating serialization to repository/transport boundaries
- Guard 8 (repository pattern, tests) grew from 57 to ~155 because the scanner was expanded to discover more legacy test files -- the allowlist grew to capture pre-existing debt, not new violations
- Guard 11 (obligation coverage) had the most dramatic trajectory: 1,012 → 451 through a combination of reclassification (schema vs behavioral), test writing, and scanner expansion

### 10.3 Boundary Completeness Detail (8 → 2)

```
Date        Violations  Event
────────────────────────────────
Feb 26         8       Guard introduced
Feb 26         3       -5: creative test remediation fixed 5 wrappers
Feb 27         3       Registered new _impl (get_media_buys)
Mar 5          2       -1: forwarded push_notification_config in MCP
────────────────────────────────
              CURRENT: 2 (both: A2A context_id not forwarded)
```

---

## 11. Before / After: Coverage Baseline

To quantify the transformation, the codebase was checked out at commit `a5f396c3` (Feb 15, 2026 -- one day before the work began) and compared to the current state.

### Test Count Comparison

| Metric | Baseline (Feb 15) | Current (main, Mar 9) | In-flight branches | Baseline → Main | Baseline → In-flight |
|--------|-------------------|----------------------|-------------------|----------------|---------------------|
| **Test files** | 303 | 400 | -- | **+32%** | -- |
| **Test functions** | 2,846 | 4,157 | 4,759 (product) | **+46%** | **+67%** |
| **Lines of test code** | 93,070 | 132,972 | -- | **+43%** | -- |

### Breakdown by Suite

| Suite | Baseline (Feb 15) | Current (main) | Delta | Change |
|-------|-------------------|----------------|-------|--------|
| `tests/unit/` | 1,895 funcs / 171 files | 3,088 funcs / 259 files | +1,193 / +88 | **+63%** / +51% |
| `tests/integration/` | 617 funcs / 86 files | 721 funcs / 95 files | +104 / +9 | +17% / +10% |
| `tests/integration_v2/` | 196 funcs / 23 files | 195 funcs / 23 files | -1 / 0 | ~0% |
| `tests/e2e/` | 93 funcs / 17 files | 109 funcs / 17 files | +16 / 0 | +17% |
| `tests/ui/` | 8 funcs / 2 files | 7 funcs / 2 files | -1 / 0 | ~0% |

### Infrastructure That Did Not Exist Before Feb 16

| Infrastructure | Before | After |
|----------------|--------|-------|
| Structural guard tests | 0 | 11 guard files, 53 individual tests |
| Test obligation documents | 0 | 16 files defining 1,048 obligations |
| `Covers:` traceability tags | 0 | 675 tags across 30 test files |
| tox.ini (parallel test runner) | Did not exist | 5 parallel suites + coverage combine |
| Makefile (`make quality`) | Did not exist | format + lint + typecheck + unit tests |
| `tests/harness/` (test harness) | Did not exist | 7+ environment classes, dual-mode |
| `tests/factories/` (ORM factories) | Did not exist | 11 factory-boy factories |
| `docs/test-obligations/` | Did not exist | 16 obligation documents |
| `docs/development/structural-guards.md` | Did not exist | 399-line guard reference |
| `scripts/tag_obligation_ids.py` | Did not exist | 424-line obligation ID generator |
| REST API (`src/routes/api_v1.py`) | Did not exist | Full feature parity with MCP/A2A |
| `src/core/resolved_identity.py` | Did not exist | Frozen identity model for all transports |
| `src/core/exceptions.py` (AdCPError hierarchy) | Did not exist | 6-class error hierarchy |
| `src/core/database/repositories/` | Did not exist | 2 repositories + 2 UoW classes |
| `tests/coverage_scopes.yaml` | Did not exist | Per-domain scoped coverage config |

### Visualization Data (for chart)

```
Metric                    Feb 15    Mar 9 (main)    Mar 9 (product branch)
────────────────────────────────────────────────────────────────────────────
Test functions             2,846        4,157              4,759
Unit test functions        1,895        3,088              ~3,500
Integration functions        617          721                ~900
Test files                   303          400                ~450
Lines of test code        93,070      132,972            ~150,000
Structural guards              0           11                  11
Obligation tags                0            0                 675
```

---

## 12. Remaining Technical Debt

### Quantified by Structural Guards

| Debt Category | Count | Tracking |
|---------------|-------|----------|
| Uncovered behavioral obligations | 452 | `obligation_coverage_allowlist.json` |
| `model_dump()` in `_impl` functions | 25 | `salesagent-hr8n` |
| `get_db_session()` in `_impl` functions | 18 functions | `salesagent-qo8a` |
| `session.add()` in integration tests | ~155 functions | `salesagent-qo8a` |
| Raw `select(MediaPackage)` outside repository | 3 functions | `salesagent-rva2` |
| Legacy migration downgrade gaps | 5 | `salesagent-t735` |
| A2A boundary `context_id` not forwarded | 2 wrappers | `salesagent-v0kb` |
| Integer PK type mismatch in queries | 1 | `salesagent-mq3n` |

### Uncovered Obligation Breakdown

| Use Case | Uncovered | Notes |
|----------|-----------|-------|
| UC-002 (Create Media Buy) | 51 | Deep alternative/error flows |
| UC-003 (Update Media Buy) | 51 | Deep alternative/error flows |
| UC-013 (Property Lists) | 50 | New in v3.6, largely unimplemented |
| UC-012 (Content Standards) | 30 | New domain |
| UC-008 (Audience Signals) | 27 | New domain |
| UC-011 (Accounts) | 26 | New domain |
| UC-010 (Capabilities) | 21 | New domain |
| UC-004 (Delivery) | 19 | Being addressed in PR #1081 |
| UC-009 (Performance) | 19 | New domain |
| UC-005 (Creative Formats) | 15 | Being addressed in PR #1080 |
| UC-007 (Properties) | 12 | Partially addressed |
| UC-006 (Creative Sync) | 11 | Being addressed in PR #1080 |
| Business Rules | 22 | Cross-cutting |
| Constraints | 43 | Schema validation gaps |

### Architecture Improvements Still Needed

1. **Adapter async execution** -- Adapters currently run synchronously in the HTTP request cycle. The correct architecture: accept order → validate → return 201 → background worker calls adapter → update status. (Documented in `.claude/notes/async-sync-architecture.md`)

2. **Multi-transport harness for all domains** -- Currently only creative domain has the 4-transport parametrized testing. Product and delivery domains have single-transport harnesses.

3. **Repository pattern completion** -- 18 `_impl` functions still call `get_db_session()` directly instead of using repositories.

---

## Appendix: Complete Evidence Index

### Merged PRs (Chronological)

| # | Date | Title | Commit | Lines | Theme |
|---|------|-------|--------|-------|-------|
| [#1044](https://github.com/prebid/salesagent/pull/1044) | Feb 18 | refactor: enforce typed model boundaries across serialization and data flow | `c412ce9e` | +5,919/-4,865 | Serialization |
| [#1035](https://github.com/prebid/salesagent/pull/1035) | Feb 19 | fix: Unify creative URL extraction and update GAM macro mappings | `56cbc6a2` | +441/-173 | Bug fix |
| [#1051](https://github.com/prebid/salesagent/pull/1051) | Feb 20 | refactor: eliminate model_dump antipatterns and migrate to adcp library base classes | `5e6815f5` | +3,236/-1,526 | Serialization |
| [#1054](https://github.com/prebid/salesagent/pull/1054) | Feb 20 | fix: resolve property_ids/property_tags authorization in property discovery | `a188b6aa` | +656/-21 | Bug fix |
| [#1058](https://github.com/prebid/salesagent/pull/1058) | Feb 21 | fix: propagate delivery_type in GAM products_map for correct line item type selection | `ff36add6` | +657/-15 | Bug fix |
| [#1053](https://github.com/prebid/salesagent/pull/1053) | Feb 23 | fix: handle FormatId objects in format validation during media buy creation | `3e9dcaf4` | +2/-4 | Bug fix |
| [#1062](https://github.com/prebid/salesagent/pull/1062) | Feb 24 | fix: improve test harness stability and add real GAM e2e tests | `52dc2310` | +10,218/-807 | Test infra |
| [#1063](https://github.com/prebid/salesagent/pull/1063) | Feb 25 | feat: implement get_media_buys tool with delivery snapshots | `0ebcf935` | +1,323/-92 | New feature |
| [#1070](https://github.com/prebid/salesagent/pull/1070) | Feb 27 | fix: bump googleads to 49.0.0 and remove GAM_API_VERSION constant | `f6ce2a94` | +185/-178 | Bug fix |
| [#1014](https://github.com/prebid/salesagent/pull/1014) | Feb 27 | fix: change docker credentials from repo level to org level | `5aeb20f3` | +3/-3 | CI/CD |
| [#1022](https://github.com/prebid/salesagent/pull/1022) | Feb 27 | chore(main): release 1.4.0 | `c87cce27` | +31/-2 | Release |
| [#1066](https://github.com/prebid/salesagent/pull/1066) | Mar 2 | refactor: FastAPI migration -- unify MCP + A2A + Admin into single process | `7d2b1d9e` | +15,442/-8,218 | Transport |
| [#1079](https://github.com/prebid/salesagent/pull/1079) | Mar 4 | fix: resolve FormatId AttributeError crashing Add/Edit Product pages | `0b22f1e4` | +64/-3 | Bug fix |
| [#1071](https://github.com/prebid/salesagent/pull/1071) | Mar 6 | feat: AdCP v3.6 upgrade -- schema migration, auth hardening, repository pattern, multi-tenant isolation | `3398aabf` | +49,261/-3,793 | Protocol + Guards |

### Open PRs (In-Progress Domain Completions)

| # | Branch | Title | Commits Ahead |
|---|--------|-------|---------------|
| [#1082](https://github.com/prebid/salesagent/pull/1082) | `v3.6-product-completion` | Product v3.6 completion | 283 |
| [#1080](https://github.com/prebid/salesagent/pull/1080) | `v3.6-creative-completion` | Creative domain completion | 300 |
| [#1081](https://github.com/prebid/salesagent/pull/1081) | `v3.6-delivery-mb-cleanup` | Delivery domain completion | 296 |
| [#1083](https://github.com/prebid/salesagent/pull/1083) | `v3.6-error-resilience` | Error recovery classification | 9 |

### Key File Paths

| Purpose | Path |
|---------|------|
| Transport identity abstraction | `src/core/resolved_identity.py` |
| Error hierarchy | `src/core/exceptions.py` |
| MCP auth middleware | `src/core/mcp_auth_middleware.py` |
| A2A server | `src/a2a_server/adcp_a2a_server.py` |
| REST API | `src/routes/api_v1.py` |
| Repository pattern | `src/core/database/repositories/` |
| Schema inheritance | `src/core/schemas/` |
| Structural guards | `tests/unit/test_architecture_*.py` (11 files) |
| Obligation documents | `docs/test-obligations/` (16 files) |
| Obligation allowlist | `tests/unit/obligation_coverage_allowlist.json` |
| Guard documentation | `docs/development/structural-guards.md` |
| Test harness | `tests/harness/` |
| Factories | `tests/factories/` |
| Requirements methodology | `adcp-req/docs/requirements/REQUIREMENTS_METHODOLOGY.md` |

### Active Worktrees

| Path | Branch | Purpose |
|------|--------|---------|
| `/Users/konst/projects/salesagent` | `v3.6-product-completion` | Product domain |
| `/Users/konst/projects/salesagent-creative` | `v3.6-creative-completion` | Creative domain |
| `/Users/konst/projects/salesagent-delivery` | `v3.6-delivery-mb-cleanup` | Delivery domain |
| `/Users/konst/projects/salesagent-errors` | `v3.6-error-resilience-clean` | Error classification |
| `/Users/konst/projects/salesagent-admin-oath` | `admin-gam-oauth-fix` | GAM OAuth fix |

### Test Suite Snapshot (March 9, 2026)

| Suite | Count | Status |
|-------|-------|--------|
| Unit | 3,473 | All passing (3 xfail) |
| Integration | 773 | All passing (8 skip) |
| Integration V2 | 388 | All passing |
| E2E | 81 | All passing (25 skip -- require GAM credentials) |
| UI | 4 | All passing |
| **Total** | **4,719** | **All passing** |
