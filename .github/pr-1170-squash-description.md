## Summary

### adcp library migration (3.6.0 → 3.10.0)

- **Schema changes**: `account_id` → `AccountReference`, `PackageUpdate` variants merged, `GetSignalsRequest` no longer `RootModel`, `Signal.pricing` → `pricing_options`, `CreativeAsset.provenance.ai_tool` → `AiTool` model, `CreativeFilters.formats` → `format_ids`, creative types moved from `generated_poc.media_buy/` to `generated_poc.creative/`
- **Removed local redeclarations**: `idempotency_key`, `DeliveryStatus`, `ReportingDimensions`, `AttributionWindow` now provided natively by library
- **Internal flag isolation**: `include_snapshot`, `include_performance`, `include_sub_assets` extracted from request schemas into `_impl` parameters — buyers cannot inject them

### Account management (UC-011)

- **Production code**: `_list_accounts_impl` + `_sync_accounts_impl` with MCP/A2A/REST wrappers, `AccountRepository` (tenant-scoped), `AccountUoW`, `resolve_account()` helper with access checks and ambiguity detection
- **ORM**: `Account` + `AgentAccountAccess` models, typed JSON columns via `PydanticJSONType`, 4 Alembic migrations
- **Admin UI**: list/create/detail/edit/status blueprint with 10 BDD scenarios (integration + e2e transports)
- **Account resolution**: wired into `create_media_buy` and `sync_creatives` at the transport boundary via `enrich_identity_with_account()`

### BDD behavioral test suite

- **Infrastructure**: 27 feature files compiled from AdCP spec, multi-transport dispatch (IMPL/MCP/A2A/REST), harness environments per use case, auto-xfail for missing step definitions
- **UC-011 coverage** (93 scenarios × 4 transports): auth boundaries, agent scoping, sync upsert/idempotency, governance agents, natural key resolution, field preservation, delete_missing, dry_run, pagination, error handling
- **UC-002/UC-004/UC-005/UC-006**: account resolution error paths, delivery metrics, creative format discovery, creative sync — all dispatched through real production code
- **6 structural guards**: no-pass Then steps, no trivial assertions, no dict registries, no duplicate step bodies, no silent env checks, no direct `call_impl` bypass

### Review fixes (8 comments from @ChrisHuie)

1. Governance comparison serializes both sides before `!=` (model-vs-dict false positive)
2. `_resolve_by_natural_key` now enforces `has_access()` (security parity with ID path)
3. Broad `KeyError` auto-xfail hook removed — explicit `pytest.xfail()` per UC/marker
4. `_list_accounts_impl` checks `principal_id is None` (parity with sync)
5. 8 Then-step assertions restored to conjunctive (AND, not OR)
6. Format identity check + non-degenerate partition guard restored
7. `hasattr` guard kept as explicit `ValueError` (not silent skip)
8. `count_by_natural_key` + `get_by_natural_key` consolidated into single `list_by_natural_key(limit=2)`

### CI fixes

- `fastmcp` 3.0.2 → 3.2.0 (GHSA-m8x7-r2rg-vh5g, GHSA-rww4-4w9c-7733)
- `cryptography` bumped to 46.0.6
- Creative agent pinned to known-good commit via GitHub archive API (upstream migration breakage)
- `pg_isready -U adcp_user` on health check

### Test results (all green)

| Suite | Passed |
|-------|--------|
| Unit | 4,143 |
| Integration | 1,822 |
| BDD | 1,057 |
| E2E | 93 |
| Admin | 10 |
