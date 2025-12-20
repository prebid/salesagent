# Deployment Guide

## Overview

The AdCP Sales Agent reference implementation is designed to be hosted anywhere. This guide covers several deployment options.

**Deployment Flexibility:**
- **Pre-built Docker images** available at `ghcr.io/adcontextprotocol/salesagent`
- This is a **standard Python application** that can run on any infrastructure
- **Docker recommended** but not required
- **PostgreSQL required** for production deployments
- We'll support your deployment approach as best we can

**Common Deployment Options:**
- **Docker Compose** (recommended for most deployments)
- **Kubernetes** (for enterprise/scale deployments)
- **Cloud Platforms** (AWS, GCP, Azure, DigitalOcean, etc.)
- **Platform Services** (Fly.io, Heroku, Railway, Render, etc.)
- **Bare Metal** (direct Python deployment)
- **Standalone** (development/testing only)

**Reference Implementation:**
The reference implementation at https://adcp-sales-agent.fly.dev is hosted on Fly.io, but this is just one option.

## Docker Images

Pre-built Docker images are published to GitHub Container Registry on every release.

### Available Tags

| Tag | Description | Use Case |
|-----|-------------|----------|
| `latest` | Most recent release | Quick evaluation |
| `0` | Latest major version 0.x.x | Auto-update within major |
| `0.1` | Latest minor version 0.1.x | Auto-update within minor |
| `0.1.0` | Specific patch version | Production (recommended) |

### Pulling Images

```bash
# Latest release
docker pull ghcr.io/adcontextprotocol/salesagent:latest

# Pin to specific version (recommended for production)
docker pull ghcr.io/adcontextprotocol/salesagent:0.1.0

# Pin to minor version (gets patch updates)
docker pull ghcr.io/adcontextprotocol/salesagent:0.1
```

### Version Pinning Strategy

| Environment | Recommended Tag | Rationale |
|-------------|-----------------|-----------|
| **Production** | `0.1.0` (specific version) | Predictable, tested deployments |
| **Staging** | `0.1` (minor version) | Test patch updates before prod |
| **Development** | `latest` or build from source | Latest features |

See all available versions: https://github.com/adcontextprotocol/salesagent/pkgs/container/salesagent

## Docker Deployment (Recommended)

### Prerequisites
- Docker and Docker Compose installed
- Environment variables configured (optional for evaluation)
- Google OAuth credentials (for Admin UI in production)

### Option A: Pre-built Images (Recommended)

The fastest way to get started using published images:

```bash
# Download the production compose file
curl -O https://raw.githubusercontent.com/adcontextprotocol/salesagent/main/docker-compose.prod.yml

# Start services
docker compose -f docker-compose.prod.yml up -d

# Verify it's running
curl http://localhost:8080/health
```

**Pin to a specific version for production:**
```bash
IMAGE_TAG=0.1.0 docker compose -f docker-compose.prod.yml up -d
```

**Access services:**
- MCP Server: http://localhost:8080/mcp/
- Admin UI: http://localhost:8001 (test login: `test_super_admin@example.com` / `test123`)
- PostgreSQL: localhost:5432

### Option B: Build from Source

For development or customization:

1. **Clone and configure:**
   ```bash
   git clone https://github.com/adcontextprotocol/salesagent.git
   cd salesagent
   cp .env.template .env
   # Edit .env with your configuration
   ```

2. **Start services:**
   ```bash
   docker-compose up -d
   ```

3. **Access services:**
   - MCP Server: http://localhost:8080/mcp/
   - Admin UI: http://localhost:8001
   - PostgreSQL: localhost:5432

### Docker Services

The `docker-compose.yml` defines three services:

```yaml
services:
  postgres:      # PostgreSQL database
  adcp-server:   # MCP server (port 8080)
  admin-ui:      # Admin interface (port 8001)
```

### Docker Caching

The system uses BuildKit caching with shared volumes:
- `adcp_global_pip_cache` - Python packages cache
- `adcp_global_uv_cache` - uv dependencies cache

This reduces rebuild times from ~3 minutes to ~30 seconds across all Conductor workspaces.

### Docker Management

