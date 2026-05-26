# Embedded Wholesale Products API

**Status:** Implemented
**Audience:** Host products embedding salesagent and managing publisher setup through APIs
**Last updated:** 2026-05-26

This guide shows the API-only path for setting up an embedded tenant and
creating wholesale products. A host product can use this flow to build its own
storefront UI without relying on salesagent internals or adapter-specific
OpenAPI files.

Wholesale products are the sellable objects exposed to buyers through AdCP
`get_products`. Each product combines buyer-facing merchandising with the
publisher domains, creative formats, pricing, targeting, optimization, and
ad-server selectors needed to execute the buy.

## API Reference And Auth

The canonical machine-readable reference is the Tenant Management OpenAPI spec:

- Static: `openapi.yaml`, `openapi.json`
- Static copy: `docs/api/tenant-management-openapi.yaml`,
  `docs/api/tenant-management-openapi.json`
- Live JSON: `GET /api/v1/tenant-management/docs/openapi.json`
- Live Swagger UI: `GET /api/v1/tenant-management/docs/swagger/`

When the admin app is mounted under `/admin/`, the public paths gain that
prefix. For example:

```text
/admin/api/v1/tenant-management/docs/swagger/
```

All Tenant Management API calls use the server-to-server API key:

```http
X-Tenant-Management-API-Key: <tenant-management-api-key>
Content-Type: application/json
```

In local embedded-mode development, `docker-compose.core.yml` defaults this key
to `dev-tenant-management-key-change-me`.

## End-To-End Flow

1. Discover supported adapters and their connection schemas.
2. Provision an embedded tenant with adapter credentials.
3. Trigger or inspect adapter inventory sync.
4. Discover the tenant's adapter selector vocabulary.
5. Look up publisher domains and property IDs/tags through AAO.
6. Search cached ad-server selectors.
7. Discover creative formats.
8. Optionally configure buyer-facing signal mappings through the
   [Embedded Signal Mapping API](embedded-signals-api.md).
9. Validate and preview a wholesale product draft.
10. Create or update the wholesale product.
11. Confirm buyer-facing discovery through AdCP `get_products`.

The host product owns which of these steps are automated and which are surfaced
to a publisher in setup UI.

## 1. Discover Adapters

List the adapter catalog:

```bash
curl -sS "$SALESAGENT_BASE_URL/api/v1/tenant-management/adapters" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY"
```

Fetch one adapter's capability contract:

```bash
curl -sS "$SALESAGENT_BASE_URL/api/v1/tenant-management/adapters/google_ad_manager/capabilities" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY"
```

Fetch one adapter's connection schema:

```bash
curl -sS "$SALESAGENT_BASE_URL/api/v1/tenant-management/adapters/google_ad_manager/config-schema" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY"
```

The connection schema tells the embedding storefront which credential fields to
collect. The current live adapters are:

| Adapter | `adapter.type` | Main setup objects for wholesale products |
|---|---|---|
| Google Ad Manager | `google_ad_manager` | Placements, ad units, creative placeholders |
| FreeWheel | `freewheel` | Sites, site sections, site groups, series, video groups, ad unit packages/nodes, standard attributes |
| Broadstreet | `broadstreet` | Zones |
| SpringServe | `springserve` | Supply partners, supply routers, supply tags, keys, value lists |

`mock` is available for local and CI flows but should normally be hidden from
production publisher setup.

## 2. Provision A Tenant

Provision creates the embedded tenant, persists adapter configuration, probes
the connection, and optionally creates an initial principal.

```bash
curl -sS -X POST "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/provision" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Wonderstruck",
    "external_org_id": "wonderstruck-prod",
    "external_source": "storefront",
    "contact_email": "ops@wonderstruck.example",
    "public_agent_url": "https://interchange.io",
    "adapter": {
      "type": "google_ad_manager",
      "network_code": "12345678",
      "service_account_email": "salesagent@project.iam.gserviceaccount.com",
      "service_account_key_json": "{\"type\":\"service_account\"}"
    },
    "default_currency": "USD"
  }'
```

On success, the response includes:

- `tenant_id`
- `admin_url_path`
- `mcp_url`
- `a2a_url`
- `adapter.connection_test_passed`
- optional `initial_principal.access_token`

Provisioning is synchronous and binary: 201 means the tenant and adapter config
were committed; 4xx means nothing was written and the publisher can retry after
fixing input.

To replace adapter credentials later:

```bash
curl -sS -X PUT "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/adapter-config" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{ "type": "google_ad_manager", "network_code": "12345678", "service_account_email": "salesagent@project.iam.gserviceaccount.com", "service_account_key_json": "{\"type\":\"service_account\"}" }'
```

To probe the saved connection without writing:

```bash
curl -sS -X POST "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/adapter-config/test-connection" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY"
```

## 3. Refresh And Inspect Sync State

Provision kicks off initial sync work when the adapter supports it. A host
product can request a new refresh:

