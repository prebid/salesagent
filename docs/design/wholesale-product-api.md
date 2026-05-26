# Wholesale Product API

**Status:** Design note
**Last updated:** 2026-05-26

For the implemented end-to-end API guide, use
[Embedded Wholesale Products API](../integration/embedded-wholesale-products-api.md).
This document explains the model and tradeoffs; the integration guide and
generated Tenant Management OpenAPI spec are authoritative for exact request
and response shapes.

## Summary

Wholesale products are the durable sellable primitive that an embedding
storefront uses to manage publisher supply. They answer five questions in one
object:

1. **Where can this run?** Publisher properties plus ad-server inventory selectors.
2. **What creative can a buyer send?** Accepted creative format IDs and optional slot requirements.
3. **What does it cost?** Pricing options, delivery type, and forecast.
4. **What composition is allowed?** Targeting and optimization capabilities.
5. **How does the adapter execute it?** Adapter-specific selector and format-binding configuration.

An inventory component can still exist internally as a reusable component, but it
should not be the primary public API noun. The external API should expose
**wholesale products** because that is the object the storefront is building:
buyer-facing merchandising plus the supply, pricing, targeting, optimization,
and adapter execution details needed to materialize it.

The existing database models are `Product` and `InventoryProfile`. The first
implementation can persist the inventory portion in `InventoryProfile` while
using `Product` for the buyer-facing product, pricing, delivery, and forecast
fields. That split should remain an implementation detail of the migration.

## Core Model

```json
{
  "wholesale_product_id": "homepage_takeover",
  "name": "Homepage Takeover",
  "description": "High-impact homepage package.",
  "status": "active",
  "delivery_type": "guaranteed",

  "pricing_options": [
    {
      "pricing_model": "cpm",
      "currency": "USD",
      "is_fixed": true,
      "rate": 40
    }
  ],

  "forecast": {
    "impressions": 1000000
  },

  "inventory": {
    "publisher_properties": [
      {
        "publisher_domain": "example.com",
        "selection_type": "all"
      }
    ],

    "creative_formats": [
      {
        "format_id": {
          "agent_url": "https://creative.adcontextprotocol.org",
          "id": "homepage_takeover"
        },
        "slot_requirements": [
          {
            "slot_id": "leaderboard",
            "name": "Leaderboard",
            "asset_type": "image",
            "width": 970,
            "height": 250,
            "required": true
          },
          {
            "slot_id": "rail",
            "name": "Right rail",
            "asset_type": "image",
            "width": 300,
            "height": 600,
            "required": true
          }
        ]
      }
    ],

    "execution": {
      "adapter": "google_ad_manager",
      "selectors": [
        {
          "selector_type": "placement",
          "external_id": "123456"
        },
        {
          "selector_type": "ad_unit",
          "external_id": "654321",
          "options": {
            "include_descendants": true
          }
        }
      ],
      "format_bindings": [
        {
          "format_id": {
            "agent_url": "https://creative.adcontextprotocol.org",
            "id": "homepage_takeover"
          },
          "adapter_config": {
            "creative_placeholders": [
              {"slot_id": "leaderboard", "size": "970x250"},
              {"slot_id": "rail", "size": "300x600"}
            ],
            "roadblocking": "as_many_as_possible"
          }
        }
      ]
    }
  },

  "targeting_capabilities": {
    "allowed_dimensions": ["geo", "device", "audience"],
    "blocked_dimensions": ["postal_code"],
    "required_dimensions": []
  },

  "optimization_capabilities": {
    "allowed_goals": ["impressions", "viewability"],
    "supported_pacing": ["even", "asap"]
  },

  "created_at": "2026-05-26T00:00:00Z",
  "updated_at": "2026-05-26T00:00:00Z",
  "etag": "..."
}
```

### Field Ownership

| Field | Owner | Visible in `get_products` | Notes |
|---|---|---:|---|
| `wholesale_product_id`, `name`, `description` | Wholesale product | Yes | Buyer-facing merchandising text. |
| `delivery_type`, `pricing_options`, `forecast` | Wholesale product | Yes | Commercial offer and availability. |
| `inventory.publisher_properties` | Wholesale product inventory | Yes | AdCP publisher-property selector shape. |
| `inventory.creative_formats` | Wholesale product inventory | Yes as `format_ids`; slot details when schema allows | Buyer knows what creative to submit. |
| `targeting_capabilities` | Wholesale product | Yes | Narrows what buyer/storefront can compose. |
| `optimization_capabilities` | Wholesale product | Yes | Narrows package optimization and pacing choices. |
| `inventory.execution` | Wholesale product inventory | No | Internal/storefront-authoring only; not buyer-facing. |

