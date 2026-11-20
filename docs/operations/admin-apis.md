# Tenant Management API and Inventory Sync API

The admin application serves two HTTP APIs for the people who operate a deployment. Scripts
and provisioning tools call them with an API key. A browser session is neither needed nor
accepted.

The following table summarizes both APIs.

| API | Base path | Key header | Purpose |
|---|---|---|---|
| Tenant Management API | `/api/v1/tenant-management` | `X-Tenant-Management-API-Key` | Create, read, update, and delete tenants |
| Inventory Sync API | `/api/sync` | `X-API-Key` | Run and inspect Google Ad Manager (GAM) inventory and order syncs |

Both APIs belong to the Flask admin application, so each path works at the root of the
deployment and under `/admin`. For example, `/api/sync/stats` and `/admin/api/sync/stats`
reach the same view. [Admin UI architecture](../development/admin-ui-architecture.md)
explains the two mount points.

## How the APIs authenticate a request

Each API has one key for the whole deployment. A key is not tied to a tenant, so a caller
that holds the key reaches every tenant the API serves. Treat both keys as deployment
secrets.

`require_api_key_auth` in `src/admin/auth_helpers.py` builds the check for both APIs. The
check looks for the key in two places, in this order:

1. The environment variable, `TENANT_MANAGEMENT_API_KEY` or `SYNC_API_KEY`. When the
   variable is set, the check compares the presented key with it and ignores the database.
2. A row in the `superadmin_config` table. The row stores a SHA-256 digest of the key, never
   the key itself.

The following table lists the responses the check returns before a view runs.

| Condition | Status | Body |
|---|---|---|
| The request has no key header | `401` | `{"error": "Missing API key"}` |
| No key is configured in either place | `503` | `{"error": "API not configured. Set <VARIABLE> environment variable."}` |
| The presented key does not match | `401` | `{"error": "Invalid API key"}` |

### Configure the Tenant Management API key

Set `TENANT_MANAGEMENT_API_KEY` in the environment of the application. No script or route
mints the stored row for this API, so the environment variable is the supported way to turn
the API on.

### Configure the Inventory Sync API key

Use one of the following methods:

- Set `SYNC_API_KEY` in the environment of the application.
- Mint a stored key with `scripts/initialize_tenant_mgmt_api_key.py`. The script prints the
  key once, and the database keeps only its digest and a short display prefix. Run the
  script with `--rotate` to replace a stored key. The earlier key stops working at once.

```bash
uv run scripts/initialize_tenant_mgmt_api_key.py
```

The script imports the application package `src`, so run it with the repository root on
`PYTHONPATH`. The container image sets `PYTHONPATH` to `/app`, the repository root inside the
container.

Despite its name, the script mints the key that the Inventory Sync API checks. The scheduled
inventory sync, `scripts/ops/sync_all_tenants.py`, reads the key from `SYNC_API_KEY` only,
so a deployment that runs the schedule needs the environment variable.

## Tenant Management API

Every route requires the `X-Tenant-Management-API-Key` header. The routes write no audit log
rows.

The following table lists the routes.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/tenant-management/health` | Confirm that the API is up and the key is valid |
| `GET` | `/api/v1/tenant-management/tenants` | List tenants |
| `POST` | `/api/v1/tenant-management/tenants` | Create a tenant |
| `GET` | `/api/v1/tenant-management/tenants/<tenant_id>` | Read one tenant |
| `PUT` | `/api/v1/tenant-management/tenants/<tenant_id>` | Update one tenant |
| `DELETE` | `/api/v1/tenant-management/tenants/<tenant_id>` | Deactivate or delete one tenant |

### Check health

`GET /health` returns `{"status": "healthy", "timestamp": "<ISO 8601 time>"}`.

### List tenants

`GET /tenants` returns every tenant, newest first:

```json
{
  "tenants": [
    {
      "tenant_id": "tenant_1a2b3c4d",
      "name": "Example Publisher",
      "subdomain": "example",
      "is_active": true,
      "billing_plan": "standard",
      "ad_server": "google_ad_manager",
      "created_at": "2030-01-15T10:00:00+00:00",
      "adapter_configured": true
    }
  ],
  "count": 1
}
```

### Create a tenant

`POST /tenants` takes a JSON body. The API generates the tenant ID itself: `tenant_`
followed by eight hexadecimal characters.

```bash
curl -X POST "https://sales-agent.example.com/api/v1/tenant-management/tenants" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Example Publisher",
    "subdomain": "example",
    "virtual_host": "sales.example.com",
    "ad_server": "google_ad_manager",
    "gam_network_code": "123456",
    "authorized_domains": ["example.com"]
  }'