```bash
curl -sS -X POST "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/refresh" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY"
```

The response is `202 Accepted` with `sync_run_ids` by sync type. Repeating the
call within the idempotency window reuses the same run IDs.

Inspect progress and setup completeness:

```bash
curl -sS "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/status" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY"
```

Read `syncs.inventory`, `syncs.custom_targeting`, `syncs.advertisers`, and
`setup_tasks` to decide which setup UI sections are ready or blocked.

## 4. Discover Tenant Inventory Capabilities

This endpoint returns the selector types, pricing models, targeting flags, and
optimization flags that are valid for the tenant's configured adapter:

```bash
curl -sS "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/inventory/adapter-capabilities" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY"
```

The storefront should render selector pickers from `selector_types` instead of
hard-coding GAM terms. For example, GAM returns `placement` and `ad_unit`;
SpringServe returns `supply_partner`, `supply_router`, `supply_tag`, `key`, and
`value_list`.

## 5. Look Up Publisher Properties

Publisher domains should be mapped on each wholesale product. Do not use the
agent URL as a substitute for publisher supply. The agent URL identifies the
authorized sales agent; the publisher domain identifies the supply.

Look up one publisher domain through AAO and cache its `adagents.json`
properties:

```bash
curl -sS -X POST "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/inventory/publisher-properties:lookup" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{ "publisher_domain": "wonderstruck.org", "force_refresh": true }'
```

The response includes:

- `aao_status`: `authorized`, `unbound`, `pending`, `no_properties`, or
  `unreachable`
- `is_authorized`
- `property_ids`
- `property_tags`
- `domains`
- `properties`
- `allowed_selectors`

`allowed_selectors` is the easiest input for setup UI. It contains ready-to-use
publisher-property selector shapes:

```json
[
  {
    "publisher_domain": "wonderstruck.org",
    "selection_type": "all",
    "label": "All properties on wonderstruck.org"
  },
  {
    "publisher_domain": "wonderstruck.org",
    "selection_type": "by_id",
    "property_ids": ["wonderstruck_home"],
    "label": "Selected properties on wonderstruck.org"
  },
  {
    "publisher_domain": "wonderstruck.org",
    "selection_type": "by_tag",
    "property_tags": ["premium"],
    "label": "Tagged properties on wonderstruck.org"
  }
]
```

List already cached publisher domains and properties:

```bash
curl -sS "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/inventory/publisher-properties" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY"
```

Large publishers can return thousands of properties. The setup UI should allow
domain-level `selection_type: "all"` and tag-level `selection_type: "by_tag"`
instead of forcing a user to select every property ID.

## 6. Search Ad-Server Selectors

Use the tenant's selector capability response to decide which selector types to
query.

```bash
curl -sS "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/inventory/selectors?selector_type=ad_unit&q=homepage&limit=25" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY"
```

Supported query parameters:

| Parameter | Meaning |
|---|---|
| `selector_type` | Adapter selector type, such as `ad_unit`, `placement`, `zone`, or `supply_tag`. |
| `q` | Search string. |
| `parent_id` | Optional parent filter when the selector capability has `supports_parent_filter: true`. |
| `limit` | Page size, 1-100. Defaults to 50. |
| `cursor` | Offset cursor returned by the previous page. |

Each selector contains:

```json
{
  "selector_type": "ad_unit",
  "external_id": "au_home",
  "name": "Homepage",
  "path": ["Wonderstruck", "Homepage"],
  "parent_id": null,
  "status": "active",
  "metadata": {
    "sizes": [{ "width": 970, "height": 250 }]
  }
}
```

The selected values are later written to
`inventory.execution.selectors[]` on the wholesale product.

## 7. Discover Creative Formats

Creative formats tell the buyer what they can submit for the product. Fetch the
available formats:

```bash
curl -sS "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/creative-formats" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY"
```

Optional filters:

```bash
curl -sS "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/creative-formats?q=takeover&asset_type=image" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY"
```

Each returned `format_id` can be copied into
`inventory.creative_formats[].format_id`. Multi-asset formats can include
`slot_requirements`, and adapter execution can include a matching
`format_bindings[]` entry.

## 8. Build The Wholesale Product Draft

A minimal GAM-backed draft looks like this:

```json
{
  "wholesale_product_id": "homepage_takeover",
  "name": "Homepage Takeover",
  "description": "High-impact homepage package.",
  "status": "active",
  "delivery_type": "guaranteed",
  "channels": ["display"],
  "pricing_options": [
    {
      "pricing_model": "cpm",
      "currency": "USD",
      "is_fixed": true,
      "rate": "40.00"
    }
  ],
  "forecast": {
    "impressions": 1000000
  },
  "inventory": {
    "publisher_properties": [
      {
        "publisher_domain": "wonderstruck.org",
        "selection_type": "by_id",
        "property_ids": ["wonderstruck_home"]
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
          }
        ]
      }
    ],
    "execution": {
      "adapter": "google_ad_manager",
      "selectors": [
        {
          "selector_type": "placement",
          "external_id": "pl_homepage_takeover"
        },
        {
          "selector_type": "ad_unit",
          "external_id": "au_home",
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
              { "slot_id": "leaderboard", "size": "970x250" }
            ],
            "roadblocking": "as_many_as_possible"
          }
        }
      ]
    }
  },
  "targeting_capabilities": {
    "allowed_dimensions": ["geo", "device"]
  },
  "optimization_capabilities": {
    "allowed_goals": ["impressions"]
  }
}
```

