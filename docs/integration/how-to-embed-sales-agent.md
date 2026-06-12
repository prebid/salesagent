# How to Embed Sales Agent

**Audience:** host-product engineers embedding Sales Agent inside their own
publisher storefront or control plane.

This guide is the practical integration path. Design rationale and operator
details live in:

- [Embedded Mode Design](../design/embedded-mode.md)
- [Embedded Mode Operational Reference](embedded-mode-operational.md)
- [Embedded Mode Identity Contract](embedded-mode-identity-contract.md)
- [Tenant Management OpenAPI YAML](../api/tenant-management-openapi.yaml)

## Overview

Embedded mode runs Sales Agent as a private service behind the host product.
The host owns authentication, tenant provisioning, platform-managed settings,
and outer navigation. Sales Agent owns the AdCP buyer protocol surfaces, the
publisher admin UI, adapter execution, sync history, workflows, products,
creatives, and operational diagnostics.

Recommended request flow:

```text
Publisher user
  -> Host product UI
  -> Host authenticated reverse proxy
  -> Sales Agent /tenant/{tenant_id}/...

Host control plane
  -> Sales Agent /api/v1/tenant-management/...
```

The examples below use `/api/v1/tenant-management` as the management API base
path. In deployments where the admin app is mounted under `/admin`, use
`/admin/api/v1/tenant-management` instead.

The host product should call the Tenant Management API server-to-server. The
embedded admin UI should be reached only through the host proxy with trusted
identity headers.

## Deployment Shape

Run Sales Agent on a private network. Do not expose embedded instances directly
to the public internet.

Required production posture:

- `MANAGED_INSTANCE=true`
- `TENANT_MANAGEMENT_API_KEY=<strong random key>`
- A network boundary that makes Sales Agent reachable only through the host
  proxy for embedded admin UI requests.
- `MANAGED_MODE_FRAME_ANCESTORS=<host origin>` to restrict iframe embedding.
- CIDR allow-lists for management, admin, and buyer-protocol listeners as
  described in the operational reference.
- Adapter secrets for the adapters you enable, such as Google Ad Manager OAuth
  or service-account settings.

Health checks:

```bash
curl -fsS "$SALESAGENT_BASE_URL/health"
curl -fsS "$SALESAGENT_BASE_URL/api/v1/tenant-management/health" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY"
```

For local embedded development, `docker-compose.core.yml` is the closest shape:

```bash
docker compose -p core -f docker-compose.core.yml up -d
export SALESAGENT_BASE_URL=http://localhost:3091
export TENANT_MANAGEMENT_API_KEY=dev-tenant-management-key-change-me
```

## Tenant Provisioning

Provision tenants through the Tenant Management API:

```http
POST /api/v1/tenant-management/tenants/provision
X-Tenant-Management-API-Key: <tenant-management-api-key>
Content-Type: application/json
```

Example Google Ad Manager service-account request:

```json
{
  "name": "Example Publisher",
  "external_org_id": "host-org-123",
  "external_source": "example-storefront",
  "contact_email": "publisher-admin@example.com",
  "adapter": {
    "type": "google_ad_manager",
    "network_code": "1234567",
    "service_account_email": "salesagent@example-project.iam.gserviceaccount.com",
    "service_account_key_json": "{...}"
  },
  "initial_principal": {
    "principal_id": "publisher-admin-example-com",
    "name": "Publisher Admin",
    "access_token": "..."
  }
}
```

Successful response shape:

```json
{
  "tenant_id": "tnt_example",
  "admin_url_path": "/tenant/tnt_example",
  "mcp_url": "https://salesagent.internal/mcp/",
  "a2a_url": "https://salesagent.internal/a2a",
  "initial_principal": {
    "principal_id": "publisher-admin-example-com",
    "access_token": "..."
  }
}
```

Treat `201 Created` as "tenant is ready to enter." Initial syncs may still be
running, but the publisher can use non-inventory-dependent screens immediately.
If provisioning returns `4xx`, do not create local host-product state that
assumes Sales Agent exists; the operation is intended to be synchronous and
binary.

For GAM, prefer service accounts for embedded deployments when the host can
manage key rotation and IAM. OAuth is still valid when the publisher must grant
access interactively.

## Embedding the Admin UI

Mount the Sales Agent tenant UI behind a host-owned route, for example:

```text
Host URL:        /storefront/salesagent/tenant/{tenant_id}/products
Sales Agent URL: /tenant/{tenant_id}/products?embedded=1
```

Example reverse-proxy mapping:

```nginx
location /storefront/salesagent/ {
    proxy_pass http://salesagent.internal/;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /storefront/salesagent;

    proxy_set_header X-Identity-Email $auth_user_email;
    proxy_set_header X-Identity-User-Id $auth_user_id;
    proxy_set_header X-Identity-Org-Id $auth_org_id;
    proxy_set_header X-Identity-Role $auth_role;
    proxy_set_header X-Identity-Source "example-storefront";
}
```

