# Setup and Configuration Guide

## Quick Start (Docker)

### Option A: Pre-built Images (Fastest)

```bash
# 1. Download compose file
curl -O https://raw.githubusercontent.com/adcontextprotocol/salesagent/main/docker-compose.prod.yml

# 2. Start services
docker compose -f docker-compose.prod.yml up -d

# 3. Test the MCP endpoint
uvx adcp http://localhost:8080/mcp/ --auth test-token list_tools

# 4. Access Admin UI (test login: test_super_admin@example.com / test123)
open http://localhost:8001
```

For production, pin to a specific version:
```bash
IMAGE_TAG=0.1.0 docker compose -f docker-compose.prod.yml up -d
```

### Option B: Build from Source (For Development)

```bash
# 1. Clone the repository
git clone https://github.com/adcontextprotocol/salesagent.git
cd salesagent

# 2. Create and configure .env
cp .env.template .env
# Edit .env with your values (see Required Configuration below)

# 3. Start services
docker-compose up -d

# 4. Access Admin UI
open http://localhost:8001
```

### Required Configuration

Edit `.env` with these values:

| Variable | Description | How to get |
|----------|-------------|------------|
| `GEMINI_API_KEY` | AI features | Free at https://aistudio.google.com/apikey |
| `SUPER_ADMIN_EMAILS` | Your email | Grants admin access |
| `GOOGLE_CLIENT_ID` | OAuth login | [Google Cloud Console](https://console.cloud.google.com/apis/credentials) |
| `GOOGLE_CLIENT_SECRET` | OAuth login | Same as above |

**Google OAuth Setup:**
1. Create OAuth 2.0 credentials (Web application) in Google Cloud Console
2. Add authorized redirect URI: `http://localhost:8001/auth/google/callback`

### Services

After `docker-compose up -d`:
- **Admin UI**: http://localhost:8001
- **MCP Server**: http://localhost:8080/mcp/
- **A2A Server**: http://localhost:8091
- **PostgreSQL**: localhost:5432

## Alternative Deployments

### Fly.io

```bash
fly apps create adcp-sales-agent
fly postgres create --name adcp-db --region iad
fly postgres attach adcp-db --app adcp-sales-agent

fly secrets set GOOGLE_CLIENT_ID="..." GOOGLE_CLIENT_SECRET="..."
fly secrets set GEMINI_API_KEY="..." SUPER_ADMIN_EMAILS="..."

fly deploy
```

### Standalone (without Docker)

```bash
uv sync
uv run python migrate.py
uv run python run_server.py
```

Requires PostgreSQL running separately.

## Creating Your First Tenant

```bash
# Create publisher/tenant with access control
docker-compose exec adcp-server python -m scripts.setup.setup_tenant "Publisher Name" \
  --adapter google_ad_manager \
  --gam-network-code 123456 \
  --domain publisher.com \
  --admin-email admin@publisher.com

# Create with mock adapter for testing
docker-compose exec adcp-server python -m scripts.setup.setup_tenant "Test Publisher" \
  --adapter mock \
  --admin-email test@example.com
```

**⚠️ Important:** Always specify `--domain` or `--admin-email` to configure access control. Without this, nobody can access the tenant.

## Admin UI Management

The Admin UI provides secure web-based management at http://localhost:8001

### Access Levels

1. **Super Admin** - Full system access
   - Manage all tenants (publishers)
   - View all operations
   - System configuration

2. **Tenant Admin** - Publisher management
   - Manage products and advertisers
   - View tenant operations
   - Configure integrations

3. **Tenant User** - Read-only access
   - View products and campaigns
   - Monitor performance

### Key Features

- **Publisher Management** - Create and configure tenants
- **Advertiser Management** - Add principals (advertisers)
- **Product Catalog** - Define inventory products
- **Creative Approval** - Review and approve creatives
- **Operations Dashboard** - Monitor all activity
- **Audit Logs** - Track all operations

### Publisher Configuration

Each publisher has JSON configuration:

```json
{
  "adapters": {
    "google_ad_manager": {
      "enabled": true,
      "network_code": "123456",
      "manual_approval_required": false
    }
  },
  "creative_engine": {
    "auto_approve_formats": ["display_300x250"],
    "human_review_required": true
  },
  "features": {
    "max_daily_budget": 10000,
    "enable_axe_signals": true
  }
}
```

### Advertiser (Principal) Management

Add advertisers to publishers:

```bash
# Via Admin UI (recommended)
# 1. Login to http://localhost:8001
# 2. Navigate to tenant
# 3. Add new advertiser/principal
# 4. Configure GAM advertiser ID

# Via API
curl -X POST "http://localhost:8001/admin/tenant/{tenant_id}/principals" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Advertiser Name",
    "platform_mappings": {
      "google_ad_manager": {
        "advertiser_id": "123456",
        "enabled": true
      }
    }
  }'
```

### Product Management

#### AI-Powered Product Creation

Create products with AI assistance:

```bash
# Quick create from templates
curl -X POST "/admin/tenant/{tenant_id}/products/quick-create" \
  -d '{"template": "news_display", "name": "News Display Ads"}'

# Get AI suggestions
curl -X POST "/admin/tenant/{tenant_id}/products/ai-suggest" \
  -d '{"description": "Video ads for sports content"}'
```

#### Default Products

New tenants get 6 standard products:
- Premium Display (guaranteed)
- Standard Display (non-guaranteed)
- Video Pre-Roll (guaranteed)
- Native Content (guaranteed)
- Mobile Display (non-guaranteed)
- Newsletter Sponsorship (guaranteed)

#### Bulk Operations

```bash
# Upload CSV
curl -X POST "/admin/tenant/{tenant_id}/products/upload" \
  -F "file=@products.csv"

# JSON import
curl -X POST "/admin/tenant/{tenant_id}/products/import" \
  -H "Content-Type: application/json" \
  -d @products.json
```

### Creative Management

#### Auto-Approval Workflow

1. Configure auto-approve formats per tenant
2. Standard formats approved instantly
3. Non-standard sent to review queue
4. Admin reviews in UI
5. Email notifications on status change

#### Creative Groups

Organize creatives across campaigns:
- Group by advertiser, campaign, or theme
- Share creatives across media buys
- Track performance by group

## Database Migrations

Migrations run automatically on startup, but can be managed manually:

```bash
# Run migrations
uv run python migrate.py

# Check status
uv run python migrate.py status

# Create new migration
uv run alembic revision -m "description"
```

## Docker Management

### Building and Caching

Docker uses BuildKit caching with shared volumes across Conductor workspaces:
- `adcp_global_pip_cache` - Python packages
- `adcp_global_uv_cache` - uv dependencies

This reduces build times from ~3 minutes to ~30 seconds.

### Common Commands

```bash
# Rebuild after changes
docker-compose build
docker-compose up -d

# View logs
docker-compose logs -f

# Enter container
docker-compose exec adcp-server bash

# Backup database
docker-compose exec postgres pg_dump -U adcp_user adcp > backup.sql
```

## Test Authentication Mode

For UI testing without OAuth:

```bash
# Enable in docker-compose.override.yml
ADCP_AUTH_TEST_MODE=true

# Test users available:
# - test_super_admin@example.com / test123
# - test_tenant_admin@example.com / test123
# - test_tenant_user@example.com / test123
```

⚠️ **Never enable in production!**

## Conductor Workspaces

For Conductor users running multiple parallel workspaces.

### Prerequisites

Create `.env.secrets` in the project root with your secrets:
```bash
cp .env.secrets.template .env.secrets
# Edit with your actual values
```

### What Conductor Setup Does

When you create a workspace, the setup script:
- Assigns unique ports to avoid conflicts
- Creates `.env` with workspace-specific configuration
- Creates `docker-compose.override.yml` for hot reload development
- Installs git hooks

### Troubleshooting

**Port conflicts:** Check assigned ports with `cat .env | grep PORT`

**Import errors with hot reload:** The setup script includes PYTHONPATH configuration automatically. If missing, see `docker-compose.override.example.yml`.

**Docker caching issues:**
```bash
docker volume rm adcp_global_pip_cache adcp_global_uv_cache
```

## Health Checks

```bash
# MCP Server
curl http://localhost:8080/health

# Admin UI
curl http://localhost:8001/health

# Database
docker-compose exec postgres pg_isready
```