Important distinctions:

- `inventory.publisher_properties` is publisher supply: domains, property IDs,
  and property tags discovered through AAO/adagents.
- `inventory.execution.selectors` is adapter execution: ad units, placements,
  zones, supply tags, or other native ad-server selectors.
- `inventory.creative_formats` is buyer-facing creative eligibility.
- `inventory.execution.format_bindings` is adapter-specific execution detail.

## 9. Validate, Preview, And Create

Validate without persisting:

```bash
curl -sS -X POST "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/wholesale-products:validate" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d @homepage-takeover.json
```

Preview the buyer-facing projection and adapter projection:

```bash
curl -sS -X POST "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/wholesale-products:preview" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d @homepage-takeover.json
```

Create the product:

```bash
curl -sS -X POST "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/wholesale-products" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d @homepage-takeover.json
```

Manage products after creation:

```http
GET    /api/v1/tenant-management/tenants/{tenant_id}/wholesale-products
GET    /api/v1/tenant-management/tenants/{tenant_id}/wholesale-products/{product_id}
PUT    /api/v1/tenant-management/tenants/{tenant_id}/wholesale-products/{product_id}
DELETE /api/v1/tenant-management/tenants/{tenant_id}/wholesale-products/{product_id}
```

Validation currently checks:

- At least one publisher-property selector is present.
- At least one creative format is present.
- The execution adapter matches the tenant adapter.
- Selector types are supported by the tenant adapter.
- Selector IDs exist when the adapter inventory cache has data for that
  selector type.

## 10. Confirm Buyer Discovery

Wholesale products are backed by the same product catalog used by AdCP buyer
tools. After creation, a buyer or host-side test can call `get_products` through
MCP using an advertiser/principal token:

```bash
uvx adcp "$SALESAGENT_BASE_URL/mcp/" \
  --auth "$ADCP_PRINCIPAL_TOKEN" \
  get_products '{"brief":"homepage takeover"}'
```

The buyer-facing product should include the product name, description, pricing,
creative format IDs, and publisher-property selectors. Adapter execution details
remain internal to the Tenant Management API response.

## Adapter Notes

The API surface is generic, but adapters differ in which selectors and sync
streams they expose.

| Adapter | Selector examples | Inventory sync behavior | Notes |
|---|---|---|---|
| Google Ad Manager | `placement`, `ad_unit` | Imports GAM inventory, custom targeting, and advertisers. | `ad_unit.options.include_descendants` controls descendant targeting. Format bindings can carry creative placeholder and roadblocking details. |
| FreeWheel | `site`, `site_section`, `site_group`, `series`, `video_group`, `ad_unit_package`, `ad_unit_node`, `standard_attribute` | Imports FreeWheel inventory/reporting streams where configured. | Use capability discovery instead of assuming GAM-style hierarchy. |
| Broadstreet | `zone` | No broad inventory-sync stream in the current contract. | Zone IDs are the natural execution selector. |
| SpringServe | `supply_partner`, `supply_router`, `supply_tag`, `key`, `value_list` | Imports SpringServe inventory/reporting streams where configured. | Supply tags and routers are the natural roots for curated wholesale products. |

## Operational Guidance

- Use `GET /adapters` and `GET /adapters/{adapter_type}/config-schema` to build
  adapter setup UI. Do not depend on per-adapter OpenAPI files.
- Use `GET /tenants/{tenant_id}/inventory/adapter-capabilities` to render the
  product authoring UI for a specific tenant.
- Use `GET /tenants/{tenant_id}/signals/adapter-capabilities` when the
  storefront also manages buyer-facing signal mappings.
- Use `publisher-properties:lookup` at product setup time for each publisher
  domain the user adds.
- Prefer `selection_type: "all"` or `selection_type: "by_tag"` for large
  publishers; use `by_id` for explicitly curated property lists.
- Always call `wholesale-products:validate` before `POST` or `PUT` so the
  storefront can show structured issues.
- Keep the Tenant Management API key server-side. It should not be exposed to
  browser JavaScript.

## Related Docs

- [Embedded Mode Operational Reference](embedded-mode-operational.md)
- [Embedded Mode Identity Contract](embedded-mode-identity-contract.md)
- [Embedded Signal Mapping API](embedded-signals-api.md)
- [Wholesale Product API design notes](../design/wholesale-product-api.md)
- [Tenant Management OpenAPI YAML](../api/tenant-management-openapi.yaml)