```

The following table lists the required fields.

| Field | Type | Meaning |
|---|---|---|
| `name` | string | The publisher name |
| `subdomain` | string | A unique identifier for the tenant |
| `virtual_host` | string | The host name that requests for this tenant arrive on, with no scheme and no path |
| `ad_server` | string | The adapter. The API sets adapter fields for `google_ad_manager`, `kevel`, and `triton`, and stores any other value, such as `mock`, without adapter fields. |

The request must also name at least one person or domain that can sign in. Send one or more
of the following fields:

- `authorized_emails` is a list of email addresses.
- `authorized_domains` is a list of email domains.
- `creator_email` is one address. The API adds it to `authorized_emails`.

The following table lists the optional fields.

| Field | Default | Meaning |
|---|---|---|
| `is_active` | `true` | Whether the tenant serves requests |
| `billing_plan` | `"standard"` | A label stored with the tenant |
| `billing_contact` | none | A contact stored with the tenant |
| `enable_axe_signals` | `true` | The tenant's AXE signals setting |
| `human_review_required` | `true` | Whether a media buy waits for manual approval |
| `auto_approve_format_ids` | `["display_300x250_image"]` | Format IDs stored for creative auto-approval |
| `policy_settings` | `{}` | The tenant's advertising policy settings |
| `slack_webhook_url` | none | The Slack webhook for task notifications |
| `slack_audit_webhook_url` | none | The Slack webhook for audit notifications |
| `hitl_webhook_url` | none | A webhook URL stored with the tenant. The sales agent sends nothing to it. |
| `create_default_principal` | `true` | Whether the API also creates a first advertiser |

The API checks the three webhook URLs against the outbound egress policy and refuses a URL
the policy blocks. See [Outbound egress](../security/outbound-egress.md).

The adapter fields depend on `ad_server`. The following list shows them.

- `google_ad_manager`: `gam_network_code`, `gam_refresh_token`, `gam_trafficker_id`,
  `gam_manual_approval_required`.
- `kevel`: `kevel_network_id`, `kevel_api_key`, `kevel_manual_approval_required`.
- `triton`: `triton_station_id`, `triton_api_key`.
- `mock`: No adapter fields.

A successful request returns `201`:

```json
{
  "tenant_id": "tenant_1a2b3c4d",
  "name": "Example Publisher",
  "subdomain": "example",
  "admin_ui_url": "https://sales-agent.example.com/admin/tenant/tenant_1a2b3c4d",
  "default_principal_token": "<token>"
}
```

`default_principal_token` is the API token of the first advertiser. The response is the only
place the token appears, so store it when you receive it. The field is absent when
`create_default_principal` is `false`.

The following table lists the refusals.

| Status | Body | Cause |
|---|---|---|
| `400` | `{"error": "Missing required field: <field>"}` | A required field is absent |
| `400` | `{"error": "<reason>", "field": "virtual_host"}` | `virtual_host` is not a bare host name |
| `400` | An `error` that names the three access fields | The request names no authorized email or domain |
| `400` | An `error` for a blocked URL | A webhook URL fails the egress policy |
| `409` | `{"error": "virtual_host already in use", "field": "virtual_host"}` | Another tenant has the host |
| `409` | `{"error": "Subdomain already exists"}` | Another tenant has the subdomain |
| `500` | `{"error": "Failed to create tenant"}` | An unexpected error; the log has the details |

### Read a tenant

`GET /tenants/<tenant_id>` returns the tenant, its settings, and a summary of its adapter
configuration:

```json
{
  "tenant_id": "tenant_1a2b3c4d",
  "name": "Example Publisher",
  "subdomain": "example",
  "is_active": true,
  "billing_plan": "standard",
  "billing_contact": null,
  "ad_server": "google_ad_manager",
  "created_at": "2030-01-15T10:00:00+00:00",
  "updated_at": "2030-01-15T10:00:00+00:00",
  "settings": {
    "enable_axe_signals": true,
    "authorized_emails": [],
    "authorized_domains": ["example.com"],
    "slack_webhook_url": null,
    "slack_audit_webhook_url": null,
    "hitl_webhook_url": null,
    "auto_approve_formats": ["display_300x250_image"],
    "human_review_required": true,
    "policy_settings": {}
  },
  "adapter_config": {
    "adapter_type": "google_ad_manager",
    "created_at": "2030-01-15T10:00:00+00:00",
    "gam_network_code": "123456",
    "has_refresh_token": false,
    "gam_trafficker_id": null,
    "gam_manual_approval_required": false
  },
  "principals_count": 1
}
```

The response leaves out the GAM refresh token and the Kevel and Triton API keys. It reports
`has_refresh_token` for GAM and `has_api_key` for Kevel and Triton instead. It returns
`slack_webhook_url`, `slack_audit_webhook_url`, and `hitl_webhook_url` as stored.
`adapter_config` is absent when the tenant has no adapter configuration. An unknown tenant
returns `404` with `{"error": "Tenant not found"}`.

### Update a tenant

`PUT /tenants/<tenant_id>` changes the fields the body names and leaves the others as they
are. You can update the following fields:

- `name`, `is_active`, `billing_plan`, and `billing_contact`
- `enable_axe_signals`, `human_review_required`, `auto_approve_format_ids`, and
  `policy_settings`
- `authorized_emails` and `authorized_domains`, where the list in the request replaces the stored list
- `slack_webhook_url`, `slack_audit_webhook_url`, and `hitl_webhook_url`, where an empty
  value clears the URL
- `adapter_config`, an object with the adapter fields from the create request

The API does not change `virtual_host`, `subdomain`, or `ad_server`. To change the virtual
host, use the **Account** section of the tenant's settings page.

A successful request returns `{"tenant_id": ..., "name": ..., "updated_at": ...}`. An
unknown tenant returns `404`.

### Deactivate or delete a tenant

`DELETE /tenants/<tenant_id>` deactivates the tenant by default: it sets `is_active` to
`false` and keeps every row. The response is
`{"message": "Tenant deactivated successfully", "tenant_id": ...}`.

To delete the tenant and its data, send a JSON body with `"hard_delete": true`:

```bash
curl -X DELETE "https://sales-agent.example.com/api/v1/tenant-management/tenants/tenant_1a2b3c4d" \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"hard_delete": true}'
```

A hard delete removes the tenant's adapter configuration, advertisers, products, media buys,
audit log rows, and users, and then the tenant. You cannot undo it. The handler reads the
flag from the JSON body only. A `hard_delete` query parameter has no effect.

## Inventory Sync API

Every route requires the `X-API-Key` header. `POST /trigger/<tenant_id>` and
`POST /tenant/<tenant_id>/orders/sync` refuse a tenant that does not use GAM. The read
routes return what is stored for the ID that you send.

The following table lists the routes.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/sync/trigger/<tenant_id>` | Run an inventory sync |
| `GET` | `/api/sync/status/<sync_id>` | Read one sync job |
| `GET` | `/api/sync/history/<tenant_id>` | List a tenant's sync jobs |
| `GET` | `/api/sync/tenants` | List the GAM tenants and each one's last sync |
| `GET` | `/api/sync/stats` | Summarize sync jobs across tenants |
| `POST` | `/api/sync/tenant/<tenant_id>/orders/sync` | Sync GAM orders and line items |
| `GET` | `/api/sync/tenant/<tenant_id>/orders` | List synced orders |
| `GET` | `/api/sync/tenant/<tenant_id>/orders/<order_id>` | Read one synced order with its line items |
| `GET` | `/api/sync/tenant/<tenant_id>/line-items` | List synced line items |

