# Mock Adapter Developer Guide

## Overview

The Mock Adapter is a full-featured AdCP-compliant testing adapter that simulates ad server behavior without requiring external API credentials or making real API calls. It's designed for:

- **Local development** - Test AdCP integrations without connecting to real ad servers
- **CI/CD testing** - Fast, deterministic tests for automated pipelines
- **AI agent development** - Accelerated delivery simulation for testing agent responses
- **Protocol compliance testing** - Validate AdCP V2.3 specification adherence

## Supported Capabilities

### ✅ Fully Supported AdCP Operations

The mock adapter implements all AdCP V2.3 operations:

| Operation | Support Level | Notes |
|-----------|--------------|-------|
| **`get_products`** | ✅ Full | Returns mock product catalog |
| **`create_media_buy`** | ✅ Full | Creates in-memory campaigns with validation |
| **`sync_creatives`** | ✅ Full | Simulates creative upload and approval |
| **`get_media_buy_delivery`** | ✅ Full | Returns simulated delivery metrics |
| **`update_media_buy`** | ✅ Full | Supports pause, resume, budget updates |
| **`update_performance_index`** | ✅ Full | Accepts performance signals |
| **`list_creatives`** | ✅ Full | Returns uploaded creatives |
| **`list_creative_formats`** | ✅ Full | Returns supported formats |
| **`list_authorized_properties`** | ✅ Full | Returns mock property list |

### 🎯 Advanced Features

#### 1. **Accelerated Delivery Simulation**
- Time-accelerated campaign delivery (1 sec = 1 hour configurable)
- AdCP V2.3 compliant delivery webhooks
- Realistic pacing and spend simulation
- **See**: [`docs/delivery-simulation.md`](delivery-simulation.md)

#### 2. **Human-in-the-Loop (HITL) Testing**
- Sync mode: Delayed responses with configurable timeouts
- Async mode: Pending states with webhook completion
- Approval simulation with configurable success rates
- **Configuration**: Via principal's `platform_mappings.mock.hitl_config`

#### 3. **Targeting Capabilities**
Unlike real adapters with limitations, mock supports **all targeting dimensions**:
- ✅ Geographic (countries, regions, metros)
- ✅ Device types (desktop, mobile, tablet, CTV, DOOH, audio)
- ✅ Operating systems
- ✅ Browsers
- ✅ Content categories
- ✅ Keywords
- ✅ Custom key-value pairs (AEE integration)

**Note**: For testing targeting errors, mock can be configured to reject specific dimensions.

#### 4. **GAM-like Object Hierarchy**
Simulates Google Ad Manager's structure:
- Ad unit hierarchy with parent/child relationships
- Custom targeting keys and values
- Line item templates for different campaign types
- Creative library with format specifications

#### 5. **Configurable Simulation Scenarios**
Via Admin UI or product configuration:
- **Traffic simulation**: Impressions, fill rate, CTR, viewability
- **Performance simulation**: Latency, error rates
- **Test scenarios**: Normal, high demand, degraded, outage
- **Delivery simulation**: Time acceleration, webhook intervals

## Getting Started

### 1. Create a Tenant with Mock Adapter

```bash
# Inside Docker container
docker-compose exec adcp-server python -m scripts.setup.setup_tenant "Test Publisher" \
  --adapter mock \
  --subdomain test-pub

# This creates:
# - Tenant: test-pub
# - Principal: test-pub-buyer (with access token)
# - Products: Configured with mock adapter
```

### 2. Get Your Access Token

**Via Admin UI:**
1. Navigate to http://localhost:8000
2. Select your tenant
3. Go to Principals
4. Copy the access token

**Via Database:**
```bash
docker-compose exec postgres psql -U adcp_user -d adcp
SELECT access_token FROM principals WHERE tenant_id = 'test-pub';
```

### 3. Test with MCP Client

