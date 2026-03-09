# Two Approaches to the Same Codebase: A Comparative Analysis

**Date:** March 9, 2026
**Author:** Konstantin Mirin
**Subject:** Comparing architectural transformation (Python) vs mechanical rewrite (TypeScript)
**Evidence basis:** All claims reference specific commits, files, and line numbers from both codebases

---

## Executive Summary

Two parallel efforts have been underway for the same product over the same three-week period (Feb 16 -- Mar 9, 2026):

1. **Python team**: Identified three root-cause architectural problems, derived formal requirements from the AdCP protocol specification, built structural guards to prevent regression, and grounded every test in a traceable obligation chain. Fixed 40+ production bugs along the way.

2. **TypeScript team**: Used an AI coding agent to mechanically translate the Python codebase line-by-line into TypeScript. Produced ~73,000 lines of TypeScript/TSX (55,400 production + 16,900 tests) with 515 unit tests (updated from 362 after a March 7-9 weekend push). Added 4 guardrail scripts and security hardening. Still no integration tests against a real database. No formal requirements.

This report compares the two approaches across eight dimensions, using evidence from the code itself. All TypeScript claims have been verified against the codebase as of commit `3f40c92` (March 9, 2026, post-security-fixes merge). Section 6 presents the most concrete evidence: 9 specific bugs found and fixed in Python, traced to their TypeScript equivalents — all re-verified after the weekend updates.

---

## Table of Contents