On an unexpected error, a route returns `500` with `{"error": "<exception text>"}`.

### Run an inventory sync

`POST /trigger/<tenant_id>` runs the sync inside the request and answers when the sync ends.
Give the client a long timeout. The scheduled job allows 300 seconds.

```bash
curl -X POST "https://sales-agent.example.com/api/sync/trigger/tenant_1a2b3c4d" \
  -H "X-API-Key: $SYNC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"sync_type": "full"}'
```

Send a JSON body with `Content-Type: application/json`. An empty object, `{}`, is enough.
The following table lists the body fields. Every field is optional.

| Field | Default | Meaning |
|---|---|---|
| `sync_type` | `"full"` | `full`, `inventory`, `targeting`, or `selective`. The handler runs the same inventory sync for `inventory` and `targeting`. |
| `force` | `false` | Run the sync even when the tenant has a sync job from the current UTC day |
| `sync_types` | `[]` | For `selective`: any of `ad_units`, `placements`, `labels`, `custom_targeting`, and `audience_segments` |
| `custom_targeting_limit` | `1000` | For `selective`, the largest number of values to read per custom targeting key. A `full` sync reads the keys without values. For `inventory` and `targeting`, the sync reads values, at most 1000 per key, unless the field is `0` or `null`. |
| `audience_segment_limit` | none | For `selective`: the largest number of audience segments to read |

