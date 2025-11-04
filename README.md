# AdCP Sales Agent

A reference implementation of the Advertising Context Protocol (AdCP) V2.3 sales agent, enabling AI agents to buy advertising inventory through a standardized MCP (Model Context Protocol) interface.

## What is this?

The AdCP Sales Agent is a server that:
- **Exposes advertising inventory** to AI agents via MCP protocol
- **Manages multi-tenant publishers** with isolated data and configuration
- **Integrates with ad servers** like Google Ad Manager, Kevel, and Triton
- **Provides an admin interface** for managing inventory and monitoring campaigns
- **Handles the full campaign lifecycle** from discovery to reporting

## Quick Start

### Standard Setup

```bash
# Clone the repository
git clone https://github.com/adcontextprotocol/salesagent.git
cd salesagent

# Configure secrets (choose one method)
# Method 1: .env.secrets file (recommended)
cp .env.secrets.template .env.secrets
# Edit .env.secrets with your API keys and OAuth credentials

# Method 2: Environment variables
export GEMINI_API_KEY="your-api-key"
export GOOGLE_CLIENT_ID="your-client-id"
export GOOGLE_CLIENT_SECRET="your-client-secret"
export SUPER_ADMIN_EMAILS="your-email@example.com"

# Start with Docker Compose
docker-compose up -d

# Access services
open http://localhost:8001  # Admin UI
```

### Conductor Setup

```bash
# One-time: Create secrets file in project root
cp .env.secrets.template .env.secrets
# Edit .env.secrets with your actual values

# Create Conductor workspace (auto-runs setup)
# Services will be available on unique ports
# Check .env file for your workspace's ports
```

**Standard Setup** starts services on:
- PostgreSQL database (port 5432)
- MCP server (port 8080)
- Admin UI (port 8001)

**Conductor Setup** uses unique ports per workspace:
- Check `.env` file for your workspace's assigned ports
- Example: PostgreSQL (5486), MCP (8134), Admin UI (8055)

## Documentation

- **[Setup Guide](docs/SETUP.md)** - Installation and configuration
- **[Development Guide](docs/DEVELOPMENT.md)** - Local development and contributing
- **[Testing Guide](docs/testing/README.md)** - Running and writing tests
- **[Deployment Guide](docs/deployment.md)** - Production deployment options
- **[Troubleshooting Guide](docs/TROUBLESHOOTING.md)** - Monitoring and debugging
- **[Architecture](docs/ARCHITECTURE.md)** - System design and database schema

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
The primary interface for AI agents to interact with the AdCP Sales Agent. Uses FastMCP with HTTP/SSE transport.

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
    url="http://localhost:8080/mcp/",
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
    └── fly/             # Fly.io deployment configs
```

## Requirements

- Python 3.12+
- Docker and Docker Compose (for easy deployment)
- PostgreSQL (production) or SQLite (development)
- Google OAuth credentials (for Admin UI)
- Gemini API key (for AI features)

## Contributing

We welcome contributions! Please see our [Development Guide](docs/DEVELOPMENT.md) for:
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
See [Database Patterns Guide](docs/database-patterns.md) for details.

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

- **Issues**: [GitHub Issues](https://github.com/adcontextprotocol/salesagent/issues)
- **Discussions**: [GitHub Discussions](https://github.com/adcontextprotocol/salesagent/discussions)
- **Documentation**: [docs/](docs/)

## License

Apache 2.0 License - see [LICENSE](LICENSE) file for details.

## Related Projects

- [AdCP Specification](https://github.com/adcontextprotocol/adcp-spec) - Protocol specification
- [MCP SDK](https://github.com/modelcontextprotocol) - Model Context Protocol tools
- [FastMCP](https://github.com/jlowin/fastmcp) - MCP server framework
# CI test trigger Tue Sep  2 16:01:44 EDT 2025