```bash
# Rebuild after code changes
docker-compose build
docker-compose up -d

# View logs
docker-compose logs -f
docker-compose logs -f adcp-server

# Stop services
docker-compose down

# Reset everything (including volumes)
docker-compose down -v

# Enter container
docker-compose exec adcp-server bash

# Backup database
docker-compose exec postgres pg_dump -U adcp_user adcp > backup.sql
```

## Fly.io Deployment (Reference Implementation)

### Overview

This section documents the Fly.io deployment used for the **reference implementation** at https://adcp-sales-agent.fly.dev.

**Note**: Fly.io is just one hosting option. You can deploy to any platform that supports Docker containers.

Fly.io provides:
- Managed cloud solution with automatic SSL
- Global distribution with edge locations
- Integrated PostgreSQL clusters
- Built-in monitoring and logging

### Architecture

```
Internet → Fly.io Edge → Proxy (8000) → MCP Server (8080)
                                      → Admin UI (8001)
```

### Prerequisites

1. **Install Fly CLI:**
   ```bash
   brew install flyctl  # macOS
   # or see https://fly.io/docs/hands-on/install-flyctl/
   ```

2. **Authenticate:**
   ```bash
   fly auth login
   ```

### Deployment Steps

1. **Create application:**
   ```bash
   fly apps create adcp-sales-agent
   ```

2. **Create PostgreSQL cluster:**
   ```bash
   fly postgres create --name adcp-db \
     --region iad \
     --initial-cluster-size 1 \
     --vm-size shared-cpu-1x \
     --volume-size 10

   fly postgres attach adcp-db --app adcp-sales-agent
   ```

3. **Create persistent volume:**
   ```bash
   fly volumes create adcp_data --region iad --size 1
   ```

4. **Set secrets:**
   ```bash
   # OAuth configuration
   fly secrets set GOOGLE_CLIENT_ID="your-client-id.apps.googleusercontent.com"
   fly secrets set GOOGLE_CLIENT_SECRET="your-client-secret"

   # Admin configuration
   fly secrets set SUPER_ADMIN_EMAILS="admin@example.com"
   fly secrets set SUPER_ADMIN_DOMAINS="example.com"

   # API keys
   fly secrets set GEMINI_API_KEY="your-gemini-api-key"
   ```

5. **Configure OAuth redirect URI:**

   Add to Google Cloud Console:
   ```
   https://adcp-sales-agent.fly.dev/auth/google/callback
   ```

6. **Deploy:**
   ```bash
   fly deploy
   ```

7. **Initialize database (first time only):**
   ```bash
   fly ssh console -C "cd /app && python init_database.py"
   ```

### Fly.io Configuration Files

- `fly.toml` - Main application configuration
- `Dockerfile.fly` - Optimized Docker image for Fly.io
- `fly-proxy.py` - Request routing proxy
- `debug_start.sh` - Service startup script

### Monitoring on Fly.io

```bash
# View logs
fly logs

# Check status
fly status

# SSH into machine
fly ssh console

# View metrics
fly dashboard

# Scale horizontally
fly scale count 2

# Scale vertically
fly scale vm shared-cpu-2x
```

### Accessing Services

- Admin UI: https://adcp-sales-agent.fly.dev/admin
- MCP Endpoint: https://adcp-sales-agent.fly.dev/mcp/
- Health Check: https://adcp-sales-agent.fly.dev/health

## Environment Configuration

### Required Variables

```bash
# API Keys
GEMINI_API_KEY=your-gemini-api-key-here

# OAuth Configuration (choose one method)
# Method 1: Environment variables (recommended)
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret

# Method 2: File path (legacy)
# GOOGLE_OAUTH_CREDENTIALS_FILE=/path/to/client_secret.json

# Admin Configuration
SUPER_ADMIN_EMAILS=user1@example.com,user2@example.com
SUPER_ADMIN_DOMAINS=example.com

# Database (Docker/Fly.io handle automatically)
DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

### Google OAuth Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create new project or select existing
3. Enable Google+ API
4. Create OAuth 2.0 Client ID (Web application)
5. Add authorized redirect URIs:
   - Local: `http://localhost:8001/auth/google/callback`
   - Docker: `http://localhost:8001/auth/google/callback`
   - Fly.io: `https://your-app.fly.dev/auth/google/callback`
   - Conductor: `http://localhost:8002-8011/auth/google/callback`