Without `force`, the handler looks for a sync job of the tenant that started since 00:00 UTC
and is running or completed. The following table lists every response.

| Status | Body | Cause |
|---|---|---|
| `200` | The sync summary that the GAM adapter returns | The sync ran |
| `200` | `{"message": "Recent sync exists", "sync_id": ..., "completed_at": ...}` | A job already completed today, and `force` is not set |
| `409` | `{"message": "Sync already in progress", "sync_id": ...}` | A job is running, and `force` is not set |
| `400` | `{"error": "Only Google Ad Manager sync is currently supported"}` | The tenant uses another adapter |
| `400` | `{"error": "Adapter not configured"}` | The tenant has no adapter configuration |
| `404` | `{"error": "Tenant not found"}` | No tenant has that ID |
| `500` | `{"sync_id": ..., "status": "failed", "error": ...}` | The sync failed. A `selective` request with no `sync_types`, or an unknown `sync_type`, also ends here. |
| `500` | `{"error": "<parser message>"}` | The request has no JSON body |

A caller that polls should treat the second `200` as "nothing ran". Check for the `message`
field before reading the summary.

### Read sync jobs

`GET /status/<sync_id>` returns one job:

```json
{
  "sync_id": "sync_tenant_1a2b3c4d_full_1894701600",
  "tenant_id": "tenant_1a2b3c4d",
  "adapter_type": "google_ad_manager",
  "sync_type": "full",
  "status": "completed",
  "started_at": "2030-01-15T10:00:00+00:00",
  "triggered_by": "api",
  "triggered_by_id": "api_sync",
  "completed_at": "2030-01-15T10:04:10+00:00",
  "duration_seconds": 250.0,
  "summary": {}
}
```

`status` is `pending`, `running`, `completed`, or `failed`. The response has `completed_at`
and `duration_seconds` after the job ends. It has `summary` when the job stored one, as a
completed job does, and `error` when the job failed. An unknown ID returns `404` with
`{"error": "Sync job not found"}`.

