# adcp 3.2→3.6 Migration: Comprehensive Review Summary

## Scope

- **Branch:** `KonstantinMirin/adcp-v3-upgrade`
- **Migration:** adcp 3.2.0 → 3.6.0, plus Flask→FastAPI unification
- **Stats:** 289 files changed, 24,268 lines added, 8,138 lines removed
- **Review date:** 2026-02-26
- **Methodology:** 11 parallel agents processed 252 requirement files from adcp-req and reviewed all changed source files

## Deliverables

### Test Obligations (docs/test-obligations/) — 15 files, 412KB

Extracted from `/Users/konst/projects/adcp-req/docs/requirements/` using logical reasoning against the AdCP spec and current salesagent implementation.

| File | Scenarios | Key 3.6 Impact |
|------|-----------|----------------|
| UC-001-discover-available-inventory.md | 155 | Product schema fields (qo8a FIXED), adapter support annotation (mq3n) |
| UC-002-create-media-buy.md | 141 | Boundary field drops (7gnv), 10 dedicated propagation scenarios |
| UC-003-update-media-buy.md | 91 | buyer_campaign_ref/ext roundtrip (7gnv), creative model (goy2) |
| UC-004-deliver-media-buy-metrics.md | 74 | pricing_option_id lookup (mq3n), 6 P0 regression scenarios |
| UC-005-discover-creative-formats.md | 31 | Low direct impact (format discovery prerequisite for UC-006) |
| BR-UC-006-sync-creatives.md | 89 | Creative wrong base class (goy2), 10 P0 schema compliance scenarios |
| BR-UC-007-list-authorized-properties.md | 24 | Low impact |
| BR-UC-008-manage-audience-signals.md | 34 | Medium impact (signal targeting fields) |
| BR-UC-009-update-performance-index.md | 24 | Medium impact |
| BR-UC-010-discover-seller-capabilities.md | 28 | High impact (capabilities response accuracy) |
| BR-UC-011-manage-accounts.md | 46 | High impact (account field in responses) |
| BR-UC-012-manage-content-standards.md | 41 | High impact (new v3 domain) |
| BR-UC-013-manage-property-lists.md | 58 | High impact (new v3 domain) |
| business-rules.md | 71 rules | 4 bugs mapped to affected rules |
| constraints.md | 134 constraints | Full schema constraint coverage |
| **Total** | **836+ scenarios, 205 obligations** | |

### Code Reviews (docs/code-reviews/) — 5 layer reviews + this summary

| File | Focus | Critical | High | Medium | Low |
|------|-------|----------|------|--------|-----|
| 01-schema-model-layer.md | Pydantic models, DB models, conversions | 2 | 2 | 2 | 1 |
| 02-api-boundary-layer.md | MCP/A2A tools, field propagation | 3 | 2 | 3 | 2 |
| 03-transport-infrastructure.md | FastAPI app, auth, tenant isolation | 3 | 3 | 2 | 1 |
| 04-adapter-layer.md | GAM/Mock/Broadstreet adapters, pricing | 1 | 2 | 2 | 1 |
| 05-test-infrastructure.md | Test coverage, fixtures, tox | 2 | 2 | 2 | 0 |
| **Total** | | **11** | **11** | **11** | **5** |

---

## Critical Issues (11)

### Previously Filed Bugs (3) — All Confirmed by Review

#### CRIT-1: Creative extends wrong adcp base class [salesagent-goy2, P1]