Use `?embedded=1` when the UI is rendered inside host chrome. Sales Agent also
detects embedded identity-header requests and hides its global chrome for
embedded tenants. Page-level breadcrumbs and action buttons remain visible.

To make the first breadcrumb return to the host product, set the tenant's
`embed_breadcrumb_root` through the Tenant Management API or forward the
documented request header when appropriate.

## Trusted Identity Headers

Forward these on every proxied admin UI request:

| Header | Required | Purpose |
|---|---:|---|
| `X-Identity-Email` | yes | Authenticated user's email |
| `X-Identity-Org-Id` | yes | Host org id; must map to `Tenant.external_org_id` |
| `X-Identity-Role` | yes | `admin`, `member`, or `viewer` |
| `X-Identity-Source` | yes | Host product identifier |
| `X-Identity-User-Id` | no | Stable host user id for audit |

Security boundary:

- The host product must authenticate the user before proxying.
- The host proxy must overwrite any incoming `X-Identity-*` headers from the
  browser.
- Sales Agent must not be reachable on a path that bypasses the proxy.
- `X-Identity-Org-Id` must match the tenant URL being requested.

See the [identity contract](embedded-mode-identity-contract.md) for exact
failure modes.

## Host-Controlled Configuration

Use the Tenant Management API for settings the host owns:

```text
GET  /api/v1/tenant-management/adapters
GET  /api/v1/tenant-management/adapters/{adapter_type}/capabilities
GET  /api/v1/tenant-management/tenants/{tenant_id}
PUT  /api/v1/tenant-management/tenants/{tenant_id}/adapter-config
POST /api/v1/tenant-management/tenants/{tenant_id}/adapter-config/test-connection
GET  /api/v1/tenant-management/tenants/{tenant_id}/accounts
POST /api/v1/tenant-management/tenants/{tenant_id}/accounts
GET  /api/v1/tenant-management/tenants/{tenant_id}/buyer-advertiser-mappings
POST /api/v1/tenant-management/tenants/{tenant_id}/buyer-advertiser-mappings
GET  /api/v1/tenant-management/tenants/{tenant_id}/signals
POST /api/v1/tenant-management/tenants/{tenant_id}/signals
```

For wholesale-product and signal-mapping API flows, use the focused guides:

- [Embedded Wholesale Products API](embedded-wholesale-products-api.md)
- [Embedded Signal Mapping API](embedded-signals-api.md)

## Publisher-Controlled Surfaces

The publisher should normally use the embedded Sales Agent UI for:

- Product catalog management.
- Creative review and creative status.
- Workflow approvals and rejections.
- Media-buy and package drill-downs.
- Inventory selection screens that depend on synced adapter data.
- Publisher-owned policy and configuration surfaces enabled by the deployment.

The host product can choose to build its own UI for selected API surfaces, but
avoid splitting ownership for the same setting. One owner should write a given
configuration surface.

## Sync and Refresh Flows

Sales Agent creates routine server-owned sync jobs for inventory, custom
targeting, advertisers, and adapter-specific reporting streams. The host can
also request a refresh:

```http
POST /api/v1/tenant-management/tenants/{tenant_id}/refresh
X-Tenant-Management-API-Key: <tenant-management-api-key>
```

Typical `202 Accepted` response:

```json
{
  "sync_run_ids": {
    "inventory": "sync_inventory_abc",
    "custom_targeting": "sync_targeting_def",
    "advertisers": "sync_advertisers_ghi"
  },
  "started_at": "2026-05-27T12:00:00Z"
}
```

The endpoint is idempotent for a short window. If a sync is already running
outside that window, Sales Agent returns `409 sync_already_running` with the
running sync types and current run ids. Treat this as "do not start another
run"; update UI from `/status` instead.

Do not interpret raw `SyncJob.status` values in host UI. Use the derived
`syncs` block on `/status`:

```json
{
  "status": "failed",
  "severity": "warning",
  "last_success_at": "2026-05-27T11:00:00Z",
  "issue": {
    "code": "sync_transient_failure",
    "category": "transient",
    "message": "Timeout while fetching inventory",
    "retryable": true,
    "action": "retry_sync"
  }
}
```

Public status normalization:

| Raw state | Public `status` |
|---|---|
| `pending`, `queued`, `running`, `in_progress` | `running` |
| `completed`, `success` | `success` |
| `failed`, `error` | `failed` |
| no row | `never_run` |

Applicability is evaluated before freshness for platform-derived streams. For
example, a GAM tenant with no mapped `custom_key_value` signals has no
`signal_coverage` stream to run, and a GAM tenant whose products do not target
placements or ad units has no `pricing_availability` stream to run. In
those cases `/status.syncs.<type>` may return `status: "success"`,
`severity: "ok"`, and `item_count: 0` even when no `SyncJob` row exists.

