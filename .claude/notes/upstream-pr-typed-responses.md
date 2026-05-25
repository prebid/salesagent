# Upstream PR: Typed Response Builders for adcp-client-python

## Repository
`adcontextprotocol/adcp-client-python` (fork: `KonstantinMirin/adcp-client-python`)

## Branch from
`main` (post-v4.3.0)

## Problem

`adcp.server.responses` has 18 response builder functions that all return `dict[str, Any]`. The SDK already has fully-typed Pydantic response models in `adcp.types.generated_poc/` (auto-generated from the AdCP JSON Schema spec, with `Field(description=...)` on every field). The builders don't use them.

This creates a type-safety gap for downstream implementors:
- Implementors using the SDK helpers get raw dicts — no autocomplete, no validation, no IDE support
- Implementors who want typed responses must construct Pydantic models manually, bypassing the builders entirely
- The `_serialize()` helper calls `.model_dump()` on Pydantic inputs, discarding type information
- The builders duplicate field names and response shapes that already exist in the generated types

The salesagent project (prebid/salesagent) has a structural guard enforcing that `_impl` functions return Pydantic models, never dicts. The current SDK builders are incompatible with this pattern, forcing implementors to choose between typed returns and DRY helpers.

## Proposed Change

Each response builder should construct and return the corresponding Pydantic response model from `adcp.types.generated_poc/`, not a raw dict.

### Mapping: Builder → Generated Response Type

| Builder function | Current return | Proposed return type | Source |
|---|---|---|---|
| `capabilities_response()` | `dict` | `GetAdcpCapabilitiesResponse` | `generated_poc/protocol/get_adcp_capabilities_response.py` |
| `products_response()` | `dict` | `GetProductsResponse` | `generated_poc/media_buy/get_products_response.py` |
| `media_buy_response()` | `dict` | `CreateMediaBuyResponse1` | `generated_poc/media_buy/create_media_buy_response.py` |
| `media_buy_error_response()` | `dict` | `CreateMediaBuyResponse2` | same file (error variant) |
| `update_media_buy_response()` | `dict` | `UpdateMediaBuyResponse1` | `generated_poc/media_buy/update_media_buy_response.py` |
| `media_buys_response()` | `dict` | `GetMediaBuysResponse` | `generated_poc/media_buy/get_media_buys_response.py` |
| `delivery_response()` | `dict` | `GetMediaBuyDeliveryResponse` | `generated_poc/media_buy/get_media_buy_delivery_response.py` |
| `creative_formats_response()` | `dict` | `ListCreativeFormatsResponse` | `generated_poc/creative/list_creative_formats_response.py` |
| `sync_creatives_response()` | `dict` | `SyncCreativesResponse1` | `generated_poc/creative/sync_creatives_response.py` |
| `list_creatives_response()` | `dict` | `ListCreativesResponse` | `generated_poc/creative/list_creatives_response.py` |
| `preview_creative_response()` | `dict` | `PreviewCreativeResponse1` | `generated_poc/creative/preview_creative_response.py` |
| `build_creative_response()` | `dict` | `BuildCreativeResponse1` or `BuildCreativeResponse2` | `generated_poc/media_buy/build_creative_response.py` |
| `signals_response()` | `dict` | `GetSignalsResponse` | `generated_poc/signals/get_signals_response.py` |
| `activate_signal_response()` | `dict` | `ActivateSignalResponse1` | `generated_poc/signals/activate_signal_response.py` |
| `log_event_response()` | `dict` | `LogEventResponse1` | `generated_poc/media_buy/log_event_response.py` |
| `sync_catalogs_response()` | `dict` | `SyncCatalogsResponse1` | `generated_poc/media_buy/sync_catalogs_response.py` |
| `sync_accounts_response()` | `dict` | `SyncAccountsResponse1` | `generated_poc/account/sync_accounts_response.py` |
| `sync_governance_response()` | `dict` | `SyncGovernanceResponse` | `generated_poc/account/sync_governance_response.py` |

### Also: `adcp_error()` in helpers.py

`adcp_error()` currently returns `dict[str, Any]`. It should return the error variant of the appropriate response type — or at minimum, a typed `ErrorResponse` model with `errors: list[Error]`.

The `Error` model already exists at `adcp.types.generated_poc.core.error.Error`.

## Design Constraints