1. [Development Methodology](#1-development-methodology)
2. [Architecture Quality](#2-architecture-quality)
3. [Test Infrastructure](#3-test-infrastructure)
4. [Protocol Compliance](#4-protocol-compliance)
5. [Technical Debt Management](#5-technical-debt-management)
6. [Bug Spot-Check: 9 Python Fixes Traced to TypeScript](#6-bug-spot-check-9-python-fixes-traced-to-typescript)
7. [Cross-Tenant Security Audit](#7-cross-tenant-security-audit)
8. [Quantitative Comparison](#8-quantitative-comparison)
9. [Conclusion](#9-conclusion)

---

## 1. Development Methodology

### Python: Requirements-First Architecture

The Python team's approach followed a formal chain:

```
AdCP JSON Schema (spec v3.6.0)
  → 95+ Field Constraint YAMLs (adcp-req repo)
    → 13 Use Cases, 149 Atomic Flows
      → 71 Business Rules
        → 13 BDD Feature Files, 1,048 Obligations
          → Tests with Covers: tags
            → Structural guard enforces coverage
```

Every test traces to an obligation, every obligation traces to a spec requirement. The obligation coverage guard (`test_architecture_obligation_coverage.py`) runs on every `make quality` and fails the build if new behavioral obligations lack test coverage.

**Evidence:** `adcp-req` repository contains the full derivation chain. Obligation documents live in `docs/obligations/`. The guard's allowlist shrank from 1,012 to 451 entries over 3 weeks (commit `8191c36d` → current `obligation_coverage_allowlist.json`).

### TypeScript: Code-Translation Port

The TypeScript team used an AI coding agent to translate Python files into TypeScript, one at a time. The evidence for this methodology is in the code itself:

- **70+ comments citing Python line numbers** (e.g., `// Date-based status logic (mirrors Python operations.py L387-413)` in `mediaBuyActions.ts:143`)
- **Utility functions duplicated across files** instead of shared — `parseJsonArray()` is byte-for-byte identical in `addProduct.ts:14-52` and `editProduct.ts:12-50`
- **A duplicated DELETE endpoint** — `principalsCrud.ts:179-201` registers both `DELETE` and `POST` handlers with character-for-character identical bodies
- **Cookie-cutter boilerplate** — `request.params as { id: string }` appears 68+ times (unsafe type assertion bypassing Fastify's schema inference)
- **21 identical audit comments** — `/* audit failure must not block response */` across 13 files

A human doing a planned rewrite would not cite Python line numbers in TypeScript comments. An AI agent porting code file-by-file leaves these breadcrumbs.

**Evidence:** `SigmaSalesAgent/packages/server/src/admin/routes/operations/mediaBuyActions.ts:143`, `addProduct.ts:14-52` vs `editProduct.ts:12-50`, `principalsCrud.ts:179-201` and `:219-241`.

---

## 2. Architecture Quality

### Layer Separation

| Dimension | Python | TypeScript |
|-----------|--------|-----------|
| Business logic isolation | `_impl()` functions — zero transport imports (enforced by AST guard) | Mixed — MCP tools in 370-line monolith (`mcpProtocol.ts`), A2A skills in separate modules |
| Identity resolution | `ResolvedIdentity` frozen model, resolved once at boundary | 3 parallel auth systems, MCP duplicates auth inline (20-line copy at `mcpProtocol.ts:328-351`) |
| Error hierarchy | `AdCPError` with 6 subclasses, HTTP codes, transport mapping | **14** independent `extends Error` classes (up from 7 after Mar 7-9 push), no shared base, no HTTP codes |
| Data access | Repository pattern with auto tenant-scoping | Raw `db` import in 81+ files, manual tenant filter per query |
| Serialization boundary | `model_dump()` only at transport wrappers (guard tracks 25 remaining violations) | Three mechanisms: `stripInternalFields()`, `serializeNested()`, Zod `.parse()` |

**Evidence:**
- Python guards: `test_transport_agnostic_impl.py` (0 violations), `test_no_toolerror_in_impl.py` (0 violations), `test_impl_resolved_identity.py` (13 `_impl` functions, all compliant)
- TypeScript auth copy: `SigmaSalesAgent/packages/server/src/routes/mcp/mcpProtocol.ts:328-351`
- TypeScript error handler default 500: `SigmaSalesAgent/packages/server/src/app.ts:83-120` — `MediaBuyNotFoundError` (should be 404) falls through to 500

### MCP Runtime Architecture

The most architecturally significant difference:

- **Python:** FastMCP creates one long-lived server instance with persistent sessions, middleware, and enriched per-tool context
- **TypeScript:** Creates a **brand new `McpServer` on every HTTP POST** and destroys it when the request ends (`mcpProtocol.ts:353-369`)

```typescript
// TypeScript: mcpProtocol.ts:353-369
const mcpServer = buildMcpServer(auth, tenantId);
const transport = new StreamableHTTPServerTransport({
  sessionIdGenerator: undefined, // stateless — no session tracking
});
// ... handle request ...
await mcpServer.close().catch(() => undefined);  // destroyed here
```

This means: no session persistence, no conversation history, no workflow tracking across calls. The MCP protocol's stateful capabilities cannot function. The AI agent saw "MCP server with tools" in Python and produced "MCP server with tools" in TypeScript, but missed that one is a **stateful singleton** and the other is a **disposable-per-request factory**.

**Evidence:** `SigmaSalesAgent/packages/server/src/routes/mcp/mcpProtocol.ts:353-369`, GitHub Issue [StellarPOC/SalesAgent#4](https://github.com/StellarPOC/SalesAgent/issues/4)

---

## 3. Test Infrastructure

| Metric | Python | TypeScript (as of Mar 9) |
|--------|--------|-----------|
| Total tests | 4,719 passing (4,157 on main + in-flight) | **515** (up from 362 after Mar 7-9 push) |
| Test files | 400 | **63** (up from 47) |
| Unit tests | 3,088 functions | 515 (all unit) |
| Integration tests (real DB) | 721 functions | **0** |
| E2E tests | 109 functions | **0** |
| Structural guard tests | 11 guards, 53 test functions | **4 guardrail scripts** (see Section 5) |
| Test factories | 11 polyfactory ORM factories | **None** |
| Shared test helpers | `tests/harness/` (7 environment classes) | **None** (93 `vi.mock()` calls across 42 files) |
| Obligation traceability | 675 `Covers:` tags across 30 files | **None** |
| Coverage scope tracking | Per-domain (creative >95%, delivery 97.9%) | **None** |
| Admin route test coverage | Present | ~7% (6 of 80+ routes) |

### What This Means in Practice

Every TypeScript test mocks the database. The pattern reconstructs Drizzle's fluent API as chains:

```typescript
// SigmaSalesAgent: mediaBuyCreateService.spec.ts:30-45
function mockSelectChain(rows: unknown[]) {
  return {
    from: vi.fn().mockReturnThis(),
    where: vi.fn().mockResolvedValue(rows),
  };
}
```

Tests configure 4+ sequential `.mockReturnValueOnce()` calls in exact order, mapping 1:1 to the implementation's query sequence. If someone reorders two independent DB queries, every test breaks — even if behavior is unchanged.

The Python team explicitly rejected this approach. Integration tests run against real PostgreSQL. The multi-transport harness (`tests/harness/`) parametrizes tests across IMPL/A2A/REST/MCP, catching transport-specific bugs automatically.

The March 7-9 weekend push added `mcpProtocol.integration.spec.ts` (1,271 lines, 32 tests). Despite the filename, it contains **16 `vi.mock()` calls** replacing every downstream service. It uses Fastify `app.inject()` (in-memory, no network). You could delete every service implementation and this test would still pass. It is a unit test of the route handler, mislabeled as "integration."

**Evidence:**
- Python test harness: `tests/harness/transport.py`, `tests/harness/dispatchers.py`, `tests/harness/_base.py`
- TypeScript mock pattern: `SigmaSalesAgent/packages/server/src/services/mediaBuyCreateService.spec.ts:30-45`
- Mislabeled "integration" test: `SigmaSalesAgent/packages/server/src/routes/mcp/mcpProtocol.integration.spec.ts` (16 `vi.mock()` calls)

---

## 4. Protocol Compliance

| Dimension | Python | TypeScript (verified Mar 9) |
|-----------|--------|-----------|
| Schema source | `adcp` PyPI library, inheritance pattern (`Library*` alias) | `@adcp/client` v4.5.0 added as dependency (Mar 7-9), but **only 2 runtime imports** — all 18 Zod schema files remain hand-written |
| Drift protection | `test_architecture_schema_inheritance.py` + `test_adcp_contract.py` | `adcpSdkValidation.spec.ts` constructs hand-crafted payloads against SDK schemas — tests the team's *understanding* of the spec, not actual server output |
| Protocol version | v3.6.0 (current) | `@adcp/client` v4.5.0 installed, but runtime schemas are still hand-written against earlier spec |
| `delivery_measurement` | **REQUIRED** on Product (commit `f9df3747`) | Still **optional** (`product.ts:116`) — verified Mar 9 |
| `buyer_campaign_ref` | Supported (commit `d9512a64` fixed forwarding) | Still **entirely absent** from all schemas and code — verified Mar 9 |
| Schema update path | Bump `adcp` version, run tests, failures show drift | Manually edit 18 Zod files; SDK used only as test comparison target |

**`@adcp/client` was added but not used as the runtime source of truth.** The March 7-9 push added `@adcp/client@^4.5.0` as a dependency. Verification shows only 2 runtime files import it: `capabilitiesService.ts` (the one correct usage — `.parse()` against SDK schema) and `adcpAgentClient.ts` (client utilities for calling external agents). Every other runtime schema (`product.ts`, `mediaBuyCreate.ts`, `creative.ts`, `syncCreatives.ts`, `mediaBuyUpdate.ts`, `mediaBuyList.ts`, `mediaBuyDelivery.ts`, `creativeFormats.ts`) imports only `zod` — no `@adcp/client` schemas.

The `adcpSdkValidation.spec.ts` test does **not** validate actual server output against SDK schemas. It constructs minimal payloads and runs `.safeParse()`. If a service returns a response with an extra field, a renamed field, or a missing required field, this test would not catch it because it never sees actual service output.

**Evidence:**
- Python AdCP v3.6 enforcement: `salesagent` commit `f9df3747`
- TypeScript optional delivery_measurement: `SigmaSalesAgent/packages/server/src/schemas/product.ts:116` (verified Mar 9)
- `buyer_campaign_ref` absent: `grep -r "buyer_campaign_ref" packages/server/src/` returns 0 results (verified Mar 9)
- `@adcp/client` runtime imports: only `capabilitiesService.ts` and `adcpAgentClient.ts` (verified Mar 9)
- Hand-rolled schemas: all 8 listed above import only `zod`, not `@adcp/client` (verified Mar 9)

---

## 5. Technical Debt Management

### Python: Quantified, Tracked, Enforced

Every category of debt is tracked by a structural guard with a shrink-only allowlist:

| Debt Category | Count | Guard | Tracker |
|---------------|-------|-------|---------|
| Uncovered obligations | 452 | `test_architecture_obligation_coverage.py` | `obligation_coverage_allowlist.json` |
| `model_dump()` in `_impl` | 25 | `test_architecture_no_model_dump_in_impl.py` | `salesagent-hr8n` |
| `get_db_session()` in `_impl` | 17 | `test_architecture_repository_pattern.py` | `salesagent-qo8a` |
| Raw MediaPackage queries | 3 | `test_architecture_no_raw_media_package_select.py` | `salesagent-rva2` |
| Boundary param gaps | 2 | `test_architecture_boundary_completeness.py` | `salesagent-v0kb` |
| Legacy migration gaps | 5 | `test_architecture_migration_completeness.py` | `salesagent-t735` |

Allowlists can only shrink — adding new violations fails the build. Every allowlisted item has a `# FIXME(salesagent-xxxx)` comment at source.

### TypeScript: 4 Guardrails Added (Mar 7-9), Different Category

The March 7-9 push added 4 executable guardrail scripts in `scripts/guardrails/`, run via CI (`.github/workflows/guardrails.yml`):

| TS Guardrail | What it checks |
|---|---|
| `check-raw-fetch.mjs` | No new raw `fetch()` in protected directories (must use `safeFetch`) |
| `check-privileged-route-coverage.mjs` | Admin routes calling `requireTenantPermission` must be in a manifest with matching test files |
| `check-regression-tests.mjs` | Changes to sensitive files (auth, OIDC, security) must be accompanied by test file changes |
| `check-rollup-lockfile.mjs` | `package-lock.json` includes Linux rollup binary |

Additionally, a 344-line `AGENTS.md` documents extensive rules for security, permissions, OIDC, MCP, error handling, and testing.

**The gap is in what they enforce.** None of the 4 TS guardrails overlap with any of the 11 Python structural guards:

| Python Guard | What it enforces | TS equivalent? |
|---|---|---|
| No transport imports in `_impl` | Business logic can't import from fastmcp/a2a | **None** |
| No `ToolError` in `_impl` | Must use `AdCPError` hierarchy | **None** |
| `_impl` accepts `ResolvedIdentity` | Not raw `Context` objects | **None** |
| Wrappers forward all `_impl` params | MCP/A2A completeness | **None** |
| No `.model_dump()` in `_impl` | No serialization in business logic | **None** |
| No `get_db_session()` in `_impl` | DB access through repositories | **None** |
| Schemas extend AdCP library types | No field duplication | **None** |
| DB queries use correct types | Type safety in queries | **None** |
| No duplicate route registrations | Route conflict prevention | **None** |
| Responses conform to AdCP spec | Protocol compliance | **None** |
| Obligation coverage | Behavioral tests match spec obligations | **None** |

**0 of 11 Python structural guards have TS equivalents.** The TS guardrails catch a different, narrower set of concerns (raw fetch, permission registration, test-with-change, lockfile). They do not catch:
- Fat controllers with inline DB queries and business logic
- Raw database access in route handlers (all 81 admin routes import `db` directly)
- Business logic in transport layer
- Missing tenant isolation in queries
- Inconsistent error handling patterns

**How to violate the architecture and pass all TS guardrails:** Write a 500-line admin route handler that mixes DB queries, business logic, HTTP handling, and outbound calls — with no tenant filter on one query, three different error response shapes, and a silent catch block — and every guardrail passes clean, as long as you use `safeFetch` for outbound calls and register your permissions.

**AGENTS.md documents extensive rules — ~90% are unenforced.** Only 3 of the document's categories have executable guardrails. Everything else (error hierarchy, repository pattern, schema inheritance, transport boundary separation) is enforced by developer discipline only.

**Evidence:**
- Python guard progression: obligation allowlist 1,012 → 451 over 3 weeks
- TS guardrails: `SigmaSalesAgent/scripts/guardrails/` (4 files, verified Mar 9)
- TS CI: `.github/workflows/guardrails.yml`
- AGENTS.md: `SigmaSalesAgent/AGENTS.md` (344 lines)

---

## 6. Bug Spot-Check: 9 Python Fixes Traced to TypeScript

This is the core empirical test. Over 3 weeks, the Python team found and fixed 40+ production bugs. We traced 9 of the most significant to their TypeScript equivalents.

All 9 spot-checks were **re-verified on March 9** against the TypeScript codebase after the weekend security/compliance push (PR #5 `differences`, PR #7 `AdCP`, PR #8 `security_fixes`). None of the 9 traced bugs were fixed by the weekend changes.

### Methodology

For each Python fix:
1. Read the Python commit to understand the bug and fix
2. Find the equivalent TypeScript code
3. Check if the same bug exists
4. Check if there's a test that would catch it

### Results

| # | Bug | Python Fix | Same Bug in TS? | TS Test? |
|---|-----|-----------|-----------------|----------|
| 1 | `push_notification_config` silently dropped in MCP `create_media_buy` | Commit `204ad527`, test `test_push_notification_forwarding.py` (142 lines) | **PARTIALLY** — not in MCP tool schema, undiscoverable by clients | **NO** |
| 2 | Media buy status stuck at `pending_approval` after successful adapter execution | Commit `fb703b71`, test `test_execute_approved_status_update.py` (184 lines) | **NO** — different architecture handles status transitions | PARTIALLY |
| 3 | CPC clicks never computed — enum vs string comparison always `False` | Commit `35166f48`, test `test_delivery_poll_behavioral.py` (150 lines) | **NO** — but only because delivery metrics are entirely hardcoded to zero | **NO** |
| 4 | `Creative.name` accidentally optional, `null` crashes at DB NOT NULL | Commit `d3705e85`, test `test_creative_schema_regression.py` (104 lines) | **YES** — `syncCreatives.ts:19` has `name: z.string().optional()` | PARTIALLY — strict mode tested, lenient mode gap |
| 5 | `buyer_campaign_ref` and `ext` silently dropped by transport wrappers | Commit `d9512a64`, test `test_boundary_field_forwarding.py` (206 lines, 7 AST tests) | **PARTIALLY** — `buyer_campaign_ref` **entirely absent** from TS codebase; `ext` missing from MCP create schema | **NO** |
| 6 | `is_fixed_price` filter used wrong field name, never matched | Commit `dbf8cd56`, integration tests in `test_product_v3.py` | **NO** — TS correctly uses `option.fixed_price != null` | YES |
| 7 | `BrandReference isinstance(dict)` always `False` — policy rejected all requests | Commits `2e53eaeb` + `1223e024` | **NO** — TS uses simpler presence check, no type confusion | YES |
| 8 | `status_filter` silently ignored with `media_buy_ids` or `buyer_refs` | Commit `59f3ee1b` by Brian O'Kelley | **NO** — TS uses single accumulated conditions array | PARTIALLY — `mediaBuyListService` has **zero tests** |
| 9 | SSRF in `property_list_resolver` — buyer URL fetched without validation | Commit `71bd9761`, test `test_property_list_resolver.py` (132 lines, 20 tests) | **N/A** — feature not implemented (capability set to `false`) | N/A |

### What the Spot-Check Reveals

**3 bugs where TypeScript is correct** (#6, #7, #8): The TS code avoids these specific bugs through different implementation choices (array iteration instead of RootModel unwrapping, presence check instead of isinstance, single query builder instead of three paths). However, #8 has **zero test coverage** — the code is correct by coincidence, not by verification.

**2 bugs where TypeScript has the same or analogous bug** (#4, #5): `Creative.name` is optional in the TS input schema, matching the exact Python bug. `buyer_campaign_ref` doesn't exist at all — the entire AdCP field was never ported.

**2 bugs where TypeScript avoids the bug by not implementing the feature** (#3, #9): CPC delivery metrics are hardcoded to zero. Property list resolution is disabled. The bugs can't occur because the functionality doesn't exist.

**2 bugs with partial issues** (#1, #2): Push notification config isn't in the MCP tool schema (undiscoverable by clients, but survives passthrough). Status transitions use a different architecture that works.

### The Key Insight

The Python team found these bugs **because they have the test infrastructure to find them**: integration tests running against real PostgreSQL, structural guards scanning for pattern violations, obligation-grounded tests that define expected behavior from the spec. The TypeScript rewrite has none of these mechanisms — bugs like #4 (`Creative.name` optional) exist silently, discoverable only when they crash in production.

---

## 7. Cross-Tenant Security Audit

The Python team found and fixed **13 cross-tenant query leaks** (commit `a1f315da`) where queries were missing `tenant_id` in WHERE clauses. They then built a structural guard (`test_cross_tenant_query_isolation.py`, 294 lines, 10 AST-scanning regression tests) to prevent this class of bug from recurring.

We audited the TypeScript codebase for the same vulnerability. The initial audit (before the March 7-9 weekend push) found 13 unsafe queries. After the security fixes in PR #8 (`security_fixes` branch), we re-verified every location.

### Findings: 4 Fixed, 9 Still Vulnerable (verified Mar 9, post-PR #8)

| # | File | Line | Query Type | Missing Filter | Fixed? |
|---|------|------|-----------|----------------|--------|
| 1 | `mediaBuyUpdateService.ts` | 148 | SELECT media buy | `tenantId` | **NO** |
| 2 | `reviewActions.ts` | 48 | Raw SQL creative_assignments (1st) | `tenant_id` | **YES** |
| 3 | `reviewActions.ts` | 65 | Raw SQL creative_assignments (2nd) | `tenant_id` | **NO** |
| 4 | `reviewActions.ts` | 72 | SELECT creatives (cascade) | `tenantId` | **NO** |
| 5 | `reviewActions.ts` | 92 | **UPDATE** media buy (cascade) | `tenantId` | **NO** |
| 6 | `stepActions.ts` | 41 | SELECT media buy | `tenantId` | **YES** |
| 7 | `stepActions.ts` | 54 | Raw SQL creative_assignments | `tenant_id` | **NO** |
| 8 | `stepActions.ts` | 61 | SELECT creatives | `tenantId` | **NO** |
| 9 | `stepActions.ts` | 74 | **UPDATE** media buy (pending_creatives) | `tenantId` | **NO** |
| 10 | `stepActions.ts` | 88 | **UPDATE** media buy (scheduled) | `tenantId` | **NO** |
| 11 | `mediaBuyActions.ts` | 113 | SELECT media buy | `tenantId` | **YES** |
| 12 | `mediaBuyActions.ts` | 121 | Raw SQL creative_assignments | `tenant_id` | **YES** |
| 13 | `mediaBuyActions.ts` | 128 | SELECT creatives | `tenantId` | **NO** |
| 14 | `mediaBuyDetail.ts` | 29 | SELECT media packages | `tenantId` | **NO** |
| 15 | `creativePages.ts` | 89 | SELECT media buys (batch) | `tenantId` | **NO** |
| 16 | `mediaBuyStatusWorker.ts` | 91 | **UPDATE** media buy | `tenantId` | **NO** |
| 17 | `mediaBuyUpdateService.ts` | 233 | SELECT media packages | `tenantId` | **NO** |

The security fixes addressed the top-level lookups (media buy, creative_assignments in mediaBuyActions) but left the **cascade sub-queries and UPDATE operations** unfixed. The pattern is consistent: the first query in each function was fixed, but the downstream queries within the same function were not.

**3 of the remaining 9 are UPDATE operations** (#5, #9, #10) that can modify data across tenant boundaries. The background worker UPDATE (#16) is also unfixed.

### Guards and Tests

| | Python | TypeScript (verified Mar 9) |
|--|--------|-----------|
| Structural guard for tenant isolation | `test_cross_tenant_query_isolation.py` (294 lines, 10 AST tests) | **None** — not added in Mar 7-9 push |
| Cross-tenant tests | Yes — verify query-level filtering | Surface-level only — verify auth middleware returns 403 |
| Repository pattern | Auto-scopes queries to tenant in constructor | **No repository** — manual filter per query |

The TypeScript "cross-tenant" tests (`adminSchemaBound.validation.spec.ts`) verify that the route-level auth guard (`requireTenantAccess`) returns 403. They do **not** verify that individual SQL queries include `tenantId` in their WHERE clauses. None of the 4 new TS guardrails scan for missing tenant filters. If any code path bypasses or runs after the auth check (cascade functions, background workers), the queries offer no tenant boundary.

**Evidence (all verified Mar 9 against commit `3f40c92`):**
- Python guard: `tests/unit/test_cross_tenant_query_isolation.py`
- TypeScript unsafe queries (still present): `reviewActions.ts:65,72,92`, `stepActions.ts:54,61,74,88`, `mediaBuyActions.ts:128`, `mediaBuyDetail.ts:29`, `creativePages.ts:89`, `mediaBuyStatusWorker.ts:91`, `mediaBuyUpdateService.ts:148,233`
- TypeScript fixed queries: `reviewActions.ts:48`, `stepActions.ts:41`, `mediaBuyActions.ts:113,121`

---

## 8. Quantitative Comparison

### Head-to-Head Metrics (verified Mar 9)

| Metric | Python (Feb 16 → Mar 9) | TypeScript (~2 weeks + weekend push) |
|--------|--------------------------|----------------------|
| Lines of code | ~265,000 total (~98K source, ~147K tests, ~10K migrations) | ~73,000 TS/TSX (~55K production, ~17K tests) + ~17K non-TS assets |
| Total tests | 2,846 → 4,719 (+66%) | **515** (up from 362) |
| Integration tests (real DB) | 617 → 721 | **0** (unchanged) |
| E2E tests | 93 → 109 | **0** (unchanged) |
| Structural guards | 0 → 11 AST-scanning | **4 guardrail scripts** (different category — see Section 5) |
| Obligation traceability | 0 → 675 `Covers:` tags | **0** (unchanged) |
| Bugs found and fixed | 40+ with regression tests | Unknown (no mechanism to find them) |
| Protocol version | v3.6.0 (current) | `@adcp/client` v4.5.0 added but only 2 runtime imports |
| PRs merged | 14 | 8 |
| Admin route test coverage | Present | ~7% |
| Cross-tenant guards | AST-scanning + repository pattern | **None** (9 unsafe queries remain after partial fix) |
| Error hierarchy | 6-class tree with HTTP codes | **14** independent classes, no shared base (doubled, got worse) |
| Schema source | Library inheritance + contract tests | `@adcp/client` in tests only; 18 runtime schemas still hand-written |
| Security hardening | SSRF validation, hmac.compare_digest, Host header validation | **SSRF-safe fetch, AES-256-GCM secrets** (well-implemented) |
| Test-to-production ratio | **1.5:1** (147K tests / 98K source) | **0.3:1** (17K tests / 55K production) |

### What Got Built vs What Got Ported

**Python (built):**
- 11 structural guards that run on every build
- Formal requirements chain (spec → use cases → BDD → tests)
- Multi-transport test harness (same test runs 4x across IMPL/A2A/REST/MCP)
- Repository + UoW pattern for data access
- Frozen `ResolvedIdentity` model for transport-agnostic business logic
- 6-class `AdCPError` hierarchy with transport mapping
- Factory-based test fixtures (11 ORM factories)
- Per-domain scoped coverage (creative >95%, delivery 97.9%)
- REST API with full parity to MCP/A2A
- tox parallel test runner (5 suites simultaneously)

**TypeScript (ported + weekend push):**
- All 11 MCP tools + 3 HITL task tools
- A2A skill layer with modular registry
- Admin UI with 80+ routes
- GAM + Mock adapters
- 6 background workers
- 4 AI agents with identical prompts
- Audit logging via Fastify plugin
- SSRF-safe fetch + AES-256-GCM secret encryption (well-implemented)
- 4 guardrail scripts + CI workflow
- AGENTS.md guardrails document (344 lines, ~90% unenforced)
- `@adcp/client` dependency (used in tests, not runtime schemas)
- OIDC, session, and permission tests (high-value security additions)

The Python team built **infrastructure to find and prevent bugs**. The TypeScript team built **features** and then added **surface-level guards and security hardening** — valuable, but not addressing the structural problems (repository pattern, error hierarchy, schema source of truth, cross-tenant query isolation, integration testing).

### Cost of "Catching Up"

To bring the TypeScript codebase to architectural parity, it would need:

1. **Integration test suite** — still 0 tests against real PostgreSQL (unchanged by weekend push). The Python suite has 721.
2. **Structural guards for architecture** — the 4 new guardrails cover fetch safety, permissions, and test pairing. None of the 11 Python guards (transport isolation, schema inheritance, repository pattern, boundary completeness, etc.) have TS equivalents.
3. **Repository pattern** — still every service and admin route imports `db` directly (unchanged by weekend push). All 81 admin route files need refactoring.
4. **Error hierarchy** — now **14** disconnected error classes (up from 7, got worse). Need unified base with HTTP codes.
5. **Schema migration** — `@adcp/client` added but used only in tests. All 18 runtime Zod files still hand-written. Need to import SDK schemas as base types.
6. **MCP runtime** — still stateless per-request factory (now codified as "intentional" in AGENTS.md). Need persistent session architecture.
7. **Cross-tenant query audit** — 4 of 13 original queries fixed, **9 still vulnerable** (including 3 UPDATEs). No structural guard added.
8. **Protocol gaps** — `delivery_measurement` still optional, `buyer_campaign_ref` still entirely absent. Need to reach AdCP v3.6 compliance.
9. **Obligation traceability** — still 0. Python has 675 Covers: tags across 30 files.
10. **Factory fixtures** — still no test helpers. 93 `vi.mock()` calls across 42 files, each recreating mock chains independently.

Each of these items is a multi-day effort. Together, they represent roughly the same investment that was already made in the Python codebase over 3 weeks — except the TypeScript team would be building these from scratch while also maintaining the features they've already ported.

---

## 9. Conclusion

### What Each Approach Demonstrated

**Python approach:** That identifying root causes, deriving requirements from the protocol specification, and building automated guardrails produces a codebase where:
- Bugs are found by infrastructure (guards, integration tests, obligation coverage) before they reach production
- Every fix comes with a regression test grounded in a spec requirement
- Technical debt is quantified, tracked, and systematically reduced
- New violations are prevented at build time, not discovered in production

**TypeScript approach:** That an AI coding agent can produce ~73,000 lines of TypeScript/TSX (55,400 production, 16,900 tests) in approximately two weeks, compiling and passing 515 unit tests. A weekend push added security hardening, guardrails, and `@adcp/client` as a test dependency. It also demonstrated that:
- Speed without architectural oversight reproduces every known weakness of the original
- Features ported without understanding carry the same bugs (Creative.name optional, cross-tenant query leaks — both still present after weekend security fixes)
- Reactive hardening addresses *visible* problems (security, guardrails documentation) while leaving *structural* problems untouched (repository pattern, error hierarchy, schema source of truth, integration testing)
- The shape looks right but the behavior has gaps (stateless MCP, hardcoded delivery metrics, missing AdCP fields — all unchanged after weekend push)

### The Weekend Push Pattern

The March 7-9 weekend burst (+15,303 lines across 177 files) is itself a data point. It addressed the *visible* problems — security hardening, `@adcp/client` dependency, guardrails documentation, MCP protocol tests — while leaving every *structural* problem untouched: no repository pattern, no error hierarchy, no schema migration, no integration tests, 9 of 13 cross-tenant queries still vulnerable.

The guardrails added are genuine improvements for security surface. But they are surface-level: a 500-line admin route handler with inline DB queries, no tenant filter, three error response shapes, and a silent catch block passes all 4 guardrails clean. The Python guards catch exactly these patterns.

This is the pattern of reactive engineering: fix what's embarrassing, ship more features, defer the architecture.

### The Security Hardening Proof

The TypeScript team's security hardening — `safeFetch` (SSRF protection), AES-256-GCM token encryption, rate limiting, guardrail scripts — is likely to be presented as a flagship achievement. It is real, well-implemented work totaling ~1,700 lines of code.

But it is also the most convincing evidence that approach matters more than any individual feature.

The Python codebase can achieve identical security hardening in approximately **220 lines of code** — because the architecture already has the seams. Each security feature maps to exactly one layer and one insertion point:

| Security Feature | Python Insertion Point | Why It's Trivial |
|---|---|---|
| SSRF protection | Consolidate 2 existing validators into `safe_request()`, wire into ~4 call sites | Transport boundary pattern means `_impl` functions never make HTTP calls — only the boundary layer does |
| Token encryption at rest | Add property getters/setters to 6 model fields (pattern already exists on `Tenant._gemini_api_key`) | ORM model encapsulation means encrypt/decrypt is invisible to all callers |
| Token hashing | SHA-256 hash in `auth_utils.py` lookup + migration | Single auth resolution path (`resolve_identity`) means one change point |
| Rate limiting | 10 lines of nginx config | All 3 transports (MCP, A2A, Admin) pass through nginx — zero Python code |
| Security headers | `@app.after_request` hook or nginx config | Standard middleware pattern |
| Session cookie fix | 1 line: `SESSION_COOKIE_HTTPONLY = True` | — |

The TypeScript team needed ~1,700 lines because there is no pre-existing architecture to hang these features on. `safeFetch` had to be threaded through 7 call sites across services, workers, and routes — all at the same level of abstraction, all making direct HTTP calls. Token encryption had to be wired into 10 files that directly read and write credentials. There is no repository layer to encapsulate data access, no transport boundary to isolate HTTP calls, no ORM property pattern to make encryption transparent.

The Python team invested 3 weeks building those architectural seams. The payoff is that security hardening — the TypeScript team's biggest visible achievement — is a quiet afternoon of work in Python. Not because the security features are unimportant, but because the architecture makes them trivial to add correctly.

This is what architecture-first development looks like in practice: the hard work is invisible (structural guards, repository patterns, transport boundaries), and the visible features become easy.

### The Fundamental Question

The choice is not "Python vs TypeScript." The choice is between two development methodologies:

1. **Requirements-driven architecture** — slower to produce features, but every feature is grounded in spec requirements, protected by guards, and verified by traceable tests. Bugs are found before production.

2. **AI-assisted mechanical translation** — fast to produce features, but carries hidden debt, untested code paths, and no mechanism to prevent regression. Bugs are found in production. Weekend pushes add surface hardening but don't address root causes.

The Python codebase's 3-week transformation is evidence that the first approach works: 40+ production bugs found and fixed, 11 structural guards built, 1,048 obligations derived, 675 test-to-spec traceability links created, and technical debt quantified from "unknown" to specific allowlist entries.

The TypeScript rewrite — even after the weekend hardening push — is evidence that the second approach produces something that looks increasingly complete but remains structurally unsound. The infrastructure to discover its own problems (integration tests, tenant isolation guards, obligation traceability, schema source-of-truth enforcement) still does not exist.

---

## Appendix: Evidence Index

### Python Repository (prebid/salesagent)

| Evidence | Reference |
|----------|-----------|
| Architecture transformation report | `docs/ARCHITECTURE_TRANSFORMATION_REPORT.md` |
| 14 merged PRs | #1044, #1035, #1051, #1054, #1058, #1053, #1062, #1063, #1070, #1014, #1022, #1066, #1079, #1071 |
| 4 open PRs | #1082 (product), #1080 (creative), #1081 (delivery), #1083 (error resilience) |
| 11 structural guards | `tests/unit/test_architecture_*.py`, `test_no_toolerror_in_impl.py`, `test_transport_agnostic_impl.py`, `test_impl_resolved_identity.py` |
| Obligation coverage allowlist | `tests/unit/obligation_coverage_allowlist.json` |
| Cross-tenant guard | `tests/unit/test_cross_tenant_query_isolation.py` |
| ResolvedIdentity model | `src/core/resolved_identity.py` |
| AdCPError hierarchy | `src/core/exceptions.py` |
| Requirements derivation | `adcp-req` repository |

### TypeScript Repository (SigmaSalesAgent) — verified against commit `3f40c92` (Mar 9)

| Evidence | Reference |
|----------|-----------|
| Technical review (updated) | `report-port.md` (sections A-H, including G: March 7-9 update) |
| Feature parity tracking | `docs/FEATURE_PARITY.md` |
| MCP monolith | `packages/server/src/routes/mcp/mcpProtocol.ts` |
| MCP per-request factory (still present) | `mcpProtocol.ts:186-193` (`new McpServer` per request), `:1241` (`buildMcpServer` call) |
| Error handler | `packages/server/src/app.ts:83-120` |
| 14 error classes (up from 7) | `safeFetch.ts`, `secrets.ts`, `requestContext.ts`, `dispatcher.ts`, `pushNotificationService.ts`, `contextService.ts` (4 classes), `taskDetailService.ts`, `mediaBuyLookup.ts` (2 classes), `contextEventService.ts`, `taskCompleteService.ts` |
| Inline auth copy | `packages/server/src/routes/mcp/mcpProtocol.ts:328-351` |
| Hand-written schemas (still 18 files) | `packages/server/src/schemas/*.ts` — all import `zod` only, not `@adcp/client` |
| `@adcp/client` v4.5.0 (test-only) | Only 2 runtime imports: `capabilitiesService.ts`, `adcpAgentClient.ts` |
| 4 guardrail scripts | `scripts/guardrails/check-raw-fetch.mjs`, `check-privileged-route-coverage.mjs`, `check-regression-tests.mjs`, `check-rollup-lockfile.mjs` |
| AGENTS.md | `AGENTS.md` (344 lines, ~90% unenforced) |
| Mislabeled integration test | `mcpProtocol.integration.spec.ts` (16 `vi.mock()` calls, 32 tests) |
| Cross-tenant: 4 fixed, 9 still vulnerable | Fixed: `reviewActions.ts:48`, `stepActions.ts:41`, `mediaBuyActions.ts:113,121`. Still vulnerable: `reviewActions.ts:65,72,92`, `stepActions.ts:54,61,74,88`, `mediaBuyActions.ts:128`, `mediaBuyDetail.ts:29`, `creativePages.ts:89`, `mediaBuyStatusWorker.ts:91`, `mediaBuyUpdateService.ts:148,233` |
| Creative.name optional (still present) | `packages/server/src/schemas/syncCreatives.ts:19` |
| buyer_campaign_ref absent (still) | `grep -r "buyer_campaign_ref" packages/server/src/` → 0 results |
| delivery_measurement optional (still) | `packages/server/src/schemas/product.ts:116` |
| Delivery metrics stubbed | `packages/server/src/services/deliveryQueryService.ts:295-296` |
| Security hardening (new, well-done) | `security/safeFetch.ts` (SSRF), `security/secrets.ts` (AES-256-GCM), `security/outboundUrl.ts` |