## Storefront API Surface

The embedding storefront needs two classes of API:

1. **Authoring APIs** to create and manage wholesale products.
2. **Discovery APIs** to know what an adapter supports, search synced inventory,
   list publisher properties, and choose creative formats.

All endpoints use the same API-key auth as the existing composition API.

### Embedded Adapter Setup Boundary

Embedded setup should be candidate-driven, not per-adapter-OpenAPI-driven.
Generated OpenAPI is still useful for client typing, but it should describe a
small generic setup contract rather than requiring the storefront to learn a
different write model for every ad server.

The issue-600 adapter-specific OpenAPI direction has been removed. It was brand
new, had no downstream consumers, and split setup across one speculative
contract per ad server. The durable API surface is:

- `GET /api/v1/tenant-management/adapters`
- `GET /api/v1/tenant-management/adapters/{adapter_type}/capabilities`
- `GET /api/v1/tenant-management/adapters/{adapter_type}/config-schema`
- generic wholesale product / inventory profile authoring APIs
- generic selector, publisher-property, creative-format, signal, and
  preview/validation APIs as they are promoted from setup internals

Responsibility split:

| Responsibility | Owner | API family |
|---|---|---|
| Tenant lifecycle | Host platform | Tenant Management API |
| Adapter credentials and network binding | Host platform | Tenant Management API |
| Inventory sync trigger/status | Host platform or setup UI | Generic inventory setup API |
| Publisher domains/properties | Publisher or host setup UI | Generic publisher-property API |
| Wholesale products | Publisher or host setup UI | Generic wholesale-product API |
| Signal mappings | Publisher or host setup UI | Generic signal setup API |

The embedded setup UI needs to guide a publisher through adapter-specific
objects without exposing raw adapter implementation as the public contract. The
API should therefore expose:

- **candidate lists**: normalized objects the user can map
- **mapping writes**: generic create/update/delete backed by internal models
- **operator reads**: round-trip the adapter-facing config that the setup UI
  authored
- **preview/validation**: show buyer-facing projection plus native adapter
  consequences before publishing

This is the layer missing from issue 600: the primitives already exist
(`Product`, `InventoryProfile`, `TenantSignal`), but the
candidate/preview/operator-read setup contract is incomplete.

### Inventory Sync

```http
POST /api/v1/tenant-management/tenants/{tenant_id}/refresh
GET  /api/v1/tenant-management/tenants/{tenant_id}/status
```

The POST requests a refresh across the tenant's sync streams. The GET returns
the latest tenant status, including the `syncs` block:

```json
{
  "adapter": "google_ad_manager",
  "status": "succeeded",
  "started_at": "2026-05-26T00:00:00Z",
  "finished_at": "2026-05-26T00:00:20Z",
  "counts": {
    "ad_unit": 1200,
    "placement": 85
  },
  "scope_pending": false,
  "errors": {}
}
```

Adapter-specific setup pages can keep their existing endpoints, but the
embedding storefront needs this generic refresh/status surface so it does not
need to special-case every adapter.

### Adapter Capability Discovery

```http
GET /api/v1/tenant-management/tenants/{tenant_id}/inventory/adapter-capabilities
```

Returns the adapter-specific selector vocabulary, searchable fields, execution
binding requirements, and broad composition capabilities.

