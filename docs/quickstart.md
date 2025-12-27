# Quickstart Guide

Get the AdCP Sales Agent running locally in under 5 minutes.

## Prerequisites

- Docker and Docker Compose installed
- (Optional) Gemini API key for AI-powered creative review

## Quick Start

```bash
# 1. Download the compose file
curl -O https://raw.githubusercontent.com/adcontextprotocol/salesagent/main/docker-compose.yml

# 2. Create environment file with demo mode enabled
cat > .env << 'EOF'
CREATE_DEMO_TENANT=true
SUPER_ADMIN_EMAILS=your-email@example.com
GEMINI_API_KEY=your-gemini-key
EOF

# 3. Start services
docker compose up -d

# 4. Verify it's running
curl http://localhost:8000/health
```

## Access the Admin UI

Open http://localhost:8000/admin

In local development mode, use the test login:
- Email: `test_super_admin@example.com`
- Password: `test123`

## What Gets Created

With `CREATE_DEMO_TENANT=true`, the system creates:
- A default tenant with the **Mock adapter** (simulates an ad server)
- Sample currencies (USD, EUR, GBP)
- A test principal/advertiser for API access
- Sample products

This demo data lets you explore all features without configuring a real ad server.

> **Production deployments**: Omit `CREATE_DEMO_TENANT=true` to start with a clean slate and create your tenant via the Admin UI signup flow.

## Services

All services are accessible through port 8000:

| Service | URL |
|---------|-----|
| Admin UI | http://localhost:8000/admin |
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

## Development Mode

For development with hot-reload:

```bash
git clone https://github.com/adcontextprotocol/salesagent.git
cd salesagent
cp .env.template .env
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

## Troubleshooting

### "No tenant context" error
- Ensure you're using the test login credentials
- Check that migrations ran: `docker compose logs adcp-server | grep migration`

### Port 8000 already in use
```bash
lsof -i :8000
kill -9 $(lsof -t -i:8000)
```

### Container won't start
```bash
docker compose logs adcp-server
docker compose down -v
docker compose up -d
```

## Next Steps

- **Deploy to the cloud**: See [deployment/](deployment/) for production deployment guides
- **Configure GAM**: See [adapters/gam/](adapters/gam/) to connect Google Ad Manager
- **User guide**: See [user-guide/](user-guide/) for setting up products, advertisers, and creatives
