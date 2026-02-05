# Single-Tenant Deployment

Single-tenant mode is the default and recommended for most publishers deploying their own Prebid Sales Agent.

## Overview

**Single-Tenant Mode:**
- One publisher per deployment
- Simple path-based routing (`/admin`, `/mcp`, `/a2a`)
- No subdomain complexity
- Works with any custom domain

## Prerequisites

- Docker and Docker Compose (or your cloud platform's container service)
- PostgreSQL database (required)
- OAuth credentials from your identity provider (Google, Microsoft, Okta, etc.) - configured via Admin UI

## Warning

The instructions below are not yet fixed to work in the new repo location

## Docker Images

Pre-built images are published to two registries on every release:

| Registry | Image | Best For |
|----------|-------|----------|
| **Docker Hub** | `prebid/salesagent` | Universal access, simpler for most cloud providers |
| **GitHub Container Registry** | `ghcr.io/prebid/salesagent` | GitHub-integrated workflows |

### Pulling Images

```bash
# Docker Hub (recommended for simplicity)
docker pull prebid/salesagent:latest

# GitHub Container Registry
docker pull ghcr.io/prebid/salesagent:latest
```

### Version Tags

| Tag | Use Case |
|-----|----------|
| `latest` | Quick evaluation |
| `0.3` | Auto-update within minor version |
| `0.3.0` | Production (pin specific version) |

### Cloud Provider Notes

- **GCP Cloud Run/GKE**: Docker Hub works with zero configuration
- **AWS ECS/EKS**: Both registries work natively
- **Azure/DigitalOcean/Fly.io**: Both registries work natively

### Rate Limits

**Docker Hub**: 10 pulls/hour unauthenticated, 100 pulls/6 hours with free account. For frequent pulls, authenticate with `docker login` or use ghcr.io.

**GitHub Container Registry**: Unlimited pulls for public images, no authentication needed.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `CREATE_DEMO_TENANT` | No | Set to `true` for initial setup with demo data |
| `GEMINI_API_KEY` | No | For AI-powered creative review |

Authentication is configured **per-tenant** via the Admin UI (Users & Access page). No OAuth environment variables required.

For a complete list including GAM integration and all optional settings, see the **[Environment Variables Reference](environment-variables.md)**.

> **Session Cookies**: In single-tenant mode (default), session cookies use the actual request domain, allowing the sales agent to work with any custom domain. In multi-tenant mode, cookies are scoped to a base domain to work across tenant subdomains. See [Multi-Tenant Setup](multi-tenant.md) for details.

## Docker Compose Deployment

```bash
git clone https://github.com/prebid/salesagent.git
cd salesagent
cp .env.template .env
# Edit .env with your configuration
docker compose up -d

# Verify
curl http://localhost:8000/health
```

## Services and Ports

All services are accessible through port 8000 via nginx:

| Service | URL |
|---------|-----|
| Admin UI | http://localhost:8000/admin |
| MCP Server | http://localhost:8000/mcp/ |
| A2A Server | http://localhost:8000/a2a |
| Health Check | http://localhost:8000/health |

## Docker Management

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

## Database Migrations

Migrations run automatically on startup. For manual management:

```bash
# Check status
docker compose exec adcp-server python migrate.py status

# Run migrations
docker compose exec adcp-server python migrate.py

# Create new migration
docker compose exec adcp-server alembic revision -m "description"
```

## SSO Setup

SSO is configured per-tenant via the Admin UI:

1. Log in with test credentials (Setup Mode is enabled by default)
2. Go to **Users & Access** page
3. Configure your identity provider (Google, Microsoft, Okta, Auth0, Keycloak, or custom OIDC)
4. Copy the **Redirect URI** shown and add it to your provider's allowed redirect URIs
5. **Add yourself**: Add your email as a user OR add your domain to Allowed Domains
6. Click **Save Configuration**, then **Test Connection** - SSO is automatically enabled on success
7. **Disable Setup Mode** once SSO is working

See [SSO Setup Guide](../user-guide/sso-setup.md) for detailed provider-specific instructions.

## Custom Domain Configuration

1. Deploy to your cloud platform (see [walkthroughs](walkthroughs/))
2. Point your domain's DNS to your deployment
3. In Admin UI, go to **Settings > General** and set your **Virtual Host**
4. Update OAuth redirect URI to include your custom domain

## Health Monitoring

```bash
# Health check
curl http://localhost:8000/health

# PostgreSQL check
docker compose exec postgres pg_isready
```

## Security Checklist

- [ ] Use HTTPS in production
- [ ] Set strong database passwords
- [ ] Configure SSO and disable Setup Mode
- [ ] Restrict authorized email domains per-tenant
- [ ] Rotate API tokens regularly
- [ ] Never commit `.env` files
- [ ] Implement backup strategy

## Backup and Recovery

```bash
# Backup PostgreSQL
docker compose exec postgres pg_dump -U adcp_user adcp > backup_$(date +%Y%m%d).sql

# Restore
docker compose exec -T postgres psql -U adcp_user adcp < backup.sql
```

## First-Time Setup

On first startup, the system creates an empty default tenant with **Setup Mode** enabled. This allows you to log in with test credentials to configure SSO:

- Email: `test_super_admin@example.com`
- Password: `test123`

**To complete setup:**
1. Log in with test credentials
2. Go to **Users & Access**
3. Configure your SSO provider (Google, Microsoft, etc.)
4. **Add yourself**: Add your email OR your domain to Allowed Domains
5. Click **Test Connection** - SSO is automatically enabled on success
6. Click **Disable Setup Mode** to require SSO for all users

See [SSO Setup Guide](../user-guide/sso-setup.md) for detailed provider-specific instructions.

### Local Testing with Demo Data

For local development without a real ad server:

```bash
# Add to .env for local testing only - NOT for production
CREATE_DEMO_TENANT=true
```

This creates a "Default Publisher" tenant with a mock adapter, sample currencies, and test data for exploring features.

## Next Steps

- Configure your ad server adapter in Admin UI
- Set up products that match your GAM line item templates
- Add advertisers (principals) who will use the MCP API
- See [walkthroughs/](walkthroughs/) for cloud-specific deployment guides
