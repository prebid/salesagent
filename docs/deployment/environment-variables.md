# Environment Variables Reference

Complete reference for all environment variables supported by the Prebid Sales Agent.

## Quick Start

For a minimal working deployment:

```bash
# Required
DATABASE_URL=postgresql://user:password@host:5432/adcp

# Optional - AI features
GEMINI_API_KEY=your-key
```

Authentication is configured **per-tenant** via the Admin UI. No OAuth environment variables required.

## Authentication

### Per-Tenant SSO (Recommended)

Each tenant configures their own SSO provider via the Admin UI (**Users & Access** page). This is the recommended approach for all deployments.

**Setup Flow:**
1. Start with `CREATE_DEMO_TENANT=true` for initial access
2. Log in with test credentials (Setup Mode is enabled by default for new tenants)
3. Configure SSO in **Users & Access** - supports Google, Microsoft, Okta, Auth0, Keycloak, or any OIDC provider
4. Test your SSO login
5. Disable Setup Mode once SSO is working

See [SSO Setup Guide](../user-guide/sso-setup.md) for detailed instructions.

### Setup Mode (Per-Tenant)

New tenants start with `auth_setup_mode=true`, which enables test credentials:
- Email: `test_super_admin@example.com`
- Password: `test123`

Once SSO is configured and tested, disable Setup Mode from the Users & Access page. After that, only SSO authentication works for that tenant.

### Legacy: Global Test Mode

| Variable | Default | Description |
|----------|---------|-------------|
| `ADCP_AUTH_TEST_MODE` | `false` | Enable test authentication globally. **Deprecated - use per-tenant Setup Mode instead.** |

### Legacy: Environment Variable OAuth

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

### Legacy: Super Admin Access Control

| Variable | Default | Description |
|----------|---------|-------------|
| `SUPER_ADMIN_EMAILS` | - | Comma-separated super admin emails. **Deprecated - use per-tenant user management.** |
| `SUPER_ADMIN_DOMAINS` | - | Comma-separated domains for admin access. **Deprecated.** |

> **Note**: Per-tenant SSO configuration replaces `SUPER_ADMIN_EMAILS`. Users are managed per-tenant via the Users & Access page, with authorized emails/domains configured per-tenant.

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

### Connection Pool

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

## AI Features

AI features (creative review, product suggestions) are configured **per-tenant** in the Admin UI. Each tenant sets their own Gemini API key.

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

## Multi-Tenant Mode

| Variable | Default | Description |
|----------|---------|-------------|
| `ADCP_MULTI_TENANT` | `false` | Enable multi-tenant mode with subdomain routing |
| `SALES_AGENT_DOMAIN` | - | Base domain for tenant subdomains (e.g., `sales-agent.example.com`) |
| `BASE_DOMAIN` | - | Top-level domain for cookies (e.g., `example.com`) |

### SSO Requirements by Deployment Mode

The SSO requirement varies based on deployment mode:

- **Single-tenant mode** (default): SSO is **critical** - required before accepting orders. Each deployment needs its own authentication.
- **Multi-tenant mode** (`ADCP_MULTI_TENANT=true`): SSO is **optional** per-tenant. The platform manages authentication centrally, so individual tenants can skip SSO configuration.

---

## Environment & Deployment

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | `development` | `development` (strict validation) or `production` (lenient) |
| `PRODUCTION` | `false` | Set to `true` for production deployments |
| `ADMIN_UI_URL` | `http://localhost:8000` | Public URL for Admin UI (used in notifications) |

### Demo Data

| Variable | Default | Description |
|----------|---------|-------------|
| `CREATE_DEMO_TENANT` | `false` | **Local testing only.** Creates "Default Publisher" tenant with mock adapter. Do NOT use in production. |
| `CREATE_SAMPLE_DATA` | `false` | Create sample products, media buys, etc. |

### Security

| Variable | Default | Description |
|----------|---------|-------------|
| `ENCRYPTION_KEY` | auto-generated | Key for encrypting sensitive data in database |
| `FLASK_SECRET_KEY` | dev key | Flask session secret (auto-generated in production) |
| `WEBHOOK_SECRET` | - | Secret for verifying incoming webhooks |

---

## External Integrations

| Variable | Default | Description |
|----------|---------|-------------|
| `APPROXIMATED_API_KEY` | - | Approximated proxy service API key |

---

## Development & Debugging

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_DEBUG` | `0` | Enable Flask debug mode |
| `FLASK_ENV` | `production` | Flask environment |
| `ADCP_DRY_RUN` | `false` | Run operations without making actual changes |
| `ADCP_TESTING` | `false` | Enable testing mode (internal) |

### Service Startup

| Variable | Default | Description |
|----------|---------|-------------|
| `SKIP_NGINX` | `false` | Skip nginx in deployment scripts |
| `SKIP_CRON` | `false` | Skip cron job scheduling |

---

## Categorized Summary

### Secrets (set via `fly secrets set` or secure vault)

These contain sensitive credentials and should never be in config files:

- `DATABASE_URL`
- `ENCRYPTION_KEY`
- `GAM_OAUTH_CLIENT_ID`, `GAM_OAUTH_CLIENT_SECRET` (for GAM integration)
- `GOOGLE_APPLICATION_CREDENTIALS_JSON` (for GAM service accounts)
- `APPROXIMATED_API_KEY`
- `WEBHOOK_SECRET`
- `FLASK_SECRET_KEY`

> **Note**: Admin OAuth credentials (`GOOGLE_CLIENT_ID`, etc.) are now configured per-tenant via the Admin UI instead of environment variables.

### Environment Variables (can be in fly.toml, docker-compose, etc.)

Non-sensitive configuration:

- `ENVIRONMENT`, `PRODUCTION`
- `ADCP_MULTI_TENANT`, `BASE_DOMAIN`, `SALES_AGENT_DOMAIN`
- `ADMIN_UI_URL`
- `CREATE_DEMO_TENANT`
- `SKIP_NGINX`, `SKIP_CRON`

### Variables with Sensible Defaults (usually don't need to set)

- All `DB_*` individual variables (use `DATABASE_URL` instead)
- All `*_PORT` variables (hardcoded in nginx)
- `DATABASE_*_TIMEOUT` variables
- `PYDANTIC_AI_*` variables
