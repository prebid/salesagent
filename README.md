# Prebid Sales Agent

An open-source implementation of the [Ad Context Protocol (AdCP)](https://adcontextprotocol.org)
sales-agent role, maintained under Prebid.org. It lets AI agents discover and buy advertising
inventory through standardized MCP and A2A interfaces.

> **Status: alpha.** A pre-1.0 implementation of a pre-release protocol
> (AdCP 3.1.0-beta.3), under active development. APIs still change between
> releases — `v2.0.0` introduced breaking changes — so pin a version and expect
> to adapt on upgrade. It is one of several AdCP sales-agent implementations,
> not a sole or canonical reference. The codebase is substantial and functional
> (Google Ad Manager integration, multi-tenant isolation, 16 AdCP tools,
> extensive tests), but treat it as alpha and validate before relying on it.

## What is this?

The Prebid Sales Agent is a server that:
- **Exposes advertising inventory** to AI agents via MCP (Model Context Protocol) and A2A (Agent-to-Agent)
- **Integrates with ad servers** like Google Ad Manager
- **Provides an admin interface** for managing inventory and campaigns
- **Handles the full campaign lifecycle** from discovery to reporting

## AdCP Compatibility

This implementation targets **AdCP spec version 3.1.0-beta.3** via the `adcp==5.7.0`
Python SDK. That spec version is a **beta** — request/response shapes are not yet
frozen, and SDK bumps can change them. See
[docs/adcp-spec-version.md](docs/adcp-spec-version.md) for the SDK-to-spec mapping
and bump procedure. The pin is enforced by a CI guard
(`tests/unit/test_adcp_spec_version.py`), which fails on drift.

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

Because this is alpha software tracking a beta protocol, pin the version you deploy
and re-test after every upgrade — minor releases can carry breaking changes.

### After Deployment

Configure via the Admin UI:
1. Configure your ad server (Settings → Adapters)
2. Set up products that match your GAM line items
3. Add advertisers who will use the MCP API
4. Set your custom domain (Settings → General)

---

## Development Setup

```bash
git clone https://github.com/prebid/salesagent.git
cd salesagent
make setup    # One command: installs deps, starts Docker, verifies health
```

See the [Getting Started guide](docs/development/GETTING_STARTED.md) for prerequisites, manual setup steps, testing workflows, and common operations.

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

## AdCP Tools

The MCP server exposes 16 AdCP tools (registered in
[`src/core/main.py`](src/core/main.py)). The server also exposes AdCP operations over A2A as JSON-RPC skills; the exact set differs slightly from the MCP tool list.

| Area | Tools |
|------|-------|
| **Discovery** | `get_products`, `list_creative_formats`, `list_authorized_properties`, `get_adcp_capabilities` |
| **Media buys** | `create_media_buy`, `update_media_buy`, `get_media_buys`, `get_media_buy_delivery`, `update_performance_index` |
| **Creatives** | `sync_creatives`, `list_creatives` |
| **Tasks / workflow** | `list_tasks`, `get_task`, `complete_task` |
| **Accounts** | `list_accounts`, `sync_accounts` |

## Ad Server Adapters

Adapters live in [`src/adapters/`](src/adapters/), are registered in
`src/adapters/__init__.py`, and are selected per tenant. The adapter interface is
defined in `src/adapters/base.py`. Maturity varies — GAM is the most complete;
the others are at earlier stages.

| Adapter | Key(s) | Notes |
|---------|--------|-------|
| **Google Ad Manager** | `gam`, `google_ad_manager` | The most developed adapter (`src/adapters/gam/`). Supports CPM, VCPM, CPC, and FLAT_RATE pricing with automatic line-item-type selection. See [docs/adapters/](docs/adapters/). |
| **Broadstreet** | `broadstreet` | Broadstreet integration (`src/adapters/broadstreet/`), with Admin UI configuration. |
| **Kevel** | `kevel` | Kevel integration (`src/adapters/kevel.py`). |
| **Triton Digital** | `triton`, `triton_digital` | Triton Digital integration (`src/adapters/triton_digital.py`). |
| **Mock** | `mock` | Simulated ad server for testing and local development (`src/adapters/mock_ad_server.py`). Supports all AdCP pricing models; zero real spend. |

## Capabilities

**For AI agents**
- Natural-language product discovery
- Media-buy creation, updates, and delivery reporting
- Creative sync and listing with approval workflows

**For publishers**
- Multi-tenant isolation (data scoped per publisher)
- Adapter pattern for multiple ad servers
- Real-time activity dashboard (Server-Sent Events)
- Human-in-the-loop workflow/approval system
- Audit logging of operations
- Admin web UI with Google OAuth

**For developers**
- MCP interface (FastMCP, HTTP/SSE transport)
- A2A interface (JSON-RPC 2.0)
- REST API for tenant management
- Docker-based local and production deployment
- Unit, integration, e2e, admin, BDD, and UI test suites

## Protocol Support

### MCP (Model Context Protocol)
The primary interface for AI agents. Built with FastMCP over HTTP/SSE transport.

### A2A (Agent-to-Agent Protocol)
JSON-RPC 2.0 server for agent-to-agent communication:
- **Endpoint**: `/a2a` (also served on port 8091)
- **Discovery**: `/.well-known/agent.json` (also `/.well-known/agent-card.json`, `/agent.json`)
- **Authentication**: Bearer tokens via Authorization header
- **Library**: Built with `a2a-sdk[http-server]`

## Testing Backend

The mock server provides AdCP testing capabilities for developers, driven by request headers:

### Request Headers
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
- **Campaign Lifecycle Simulation**: Event progression (creation → completion)
- **Error Scenario Testing**: Budget exceeded, delivery issues, platform errors
- **Time Simulation**: Fast-forward campaigns for testing
- **Session Isolation**: Parallel test execution without conflicts
- **Zero real spend during testing**

```python
# Example: Test with time simulation
headers = {
    "x-adcp-auth": "your_token",
    "X-Dry-Run": "true",
    "X-Mock-Time": "2026-02-15T12:00:00Z",
    "X-Test-Session-ID": "test-123",
}

# Use with any MCP client for safe testing
```

See `examples/mock_server_testing_demo.py` for complete testing examples.

## Using the MCP Client

```python
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

# Connect to the server
transport = StreamableHttpTransport(
    url="http://localhost:8000/mcp/",
    headers={"x-adcp-auth": "your_token"},
)
client = Client(transport=transport)

async with client:
    # 1. Discover products. Each product carries one or more `pricing_options`.
    products = await client.tools.get_products(brief="video ads for sports content")

    # 2. Book a media buy. Each package references a product and one of its
    #    pricing options. `idempotency_key` is REQUIRED (16-255 chars) — reusing
    #    the same key makes retries safe (returns the original buy, no duplicate).
    result = await client.tools.create_media_buy(
        brand="acme.com",                     # domain shorthand for a BrandReference
        start_time="2026-08-01T00:00:00Z",    # ISO 8601, or "asap"
        end_time="2026-08-31T23:59:59Z",
        packages=[
            {
                "product_id": "ctv_sports",
                "pricing_option_id": "cpm_usd",  # from product.pricing_options
                "budget": 50000,                 # amount in the pricing option's currency
            }
        ],
        idempotency_key="acme-ctv-2026-08-0001",
    )
```

## Project Structure

```
salesagent/
├── src/                       # Source code
│   ├── core/                  # Core MCP server components
│   │   ├── main.py            # MCP server + tool registration
│   │   ├── schemas/           # Pydantic models (AdCP-compliant; package)
│   │   ├── tools/             # Tool _impl functions + wrappers (package)
│   │   ├── database/          # SQLAlchemy models, session, repositories
│   │   ├── config_loader.py   # Configuration management
│   │   └── audit_logger.py    # Security and audit logging
│   ├── services/              # Business logic services
│   ├── adapters/              # Ad server integrations
│   │   ├── base.py            # Base adapter interface
│   │   ├── gam/               # Google Ad Manager adapter
│   │   ├── broadstreet/       # Broadstreet adapter
│   │   └── mock_ad_server.py  # Mock adapter
│   ├── a2a_server/            # A2A (agent-to-agent) server
│   └── admin/                 # Admin UI (Flask)
├── scripts/                   # Setup, dev, ops, and deploy scripts
├── tests/                     # unit / integration / e2e / admin / bdd
├── docs/                      # Documentation
├── examples/                  # Example code
├── alembic/                   # Database migrations
├── templates/                 # Jinja2 templates
└── config/                    # Configuration files (incl. nginx/)
```

## Requirements

- Python 3.12+
- Docker and Docker Compose (for easy deployment)
- PostgreSQL (Docker Compose handles this automatically)
- Google OAuth credentials (for Admin UI)
- Gemini API key (for AI-powered product discovery)

## Contributing

We welcome contributions! Please see our [Development Guide](docs/development/README.md) for:
- Setting up your development environment
- Running tests
- Code style guidelines
- Creating pull requests

### Important: Database Access Patterns

When contributing, follow the standardized database patterns. All data access goes
through SQLAlchemy 2.0 ORM via repository classes — see
[Contributing Guide](docs/development/contributing.md) and `CLAUDE.md` for details.

```python
# Use a context-managed session
from src.core.database.database_session import get_db_session
with get_db_session() as session:
    # Your database operations
    session.commit()
```

## Admin Features

### Multi-Tenant User Access
Users can belong to multiple tenants with the same email address (like GitHub, Slack, etc.):
- Sign up for multiple publisher accounts with one Google login
- Different roles per tenant (admin in one, viewer in another)
- Users are tenant-scoped via a composite unique constraint `(tenant_id, email)`

### Tenant Deactivation (Soft Delete)
Deactivate test or unused tenants without losing data:
- All data preserved (media buys, creatives, principals)
- Hidden from login and tenant selection; API access blocked
- Reactivatable by a super admin

### Self-Signup
New users can self-provision tenants:
- Google OAuth authentication
- GAM-only for self-signup (other adapters via support)
- Auto-creates tenant, user, and default principal
- Available at `/signup` on the main domain

## Support

- **Issues**: [GitHub Issues](https://github.com/prebid/salesagent/issues)
- **Discussions**: [GitHub Discussions](https://github.com/prebid/salesagent/discussions)
- **Documentation**: [docs/](docs/)

## License

Apache 2.0 License - see [LICENSE](LICENSE) file for details.

## Related Projects

- [AdCP Specification](https://github.com/adcontextprotocol/adcp) - Protocol specification
- [Model Context Protocol](https://github.com/modelcontextprotocol) - MCP tools and SDKs
- [FastMCP](https://github.com/jlowin/fastmcp) - MCP server framework