**Reviews:** 01-schema-model (CRITICAL-1), 05-test-infrastructure (Coverage Gap #1)
**Confidence:** 100%

`from adcp.types import Creative` resolves to `adcp.types.generated_poc.creative.get_creative_delivery_response.Creative` — a delivery metrics model, not a creative entity. This model requires `variants: list[CreativeVariant]` and lacks `name`, `status`, `created_date`, `updated_date`.

**Impact chain:**
- `variants=[]` hardcoded in all listing paths (listing.py:367, _validation.py:60)
- `name`, `status`, `created_date`, `updated_date` marked `exclude=True` and stripped from all responses
- Every `list_creatives` and `sync_creatives` response is schema-violating
- Buyers cannot check creative approval status from API responses

**Files:** `src/core/schemas.py:14,1655`, `src/core/tools/creatives/listing.py:367`

#### CRIT-2: PricingOption delivery lookup string vs integer PK [salesagent-mq3n, P1]

**Reviews:** 01-schema-model (CRITICAL-2), 02-api-boundary (CRITICAL-2), 05-test-infrastructure (Coverage Gap #2)
**Confidence:** 100%

`_get_pricing_options()` queries `PricingOption.id` (integer PK) with synthetic string IDs like `"cpm_usd_fixed"`. PostgreSQL returns 0 rows. All pricing context in delivery metrics is `None`. CPC click calculations never execute.

**Files:** `src/core/tools/media_buy_delivery.py:645-649` (query), `src/core/tools/media_buy_create.py:1003` (string ID generation)

#### CRIT-3: MediaBuy boundary drops fields [salesagent-7gnv, P1]

**Reviews:** 02-api-boundary (CRITICAL-1), 05-test-infrastructure (Coverage Gap #3)
**Confidence:** 100%

Both `create_media_buy` and `create_media_buy_raw` construct `CreateMediaBuyRequest` with 8 of 14 fields. `buyer_campaign_ref` is not even a parameter. `ext` and `account_id` are absent from both signatures. `raw_request` permanently stores the truncated version.

**Files:** `src/core/tools/media_buy_create.py:3609-3618` (MCP), `3698-3707` (A2A)

### New Issues Discovered (8)

#### CRIT-4: Dead error handler in delivery — raise e before logger [NEW]

**Review:** 02-api-boundary (CRITICAL-3)
**Confidence:** 100%

```python
except Exception as e:
    raise e                    # unconditional re-raise
    logger.error(...)          # UNREACHABLE
    # Continue with other...   # UNREACHABLE
```

Any adapter error for one media buy kills the entire `get_media_buy_delivery` response. The intent was partial failure tolerance (continue with remaining buys), but `raise e` makes the handler dead code.

**File:** `src/core/tools/media_buy_delivery.py:390-394`
**Fix:** Remove `raise e`, replace with `logger.error(...); continue`

#### CRIT-5: Auth token prefix leaked in A2A error responses [NEW]

**Review:** 03-transport (Critical-1)
**Confidence:** 100%

```python
raise ServerError(InvalidRequestError(
    message=f"Invalid authentication token (not found in database). "
    f"Token: {auth_token[:20]}..., ..."    # <-- 20-char token prefix in response
))
```

A2A clients with invalid tokens receive the first 20 characters of their raw bearer token in the error response body. Violates credential-never-in-response principle.

**File:** `src/a2a_server/adcp_a2a_server.py:254-258`
**Fix:** Remove token prefix from error message. Use opaque error: "Token may be expired, revoked, or belong to a different tenant."

#### CRIT-6: reset-db-pool on public router, not debug_router [NEW]

**Review:** 03-transport (Critical-2)
**Confidence:** 95%

`/_internal/reset-db-pool` is registered on the unguarded `router`, not `debug_router`. Returns 403 in production (confirming the endpoint exists) instead of 404. Resets the DB connection pool and clears tenant context ContextVar.

**File:** `src/routes/health.py:43-79`
**Fix:** Move to `@debug_router.post("/_internal/reset-db-pool")`

#### CRIT-7: Non-unique A2A task IDs from len(tasks)+1 [NEW]

**Review:** 03-transport (Critical-3)
**Confidence:** 90%

```python
task_id = f"task_{len(self.tasks) + 1}"
```

Concurrent requests or deleted tasks cause ID collisions. Second task silently overwrites the first.

**File:** `src/a2a_server/adcp_a2a_server.py:135,547`
**Fix:** `task_id = f"task_{uuid.uuid4().hex[:16]}"`

#### CRIT-8: request.brand.domain AttributeError on dict [NEW]

**Review:** 04-adapter (CRIT-1)
**Confidence:** 95%

`CreateMediaBuyRequest.brand` is typed as `dict[str, Any] | None` in schemas.py. Two adapter call sites access `.domain` as an attribute on the dict:

```python
test_message = str(request.brand.domain)  # AttributeError: 'dict' has no attribute 'domain'
```

**Files:** `src/adapters/mock_ad_server.py:466`, `src/adapters/broadstreet/managers/workflow.py:86`
**Fix:** Use `brand.get("domain") if isinstance(brand, dict) else getattr(brand, "domain", None)`

#### CRIT-9: Tests encode wrong Creative base class as correct behavior [NEW]

**Review:** 05-test-infrastructure (Critical Issue 1)
**Confidence:** 85%

`TestCreativeVariantsBoundary` (7 tests) asserts `variants` is required and `Creative(creative_id="c1", variants=[])` is valid. This is the delivery Creative contract, not the listing Creative. When salesagent-goy2 is fixed, every assertion inverts. No comment marks these as documenting wrong behavior.

`test_list_creatives_serialization.py` asserts `name`, `status`, `created_date`, `updated_date` are *absent* from `model_dump()`. Per the spec, these are *required* listing fields that should be *present*.

**Files:** `tests/unit/test_adcp_36_schema_upgrade.py:16-93`, `tests/unit/test_list_creatives_serialization.py`

#### CRIT-10: Dead BrandManifest factory returns wrong type for 3.6 [NEW]

**Review:** 05-test-infrastructure (Critical Issue 2)
**Confidence:** 80%

`create_test_brand_manifest()` returns `BrandManifest` but `CreateMediaBuyRequest.brand` in 3.6 expects `BrandReference(domain=...)`. Factory is dead code (none of the new test files call it) but remains as a trap.

**File:** `tests/helpers/adcp_factories.py:14,497-523`
**Fix:** Remove and replace with `create_test_brand_reference(domain="test.com") -> BrandReference`

#### CRIT-11: Cross-tenant principal ID injection risk in REST API [NEW]

**Review:** 03-transport (High-6)
**Confidence:** 83%

`api_v1._resolve_auth` performs global token lookup (step 1) then tenant-scoped identity resolution (step 2). If header-detected tenant differs from token's tenant, the code patches `ResolvedIdentity` with `principal_id` from tenant A and `tenant_id` from tenant B.

**File:** `src/routes/api_v1.py:57-85`
**Fix:** Pass `tenant_id` to `get_principal_from_token` in step 1, or rely entirely on `resolve_identity()`

---

## High Issues (11)

| # | Issue | Review | File |
|---|-------|--------|------|
| H-1 | ORM CheckConstraint 2-field XOR doesn't match DB 3-field XOR | 01-schema | models.py:483-490 |
| H-2 | Creative.variants hardcoded to [] makes delivery base class meaningless | 01-schema | listing.py:367 |
| H-3 | list_creatives bare `= None` without `| None` annotation (mypy) | 02-api | listing.py:456-480 |
| H-4 | get_media_buys_raw missing `identity` param (Pattern #5 violation) | 02-api | media_buy_list.py:76,237 |
| H-5 | debug/root dumps all request headers including auth tokens | 03-transport | health.py:169-200 |
| H-6 | A2A auth middleware allows unauthenticated NL requests (500 not 401) | 03-transport | app.py:229-273 |
| H-7 | Duplicated build_order_name_context missing {auto_name} support | 04-adapter | gam/utils/naming.py:102-142 |
| H-8 | package_pricing_info docstrings use V2 field names without clarification | 04-adapter | base.py:265 |
| H-9 | _get_pricing_options always mocked — mq3n invisible to entire unit suite | 05-tests | test_delivery_behavioral.py:166-169 |
| H-10 | Multi-buy partial failure semantics untested | 05-tests | test_delivery_behavioral.py:376-479 |
| H-11 | api_v1.py double-validates token with mismatched tenant scope | 03-transport | api_v1.py:57-85 |

## Medium Issues (11)

| # | Issue | Review | File |
|---|-------|--------|------|
| M-1 | schema_helpers passes BrandReference to dict-typed field | 01-schema | schema_helpers.py:108-113 |
| M-2 | PricingOption.model_dump_internal() silently discards exclude kwarg | 01-schema | schemas.py:618-621 |
| M-3 | list_creatives_raw drops filters, include_performance, include_assignments | 02-api | listing.py:531-598 |
| M-4 | Delivery status filter ignores paused/failed DB states | 02-api | media_buy_delivery.py:601-637 |
| M-5 | get_media_buys_impl uses get_current_tenant() not identity.tenant | 02-api | media_buy_list.py:99 |
| M-6 | [DEBUG] logger.info statements left in production A2A code | 03-transport | adcp_a2a_server.py:1148,1153 |
| M-7 | AuthContext headers field is mutable dict despite frozen=True | 03-transport | auth_context.py:15-26 |
| M-8 | New workflow code uses deprecated tenant_gemini_key argument | 04-adapter | gam/managers/workflow.py:172 |
| M-9 | build_packages_summary labels all rates as "cpm" regardless of model | 04-adapter | base_workflow.py:287-293 |
| M-10 | _PatchContext patches get_db_session at source module not consumer | 05-tests | test_create_media_buy_behavioral.py:176 |
| M-11 | tox.ini coverage --fail-under=30 provides no regression signal | 05-tests | tox.ini:94 |

---

## Test Coverage Analysis: Known Bugs

### salesagent-goy2 (Creative wrong base class) — NO EFFECTIVE COVERAGE

| What exists | What it actually tests |
|-------------|----------------------|
| `test_list_creatives_serialization.py` | Asserts name, status, created_date, updated_date are **absent** from model_dump() — confirms the wrong workaround |
| `test_adcp_36_schema_upgrade.py::TestCreativeVariantsBoundary` | Asserts variants is required — confirms the wrong base class |
| `test_adcp_contract.py::test_list_creatives_response_adcp_compliance` | Only checks creative_id present, principal_id absent |

**What's missing:**
- `issubclass(Creative, list_creatives_response.Creative)` assertion
- Test that `model_dump()` **includes** name, status, created_date, updated_date
- Test that `model_dump()` **excludes** variants, variant_count, totals (delivery-only fields)
- Test that `ListCreativesResponse` schema validates against adcp list-creatives-response.json

### salesagent-mq3n (PricingOption delivery lookup) — ZERO COVERAGE

| What exists | What it actually tests |
|-------------|----------------------|
| `test_delivery_behavioral.py` (40+ tests) | ALL mock `_get_pricing_options` to return `{}` — the actual function is never called |

**What's missing:**
- Direct test of `_get_pricing_options(["cpm_usd_fixed"])` without mocking
- Integration test that creates a media buy with a PricingOption, then calls delivery and verifies pricing context is populated
- Test that pricing_option_id stored in raw_request can be resolved back to the PricingOption

### salesagent-7gnv (MediaBuy boundary drops fields) — PARTIAL COVERAGE (schema only)

| What exists | What it actually tests |
|-------------|----------------------|
| `test_adcp_contract.py::test_all_request_schemas_match_library` | Confirms buyer_campaign_ref, ext, account_id exist in the schema class |
| `test_response_shapes.py::TestCreateMediaBuyResponseShape` | Only checks media_buy_id, buyer_ref, packages — never populates buyer_campaign_ref or ext |

**What's missing:**
- Test that `create_media_buy` MCP wrapper accepts and forwards buyer_campaign_ref
- Test that `create_media_buy_raw` A2A wrapper accepts and forwards ext, account_id
- Roundtrip test: create with buyer_campaign_ref → get_media_buys → verify buyer_campaign_ref returned
- Test that raw_request stored in DB includes buyer_campaign_ref when provided

---

## New Issues Discovered by Review (not previously filed)

Beyond the 3 known bugs (goy2, mq3n, 7gnv) that were already filed as P1 beads issues, the code reviews surfaced 8 additional critical issues that should be tracked:

| Issue | Severity | Category | Suggested Action |
|-------|----------|----------|-----------------|
| Dead error handler in delivery (raise e) | CRITICAL | Bug | Fix immediately — one-line change |
| Auth token leak in A2A error response | CRITICAL | Security | Fix before merge — remove token prefix |
| reset-db-pool on public router | CRITICAL | Security | Fix before merge — move to debug_router |
| Non-unique A2A task IDs | CRITICAL | Bug | Fix before merge — use uuid |
| request.brand.domain AttributeError | CRITICAL | Bug | Fix immediately — dict guard |
| Tests encode wrong Creative contract | CRITICAL | Test quality | Add FIXME markers, plan inversion |
| Dead BrandManifest factory | HIGH | Test quality | Remove, replace with BrandReference |
| Cross-tenant principal injection risk | HIGH | Security | Fix tenant-scoped token lookup |

---

## Recommendations: Priority Order

### Before Merge (blocking)

1. **CRIT-5:** Remove auth token prefix from A2A error messages
2. **CRIT-6:** Move reset-db-pool to debug_router
3. **CRIT-4:** Remove `raise e` in delivery error handler, replace with `continue`
4. **CRIT-7:** Replace sequential task ID with uuid
5. **CRIT-8:** Fix brand.domain dict access in mock and broadstreet adapters

### Next Sprint (P1 bugs)

6. **CRIT-1 (goy2):** Rebase Creative onto correct base class or define local entity model
7. **CRIT-2 (mq3n):** Fix _get_pricing_options to resolve string IDs correctly
8. **CRIT-3 (7gnv):** Add buyer_campaign_ref, ext, account_id to wrapper signatures

### Follow-up

9. **CRIT-9:** Add FIXME(salesagent-goy2) to TestCreativeVariantsBoundary, plan test inversion
10. **CRIT-10:** Replace create_test_brand_manifest with create_test_brand_reference
11. **CRIT-11:** Fix cross-tenant principal resolution in api_v1._resolve_auth
12. **H-1:** Sync ORM CheckConstraint with actual DB 3-field XOR
13. **H-7:** Remove duplicate build_order_name_context from gam/utils/naming.py
14. **M-6:** Remove [DEBUG] logger.info statements from A2A server
