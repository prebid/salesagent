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
- [SSO setup](#sso-setup)
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
| `CREATE_DEMO_TENANT` | No | Set to `true` for initial setup with demo data |
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

## First-time setup

On first startup, the system creates an empty default tenant with **Setup Mode** enabled,
which lets you complete an OIDC login for it before its SSO is switched on.

Sign in with the deployment's own OAuth — set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`
(or the generic `OAUTH_*` variables) and put your address in `SUPER_ADMIN_EMAILS`.

On the Docker Compose stack, the Admin UI answers under `/admin/` and at the root, and the
callback follows the path that the sign-in starts from. Before you sign in, register both of
the following redirect URIs with that OAuth client:

- `http://localhost:8000/admin/auth/google/callback`
- `http://localhost:8000/auth/google/callback`

**To complete setup:**

1. Log in with Google as a `SUPER_ADMIN_EMAILS` address.
2. In **Settings > Account**, set **Custom Domain** to the host where you open the Admin UI,
   for example `localhost:8000`. Then go to **Users & Access**, which builds the SSO redirect
   URI from that field.
3. Configure your SSO provider (Google, Microsoft, or a custom OIDC provider).
4. **Add yourself**: Add your email OR your domain to Allowed Domains.
5. Click **Test Connection** - SSO is automatically enabled on success.
6. Sign out, then sign in again with **Sign in with SSO**. If the header shows no **Logout**
   link, open `/admin/logout` to sign out.
7. Click **Disable Setup Mode**. After this, **Sign in with SSO** works only while the
   tenant's SSO is enabled.

See the [SSO setup guide](../user-guide/sso-setup.md) for detailed provider-specific instructions.

### Local testing with demo data

For local development without a real ad server:

```bash
# Add to .env for local testing only - NOT for production
CREATE_DEMO_TENANT=true
```

This creates a "Demo Sales Agent" tenant with a mock adapter, sample currencies, and test data for exploring features.

## SSO setup

SSO is configured per-tenant in the Admin UI:

1. Log in with Google as a `SUPER_ADMIN_EMAILS` address.
2. In **Settings > Account**, set **Custom Domain** to the host where you open the Admin UI,
   for example `localhost:8000`. Then go to the **Users & Access** page, which builds the SSO
   redirect URI from that field.
3. Configure your identity provider (Google, Microsoft, or any OIDC provider as Custom OIDC).
4. Copy the **Required Redirect URI** shown and add it to your provider's allowed redirect URIs.
5. **Add yourself**: Add your email as a user OR add your domain to Allowed Domains.
6. Click **Save Configuration**, then **Test Connection** - SSO is automatically enabled on success.
7. **Disable Setup Mode** once SSO is working.

See the [SSO setup guide](../user-guide/sso-setup.md) for detailed provider-specific instructions. [Security and authentication](../security.md) covers how sessions and secrets are handled.

## Custom domain configuration

1. Deploy to your cloud platform (see [walkthroughs](walkthroughs/)).
2. Point your domain's DNS to your deployment.
3. In the Admin UI, go to **Settings > Account** and set the **Custom Domain** field.
4. Register `https://<custom domain>/admin/auth/google/callback` and `https://<custom domain>/auth/google/callback` with the deployment's OAuth client. In production, register only the first, and set `SALES_AGENT_DOMAIN` to the custom domain.
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
