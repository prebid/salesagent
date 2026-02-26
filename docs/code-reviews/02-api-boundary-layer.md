# Code Review: API Boundary Layer

**Branch:** `KonstantinMirin/adcp-v3-upgrade`
**Scope:** adcp 3.2.0 to 3.6.0 migration — MCP/A2A tool boundary layer
**Date:** 2026-02-26

## Summary

The migration successfully adopted adcp 3.6.0 library types and restructured several files. The shared `_impl()` pattern is consistently applied. However, three confirmed bugs from the known-bugs list are verified present in the code, and two additional high-confidence issues were found. The most dangerous issue is an unreachable error-handling `logger.error` call that is dead code sitting after a bare `raise e`, which means all per-media-buy errors in `get_media_buy_delivery` propagate as uncaught exceptions instead of being logged and skipped.

## Files Reviewed

1. `src/core/tools/media_buy_create.py`
2. `src/core/tools/media_buy_delivery.py`
3. `src/core/tools/media_buy_list.py`
4. `src/core/tools/media_buy_update.py`
5. `src/core/tools/products.py`
6. `src/core/tools/creatives/listing.py`
7. `src/core/tools/creatives/sync_wrappers.py`
8. `src/core/tools/creatives/_sync.py`
9. `src/core/tools/capabilities.py`
10. `src/core/tools/properties.py`
11. `src/core/tools/signals.py`
12. `src/core/tools/performance.py`

## Critical Issues

### [CRITICAL-1]: `buyer_campaign_ref`, `ext`, and `account_id` dropped at both MCP and A2A boundaries

**File:** `src/core/tools/media_buy_create.py:3609-3618` (MCP wrapper), `src/core/tools/media_buy_create.py:3698-3707` (A2A raw wrapper)
**Bug ID:** salesagent-7gnv (confirmed)

**Issue:** Both the MCP `create_media_buy` and A2A `create_media_buy_raw` wrapper functions accept a large parameter set, but construct `CreateMediaBuyRequest` with only 8 fields:

```python
req = CreateMediaBuyRequest(
    buyer_ref=buyer_ref,
    brand=brand,
    packages=packages,
    start_time=start_time,
    end_time=end_time,
    po_number=po_number,
    reporting_webhook=reporting_webhook,
    context=context,
    # MISSING: ext, buyer_campaign_ref, account_id
)
```

`buyer_campaign_ref` is not even a parameter of either wrapper function — it cannot be passed at all. `ext` and `account_id` are also absent from both the function signatures and the constructor call. Since `raw_request` is stored from `req.model_dump(mode="json", by_alias=True)` at line 1912, the permanently-truncated version is saved to the database. The `get_media_buys` response at line 174 correctly reads `buyer_campaign_ref` back from `raw_request`, so it will always return `null`.

**Impact:** Any buyer passing `buyer_campaign_ref` gets a 200 response but the value is permanently lost. The truncated `raw_request` is the only copy — the data is unrecoverable after creation.

**Fix:** Add `ext: dict | None = None`, `buyer_campaign_ref: str | None = None`, and `account_id: str | None = None` to both wrapper signatures. Pass them into the `CreateMediaBuyRequest` constructor:

```python
req = CreateMediaBuyRequest(
    buyer_ref=buyer_ref,
    brand=brand,
    packages=packages,
    start_time=start_time,
    end_time=end_time,
    po_number=po_number,
    reporting_webhook=reporting_webhook,
    context=context,
    ext=ext,
    buyer_campaign_ref=buyer_campaign_ref,
    account_id=account_id,
)
```

### [CRITICAL-2]: `_get_pricing_options` queries integer PK with synthetic string IDs — always returns empty

**File:** `src/core/tools/media_buy_delivery.py:645-649`
**Bug ID:** salesagent-mq3n (confirmed)

**Issue:** The `_get_pricing_options` function:

```python
def _get_pricing_options(pricing_option_ids: list[Any | None]) -> dict[str, PricingOption]:
    with get_db_session() as session:
        statement = select(PricingOption).where(PricingOption.id.in_(pricing_option_ids))
        pricing_options = session.scalars(statement).all()
        return {str(pricing_option.id): pricing_option for pricing_option in pricing_options}
```

