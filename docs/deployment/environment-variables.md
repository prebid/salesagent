# Environment variables reference

Complete reference for all environment variables supported by the Prebid Sales Agent.

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

# Optional - AI features
GEMINI_API_KEY=your-key
```

Authentication is configured **per-tenant** in the Admin UI. No OAuth environment variables are required.

## Authentication

### Per-tenant SSO (recommended)

Each tenant configures their own SSO provider in the Admin UI (**Users & Access** page). This is the recommended approach for all deployments.

**Setup flow:**

1. Start the system. The first startup creates a default tenant with Setup Mode enabled.
2. Log in with test credentials (Setup Mode enables them for new tenants).
3. Configure SSO in **Users & Access** - supports Google, Microsoft, or any OIDC provider (Okta, Auth0, Keycloak, and others) as Custom OIDC.
4. Test your SSO login.
5. Disable Setup Mode once SSO is working.

See the [SSO setup guide](../user-guide/sso-setup.md) for detailed instructions.

### Setup Mode (per-tenant)

New tenants start with `auth_setup_mode=true`, which enables test credentials:

- Email: `test_super_admin@example.com`
- Password: `test123`

Once SSO is configured and tested, disable Setup Mode from the Users & Access page. After that, only SSO authentication works for that tenant.

### Legacy: global test mode

| Variable | Default | Description |
|----------|---------|-------------|
| `ADCP_AUTH_TEST_MODE` | `false` | Enable test authentication globally. **Deprecated - use per-tenant Setup Mode instead.** |

### Legacy: environment variable OAuth

These variables configure a **global** OAuth provider shared by all tenants. For new deployments, use per-tenant SSO configuration instead.

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_CLIENT_ID` | - | Google OAuth client ID (legacy) |
| `GOOGLE_CLIENT_SECRET` | - | Google OAuth client secret (legacy) |
| `OAUTH_DISCOVERY_URL` | - | OIDC discovery URL (legacy) |
| `OAUTH_CLIENT_ID` | - | OAuth client ID (legacy) |
| `OAUTH_CLIENT_SECRET` | - | OAuth client secret (legacy) |
| `OAUTH_SCOPES` | `openid email profile` | OAuth scopes to request |
| `OAUTH_PROVIDER` | `google` | Provider name for display |

### Legacy: super admin access control

| Variable | Default | Description |
|----------|---------|-------------|
| `SUPER_ADMIN_EMAILS` | - | Comma-separated super admin emails. **Deprecated - use per-tenant user management.** |
| `SUPER_ADMIN_DOMAINS` | - | Comma-separated domains for admin access. **Deprecated.** |

> **Note**: Per-tenant SSO configuration replaces `SUPER_ADMIN_EMAILS`. Users are managed per-tenant on the Users & Access page, with authorized emails and domains configured per-tenant.

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
| `ADCP_MULTI_TENANT` | `false` | Enable multi-tenant mode with subdomain routing |
| `SALES_AGENT_DOMAIN` | - | Base domain for tenant subdomains (for example, `sales-agent.example.com`). Also scopes session cookies across subdomains. |
| `ADMIN_DOMAIN` | - | Domain where the Admin UI is accessible (for example, `admin.sales-agent.example.com`) |
| `SUPER_ADMIN_DOMAIN` | - | Email domain whose users get super admin access |

### SSO requirements by deployment mode

The SSO requirement varies based on deployment mode:

- **Single-tenant mode** (default): SSO is **critical** - required before accepting orders. Each deployment needs its own authentication.
- **Multi-tenant mode** (`ADCP_MULTI_TENANT=true`): SSO is **optional** per-tenant. The platform manages authentication centrally, so individual tenants can skip SSO configuration.

---

## Environment and deployment

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | `development` | `development` (strict validation) or `production` (lenient) |
| `PRODUCTION` | `false` | Set to `true` for production deployments |
| `ADMIN_UI_URL` | `http://localhost:8001` | Public URL for the Admin UI (used in notifications) |

### Demo data

| Variable | Default | Description |
|----------|---------|-------------|
| `CREATE_DEMO_TENANT` | `false` | **Local testing only.** Creates a "Demo Sales Agent" tenant with the mock adapter. Do NOT use in production. |
| `CREATE_SAMPLE_DATA` | `false` | Create sample products, media buys, and related records (requires the demo tenant) |

### Security

| Variable | Default | Description |
|----------|---------|-------------|
| `ENCRYPTION_KEY` | - | Fernet key for encrypting stored secrets (OIDC client secrets, API keys). The application refuses to encrypt or decrypt without it. Generate with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |
| `FLASK_SECRET_KEY` | auto-generated at startup | Flask session secret. Set it explicitly so sessions survive restarts and work across multiple processes. |
| `WEBHOOK_SECRET` | - | Secret for verifying incoming webhooks |

---

## External integrations

| Variable | Default | Description |
|----------|---------|-------------|
| `APPROXIMATED_API_KEY` | - | Approximated proxy service API key |
| `APPROXIMATED_BACKEND_URL` | `adcp-sales-agent.fly.dev` | Backend address that Approximated proxies custom domains to |

---

## Development and debugging

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_DEBUG` | `0` | Enable Flask debug mode |
| `FLASK_ENV` | `production` | Flask environment |
| `ADCP_DRY_RUN` | `false` | Run operations without making actual changes |
| `ADCP_TESTING` | `false` | Testing mode: serve checked-in reference creative formats instead of calling external services |

### Service startup

| Variable | Default | Description |
|----------|---------|-------------|
| `ADCP_SALES_PORT` | `8080` | Port the unified application listens on (nginx proxies to it) |
| `SKIP_NGINX` | `false` | Skip nginx in deployment scripts |
| `SKIP_CRON` | `false` | Skip cron job scheduling |

`CONDUCTOR_PORT` (default `8000`) is read by `docker-compose.yml` only - it sets the host port the nginx proxy publishes, which is useful when running multiple worktrees.

---

## Categorized summary

### Secrets

Set these through your platform's secret store (for example, `fly secrets set`) or a secure vault - never in config files:

- `DATABASE_URL`
- `ENCRYPTION_KEY`
- `GAM_OAUTH_CLIENT_ID`, `GAM_OAUTH_CLIENT_SECRET` (for GAM integration)
- `GOOGLE_APPLICATION_CREDENTIALS_JSON` (for GAM service accounts)
- `APPROXIMATED_API_KEY`
- `WEBHOOK_SECRET`
- `FLASK_SECRET_KEY`

> **Note**: Admin OAuth credentials (`GOOGLE_CLIENT_ID` and the other legacy OAuth variables) are configured per-tenant in the Admin UI instead of environment variables.

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
- [Multi-tenant setup](multi-tenant.md) - subdomain routing and per-tenant domains
- [Security and authentication](../security.md) - how secrets and sessions are handled
- [Architecture guide](../development/architecture.md) - deployment topology and component map