6. Download credentials or copy Client ID and Secret

### Database Configuration

#### PostgreSQL (Production)
```bash
DATABASE_URL=postgresql://user:password@host:5432/dbname
DB_TYPE=postgresql
```

#### SQLite (Development Only)
```bash
DATABASE_URL=sqlite:///adcp_local.db
DB_TYPE=sqlite
```

## Tenant Management

### Creating Tenants

```bash
# Docker deployment
docker-compose exec adcp-server python -m scripts.setup.setup_tenant \
  "Publisher Name" \
  --adapter google_ad_manager \
  --gam-network-code 123456 \
  --gam-refresh-token YOUR_REFRESH_TOKEN

# Fly.io deployment
fly ssh console -C "python -m scripts.setup.setup_tenant 'Publisher Name' \
  --adapter google_ad_manager \
  --gam-network-code 123456"

# Mock adapter for testing
docker-compose exec adcp-server python -m scripts.setup.setup_tenant "Test Publisher" --adapter mock
```

### Managing Principals (Advertisers)

After creating a tenant:
1. Login to Admin UI with Google OAuth
2. Navigate to "Advertisers" tab
3. Click "Add Advertiser"
4. Each advertiser gets their own API token

## Database Migrations

### Automatic Migrations

Migrations run automatically on startup via `entrypoint.sh`.

### Manual Migration Management

```bash
# Run migrations
uv run python migrate.py

# Check migration status
uv run python migrate.py status

# Create new migration
uv run alembic revision -m "description_of_change"

# Rollback last migration
uv run alembic downgrade -1
```

### Migration Best Practices

1. Always test on both SQLite and PostgreSQL
2. Use SQLAlchemy operations for compatibility
3. Include proper downgrade logic
4. Test with fresh database before deploying

## Health Monitoring

### Health Check Endpoints

```bash
# MCP Server health
curl http://localhost:8080/health

# Admin UI health
curl http://localhost:8001/health

# PostgreSQL health (Docker)
docker-compose exec postgres pg_isready
```

### Monitoring Metrics

The system prepares for Prometheus metrics:
- Request latency
- Active media buys
- API call rates
- Error rates

## Security Considerations

### Production Checklist

- [ ] Use HTTPS everywhere (automatic on Fly.io)
- [ ] Set strong database passwords
- [ ] Rotate API keys regularly
- [ ] Enable audit logging
- [ ] Configure rate limiting
- [ ] Use environment variables for secrets
- [ ] Never commit `.env` files
- [ ] Implement backup strategy
- [ ] Monitor error logs
- [ ] Set up alerting

### SSL/TLS Configuration

#### Fly.io
SSL is automatic - Fly.io handles certificates.

#### Docker with Nginx
```nginx
server {
    listen 443 ssl;
    ssl_certificate /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;

    location / {
        proxy_pass http://admin-ui:8001;
    }

    location /mcp/ {
        proxy_pass http://adcp-server:8080;
    }
}
```

## Backup and Recovery

### Database Backup

#### PostgreSQL Backup
```bash
# Docker
docker-compose exec postgres \
  pg_dump -U adcp_user adcp > backup_$(date +%Y%m%d).sql

# Fly.io
fly postgres backup create --app adcp-db
```

#### PostgreSQL Restore
```bash
# Docker
docker-compose exec -T postgres \
  psql -U adcp_user adcp < backup.sql

# Fly.io
fly postgres backup restore <backup-id> --app adcp-db
```

### File Backup

Important files to backup:
- `.env` configuration
- `conductor_ports.json` (if using Conductor)
- Database backups
- Custom adapter configurations

## Troubleshooting Deployment

### Common Issues

#### Port Conflicts
```bash
# Check what's using a port
lsof -i :8080

# Kill process using port
kill -9 $(lsof -t -i:8080)
```

#### Database Connection Issues
```bash
# Test connection
psql postgresql://user:pass@localhost:5432/adcp

# Check Docker network
docker network ls
docker network inspect salesagent_default
```

#### OAuth Redirect Mismatch
- Ensure redirect URI matches exactly (including trailing slash)
- Check for http vs https
- Verify port numbers match

