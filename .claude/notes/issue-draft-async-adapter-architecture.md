# GitHub Issue Draft — DO NOT POST WITHOUT REVIEW

**Title:** Architecture Proposal: Decouple Adapter Execution from Request Handling

**Labels:** dev topic

---

## Observation

The database schema is adapter-agnostic. The request-handling tools work entirely from Postgres. Yet one code path instantiates an adapter inside the HTTP request cycle.

### The schema separation is already clean

**Core tables** (adapter-agnostic):
- `media_buys` — budget, dates, status, raw_request. No GAM/Broadstreet columns.
- `media_packages` — package_config, budget, bid_price, pacing. No platform IDs.
- `products` — name, pricing_options, format_ids, targeting_template. Generic.
- `principals` — with `platform_mappings` JSON for per-adapter advertiser IDs (lookup only).
- `creatives`, `creative_assignments` — generic creative data.

**Adapter-specific tables** (isolated, used only by background sync):
- `gam_orders`, `gam_line_items` — populated by background sync jobs for GAM reporting. Not referenced by core tables.
- `adapter_configs` — tenant-level credentials (which adapter, what API keys).
- `sync_jobs` — tracks background sync operations.

No `platform_line_item_id` in the main schema. A media buy exists independently of any ad server.

### Request-handling tools confirm it

| Tool | Data source | Adapter needed? |
|------|-------------|-----------------|
| `get_products` | `products` table query | No |
| `list_authorized_properties` | `publisher_partners` table query | No |
| `list_creative_formats` | Creative agent registry | No |
| `create_media_buy` (human-in-the-loop) | Store to `media_buys` + `workflow_steps`, return | No |
| `create_media_buy` (auto-create) | Calls `adapter.create_media_buy()` synchronously | **Yes — this is the one violation** |

Evidence:
- `src/core/tools/products.py:323-335` — pure database query
- `src/core/tools/properties.py:80-87` — pure database query
- `src/core/tools/media_buy_create.py:1851` — human-in-the-loop stores to DB, returns immediately
- `src/core/tools/media_buy_create.py:2741` — auto-create calls adapter synchronously

### Two paths in create_media_buy

**Path A — Human-in-the-loop (`manual_approval_required=True`):**
```
create_media_buy()
  → Store MediaBuy with status="pending_approval"
  → Create WorkflowStep with status="requires_approval"
  → Send Slack notification
  → Return immediately                                    ← No adapter call

  ... human approves via Admin UI ...

  → execute_approved_media_buy()                           ← Adapter runs in background
```
This is correct. The adapter is a fulfillment channel invoked after approval. The request layer works purely from Postgres.

**Path B — Auto-create (`manual_approval_required=False`):**
```
create_media_buy()
  → _execute_adapter_media_buy_creation()                  ← BLOCKS on adapter
    → GAM: create_order() — 60s timeout
    → GAM: create_line_items() — 300s timeout (can be hundreds of items)
    → GAM: approve_order() — 1 attempt, then background if NO_FORECAST_YET
  ← Return to client after creation completes
```
This violates the separation. The adapter — an output channel — runs inside the request cycle.

### Background infrastructure already exists

| Service | Purpose | Status |
|---------|---------|--------|
| `order_approval_service.py` | Background GAM approval polling (SyncJob-based) | Active — used by GAM adapter |
| `background_approval_service.py` | Background approval polling (WorkflowStep-based) | Active |
| `background_sync_service.py` | Creative/inventory sync from ad servers | Active |
| `execute_approved_media_buy()` | Run adapter from stored request after approval | Active — used by Path A |
| `WorkflowStep` table | Persistent state tracking | Active |

The pattern for "store order, execute adapter later" already exists in Path A and in the background approval services.

## Proposal

Make Path B work like Path A: store the order, return immediately, execute the adapter as a background job.

```
create_media_buy() — both paths:
  → Validate request against Postgres (products, budgets, availability)
  → Store MediaBuy + MediaPackages
  → If manual approval: WorkflowStep status="requires_approval", await human
  → If auto-create: WorkflowStep status="pending", enqueue background execution
  → Return immediately with media_buy_id + status

Background worker:
  → execute_approved_media_buy(media_buy_id)    # Same function Path A uses
  → adapter.create_media_buy()                  # Sync is fine in a worker
  → Update MediaBuy status
  → Notify buyer (webhook / status polling)
```

At the time of placing the order, it does not matter which adapter will fulfill it. What matters is that the order is recorded with complete data and that the Postgres state (products, availability, pricing) is synchronized with the ad server. This synchronization is a separate concern handled by background sync jobs.

### Side effects

Decoupling adapters from request handling also resolves:

- **`run_async_in_sync_context()` bridge** (`src/core/validation_helpers.py:25`) — exists because async AI naming is called from sync adapter code inside the async event loop. With adapters in background workers, the async handler can `await` directly, and the sync worker can call `asyncio.run()` without conflict.

- **Unawaited coroutine bugs** (`salesagent-3laa`, `salesagent-5shl`) — symptoms of mixed async/sync execution contexts. Clean separation eliminates this category.

- **Single-adapter-per-tenant constraint** — with adapters as background fulfillment channels, an order could fan out to multiple adapters as parallel tasks.

## Priority

**Low.** Path A (human-in-the-loop) is already correct and likely the common production path. The auto-create path works at current scale. This becomes important when:
- Concurrent auto-create requests cause event loop thread starvation
- Sellers need multi-adapter support
- Response latency SLAs tighten

## Open questions

- Should the background worker be Celery, the existing threading model, or something else? The existing `order_approval_service` uses daemon threads — works but doesn't survive process restarts.

- The two approval services (`order_approval_service.py` with SyncJob tracking, `background_approval_service.py` with WorkflowStep tracking) serve similar purposes with different tracking. Should they be consolidated?

- Should `execute_approved_media_buy()` be the single entry point for all adapter execution (both paths), or should auto-create have a simpler fast path?

## Related

- #1050 — FastAPI as unified application framework (transport layer; this addresses execution model)
- `src/core/validation_helpers.py:25` — `run_async_in_sync_context()` bridge hack
- `salesagent-3laa`, `salesagent-5shl` — unawaited coroutine bugs (symptoms of mixed contexts)