Use `severity` for badges:

| Severity | Storefront treatment |
|---|---|
| `ok` | Normal |
| `warning` | Non-blocking warning or retry affordance |
| `critical` | Prominent action required |

Use `issue.action` for CTAs:

| Action | Suggested CTA |
|---|---|
| `reconnect_adapter` | Reconnect ad server |
| `retry_sync` | Retry sync |
| `wait` | Sync in progress |
| `contact_support` | Contact support |

Detailed run history remains admin detail:

```text
GET /api/v1/tenant-management/tenants/{tenant_id}/sync-history
```

## Webhooks

Register outbound subscriptions:

```http
POST /api/v1/tenant-management/tenants/{tenant_id}/webhooks
X-Tenant-Management-API-Key: <tenant-management-api-key>
Content-Type: application/json
```

```json
{
  "url": "https://storefront.example.com/webhooks/salesagent",
  "event_types": [
    "sync_run.completed",
    "sync_run.failed",
    "sync_health.changed"
  ],
  "description": "Storefront sync notifications"
}
```

The create response returns the signing secret once. Store it in the host
secret store and verify every delivery signature. See the OpenAPI spec for
the full event taxonomy.

Use raw run events for correlation:

- `sync_run.completed`
- `sync_run.failed`

Use derived health events for storefront alerts:

- `sync_health.changed`

`sync_health.changed` emits on committed sync-run transitions when the public
health severity changes for a tenant sync stream. It is not sent for every
successful run, and it is not a timer for freshness-only degradation when no
new sync run has been written.

Example derived event `data` block:

```json
{
  "sync_type": "inventory",
  "adapter_type": "google_ad_manager",
  "health": "critical",
  "previous_health": "warning",
  "reason": "auth",
  "message": "Reconnect the ad server adapter.",
  "action": "reconnect_adapter",
  "last_success_at": "2026-05-27T10:00:00Z",
  "last_failure_at": "2026-05-27T12:00:00Z",
  "next_retry_at": null,
  "related_sync_run_id": "sync_inventory_abc"
}
```

## Status Dashboard Integration

Poll or refresh from:

```text
GET /api/v1/tenant-management/tenants/{tenant_id}/status
```

Recommended host dashboard behavior:

- Poll on page load and after user-triggered refresh.
- Subscribe to `sync_health.changed` for run-triggered alert changes.
- Continue lightweight periodic polling if the host UI must notice
  freshness-only degradation while no sync job is running.
- Show `syncs.<type>.severity` as the badge.
- Show `syncs.<type>.issue.message` as publisher-facing copy.
- Route `issue.action=reconnect_adapter` to a host-owned adapter setup flow.
- Route `issue.action=retry_sync` to `POST /refresh`.
- Keep raw `error`, trigger source, `sync_run_id`, and full history in Sales
  Agent admin or internal operator tooling.

## Security Checklist

- Sales Agent is private to the host network.
- Browser traffic cannot reach Sales Agent without the host proxy.
- The host proxy overwrites all identity headers.
- Tenant Management API keys are stored server-side only.
- Webhook secrets are stored in a host secret manager.
- Webhook signatures are verified before processing.
- `MANAGED_MODE_FRAME_ANCESTORS` limits iframe parents.
- Public UI copy uses `/status.syncs.*.issue.message`, not raw
  `SyncJob.error_message`.
- Reverse-proxy configuration preserves path prefixes and does not create SSRF
  or open-proxy behavior.
- Host logs avoid storing adapter secrets, access tokens, service-account keys,
  and webhook secrets.

## Local Smoke Test

Start Sales Agent:

```bash
docker compose -p core -f docker-compose.core.yml up -d
export SALESAGENT_BASE_URL=http://localhost:3091
export TENANT_MANAGEMENT_API_KEY=dev-tenant-management-key-change-me
```

Check health:

```bash
curl -fsS "$SALESAGENT_BASE_URL/api/v1/tenant-management/health" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY"
```

Provision a mock tenant:

```bash
curl -sS -X POST "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/provision" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Local Embedded Publisher",
    "external_org_id": "local-org-123",
    "external_source": "local-storefront",
    "contact_email": "admin@example.com",
    "adapter": {"type": "mock", "dry_run": true},
    "initial_principal": {
      "principal_id": "admin-example-com",
      "name": "Admin",
      "access_token": "local-dev-token"
    }
  }'
```

Open the embedded UI through your host proxy route, or directly during local
debugging:

```text
http://localhost:3091/tenant/{tenant_id}?embedded=1
```

Trigger refresh and inspect status:

```bash
curl -sS -X POST "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/refresh" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY"

curl -sS "$SALESAGENT_BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/status" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY"
```

For a scripted version of the same flow, run:

```bash
./scripts/managed_mode_smoke.sh
```
