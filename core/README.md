# core/ — greenfield rebuild on adcp 4.3

Status: **skeleton**. Live wiring lands after the v3→v4 migration on `src/` (141 findings, see codemod report).

## Why this exists

Replace the hand-rolled MCP/A2A/nginx/wrapper machinery in `src/` with the
framework primitives shipped in [`adcp>=4.3`](https://github.com/adcontextprotocol/adcp-client-python):

| salesagent today (`src/`)                            | greenfield (`core/`)                              |
|------------------------------------------------------|---------------------------------------------------|
| `src/core/main.py` MCP wrappers + `_impl()` shims    | subclass `adcp.decisioning.DecisioningPlatform`   |
| `src/core/tools.py` A2A `*_raw` functions            | same handler, served by `serve(transport="a2a")`  |
| `src/core/domain_routing.py` (~250 LOC nginx routing)| `adcp.server.SubdomainTenantMiddleware`           |
| `src/adapters/__init__.py` `ADAPTER_REGISTRY` dict   | `adcp.decisioning.PlatformRouter`                 |
| `src/core/auth.py` + `resolved_identity.py`          | `core/stores/accounts.py` AccountStore impl       |
| Custom webhook delivery + retry                      | `adcp.webhook_supervisor_pg.PgWebhookDeliverySupervisor` |
| `tests/bdd/`, structural guards for transport boundary | active `tests/unit/`, `tests/integration/`, and `tests/e2e/` coverage |

The framework's typed handler signatures *are* the transport boundary — no
guard tests required. The `tenant_router.py` docstring upstream literally
says it's the migration target for salesagent's `domain_routing.py`.

## ORM is shared with `src/`

`core/stores/*.py` import the existing SQLAlchemy models from
`src.core.database.models`. Both stacks read/write the same Postgres tables
through one source of truth. Migrations stay in `alembic/`. As port-back
progresses, `src/` shrinks; the ORM and migrations don't move.

## Layout

```
core/
├── main.py              # PlatformRouter + serve() entrypoint
├── tenancy.py           # SubdomainTenantRouter backed by Tenant ORM
├── platforms/
│   ├── mock.py          # DecisioningPlatform subclass (first milestone)
│   ├── gam.py           # ported later
│   └── kevel.py         # ported later
├── stores/
│   ├── accounts.py      # AccountStore impl
│   ├── media_buy.py     # MediaBuyStore (targeting_overlay echo)
│   ├── tenants.py       # tenant lookup for the router
│   └── audit.py         # AuditSink over audit_logs table
└── management_api.py    # FastAPI: tenant/principal/token CRUD (replaces /admin)
```

## Milestones

- **M1 — first runnable.** `MockSellerPlatform` answers `get_products` end-to-end.
  Subdomain routing works. `get_products` dispatch is covered by the active
  unit test suite.
- **M2 — full mock seller.** Pass the entire `media_buy_seller` storyboard
  (9 steps) including `create_media_buy`, `sync_creatives`, delivery polling.
- **M3 — port GAM adapter.** Subclass `DecisioningPlatform` with the
  existing GAM client + reporting code. Run side-by-side with `src/` against
  the same DB.
- **M4 — port admin operations.** Replace `src/admin` Flask UI with the
  management API (humans hit FastAPI directly; no Google OAuth in v1, just
  signed tokens).
- **M5 — retire `src/`.** Once every adapter and auth flow runs through
  `core/`, delete the legacy tree.

## What's deliberately deferred

- ProposalManager (in flight in `src/`); the framework's `proposal.py` ships
  builders — port the in-flight work to land upstream rather than duplicating
- A2A transport (one-line flip via `serve(transport="a2a")` once MCP is solid)
- Workflow engine (revisit whether the framework's task registry is enough)
- Approximated DNS automation (not needed if we ditch nginx and use a single
  wildcard cert behind a load balancer)

## How to follow along

The codemod report at `/tmp/codemod-report.json` lists every v3→v4 issue in
`src/`. The current `pyproject.toml` pins `adcp` to github main so all
4.3.0+ surfaces (`PlatformRouter`, `ProposalManager`, etc.) are available.

## Live in dev

`docker compose up -d` brings the full stack up. To make `core/main.py`
reachable from the host (so `npx @adcp/sdk@latest` etc. can connect),
create a local `docker-compose.override.yml` (gitignored):

```yaml
services:
  adcp-server:
    ports:
      - "3001:3001"
```

Then `docker compose up -d adcp-server` (recreates the container with
the port mapped). Connect from the host using a tenant subdomain:

```bash
npx @adcp/sdk@latest http://wonderstruck.localhost:3001/mcp \
  get_products '{"account":{"account_id":"wonderstruck:demo"},
                 "buying_mode":"brief","brief":"display ads"}'
```

To run `core/main.py` alongside the legacy server:

```bash
docker compose exec -d adcp-server bash -c \
  'ADCP_PORT=3001 nohup python -m core.main > /tmp/core-main.log 2>&1 &'
```

Then inside the container:

```bash
curl -X POST http://localhost:3001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Host: default.localhost" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
    "name":"get_products","arguments":{
      "account":{"account_id":"default:demo"},
      "buying_mode":"brief",
      "brief":"display ads",
      "promoted_offering":"shoes"
    }
  }}'
```

The Host header drives `SubdomainTenantMiddleware` to resolve `default`
from the `tenants` table. Products come back from the real Postgres rows.

## Auth status (M1 vs M2)

M1's `SalesagentAccountStore` resolves tenants via:
1. Subdomain contextvar (set by `SubdomainTenantMiddleware`), or
2. Explicit `account.account_id = "<tenant_id>:<rest>"` prefix (storyboard
   convention, also what `MultiTenantAccountStore` does in the framework's
   `multi_platform_seller` example).

It does **not** yet validate the `x-adcp-auth` token against the
`principals.access_token` column the way the legacy server does. M2 wires
this via:

- A small Starlette middleware that reads `x-adcp-auth`, looks up
  `Principal.access_token`, and sets `auth_info.principal = principal_id`
  on the request-scoped contextvar.
- A `context_factory` passed to `serve()` that reads that contextvar to
  populate `ToolContext.auth_info`.
- The framework's `FromAuthAccounts` then resolves principal → account.

Until then, `core/main.py` works with explicit-prefix account refs (which
is what storyboards use anyway), and the legacy server continues to
serve the token-authenticated path.
