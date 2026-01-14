# Quickstart Guide

Get the AdCP Sales Agent running locally in under 5 minutes.

## Prerequisites

- Docker installed
- (Optional) OAuth credentials from your identity provider for SSO

## Option 1: Docker Run (Quickest)

If you have an existing PostgreSQL database:

```bash
# Generate an encryption key (run once, save the output)
docker run --rm ghcr.io/adcontextprotocol/salesagent:latest \
  python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'

# Run the sales agent (replace YOUR_KEY with the generated key)
docker run -p 8000:8000 \
  -e DATABASE_URL=postgresql://user:pass@host:5432/dbname \
  -e ENCRYPTION_KEY=YOUR_KEY \
  ghcr.io/adcontextprotocol/salesagent:latest

# Verify it's running
curl http://localhost:8000/health
```

## Option 2: Clone and Run (Recommended for Development)

```bash
# Clone the repository
git clone https://github.com/adcontextprotocol/salesagent.git
cd salesagent

# Start all services (includes PostgreSQL)
docker compose up -d

# Verify it's running
curl http://localhost:8000/health
```

## First-Time Setup

1. Open http://localhost:8000/admin
2. New tenants start in **Setup Mode** - test credentials work initially
3. Log in with test credentials (see below)
4. Configure SSO in **Users & Access** (see [SSO Setup Guide](user-guide/sso-setup.md))
5. Test your SSO login works
6. Disable Setup Mode to require SSO for all users

### Setup Mode

New tenants start with `auth_setup_mode=true`, which allows test credentials:
- Click "Log in to Dashboard" button on the login page
- Password: `test123`

Once you've configured and tested SSO, disable Setup Mode from the Users & Access page. After that, only SSO authentication works.

## Local Testing with Demo Data

For local testing without a real ad server, you can create a demo tenant with mock data:

```bash
# Add to .env for local testing only
CREATE_DEMO_TENANT=true
```

With `CREATE_DEMO_TENANT=true`, the system creates:
- A "Default Publisher" tenant with the **Mock adapter** (simulates an ad server)
- Sample currencies (USD, EUR, GBP)
- A test principal/advertiser for API access
- Sample products

This demo data lets you explore features without configuring Google Ad Manager or another ad server.

> **Production deployments**: Do NOT set `CREATE_DEMO_TENANT=true`. Start with the default empty tenant, configure your real ad server adapter, set up SSO, and create users through the Admin UI.

## Endpoints

All services are accessible through port 8000:

| Service | URL |
|---------|-----|
| Admin UI | http://localhost:8000/ |
| Admin UI (alternate) | http://localhost:8000/admin |
| MCP Server | http://localhost:8000/mcp/ |
| A2A Server | http://localhost:8000/a2a |
| Health Check | http://localhost:8000/health |

## Connecting an AI Agent

Once running, AI agents can connect via MCP:

```python
from fastmcp.client import Client, StreamableHttpTransport

# Get your token from Admin UI > Advertisers > View Token
transport = StreamableHttpTransport(
    url="http://localhost:8000/mcp/",
    headers={"x-adcp-auth": "your-principal-token"}
)

async with Client(transport=transport) as client:
    # List available products
    products = await client.call_tool("get_products", {"brief": "video ads"})

    # Create a media buy
    result = await client.call_tool("create_media_buy", {
        "product_ids": ["prod_123"],
        "budget": {"amount": 10000, "currency": "USD"},
        "flight_start": "2024-02-01",
        "flight_end": "2024-02-28"
    })
```

## Common Commands

```bash
# View logs
docker compose logs -f

# Stop services
docker compose down

# Reset everything (including database)
docker compose down -v

# Rebuild after code changes
docker compose build && docker compose up -d
```

## Troubleshooting

### Container won't start on first run
If containers don't start properly on the first attempt, this is usually a timing issue:
```bash
docker compose down -v          # Clean up
docker compose build --no-cache # Rebuild without cache
docker compose up -d            # Start fresh
```

### Docker volume conflicts
If you have other Postgres containers, volumes may conflict. The salesagent uses `adcp_` prefixed volumes to avoid conflicts. If issues persist:
```bash
docker compose down -v  # Remove volumes
docker volume prune     # Clean up orphan volumes
docker compose up -d
```

### "No tenant context" error
- Ensure you're using the test login credentials
- Check that migrations ran: `docker compose logs db-init`

### Port 8000 already in use
```bash
lsof -i :8000
kill -9 $(lsof -t -i:8000)
```

### "Authentication required by tenant policy" error
This can happen if the default tenant wasn't created with public discovery enabled:
```bash
docker compose down -v  # Reset database
docker compose up -d    # Recreate demo tenant
```

## Next Steps

- **Deploy to the cloud**: See [deployment/](deployment/) for production deployment guides
- **Configure GAM**: See [adapters/gam/](adapters/gam/) to connect Google Ad Manager
- **User guide**: See [user-guide/](user-guide/) for setting up products, advertisers, and creatives