is called with IDs extracted from `raw_request.pricing_option_id` (lines 160-166). These are the **synthetic string composite keys** written at media buy creation time by `_validate_pricing_model_selection` (line 1003 of `media_buy_create.py`):

```python
option_id = f"{opt_inner.pricing_model}_{opt_inner.currency.lower()}_{fixed_str}"
# produces e.g. "cpm_usd_fixed"
```

`PricingOption.id` is an integer PK. PostgreSQL receives `WHERE id IN ('cpm_usd_fixed', ...)` and returns 0 rows. The result dict is always empty, `pricing_option` is always `None` at line 311, and the CPC click calculation at lines 324-326 is never reached — all packages always return `package_clicks = None`.

**Impact:** CPC click calculations are permanently broken. All packages report `clicks: null` regardless of actual CPC pricing configuration.

**Fix:** At creation time in `media_buy_create.py`, store the actual integer `PricingOption.id` into `raw_request`. Update `_get_pricing_options` to query by that integer PK. Alternatively, restructure the lookup to query by the composite `(pricing_model, currency, is_fixed)` columns if those columns exist on `PricingOption`.

### [CRITICAL-3]: Dead code after bare `raise e` makes per-media-buy error handling unreachable

**File:** `src/core/tools/media_buy_delivery.py:390-394`

**Issue:**

```python
except Exception as e:
    raise e                           # line 391 — unconditional re-raise
    logger.error(f"Error getting delivery for {media_buy_id}: {e}")  # UNREACHABLE
    # TODO: @yusuf - Ask should we attach an error message...         # UNREACHABLE
    # Continue with other media buys                                   # UNREACHABLE
```

The `raise e` at line 391 propagates the exception immediately. The `logger.error` and the comment "Continue with other media buys" are dead code. Any adapter exception for a single media buy kills the entire `get_media_buy_delivery` response, returning nothing to the caller. This directly contradicts the function's defensive design — it returns partial error response objects for every other failure mode.

**Impact:** Any transient adapter error for one media buy in a multi-media-buy request fails the entire request, returning no data for any buy.

**Fix:**

```python
except Exception as e:
    logger.error(f"Error getting delivery for {media_buy_id}: {e}", exc_info=True)
    # Continue processing other media buys — return partial results
    continue
```

## High Issues

### [HIGH-1]: `list_creatives` MCP wrapper uses bare `= None` without `| None` annotation

**File:** `src/core/tools/creatives/listing.py:456-480`

**Issue:** Multiple parameters in the `list_creatives` async wrapper use `str = None` / `list[str] = None` instead of `str | None = None` / `list[str] | None = None`:

```python
async def list_creatives(
    media_buy_id: str = None,        # should be str | None = None
    media_buy_ids: list[str] = None,  # should be list[str] | None = None
    buyer_ref: str = None,
    buyer_refs: list[str] = None,
    status: str = None,
    format: str = None,
    tags: list[str] = None,
    created_after: str = None,
    created_before: str = None,
    search: str = None,
    ...
```

This violates the project convention in CLAUDE.md ("Use `| None` instead of `Optional[]` (Python 3.10+)") and will produce mypy errors, causing `make quality` to fail.

**Impact:** `make quality` will fail on mypy checks for this file, blocking CI.

**Fix:** Change all bare `= None` parameters in the function signature to `str | None = None`, `list[str] | None = None`, etc.

### [HIGH-2]: `get_media_buys_raw` and `_get_media_buys_impl` do not accept `identity` — inconsistent with Critical Pattern #5

**File:** `src/core/tools/media_buy_list.py:76` (`_get_media_buys_impl`), `src/core/tools/media_buy_list.py:237` (`get_media_buys_raw`)

**Issue:** Every other `_raw` function in the tool layer accepts `identity: ResolvedIdentity | None = None`:
- `get_media_buy_delivery_raw` at line 489 of `media_buy_delivery.py`
- `list_creatives_raw` at line 531 of `listing.py`
- `create_media_buy_raw` at line 3662 of `media_buy_create.py`