#### Container Won't Start
```bash
# Check logs
docker-compose logs adcp-server

# Rebuild from scratch
docker-compose down -v
docker-compose build --no-cache
docker-compose up
```

### Debug Mode

Enable debug logging:
```bash
# Docker
DEBUG=true docker-compose up

# Fly.io
fly secrets set DEBUG=true
```

## Migration from Single-Tenant

If migrating from an older single-tenant version:

1. **Backup existing data:**
   ```bash
   cp adcp.db adcp_backup.db
   sqlite3 adcp.db .dump > backup.sql
   ```

2. **Update code:**
   ```bash
   git pull
   uv sync
   ```

3. **Run migration:**
   ```bash
   uv run python migrate.py
   ```

The system will automatically:
- Create a default tenant from existing config
- Migrate data to multi-tenant schema
- Generate authentication tokens

## Performance Tuning

### PostgreSQL Optimization

```sql
-- Increase connections
ALTER SYSTEM SET max_connections = 200;

-- Optimize for SSD
ALTER SYSTEM SET random_page_cost = 1.1;

-- Increase shared buffers
ALTER SYSTEM SET shared_buffers = '256MB';
```

### Docker Resource Limits

```yaml
services:
  adcp-server:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
        reservations:
          cpus: '1'
          memory: 1G
```

### Connection Pooling

The system uses SQLAlchemy connection pooling:
```python
# Configured in database.py
pool_size=20
max_overflow=40
pool_timeout=30
```

## Scaling Strategies

### Horizontal Scaling

#### Docker Swarm
```bash
docker swarm init
docker stack deploy -c docker-compose.yml adcp
docker service scale adcp_adcp-server=3
```

#### Fly.io
```bash
fly scale count 3 --region iad
fly scale count 2 --region lhr
```

### Vertical Scaling

#### Docker
Update `docker-compose.yml` resource limits.

#### Fly.io
```bash
fly scale vm dedicated-cpu-2x
fly scale memory 4096
```

## Maintenance Mode

To enable maintenance mode:

1. **Create maintenance page:**
   ```html
   <!-- templates/maintenance.html -->
   <h1>System Maintenance</h1>
   <p>We'll be back shortly.</p>
   ```

2. **Enable in Admin UI:**
   ```python
   # Set environment variable
   MAINTENANCE_MODE=true
   ```

3. **Or use nginx:**
   ```nginx
   location / {
       if (-f /var/www/maintenance.html) {
           return 503;
       }
       proxy_pass http://upstream;
   }
   error_page 503 /maintenance.html;
   ```

## Backup and Recovery

### PostgreSQL Backup

```bash
# Full backup
docker-compose exec postgres pg_dump -U adcp_user adcp > backup.sql

# Compressed backup
docker-compose exec postgres pg_dump -U adcp_user adcp | gzip > backup.sql.gz

# Restore
docker-compose exec -T postgres psql -U adcp_user adcp < backup.sql
```

### SQLite Backup

```bash
# Simple copy
cp adcp_local.db backup.db

# With active connections
sqlite3 adcp_local.db ".backup backup.db"
```

## Production Considerations

### Security

- Always use HTTPS in production
- Rotate API tokens regularly
- Monitor audit logs for anomalies
- Keep dependencies updated
- Input validation enforced on all API endpoints
- ID formats validated to prevent injection attacks
- Timezone strings validated against pytz database
- Temporary files cleaned up with try/finally blocks
- Database queries use parameterized statements only

### Performance

- Use PostgreSQL for production
- Enable connection pooling
- Implement caching where appropriate
- Monitor resource usage

### Scaling

- Database replication for read scaling
- Load balancer for multiple app instances
- Consider CDN for static assets
- Queue system for async tasks

## Other Deployment Options

### Kubernetes Deployment

**Benefits:**
- Enterprise-grade orchestration
- Auto-scaling and self-healing
- Advanced networking and service mesh
- Multi-cloud portability

**Basic Setup:**
1. Create Docker image: `docker build -t adcp-sales-agent:latest .`
2. Push to registry: `docker push your-registry/adcp-sales-agent:latest`
3. Apply k8s manifests (see `k8s/` directory for examples)
4. Configure ingress for external access

