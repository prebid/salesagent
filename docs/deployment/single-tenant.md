# Single-tenant deployment

Single-tenant mode is the default and recommended for most publishers deploying their own Prebid Sales Agent.

## Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Docker images](#docker-images)
- [Environment variables](#environment-variables)
- [Docker Compose deployment](#docker-compose-deployment)
- [Services and ports](#services-and-ports)
- [Docker management](#docker-management)
- [Database migrations](#database-migrations)
- [First-time setup](#first-time-setup)
- [Custom domain configuration](#custom-domain-configuration)
- [Health monitoring](#health-monitoring)
- [Security checklist](#security-checklist)
- [Backup and recovery](#backup-and-recovery)
- [Next steps](#next-steps)

## Overview

**Single-tenant mode:**

- One publisher per deployment
- Simple path-based routing (`/admin`, `/mcp`, `/a2a`)
- No subdomain complexity
- Works with any custom domain

The [architecture guide](../development/architecture.md#deployment-topology) shows the deployment topology (nginx in front of the unified application and PostgreSQL) and the full component map. The [request lifecycle](../development/request-lifecycle.md) explains how a request travels from nginx to business logic, which matters when you configure your own proxy in front of the deployment.

## Prerequisites

- Docker and Docker Compose (or your cloud platform's container service)
- PostgreSQL database (required)
- OAuth credentials from your identity provider (Google, Microsoft, Okta, or another OIDC provider) for the deployment's own sign-in, set in environment variables

## Docker images

Pre-built images are published to two registries on every release:

| Registry | Image | Best for |
|----------|-------|----------|
| **Docker Hub** | `prebid/salesagent` | Universal access, simpler for most cloud providers |
| **GitHub Container Registry** | `ghcr.io/prebid/salesagent` | GitHub-integrated workflows |

### Pull an image

```bash
# Docker Hub (recommended for simplicity)
docker pull prebid/salesagent:latest

# GitHub Container Registry
docker pull ghcr.io/prebid/salesagent:latest
```

### Version tags

Each release publishes a full version tag plus rolling major and major.minor tags:

| Tag | Use case |
|-----|----------|
| `latest` | Quick evaluation |
| `2` | Auto-update within a major version |
| `2.0` | Auto-update within a minor version |
| `2.0.0` | Production (pin a specific version) |

### Cloud provider notes

- **GCP Cloud Run/GKE**: Docker Hub works with zero configuration
- **AWS ECS/EKS**: Both registries work natively
- **Azure/DigitalOcean/Fly.io**: Both registries work natively

### Rate limits

**Docker Hub**: 10 pulls/hour unauthenticated, 100 pulls/6 hours with a free account. For frequent pulls, authenticate with `docker login` or use ghcr.io.

**GitHub Container Registry**: Unlimited pulls for public images, no authentication needed.

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | For sign-in | The deployment's own OAuth client. `OAUTH_DISCOVERY_URL`, `OAUTH_CLIENT_ID`, and `OAUTH_CLIENT_SECRET` configure any OIDC provider instead |
| `SUPER_ADMIN_EMAILS` | For sign-in | Comma-separated super admin addresses. `SUPER_ADMIN_DOMAINS` names whole email domains instead |
| `ENCRYPTION_KEY` | Yes | Fernet key that encrypts stored secrets, such as a tenant's SSO client secret |
| `FLASK_SECRET_KEY` | Yes | Long random value that signs session cookies |
| `GEMINI_API_KEY` | No | For AI-powered creative review |

The first sign-in uses the deployment's own OAuth client and a super admin address. After it, each tenant configures its own SSO on the **Users & Access** page.

For a complete list including GAM integration and all optional settings, see the **[environment variables reference](environment-variables.md)**.

> **Session cookies**: In single-tenant mode (default), session cookies use the actual request domain, which lets the sales agent work with any custom domain. In multi-tenant mode, cookies are scoped to a base domain to work across tenant subdomains. See [Multi-tenant setup](multi-tenant.md) for details.

## Docker Compose deployment

```bash
git clone https://github.com/prebid/salesagent.git
cd salesagent
cp .env.template .env
# Edit .env with your configuration
docker compose up -d

# Verify
curl http://localhost:8000/health
```

## Services and ports

All services are accessible through port 8000 via nginx:

| Service | URL |
|---------|-----|
| Admin UI | http://localhost:8000/admin |
| MCP Server | http://localhost:8000/mcp/ |
| A2A Server | http://localhost:8000/a2a |
| Health check | http://localhost:8000/health |

## Docker management

```bash
# View logs
docker compose logs -f

# Stop services
docker compose down

# Reset everything (including database)
docker compose down -v

# Enter container
docker compose exec adcp-server bash

# Backup database
docker compose exec postgres pg_dump -U adcp_user adcp > backup.sql
```

## Database migrations

Migrations run automatically on startup. For manual management:

```bash
# Check status
docker compose exec adcp-server python scripts/ops/migrate.py status

# Run migrations
docker compose exec adcp-server python scripts/ops/migrate.py

# Create new migration
docker compose exec adcp-server alembic revision -m "description"
```

### Checks before an upgrade

Some migrations add a constraint that the existing data must already meet, and the upgrade stops
on the first row that doesn't. Each query below only reads. Run it against the deployment's
database before you upgrade; when it returns no rows, the migration it names will apply.

**`502abb701275` creative assignment names a package its buy holds.** The migration adds a foreign
key from `creative_assignments (media_buy_id, package_id)` to `media_packages`. It fails on an
assignment that names a package its media buy doesn't hold. This query lists those assignments:

```sql
SELECT ca.tenant_id, ca.media_buy_id, ca.package_id, ca.creative_id, ca.assignment_id,
       jsonb_path_exists(mb.raw_request, '$.packages[*] ? (@.package_id == $p)',
                         jsonb_build_object('p', ca.package_id)) AS named_in_request
FROM creative_assignments ca
JOIN media_buys mb ON mb.media_buy_id = ca.media_buy_id
LEFT JOIN media_packages mp
  ON mp.media_buy_id = ca.media_buy_id AND mp.package_id = ca.package_id
WHERE mp.package_id IS NULL
ORDER BY ca.tenant_id, ca.media_buy_id, ca.package_id;
```

`named_in_request` is true when the buy's stored request names the package, so the buy carried it
without a `media_packages` row. Decide each listed row before you upgrade: delete the assignment,
or add the missing `media_packages` row for a package the buy does hold.

## First-time setup

Starting the server applies migrations and nothing else: it creates no tenant, credential or
sample data. A fresh deployment serves nobody until you create its first tenant, once:

```bash
docker compose exec adcp-server python -m scripts.setup.setup_tenant "Acme"   --tenant-id acme --virtual-host sales.acme.com --adapter mock
```

The command prints a principal token for the tenant's first advertiser. It is shown once; store
it. Running it again for a tenant id, subdomain or virtual host that already exists refuses and
changes nothing. A super admin can also create tenants in the Admin UI.

New tenants start in **Setup Mode**, which lets you complete an OIDC login for them before their
SSO is switched on. Sign in with the deployment's own OAuth — set `GOOGLE_CLIENT_ID` /
`GOOGLE_CLIENT_SECRET` (or the generic `OAUTH_*` variables) and put your address in
`SUPER_ADMIN_EMAILS`. There is no password login and no web route that creates the first admin.

On the Docker Compose stack, register both of the following redirect URIs with that OAuth
client before you sign in. [Deployment OAuth](environment-variables.md#deployment-oauth)
explains why it needs both.

- `http://localhost:8000/admin/auth/google/callback`
- `http://localhost:8000/auth/google/callback`

**To complete setup:**

1. Log in with Google as a `SUPER_ADMIN_EMAILS` address.
2. Configure the tenant's SSO in the Admin UI with the
   [SSO quick start](../user-guide/sso-setup.md#quick-start). In its first step, set
   **Custom Domain** to `localhost:8000`, the host where you open the Admin UI.

Add yourself with the **Admin** role (or through an allowed domain) before you test, and sign in
with SSO before **Disable Setup Mode**. That SSO session is a tenant admin session only: for
super-admin work (creating tenants, deployment settings), sign in with the deployment's own OAuth
again, because a tenant's SSO never grants super admin.

The [SSO setup guide](../user-guide/sso-setup.md) has provider-specific instructions.
[Security and authentication](../security.md) covers how sessions and secrets are handled.

### Local testing with demo data

For local development without a real ad server:

```bash
docker compose --profile demo up
docker compose logs demo-setup     # shows the demo tenant's token, once
```

The `demo` profile runs `scripts/setup/demo_tenant.py` once. On an empty database it creates a
"Demo Sales Agent" tenant (id `demo`, served at `localhost:8000`) with the mock adapter,
USD/EUR/GBP, one publisher and two sample products, and prints a freshly minted principal token.
Once the database holds any tenant it creates nothing. Call it with
`uvx adcp http://localhost:8000/mcp/ --auth <token> list_tools`, or send
`x-adcp-tenant: demo` with the token from any other host. Do not use the profile in production.

## Custom domain configuration

1. Deploy to your cloud platform (see [walkthroughs](walkthroughs/)).
2. Point your domain's DNS to your deployment.
3. In the Admin UI, go to **Settings > Account** and set the **Custom Domain** field.
4. Register `https://<custom domain>/admin/auth/google/callback` and `https://<custom domain>/auth/google/callback` with the deployment's OAuth client, and in production set `SALES_AGENT_DOMAIN` to the custom domain. [Deployment OAuth](environment-variables.md#deployment-oauth) says which callback each sign-in uses.
5. If the tenant uses SSO, add the **Required Redirect URI** that **Users & Access** then shows to your SSO provider.

## Health monitoring

```bash
# Health check
curl http://localhost:8000/health

# PostgreSQL check
docker compose exec postgres pg_isready
```

## Security checklist

- [ ] Use HTTPS in production
- [ ] Set strong database passwords
- [ ] Configure SSO and disable Setup Mode
- [ ] Restrict authorized email domains per-tenant
- [ ] Rotate API tokens regularly
- [ ] Never commit `.env` files
- [ ] Implement a backup strategy

[Security and authentication](../security.md) is the full security reference; [Outbound egress](../security/outbound-egress.md) covers how the sales agent's own outbound HTTP is controlled.

## Backup and recovery

```bash
# Backup PostgreSQL
docker compose exec postgres pg_dump -U adcp_user adcp > backup_$(date +%Y%m%d).sql

# Restore
docker compose exec -T postgres psql -U adcp_user adcp < backup.sql
```

## Next steps

- Configure your ad server adapter in the Admin UI
- Set up products that match your GAM line item templates
- Add advertisers (principals) who will use the MCP API
- See [walkthroughs](walkthroughs/) and the [AWS guide](aws.md) for cloud-specific deployment guides
- See [Single VM deployment](vm.md) to run it on one Linux VM with Docker Compose and Caddy
