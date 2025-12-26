# AdCP Scripts

This directory contains utility scripts for testing, development, deployment, and operations of the AdCP Sales Agent.

## Directory Structure

### `/setup/` - Setup and Initialization Scripts
- `setup_tenant.py` - Create new publisher/tenant
- `init_database.py` - Initialize database with schema
- Database initialization is handled by `src/core/database/database.py::init_db()` for both dev and CI
- `populate_creative_formats.py` - Populate creative format data
- `setup_hooks.sh` - Setup git hooks for development
- `setup_conductor_workspace.sh` - Setup Conductor workspace
- `create_scribd_tenant.sh` - Create Scribd-specific tenant

### `/dev/` - Development and Testing Scripts
- `run_all_tests.sh` - Run comprehensive test suite
- `test_with_real_auth.sh` - Test with real authentication
- `check_ci.py` - Check CI configuration
- `check_ports.py` - Check port availability
- `manage_conductor_ports.py` - Manage Conductor port allocation
- `debug_start.sh` - Start services in debug mode
- `start_admin_ui.sh` - Start admin UI separately
- `cleanup_conductor_workspace.sh` - Clean up Conductor workspace
- `docker-compose-safe.sh` - Safe Docker Compose operations
- `fix_test_mocks.py` - Fix test mocking issues
- `test_migration.sh` - Test database migrations

### `/ops/` - Operations and Management Scripts
- `migrate.py` - Run database migrations
- `manage_auth.py` - Manage authentication tokens
- `get_tokens.py` - Retrieve access tokens
- `gam_helper.py` - Google Ad Manager helper utilities
- `check_tenants.py` - Check tenant health
- `sync_all_tenants.py` - Sync all GAM tenants (cron job)
- `sync_api_curl_examples.sh` - API testing with curl

### `/deploy/` - Deployment Scripts
- `run_all_services.py` - Main Docker container entrypoint (starts all services)
- `entrypoint_admin.sh` - Admin UI container entrypoint
- `fly-proxy.py` - Fly.io proxy configuration
- `fly-set-secrets.sh` - Set secrets for Fly.io deployment

### Root Level Scripts
- `run_server.py` - Main MCP server runner
- `run_admin_ui.py` - Admin UI runner
- `run_tests.py` - Test runner

## Common Usage Patterns

### Setting Up a New Environment
```bash
# 1. Setup the workspace
scripts/setup/setup_conductor_workspace.sh

# 2. Initialize database
python scripts/setup/init_database.py

# 3. Create a tenant
python -m scripts.setup.setup_tenant "Publisher Name" \
  --adapter google_ad_manager \
  --gam-network-code 123456

# 4. Populate creative formats (if needed)
# Creative formats are now fetched from creative agents via AdCP
```

### Development Workflow
```bash
# Run tests
scripts/dev/run_all_tests.sh

# Start server in debug mode
scripts/dev/debug_start.sh

# Check for port conflicts
python scripts/dev/check_ports.py

# Clean up workspace
scripts/dev/cleanup_conductor_workspace.sh
```

### Operations and Maintenance
```bash
# Run migrations
python scripts/ops/migrate.py

# Check tenant health
python scripts/ops/check_tenants.py

# Sync all tenants (typically run by cron)
python scripts/ops/sync_all_tenants.py

# Get tokens for debugging
python scripts/ops/get_tokens.py
```

### Deployment
```bash
# Deploy to Fly.io
scripts/deploy/fly-set-secrets.sh

# Or use Docker locally
docker-compose up -d
```

## API Testing Without UI

The sync API allows complete testing without UI interaction:

```python
# Python example
import requests

api_key = "your-superadmin-api-key"
tenant_id = "your-tenant-id"

# Trigger sync
response = requests.post(
    f"http://localhost:8001/api/v1/sync/trigger/{tenant_id}",
    headers={"X-API-Key": api_key},
    json={"sync_type": "incremental"}
)

# Check status
status = requests.get(
    f"http://localhost:8001/api/v1/sync/status/{tenant_id}",
    headers={"X-API-Key": api_key}
)
```

This approach enables:
- Automated testing in CI/CD
- Debugging without manual UI clicks
- Performance testing
- Integration with external systems
