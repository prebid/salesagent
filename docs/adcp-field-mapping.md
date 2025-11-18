# AdCP Protocol Field Mapping Guide

This document defines the exact field mappings between database models, Pydantic schemas, and the AdCP (Advertising Context Protocol) v2.4 specification.

## Overview

The AdCP protocol defines a standard interface for AI agents to discover and purchase advertising inventory. This guide ensures our implementation correctly maps internal data structures to AdCP-compliant API responses.

## Product Fields

| Database Model (models.py) | Pydantic Schema (schemas.py) | AdCP Protocol Field | Type | Required | Notes |
|---------------------------|----------------------------|-------------------|------|----------|-------|
| `product_id` | `product_id` | `product_id` | string | Yes | Unique identifier for the product |
| `name` | `name` | `name` | string | Yes | Human-readable product name |
| `description` | `description` | `description` | string | Yes | Detailed product description |
| `formats` | `formats` | `formats` | array | Yes | Array of Format objects |
| `delivery_type` | `delivery_type` | `delivery_type` | enum | Yes | Must be "guaranteed" or "non_guaranteed" |
| `is_custom` | `is_custom` | `is_custom` | boolean | No | Default: false |
| `expires_at` | `expires_at` | `expires_at` | datetime | No | When product expires |
| `countries` | - | - | array | Internal | Not exposed in AdCP |
| `implementation_config` | `implementation_config` | - | object | Internal | NEVER exposed in AdCP responses |
| `targeting_template` | - | - | object | Internal | Not directly exposed |

### Important Notes:
- **NEVER expose `implementation_config`** - This contains proprietary ad server settings
- `delivery_type` must use AdCP values: "guaranteed" or "non_guaranteed" (note the underscore)

## Format Fields

| Database/Schema Field | AdCP Protocol Field | Type | Required | Notes |
|---------------------|-------------------|------|----------|-------|
| `format_id` | `format_id` | string | Yes | Unique format identifier |
| `name` | `name` | string | Yes | Format name |
| `type` | `type` | enum | Yes | "display", "video", "audio", or "native" |
| `description` | `description` | string | Yes | Format description |
| `width` | `width` | integer | Conditional | Required for display formats |
| `height` | `height` | integer | Conditional | Required for display formats |
| `duration` | `duration` | integer | Conditional | Required for video/audio formats |
| `assets` | `assets` | array | No | For native formats |
| `delivery_options` | `delivery_options` | object | Yes | Hosting and delivery configuration |

## Price Guidance Fields

| Database/Schema Field | AdCP Protocol Field | Type | Notes |
|---------------------|-------------------|------|-------|
| `floor` | `floor` | float | Minimum price |
| `p50` | `p50` | float | 50th percentile (median) |
| `p75` | `p75` | float | 75th percentile |
| `p90` | `p90` | float | 90th percentile |

### Legacy Field Conversions:
- If data has `min`/`max` instead of percentiles, convert:
  - `min` → `floor`
  - `(min + max) / 2` → `p50`
  - `max * 0.9` → `p90`

## Principal/Authentication Fields

| Database Model | Pydantic Schema | Usage | Notes |
|---------------|----------------|-------|-------|
| `principal_id` | `principal_id` | Internal identifier | Used for tracking |
| `name` | `name` | Advertiser name | Human-readable |
| `access_token` | - | API authentication | Used in x-adcp-auth header |
| `platform_mappings` | `platform_mappings` | Platform configs | Structure changed in v2 |

### Platform Mappings Structure:
```json
{
  "google_ad_manager": {
    "advertiser_id": "123456"
  },
  "kevel": {
    "advertiser_id": "abc-def"
  },
  "mock": {
    "id": "test"
  }
}
```

## AdCP Request/Response Structures

### get_products Request (Required Fields)
```json
{
  "brief": "string - description of what buyer is looking for",
  "promoted_offering": "string - REQUIRED - advertiser and product description"
}
```

### get_products Response
```json
{
  "products": [
    {
      "product_id": "string",
      "name": "string",
      "description": "string",
      "formats": [...],
      "delivery_type": "guaranteed|non_guaranteed"
    }
  ]
}
```

### create_media_buy Request
```json
{
  "product_ids": ["product_1"],
  "total_budget": 5000.0,
  "flight_start_date": "2025-02-01",
  "flight_end_date": "2025-02-28",
  "targeting_overlay": {
    "geo_country_any_of": ["US", "CA"],
    "signals": ["sports_enthusiasts"],
    "aee_signals": {"custom_key": "custom_value"}
  }
}
```

## Targeting Fields (AdCP v2.4)

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `geo_country_any_of` | array | Target specific countries | ["US", "CA"] |
| `geo_region_any_of` | array | Target specific regions/states | ["CA", "NY"] |
| `device_type_any_of` | array | Target device types | ["desktop", "mobile"] |
| `signals` | array | Audience/contextual signals | ["sports_enthusiasts"] |
| `aee_signals` | object | Custom AEE key-value pairs | {"key": "value"} |

### Signal Support (AdCP v2.4):
- `signals`: Array of signal IDs for audiences, contextual, or geographic targeting
- `aee_signals`: Object with custom key-value pairs (renamed from `provided_signals`)

## Common Issues and Solutions

### Issue 1: Missing Required Fields
**Problem**: Database has nullable fields that AdCP requires
**Solution**: Add defaults in product_catalog_providers/database.py:
```python
if not product_data.get("description"):
    product_data["description"] = f"Advertising product: {product_data.get('name', 'Unknown')}"
```

### Issue 2: Wrong Field Names
**Problem**: Code uses `is_guaranteed` but model has `delivery_type`
**Solution**: Use correct field names from this mapping

### Issue 3: Invalid Enum Values
**Problem**: Using "programmatic" instead of "non_guaranteed"
**Solution**: Use only AdCP-specified values

### Issue 4: Exposing Internal Fields
**Problem**: `implementation_config` appears in API responses
**Solution**: Always exclude in model_dump():
```python
def model_dump(self, **kwargs):
    kwargs["exclude"] = kwargs.get("exclude", set())
    kwargs["exclude"].add("implementation_config")
    return super().model_dump(**kwargs)
```

## Validation Checklist

Before returning any Product in an API response, verify:

- [ ] `product_id` is present and non-empty
- [ ] `name` is present and non-empty
- [ ] `description` is present and non-empty
- [ ] `formats` is an array with at least one valid format
- [ ] `delivery_type` is exactly "guaranteed" or "non_guaranteed"
- [ ] `implementation_config` is NOT in the response
- [ ] All format objects have required fields

## Testing

Run these tests to verify AdCP compliance:
```bash
# Contract tests
pytest tests/unit/test_adcp_contract.py -v

# Integration tests
pytest tests/integration/test_mcp_endpoints_comprehensive.py -v

# Full MCP protocol tests
pytest tests/integration/test_mcp_protocol.py -v
```

## References

- AdCP v2.4 Specification: Internal specification document
- MCP (Model Context Protocol): https://modelcontextprotocol.io/
- Pydantic Validation: https://docs.pydantic.dev/