**Minimal k8s resources needed:**
- Deployment (for app pods)
- Service (for internal networking)
- Ingress (for external access)
- ConfigMap (for configuration)
- Secret (for sensitive data)
- PersistentVolumeClaim (for PostgreSQL)

### AWS Deployment

**Option 1: ECS with Fargate**
- Use Docker container from this repo
- Deploy to ECS with Fargate (serverless)
- RDS PostgreSQL for database
- Application Load Balancer for traffic

**Option 2: EKS (Kubernetes)**
- Deploy using k8s manifests
- Managed Kubernetes service
- Integrate with AWS services (RDS, Secrets Manager, etc.)

**Option 3: EC2 with Docker**
- Launch EC2 instance
- Install Docker and Docker Compose
- Deploy using docker-compose.yml
- Manage SSL with certbot/Let's Encrypt

### GCP Deployment

**Option 1: Cloud Run**
- Deploy Docker container directly
- Serverless, auto-scaling
- Cloud SQL for PostgreSQL
- Cloud Load Balancing

**Option 2: GKE (Kubernetes)**
- Deploy using k8s manifests
- Managed Kubernetes service
- Integrate with GCP services

**Option 3: Compute Engine**
- Similar to EC2 approach
- Deploy with Docker Compose
- Cloud SQL or self-hosted PostgreSQL

### Azure Deployment

**Option 1: Azure Container Instances**
- Deploy Docker container
- Serverless container service
- Azure Database for PostgreSQL

**Option 2: AKS (Kubernetes)**
- Deploy using k8s manifests
- Managed Kubernetes service
- Integrate with Azure services

**Option 3: Virtual Machines**
- Similar to EC2/Compute Engine
- Deploy with Docker Compose
- Azure Database for PostgreSQL

### DigitalOcean Deployment

**Option 1: App Platform**
- Deploy from GitHub repo
- Managed platform (like Heroku)
- Managed PostgreSQL database

**Option 2: Kubernetes**
- Deploy using k8s manifests
- DigitalOcean Kubernetes (DOKS)

**Option 3: Droplets**
- Deploy with Docker Compose
- Managed PostgreSQL database

### Bare Metal Deployment

**For direct Python deployment without Docker:**

1. **System requirements:**
   ```bash
   # Ubuntu/Debian
   sudo apt install python3.11 python3-pip postgresql nginx

   # Install uv (Python package manager)
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Application setup:**
   ```bash
   git clone https://github.com/adcontextprotocol/salesagent.git
   cd salesagent
   uv sync
   uv run python migrate.py
   ```

3. **Systemd services:**
   - Create service files for MCP server, Admin UI, and A2A server
   - Use gunicorn or uvicorn for production serving
   - Configure nginx as reverse proxy

4. **Database:**
   - Install and configure PostgreSQL
   - Create database and user
   - Run migrations

**Example systemd service:**
```ini
[Unit]
Description=AdCP Sales Agent MCP Server
After=network.target postgresql.service

[Service]
Type=simple
User=adcp
WorkingDirectory=/opt/salesagent
Environment="DATABASE_URL=postgresql://user:pass@localhost/adcp"
ExecStart=/home/adcp/.local/bin/uv run python -m src.core.main
Restart=always

[Install]
WantedBy=multi-user.target
```

### Platform Services

**Heroku:**
- Deploy using Heroku's buildpacks
- Add Heroku Postgres addon
- Configure environment variables via Heroku CLI

**Railway:**
- Deploy from GitHub repo
- Auto-detects Docker or Python
- Managed PostgreSQL available

**Render:**
- Deploy Docker container or Python app
- Managed PostgreSQL database
- Auto SSL and global CDN

## Deployment Support

**Need help deploying to your platform?**

We're here to support your deployment approach:

1. **Check existing docs** - This guide covers common platforms
2. **Docker is universal** - If you can run Docker, you can deploy this
3. **Open an issue** - Share your deployment target and any challenges
4. **Contribute back** - Add deployment guides for your platform

**Common requirements across all platforms:**
- Python 3.11+ (if not using Docker)
- PostgreSQL (production) or SQLite (dev/testing)
- Environment variables for configuration
- HTTPS/SSL for production
- Health check endpoint at `/health`

**We aim to support your chosen infrastructure - don't hesitate to reach out!**
