# Architecture Guide

## System Architecture

### Core Components

```
                     ┌──────────────────┐
                     │   nginx :8000    │
                     └────────┬─────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
       /admin/          /mcp/           /a2a
              │               │               │
┌─────────────────────────────────────────────────────┐
│           Unified FastAPI App (src/app.py) :8080    │
│                                                     │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────────┐ │
│  │ Admin (Flask │ │ MCP Server   │ │ A2A Server   │ │
│  │  via WSGI)  │ │  (FastMCP)   │ │ (python-a2a) │ │
│  └─────────────┘ └──────────────┘ └──────────────┘ │
│                                                     │
│  UnifiedAuthMiddleware (ASGI, scope["state"])        │
└─────────────────────┬───────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        │             │             │
┌───────────────┐ ┌────────────┐ ┌──────────────┐
│   PostgreSQL  │ │ Ad Server  │ │   Gemini     │
│   Database    │ │ Adapters   │ │   AI API     │
└───────────────┘ └────────────┘ └──────────────┘
```

### Multi-Tenant Architecture

Database-backed tenant isolation with:
- **Tenants** - Publishers with isolated data
- **Principals** - Advertisers within tenants
- **Products** - Inventory offerings
- **Media Buys** - Active campaigns

### Authentication Flow

1. **MCP API** - Token-based via x-adcp-auth header
2. **A2A API** - Token-based via x-adcp-auth or Authorization: Bearer
3. **Admin UI** - Google OAuth with role-based access
4. **UnifiedAuthMiddleware** - Single ASGI middleware extracts token into scope["state"]
5. **Principal Resolution** - Token → Principal → Tenant → Adapter

## Database Schema

### Core Tables

```sql
-- Publishers
tenants (
  tenant_id UUID PRIMARY KEY,
  name, subdomain, config JSONB,
  created_at, updated_at
)

-- Advertisers
principals (
  principal_id UUID PRIMARY KEY,
  tenant_id FK, name, access_token,
  platform_mappings JSONB
)

-- Inventory
products (
  product_id UUID PRIMARY KEY,
  tenant_id FK, name, description,
  formats JSONB, pricing_type,
  base_price, targeting_template JSONB
)

-- Campaigns
media_buys (
  media_buy_id UUID PRIMARY KEY,
  tenant_id FK, principal_id FK,
  status, config JSONB, total_budget,
  flight_start_date, flight_end_date
)

-- Creatives
creatives (
  creative_id UUID PRIMARY KEY,
  tenant_id FK, principal_id FK,
  format, status, content JSONB
)

-- Audit Trail
audit_logs (
  id SERIAL PRIMARY KEY,
  tenant_id FK, operation, principal_id,
  success, details JSONB, timestamp
)
```

### Data Flow

1. **Request** → MCP Server receives API call
2. **Auth** → Resolve principal from token
3. **Tenant** → Load tenant configuration
4. **Adapter** → Instantiate platform adapter
5. **Operation** → Execute business logic
6. **Audit** → Log to database
7. **Response** → Return to client

## Adapter Pattern

### Base Interface

```python
class AdServerAdapter(ABC):
    @abstractmethod
    def get_avails(self, request: GetAvailsRequest) -> GetAvailsResponse:
        """Check inventory availability"""

    @abstractmethod
    def create_media_buy(self, request, packages, start_time, end_time):
        """Create campaign/order"""

    @abstractmethod
    def activate_media_buy(self, media_buy_id: str):
        """Activate pending campaign"""
```

### Adapter Implementations

- **GoogleAdManagerAdapter** - Full GAM integration
- **KevelAdapter** - Kevel ad server
- **MockAdapter** - Testing and development
- **TritonAdapter** - Audio advertising

Each adapter handles:
- Platform authentication
- API translation
- Error handling
- Dry-run simulation

## Transport Parity Invariant

**All business behavior MUST be identical across MCP, A2A, and REST transports.**

The system exposes the same tools through three transports:
- **MCP** — FastMCP tools at `/mcp/`
- **A2A** — Agent-to-agent protocol at `/a2a`
- **REST** — Admin API at `/admin/api/`

