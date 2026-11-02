# Mock adapter

The mock adapter ([`src/adapters/mock_ad_server.py`](../../../src/adapters/mock_ad_server.py))
is a simulated ad server: it implements the full `AdServerAdapter` interface
without external API credentials or network calls. It backs local development,
CI, and most of this repository's test suites, and it can compress a campaign's
delivery timeline so you can test an AI agent's full-lifecycle behavior in
minutes. Its registry key is `mock`, and a tenant selects it like any other
adapter — see [Creating an ad server adapter](../creating-an-adapter.md) for
the interface it implements and
[Adapter pattern](../../development/architecture.md#adapter-pattern) for where
adapters sit in the system.

## Contents

- [Implemented interface](#implemented-interface) — the six adapter methods and what the mock does for each
- [Media buy lifecycle](#media-buy-lifecycle) — creation paths and status progression
- [Targeting behavior](#targeting-behavior) — what the mock accepts and what it deliberately rejects
- [Validation rules](#validation-rules) — the GAM-style checks on create
- [Test scenario keywords](#test-scenario-keywords) — driving rejections, delays, and errors from request fields
- [Human-in-the-loop simulation](#human-in-the-loop-simulation) — sync, async, and mixed approval modes
- [Failure injection from the database](#failure-injection-from-the-database) — the `test_behavior` mechanism for containerized tests
- [Getting started](#getting-started) — tenant setup, tokens, and a first request
- [Configuration reference](#configuration-reference) — all three configuration locations, verified against the code
- [State and limitations](#state-and-limitations)
- [Troubleshooting](#troubleshooting)
- [Related documentation](#related-documentation)

## Implemented interface

`MockAdServer` subclasses `AdServerAdapter` and implements all six of its
abstract methods:

- `create_media_buy` — validates the request, then creates an in-memory
  campaign; honors test keywords and HITL modes.
- `add_creative_assets` — auto-approves creatives by default; keywords or
  approval simulation can reject or hold them.
- `associate_creatives` — records every creative–line-item pairing as a
  success.
- `check_media_buy_status` — derives `pending_start`, `delivering`, or
  `completed` from the flight dates.
- `get_media_buy_delivery` — computes paced delivery metrics from campaign
  progress, with per-package breakdowns.
- `update_media_buy` — persists package budget updates through
  `MediaBuyRepository`.

Beyond the required interface, the mock overrides `get_packages_snapshot`
(near-real-time delivery snapshots with pacing indexes),
`get_available_inventory` (a static publisher-like inventory catalog), and the
configuration UI hooks.

Because those methods cover the full adapter seam, every AdCP tool — product
discovery, media buy creation and update, creative sync, and delivery
reporting — works end to end against a mock tenant over MCP, A2A, and REST.

Three capabilities are worth knowing about:

- **All pricing models**: `cpm`, `vcpm`, `cpcv`, `cpp`, `cpc`, `cpv`, and
  `flat_rate`.
- **Channels**: `display`, `olv`, `streaming_audio`, and `social` by default.
- **Delivery simulation**: time-accelerated delivery with webhooks — see
  [Delivery simulation](delivery-simulation.md).

## Media buy lifecycle

`create_media_buy` routes each request through test-keyword parsing, targeting
and validation checks, and the configured HITL mode before creating the
campaign. The following diagram shows that dispatch.

```mermaid
flowchart TD
    A["create_media_buy"] --> K{"Test keywords in\nbrand domain?"}
    K -->|"[ERROR:...] / [REJECT:...]"| E["Raises AdCPError /\nAdCPMediaBuyRejectedError"]
    K -->|"[QUESTION:...] / [ASYNC]"| P["Returns media_buy_id 'pending'\n(workflow step tracks completion)"]
    K -->|"[HITL:Nm:...]"| S["Configured delay +\nsimulated approval"]
    K -->|"none / [DELAY:N]"| V["Targeting and validation checks"]
    V -->|"unsupported dimension"| U["Raises AdCPCapabilityNotSupportedError"]
    V -->|"validation errors"| W["Raises AdCPValidationError"]
    V --> M{"HITL mode?"}
    M -->|"async"| P
    M -->|"sync"| S
    S --> I
    M -->|"immediate (default)"| I["Creates in-memory campaign;\nstarts delivery simulation if enabled"]
```

After creation, `check_media_buy_status` reports the campaign's status purely
from its flight dates, as the following diagram shows.

```mermaid
stateDiagram-v2
    [*] --> pending_start: media buy created
    pending_start --> delivering: reporting date reaches start_time
    delivering --> completed: reporting date passes end_time
    completed --> [*]
```

## Targeting behavior

The mock deliberately mirrors a real ad server's limits rather than accepting
everything, so you can test the capability-error paths.

The mock accepts these dimensions:

- Geographic targeting: countries, regions, and metros, plus postal-code
  systems (US ZIP and ZIP+4, CA FSA and full, GB outward and full, DE PLZ, FR
  code postal, AU postcode, Nielsen DMA, Eurostat NUTS2, and UK ITL1/ITL2).
- Key-value pairs (AXE integration).
- Media types.

The mock rejects the following dimensions — a package `targeting_overlay` using
any of them raises `AdCPCapabilityNotSupportedError`:

- `device_type_any_of`
- `os_any_of`
- `browser_any_of`
- `content_cat_any_of`
- `keywords_any_of`

Use those five to test how a buyer agent handles a seller that cannot fulfill
a targeting contract.

## Validation rules

`validate_media_buy_request` enforces GAM-style rules and raises
`AdCPValidationError` with GAM-style error strings when any fail:

- The flight start must precede the end, and the end must be in the future.
- Each package's impression goal must not exceed 1,000,000 (100,000,000 for
  the `cpcv`, `cpv`, and `cpp` pricing models).
- The total budget must be greater than zero and at most $1,000,000.

## Test scenario keywords

Requests can carry bracketed keywords that the mock parses
([`src/adapters/test_scenario_parser.py`](../../../src/adapters/test_scenario_parser.py))
to orchestrate deterministic test outcomes — no configuration change needed.

For `create_media_buy`, put keywords in the request's **brand domain** field:

- `[REJECT:reason]` — raises `AdCPMediaBuyRejectedError` with the given reason.
- `[ERROR:message]` — raises `AdCPError` with the given message.
- `[DELAY:N]` — sleeps N seconds before responding.
- `[ASYNC]` — returns a pending response; a workflow step tracks completion.
- `[HITL:Nm:outcome]` — simulates a human approval taking N minutes.
- `[QUESTION:text]` — returns pending, modeling an operation that needs input.

For `sync_creatives`, put keywords in the **creative name**:

- `[APPROVE]` — approves the creative.
- `[REJECT:reason]` — rejects the creative with the given reason.
- `[ASK:field needed]` — holds the creative in `pending`, requesting more
  information.

The mock auto-approves creatives that carry no keyword.

## Human-in-the-loop simulation

The mock simulates human approval workflows, configured per principal under
`platform_mappings.mock.hitl_config`:

```json
{
  "platform_mappings": {
    "mock": {
      "advertiser_id": "mock_adv_123",
      "hitl_config": {
        "enabled": true,
        "mode": "sync",
        "sync_settings": {
          "delay_ms": 2000,
          "streaming_updates": true,
          "update_interval_ms": 500
        },
        "async_settings": {
          "auto_complete": true,
          "auto_complete_delay_ms": 10000,
          "webhook_url": "https://your-app.example/webhooks/hitl",
          "webhook_on_complete": true
        },
        "operation_modes": {
          "create_media_buy": "async"
        },
        "approval_simulation": {
          "enabled": true,
          "approval_probability": 0.8,
          "rejection_reasons": ["Budget exceeds limits", "Invalid targeting"]
        }
      }
    }
  }
}
```

The modes behave as follows:

- **`sync`** — delays the response by `delay_ms` (default 2000), streaming
  progress log updates along the way, then applies approval simulation.
- **`async`** — creates a workflow step and returns a pending response. With
  `auto_complete: true` the step completes itself after
  `auto_complete_delay_ms` (default 10000) and, when `webhook_url` is set,
  notifies it; otherwise a human completes the step.
- **`mixed`** — the global mode, with per-operation overrides in
  `operation_modes` (an operation name mapped to `sync`, `async`, or
  `immediate`).

When `approval_simulation.enabled` is true, each simulated approval succeeds
with probability `approval_probability` (default 0.8) and otherwise rejects
with a random reason from `rejection_reasons`.

## Failure injection from the database

Containerized tests cannot patch an in-process mock, so BDD Given steps
persist failure injection into the `adapter_config` table's `config_json`
under a `test_behavior` key. On the next matching operation the adapter reads
it and raises a typed error:

- `fail_on_create`, `fail_on_update`, and `fail_on_upload` flag which
  operation fails.
- `recovery` selects the exception class by its recovery classification:
  `transient` (`SERVICE_UNAVAILABLE`), `terminal` (`CONFIGURATION_ERROR`), or
  `correctable` (`VALIDATION_ERROR`).
- `error_message` and `error_details` shape the error; the adapter lifts a
  `suggestion` inside `error_details` to the error's top-level suggestion field.

See [Test architecture](../../../tests/CLAUDE.md) for the factories that write
this configuration.

## Getting started

### 1. Create a tenant that uses the mock adapter

```bash
docker compose exec adcp-server python scripts/setup/setup_tenant.py "Test Publisher" \
  --adapter mock \
  --subdomain test-pub
```

This creates the tenant, its mock adapter configuration, currency limits for
USD, EUR, and GBP, and a default principal named `<tenant_id>_default`. The
command prints the principal's access token — copy it. The tenant starts with
no products; create them in the Admin UI (`http://localhost:8000/admin/`)
before calling `get_products`.

### 2. Retrieve a token later

In the Admin UI, open the tenant's advertisers list and copy the API token. The
database holds only a hash of each token, so you cannot read one back from
`principals`: use the token the command printed when the advertiser was
created, or rotate it in the Admin UI.

### 3. Call the server

Over MCP with the Python client:

```python
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

headers = {"Authorization": "Bearer your_principal_token"}
transport = StreamableHttpTransport(url="http://localhost:8000/mcp/", headers=headers)
client = Client(transport=transport)

async with client:
    products = await client.tools.get_products(brief="video ads")
```

Or from the command line:

```bash
uvx adcp http://localhost:8000/mcp/ --auth <principal-token> list_tools
uvx adcp http://localhost:8000/mcp/ --auth <principal-token> get_products '{"brief":"video"}'
```

The A2A server serves the same tools at `http://localhost:8000/a2a`, and the
REST routes serve them at `/api/v1` — see
[A2A and MCP agent flows](../../development/a2a-mcp-agent-flows.md) for why all
three transports process a request the same way, and what differs in their
framing.

To write tests against a mock tenant — the harness, factories, and fixtures
this repository expects — read [Test architecture](../../../tests/CLAUDE.md)
first, then [End-to-end testing](../../development/e2e-testing.md) for the
containerized stack the e2e suites drive.

## Configuration reference

Mock adapter configuration lives in three places.

### Adapter configuration (per tenant)

The `adapter_config` table row with `adapter_type = 'mock'` carries:

- `mock_manual_approval_required` — routes operations through manual approval.
  It is the whole of the mock adapter's connection configuration; the
  `dry_run` field it once carried is gone, along with the column behind it,
  because a preview is a rolled-back unit of work rather than an adapter mode
  (`src/adapters/mock_ad_server.py:82-89`).
- `config_json.test_behavior` — the
  [failure-injection block](#failure-injection-from-the-database).

### Product configuration

The mock product configuration page
(`/adapters/mock/config/<tenant_id>/<product_id>` in the Admin UI) stores
simulation settings on the product's `implementation_config`:

| Key | Default | Accepted range |
|-----|---------|----------------|
| `daily_impressions` | `100000` | ≥ 0 |
| `fill_rate` | `85` | 0–100 |
| `ctr` | `0.5` | 0–100 |
| `viewability_rate` | `70` | 0–100 |
| `latency_ms` | `50` | 0–60000 |
| `error_rate` | `0.1` | 0–100 |
| `test_mode` | `"normal"` | `normal`, `high_demand`, `degraded`, `outage` |
| `price_variance` | `10` | 0–100 |
| `seasonal_factor` | `1.0` | 0.1–10.0 |
| `verbose_logging` | `false` | boolean |
| `predictable_ids` | `false` | boolean |
| `delivery_simulation` | `{"enabled": false, ...}` | see [Delivery simulation](delivery-simulation.md) |

### Principal configuration

The principal's `platform_mappings.mock` block carries the `advertiser_id`
and the [HITL configuration](#human-in-the-loop-simulation).

## State and limitations

- **Campaign state is in-memory.** Created campaigns live in a class-level
  dict (`MockAdServer._media_buys`) and disappear on restart. Tests that need
  an empty store call `MockAdServer._media_buys.clear()`. Package budget
  updates are the exception: `update_media_buy` writes them to the database.
- **The mock synthesizes delivery metrics.** Spend paces evenly with random
  variance and impressions assume a fixed $10 CPM — the mock serves no ads.
- **No external network calls** apart from the webhooks the delivery
  simulation and async HITL completion send.
- **No rate limiting** and no real ad server authentication.

Use a real adapter (GAM, Kevel, Triton, or Broadstreet) for staging
validation, production, and anything adapter-specific — see
[Adapter overview](../README.md).

## Troubleshooting

### The tenant does not use the mock adapter

Check which adapter the tenant selects — `adapter_config.adapter_type` is the
source of truth, with `tenants.ad_server` as fallback:

```bash
docker compose exec postgres psql -U adcp_user -d adcp \
  -c "SELECT t.tenant_id, t.ad_server, a.adapter_type
      FROM tenants t LEFT JOIN adapter_config a USING (tenant_id)
      WHERE t.tenant_id = 'your-tenant';"
```

### Targeting fails with a capability error

That is intended behavior, not a bug: the mock rejects device, OS, browser,
content-category, and keyword targeting to mirror real ad server limits. See
[Targeting behavior](#targeting-behavior).

### HITL settings have no effect

Confirm the principal's configuration exists and is well-formed:

```bash
docker compose exec postgres psql -U adcp_user -d adcp \
  -c "SELECT platform_mappings FROM principals WHERE principal_id = 'your-principal';"
```

The block must live at `platform_mappings.mock.hitl_config` with
`"enabled": true`.

### Delivery webhooks are not firing

See [Delivery simulation § Troubleshooting](delivery-simulation.md#troubleshooting).

## Related documentation

- [Delivery simulation](delivery-simulation.md) — accelerated delivery and webhooks
- [Creating an ad server adapter](../creating-an-adapter.md) — the `AdServerAdapter` contract the mock implements
- [Architecture guide](../../development/architecture.md) — where adapters sit in the system
- [Test architecture](../../../tests/CLAUDE.md) — writing tests against the mock adapter
- [End-to-end testing](../../development/e2e-testing.md) — the containerized stack and suites
- [Troubleshooting](../../development/troubleshooting.md) — general debugging
- [AdCP spec version](../../adcp-spec-version.md) — the pinned protocol version
