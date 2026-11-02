# Mock Ad Server Security Perimeter

## Overview

The Mock Ad Server adapter implements the same security patterns as production adapters, serving as a reference implementation and testing platform for security features.

## Principal Mapping

Mock adapter uses the same principal mapping structure:

```python
# Principal configuration example:
{
    "principal_id": "test_advertiser",
    "name": "Test Advertiser Inc",
    "platform_mappings": {
        "mock": {
            "advertiser_id": "mock_adv_123"
        }
    }
}
```

## Security Implementation

### 1. In-Memory Data Isolation

The mock adapter maintains in-memory storage with principal isolation:

```python
class MockAdServer(AdServerAdapter):
    _media_buys: Dict[str, Dict[str, Any]] = {}  # Shared class variable

    # Each instance filters by principal
    def get_media_buy(self, media_buy_id: str):
        buy = self._media_buys.get(media_buy_id)
        if buy and buy['principal_id'] != self.principal.principal_id:
            raise PermissionError("Access denied")
        return buy
```

### 2. Simulated API Security

Mock adapter simulates real API security:

```python
self.log(f"Would call: MockAdServer.createCampaign()")
self.log(f"  API Request: {{")
self.log(f"    'advertiser_id': '{self.adapter_principal_id}',")
# Shows security context in logs
```

### 3. Test Data Isolation

Test scenarios maintain isolation:
- Each test principal has separate data
- No cross-contamination between test runs
- Supports security testing scenarios

## Testing Security Features

### Permission Testing

The mock adapter supports testing:
- Cross-principal access attempts
- Invalid principal mappings
- Missing advertiser IDs

```python
# Test unauthorized access
def test_cross_principal_access():
    adapter1 = MockAdServer(config, principal1)
    adapter2 = MockAdServer(config, principal2)

    # Create with principal1
    buy_id = adapter1.create_media_buy(...)

    # Attempt access with principal2
    with pytest.raises(PermissionError):
        adapter2.update_media_buy(buy_id, ...)
```

### Security Validation

Mock adapter validates:
- Principal has advertiser mapping
- Media buy ownership before updates
- Creative associations respect ownership

## Configuration

```json
{
    "adapter": "mock_ad_server"
}
```

The mock adapter:
- Requires no external credentials
- Supports all security features
- Provides deterministic testing

## Best Practices for Testing

1. **Test Isolation**:
   - Reset adapter state between tests
   - Use unique principal IDs
   - Verify security boundaries

2. **Security Scenarios**:
   - Test valid access patterns
   - Test invalid access attempts
   - Verify error messages don't leak data

3. **Audit Testing**:
   - Verify all operations are logged
   - Check principal context in logs
   - Test log security (no secrets)

## Supported Security Features

The mock adapter implements:
- Principal-based isolation
- Media buy ownership tracking
- Creative access control
- Targeting validation
- Delivery data isolation

## Limitations

As a test adapter:
- No persistent storage
- No real authentication
- Simplified permission model
- No rate limiting

## Using for Security Testing

Example security test:

```python
def test_principal_isolation():
    # Setup two principals
    principal_a = Principal(
        principal_id="company_a",
        name="Company A",
        platform_mappings={"mock": {"advertiser_id": "mock_123"}}
    )
    principal_b = Principal(
        principal_id="company_b",
        name="Company B",
        platform_mappings={"mock": {"advertiser_id": "mock_456"}}
    )

    # Create adapters
    adapter_a = MockAdServer({}, principal_a)
    adapter_b = MockAdServer({}, principal_b)

    # Create media buy with Company A
    response_a = adapter_a.create_media_buy(request, packages, start, end)

    # Try to access with Company B
    with pytest.raises(PermissionError):
        adapter_b.get_media_buy_delivery(
            response_a.media_buy_id,
            date_range,
            datetime.now()
        )
```

## Audit Trail

Mock adapter provides complete audit trail:
```
[bold]MockAdServer.create_media_buy[/bold] for principal 'Test Advertiser Inc' (adapter ID: mock_adv_123)
Creating media buy with ID: buy_PO-2024-TEST
Budget: $10,000.00
```