The trigger route also writes a record of its own, `sync_<tenant_id>_<timestamp>`, with
`triggered_by_id` set to `tenant_management_api`. The record stays `pending` when the sync
succeeds and becomes `failed` when the sync fails. The `sync_id` in a successful trigger
response names the job that ran, such as `sync_<tenant_id>_full_<timestamp>`.

`GET /history/<tenant_id>` lists a tenant's jobs, newest first. It takes the query
parameters `limit` (default `10`), `offset` (default `0`), and `status`. The response is
`{"total": ..., "limit": ..., "offset": ..., "results": [...]}`, where each result has the
fields of a job without `tenant_id` and `adapter_type`.

Both routes also return the jobs that the Admin UI starts. You can use them to watch an Admin
UI sync, such as one that an operator starts from the **Inventory** section of the settings
page.

### List tenants and statistics

`GET /tenants` lists the tenants whose ad server is GAM:

```json
{
  "total": 1,
  "tenants": [
    {
      "tenant_id": "tenant_1a2b3c4d",
      "name": "Example Publisher",
      "subdomain": "example",
      "has_adapter_config": true,
      "gam_network_code": "123456",
      "last_sync": {
        "sync_id": "sync_tenant_1a2b3c4d_full_1894701600",
        "completed_at": "2030-01-15T10:04:10+00:00",
        "summary": {}
      }
    }
  ]
}
```

`last_sync` is `null` for a tenant with no completed job.

`GET /stats` returns the following fields:

- `status_counts` has the number of jobs per status that started since 00:00 UTC.
- `recent_failures` has up to five failed jobs from the same period.
- `stale_tenants` lists the GAM tenants that have no completed job, or whose newest completed
  job finished two or more days ago.
- `since` is the start of the period.

### Sync and read orders

`POST /tenant/<tenant_id>/orders/sync` copies the tenant's GAM orders and line items into
the database. It runs inside the request and returns
`{"sync_id": ..., "status": "completed", "summary": ...}`. A tenant with no GAM network code
gets `400`.

`GET /tenant/<tenant_id>/orders` lists the synced orders as `{"total": ..., "orders": [...]}`.
The following list shows its query parameters and the values that each accepts.

- `status`: `DRAFT`, `PENDING_APPROVAL`, `APPROVED`, `PAUSED`, `CANCELED`, or `DELETED`.
- `advertiser_id`: Letters and digits, up to 50 characters.
- `search`: Up to 200 characters.
- `has_line_items`: `true` or `false`.

A value outside these limits returns `400` with an `error` that names the parameter.

`GET /tenant/<tenant_id>/orders/<order_id>` returns one order with its line items, or `404`
with `{"error": "Order not found"}`.

`GET /tenant/<tenant_id>/line-items` lists the synced line items as
`{"total": ..., "line_items": [...]}`. It takes the query parameters `status`,
`line_item_type`, `search`, and `order_id`.

## The scheduled inventory sync

The container runs `scripts/ops/sync_all_tenants.py` on a schedule, every six hours and once
more at 07:00 UTC, unless `SKIP_CRON` is true. The schedule is in the `crontab` file at the
repository root. The script calls `POST /api/sync/trigger/<tenant_id>` with
`{"sync_type": "full"}` for every active GAM tenant that has a network code and a stored
OAuth refresh token. It skips every other tenant.

The script does not send `force`. A run that follows another completed sync on the same UTC
day therefore gets the "Recent sync exists" response and changes nothing.

## Related documentation

- [Environment variables](../deployment/environment-variables.md) lists `SYNC_API_KEY`,
  `TENANT_MANAGEMENT_API_KEY`, and `SKIP_CRON`.
- [Admin UI authentication and permissions](../development/admin-ui-authentication.md)
  describes every access check in the admin application.
- [Inventory and inventory profiles](../user-guide/inventory.md) describes the sync that an
  operator starts from the Admin UI.
- [Admin UI route reference](../development/admin-ui-routes.md) lists every route.