```python
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

# Configure client
headers = {"x-adcp-auth": "your_access_token"}
transport = StreamableHttpTransport(
    url="http://localhost:8000/mcp/",
    headers=headers
)

async with Client(transport=transport) as client:
    # List products
    products = await client.tools.get_products(
        brief="Display advertising inventory"
    )
    print(f"Found {len(products.products)} products")

    # Create media buy
    result = await client.tools.create_media_buy(
        promoted_offering="Test Campaign",
        product_ids=[products.products[0].product_id],
        total_budget=5000.0,
        flight_start_date="2025-10-10",
        flight_end_date="2025-10-17"
    )
    print(f"Created media buy: {result.media_buy_id}")

    # Get delivery (will return simulated data)
    delivery = await client.tools.get_media_buy_delivery(
        media_buy_ids=[result.media_buy_id]
    )
    print(f"Impressions: {delivery.media_buy_deliveries[0].totals.impressions}")
```

### 4. Test with A2A Client

```python
from python_a2a.client import Agent, AgentConfig

# Configure A2A agent
agent = Agent(
    config=AgentConfig(
        name="test-buyer",
        agent_url="http://localhost:8091"  # A2A server port
    )
)

# Authenticate
await agent.authenticate(principal_id="test-pub-buyer")

# Same operations as MCP
products = await agent.get_products(brief="Display ads")
```

## Configuration Guide

### Product-Level Configuration

Configure mock adapter behavior per product via Admin UI:

**Navigate to**: Products → Select Product → Configure

**Available Settings:**

#### Traffic Simulation
- **Daily Impressions**: 1,000 - 10,000,000+ (default: 100,000)
- **Fill Rate**: 0-100% (default: 85%)
- **CTR**: 0-10% (default: 0.5%)
- **Viewability Rate**: 0-100% (default: 70%)

#### Performance Simulation
- **API Latency**: 0-5000ms (default: 50ms)
- **Error Rate**: 0-50% (default: 0.1%)

#### Test Scenarios
- **Normal**: Standard operation
- **High Demand**: Low fill rate (30%)
- **Degraded**: High latency (500ms), moderate errors (5%)
- **Outage**: All requests fail (100% error rate)

#### Delivery Simulation
- **Enabled**: Turn on/off accelerated delivery
- **Time Acceleration**: 60s (1 min) to 86400s (1 day)
- **Update Interval**: 0.1-60 seconds (real-time)

**See**: [`docs/delivery-simulation.md`](delivery-simulation.md)

### Principal-Level HITL Configuration

Configure Human-in-the-Loop behavior via principal's `platform_mappings`:

```json
{
  "principal_id": "test-buyer",
  "platform_mappings": {
    "mock": {
      "advertiser_id": "mock_adv_123",
      "hitl_config": {
        "enabled": true,
        "mode": "sync",
        "sync_settings": {
          "delay_ms": 2000,
          "streaming_updates": true,
          "update_interval_ms": 500
        },
        "async_settings": {
          "auto_complete": true,
          "auto_complete_delay_ms": 10000,
          "webhook_url": "https://your-app.com/webhooks/hitl"
        },
        "approval_simulation": {
          "enabled": true,
          "approval_probability": 0.8,
          "rejection_reasons": [
            "Budget exceeds limits",
            "Invalid targeting"
          ]
        }
      }
    }
  }
}
```

**HITL Modes:**
- **`sync`**: Delays response by configured time (simulates slow approval)
- **`async`**: Returns pending, completes via webhook later
- **`mixed`**: Per-operation mode overrides

## Testing Patterns

### 1. Basic Smoke Test

```python
async def test_mock_adapter_smoke():
    """Verify mock adapter basic functionality."""
    async with get_test_client() as client:
        # Get products
        products = await client.tools.get_products()
        assert len(products.products) > 0

        # Create media buy
        result = await client.tools.create_media_buy(
            promoted_offering="Smoke Test",
            product_ids=[products.products[0].product_id],
            total_budget=1000.0,
            flight_start_date="2025-10-10",
            flight_end_date="2025-10-11"
        )
        assert result.media_buy_id.startswith("buy_")

        # Verify delivery data available
        delivery = await client.tools.get_media_buy_delivery(
            media_buy_ids=[result.media_buy_id]
        )
        assert len(delivery.media_buy_deliveries) == 1
```

### 2. Delivery Simulation Test

