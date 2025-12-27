# Single-Tenant Deployment

Single-tenant mode is the default and recommended for most publishers deploying their own AdCP Sales Agent.

## Overview

**Single-Tenant Mode:**
- One publisher per deployment
- Simple path-based routing (`/admin`, `/mcp`, `/a2a`)
- No subdomain complexity
- Works with any custom domain

## Prerequisites

- Docker and Docker Compose (or your cloud platform's container service)
- PostgreSQL database (required)
- Google OAuth credentials (for production Admin UI access)

## Docker Images

Pre-built images are published to two registries on every release:

| Registry | Image | Best For |
|----------|-------|----------|
| **Docker Hub** | `adcontextprotocol/salesagent` | Universal access, simpler for most cloud providers |
| **GitHub Container Registry** | `ghcr.io/adcontextprotocol/salesagent` | GitHub-integrated workflows |

### Pulling Images

```bash
# Docker Hub (recommended for simplicity)
docker pull adcontextprotocol/salesagent:latest

# GitHub Container Registry
docker pull ghcr.io/adcontextprotocol/salesagent:latest
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

## Required Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `SUPER_ADMIN_EMAILS` | Comma-separated admin emails |

## Recommended Environment Variables

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | For AI-powered creative review |
| `GOOGLE_CLIENT_ID` | For Google OAuth login |
| `GOOGLE_CLIENT_SECRET` | For Google OAuth login |

## Optional Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CREATE_DEMO_TENANT` | Create demo tenant with sample data | `false` |
| `ENCRYPTION_KEY` | For encrypting sensitive data | Auto-generated |
| `ADCP_AUTH_TEST_MODE` | Enable test login (no OAuth required) | `false` |

## Docker Compose Deployment

### Option A: Pre-built Images (Recommended)

```bash
# Download compose file
curl -O https://raw.githubusercontent.com/adcontextprotocol/salesagent/main/docker-compose.yml

# Create environment file
cat > .env << 'EOF'
SUPER_ADMIN_EMAILS=your-email@example.com
GEMINI_API_KEY=your-gemini-key
EOF

# Start services
docker compose up -d

# Verify
curl http://localhost:8000/health
```

### Option B: Build from Source

```bash
git clone https://github.com/adcontextprotocol/salesagent.git
cd salesagent
cp .env.template .env
# Edit .env with your configuration
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
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

## Google OAuth Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create OAuth 2.0 Client ID (Web application)
3. Add redirect URI: `https://your-domain.com/auth/google/callback`
4. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` environment variables

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
- [ ] Configure `SUPER_ADMIN_EMAILS` correctly
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

On first startup:
1. Super admins (from `SUPER_ADMIN_EMAILS`) get automatic access
2. With `CREATE_DEMO_TENANT=false` (default): Clean slate - create your tenant via Admin UI signup
3. With `CREATE_DEMO_TENANT=true`: Creates a "Default Publisher" tenant with mock adapter for evaluation

## Next Steps

- Configure your ad server adapter in Admin UI
- Set up products that match your GAM line item templates
- Add advertisers (principals) who will use the MCP API
- See [walkthroughs/](walkthroughs/) for cloud-specific deployment guides
