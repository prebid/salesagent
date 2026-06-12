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
| `GET` | `/tenants/{tenant_id}/signals/adapter-capabilities` | Adapter mapping kinds, candidate browsing metadata, and targeting semantics. |
| `GET` | `/tenants/{tenant_id}/signals/candidates` | Search cached adapter objects that can become signals. |
| `POST` | `/tenants/{tenant_id}/signals:validate` | Validate a draft without persisting it. |
| `GET` | `/tenants/{tenant_id}/signals` | List persisted mappings. |
| `POST` | `/tenants/{tenant_id}/signals` | Create a mapping. |
| `GET` | `/tenants/{tenant_id}/signals/{signal_id}` | Fetch one mapping. |
| `PUT` | `/tenants/{tenant_id}/signals/{signal_id}` | Replace one mapping. |
| `DELETE` | `/tenants/{tenant_id}/signals/{signal_id}` | Delete one mapping; active references return `409` unless `confirm_referenced=true`. |

## Adapter Shapes

`signals/adapter-capabilities` is the machine-readable starting point for
generic signal authoring UIs. In addition to `mapping_kinds[]`, it returns:

- `supported_candidate_types[]` and `candidate_types[]` so clients can browse
  adapter candidates without hard-coding parent/child traversal.
- `default_candidate_type`, the preferred first browse view for the adapter.
- `targeting_semantics`, including composed-signal support, composition models,
  per-kind include/exclude modes, buyer targeting fields, and exclusivity rules.

GAM supports:

- `audience_segment`: candidate type `audience_segment`, requires
  `segment_id`.
- `custom_key_value`: candidate type `custom_targeting_value`, requires
  `key_id` and `value_id`. Search values with `parent_id={key_id}`.
- `gam_targeting_groups`: advanced manually-authored targeting groups. This
  mapping is exclusive with other selected signals in one audience list.

GAM targeting-group authoring uses the TargetingWidget/GAM materializer shape:

```json
{
  "type": "passthrough",
  "kind": "gam_targeting_groups",
  "groups": [
    {
      "criteria": [
        {
          "keyId": "key_interest",
          "values": ["val_sports", "val_news"],
          "exclude": false
        }
      ]
    }
  ]
}
```

The boolean model is `groups` OR, `criteria` within a group AND, and `values`
within one criterion OR. Set `exclude: true` to negate that criterion. The
canonical group criterion casing is camelCase: `keyId`, `values`, `exclude`.
Browse keys with `candidate_type=custom_targeting_key`; browse values with
`candidate_type=custom_targeting_value&parent_id={keyId}`. The simpler
`custom_key_value` mapping keeps its snake_case `key_id` / `value_id` payload.

SpringServe supports:

- `springserve_value_list`: candidate type `value_list`, requires `key_id` and
  `value_list_id`. Search value lists with `parent_id={key_id}`.

FreeWheel supports:

- `freewheel_viewership_profile`: candidate type `viewership_profile`, backed
  by FreeWheel standard attributes under the viewership-profile bucket.
- Manual mappings for `freewheel_audience_item` and `freewheel_custom_kv`.

### FreeWheel Targeting Profiles And Custom Signals

FreeWheel has two related but different targeting mechanisms:

- Product-level saved targeting profiles are default execution targeting. Set
  `implementation_config.freewheel.targeting_profile_id` on the wholesale
  product when every buy for that product should carry the same saved
  FreeWheel targeting profile. The adapter translates it to
  `targetingProfileId`.
- Buyer-selectable custom signals are optional targeting overlays. Author them
  through the signal mapping endpoints and buyers reference their `signal_id`
  in `targeting_overlay.audience_include`. The adapter expands those selected
  signals into `viewershipProfileIds`, `audienceItemIds`, or `customCriteria`
  and ANDs them with the product's saved targeting profile.

Do not use a signal mapping to hide product-default targeting. If the targeting
must always apply, keep it on the product. Use a signal only when the buyer or
storefront should choose whether to add that audience/custom criterion to a
buy.

Viewership profile signal:

```json
{
  "signal_id": "fw_adults_25_34",
  "name": "Adults 25-34",
  "description": "FreeWheel viewership profile selectable by buyers.",
  "value_type": "binary",
  "targeting_dimension": "audience",
  "data_provider": "publisher_1p",
  "adapter_config": {
    "type": "passthrough",
    "kind": "freewheel_viewership_profile",
    "profile_id": "4711"
  }
}
```

Custom key/value signal:

```json
{
  "signal_id": "fw_sports_context",
  "name": "Sports Context",
  "description": "FreeWheel custom criterion selectable by buyers.",
  "value_type": "binary",
  "targeting_dimension": "content",
  "data_provider": "publisher_1p",
  "adapter_config": {
    "type": "passthrough",
    "kind": "freewheel_custom_kv",
    "key": "genre",
    "value_id": "sports"
  }
}
```

Composed signal:

```json
{
  "signal_id": "fw_sports_adults",
  "name": "Sports Adults",
  "description": "Combines one viewership profile and one custom criterion.",
  "value_type": "binary",
  "targeting_dimension": "audience",
  "data_provider": "publisher_1p",
  "adapter_config": {
    "type": "composed",
    "criteria": [
      {
        "kind": "freewheel_viewership_profile",
        "profile_id": "4711"
      },
      {
        "kind": "freewheel_custom_kv",
        "key": "genre",
        "value_id": "sports"
      }
    ]
  }
}
```

FreeWheel signal mappings are include-only in buyer targeting. Omit `mode` or
set `mode="include"` on FreeWheel signal criteria; validation rejects
`mode="exclude"` because FreeWheel has no native exclusion semantic for
viewership profiles, audience items, or custom criteria. References in
`audience_exclude` are rejected during media-buy targeting validation.

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