All three call the same `_impl` functions (Critical Pattern #5). The transport
wrappers handle only: identity resolution, error format translation, and
protocol framing. No business logic, validation, or data transformation lives
in the wrappers.

### Why This Matters

Historical drift put validation, error handling, and edge-case behavior in
transport wrappers rather than `_impl`. A test passing against one transport may
fail against another because:
- Validation exists in A2A but not MCP (or vice versa)
- Error codes differ between transports for the same failure
- Parameters are silently dropped by one wrapper but not another

### Testing Enforcement

Every behavioral integration test MUST verify all three transports:

```python
@pytest.mark.parametrize("transport", ["mcp", "a2a", "rest"])
def test_empty_update_rejected(transport, ...):
    """BR-RULE-022: identical behavior regardless of transport."""
    response = call_tool(transport, "update_media_buy", {...})
    assert response.error_code == "VALIDATION_ERROR"
```

When transport parity fails, the bug is in the wrapper that deviates — fix the
wrapper, don't weaken the test.

### Structural Guards

Four guards enforce the transport boundary (see `docs/development/structural-guards.md`):
- `test_transport_agnostic_impl.py` — no transport imports in `_impl`
- `test_impl_resolved_identity.py` — `_impl` takes `ResolvedIdentity`, not `Context`
- `test_no_toolerror_in_impl.py` — `_impl` raises `AdCPError`, not `ToolError`
- `test_architecture_boundary_completeness.py` — wrappers forward all `_impl` params

## MCP Protocol Implementation

See also: [A2A and MCP Agent Flows](./a2a-mcp-agent-flows.md) for buyer, governance, and creative protocol diagrams.

### FastMCP Framework

```python
from fastmcp import FastMCP

mcp = FastMCP("AdCP Sales Agent")

@mcp.tool()
async def get_products(
    brief: str | None = None
) -> GetProductsResponse:
    return _get_products_impl(brief=brief)
```

### Transport Layer

- HTTP transport with SSE support
- Header-based authentication
- JSON request/response format
- Streaming for large responses

## Targeting System

### Two-Tier Access Model

1. **Overlay Dimensions** (Principal Access)
   - Geography (country, state, city, DMA)
   - Demographics (age, gender, income)
   - Interests and behaviors
   - Devices and platforms
   - AEE signals

2. **Managed-Only Dimensions** (Internal)
   - First-party segments
   - Lookalike audiences
   - Platform optimizations
   - Reserved inventory

### Signal Integration

```python
# AdCP Request
{
  "targeting_overlay": {
    "geo_countries": ["US"],
    "signals": ["sports_enthusiasts", "auto_intenders"]
  }
}

# Platform Translation (GAM)
{
  "geoTargeting": {"targetedLocations": [{"id": "2840"}]},
  "customTargeting": {
    "logicalOperator": "OR",
    "children": [
      {"key": "aee_signal", "values": ["sports_enthusiasts"]},
      {"key": "aee_signal", "values": ["auto_intenders"]}
    ]
  }
}
```

## AI Integration

### Latest Gemini Flash Model

Used for:
- Product configuration suggestions
- Targeting optimization
- Creative analysis
- Natural language processing

### Product Analysis Pipeline

1. **Input** - Product description
2. **Analysis** - Extract key attributes
3. **Matching** - Find similar products
4. **Configuration** - Generate settings
5. **Validation** - Check constraints

## Security Architecture

### Authentication Layers

1. **Super Admin** - Environment variable whitelist
2. **OAuth** - Google identity verification
3. **Tenant Admin** - Publisher-level access
4. **Principal** - Advertiser API tokens

### Audit System

- Database-backed logging
- Principal context tracking
- Operation success/failure
- Security violation detection
- Compliance reporting

### Data Isolation

- Tenant-scoped queries
- Principal validation
- Cross-tenant protection
- SQL injection prevention

## Performance Optimizations

### Database

- Connection pooling
- Index optimization
- JSONB for PostgreSQL
- Query result caching

### Caching Strategy

- In-memory product cache
- Redis for session storage (optional)
- CDN for static assets
- API response caching

### Async Operations

- Background task queue
- Webhook notifications
- Batch processing
- Long-running operations

## Deployment Architecture

### Docker Compose

```yaml
services:
  postgres:
    image: postgres:16
    volumes:
      - postgres_data:/var/lib/postgresql/data

  adcp-server:
    build: .
    depends_on: [postgres]
    ports: ["8080:8080"]

  nginx:
    image: nginx:alpine
    depends_on: [adcp-server]
    ports: ["8000:8000"]
```

### Production Deployment

Single-process architecture with:
- nginx reverse proxy (port 8000)
- Unified FastAPI app (port 8080) serving Admin, MCP, and A2A
- Managed PostgreSQL
- Persistent volume

### Production Considerations

- Health checks on all services
- Graceful shutdown handling
- Log aggregation
- Metric collection
- Error tracking
- Backup automation

## Extension Points

### Adding Features

1. **New MCP Tools** - Add to main.py
2. **Admin UI Pages** - Extend Flask routes
3. **Database Tables** - Create migrations
4. **API Endpoints** - Add to appropriate module

### Integration Options

- Webhook notifications
- External API calls
- Custom adapters
- Plugin system (future)