```python
async def test_accelerated_delivery():
    """Test accelerated delivery with webhooks."""
    # Setup webhook receiver
    webhooks_received = []

    async def webhook_handler(request):
        data = await request.json()
        webhooks_received.append(data)
        return web.Response(text="OK")

    # Start webhook server on port 8888
    # ... (see examples/delivery_simulation_demo.py)

    # Configure product with delivery simulation
    # (via Admin UI or database update)

    # Create media buy
    result = await client.tools.create_media_buy(...)

    # Wait for webhooks
    await asyncio.sleep(5)  # 5 seconds = 5 hours simulated

    # Verify webhooks received
    assert len(webhooks_received) > 0
    assert webhooks_received[0]["data"]["notification_type"] == "scheduled"
```

### 3. Targeting Validation Test

```python
async def test_targeting_capabilities():
    """Verify mock supports all targeting dimensions."""
    result = await client.tools.create_media_buy(
        promoted_offering="Targeting Test",
        product_ids=["prod_1"],
        total_budget=1000.0,
        flight_start_date="2025-10-10",
        flight_end_date="2025-10-11",
        targeting_overlay={
            "geo_countries": ["US", "CA"],
            "geo_regions": ["US-CA", "US-NY"],
            "device_type_any_of": ["mobile", "tablet"],
            "os_any_of": ["ios", "android"],
            "browser_any_of": ["chrome", "safari"],
            "key_value_pairs": [
                {"key": "content_category", "value": "sports"}
            ]
        }
    )
    assert result.media_buy_id is not None
```

### 4. HITL Sync Mode Test

```python
async def test_hitl_sync_mode():
    """Test synchronous HITL with delay."""
    # Principal configured with sync HITL (2 second delay)

    start = time.time()
    result = await client.tools.create_media_buy(...)
    elapsed = time.time() - start

    # Should take ~2 seconds due to HITL delay
    assert 1.8 <= elapsed <= 2.5
    assert result.media_buy_id is not None
```

### 5. Error Scenario Test

```python
async def test_outage_scenario():
    """Test mock adapter outage simulation."""
    # Configure product with "outage" test mode

    with pytest.raises(Exception) as exc:
        await client.tools.create_media_buy(...)

    # Verify appropriate error message
    assert "error" in str(exc.value).lower()
```

## Examples

### Full Working Examples

1. **`examples/delivery_simulation_demo.py`**
   - Complete delivery simulation workflow
   - Webhook setup and handling
   - Real-time progress tracking

2. **`tests/integration/test_main.py`**
   - Comprehensive integration tests
   - All AdCP operations
   - Various scenarios

3. **`simulation_full.py`** (legacy, but functional)
   - 7-phase campaign lifecycle
   - Realistic timeline progression
   - Performance optimization

## Configuration Reference

### Mock Adapter Config Schema

```json
{
  "daily_impressions": 100000,
  "fill_rate": 85,
  "ctr": 0.5,
  "viewability_rate": 70,
  "latency_ms": 50,
  "error_rate": 0.1,
  "test_mode": "normal" | "high_demand" | "degraded" | "outage",
  "price_variance": 10,
  "seasonal_factor": 1.0,
  "verbose_logging": false,
  "predictable_ids": false,
  "delivery_simulation": {
    "enabled": false,
    "time_acceleration": 3600,
    "update_interval_seconds": 1.0
  }
}
```

### Validation Rules

The mock adapter enforces realistic validation:
- **Date ranges**: Start must be before end
- **Budget limits**: Max $1,000,000 per campaign
- **Impression limits**: Max 1,000,000 per package
- **Inventory targeting**: Required for non-guaranteed campaigns

## Troubleshooting

### Issue: Mock adapter not available

**Check:**
```bash
# Verify tenant has mock adapter enabled
docker-compose exec postgres psql -U adcp_user -d adcp \
  -c "SELECT tenant_id, adapter_config FROM tenants WHERE tenant_id = 'your-tenant';"
```

**Fix:**
```sql
UPDATE tenants
SET adapter_config = '{"mock": {"enabled": true}}'::jsonb
WHERE tenant_id = 'your-tenant';
```

### Issue: Delivery webhooks not firing

**Check:**
1. Is delivery simulation **enabled** in product config?
2. Is webhook registered for principal?
3. Is webhook URL reachable?
4. Check logs: `docker-compose logs -f adcp-server | grep "delivery simulation"`

**Debug:**
```bash
# Watch for simulation logs
docker-compose logs -f adcp-server | grep "📊\|📤\|🚀"
```

### Issue: Targeting validation failing

