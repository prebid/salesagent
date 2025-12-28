# Scripts

Utility scripts for setup, deployment, and operations.

## Directory Structure

### `/setup/` - Setup and Initialization
- `init_database.py` - Initialize database with schema
- `init_database_ci.py` - CI-specific database initialization
- `setup_tenant.py` - Create new publisher/tenant

### `/ops/` - Operations
- `migrate.py` - Run database migrations
- `manage_auth.py` - Manage authentication tokens
- `get_tokens.py` - Retrieve access tokens
- `check_tenants.py` - Check tenant health
- `sync_all_tenants.py` - Sync all GAM tenants (cron job)
- `aggregate_format_metrics.py` - Aggregate format metrics from GAM
- `check_migration_heads.py` - Check for multiple Alembic migration heads
- `auto_merge_migrations.sh` - Auto-merge multiple migration heads
- `gam_helper.py` - GAM OAuth client utilities (used by reporting API)

### `/deploy/` - Deployment
- `run_all_services.py` - Main Docker container entrypoint (orchestrates all services)
- `entrypoint_admin.sh` - Admin UI container entrypoint
- `fly-set-secrets.sh` - Set secrets for Fly.io deployment

### `/hooks/` - Pre-commit Hooks
- `check_response_attribute_access.py` - Detect unsafe response attribute patterns
- `validate_mcp_schemas.py` - Validate MCP tool parameters match schema fields

### Root Level
- `run_server.py` - MCP server runner (used by run_all_services.py)
- `run_admin_ui.py` - Admin UI runner (production WSGI wrapper)
- `generate_encryption_key.py` - Generate Fernet encryption keys
- `generate_frontend_types.py` - Generate TypeScript types from Pydantic schemas
- `gam_prerequisites_check.py` - Check GAM OAuth prerequisites
- `initialize_tenant_mgmt_api_key.py` - Initialize tenant management API key

## Quick Reference

### Setting Up a New Environment
```bash
# 1. Setup git hooks (from project root)
./setup_hooks.sh

# 2. Initialize database
uv run python scripts/setup/init_database.py

# 3. Create a tenant
uv run python -m scripts.setup.setup_tenant "Publisher Name" \
  --adapter google_ad_manager \
  --gam-network-code 123456
```

### Running Tests
```bash
# Run full test suite (recommended before pushing)
./run_all_tests.sh ci
```

### Operations
```bash
# Run migrations
uv run python scripts/ops/migrate.py

# Check tenant health
uv run python scripts/ops/check_tenants.py
```

### Deployment
```bash
# Docker locally
docker-compose up -d

# Set Fly.io secrets
scripts/deploy/fly-set-secrets.sh
```