```json
{
  "adapter": "google_ad_manager",
  "label": "Google Ad Manager",
  "selector_types": [
    {
      "selector_type": "ad_unit",
      "label": "Ad unit",
      "supports_hierarchy": true,
      "supports_include_descendants": true,
      "search_fields": ["external_id", "name", "path"],
      "metadata_fields": ["path", "status", "sizes"]
    },
    {
      "selector_type": "placement",
      "label": "Placement",
      "supports_hierarchy": false,
      "supports_include_descendants": false,
      "search_fields": ["external_id", "name"],
      "metadata_fields": ["ad_unit_ids", "status"]
    }
  ],
  "creative_binding_schema": {
    "requires_format_bindings": false,
    "binding_modes": ["size_placeholder", "creative_template", "roadblock"]
  },
  "targeting_capabilities": {
    "dimensions": ["geo", "device", "audience", "custom"],
    "signal_backed_dimensions": ["audience", "custom"]
  },
  "optimization_capabilities": {
    "allowed_goals": ["impressions", "clicks", "viewability"],
    "supported_pacing": ["even", "asap"]
  },
  "pricing_capabilities": {
    "pricing_models": ["cpm", "vcpm", "cpc", "flat_rate"]
  },
  "sync": {
    "has_synced_inventory": true,
    "last_synced_at": "2026-05-26T00:00:00Z"
  }
}
```

### Inventory Selector Search

```http
GET /api/v1/tenant-management/tenants/{tenant_id}/inventory/selectors
```

Query parameters:

| Param | Meaning |
|---|---|
| `selector_type` | Optional. Adapter selector type such as `ad_unit`, `placement`, `zone`, `supply_tag`. |
| `q` | Optional search string. |
| `parent_id` | Optional hierarchy parent. |
| `format_id` | Optional filter for selectors known to support a format. |
| `limit` | Page size, default 50. |
| `cursor` | Opaque pagination cursor. |

Response:

```json
{
  "selectors": [
    {
      "selector_type": "ad_unit",
      "external_id": "654321",
      "name": "Homepage",
      "path": ["Root", "Homepage"],
      "parent_id": "111",
      "metadata": {
        "status": "active",
        "sizes": ["970x250", "300x250"]
      },
      "supported_format_ids": [
        {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_970x250"}
      ]
    }
  ],
  "next_cursor": null
}
```

This endpoint reads only from synced local caches. It must not call the ad
server live during storefront autocomplete.

### Publisher Property Discovery

```http
GET  /api/v1/tenant-management/tenants/{tenant_id}/inventory/publisher-properties
POST /api/v1/tenant-management/tenants/{tenant_id}/inventory/publisher-properties:lookup
```

Returns the publisher domains and property selectors that can be used in a
wholesale product's inventory. This is deliberately separate from ad-server selectors: publisher
properties are the AdCP supply identity that buyers see, while ad-server
selectors are execution details.

```json
{
  "publisher_domains": [
    {
      "publisher_domain": "example.com",
      "display_name": "Example Media",
      "verification_status": "verified",
      "source": "manual"
    }
  ],
  "properties": [
    {
      "publisher_domain": "example.com",
      "property_id": "site_123",
      "name": "Example Homepage",
      "property_type": "website",
      "tags": ["all_inventory", "homepage"]
    }
  ],
  "allowed_selectors": [
    {
      "publisher_domain": "example.com",
      "selection_type": "all"
    },
    {
      "publisher_domain": "example.com",
      "selection_type": "by_tag",
      "property_tags": ["all_inventory", "homepage"]
    },
    {
      "publisher_domain": "example.com",
      "selection_type": "by_id",
      "property_ids": ["site_123"]
    }
  ]
}
```

This endpoint should use tenant-scoped `PublisherPartner` and
`AuthorizedProperty` data. It must not infer publisher domains from the agent
URL on embedded/shared-agent deployments.

### Creative Format Discovery

```http
GET /api/v1/tenant-management/tenants/{tenant_id}/creative-formats
```

Returns creative formats available for wholesale product authoring. Internally this should
share implementation with the AdCP `list_creative_formats` tool so the
storefront and buyers see the same format vocabulary.

```json
{
  "formats": [
    {
      "format_id": {
        "agent_url": "https://creative.adcontextprotocol.org",
        "id": "display_300x250"
      },
      "name": "Display 300x250",
      "type": "display",
      "assets": [
        {
          "asset_type": "image",
          "width": 300,
          "height": 250,
          "required": true
        }
      ],
      "is_standard": true
    }
  ]
}
```

For composite formats, the response should expose the slots/assets that the
buyer must provide. The wholesale product can further narrow these formats, but it should
not invent a format shape that is unavailable from the creative-format catalog.

### Signal Candidate Discovery

```http
GET /api/v1/tenant-management/tenants/{tenant_id}/signals/candidates
```