1. **Backward compatibility**: Existing callers pass builder results to `model_dump()` or treat them as dicts. The transition should not break them.

   Approach: Pydantic models support dict-like access via `model_dump()`. The `_register_tool()` function in `serve.py` already calls `model_dump(mode="json", exclude_none=True)` on Pydantic return values. So returning a Pydantic model instead of a dict is transparent to the MCP dispatch layer.

2. **The generated types use `extra="allow"`**: Most response models have `model_config = ConfigDict(extra="allow")`. This means constructing them with extra fields won't fail — good for forward compatibility.

3. **Union response types**: Several tools have discriminated union responses (e.g., `CreateMediaBuyResponse = CreateMediaBuyResponse1 | CreateMediaBuyResponse2 | CreateMediaBuyResponse3`). The success builder returns the success variant (`Response1`), error builders return the error variant (`Response2`). The async variant (`Response3`) is handled separately by the dispatch layer.

4. **`_serialize()` helper**: Currently calls `.model_dump()` on Pydantic inputs. Should instead preserve Pydantic models when the outer builder also returns a Pydantic model. The generated response types accept nested Pydantic models directly (e.g., `packages: list[Package]`).

5. **`valid_actions_for_status()` auto-population**: Should still work — the builder populates the `valid_actions` field on the response model when `status` is provided but `valid_actions` is not.

6. **`cancel_media_buy_response()`**: Located in `helpers.py`. Same treatment — return typed model.

## Example: Before and After

### Before (current)
```python
def media_buy_response(media_buy_id, packages, *, status=None, ...) -> dict[str, Any]:
    resp = {"media_buy_id": media_buy_id, "packages": _serialize(packages), ...}
    if status:
        resp["status"] = status
        resp["valid_actions"] = valid_actions_for_status(status)
    return resp
```

### After (proposed)
```python
from adcp.types.generated_poc.media_buy.create_media_buy_response import CreateMediaBuyResponse1

def media_buy_response(media_buy_id, packages, *, status=None, ...) -> CreateMediaBuyResponse1:
    valid_actions = valid_actions_for_status(status) if status and not explicit_valid_actions else explicit_valid_actions
    return CreateMediaBuyResponse1(
        media_buy_id=media_buy_id,
        packages=packages,  # accepts list[Package] directly
        status=status,
        valid_actions=valid_actions,
        revision=revision or 1,
        confirmed_at=confirmed_at or datetime.now(timezone.utc),
        sandbox=sandbox,
    )
```

## Scope

- `src/adcp/server/responses.py` — all 18 builder functions
- `src/adcp/server/helpers.py` — `adcp_error()`, `cancel_media_buy_response()`
- Tests for each builder verifying they return the correct Pydantic type
- Verify `_register_tool()` in `serve.py` handles Pydantic return values (it already does)
- Verify `seller_agent.py` example still works

## Not in Scope

- Changing handler method signatures (they still accept `params: dict | RequestType`)
- Changing the ADCPHandler base class
- Changing tool registration or schema generation
- Breaking existing `model_dump()` callers

## Testing Strategy

For each builder:
1. Verify return type is the expected Pydantic model (`isinstance` check)
2. Verify `.model_dump(mode="json", exclude_none=True)` produces the same dict as before
3. Verify the round-trip: builder output → `model_dump()` → `model_validate()` → equal
4. Verify `valid_actions` auto-population still works
5. Verify transport-layer serialization: `_register_tool()` auto-calls `model_dump()` on Pydantic returns (already works — no change needed)

Serialization is the transport layer's job, not the caller's. The SDK's `_register_tool()` already calls `model_dump(mode="json", exclude_none=True)` on Pydantic return values before sending them over MCP. The A2A dispatch does the same. So builders returning Pydantic models is transparent to wire callers — serialization happens automatically at the transport boundary.

For callers that currently do `response["key"]` dict access on builder output: they switch to `response.key` attribute access (typed, IDE-supported, better). This is a behavioral improvement, not a regression.

## PR Title
`feat(server): typed Pydantic response builders grounded in generated AdCP schema`

## Motivation (for PR description)
The response builders in `adcp.server.responses` hand-build dicts that mirror the shapes defined by the generated Pydantic types in `adcp.types.generated_poc/`. This is a DRY violation — the schema is defined twice (once in the generated types, once in the dict-building code), and they can drift apart. More importantly, downstream implementors who enforce typed returns (e.g., prebid/salesagent's structural guards) can't use the builders because they return untyped dicts.

This PR makes each builder construct and return the corresponding generated Pydantic response model, grounding the response shapes in the authoritative AdCP schema. The generated types already have `Field(description=...)` on every field, so consumers also get documentation for free.
