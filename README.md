# Prebid Sales Agent

A reference implementation of the [Ad Context Protocol (AdCP)](https://adcontextprotocol.org) sales agent, enabling AI agents to buy advertising inventory through a standardized MCP interface. This codebase is maintained under Prebid.org as the Prebid Sales Agent reference implementation.

## What is this?

The Prebid Sales Agent is a server that:
- **Exposes advertising inventory** to AI agents via MCP (Model Context Protocol) and A2A (Agent-to-Agent)
- **Integrates with ad servers** like Google Ad Manager
- **Provides an admin interface** for managing inventory and campaigns
- **Handles the full campaign lifecycle** from discovery to reporting

## Choose Your Path

| I want to... | Start here |
|--------------|------------|
| **Deploy my own sales agent** (publisher) | [Quickstart Guide](docs/quickstart.md) |
| **Evaluate or develop locally** | [Quick Start](#quick-start-evaluation) below |
| **Run a multi-tenant platform** | [Deployment Guide](docs/deployment/multi-tenant.md) |

---

## Quick Start (Evaluation)

Try the sales agent locally:

```bash
# Clone and start
git clone https://github.com/prebid/salesagent.git
cd salesagent
docker compose up -d

# Test the MCP interface
uvx adcp http://localhost:8000/mcp/ --auth test-token list_tools
uvx adcp http://localhost:8000/mcp/ --auth test-token get_products '{"brief":"video"}'
```

Access services at http://localhost:8000:
- **Admin UI:** `/admin` or just click "Log in to Dashboard" (test credentials: `test123`)
- **MCP Server:** `/mcp/`
- **A2A Server:** `/a2a`

For production deployment, see the [Quickstart Guide](docs/quickstart.md).

---

## Publisher Deployment

Publishers deploy their own sales agent. Choose based on your needs:

| Platform | Time | Difficulty | Guide |
|----------|------|------------|-------|
| **Docker** (local/on-prem) | 2 min | Easy | [quickstart.md](docs/quickstart.md) |
| **Fly.io** (cloud) | 10-15 min | Medium | [fly.md](docs/deployment/walkthroughs/fly.md) |
| **Google Cloud Run** | 15-20 min | Medium | [gcp.md](docs/deployment/walkthroughs/gcp.md) |

**Docker is the fastest** - it bundles PostgreSQL and just works. Cloud platforms require separate database setup.

### After Deployment

Configure via the Admin UI:
1. Configure your ad server (Settings → Adapters)
2. Set up products that match your GAM line items
3. Add advertisers who will use the MCP API
4. Set your custom domain (Settings → General)

---

## Development Setup

For local development with hot-reload:

```bash
git clone https://github.com/prebid/salesagent.git
cd salesagent
cp .env.template .env

# Build and start (builds from source with hot-reload)
docker compose build
docker compose up -d

# View logs
docker compose logs -f
```

Access at http://localhost:8000:
- **Admin UI:** `/admin` - click "Log in to Dashboard" (password: `test123`)
- **MCP Server:** `/mcp/`
- **A2A Server:** `/a2a`

Migrations run automatically on startup.

Run tests:
```bash
uv run pytest tests/unit/ -x       # Unit tests
uv run pytest tests/integration/   # Integration tests (requires PostgreSQL)
uv run pytest tests/e2e/           # E2E tests (uses Docker)
```

See [Development Guide](docs/development/README.md) for contributing.

---

## Google Ad Manager Setup

For GAM integration, choose your authentication method:

**Service Account (Recommended for Production):**
- No OAuth credentials needed
- Configure service account JSON in Admin UI
- See [GAM Adapter Guide](docs/adapters/gam/README.md) for setup

**OAuth (Development/Testing):**
1. Create OAuth credentials at [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Add to .env:
   ```bash
   GAM_OAUTH_CLIENT_ID=your-client-id.apps.googleusercontent.com
   GAM_OAUTH_CLIENT_SECRET=your-client-secret
   ```
3. Configure in Admin UI: Settings → Adapters → Google Ad Manager

---

## Using with Claude Desktop

Add to your Claude config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "adcp": {
      "command": "uvx",
      "args": ["mcp-remote", "http://localhost:8000/mcp/", "--header", "x-adcp-auth: YOUR_TOKEN"]
    }
  }
}
```

Get your token from Admin UI → Advertisers → (select advertiser) → API Token.

---

## Troubleshooting

**Container won't start?**
```bash
docker compose logs adcp-server | head -50
```

**GAM OAuth error?**
- Verify `GAM_OAUTH_CLIENT_ID` and `GAM_OAUTH_CLIENT_SECRET` in `.env`
- Restart: `docker compose restart`

**More help:** [Troubleshooting Guide](docs/development/troubleshooting.md)

## Documentation

### Deployment Guides
- **[Quickstart](docs/quickstart.md)** - Docker deployment (2 min)
- **[Fly.io](docs/deployment/walkthroughs/fly.md)** - Cloud deployment (10-15 min)
- **[Google Cloud Run](docs/deployment/walkthroughs/gcp.md)** - GCP deployment (15-20 min)
- **[Single-Tenant](docs/deployment/single-tenant.md)** - Single publisher deployment
- **[Multi-Tenant](docs/deployment/multi-tenant.md)** - Platform deployment

### Reference
- **[Development Guide](docs/development/README.md)** - Local development and contributing
- **[Architecture](docs/development/architecture.md)** - System design and database schema
- **[Troubleshooting Guide](docs/development/troubleshooting.md)** - Monitoring and debugging

## Key Features

### For AI Agents
- **Product Discovery** - Natural language search for advertising products
- **Campaign Creation** - Automated media buying with targeting
- **Creative Management** - Upload and approval workflows
- **Performance Monitoring** - Real-time campaign metrics

### For Publishers
- **Multi-Tenant System** - Isolated data per publisher
- **Adapter Pattern** - Support for multiple ad servers
- **Real-time Dashboard** - Live activity feed with Server-Sent Events (SSE)
- **Workflow Management** - Unified system for human-in-the-loop approvals
- **Operations Monitoring** - Track all media buys, workflows, and system activities
- **Admin Interface** - Web UI with Google OAuth
- **Audit Logging** - Complete operational history

### For Developers
- **MCP Protocol** - Standard interface for AI agents
- **A2A Protocol** - Agent-to-Agent communication via JSON-RPC 2.0
- **REST API** - Programmatic tenant management
- **Docker Deployment** - Easy local and production setup
- **Comprehensive Testing** - Unit, integration, and E2E tests

## Protocol Support

### MCP (Model Context Protocol)
The primary interface for AI agents to interact with the Prebid Sales Agent. Uses FastMCP with HTTP/SSE transport.

### A2A (Agent-to-Agent Protocol)
JSON-RPC 2.0 compliant server for agent-to-agent communication:
- **Endpoint**: `/a2a` (also available at port 8091)
- **Discovery**: `/.well-known/agent.json`
- **Authentication**: Bearer tokens via Authorization header
- **Library**: Built with standard `python-a2a` library

## Testing Backend

The mock server provides comprehensive AdCP testing capabilities for developers:

### Testing Headers Support
- **X-Dry-Run**: Test operations without real execution
- **X-Mock-Time**: Control time for deterministic testing
- **X-Jump-To-Event**: Skip to specific campaign events
- **X-Test-Session-ID**: Isolate parallel test sessions
- **X-Auto-Advance**: Automatic event progression
- **X-Force-Error**: Simulate error conditions

### Response Headers
- **X-Next-Event**: Next expected campaign event
- **X-Next-Event-Time**: Timestamp for next event
- **X-Simulated-Spend**: Current campaign spend simulation

### Testing Features
- **Campaign Lifecycle Simulation**: Complete event progression (creation → completion)
- **Error Scenario Testing**: Budget exceeded, delivery issues, platform errors
- **Time Simulation**: Fast-forward campaigns for testing
- **Session Isolation**: Parallel test execution without conflicts
- **Production Safety**: Zero real spend during testing

```python
# Example: Test with time simulation
headers = {
    "x-adcp-auth": "your_token",
    "X-Dry-Run": "true",
    "X-Mock-Time": "2025-02-15T12:00:00Z",
    "X-Test-Session-ID": "test-123"
}

# Use with any MCP client for safe testing
```

See `examples/mock_server_testing_demo.py` for complete testing examples.

## Using the MCP Client

```python
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

# Connect to server
headers = {"x-adcp-auth": "your_token"}
transport = StreamableHttpTransport(
    url="http://localhost:8000/mcp/",
    headers=headers
)
client = Client(transport=transport)

# Discover products
async with client:
    products = await client.tools.get_products(
        brief="video ads for sports content"
    )

    # Create media buy
    result = await client.tools.create_media_buy(
        product_ids=["ctv_sports"],
        total_budget=50000,
        flight_start_date="2025-02-01",
        flight_end_date="2025-02-28"
    )
```

## Project Structure

```
salesagent/
├── src/                    # Source code
│   ├── core/              # Core MCP server components
│   │   ├── main.py        # MCP server implementation
│   │   ├── schemas.py     # API schemas and data models
│   │   ├── config_loader.py  # Configuration management
│   │   ├── audit_logger.py   # Security and audit logging
│   │   └── database/      # Database layer
│   │       ├── models.py  # SQLAlchemy models
│   │       ├── database.py # Database initialization
│   │       └── database_session.py # Session management
│   ├── services/          # Business logic services
│   │   ├── ai_product_service.py # AI product management
│   │   ├── targeting_capabilities.py # Targeting system
│   │   └── gam_inventory_service.py # GAM integration
│   ├── adapters/          # Ad server integrations
│   │   ├── base.py        # Base adapter interface
│   │   ├── google_ad_manager.py # GAM adapter
│   │   └── mock_ad_server.py # Mock adapter
│   └── admin/             # Admin UI (Flask)
│       ├── app.py         # Flask application
│       ├── blueprints/    # Flask blueprints
│       │   ├── tenants.py # Tenant dashboard
│       │   ├── tasks.py   # Task management (DEPRECATED - see workflow system)
│       │   └── activity_stream.py # Real-time activity feed
│       └── server.py      # Admin server
├── scripts/               # Utility scripts
│   ├── setup/            # Setup and initialization
│   ├── dev/              # Development tools
│   ├── ops/              # Operations scripts
│   └── deploy/           # Deployment scripts
├── tests/                # Test suite
│   ├── unit/            # Unit tests
│   ├── integration/     # Integration tests
│   └── e2e/             # End-to-end tests
├── docs/                 # Documentation
├── examples/             # Example code
├── tools/                # Demo and simulation tools
├── alembic/             # Database migrations
├── templates/           # Jinja2 templates
└── config/              # Configuration files
    └── nginx/           # Nginx configuration files
```

## Requirements

- Python 3.12+
- Docker and Docker Compose (for easy deployment)
- PostgreSQL (Docker Compose handles this automatically)
- Google OAuth credentials (for Admin UI)
- Gemini API key (for AI features)

## Contributing

We welcome contributions! Please see our [Development Guide](docs/development/README.md) for:
- Setting up your development environment
- Running tests
- Code style guidelines
- Creating pull requests

### Important: Database Access Patterns

When contributing, please follow our standardized database patterns:
```python
# ✅ CORRECT - Use context manager
from database_session import get_db_session
with get_db_session() as session:
    # Your database operations
    session.commit()

# ❌ WRONG - Manual management
conn = get_db_connection()
# operations
conn.close()  # Prone to leaks
```
See [Contributing Guide](docs/development/contributing.md) for details.

## Admin Features

### Multi-Tenant User Access
Users can belong to multiple tenants with the same email address (like GitHub, Slack, etc.):
- Sign up for multiple publisher accounts with one Google login
- Different roles per tenant (admin in one, viewer in another)
- No "email already exists" errors - users are tenant-scoped

**Migration**: Database schema updated with composite unique constraint `(tenant_id, email)`. See `alembic/versions/aff9ca8baa9c_allow_users_multi_tenant_access.py`

### Tenant Deactivation (Soft Delete)
Deactivate test or unused tenants without losing data:

**How to deactivate:**
1. Go to Settings → Danger Zone
2. Type tenant name exactly to confirm
3. Click "Deactivate Sales Agent"

**What happens:**
- ✅ All data preserved (media buys, creatives, principals)
- ❌ Hidden from login and tenant selection
- ❌ API access blocked
- ℹ️ Can be reactivated by super admin

**Reactivation** (super admin only):
```bash
POST /admin/tenant/{tenant_id}/reactivate
```

### Self-Signup
New users can self-provision tenants:
- Google OAuth authentication
- GAM-only for self-signup (other adapters via support)
- Auto-creates tenant, user, and default principal
- Available at `/signup` on main domain

## Support

- **Issues**: [GitHub Issues](https://github.com/prebid/salesagent/issues)
- **Discussions**: [GitHub Discussions](https://github.com/prebid/salesagent/discussions)
- **Documentation**: [docs/](docs/)

## License

Apache 2.0 License - see [LICENSE](LICENSE) file for details.

## Related Projects

- [AdCP Specification](https://github.com/adcontextprotocol/adcp-spec) - Protocol specification
- [MCP SDK](https://github.com/modelcontextprotocol) - Model Context Protocol tools
- [FastMCP](https://github.com/jlowin/fastmcp) - MCP server framework