Returns adapter objects that can become AdCP-visible signals. This endpoint is
operator/setup-facing; buyer-facing signal discovery still flows through
`get_signals` after candidates are mapped.

```json
{
  "candidates": [
    {
      "candidate_id": "gam_custom_key:123:value:456",
      "name": "Sports Enthusiasts",
      "description": "GAM custom targeting value under Audience Segment.",
      "targeting_dimension": "audience",
      "value_type": "binary",
      "enumerability": "enumerable",
      "source": {
        "adapter": "google_ad_manager",
        "kind": "custom_key_value",
        "native_ids": {
          "key_id": "123",
          "value_id": "456"
        }
      },
      "mapping": {
        "state": "unmapped",
        "signal_id": null
      },
      "review": {
        "required": false,
        "reasons": []
      },
      "evidence": {
        "confidence": 0.95,
        "sources": ["synced_custom_targeting"]
      },
      "metadata": {}
    }
  ]
}
```

Candidate fields:

| Field | Meaning |
|---|---|
| `candidate_id` | Stable opaque ID for bulk-create and preview. |
| `targeting_dimension` | Normalized AdCP dimension such as `audience`, `content`, `geo`, `device`, `custom`. |
| `value_type` | `binary`, `categorical`, or `numeric`. |
| `enumerability` | `enumerable`, `lazy_enumerable`, `freeform`, or `range`. |
| `mapping.state` | `unmapped`, `mapped`, `stale`, or `unsupported`. |
| `source` | Native adapter lineage under a normalized wrapper. |
| `review` | Flags ambiguous or sensitive mappings before publishing. |

Adapter coverage:

- GAM: audience segments, custom targeting keys/values, and complex targeting
  groups.
- FreeWheel: viewership profiles, audience items, custom KV, and standard
  attributes when synced/available.
- SpringServe: value lists and KV-like supply metadata when
  `enable_key_value_targeting` is on.
- Broadstreet: limited; expose only targeting concepts the adapter can actually
  materialize.
- Mock: conformance candidates for binary, categorical, numeric, lazy, and
  freeform cases.

### Signal Mapping Writes

```http
GET    /api/v1/tenant-management/tenants/{tenant_id}/signals/mappings
POST   /api/v1/tenant-management/tenants/{tenant_id}/signals/mappings
PATCH  /api/v1/tenant-management/tenants/{tenant_id}/signals/mappings/{signal_id}
DELETE /api/v1/tenant-management/tenants/{tenant_id}/signals/mappings/{signal_id}
POST   /api/v1/tenant-management/tenants/{tenant_id}/signals/mappings:bulk-create
```

These endpoints are backed by `TenantSignal`, but unlike buyer-facing
`get_signals` they must round-trip operator-authored mapping details:

```json
{
  "signal_id": "sports_enthusiasts",
  "name": "Sports Enthusiasts",
  "description": "Publisher first-party sports audience.",
  "targeting_dimension": "audience",
  "value_type": "binary",
  "categories": [],
  "data_provider": "publisher_1p",
  "source_candidate_id": "gam_custom_key:123:value:456",
  "adapter_config": {
    "adapter": "google_ad_manager",
    "kind": "custom_key_value",
    "key_id": "123",
    "value_id": "456"
  },
  "lineage": {
    "source": "synced_custom_targeting",
    "native_ids": {
      "key_id": "123",
      "value_id": "456"
    }
  }
}
```

The existing `/signals` composition endpoints can remain as buyer-friendly
operator reads, but embedded setup needs this mapping surface so the storefront
can inspect, diff, edit, and explain what it wrote.

### Setup Preview and Validation

```http
POST /api/v1/tenant-management/tenants/{tenant_id}/wholesale-products:preview
POST /api/v1/tenant-management/tenants/{tenant_id}/signals/mappings:preview
POST /api/v1/tenant-management/tenants/{tenant_id}/composition:validate
```

Preview responses should include:

- normalized buyer-facing projection
- adapter execution summary
- native adapter payload fragments where safe to expose
- blocking validation errors
- warnings and review-required items
- unsupported targeting/optimization explanations
- remediation hints

Example:

```json
{
  "valid": false,
  "buyer_projection": {
    "format_ids": [
      {"agent_url": "https://creative.adcontextprotocol.org", "id": "homepage_takeover"}
    ],
    "publisher_properties": [
      {"publisher_domain": "example.com", "selection_type": "all"}
    ]
  },
  "adapter_projection": {
    "adapter": "google_ad_manager",
    "line_item_inventory": {
      "targetedPlacementIds": ["123456"]
    }
  },
  "errors": [
    {
      "code": "format_binding_missing_placeholder",
      "message": "homepage_takeover requires a rail creative placeholder."
    }
  ],
  "warnings": []
}
```

### Wholesale Product CRUD

Preferred external path:

```http
GET    /api/v1/tenant-management/tenants/{tenant_id}/wholesale-products
POST   /api/v1/tenant-management/tenants/{tenant_id}/wholesale-products
GET    /api/v1/tenant-management/tenants/{tenant_id}/wholesale-products/{wholesale_product_id}
PUT    /api/v1/tenant-management/tenants/{tenant_id}/wholesale-products/{wholesale_product_id}
DELETE /api/v1/tenant-management/tenants/{tenant_id}/wholesale-products/{wholesale_product_id}
```

Existing inventory-component path:

```http
/api/v1/tenant-management/tenants/{tenant_id}/inventory-profiles
```

`inventory-profiles` can remain for existing clients that explicitly manage
reusable inventory components. It should not be the primary embedded/storefront
API, and new wholesale-product clients should only use it indirectly through
the nested `inventory` field.

### Wholesale Product Validation

```http
POST /api/v1/tenant-management/tenants/{tenant_id}/wholesale-products:validate
```

Validates without persisting:

- publisher domains are authorized for the tenant
- selector types are valid for the tenant adapter
- selector IDs exist in the local inventory cache
- creative formats are syntactically valid
- format bindings satisfy the adapter binding schema
- targeting and optimization capabilities do not exceed adapter capabilities

### Compatibility with Existing Product APIs

The existing composition API already exposes `/products`. During migration,
`/wholesale-products` should be the preferred embedded/storefront path and
`/products` should remain a compatibility path for existing internal clients
and AdCP-product terminology:

```http
GET    /api/v1/tenant-management/tenants/{tenant_id}/products
POST   /api/v1/tenant-management/tenants/{tenant_id}/products
GET    /api/v1/tenant-management/tenants/{tenant_id}/products/{product_id}
PUT    /api/v1/tenant-management/tenants/{tenant_id}/products/{product_id}
DELETE /api/v1/tenant-management/tenants/{tenant_id}/products/{product_id}
```

The compatibility write shape should accept the old profile linkage while the
new wholesale-product path accepts the nested `inventory` shape:

```json
{
  "product_id": "homepage_takeover_fixed",
  "inventory_profile_id": "homepage_takeover_inventory",
  "name": "Homepage Takeover",
  "description": "Fixed-price homepage takeover.",
  "delivery_type": "guaranteed",
  "pricing_options": [
    {
      "pricing_model": "cpm",
      "currency": "USD",
      "is_fixed": true,
      "rate": 40
    }
  ],
  "forecast": {
    "impressions": 1000000
  }
}
```

Longer term, the compatibility endpoint can be a thin alias over the same
service that powers `/wholesale-products`.

## `get_products` Projection

`get_products` returns buyer/composer-facing data from `WholesaleProduct`.
It must not expose adapter execution selectors by default.

Projection rules:

| `get_products` field | Source |
|---|---|
| `product_id`, `name`, `description` | Wholesale product |
| `publisher_properties` | `inventory.publisher_properties` |
| `format_ids` | `inventory.creative_formats[].format_id` |
| `pricing_options` | Wholesale product |
| `property_targeting_allowed` | Wholesale product capabilities |
| `signal_targeting_allowed` | Wholesale product capabilities |
| `targeting_capabilities` | Wholesale product, if supported by schema extension |
| `optimization_capabilities` | Wholesale product, if supported by schema extension |
| `forecast` | Wholesale product |
| `allowed_actions` | Wholesale product plus adapter |

## Adapter Coverage

Adapter setup capability must describe what the embedded setup flow can do
**now**, not just what the adapter conceptually supports. If an adapter can
execute a selector shape but does not yet expose synced candidates, the
capability response should mark candidate discovery unavailable or
scope-pending.

### Google Ad Manager

Selector types:

| Selector | Source | Notes |
|---|---|---|
| `ad_unit` | `GAMInventory` cache | Hierarchical. Supports `include_descendants`. May carry sizes in metadata. |
| `placement` | `GAMInventory` cache | Group of ad units. Useful for packages curated in GAM. |

Execution binding:

- `ad_unit` and `placement` selectors map to line item inventory targeting.
- Format bindings map accepted creative formats to GAM creative placeholders,
  sizes, optional creative templates, and optional roadblocking.
- Multi-slot formats such as takeovers are one creative format with multiple
  placeholders, not one format per ad unit.

Capabilities:

- Pricing models currently include CPM, VCPM, CPC, FLAT_RATE.
- Targeting is constrained by GAM targeting support and tenant custom targeting
  config.
- Candidate readiness is strongest here: synced ad units, placements, audience
  segments, custom targeting keys/values, and lazy value refresh are already
  present in admin/setup code paths and should be promoted behind the generic
  APIs.

### FreeWheel

Selector types:

| Selector | Source | Notes |
|---|---|---|
| `site` | `freewheel_inventory` | Business/top-level inventory grouping. |
| `site_section` | `freewheel_inventory` | Sub-section inventory grouping. |
| `video_group` | `freewheel_inventory` | Publisher-curated video/audience grouping. |
| `series` | `freewheel_inventory` | Specific content series. |
| `ad_unit_package` | `freewheel_inventory` | Slot package such as pre/mid/post-roll. Often closest to buyer-facing placement. |

Execution binding:

- Wholesale product selectors map into `FreeWheelProductConfig` fields:
  `site_ids`, `site_section_ids`, `video_group_ids`, `series_ids`,
  `ad_unit_package_id`.
- Creative formats are VAST/video-oriented by default.
- Standard attributes and value lists should be exposed as targeting/signal
  selector sources, not as inventory selectors unless they define supply.

Capabilities:

- Optimization/pacing should reflect what the FreeWheel write path can
  actually materialize.
- Some live write/reporting scopes are still pending; capability responses
  should include `available=false` or `scope_pending=true` per feature rather
  than pretending support is complete.
- Candidate readiness is mixed: inventory sync exists, signal materialization
  supports declared FreeWheel kinds, but setup APIs still need candidate
  discovery for audiences, viewership profiles, and custom KV before an
  embedded storefront can fully self-serve this adapter.

### Broadstreet

Selector types:

| Selector | Source | Notes |
|---|---|---|
| `zone` | Broadstreet inventory manager | Primary inventory placement concept. |

Execution binding:

- Wholesale product selectors map to `BroadstreetProductConfig.targeted_zone_ids`.
- Format bindings map to Broadstreet templates and supported ad formats:
  display, HTML, text.
- Zone dimensions can infer simple display format compatibility, but the wholesale product
  still explicitly declares accepted formats.

Capabilities:

- Pricing models are CPM and FLAT_RATE.
- Pacing maps to Broadstreet delivery rate values such as EVEN, FRONTLOADED,
  ASAP.
- Candidate readiness should be zone-first. Broadstreet setup can be simpler
  than GAM/FreeWheel because zones are the primary sellable selector and
  creative formats map to known Broadstreet templates.

### SpringServe

Selector types:

| Selector | Source | Notes |
|---|---|---|
| `supply_partner` | `springserve_inventory` | Top-level seller relationship. |
| `supply_router` | `springserve_inventory` | Natural wholesale product root when publishers curate supply routes. |
| `supply_tag` | `springserve_inventory` | Per-property supply unit; maps most directly to demand tag supply. |

Execution binding:

- Wholesale product selectors map into `SpringServeProductConfig.supply_tag_ids` and
  related future fields as the adapter matures.
- The tenant `demand_class` controls whether buyers send hosted creative assets
  or a third-party tag URL.
- KV/value-list entities should be exposed as signals or targeting selectors,
  not inventory selectors unless the publisher uses them as supply partitions.

Capabilities:

- Capability responses should reflect tenant config such as
  `enable_key_value_targeting`.
- SpringServe supply read scope can be pending; selector search should return a
  clear sync/scope state when no cache is available.
- Candidate readiness is partial: supply partners/tags are cached, value lists
  can become signals, and composed signal materialization should be advertised
  only when the adapter can execute it.

### Mock