`get_media_buys_raw` does not:

```python
def get_media_buys_raw(
    media_buy_ids: list[str] | None = None,
    ...
    ctx: Context | ToolContext | None = None,
    # MISSING: identity: ResolvedIdentity | None = None
):
```

`_get_media_buys_impl` takes `ctx` directly and reads principal from `get_principal_id_from_context(ctx)` and tenant from `get_current_tenant()`. This is incompatible with the A2A transport pattern where identity is pre-resolved and passed directly. Violates CLAUDE.md Critical Pattern #5.

**Impact:** Pre-resolved identity passed by the A2A transport layer is dropped. The function re-resolves identity from `ctx`, which may behave differently. Multi-tenant scenarios where identity is passed explicitly are not supported.

**Fix:** Add `identity: ResolvedIdentity | None = None` to both `get_media_buys_raw` and `_get_media_buys_impl`. Refactor `_get_media_buys_impl` to take `identity: ResolvedIdentity | None` instead of `ctx`, consistent with all other `_impl` functions.

## Medium Issues

### [MEDIUM-1]: `list_creatives_raw` silently drops `filters`, `include_performance`, `include_assignments`, `include_sub_assets`

**File:** `src/core/tools/creatives/listing.py:531-598`

**Issue:** The `list_creatives_raw` function signature omits `filters`, `include_performance`, `include_assignments`, and `include_sub_assets`. When calling `_list_creatives_impl`, these arguments use default values (`None`, `False`, `False`, `False`) regardless of what the A2A caller may need:

```python
def list_creatives_raw(...):  # no filters, include_performance, etc.
    return _list_creatives_impl(
        ...
        # filters=None (implicit)
        # include_performance=False (implicit)
        # include_assignments=False (implicit)
        # include_sub_assets=False (implicit)
        ...
    )
```

Compare `list_creatives` (MCP wrapper) which accepts `filters: CreativeFilters | None = None` and passes it through.

**Impact:** A2A callers cannot use advanced filtering or request performance/assignment data.

**Fix:** Add `filters`, `include_performance`, `include_assignments`, `include_sub_assets` parameters to `list_creatives_raw` and forward them to `_list_creatives_impl`.

### [MEDIUM-2]: Status filtering in delivery ignores `paused` and `failed` database states

**File:** `src/core/tools/media_buy_delivery.py:601-637`

**Issue:** The status computation inside `_get_target_media_buys` (lines 601-637) determines status solely from date arithmetic:
- `reference_date < start_compare` -> "ready"
- `reference_date > end_compare` -> "completed"
- else -> "active"

The internal statuses `"paused"` and `"failed"` exist in `valid_internal_statuses` at line 555 and are filterable, but they are never produced by the date-comparison logic. Any media buy that is actually paused or failed in the database will always be reported as "active" (or "ready"/"completed") based on its dates alone.

**Impact:** A buyer requesting `status_filter=["paused"]` gets an empty response even if they have paused media buys. A buyer requesting `status_filter=["active"]` receives paused and failed media buys.

**Fix:** Read the actual `MediaBuy.status` column from the database and use it alongside date-based computation. If `MediaBuy.status == "paused"`, override the date-based status.

### [MEDIUM-3]: `_get_media_buys_impl` uses `get_current_tenant()` instead of identity-bound tenant

**File:** `src/core/tools/media_buy_list.py:99`

**Issue:**

```python
tenant = get_current_tenant()  # reads module-level shared config state
```

All other `_impl` functions use `identity.tenant`, which was resolved at the transport boundary and is guaranteed correct for the authenticated request. `get_current_tenant()` reads from a shared config state that may be stale or wrong in concurrent multi-tenant deployments.

**Impact:** Multi-tenant correctness is fragile. If `get_current_tenant()` returns a different tenant than the one the authenticated principal belongs to (race condition in multi-tenant deployments), the wrong tenant's media buys are returned.

**Fix:** Replace `get_current_tenant()` with `identity.tenant` (requires HIGH-2 to be done first so that `identity` is available).

## Low Issues

### [LOW-1]: Legacy conversion shim parameters accepted but never converted

