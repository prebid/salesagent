# Google Ad Manager (GAM) Adapter

Connect the Prebid Sales Agent to Google Ad Manager to create and manage line items programmatically.

## Getting Started

1. **[Service Account Setup](service-account-setup.md)** - Configure authentication (start here)
2. **[Product Configuration](product-configuration.md)** - Map AdCP products to GAM line item templates
3. **[Testing Setup](testing-setup.md)** - Set up a GAM test environment

## Authentication Options

| Method | Use Case | Maintenance |
|--------|----------|-------------|
| **Service Account** (recommended) | Production | Automatic - no token refresh |
| **OAuth Refresh Token** | Development/testing | Manual - tokens expire |

For multi-tenant deployments, each tenant needs their own service account. See [GCP Provisioning](gcp-provisioning.md) for automatic service account creation.

## Supported Features

The GAM adapter supports:

- **Line Item Types**: Standard, Sponsorship, Network, House
- **Pricing Models**: CPM, vCPM, CPC, Flat Rate
- **Targeting**: Geography, device, custom key-values
- **Creatives**: Display, video (VAST), native

## Pricing Model Mapping

| AdCP Pricing | GAM Line Item Type | Notes |
|--------------|-------------------|-------|
| CPM | Standard or Sponsorship | Based on guarantees |
| vCPM | Standard only | GAM requirement |
| CPC | Standard | |
| Flat Rate | Sponsorship | Translated to CPD |

## Creative Concept Enrichment (seller-side fallback)

AdCP 3.1 exposes `concept_id` / `concept_name` on `list_creatives` responses —
concepts group related creatives across sizes and formats. Per the spec these are
a **buyer-side** grouping (Flashtalking concepts, Celtra campaign folders, CM360
creative groups), but AdCP `sync_creatives` carries **no** concept field, so there
is no authoritative producer for these keys today.

To give the `concept_ids` filter a production data source, the GAM adapter derives
a **fallback** concept when it pushes a creative to the ad server:

| Field | Source |
|-------|--------|
| `concept_id` | `gam-order-<order_id>` — the GAM Order the creative is trafficked into (GAM's closest native "creative grouping") |
| `concept_name` | `GAM Order <order_id>` |
| `concept_source` | `gam_order` (provenance marker) |

**This is enrichment, not the authoritative concept.** The `gam-order-` namespace and
the `concept_source` marker keep it distinguishable from a (future, spec-defined)
buyer-supplied concept. The enrichment is written **only when no concept is already
present** — a buyer-supplied concept always takes precedence and is never overwritten.

Notes and limitations:

- Only creatives **pushed to GAM** (i.e. trafficked into a media buy) are enriched —
  uniformly across all three push paths (auto-approval creation, manual-approval
  execution, and retroactive per-creative push). Library-only creatives that were
  synced but never assigned carry no concept until they are trafficked.
- The Order is a coarse grouping: creatives reused across orders, or unrelated
  creatives in one order, will not map cleanly. It is a best-effort fallback.

Implemented in `src/adapters/gam/managers/creatives.py` (`add_creative_assets`). Each of
the three push paths folds the result into the creative `data` blob via the shared
`_apply_creative_enrichment` helper and persists it through `CreativeRepository.update_data`
(`src/core/tools/media_buy_create.py`). Follow-up to #1407 (see #1506).

## Documentation

- [Service Account Setup](service-account-setup.md) - Authentication configuration
- [Product Configuration](product-configuration.md) - Mapping products to GAM
- [Testing Setup](testing-setup.md) - Test environment configuration
- [GCP Provisioning](gcp-provisioning.md) - Automatic service account creation