Mock is the conformance adapter. It should support every generic selector and
capability path needed by tests:

- selector search with placements and ad-unit-like groupings
- display, video, and composite test formats
- targeting and optimization capability validation
- deterministic validation errors

## UI and Setup Flow

The setup flow should be driven by the same APIs the embedding storefront uses.

1. Connect adapter credentials.
2. Run inventory sync.
3. Call `GET /inventory/adapter-capabilities`.
4. Use `GET /inventory/publisher-properties` for publisher-domain/property
   selection.
5. Use `GET /creative-formats` for accepted creative-format selection.
6. Use `GET /inventory/selectors` for searchable ad-server inventory pickers.
7. Create wholesale products with publisher properties, creative formats,
   pricing, targeting capabilities, optimization capabilities, and execution
   bindings.
8. Verify `get_products` returns the buyer-facing projection.

The Admin UI should treat the existing product editor as the wholesale-product
editor. It needs to bring the inventory selection into the same flow as
pricing, targeting, optimization, forecast, and buyer-facing copy. A first cut
can still store the inventory selection in `InventoryProfile`, but the UI
should present one sellable wholesale product rather than two separate concepts.

The current inventory-profile screens can remain as an advanced/reusable
inventory-selection editor during migration, but embedded setup should use the
new wholesale-product APIs directly.

For embedded tenants specifically:

- Adapter credential forms remain platform-managed and should be read-only or
  hidden in the publisher UI.
- Setup UI should start from adapter capability and candidate APIs, not from
  raw adapter config forms.
- Publisher-facing setup should not require the publisher to type native ad
  server IDs when a synced selector cache is available.
- Shared-agent deployments must not infer publisher domains from agent URLs.
- Native adapter details can be shown as explanation/evidence, but the saved
  object should still be the generic wholesale-product/signal shape.

## Validation Rules

- Wholesale product `inventory.publisher_properties` must be non-empty.
- Wholesale product `inventory.creative_formats` must be non-empty.
- Wholesale product `inventory.execution.adapter` must match the tenant adapter.
- Every selector must use a selector type advertised by
  `adapter-capabilities`.
- Every selector ID must exist in the local synced inventory cache unless the
  selector type explicitly allows manual entry.
- Wholesale product targeting/optimization capabilities must be subsets of adapter-level
  capabilities.
- Wholesale products must own pricing options.

## Migration Plan

1. Add schemas for `WholesaleProductCreate`, `WholesaleProductUpdate`,
   `WholesaleProductRead`, `InventorySelectorRead`, and
   `AdapterInventoryCapabilitiesRead`.
2. Add `/wholesale-products` routes backed by the existing `Product` service
   plus the linked `InventoryProfile` storage for `inventory`.
3. Expand `InventoryProfile` JSON usage for the nested `inventory` object:
   - `format_ids` remains accepted for simple formats.
   - `creative_formats` can be stored in `constraints` or a new JSON column in
     a later migration. Short term, derive `creative_formats` from `format_ids`
     when slot details are absent.
   - `inventory_config` becomes the persisted representation of
     `inventory.execution.selectors` and `format_bindings`.
4. Implement adapter capability providers for GAM, FreeWheel, Broadstreet,
   SpringServe, and Mock.
5. Implement normalized selector search over each adapter's local cache.
6. Add generic inventory sync, publisher-property, and creative-format
   discovery endpoints.
7. Add signal candidate, signal mapping, and setup preview endpoints backed by
   `TenantSignal`.
8. Keep `/products` and `inventory_profile_id` compatibility while steering
   embedded clients to `/wholesale-products`.
9. Update Admin UI forms so product authoring is wholesale-product authoring.
10. Update `get_products` projection tests for inventory-derived formats,
   publisher properties, and capabilities.
11. Do not add adapter-specific OpenAPI artifacts. New setup endpoints should
    land in the generic setup API and be represented in the tenant-management
    OpenAPI, not per-adapter contracts.

## Open Decisions

- Whether `creative_formats.slot_requirements` should be an AdCP extension or
  remain a composition API authoring field until the protocol has a standard
  slot model.
- Whether adapter `format_bindings` should be visible to all embedding
  storefront clients or only to operator-admin clients.
- Whether the long-term database model should keep `InventoryProfile` as a
  reusable inventory component or fold the fields directly into `Product`.
