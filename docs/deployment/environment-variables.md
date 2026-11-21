# Environment variables reference

Complete reference for all environment variables supported by the Prebid Sales Agent.

The environment is read once, when the application is composed, into the typed settings
object in `src/core/config.py`. Every variable below is a field there, and the code reads
named facts off that object rather than the environment: a request never asks what
environment it is in. If a variable is missing from `src/core/config.py`, nothing reads it.

## Contents

- [Quick start](#quick-start)
- [Authentication](#authentication)
- [Database](#database)
- [AI features](#ai-features)
- [Google Ad Manager (GAM)](#google-ad-manager-gam)
- [Multi-tenant mode](#multi-tenant-mode)
- [Environment and deployment](#environment-and-deployment)
- [External integrations](#external-integrations)
- [Development and debugging](#development-and-debugging)
- [Categorized summary](#categorized-summary)
- [Related documentation](#related-documentation)

## Quick start

For a minimal working deployment:

```bash
# Required
DATABASE_URL=postgresql://user:password@host:5432/adcp
ENCRYPTION_KEY=your-fernet-key   # the image entrypoint refuses to start without it; see Security

# Required for the first sign-in to the Admin UI (there is no password login)
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
SUPER_ADMIN_EMAILS=you@example.com

# Optional - AI features
GEMINI_API_KEY=your-key
```

Each tenant can then configure its own SSO in the Admin UI, but the first administrator
signs in through the deployment's OAuth client (`GOOGLE_*`, or the generic `OAUTH_*`
variables) and `SUPER_ADMIN_EMAILS`. The [quickstart](../quickstart.md) has the Google
Cloud Console steps and the redirect URIs to register.

## Authentication

### Per-tenant SSO (recommended)

Each tenant configures their own SSO provider in the Admin UI (**Users & Access** page). This is the recommended approach for all deployments.

**Setup flow:**

1. Start the system. The first startup creates a default tenant with Setup Mode enabled.
2. Log in with Google as one of the `SUPER_ADMIN_EMAILS` addresses.
3. Configure SSO in **Users & Access** - supports Google, Microsoft, or any OIDC provider (Okta, Auth0, Keycloak, and others) as Custom OIDC.
4. Test your SSO login.
5. Disable Setup Mode once SSO is working.

See the [SSO setup guide](../user-guide/sso-setup.md) for detailed instructions.

### Setup Mode (per-tenant)

New tenants start with `auth_setup_mode=true`. Setup Mode lets a tenant complete an OIDC
login *before* its SSO is enabled, so you can verify a provider before committing to it.
It grants no credentials of its own — the first administrator signs in with the
deployment's own OAuth (`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` or the generic
`OAUTH_*` variables) plus `SUPER_ADMIN_EMAILS`.

Once SSO is configured and tested, disable Setup Mode from the Users & Access page. After that, only SSO authentication works for that tenant.

### Deployment OAuth client and super admins

These variables bootstrap the first sign-in. The deployment's OAuth client is the only way
into the Admin UI until a tenant has its own SSO, and the super admins are the accounts it
lets in. Per-tenant SSO and per-tenant users (**Users & Access**) come after that first
sign-in; they do not replace these variables. Configure one OAuth provider: `GOOGLE_*`, or
the generic `OAUTH_*` variables for any OIDC provider. The redirect URIs to register are in
[quickstart step 2](../quickstart.md#2-create-a-google-oauth-client).

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_CLIENT_ID` | - | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | - | Google OAuth client secret |
| `OAUTH_DISCOVERY_URL` | - | OIDC discovery URL, for a provider other than Google |
| `OAUTH_CLIENT_ID` | - | OIDC client ID |
| `OAUTH_CLIENT_SECRET` | - | OIDC client secret |
| `OAUTH_SCOPES` | `openid email profile` | OAuth scopes to request |
| `OAUTH_PROVIDER` | `google` | Provider name for display |
| `SUPER_ADMIN_EMAILS` | - | Comma-separated emails that may sign in as super admin |
| `SUPER_ADMIN_DOMAINS` | - | Comma-separated email domains whose accounts may sign in as super admin |

---

## Database

### Connection

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | - | **Required.** Full PostgreSQL connection URL |

Or use individual variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | `localhost` | Database host |
| `DB_PORT` | `5432` | Database port |
| `DB_NAME` | `adcp` | Database name |
| `DB_USER` | `adcp` | Database user |
| `DB_PASSWORD` | - | Database password |
| `DB_SSLMODE` | `prefer` | PostgreSQL SSL mode |

### Connection pool

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_QUERY_TIMEOUT` | `30` | Query timeout in seconds |
| `DATABASE_CONNECT_TIMEOUT` | `10` | Connection timeout in seconds |
| `DATABASE_POOL_TIMEOUT` | `30` | Pool checkout timeout in seconds |
| `USE_PGBOUNCER` | `false` | Enable PgBouncer connection pooling mode |

### Migrations

| Variable | Default | Description |
|----------|---------|-------------|
| `SKIP_MIGRATIONS` | `false` | Skip automatic migrations on startup |

---

## AI features

AI features (creative review, product suggestions) are configured **per-tenant** in the Admin UI. Each tenant sets their own Gemini API key.

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | - | Server-wide fallback Gemini API key. Tenants can configure their own keys in the Admin UI. |
| `PYDANTIC_AI_PROVIDER` | `gemini` | AI provider for pydantic-ai features |
| `PYDANTIC_AI_MODEL` | `gemini-2.0-flash` | Model used by pydantic-ai features |

### Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `LOGFIRE_TOKEN` | - | Logfire observability token for AI tracing |

---

## Google Ad Manager (GAM)

For GAM adapter integration:

| Variable | Default | Description |
|----------|---------|-------------|
| `GAM_OAUTH_CLIENT_ID` | - | GAM OAuth client ID (separate from admin OAuth) |
| `GAM_OAUTH_CLIENT_SECRET` | - | GAM OAuth client secret |
| `GCP_PROJECT_ID` | - | GCP project ID for service account management |
| `GOOGLE_APPLICATION_CREDENTIALS` | - | Path to GCP service account JSON file |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | - | GCP service account credentials as JSON string |

---

## Multi-tenant mode

| Variable | Default | Description |
|----------|---------|-------------|
| `ADCP_MULTI_TENANT` | `false` | Enable multi-tenant mode, where the `Host` selects the tenant |
| `SALES_AGENT_DOMAIN` | - | Base domain for tenant subdomains (for example, `sales-agent.example.com`). Also scopes session cookies across subdomains. |
| `ADMIN_DOMAIN` | - | Domain where the Admin UI is accessible (for example, `admin.sales-agent.example.com`) |
| `SUPER_ADMIN_DOMAIN` | - | Email domain whose users get super admin access |
| `TLS_ASK_ENABLED` | `false` | Mount `GET /tls/ask`, the endpoint an on-demand TLS proxy (Caddy) asks before issuing a certificate for a host. Off, the path answers 404. Needed only behind such a proxy; see [Caddy on-demand TLS](multi-tenant.md#caddy-on-demand-tls-no-wildcard-certificate). |

### SSO requirements by deployment mode

The SSO requirement varies based on deployment mode:

- **Single-tenant mode** (default): SSO is **critical** - required before accepting orders. Each deployment needs its own authentication.
- **Multi-tenant mode** (`ADCP_MULTI_TENANT=true`): SSO is **optional** per-tenant. The platform manages authentication centrally, so individual tenants can skip SSO configuration.

---

## Environment and deployment

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | `development` | `development` or `production` |
| `PRODUCTION` | `false` | Set to `true` for production deployments |
| `FLY_APP_NAME` | unset | Set by Fly.io; its presence also marks the deployment as production |
| `ADMIN_UI_URL` | `http://localhost:8001` | Public URL for the Admin UI (used in notifications) |

Any one of `PRODUCTION=true`, `ENVIRONMENT=production` or a `FLY_APP_NAME` makes the
deployment production, and every production-dependent behaviour reads that one answer:
lenient request validation, structured JSON logging, secure session cookies, the trust in
proxy headers, and the absence of verbose auth logging. A publisher's adagents.json is read
in every deployment: properties come only from it.

### Demo data

| Variable | Default | Description |
|----------|---------|-------------|
| `CREATE_DEMO_TENANT` | `false` | Has no effect with `docker-compose.yml` or in single-tenant mode, where the migration step creates the `default` tenant first. The [quickstart](../quickstart.md#about-create_demo_tenant) explains when it applies. Do not use in production. |
| `CREATE_SAMPLE_DATA` | `false` | Create sample products, media buys, and related records. Applies only where `CREATE_DEMO_TENANT` does |

### Security

| Variable | Default | Description |
|----------|---------|-------------|
| `ENCRYPTION_KEY` | - | Fernet key for encrypting stored secrets (OIDC client secrets, API keys, GAM service account JSON and GAM OAuth refresh tokens). The application refuses to encrypt or decrypt without it, so every deployment with a GAM tenant needs it, whichever GAM authentication method the tenant uses. The database migration that encrypts existing GAM refresh tokens stops if they exist and this is not set. Keep it stable: changing it makes the stored secrets unreadable. Generate with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |
| `FLASK_SECRET_KEY` | auto-generated at startup | Flask session secret. Set it explicitly so sessions survive restarts and work across multiple processes. |
| `WEBHOOK_SECRET` | - | Secret for verifying incoming webhooks |

---

## External integrations

| Variable | Default | Description |
|----------|---------|-------------|
| `APPROXIMATED_API_KEY` | - | Approximated proxy service API key |
| `APPROXIMATED_BACKEND_URL` | `adcp-sales-agent.fly.dev` | Backend address that Approximated proxies custom domains to |
| `CREATIVE_AGENT_URL` | - | The MCP endpoint of the operator's creative agent, the URL the app dials and nothing else. Give the endpoint itself: MCP has no path convention, and the AdCP reference creative agent serves MCP only at `<base>/api/creative-agent/mcp`. Unset, the app dials the public agent's endpoint, `https://creative.adcontextprotocol.org/api/creative-agent/mcp`; an agent's identity is never dialled, so setting `CREATIVE_AGENT_ORIGIN` without this variable leaves the app nothing to dial, and every dial to that agent fails as a configuration error. |
| `CREATIVE_AGENT_ORIGIN` | `https://creative.adcontextprotocol.org` | The operator creative agent's identity: an origin, `scheme://host[:port]`. A format reference whose `agent_url` has this origin, whatever its path, is the operator's agent, and the app connects to it through `CREATIVE_AGENT_URL`. The app refuses to start when the value carries a path, query, fragment or userinfo. |

---

## Development and debugging

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_DEBUG` | `0` | Enable Flask debug mode |
| `FLASK_ENV` | `production` | Flask environment |
| `ADCP_TESTING` | `false` | A test deployment. Selects, at composition: the reference-formats creative registry (no creative agent is dialled), the debug and reset routes, loopback webhook targets, the mock ad server's seeded delivery rows, the setup checklist counting the mock adapter as configured, and relaxed brand validation in `get_products`. Each is a named property on the settings; nothing reads the flag itself. |

### Service startup

| Variable | Default | Description |
|----------|---------|-------------|
| `ADCP_SALES_PORT` | `8080` | Port the unified application listens on (nginx proxies to it) |
| `ADCP_SALES_HOST` | `0.0.0.0` | Bind address for `scripts/run_server.py` (production always binds all interfaces) |
| `SKIP_NGINX` | `false` | Skip nginx in deployment scripts |
| `SKIP_CRON` | `false` | Skip cron job scheduling. The scheduled GAM inventory sync authenticates with `SYNC_API_KEY` and exits with an error when it is unset |

Every variable above is read once, by `src/core/config.py`, when a process is composed; an
empty value means unset. Knobs that only the repo's own scripts read (`ADCP_HOME`,
`ADCP_REQ_PATH`, `BDD_LIVENESS_ARTIFACT`, `STORYBOARD_LEDGER_PATH`,
`ALLOW_LIVE_CREATIVE_AGENT`) are fields on `ToolingSettings` in the same module.

`CONDUCTOR_PORT` (default `8000`) is read by `docker-compose.yml` only - it sets the host port the nginx proxy publishes, which is useful when running multiple worktrees.

---

## Categorized summary

### Secrets

Set these through your platform's secret store (for example, `fly secrets set`) or a secure vault - never in config files:

- `DATABASE_URL`
- `ENCRYPTION_KEY`
- `GOOGLE_CLIENT_SECRET` or `OAUTH_CLIENT_SECRET` (the deployment's OAuth client)
- `GAM_OAUTH_CLIENT_ID`, `GAM_OAUTH_CLIENT_SECRET` (for GAM integration)
- `GOOGLE_APPLICATION_CREDENTIALS_JSON` (for GAM service accounts)
- `APPROXIMATED_API_KEY`
- `WEBHOOK_SECRET`
- `FLASK_SECRET_KEY`

> **Note**: The deployment's OAuth client (`GOOGLE_*` or `OAUTH_*`) is set in the environment and is needed for the first sign-in. A tenant's own SSO client is configured in the Admin UI afterwards and stored encrypted with `ENCRYPTION_KEY`.

### Non-sensitive configuration

These can live in `fly.toml`, `docker-compose.yml`, or similar config files:

- `ENVIRONMENT`, `PRODUCTION`
- `ADCP_MULTI_TENANT`, `SALES_AGENT_DOMAIN`, `ADMIN_DOMAIN`
- `ADMIN_UI_URL`
- `CREATE_DEMO_TENANT`
- `SKIP_NGINX`, `SKIP_CRON`

### Variables with sensible defaults

You usually don't need to set the following variables:

- All `DB_*` individual variables (use `DATABASE_URL` instead)
- `ADCP_SALES_PORT` (nginx configuration expects the default)
- `DATABASE_*_TIMEOUT` variables
- `PYDANTIC_AI_*` variables

## Related documentation

- [Single-tenant deployment](single-tenant.md) - the default deployment mode
- [Multi-tenant setup](multi-tenant.md) - host-based tenant routing and per-tenant domains
- [Security and authentication](../security.md) - how secrets and sessions are handled
- [Architecture guide](../development/architecture.md) - deployment topology and component map
