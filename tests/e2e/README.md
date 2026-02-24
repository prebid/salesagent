# End-to-End Tests for AdCP Sales Agent

This directory contains comprehensive end-to-end tests for the AdCP (Advertising Context Protocol) Sales Agent Server. These tests validate full protocol compliance and can be used by implementors to test their own AdCP servers.

## Overview

The E2E tests exercise:
- All MCP (Model Context Protocol) tools
- A2A (Agent-to-Agent) protocol integration
- Testing hooks from [AdCP PR #34](https://github.com/adcontextprotocol/adcp/pull/34)
- Complete campaign lifecycle from discovery to completion
- Error handling and edge cases

## Test Structure

```
tests/e2e/
├── conftest.py                        # Shared fixtures and test infrastructure
├── test_adcp_full_lifecycle.py        # Main comprehensive test suite
├── test_adcp_schema_compliance.py     # Schema validation compliance tests
├── test_schema_validation_standalone.py  # Standalone schema validation tests
├── test_testing_hooks.py              # Testing hooks implementation (PR #34)
├── adcp_schema_validator.py            # AdCP schema validation system
├── schemas/                           # Versioned schema cache for offline validation
│   └── v1/                           # AdCP v1 schemas (37 files, ~160KB)
└── README.md                          # This file
```

## Running Tests

### Prerequisites

1. **Docker and Docker Compose installed**
2. **Python environment with dependencies**:
   ```bash
   uv pip install -r requirements.txt
   ```

3. **Environment variables** (.env file):
   ```bash
   GEMINI_API_KEY=your-key-here
   ADCP_SALES_PORT=8080  # Unified server port (MCP + A2A + Admin all on same port)
   ```

### Test Execution Modes

#### 1. Docker Mode (Default)
Uses existing Docker services or starts them automatically:
```bash
# Start Docker services (if not running)
docker-compose up -d

# Run all E2E tests
pytest tests/e2e/test_adcp_full_lifecycle.py

# Run with verbose output
pytest tests/e2e/test_adcp_full_lifecycle.py -v -s

# Run specific test
pytest tests/e2e/test_adcp_full_lifecycle.py::TestAdCPFullLifecycle::test_product_discovery
```

#### 2. CI Mode
Optimized for continuous integration:
```bash
pytest tests/e2e/test_adcp_full_lifecycle.py --mode=ci
```

#### 3. External Server Mode
Test against any AdCP-compliant server:
```bash
pytest tests/e2e/test_adcp_full_lifecycle.py \
  --server-url=https://your-adcp-server.com \
  --mode=external
```

#### 4. Keep Test Data
Preserve test data after completion for debugging:
```bash
pytest tests/e2e/test_adcp_full_lifecycle.py --keep-data
```

## Testing Hooks

The tests implement all AdCP testing hooks from [PR #34](https://github.com/adcontextprotocol/adcp/pull/34):

### X-Dry-Run
Execute operations without affecting production platforms:
```python
headers = {"X-Dry-Run": "true"}
```

### X-Mock-Time
Control simulated time for testing:
```python
headers = {"X-Mock-Time": "2025-09-15T14:00:00Z"}
```

### X-Jump-To-Event
Jump to specific campaign lifecycle events:
```python
headers = {"X-Jump-To-Event": "campaign-midpoint"}
```

Available events:
- `campaign-start`
- `campaign-midpoint`
- `campaign-complete`

### X-Test-Session-ID
Isolate test sessions for parallel execution:
```python
headers = {"X-Test-Session-ID": "unique-test-id"}
```

### X-Simulated-Spend
Track spending without real money:
```python
headers = {"X-Simulated-Spend": "true"}
```

## Test Coverage

### MCP Tools Tested
- ✅ `get_products` - Product discovery with brief/promoted offering
- ✅ `create_media_buy` - Campaign creation with targeting
- ✅ `check_media_buy_status` - Status verification
- ✅ `add_creative_assets` - Creative upload
- ✅ `check_creative_status` - Creative approval status
- ✅ `update_media_buy` - Campaign modifications
- ✅ `get_media_buy_delivery` - Performance metrics
- ✅ `get_all_media_buy_delivery` - Bulk delivery data
- ✅ `simulation_control` - Time progression control
- ✅ `check_axe_requirements` - AEE compliance
- ✅ `create_creative_group` - Creative organization
- ✅ `update_performance_index` - Performance optimization

### A2A Protocol Tests
- Product discovery queries
- Targeting capability queries
- Pricing information queries
- Campaign optimization suggestions

### Lifecycle Phases
1. **Discovery** - Finding suitable products
2. **Creation** - Setting up campaigns with targeting
3. **Creative Setup** - Adding and approving assets
4. **Launch** - Campaign activation
5. **Optimization** - Mid-flight adjustments
6. **Completion** - Final reporting

## Schema Validation

The test suite includes comprehensive AdCP protocol schema validation:

### Features
- **Automatic validation** of all MCP tool calls against official AdCP schemas
- **Multi-version support** (currently v1, ready for v2+)
- **Offline validation** for reliable CI execution
- **Detailed error reporting** with JSON path locations

### Usage
```python
# Automatic validation in E2E tests (enabled by default)
async with AdCPTestClient(
    mcp_url="http://localhost:8000/mcp/",
    auth_token="test-token",
    validate_schemas=True  # Default: True
) as client:
    # Both request and response automatically validated
    products = await client.call_mcp_tool("get_products", {...})
    # Console output:
    # ✓ Request schema validation passed for get-products
    # ✓ Response schema validation passed for get-products
```

### Schema Compliance Tests
```bash
# Run full protocol compliance validation
pytest tests/e2e/test_adcp_schema_compliance.py -v

# Test specific operations
pytest tests/e2e/test_schema_validation_standalone.py -v
```

### Cached Schemas
- **Location**: `tests/e2e/schemas/v1/` (37 schemas, ~160KB)
- **Purpose**: Offline validation, CI reliability, version pinning
- **Update**: Manual updates when AdCP specification changes

### Multi-Version Support
```python
# Use specific schema version
async with AdCPSchemaValidator(adcp_version="v1") as validator:
    await validator.validate_response("get-products", data)

# Future: Support multiple versions for different partners
async with AdCPSchemaValidator(adcp_version="v2") as validator:
    await validator.validate_response("get-products", data)
```

## Writing New Tests

### Basic Test Structure
```python
import pytest
from tests.e2e.conftest import AdCPTestClient

class TestNewFeature:
    @pytest.mark.asyncio
    async def test_feature(self, test_client: AdCPTestClient):
        # Your test implementation
        result = await test_client.call_mcp_tool(
            "tool_name",
            {"param": "value"}
        )
        assert "expected_field" in result
```

### Using Testing Hooks
```python
# Set mock time
test_client.set_mock_time(datetime(2025, 9, 15))

# Jump to event
test_client.jump_to_event("campaign-midpoint")

# Query with isolation
async with AdCPTestClient(
    mcp_url="...",
    a2a_url="...",
    auth_token="...",
    test_session_id="unique-id",
    dry_run=True
) as client:
    # Isolated test operations
```

## Debugging

### View Docker Logs
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f adcp-server

# Last 100 lines
docker-compose logs --tail=100 adcp-server
```

### Check Service Health
```bash
# Service status
docker-compose ps

# Health endpoints
curl http://localhost:8012/health  # MCP
curl http://localhost:8091/health  # A2A
curl http://localhost:8003/health  # Admin
```

### Database Access
```bash
# Connect to PostgreSQL
docker-compose exec postgres psql -U adcp_user -d adcp

# View test data
SELECT * FROM tenants WHERE subdomain = 'e2e-test';
SELECT * FROM media_buys ORDER BY created_at DESC LIMIT 5;
```

## Continuous Integration

### GitHub Actions Example
```yaml
name: E2E Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v2

    - name: Set up Docker
      uses: docker/setup-buildx-action@v1

    - name: Start services
      run: docker-compose up -d

    - name: Wait for services
      run: |
        timeout 60 bash -c 'until curl -f http://localhost:8012/health; do sleep 2; done'

    - name: Run E2E tests
      run: |
        pytest tests/e2e/test_adcp_full_lifecycle.py --mode=ci

    - name: Cleanup
      if: always()
      run: docker-compose down
```

## Troubleshooting

### Common Issues

#### Port Already in Use
```bash
# Check what's using the port
lsof -i :8012

# Stop conflicting service or change port in .env
ADCP_SALES_PORT=8013
```

#### Docker Services Not Starting
```bash
# Reset Docker state
docker-compose down -v
docker-compose build --no-cache
docker-compose up -d
```

#### Authentication Errors
```bash
# Create new test principal
docker-compose exec adcp-server \
  python -m scripts.setup.setup_tenant "Test Publisher" --adapter mock
```

#### Database Connection Issues
```bash
# Check PostgreSQL is running
docker-compose ps postgres

# Verify connection
docker-compose exec adcp-server \
  python -c "from src.core.database.connection import get_db_session; print('Connected')"
```

## For Implementors

These tests can validate any AdCP-compliant server:

1. **Fork this repository**
2. **Configure your server URL**:
   ```bash
   export ADCP_SERVER_URL=https://your-server.com
   ```

3. **Run compliance tests**:
   ```bash
   pytest tests/e2e/test_adcp_full_lifecycle.py \
     --server-url=$ADCP_SERVER_URL \
     --mode=external
   ```

4. **Expected Results**:
   - All tests should pass for full AdCP compliance
   - Some optional features may skip if not implemented
   - Error handling tests validate proper responses

## Support

For issues or questions:
- Review [AdCP Specification](https://github.com/adcontextprotocol/adcp)
- Check [Testing Hooks PR](https://github.com/adcontextprotocol/adcp/pull/34)
- File issues in the repository