**Remember**: Mock adapter validates targeting but supports all dimensions. If you want to test targeting failures, you need to configure the mock to reject specific dimensions (not currently exposed in UI).

### Issue: HITL not working

**Check principal configuration:**
```bash
docker-compose exec postgres psql -U adcp_user -d adcp \
  -c "SELECT platform_mappings FROM principals WHERE principal_id = 'your-principal';"
```

**Verify HITL config exists** in `platform_mappings.mock.hitl_config`.

## Performance

### Mock Adapter Benchmarks

| Operation | Latency | Notes |
|-----------|---------|-------|
| `get_products` | <10ms | Returns from memory |
| `create_media_buy` | 10-50ms | Validation + in-memory storage |
| `sync_creatives` | 10-30ms | Per creative |
| `get_media_buy_delivery` | <10ms | Calculated on-demand |
| `update_media_buy` | <10ms | In-memory update |

**With HITL Enabled:**
- Sync mode: +configured delay (default 2000ms)
- Async mode: Immediate response, webhook later

**With Delivery Simulation:**
- Minimal overhead (~1% CPU per active campaign)
- Scales to 100+ concurrent simulations

## Best Practices

### 1. Use Mock for Development, Real Adapters for Staging

```python
# Development
ADAPTER = "mock"

# Staging
ADAPTER = "google_ad_manager"

# Production
ADAPTER = "google_ad_manager"
```

### 2. Reset State Between Tests

```python
@pytest.fixture
def clean_mock_adapter():
    """Reset mock adapter state."""
    from src.adapters.mock_ad_server import MockAdServer
    MockAdServer._media_buys.clear()
    yield
```

### 3. Use Deterministic Test Data

```python
# ✅ Good - predictable
media_buy_id = f"buy_test_{test_name}"

# ❌ Bad - non-deterministic
media_buy_id = f"buy_{uuid.uuid4()}"
```

### 4. Test Both Happy and Error Paths

```python
# Happy path
result = await client.tools.create_media_buy(...)
assert result.media_buy_id

# Error path - invalid dates
with pytest.raises(Exception):
    await client.tools.create_media_buy(
        flight_start_date="2025-10-10",
        flight_end_date="2025-10-09"  # End before start
    )
```

### 5. Use Delivery Simulation for Agent Testing

Perfect for testing AI agents that respond to delivery updates:
- Set acceleration to 3600 (1 sec = 1 hour)
- Test agent reacts to under-delivery
- Test agent handles budget pacing
- Test agent responds to completion

## Limitations

### What Mock Adapter DOES NOT Do

- ❌ **Persist data** - All data is in-memory, lost on restart
- ❌ **Real API calls** - No external network requests
- ❌ **Real authentication** - Simplified security model
- ❌ **Rate limiting** - No request throttling
- ❌ **Real ad serving** - No actual ads delivered
- ❌ **Cross-principal queries** - Strict isolation

### When to Use Real Adapters

Use GAM/Kevel/Triton adapters for:
- Integration testing with real ad servers
- Staging environment validation
- Production deployments
- Testing adapter-specific features
- Performance testing with real APIs

## Related Documentation

- [Delivery Simulation Guide](delivery-simulation.md) - Accelerated delivery webhooks
- [Troubleshooting Guide](../../development/troubleshooting.md) - Common issues and solutions
- [Architecture Guide](../../development/architecture.md) - System design
- [AdCP Specification](https://adcontextprotocol.org/docs/) - Protocol documentation

## Support

For questions or issues:
- Check [Troubleshooting Guide](../../development/troubleshooting.md)
- Review test examples in `/tests/integration/`
- See [AdCP specification](https://adcontextprotocol.org/docs/)

## Quick Reference

```bash
# Create test tenant with mock adapter
docker-compose exec adcp-server python -m scripts.setup.setup_tenant "Test" --adapter mock

# Get access token
docker-compose exec postgres psql -U adcp_user -d adcp \
  -c "SELECT access_token FROM principals WHERE tenant_id = 'test';"

# Test with curl (MCP)
curl -X POST http://localhost:8000/mcp/ \
  -H "x-adcp-auth: YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"method": "tools/list", "params": {}}'

# Run delivery simulation demo
python examples/delivery_simulation_demo.py

# Run integration tests
uv run pytest tests/integration/test_main.py -v
```