**File:** `src/core/tools/media_buy_create.py:3557-3568` (MCP), `src/core/tools/media_buy_create.py:3647-3658` (A2A raw)

**Issue:** `product_ids`, `start_date`, `end_date`, `total_budget`, `targeting_overlay`, `pacing`, `daily_budget` are accepted by both wrappers but are never converted into `packages` or forwarded to `CreateMediaBuyRequest`. A legacy caller using `product_ids` and `total_budget` will get a Pydantic validation error from `CreateMediaBuyRequest` (missing `packages`) rather than the legacy fallback. The docstrings say "Legacy format conversion" but no conversion occurs.

**Impact:** Legacy callers cannot use these fields despite the function accepting them without error.

**Fix:** Either implement the legacy conversion logic (convert `product_ids` + `total_budget` into `packages`) or remove the misleading parameters from the function signatures with a deprecation notice.

### [LOW-2]: `sort_applied` uses `.value` access that will fail if adcp 3.6 changed Sort fields to plain strings

**File:** `src/core/tools/creatives/listing.py:402-404`

**Issue:**

```python
sort_applied = None
if req.sort and req.sort.field and req.sort.direction:
    sort_applied = {"field": req.sort.field.value, "direction": req.sort.direction.value}
```

The `Sort` model uses `FieldModel` (an enum) and `Sort.direction` (also an enum). Calling `.value` on these is correct for enums, but if adcp 3.6.0 changed these from enums to plain strings (a common library migration pattern), this code would raise `AttributeError: 'str' object has no attribute 'value'`.

**Impact:** Runtime crash if Sort fields are plain strings in adcp 3.6.0.

**Fix:** Guard with `getattr(req.sort.field, "value", req.sort.field)`.

## Data Flow Analysis

### `create_media_buy` — Request in -> Processing -> Response out

| Stage | What happens |
|---|---|
| MCP/A2A boundary | `CreateMediaBuyRequest` constructed with 8 fields. `ext`, `buyer_campaign_ref`, `account_id` permanently dropped. |
| `_create_media_buy_impl` | Receives truncated request. Budget, date, product, pricing validation correct. |
| `raw_request` storage | `req.model_dump(mode="json", by_alias=True)` at line 1912 — truncated version stored permanently in DB. |
| Response | `CreateMediaBuySuccess` with `media_buy_id`, `packages`. Structurally correct. |

**Field drops confirmed:** `ext`, `buyer_campaign_ref`, `account_id`.

### `get_media_buy_delivery` — Request in -> Processing -> Response out

| Stage | What happens |
|---|---|
| Wrapper | Correct pass-through of all parameters. |
| `_get_target_media_buys` | Status computed from dates only. Paused/failed buys misclassified as "active". |
| `_get_pricing_options` | Always returns empty dict (string IDs vs integer PK). |
| Per-buy delivery loop | Exception re-raises via bare `raise e`. Error handling dead code. |
| Response | `GetMediaBuyDeliveryResponse` correct structure. CPC `clicks` always `None` due to CRITICAL-2. |

### `get_media_buys` (NEW file) — Request in -> Processing -> Response out

| Stage | What happens |
|---|---|
| Wrapper | Correct. `account_id` explicitly rejected with `ToolError` (not silently dropped). |
| `_get_media_buys_impl` | `tenant` from `get_current_tenant()` not `identity.tenant`. No `identity` parameter. |
| Response | `GetMediaBuysMediaBuy` includes `buyer_campaign_ref` correctly read from `raw_request` at line 174. |

**Note:** `buyer_campaign_ref` reads correctly from `raw_request` in `get_media_buys`, but because `create_media_buy` never stores it (CRITICAL-1), this field always returns `null`.

### `list_creatives` — Request in -> Processing -> Response out

| Stage | What happens |
|---|---|
| MCP wrapper | Full parameter pass-through. |
| A2A raw wrapper | Missing `filters`, `include_performance`, `include_assignments`, `include_sub_assets`. |
| `_list_creatives_impl` | All four previously-missing required fields now present: `name` (line 360), `status` (line 369), `created_date` (line 370), `updated_date` (line 371). `variants=[]` added for adcp 3.6.0 (line 367). |
| Response | `ListCreativesResponse`. **salesagent-goy2 appears resolved in this branch** — all four required schema fields are present. |

