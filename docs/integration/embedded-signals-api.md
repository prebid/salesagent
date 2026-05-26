# Embedded Signal Mapping API

Embedded storefronts use the Tenant Management API to author buyer-facing
signals without knowing Sales Agent internals. The API stores mappings as
`TenantSignal` rows, exposes adapter-specific candidate discovery, and keeps
buyer-facing `get_signals` output separate from private adapter execution
configuration.

The canonical machine-readable reference is the Tenant Management OpenAPI spec:

- Static: `openapi.yaml`, `openapi.json`
- Static copy: `docs/api/tenant-management-openapi.yaml`,
  `docs/api/tenant-management-openapi.json`
- Live JSON: `GET /api/v1/tenant-management/docs/openapi.json`
- Live Swagger UI: `GET /api/v1/tenant-management/docs/swagger/`

All endpoints require `X-Tenant-Management-API-Key`.

## Flow

1. Discover adapter signal mapping support:
   `GET /tenants/{tenant_id}/signals/adapter-capabilities`
2. Search synced adapter candidates:
   `GET /tenants/{tenant_id}/signals/candidates?candidate_type=...`
3. Validate the mapping draft:
   `POST /tenants/{tenant_id}/signals:validate`
4. Create or update the mapping:
   `POST /tenants/{tenant_id}/signals`
   or `PUT /tenants/{tenant_id}/signals/{signal_id}`
5. Buyers discover the mapped signal through the protocol `get_signals` tool and
   reference `signal_id` in `targeting_overlay.audience_include` or
   `targeting_overlay.audience_exclude`.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/tenants/{tenant_id}/signals/adapter-capabilities` | Adapter mapping kinds and required `adapter_config` shape. |
| `GET` | `/tenants/{tenant_id}/signals/candidates` | Search cached adapter objects that can become signals. |
| `POST` | `/tenants/{tenant_id}/signals:validate` | Validate a draft without persisting it. |
| `GET` | `/tenants/{tenant_id}/signals` | List persisted mappings. |
| `POST` | `/tenants/{tenant_id}/signals` | Create a mapping. |
| `GET` | `/tenants/{tenant_id}/signals/{signal_id}` | Fetch one mapping. |
| `PUT` | `/tenants/{tenant_id}/signals/{signal_id}` | Replace one mapping. |
| `DELETE` | `/tenants/{tenant_id}/signals/{signal_id}` | Delete one mapping; active references return `409` unless `confirm_referenced=true`. |

## Adapter Shapes

GAM supports:

- `audience_segment`: candidate type `audience_segment`, requires
  `segment_id`.
- `custom_key_value`: candidate type `custom_targeting_value`, requires
  `key_id` and `value_id`. Search values with `parent_id={key_id}`.
- `gam_targeting_groups`: advanced manually-authored targeting groups.

SpringServe supports:

- `springserve_value_list`: candidate type `value_list`, requires `key_id` and
  `value_list_id`. Search value lists with `parent_id={key_id}`.

FreeWheel supports manual mappings for:

- `freewheel_viewership_profile`
- `freewheel_audience_item`
- `freewheel_custom_kv`

Broadstreet and Mock currently return no signal mapping kinds.

## Example

```bash
curl -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY" \
  "$BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/signals/candidates?candidate_type=audience_segment&q=Auto"
```

```json
{
  "candidates": [
    {
      "candidate_type": "audience_segment",
      "external_id": "seg_auto_intenders",
      "name": "Auto Intenders",
      "mapping_kind": "audience_segment",
      "adapter_config_template": {
        "type": "passthrough",
        "kind": "audience_segment",
        "segment_id": "seg_auto_intenders"
      },
      "default_signal": {
        "signal_id": "audience_auto_intenders",
        "name": "Auto Intenders",
        "value_type": "binary",
        "targeting_dimension": "audience",
        "adapter_config": {
          "type": "passthrough",
          "kind": "audience_segment",
          "segment_id": "seg_auto_intenders"
        }
      },
      "metadata": {
        "type": "FIRST_PARTY"
      }
    }
  ],
  "count": 1
}
```

Create the mapping:

```bash
curl -X POST \
  -H "X-Tenant-Management-API-Key: $TENANT_MANAGEMENT_API_KEY" \
  -H "Content-Type: application/json" \
  "$BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/signals" \
  -d '{
    "signal_id": "audience_auto_intenders",
    "name": "Auto Intenders",
    "description": "First-party auto audience.",
    "value_type": "binary",
    "tags": ["audience", "first_party"],
    "adapter_config": {
      "type": "passthrough",
      "kind": "audience_segment",
      "segment_id": "seg_auto_intenders"
    },
    "data_provider": "publisher_1p",
    "targeting_dimension": "audience"
  }'
```
