# AdCP Schema Cache

This directory contains cached copies of official AdCP JSON schemas from https://adcontextprotocol.org/schemas/

## Purpose

These cached schemas serve as the **source of truth** for the entire project:

1. **Offline Development** - Work without internet access
2. **Schema Validation** - Validate requests/responses in tests (see `tests/e2e/adcp_schema_validator.py`)
3. **Pydantic Generation** - Auto-generate type-safe Pydantic models (see `src/core/schemas_generated/`)
4. **CI/CD Stability** - Ensure consistent schemas across all environments

## Directory Structure

```
schemas/
├── README.md                    # This file
├── v1/                         # AdCP v1 schemas
│   ├── *.json                  # Cached schema files
│   ├── *.json.meta             # Metadata (ETag, Last-Modified)
│   └── SCHEMAS_INFO.md         # Schema registry info
└── compliance_report.json      # Last compliance check results
```

## Schema Naming Convention

Schema files use a flattened naming convention to avoid directory nesting:

```
/schemas/v1/media-buy/create-media-buy-request.json
→ _schemas_v1_media-buy_create-media-buy-request_json.json
```

This allows simpler file management while preserving the logical structure.

## Updating Schemas

### Automatic Update (Recommended)

```bash
# Download all schemas from official registry (incremental, with ETag caching)
uv run python scripts/refresh_adcp_schemas.py

# Or force clean refresh (re-download everything)
uv run python scripts/refresh_adcp_schemas.py --clean

# Regenerate Pydantic models
uv run python scripts/generate_schemas.py

# Commit changes
git add schemas/ src/core/schemas_generated/
git commit -m "Update AdCP schemas to latest from registry"
```

### Manual Update (Specific Schema)

```bash
# Download specific schema
curl -s https://adcontextprotocol.org/schemas/v1/media-buy/create-media-buy-request.json > \
  schemas/v1/_schemas_v1_media-buy_create-media-buy-request_json.json

# Regenerate Pydantic models
uv run python scripts/generate_schemas.py
```

## Schema Validation

The `tests/e2e/adcp_schema_validator.py` module uses these cached schemas to validate:

- Request payloads before sending to external systems
- Response payloads from external AdCP agents
- E2E test data for compliance

## ETag-Based Caching

Each schema file has a corresponding `.meta` file containing:

```json
{
  "etag": "W/\"abc123\"",
  "last-modified": "Mon, 01 Jan 2024 12:00:00 GMT",
  "downloaded_at": "2024-01-01T12:00:00",
  "schema_ref": "/schemas/v1/media-buy/create-media-buy-request.json"
}
```

The schema validator uses ETags for conditional GET requests:
- If schema hasn't changed on server (304 Not Modified), use cached version
- If schema updated, download and cache new version
- Falls back to cache if server unavailable

## Related Files

- **Schema Generator**: `scripts/generate_schemas.py`
- **Schema Validator**: `tests/e2e/adcp_schema_validator.py`
- **Schema Sync Checker**: `scripts/check_schema_sync.py`
- **Generated Pydantic Models**: `src/core/schemas_generated/`
- **Manual Pydantic Schemas**: `src/core/schemas.py` (hand-written)

## Pre-commit Hooks

Pre-commit hooks automatically check:

1. **Schema Sync** - Ensures cached schemas match official registry
2. **Pydantic Alignment** - Ensures hand-written schemas match official spec
3. **Adapter Compliance** - Ensures adapter schemas use correct structure

If schemas are out of sync, the commit is blocked with instructions to update.

## Version History

- **v1** - Initial AdCP specification (current)
- **v2** - Future version (when released)

## Official Sources

- **Registry**: https://adcontextprotocol.org/schemas/v1/
- **Documentation**: https://adcontextprotocol.org/docs/
- **GitHub**: https://github.com/adcontextprotocol/adcp

## FAQ

### Why cache schemas locally?

1. **Offline Development** - Work without internet
2. **Deterministic Builds** - CI doesn't depend on external services
3. **Fast Tests** - No network overhead during test runs
4. **Version Control** - Track schema changes in git

### How often should I update schemas?

- **Development**: When you need new features or notice missing fields
- **Production**: Before each major release
- **CI**: Pre-commit hooks check automatically

### What if schemas are out of sync?

Pre-commit hooks will block commits and show:

```
❌ Schema out of sync: create-media-buy-request.json
   Run: uv run python scripts/refresh_adcp_schemas.py
```

Just run the suggested command and commit the changes.

### Why both cached schemas AND generated Pydantic models?

- **Cached JSON**: Source of truth for validation (runtime)
- **Generated Pydantic**: Type-safe models for development (compile-time)

Both serve different purposes and are generated from the same official schemas.