### `update_media_buy` — Request in -> Processing -> Response out

| Stage | What happens |
|---|---|
| Wrapper | Correct identity-based pattern. `ResolvedIdentity` passed through. |
| `_update_media_buy_impl` | Proper `identity` parameter. Resolves `media_buy_id` from `buyer_ref` if needed. Correct dual-path for manual approval vs auto-apply. |
| Response | `UpdateMediaBuySuccess` or `UpdateMediaBuyError`. No field drops identified. |

### `capabilities` — Request in -> Processing -> Response out

| Stage | What happens |
|---|---|
| Both wrappers | Correct pass-through. |
| `_get_adcp_capabilities_impl` | `MajorVersion(root=3)` correctly signals adcp v3. `MediaBuyFeatures` accurately reports capabilities (`content_standards=False`, `inline_creative_management=True`, `property_list_filtering=False`). |
| Response | `GetAdcpCapabilitiesResponse`. No schema issues. |

### `products` — Request in -> Processing -> Response out

| Stage | What happens |
|---|---|
| Both wrappers | Async `_get_products_impl` called with `GetProductsRequestGenerated` and `identity`. |
| `_get_products_impl` | Reads products from database, applies policy checks, converts via `convert_product_model_to_schema`. |
| Response | `GetProductsResponse`. No boundary-level issues identified. |

### `signals` — Request in -> Processing -> Response out

| Stage | What happens |
|---|---|
| Both wrappers | Standard pass-through pattern. |
| `_get_signals_impl` | Returns mock/sample signals. No database interactions. |
| Response | `GetSignalsResponse`. No schema issues (mock data). |

### `properties` — Request in -> Processing -> Response out

| Stage | What happens |
|---|---|
| Both wrappers | Standard pass-through pattern. |
| `_list_authorized_properties_impl` | Queries `PublisherPartner` by tenant. Returns domains and policy info. |
| Response | `ListAuthorizedPropertiesResponse`. No boundary-level issues identified. |

### `performance` — Request in -> Processing -> Response out

| Stage | What happens |
|---|---|
| Both wrappers | Standard pass-through pattern. |
| `_update_performance_index_impl` | Converts `ProductPerformance` to `PackagePerformance` for adapter. |
| Response | `UpdatePerformanceIndexResponse`. No field drops identified. |

### `sync_creatives` — Request in -> Processing -> Response out

| Stage | What happens |
|---|---|
| MCP wrapper | Correct pass-through. Converts `ValidationMode` enum to string. |
| A2A raw wrapper | Correct pass-through. |
| `_sync_creatives_impl` | Processes creatives with upsert semantics. |
| Response | `SyncCreativesResponse`. No boundary-level issues identified. |

## Recommendations

1. **Fix CRITICAL-1 first** — add `ext`, `buyer_campaign_ref`, `account_id` to both `create_media_buy` and `create_media_buy_raw` signatures and forward into `CreateMediaBuyRequest`. Run `pytest tests/unit/test_adcp_contract.py` to verify schema compliance.

2. **Fix CRITICAL-2** — at media buy creation time in `_create_media_buy_impl`, store the actual integer `PricingOption.id` (not the synthetic string) in `raw_request`. Update `_get_pricing_options` to query by that integer PK.

3. **Fix CRITICAL-3** — remove the bare `raise e` at line 391 of `media_buy_delivery.py` and replace with `logger.error(..., exc_info=True); continue` to enable the graceful degradation the comment describes.

4. **Fix HIGH-2 + MEDIUM-3 together** — refactor `get_media_buys_raw` and `_get_media_buys_impl` to accept `identity: ResolvedIdentity | None` and use `identity.tenant` instead of `get_current_tenant()`.

5. **Fix HIGH-1** — annotate all bare `= None` parameters in `list_creatives` wrapper as `| None = None`.

6. **After CRITICAL-1 and CRITICAL-2**, run `tox -e integration` — both involve database interactions (raw_request storage and PricingOption lookup) that unit tests cannot fully exercise.
