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

# Required to store secrets, such as a tenant's SSO client secret
ENCRYPTION_KEY=your-fernet-key

# Required for the first sign-in: the deployment's OAuth client and a super admin
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
SUPER_ADMIN_EMAILS=you@example.com

# Optional - AI features
GEMINI_API_KEY=your-key
```

After the first sign-in, each tenant configures its own SSO in the Admin UI. See [Authentication](#authentication).

## Authentication

### Per-tenant SSO (recommended)

Each tenant configures their own SSO provider in the Admin UI (**Users & Access** page). This is the recommended approach for all deployments.

**Setup flow:**

1. Start the system. The first startup creates a default tenant with Setup Mode enabled.
2. Log in with Google as one of the `SUPER_ADMIN_EMAILS` addresses.
3. Configure the tenant's SSO in **Users & Access** with the [SSO quick start](../user-guide/sso-setup.md#quick-start). It supports Google, Microsoft, or any OIDC provider (Okta, Auth0, Keycloak, and others) as Custom OIDC.

See the [SSO setup guide](../user-guide/sso-setup.md) for detailed instructions.

### Setup Mode (per-tenant)

New tenants start with `auth_setup_mode=true`. Setup Mode lets a tenant complete an OIDC
login *before* its SSO is enabled, so you can verify a provider before committing to it.
It grants no credentials of its own — the first administrator signs in with the
deployment's own OAuth (`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` or the generic
`OAUTH_*` variables) plus `SUPER_ADMIN_EMAILS`.

Once SSO is configured and tested, sign in through **Sign in with SSO** and disable Setup Mode from the Users & Access page. After that, **Sign in with SSO** works only while the tenant's SSO is enabled.

### Deployment OAuth

These variables configure the deployment's own OAuth client. The Admin UI signs people in through it unless a tenant's own SSO handles the sign-in. The first administrator of a tenant in Setup Mode signs in through it, and per-tenant SSO doesn't replace it.

The following variables configure the client:

| Variable | Default | Description |
|----------|---------|-------------|
| `OAUTH_DISCOVERY_URL` | - | OIDC discovery URL of the provider |
| `OAUTH_CLIENT_ID` | - | OAuth client ID |
| `OAUTH_CLIENT_SECRET` | - | OAuth client secret |
| `OAUTH_SCOPES` | `openid email profile` | Scopes to request with the `OAUTH_*` credentials |
| `OAUTH_PROVIDER` | `google` | Provider name. Without `OAUTH_DISCOVERY_URL`, `google` or `microsoft` selects that provider's discovery URL |
| `GOOGLE_CLIENT_ID` | - | Google OAuth client ID, read when the `OAUTH_*` credentials don't form a complete configuration |
| `GOOGLE_CLIENT_SECRET` | - | Google OAuth client secret, read with `GOOGLE_CLIENT_ID` |
| `GOOGLE_OAUTH_REDIRECT_URI` | - | Callback URL that the deployment's sign-in sends to the provider. Register the same URL with the OAuth client |

The Admin UI uses the first complete configuration in the following order:

1. `OAUTH_DISCOVERY_URL`, `OAUTH_CLIENT_ID`, and `OAUTH_CLIENT_SECRET`
2. `OAUTH_CLIENT_ID` and `OAUTH_CLIENT_SECRET`, with `OAUTH_PROVIDER` set to `google` or `microsoft`
3. `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, the older Google-only spelling
4. A `client_secret.json` file of Google OAuth credentials in the application's root directory

Without `GOOGLE_OAUTH_REDIRECT_URI`, the global sign-in builds the callback URL from the
request's host. In production, unless `SKIP_NGINX` is set, the callback is always under
`/admin`. Otherwise it keeps the path that the sign-in starts from, so a deployment whose
Admin UI answers under `/admin/` and at the root, such as the Docker Compose stack, needs
both `<host>/admin/auth/google/callback` and `<host>/auth/google/callback` registered with
the OAuth client. In production, a tenant's sign-in uses
`https://<SALES_AGENT_DOMAIN>/admin/auth/google/callback`. Outside production, a tenant's
sign-in ignores the setting and uses the request's host.

### Super admin access control

A super admin reaches every tenant in the Admin UI. The following variables name the super admins:

| Variable | Default | Description |
|----------|---------|-------------|
| `SUPER_ADMIN_EMAILS` | - | Comma-separated super admin emails |
| `SUPER_ADMIN_DOMAINS` | - | Comma-separated email domains whose users are super admins |

The Admin UI checks these variables before the `super_admin_emails` and `super_admin_domains` rows of the `TenantManagementConfig` table.

`scripts/setup/init_database.py` copies these variables into those two rows when you run it. The script prefers `TENANT_MANAGEMENT_EMAILS` over `SUPER_ADMIN_EMAILS`, and `TENANT_MANAGEMENT_DOMAINS` over `SUPER_ADMIN_DOMAINS`, when they are set.

> **Note**: Per-tenant SSO and the **Users & Access** page control who reaches one tenant. Neither grants super admin access.

> Super admin is granted only to a session from the deployment's own sign-in (Google or the `OAUTH_*` provider above). A session from a tenant's own SSO is limited to that tenant and is never a super admin, even for an address listed here.

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

AI features (advertising policy checks, creative review, product ranking, AI order naming) are configured **per-tenant** in the Admin UI under Settings → Integrations → AI Services. A tenant can use any supported provider (Google Gemini, Anthropic, OpenAI and others); a key stored in the legacy per-tenant Gemini column still works.

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
| `SUPER_ADMIN_DOMAIN` | - | Email domain that sign-in treats as super admin. The access checks ignore it, and so does the page that lists every tenant, so grant super admin access with `SUPER_ADMIN_DOMAINS`, as [Super admin access control](#super-admin-access-control) describes. No tenant can authorize this domain, in any spelling, on the settings page or the **Users & Access** page; the **Account** section of the settings page also refuses an address in it |
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
| `CREATE_DEMO_TENANT` | `false` | **Local testing only.** Creates a "Demo Sales Agent" tenant with the mock adapter. Do NOT use in production. |
| `CREATE_SAMPLE_DATA` | `false` | Create sample products, media buys, and related records (requires the demo tenant) |

### Security

| Variable | Default | Description |
|----------|---------|-------------|
| `ENCRYPTION_KEY` | - | Fernet key for encrypting stored secrets (OIDC client secrets, API keys, GAM service account JSON and GAM OAuth refresh tokens). The container refuses to start without it, and the application refuses to encrypt or decrypt without it, so every deployment with a GAM tenant needs it, whichever GAM authentication method the tenant uses. The database migration that encrypts existing GAM refresh tokens stops if they exist and this is not set. Keep it stable: changing it makes the stored secrets unreadable. Generate with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |
| `FLASK_SECRET_KEY` | auto-generated at startup | Flask session secret. Set it explicitly so sessions survive restarts and work across multiple processes. |

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
- `GAM_OAUTH_CLIENT_ID`, `GAM_OAUTH_CLIENT_SECRET` (for GAM integration)
- `GOOGLE_APPLICATION_CREDENTIALS_JSON` (for GAM service accounts)
- `OAUTH_CLIENT_SECRET` or `GOOGLE_CLIENT_SECRET` (for the deployment's own sign-in)
- `APPROXIMATED_API_KEY`
- `FLASK_SECRET_KEY`

> **Note**: Each tenant's own SSO credentials live in the database, encrypted with `ENCRYPTION_KEY`. You enter them on the Users & Access page.

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
