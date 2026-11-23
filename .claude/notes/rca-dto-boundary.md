# RCA: the DTO boundary — how many shapes a payload takes between wire and DB

Repo `.`, branch `feature/spec-gaps-1210`.
SDK: `adcp==6.6.0` at `.venv/lib/python3.12/site-packages/adcp`
(`pyproject.toml:10`). Everything below is file:line + quoted source; anything I could not
check is marked **unverified**.

**Headline answer to the question asked.** A buyer payload takes **6 to 9 distinct shapes**
between the HTTP body and the database and back, depending on tool and transport. The
minimum (get_adcp_capabilities on REST) is 4. The maximum (create_media_buy on A2A) is 9.
The SDK model is present for **exactly one hop of the inbound leg and one hop of the
outbound leg**; every other hop is a dict, a kwargs bag, or a locally-declared class.

---

## Section 1 — SDK model inventory

### 1.1 Counts

`adcp.types` exports **222 names** ending in `Request` or `Response`
(measured: `uv run python -c "import adcp.types as T; [n for n in dir(T) if n.endswith(('Request','Response'))]"`).

| property | count |
|---|---|
| names ending Request/Response | 222 |
| of those, actual Pydantic models | 198 |
| of those, **union aliases / not a class** | 24 |
| `model_config extra="allow"` | 186 |
| `model_config extra="forbid"` | 8 |
| `model_config extra="ignore"` | 4 |
| carry an `ext` field | 167 |
| derive from `AdcpVersionEnvelope` | 180 |
| derive from `AdCPBaseModel` directly | 15 |

The 8 `extra="forbid"` models: `BusinessEntityResponse`, `ContextMatchRequest`,
`IdentityMatchRequest`, `PaginationRequest`, `PaginationResponse`,
`VerifyBrandClaimSignedResponse`, `VerifyBrandClaimsSignedResponse`,
`WebhookChallengeResponse`.

The 4 `extra="ignore"`: `CalibrateContentRequest`, `GetContentStandardsRequest`,
`GetMediaBuyArtifactsRequest`, `ValidateContentDeliveryRequest`.

The 24 "not a class" names are **response unions**, and they matter: the SDK models a
tool's response as `Success | Error | Submitted`, not one class. They include exactly the
names a naive `from adcp.types import CreateMediaBuyResponse` would grab:

```
AcquireRightsResponse, ActivateSignalResponse, BuildCreativeResponse,
CalibrateContentResponse, CreateMediaBuyResponse, GetAccountFinancialsResponse,
GetBrandIdentityResponse, GetContentStandardsResponse, GetCreativeFeaturesResponse,
GetMediaBuyArtifactsResponse, GetRightsResponse, LogEventResponse,
PreviewCreativeResponse, ProvidePerformanceFeedbackResponse, SyncAccountsResponse,
SyncAudiencesResponse, SyncCatalogsResponse, SyncCreativesResponse,
SyncEventSourcesResponse, UpdateMediaBuyResponse, UpdateRightsResponse,
ValidateContentDeliveryResponse, VerifyBrandClaimResponse, VerifyBrandClaimsResponse
```

### 1.2 The SDK's tool surface

`adcp.server.mcp_tools.ADCP_TOOL_DEFINITIONS` declares **63 tools**. `src/core/main.py:331`
imports it and `src/core/main.py:351-360` uses it for descriptions/annotations only:

```python
def _register_tool(fn: Any) -> None:
    """Register an MCP tool with SDK description and annotations when available."""
    tool_name = fn.__name__
    sdk_def = _sdk_tool_defs.get(tool_name)
    kwargs: dict[str, Any] = {}
    if sdk_def:
        kwargs["description"] = sdk_def["description"]
        if sdk_def.get("annotations"):
            kwargs["annotations"] = ToolAnnotations(**sdk_def["annotations"])
    mcp.tool(**kwargs)(with_error_logging(fn))
```

**The SDK definition supplies prose and annotations only. The `inputSchema` FastMCP
publishes is derived from `fn`'s Python signature, not from the SDK.** This is the single
most consequential fact in this document — see §5.

Two registered tools are **not in the SDK's 63**: `list_authorized_properties`,
`update_performance_index`. Three more (`list_tasks`, `get_task`, `complete_task`) map
loosely onto the SDK's `list_tasks` / `get_task_status`.

### 1.3 Serialization / field-selection helpers the SDK supplies

Yes — and none of them are used.

`adcp/types/base.py:269-287` — `AdCPBaseModel.model_dump` sets `exclude_none=True` and
`serialize_as_any=True` by default. This IS inherited (our `SalesAgentBaseModel` extends
it, `src/core/schemas/_base.py:292-307`).

`adcp/types/projections.py` — write-only field stripping:

```python
class BusinessEntityResponse(BusinessEntity):        # projections.py:139
    bank: Any = Field(default=None, exclude=True)
    @field_validator("bank", mode="before")
    @classmethod
    def _reject_bank(cls, v: Any) -> None:
        if v is not None:
            raise ValueError("BusinessEntityResponse must not carry bank details ...")

class AccountResponse(Account):                      # projections.py:168
    billing_entity: BusinessEntityResponse | None = None

def to_account_response(account: Account) -> AccountResponse:   # projections.py:182
```

Grep over `src/`: `to_account_response` → **NOT USED**. `AccountResponse` → **NOT USED**.
`BusinessEntityResponse` → **NOT USED**. `adcp.types.projections` → **NOT IMPORTED
ANYWHERE**. We hand-rolled the same thing at `src/core/tools/accounts.py:399-417`
(see §4.7).

`adcp._idempotency` / `adcp.server.idempotency` also ship; use is **unverified** (out of
scope for this census).

---

## Section 2 — the per-tool shape chain

Legend for the "shape" column:
`SDK` = an `adcp.types` model or a direct subclass · `LOCAL` = a class declared here that
is NOT an SDK subclass · `dict` = plain dict · `kwargs` = loose function parameters ·
`proto` = protobuf `Struct`/`Value`.

Transports: **MCP** = `src/core/main.py` + `@mcp.tool` wrapper in `src/core/tools/*`;
**A2A** = `src/a2a_server/adcp_a2a_server.py`; **REST** = `src/routes/api_v1.py`;
**impl** = `_*_impl` called directly (tests / internal).

### 2.0 The two hops every transport shares

**MCP inbound, all tools.** FastMCP parses the JSON `arguments` object into the wrapper's
declared Python parameters. Before that, `src/core/mcp_compat_middleware.py:67-77` runs:

```python
if is_production():
    known_params = await self._get_known_params(context, tool_name)
    if known_params is not None:
        normalized, stripped = strip_unknown_params(normalized, known_params)
        if stripped:
            modified = True
            logger.warning("Stripped unknown fields from %s: %s", tool_name, ", ".join(stripped))
```

`known_params` comes from the FastMCP tool schema, i.e. from the wrapper signature. **In
production, any wire field the wrapper does not name is deleted with a log line and no
error.** `src/core/request_compat.py:175-193`.

**All outbound, MCP.** `src/core/tools/_mcp.py:9-30`:

```python
def mcp_result(response: AdCPBaseModel, content: str | None = None) -> ToolResult:
    return ToolResult(
        content=content if content is not None else str(response),
        structured_content=response.model_dump(mode="json"),
    )
```

15 call sites (`grep "return mcp_result("` over `src/core/tools/`).

**All outbound, A2A.** `src/a2a_server/adcp_a2a_server.py:1475-1508`:

```python
def _stamp_a2a_protocol_fields(response: AdCPBaseModel) -> dict[str, Any]:
    response_data = response.model_dump(mode="json")
    response_data["message"] = str(response)
    if "errors" in response_data:
        response_data["success"] = not bool(response_data["errors"])
    else:
        response_data.setdefault("success", True)
    return response_data
```

then `_dict_to_value` (`:164-170`) → protobuf `Struct` → `json_format.MessageToJson`
(`:1053`) → `restore_a2a_integer_types` (`src/app.py:361`). **Every integer becomes a
float in the protobuf `Value` and only 18 named fields are restored** — §4.1.

**All outbound, REST.** `response.model_dump(mode="json")` inline in the route — 13 sites
in `src/routes/api_v1.py`. Only `get_products` additionally calls `apply_version_compat`
(`src/routes/api_v1.py:267`).

---

### 2.1 `get_products`

SDK: `GetProductsRequest` (18 fields) / `GetProductsResponse`.
Our subclasses: `src/core/schemas/product.py:253` (adds `product_selectors`), `:284`.
`_impl`: `src/core/tools/products.py:157` `(req: GetProductsRequestGenerated, identity)`.

| dir | transport | chain (file:line) | non-SDK hops |
|---|---|---|---|
| in | MCP | body → **kwargs** (5 names, `products.py:825-840`) → `create_get_products_request` (`schema_helpers.py:221`) → **SDK** `GetProductsRequest` → `_impl` | 1 |
| in | A2A | `parameters` **dict** → 5 hand-picked `.get()` (`a2a:1679-1697`) → `get_products_raw` **kwargs** (`products.py:883`) → `create_get_products_request` → **SDK** | 2 |
| in | REST | body → **LOCAL** `GetProductsBody` (`api_v1.py:68-78`, 6 fields) → **kwargs** → `create_get_products_request` → **SDK** | 2 |
| in | impl | caller builds **SDK** directly | 0 |
| out | MCP | **SDK** `GetProductsResponse` → `mcp_result` → **dict** → wire | 1 |
| out | A2A | **SDK** → `_stamp_a2a_protocol_fields` **dict**+2 injected keys → `apply_version_compat` (`a2a:1704-1712`) → **proto** → JSON → int-restore → wire | 4 |
| out | REST | **SDK** → `model_dump` → `apply_version_compat` → **dict** → wire (`api_v1.py:265-267`) | 2 |

Total distinct shapes, A2A round trip: **9**.

**MCP/A2A/REST all accept 5 of the SDK's 18 request fields.** Missing on all three:
`account, buying_mode, catalog, ext, fields, if_pricing_version, if_wholesale_feed_version,
pagination, preferred_delivery_types, push_notification_config, refine, required_policies,
time_budget`. The transport-parity guard is blind to this — §4.3.

A2A's handler is the "shadow-ish" one: it names 5 keys explicitly rather than consuming the
bag, so a 6th field added to `GetProductsBody` would diverge silently on A2A alone:

```python
# src/a2a_server/adcp_a2a_server.py:1686-1697
brief = parameters.get("brief", "")
brand = parameters.get("brand")
filters = parameters.get("filters")
response = await core_get_products_tool(
    brief=brief, brand=brand, filters=filters,
    property_list=parameters.get("property_list"),
    context=parameters.get("context"),
    identity=identity,
)
```

### 2.2 `create_media_buy`

SDK: `CreateMediaBuyRequest` (20 fields). Our subclass `src/core/schemas/_base.py:1740`
(field-identical to SDK — verified: no adds, no drops).
`_impl`: `src/core/tools/media_buy_create.py:2008` → returns **LOCAL** `CreateMediaBuyResult`.

| dir | transport | chain | non-SDK hops |
|---|---|---|---|
| in | MCP | body → **kwargs** (12 names, `media_buy_create.py:4390-4470`) → `_build_create_media_buy_request` (`:4339`) → **SDK-sub** `CreateMediaBuyRequest` → `_impl`; `push_notification_config` separately downgraded model→**dict** (`:4498`) | 2 |
| in | A2A | `parameters` **dict** → pnc protobuf `MessageToDict` + `scheme`→`schemes` rewrite (`a2a:1570-1579`) → `normalize_request_params` **dict** (`a2a:1583-1584`) → `create_media_buy_raw` **kwargs** → `_build_create_media_buy_request` → **SDK-sub** | 3 |
| in | REST | body → **LOCAL** `CreateMediaBuyBody` (`api_v1.py:80-99`) → 5 `to_*` coercions (`api_v1.py:363-368`) → **kwargs** → builder → **SDK-sub** | 2 |
| DB | all | **SDK-sub** → `req.model_dump(mode="json")` → **dict** with injected `package_id`s → `MediaBuy.raw_request` JSONType (`repositories/media_buy.py:374-397`) | 1 |
| DB→ | replay | `raw_request` **dict** → `CreateMediaBuyRequest(**raw_request_data)` (`media_buy_create.py:792-803`) | 1 |
| out | MCP | **LOCAL** `CreateMediaBuyResult` → `_serialize` flattens+overwrites `status` (`_base.py:541-553`) → `mcp_result` → **dict** | 2 |
| out | A2A | same → `_stamp_a2a_protocol_fields` → **proto** → JSON → int-restore | 4 |
| out | REST | same → `model_dump(mode="json")` (`api_v1.py:392`) | 2 |

Total distinct shapes, A2A round trip including DB: **9**.

The outbound envelope is hand-composed, not any SDK response class:

```python
# src/core/schemas/_base.py:541-553
@model_serializer(mode="wrap")
def _serialize(self, serializer, info):
    result = self.response.model_dump(mode=info.mode, context=info.context)
    result["status"] = self.status
    result.pop("replayed", None)
    if self.replayed:
        result["replayed"] = True
    return result
```

Missing on MCP **and** REST vs the SDK request: `advertiser_industry,
agency_estimate_number, artifact_webhook, invoice_recipient, io_acceptance, plan_id,
proposal_id, total_budget`.

### 2.3 `update_media_buy`

SDK: `UpdateMediaBuyRequest` (16 fields). Our subclass `src/core/schemas/_base.py:2034`
adds `budget`, `today` (verified: `set(local)-set(sdk) == {'budget','today'}`).
`_impl`: `src/core/tools/media_buy_update.py:346` → **LOCAL** `UpdateMediaBuyResult`.

| dir | transport | chain | non-SDK hops |
|---|---|---|---|
| in | MCP | body → **kwargs** (18 names) → `_build_update_request` (`:1422`) → hand-built **dict** `request_params` (11 `if x is not None` branches, `:1470-1491`) → **SDK-sub** | 2 |
| in | A2A | `parameters` **dict** → `updates.packages`→`packages` rewrite (`a2a:2107-2111`) → **SDK-sub** `UpdateMediaBuyRequest` built from 5 keys (`a2a:2124-2131`) → **then discarded**: 8 of the 9 forwarded values are re-read from the raw dict (`a2a:2134-2144`) → `update_media_buy_raw` **kwargs** → `_build_update_request` → **SDK-sub** again | 4 |
| in | REST | body → **LOCAL** `UpdateMediaBuyBody` (`api_v1.py:101-121`) → 3 `to_*` → **kwargs** → builder dict → **SDK-sub** | 3 |
| out | ×3 | **LOCAL** `UpdateMediaBuyResult` → `TaskResultEnvelope._serialize` (`_base.py:516-521`) → per-transport as §2.0 | 2–4 |

The A2A double-validation is worth quoting in full, because the typed model it builds is
thrown away:

```python
# src/a2a_server/adcp_a2a_server.py:2124-2144
with adcp_validation_boundary():
    req = UpdateMediaBuyRequest(
        media_buy_id=params.get("media_buy_id"), paused=params.get("paused"),
        start_time=params.get("start_time"), end_time=params.get("end_time"),
        context=params.get("context"),
    )
response = core_update_media_buy_tool(
    media_buy_id=req.media_buy_id or "", paused=req.paused,
    start_time=params.get("start_time"),     # <-- back to the raw dict
    end_time=params.get("end_time"),
    budget=params.get("budget"),
    packages=params.get("packages"),
    push_notification_config=params.get("push_notification_config"),
    context=params.get("context"),
    identity=identity,
)
```

MCP carries 8 fields the SDK request does not declare (`budget, creatives, currency,
daily_budget, flight_end_date, flight_start_date, pacing, targeting_overlay`) and misses 6
it does (`account, canceled, cancellation_reason, invoice_recipient, new_packages,
revision`). `creatives` and `targeting_overlay` are dead — accepted, then dropped before
`_build_update_request` (documented at `api_v1.py:109-112`, allowlisted at
`tests/unit/test_architecture_transport_field_parity.py:57-62`).

### 2.4 `sync_creatives` — **no request model exists on any path**

SDK: `SyncCreativesRequest` (11 fields). Our subclass: `src/core/schemas/creative.py:326`.
**Neither is ever constructed.** Verified by AST scan of all of `src/`: the only three
occurrences of the name are the import alias (`creative.py:36`), the `class` line
(`:326`), and its docstring (`:327`).

`_impl` takes 9 loose kwargs:

```python
# src/core/tools/creatives/_sync.py:40-50
def _sync_creatives_impl(
    creatives: Sequence[CreativeAsset | BaseModel | dict[str, Any]],
    assignments: dict | None = None,
    creative_ids: list[str] | None = None,
    delete_missing: bool = False,
    dry_run: bool = False,
    validation_mode: str = "strict",
    push_notification_config: PushNotificationConfig | dict | None = None,
    context: ContextObject | dict | None = None,
    identity: ResolvedIdentity | None = None,
) -> SyncCreativesResponse:
```

| dir | transport | chain | non-SDK hops |
|---|---|---|---|
| in | MCP | body → **kwargs** (`sync_wrappers.py:19-31`) → **kwargs** to `_impl` | 1 (never becomes a request model) |
| in | A2A | `parameters` **dict** → per-item `CreativeAsset(**c)` loop with a `format_id` string-upgrade (`a2a:1837-1843`) → **kwargs** | 2 |
| in | REST | body → **LOCAL** `SyncCreativesBody` (`api_v1.py:134-145`) → 3 `to_*` → **kwargs**; `creatives` stay raw dicts by design (`api_v1.py:483-485`) | 2 |
| out | ×3 | **SDK-sub** `SyncCreativesResponse` (`creative.py:477`) → §2.0 | 1–4 |

All three transports miss `ext` and `idempotency_key` from the SDK request.

### 2.5 `list_creatives`

SDK: `ListCreativesRequest` (15 fields). Our subclass `src/core/schemas/creative.py:592`.
`_impl`: `src/core/tools/creatives/listing.py:190`.

The chain has an extra shape nobody else has: **four out-of-band kwargs that the request
model has no slot for**, carried alongside `req`:

```python
# src/core/tools/creatives/listing.py:190-196
def _list_creatives_impl(
    req: "ListCreativesRequest",
    format: str | None = None,
    include_performance: bool = False,
    include_sub_assets: bool = False,
    page: int = 1,
    identity: ResolvedIdentity | None = None,
) -> ListCreativesResponse:
```

and the builder itself is a hand-assembled dict:

```python
# src/core/tools/creatives/listing.py:131-160 (abridged)
filters_dict: dict[str, Any] = {}
if status:  filters_dict["statuses"] = [status]
if tags:    filters_dict["tags"] = tags
if created_after_dt:  filters_dict["created_after"] = created_after_dt
if created_before_dt: filters_dict["created_before"] = created_before_dt
if search:  filters_dict["name_contains"] = search
...
filters_dict = _merge_structured_filters(filters, filters_dict)
structured_filters = LibraryCreativeFilters(**filters_dict) if filters_dict else None
```

with `_merge_structured_filters` (`listing.py:57-66`) doing model → dict → model:

```python
if filters:
    return {**filters.model_dump(exclude_none=True), **flat_params}
```

| dir | transport | chain | non-SDK hops |
|---|---|---|---|
| in | MCP | body → **kwargs** (20 names) → structured `sort`/`pagination` flattened back to `sort_by`/`sort_order`/`limit` (`listing.py:573-580`) → `_build_list_creatives_request` → **dict** `filters_dict` → **SDK-sub** + 4 out-of-band kwargs | 3 |
| in | A2A | `parameters` **dict** → 18 hand-picked `.get()` (`a2a:1869-1897`) → `list_creatives_raw` **kwargs** → builder **dict** → **SDK-sub** + 4 | 3 |
| in | REST | body → **LOCAL** `ListCreativesBody` (`api_v1.py:147-171`) → `coerce_creative_filters` → **kwargs** → builder **dict** → **SDK-sub** + 4 | 3 |
| out | ×3 | **SDK-sub** `ListCreativesResponse` (`creative.py:632`) → §2.0 | 1–4 |

All three transports carry 14 fields the SDK request does not declare and miss 9 it does
(`account, ext, include_items, include_pricing, include_purged, include_snapshot,
include_variables, include_webhook_activity, webhook_activity_limit`; A2A/REST also miss
`pagination` and `sort`, which are the two allowlisted divergences at
`test_architecture_transport_field_parity.py:51-56`).

### 2.6 `list_creative_formats`

SDK: `ListCreativeFormatsRequest` (18 fields). Our subclass `src/core/schemas/creative.py:547`.
**Cleanest of the buyer-facing tools.** One shared builder,
`src/core/tools/creative_formats.py:146` `build_list_creative_formats_request(...)`,
called by all three transports (`creative_formats.py:165` MCP, `a2a:2023-2038`,
and REST via `ListCreativeFormatsRequest(**body_fields)` at `api_v1.py:304`).

| dir | transport | chain | non-SDK hops |
|---|---|---|---|
| in | MCP | body → **kwargs** (14) → builder → **SDK-sub** | 1 |
| in | A2A | `parameters` **dict** → 14 hand-picked `.get()` → builder → **SDK-sub** | 2 |
| in | REST | body → **LOCAL** `ListCreativeFormatsBody` (`api_v1.py:176-202`) → `model_dump(exclude={"adcp_version"}, exclude_none=True)` **dict** → `**dict` splat → **SDK-sub** | 3 |
| out | ×3 | **SDK-sub** `ListCreativeFormatsResponse` → §2.0 | 1–4 |

Note the REST hop is model→dict→model:
```python
# src/routes/api_v1.py:302-305
body_fields = body.model_dump(exclude={"adcp_version"}, exclude_none=True)
with adcp_validation_boundary(context="list_creative_formats request"):
    req = ListCreativeFormatsRequest(**body_fields) if body_fields else None
```

All three miss `ext, pagination, property_id, publisher_domain`. The guard docstring
(`test_architecture_transport_field_parity.py:11-15`) records that this tool's A2A handler
**still dropped two of the shared builder's kwargs after the builder existed** — "A builder
does not remove the enumeration; it moves it one frame."

### 2.7 `get_media_buys` — **shadowed SDK model, dead REST leg, dead raw wrapper**

SDK **has** `GetMediaBuysRequest` (10 fields) and `GetMediaBuysResponse`.
We declare our own from scratch — §3.1.

| dir | transport | chain | non-SDK hops |
|---|---|---|---|
| in | MCP | body → **kwargs** (5) → `_build_get_media_buys_request` (`media_buy_list.py:309`) → **LOCAL** `GetMediaBuysRequest` + `include_snapshot` out-of-band | 2 |
| in | A2A | `parameters` **dict** → `params.pop("include_snapshot")` → `GetMediaBuysRequest.model_validate(params)` (`a2a:2153-2159`) → **LOCAL** | 2 |
| in | REST | **NO ROUTE.** `grep "media-buys/query" src/` → nothing. | n/a |
| in | impl | **LOCAL** | 1 |
| out | MCP/A2A | **LOCAL** `GetMediaBuysResponse` → §2.0 | 2–5 |

Two dead limbs:

1. `tests/harness/media_buy_list.py:24` declares `REST_ENDPOINT = "/api/v1/media-buys/query"`
   and `:62-71` builds a body for a class named `GetMediaBuysBody` — **neither the route nor
   the body class exists**.
2. `get_media_buys_raw` (`media_buy_list.py:363`) has **zero production callers** (only the
   two re-export lines in `src/core/tools/__init__.py:19,39`). The harness records why:
   ```python
   # tests/harness/media_buy_list.py:47-53
   """The production A2A path is ``_handle_get_media_buys_skill`` —
   ``get_media_buys_raw`` has ZERO production callers, so dispatching to it
   here gave false confidence (#1417): a boundary fix on the raw
   wrapper made 'A2A' tests green while the real skill handler still
   leaked bare ValidationErrors."""
   ```

Reading back from the DB, the response is assembled from an unvalidated JSON blob:
```python
# src/core/tools/media_buy_list.py:286
buyer_campaign_ref = (buy.raw_request or {}).get("buyer_campaign_ref")
```

### 2.8 `get_media_buy_delivery`

SDK: `GetMediaBuyDeliveryRequest` (12 fields). Our subclass `src/core/schemas/delivery.py:65`.

| dir | transport | chain | non-SDK hops |
|---|---|---|---|
| in | MCP | body → **kwargs** (9) → `_build_get_media_buy_delivery_request` (`media_buy_delivery.py:708`) → **SDK-sub** | 1 |
| in | A2A | `parameters` **dict** → `media_buy_id`→`media_buy_ids` rewrite (`a2a:2189-2191`) → `GetMediaBuyDeliveryRequest.model_validate(params)` **SDK-sub** → **then partially discarded**: 4 of 9 forwarded values re-read from `params` (`a2a:2200-2211`) → `get_media_buy_delivery_raw` **kwargs** → builder → **SDK-sub** again | 4 |
| in | REST | body → **LOCAL** `GetMediaBuyDeliveryBody` (`api_v1.py:123-132`) → **kwargs** → builder → **SDK-sub** | 2 |
| out | ×3 | **SDK-sub** `GetMediaBuyDeliveryResponse` (`delivery.py:310`) → §2.0 | 1–4 |

MCP+REST miss `ext, include_window_breakdown, time_granularity`. The A2A handler is the
only one that names `account` correctly (`req.account`, `a2a:2209`) after #1438.

### 2.9 `sync_accounts` / `list_accounts` — the reference implementation

SDK: `SyncAccountsRequest` (7), `ListAccountsRequest` (6). Our subclasses
`src/core/schemas/account.py:82`, `:67`. Shared builders at
`src/core/tools/accounts.py:1843` and `:244`. A2A and REST consume the bag wholesale:

```python
# src/a2a_server/adcp_a2a_server.py:2049-2051
with adcp_validation_boundary(context="list_accounts request"):
    request = build_list_accounts_request(**select_request_fields(ListAccountsRequest, parameters))
return core_list_accounts_tool(req=request, identity=identity)
```
```python
# src/routes/api_v1.py:531-534
with adcp_validation_boundary(context="sync_accounts request"):
    req = build_sync_accounts_request(**select_request_fields(SyncAccountsRequest, body))
response = await accounts_module.sync_accounts_raw(req=req, identity=identity)
```

**MCP and REST field sets match the SDK request exactly** (`missing: [] | extra: []` for
sync_accounts; `list_accounts` carries one tolerated extra, `idempotency_key`, documented
at `accounts.py:275-279`). This is the only tool family where that is true.

| dir | transport | chain | non-SDK hops |
|---|---|---|---|
| in | MCP | body → **kwargs** (7) → builder → **SDK-sub** | 1 |
| in | A2A | **dict** → `select_request_fields` **dict** → builder → **SDK-sub** | 2 |
| in | REST | body → **LOCAL** `SyncAccountsBody` → `select_request_fields` **dict** → builder → **SDK-sub** | 3 |
| DB→out | all | ORM `DBAccount` → `_db_account_to_schema` **hand field list, 18 names** (`accounts.py:83-108`) → **SDK-sub** `Account` | 1 |
| out | ×3 | **SDK-sub** `ListAccountsResponse`/`SyncAccountsResponse` → §2.0 | 1–4 |

### 2.10 `get_adcp_capabilities` — the only clean chain

SDK `GetAdcpCapabilitiesRequest` / `GetAdcpCapabilitiesResponse` used **directly**, no
subclass (`src/core/tools/capabilities.py:15`). One shared builder,
`build_get_adcp_capabilities_request` (`capabilities.py:228-249`), called by MCP
(`:652`), A2A via `get_adcp_capabilities_raw` (`a2a:1994-2000` → `capabilities.py:706`),
and REST (`api_v1.py:284-290`).

| dir | transport | chain | non-SDK hops |
|---|---|---|---|
| in | MCP | body → **kwargs** (4) → builder → **SDK** | 1 |
| in | A2A | **dict** → 4 `.get()` → raw wrapper **kwargs** → builder → **SDK** | 2 |
| in | REST | body → **LOCAL** `GetCapabilitiesBody` (`api_v1.py:238-242`) → **kwargs** → builder → **SDK** | 2 |
| out | MCP | **SDK** → `mcp_result(response, content=summary)` (`:680`) | 1 |
| out | REST | **SDK** → `model_dump(mode="json")` (`api_v1.py:276`, `:292`) | 1 |

Only `ext` is missing from the request surface. REST round trip: **4 shapes** — the floor.

**Detector blind spot:** the parity guard maps `get_adcp_capabilities` →
`GetAdcpCapabilitiesBody` (`test_architecture_transport_field_parity.py:160-161`), but the
class is named `GetCapabilitiesBody` (`api_v1.py:238`). The REST surface for this tool is
**silently excluded from the parity comparison**.

### 2.11 `list_authorized_properties` — no SDK model exists

Not one of the SDK's 63 tools; `ListAuthorizedPropertiesRequest` is **not** in
`adcp.types`. Local-only, `src/core/schemas/_base.py:2629` / `:2647`. Comment at
`a2a:2079-2080`: *"Note: ListAuthorizedPropertiesRequest was removed from adcp 3.2.0, use
local schema."* Response is assembled from a dict literal:

```python
# src/core/tools/properties.py:145-152
response_data: dict[str, Any] = {"publisher_domains": publisher_domains}
if advertising_policies_text:
    response_data["advertising_policies"] = advertising_policies_text
response = ListAuthorizedPropertiesResponse(**response_data)
```

Chain: body → **LOCAL** Body (REST) / **dict** (A2A) / **kwargs** (MCP) →
`select_request_fields` or `**body_fields` **dict** → **LOCAL** request → `_impl` →
**dict** literal → **LOCAL** response → §2.0.

### 2.12 `update_performance_index` — no SDK model exists

Not in the SDK's 63 (the spec analogue is `provide_performance_feedback`). Local request
and response, `src/core/schemas/_base.py:1492` / `:1500`. Builder at
`src/core/tools/performance.py:29-46`. The A2A handler round-trips the models to dicts:

```python
# src/a2a_server/adcp_a2a_server.py:2220-2229
with adcp_validation_boundary():
    req = UpdatePerformanceIndexRequest.model_validate(parameters)
response = core_update_performance_index_tool(
    media_buy_id=req.media_buy_id,
    performance_data=[p.model_dump(mode="json") for p in req.performance_data],
    context=req.context, identity=identity,
)
```

MCP declares a `webhook_url` parameter that no builder accepts — a dead field on the wire
(allowlisted at `test_architecture_transport_field_parity.py:63-68`).

### 2.13 `list_tasks` / `get_task` / `complete_task` — no model at all, either direction

All three are registered MCP tools (`src/core/main.py:376-378`). All three return a raw
hand-built dict, take `context: Context` (a fastmcp type) directly in the same function as
`identity`, and never touch `mcp_result` or any Pydantic model:

```python
# src/core/tools/task_management.py:28-36
async def list_tasks(
    status: str | None = None, object_type: str | None = None, object_id: str | None = None,
    limit: int = 20, offset: int = 0,
    context: Context | None = None, identity: ResolvedIdentity | None = None,
) -> dict[str, Any]:
```
```python
# src/core/tools/task_management.py:82-94
formatted_task = {
    "task_id": task.step_id, "status": task.status, "type": task.step_type,
    "tool_name": task.tool_name, "owner": task.owner,
    "created_at": (task.created_at.isoformat() if hasattr(task.created_at, "isoformat") else str(task.created_at)),
    "updated_at": None, "context_id": task.context_id,
    "associated_objects": [{"type": m.object_type, "id": m.object_id, "action": m.action} for m in mappings],
}
```
```python
# src/core/tools/task_management.py:115-122
return {"tasks": formatted_tasks, "total": total, "offset": offset,
        "limit": limit, "has_more": offset + limit < total if total is not None else False}
```

The SDK ships `ListTasksRequest`, `ListTasksResponse`, `GetTaskStatusRequest`,
`GetTaskStatusResponse`. None are imported. Chain: ORM → **dict literal** → wire. **2 shapes,
zero of them typed.**

---

## Section 3 — shadow / local declarations of SDK model names

Census of `src/core/schemas/**`, by AST: **64 classes** named `*Request`/`*Response`.
**20 derive from an SDK type. 44 do not.**

### 3.1 The true shadow: `GetMediaBuysRequest` / `GetMediaBuysResponse`

`src/core/schemas/_base.py:2797-2823`:

```python
class GetMediaBuysRequest(SalesAgentBaseModel):
    """Request to retrieve media buys.

    Matches the adcp 3.6.0 GetMediaBuysRequest spec.
    Defined locally because adcp 3.6.0 is not yet required.
    """
    media_buy_ids: list[str] | None = Field(default=None, ...)
    status_filter: Any | None = Field(default=None, ...)
    account_id: str | None = Field(default=None, description="Account to filter to (legacy, prefer account)")
    account: LibraryAccountReference | None = Field(default=None, ...)
    context: ContextObject | None = Field(default=None, ...)
```

The docstring rationale is stale: the repo pins `adcp==6.6.0`, and
`adcp.types.GetMediaBuysRequest` exists with 12 fields. Concrete divergence:

| | local | SDK 6.6.0 |
|---|---|---|
| has | `account_id` (non-spec, legacy) | — |
| lacks | — | `include_snapshot`, `include_history`, `include_webhook_activity`, `webhook_activity_limit`, `pagination`, `ext`, envelope fields |

`include_snapshot` is the one that shows the cost: because the local model has no slot for
it, it travels as a **separate `_impl` parameter** across every transport:

```python
# src/core/tools/media_buy_list.py:88-92
def _get_media_buys_impl(
    req: GetMediaBuysRequest,
    identity: ResolvedIdentity | None = None,
    include_snapshot: bool = False,
) -> GetMediaBuysResponse:
```
```python
# src/a2a_server/adcp_a2a_server.py:2153-2160
params = {**parameters}
include_snapshot = params.pop("include_snapshot", False)
with adcp_validation_boundary(context="get_media_buys request"):
    req = GetMediaBuysRequest.model_validate(params)
response = _get_media_buys_impl(req, identity=identity, include_snapshot=include_snapshot)
```

`GetMediaBuysResponse` (`_base.py:2811`) likewise lacks `pagination`, `sandbox`, `ext`, and
the whole task envelope the SDK response carries. Supporting local types
`GetMediaBuysPackage` (`:2744`), `GetMediaBuysMediaBuy` (`:2775`), `Snapshot` (`:2723`),
`ApprovalStatus` (`:2715`), `SnapshotUnavailableReason` (`:2708`) are all local.

### 3.2 Local models where the SDK has NO equivalent (legitimate, but off-spec)

`ListAuthorizedPropertiesRequest` (`_base.py:2629`), `ListAuthorizedPropertiesResponse`
(`_base.py:2647`), `UpdatePerformanceIndexRequest` (`_base.py:1492`),
`UpdatePerformanceIndexResponse` (`_base.py:1500`). Verified: `hasattr(adcp.types, n)`
is `False` for all four; the corresponding tools are absent from the SDK's 63.

### 3.3 Local `*Request` models never constructed anywhere in `src/` — 21 of 34

AST scan of every `*Request` class in `src/core/schemas/**` cross-referenced against every
construction site in `src/`:

| never constructed | declared at |
|---|---|
| `AddCreativeAssetsRequest` | `creative.py:302` |
| `ApproveCreativeRequest` | `creative.py:747` |
| `AssignCreativeRequest` | `creative.py:689` |
| `AssignTaskRequest` | `_base.py:2262` |
| `CheckAXERequirementsRequest` | `_base.py:2336` |
| `CheckCreativeStatusRequest` | `creative.py:660` |
| `CheckMediaBuyStatusRequest` | `_base.py:1840` |
| `CompleteTaskRequest` | `_base.py:2269` |
| `CreateCreativeRequest` | `creative.py:668` |
| `CreateHumanTaskRequest` | `_base.py:2214` |
| `GetAllMediaBuyDeliveryRequest` | `delivery.py:383` |
| `GetCreativeDeliveryRequest` | `delivery.py:430` |
| `GetCreativesRequest` | `creative.py:720` |
| `GetPendingCreativesRequest` | `creative.py:736` |
| `GetPendingTasksRequest` | `_base.py:2244` |
| `GetTargetingCapabilitiesRequest` | `_base.py:2304` |
| `MarkTaskCompleteRequest` | `_base.py:2295` |
| `SimulationControlRequest` | `_base.py:2535` |
| **`SyncCreativesRequest`** | `creative.py:326` — the live tool's model, unused (§2.4) |
| `UpdatePackageRequest` | `_base.py:1912` |
| `VerifyTaskRequest` | `_base.py:2278` |

### 3.4 `ProtocolEnvelope` — declared, documented as the design, never used

`src/core/protocol_envelope.py:58-170` declares `ProtocolEnvelope` with a `wrap()`
classmethod. Six response-class docstrings cite it as the mechanism that adds protocol
fields (`_base.py:348`, `:593`, `:2657`, `creative.py:574`, `product.py:289`). Grep over
`src/`: the only occurrences are inside `protocol_envelope.py` itself (its own docstring
examples at `:24`, `:31`) and those docstrings. **No transport calls it.** The job it
describes is done three different ways instead — `mcp_result`,
`_stamp_a2a_protocol_fields`, `TaskResultEnvelope._serialize`.

---

## Section 4 — hand-assembled buyer-facing payload sites

### 4.1 The 18-name A2A integer list — VERIFIED

`src/a2a_server/adcp_a2a_server.py:202-222`:

```python
A2A_WIRE_INTEGER_FIELDS = frozenset(
    {
        "replay_ttl_seconds", "limit", "returned_count", "revision", "interval",
        "attribution_window_days", "total_processed", "created", "updated",
        "unchanged", "failed", "deleted", "total_assignments_processed",
        "assigned", "unassigned", "total_impressions", "active_count", "impressions",
    }
)
```

18 names. The mechanism is real and unavoidable at the protobuf layer (`:180-201` explains
`google.protobuf.Value` has no integer variant), but the *list* is hand-maintained and the
docstring says so: *"Extend this set as new integer fields are found on the A2A wire."*
Applied at `src/app.py:361` and duplicated into the test harness
(`tests/utils/a2a_helpers.py`, per `:238-241`). Any integer-typed spec field not in the
list ships as a float on A2A.

### 4.2 `_stamp_a2a_protocol_fields` — two keys invented on the wire — VERIFIED

`src/a2a_server/adcp_a2a_server.py:1498-1508` (quoted in §2.0). `message` and `success`
exist on no AdCP response schema; the docstring at `:1477-1483` calls them "A2A
transport-envelope markers ... a deliberate A2A-binding deviation (#1868 review)". So the
same logical response is a **different JSON object** on A2A than on MCP/REST.

### 4.3 `collect_divergences()` points at the transport union, not the SDK — VERIFIED

`tests/unit/test_architecture_transport_field_parity.py:164-186`:

```python
def collect_divergences() -> dict[tuple[str, str], list[str]]:
    """(tool, field) -> the transports MISSING that field, for every real divergence."""
    ...
    for tool in sorted(set(mcp) | set(a2a_named)):
        surfaces: dict[str, set[str]] = {}
        if tool in mcp: surfaces["mcp"] = mcp[tool]
        if tool in a2a_named and tool not in a2a_wholesale: surfaces["a2a"] = a2a_named[tool]
        if _body_name(tool) in rest: surfaces["rest"] = rest[_body_name(tool)] | path_params.get(tool, set())
        if len(surfaces) < 2: continue
        for field in sorted(set().union(*surfaces.values())):
            missing = sorted(name for name, fields in surfaces.items() if field not in fields)
            if missing: divergences[(tool, field)] = missing
```

`set().union(*surfaces.values())` is the union of what the three transports *already*
accept. **A field all three drop is not a divergence, so it is invisible.** Measured
consequence, comparing each transport surface against the SDK request model instead:

| tool | fields ALL transports drop |
|---|---|
| `get_products` | 13 of 18 |
| `list_creatives` | 9 of 15 |
| `create_media_buy` | 8 of 20 (MCP+REST; A2A wholesale) |
| `list_creative_formats` | 4 of 18 |
| `get_media_buys` | 5 of 10 (MCP; no REST route) |
| `get_media_buy_delivery` | 3 of 12 |
| `sync_creatives` | 2 of 11 |
| `get_adcp_capabilities` | 1 of 3 |
| `sync_accounts` / `list_accounts` | **0** |

Second, narrower blind spot: `_body_name()` (`:160-161`) is
`"".join(part.capitalize() for part in tool.split("_")) + "Body"`, which yields
`GetAdcpCapabilitiesBody`. The actual class is `GetCapabilitiesBody` (`api_v1.py:238`), so
that tool's REST surface never enters the comparison at all.

### 4.4 `select_request_fields` — the one correct primitive, used at 4 of ~30 sites

`src/core/schema_helpers.py:276-297`:

```python
def select_request_fields(model: type[BaseModel], source: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    values = source.model_dump(exclude_none=True) if isinstance(source, BaseModel) else source
    return {
        name: value
        for name, value in values.items()
        if name in model.model_fields and name not in _VERSION_ENVELOPE_FIELDS and value is not None
    }
```

Call sites (all of them): `a2a:2050`, `a2a:2063`, `a2a:2092`, `api_v1.py:508`,
`api_v1.py:532`. That is accounts ×2 and authorized-properties — 3 of the 13 buyer-facing
tools. Every other transport hop names its fields.

### 4.5 `build_X_request` builders — 4 exist, and they do not close the hole

`build_list_accounts_request` (`accounts.py:244`), `build_sync_accounts_request`
(`accounts.py:1843`), `build_get_adcp_capabilities_request` (`capabilities.py:228`),
`build_list_creative_formats_request` (`creative_formats.py:146`), plus
`_build_create_media_buy_request` (`media_buy_create.py:4339`), `_build_update_request`
(`media_buy_update.py:1422`), `_build_list_creatives_request` (`listing.py:69`),
`_build_get_media_buys_request` (`media_buy_list.py:309`),
`_build_get_media_buy_delivery_request` (`media_buy_delivery.py:708`),
`_build_update_performance_index_request` (`performance.py:29`),
`create_get_products_request` (`schema_helpers.py:221`).

Each builder is itself a hand-written keyword list. The guard's own docstring records the
failure mode (`test_architecture_transport_field_parity.py:11-15`):

> `list_creative_formats` ALREADY had a shared `build_X_request()` that every transport
> called, and its A2A handler still omitted two of the builder's kwargs. A builder does not
> remove the enumeration; it moves it one frame.

The worst of them assembles a dict from 11 branches before the model sees anything:

```python
# src/core/tools/media_buy_update.py:1470-1491
request_params: dict[str, Any] = {}
if media_buy_id is not None:            request_params["media_buy_id"] = media_buy_id
if paused is not None:                  request_params["paused"] = paused
if effective_start is not None:         request_params["start_time"] = effective_start
if effective_end is not None:           request_params["end_time"] = effective_end
if budget_obj is not None:              request_params["budget"] = budget_obj
if packages is not None:                request_params["packages"] = packages
if push_notification_config is not None: request_params["push_notification_config"] = push_notification_config
if context is not None:                 request_params["context"] = context
if reporting_webhook is not None:       request_params["reporting_webhook"] = reporting_webhook
if ext is not None:                     request_params["ext"] = ext
if idempotency_key is not None:         request_params["idempotency_key"] = idempotency_key
```

### 4.6 `ProductEnv.build_rest_body` — 4 of 6 fields; the harness cannot exercise the fix

`tests/harness/product.py:110-117`:

```python
def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
    """Convert kwargs to GetProductsBody shape for REST POST.

    GetProductsBody (src/routes/api_v1.py) accepts:
        brief, brand, filters, adcp_version
    """
    _BODY_FIELDS = ("brief", "brand", "filters", "adcp_version")
    return {k: kwargs[k] for k in _BODY_FIELDS if k in kwargs and kwargs[k] is not None}
```

`GetProductsBody` declares **six** fields (`api_v1.py:68-78`): `brief, brand, filters,
property_list, context, adcp_version`. `property_list` and `context` were added
specifically to fix a REST-only drop (comment at `api_v1.py:74-76`: *"REST passed only 3 of
its 5 kwargs, so a buyer's property_list filter and context echo were dropped on this
transport alone"*). **The harness's REST leg still cannot send either of them**, so the fix
is ungraded on REST.

Other hand-listed harness bodies: `tests/harness/delivery_poll.py:68-81` (8-name
`_BODY_FIELDS`), `tests/harness/creative_list.py:86-98` (4 named keys + `filters` for a
body class with 19 fields), `tests/harness/performance.py:66-78`,
`tests/harness/creative_sync.py:623-646` (9 `if "x" in kwargs` branches),
`tests/harness/media_buy_list.py:62-71` (body for a class and route that do not exist,
§2.7). Correct by contrast: `tests/harness/_base.py:1079-1104` (delegates to
`req.model_dump`), `tests/harness/capabilities.py:337-342` (`return kwargs`),
`tests/harness/account_sync.py:336-353` (passes everything non-None).

### 4.7 Hand-rolled write-only scrubbing where the SDK ships the projection — **not previously filed**

`src/core/tools/accounts.py:399-417`:

```python
def _scrub_business_entity(entity: BusinessEntity | Mapping[str, object] | None) -> BusinessEntity | None:
    """Strip write-only ``bank`` from an echoed ``billing_entity``. ..."""
    from adcp.types.generated_poc.core.business_entity import BusinessEntity
    if entity is None:
        return None
    data = as_json_dict(entity, exclude_none=True)
    data.pop("bank", None)
    return BusinessEntity.model_validate(data)
```

model → dict → `.pop()` → model. The SDK ships `BusinessEntityResponse`
(`adcp/types/projections.py:139-165`) which makes `bank` **structurally impossible** — the
field is `exclude=True` and a validator raises on any non-None value — plus
`to_account_response()` (`:182-198`). Neither `adcp.types.projections` nor any of its three
exports appears anywhere in `src/`. Sibling site `_scrub_notification_credentials`
(`accounts.py:372-397`) does the same dance for `authentication.credentials`; the SDK
docstring at `projections.py:17-20` explicitly lists that one as out of scope, so that half
is legitimately ours.

### 4.8 ORM → response hand field lists

`src/core/tools/accounts.py:83-108` — `_db_account_to_schema` maps 18 columns by name.
`src/core/tools/media_buy_list.py:30-45` / `:410-433` — `_MediaBuyData` dataclass, 12
fields copied one by one out of the ORM row.
`src/core/tools/properties.py:87-91`, `:145-152` — dict literal → `**` splat into the
response model.
`src/core/tools/task_management.py:82-122` — dict literal straight to the wire, no model.

### 4.9 DB round trip: model → JSON blob → `.get()`

`src/core/database/repositories/media_buy.py:374-397`:

```python
raw = req.model_dump(mode="json", by_alias=by_alias)
if package_id_map:
    packages = raw.get("packages", [])
    for idx, pkg_id in package_id_map.items():
        if idx < len(packages):
            packages[idx]["package_id"] = pkg_id
kwargs: dict[str, Any] = { ..., "raw_request": raw, ... }
```

Read back untyped in three tools: `media_buy_list.py:286`,
`media_buy_delivery.py:242-243`, `:404-405`, `:489-491`, and re-parsed into a model in
exactly one place, the approval replay path:

```python
# src/core/tools/media_buy_create.py:792-803
raw_request_data = dict(media_buy.raw_request)
...
raw_request_data.setdefault("idempotency_key", f"legacy-approval-{media_buy_id}")
request = CreateMediaBuyRequest(**raw_request_data)
```

---

## Section 5 — the causal question: where does a payload leave SDK-typed space, and why

**Answer: yes — repeatedly, and at four sites for no reason at all.** Breakdown of every
departure point found, by cause.

### (a) Internal-only field the SDK model has no slot for — **6 sites**

| site | field(s) |
|---|---|
| `src/core/tools/media_buy_list.py:88-92` | `include_snapshot` as a separate `_impl` param |
| `src/core/tools/creatives/listing.py:190-196` | `format`, `include_performance`, `include_sub_assets`, `page` (documented: *"NOT representable on ListCreativesRequest and stay as out-of-band _impl kwargs"*, `listing.py:93-96`) |
| `src/core/schemas/_base.py:565-586` | `AffectedPackage.changes_applied`, `.buyer_package_ref`, both `exclude=True` |
| `src/core/schemas/product.py:106`, `:170` | `Product.implementation_config` (+ `expires_at`), excluded in `model_dump` |
| `src/core/schemas/_base.py:2034` | local `UpdateMediaBuyRequest.today` |
| `src/core/tools/media_buy_list.py:30-45` | `_MediaBuyData` dataclass (ORM extract, not a wire shape) |

**Legitimate.** The `exclude=True` cases are the pattern CLAUDE.md prescribes. The two
out-of-band-kwarg cases are legitimate *given the current request models* but are the
symptom of (d) — `include_snapshot` IS an SDK field, we just shadow the model (§3.1).

### (b) Serialization behaviour the SDK model does not do — **5 sites**

| site | what |
|---|---|
| `src/core/tools/_mcp.py:9-30` | `structured_content` must be a plain dict or FastMCP bypasses `model_dump()` overrides — documented in full |
| `src/core/schemas/_base.py:516-521` | `TaskResultEnvelope._serialize` flattens response + overwrites `status` with the protocol TaskStatus |
| `src/core/schemas/_base.py:541-553` | `CreateMediaBuyResult._serialize` additionally owns the `replayed` marker |
| `src/core/version_compat.py:14-55` | `apply_version_compat` derives pre-3.0 pricing fields |
| `src/core/schemas/_base.py:251` + 12 subclasses | `NestedModelSerializerMixin` — Pattern #4, Pydantic will not call child `model_dump()` overrides |

**Legitimate.** Each is a genuine Pydantic/FastMCP limitation with a written rationale.

### (c) Transport requires a different envelope — **4 sites**

| site | what |
|---|---|
| `a2a:164-171` `_dict_to_value` | dict → protobuf `Struct`; unavoidable for A2A |
| `a2a:202-249` int list + `src/app.py:341-371` | reverses protobuf double-widening; **mechanism forced, field list hand-maintained** |
| `a2a:1475-1508` `_stamp_a2a_protocol_fields` | injects `message`/`success` — a deliberate binding deviation |
| `a2a:1570-1579` | protobuf `TaskPushNotificationConfig` → dict + `scheme`→`schemes` rename |

**Forced by A2A/protobuf.** But note the *list* in the second row is not forced — see §6.

### (d) Legacy predating (or diverging from) the SDK — **7 sites**

| site | what |
|---|---|
| `src/core/schemas/_base.py:2797` | `GetMediaBuysRequest` local shadow, docstring *"Defined locally because adcp 3.6.0 is not yet required"* — pin is 6.6.0 |
| `src/core/schemas/_base.py:2811` | `GetMediaBuysResponse` ditto, plus `GetMediaBuysPackage`/`MediaBuy`/`Snapshot` at `:2744`/`:2775`/`:2723` |
| `src/core/schemas/_base.py:2629`, `:2647` | `ListAuthorizedProperties*` — tool removed from the spec |
| `src/core/schemas/_base.py:1492`, `:1500` | `UpdatePerformanceIndex*` — non-spec tool |
| `src/core/tools/task_management.py:28,124,181` | three registered tools with no models in either direction |
| `src/core/tools/media_buy_update.py` MCP signature | `budget/currency/pacing/daily_budget/flight_*_date` — pre-3.x flat fields |
| `src/core/protocol_envelope.py:58` | `ProtocolEnvelope`, superseded by three ad-hoc mechanisms, never deleted |

### (e) **No reason at all — 4 sites**

1. **`src/core/tools/accounts.py:399-417` `_scrub_business_entity`.** The SDK's
   `BusinessEntityResponse` / `to_account_response()` (`adcp/types/projections.py:139,182`)
   do exactly this, structurally. Zero imports of `adcp.types.projections` in `src/`.
2. **`src/a2a_server/adcp_a2a_server.py:2124-2144` `_handle_update_media_buy_skill`.** Builds
   a validated `UpdateMediaBuyRequest`, then re-reads 8 of 9 forwarded values from the raw
   dict. The typed object is constructed and discarded.
3. **`src/a2a_server/adcp_a2a_server.py:2186-2211` `_handle_get_media_buy_delivery_skill`.**
   Same pattern: `GetMediaBuyDeliveryRequest.model_validate(params)` then
   `params.get("status_filter")`, `params.get("start_date")`, `params.get("end_date")`,
   `params.get("context")`.
4. **`src/core/schemas/creative.py:326` `SyncCreativesRequest`.** A live, buyer-facing tool
   whose request model is declared, documented, and never constructed — while the SDK ships
   one too. `_sync_creatives_impl` takes 9 loose kwargs instead.

### Totals

| category | sites |
|---|---|
| (a) internal-only, no SDK slot | 6 |
| (b) serialization the SDK doesn't do | 5 |
| (c) transport envelope | 4 |
| (d) legacy / spec divergence | 7 |
| (e) no reason at all | 4 |
| **total departure points** | **26** |

**Why the owner's thesis is false as stated.** "Use SDK DTOs for requests and responses and
nothing else could enter or leave" fails on one structural fact, not on discipline:

> `src/core/main.py:351-360` registers each tool by handing FastMCP a **Python function**.
> FastMCP derives the published `inputSchema` from that function's signature. The SDK
> request model is constructed *inside the function body*, after FastMCP has already
> decided what the wire accepts. **The request model is downstream of the wire contract on
> MCP, not upstream of it.**

That is why the parity guard's own docstring concedes (`:15-16`): *"MCP's enumeration is
irreducible (FastMCP needs a typed signature for tool introspection), so the invariant
cannot hold by construction on every transport — it has to be pinned."*

That claim is true of the *current* registration shape and false in general — see §6.

---

## Section 6 — the smallest set of type-level changes

Ordered by leverage. Each is stated as concrete signatures.

### 6.1 Make MCP take the request model as its parameter — kills the root cause

FastMCP builds `inputSchema` from the signature. Give it one parameter whose type IS the
SDK request model, and the wire contract becomes the model:

```python
# src/core/tools/products.py — replace lines 825-880 (registration stays
# `_register_tool(get_products)` in src/core/main.py:366)
async def get_products(req: GetProductsRequest, ctx: Context | ToolContext | None = None) -> ToolResult:
    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None
    return mcp_result(await _get_products_impl(req, identity))
```

FastMCP publishes the model's own JSON Schema. `strip_unknown_params`
(`mcp_compat_middleware.py:71`) then strips against the *model's* field set, not a hand
list, so its production-mode field deletion stops being a silent data-loss path. All 13
`get_products` fields currently dropped on every transport become acceptable with no
further edit.

Cost, stated honestly: this changes the published MCP tool schema from flat arguments to a
single nested object. **Whether the AdCP MCP binding requires flat top-level arguments is
unverified** — I did not read `dist/docs/3.1.1/building/implementation/*.mdx`. If it does,
the alternative is 6.2 alone, which is strictly weaker.

### 6.2 One typed entry point per tool; delete the `_raw` layer

Today each tool has three inbound funnels (`tool()`, `tool_raw()`, `_impl()`) plus a
builder. Collapse to:

```python
# the only inbound signature any transport may call
async def _get_products_impl(req: GetProductsRequest, identity: ResolvedIdentity | None) -> GetProductsResponse
```

and make every transport reach it the same way:

```python
# A2A: src/a2a_server/adcp_a2a_server.py — replace each _handle_*_skill body
req = RequestModel.model_validate(select_request_fields(RequestModel, parameters))
return await _impl(req, identity)

# REST: src/routes/api_v1.py — replace each *Body class with the model itself
@router.post("/products")
async def get_products(req: GetProductsRequest, identity: ResolvedIdentity | None = resolve_auth):
    return (await products_module._get_products_impl(req, identity)).model_dump(mode="json")
```

Deletes: 11 `*Body` classes (`api_v1.py:68-242`), all 15 `*_raw` wrappers, the four
`build_*_request` functions, and the hand-picked `.get()` enumeration in the 7 A2A handlers that enumerate (measured by AST over `_handle_*_skill`: 7 enumerate, 9 consume the bag wholesale).
Makes departure sites (e)-2, (e)-3, (e)-4 impossible by construction.

Blocker to name up front: `_list_creatives_impl` (`listing.py:190-196`) and
`_get_media_buys_impl` (`media_buy_list.py:88-92`) carry out-of-band kwargs. For
`get_media_buys` that resolves via 6.3. For `list_creatives`, `format`, `page`,
`include_performance`, `include_sub_assets` have no SDK slot — they must either move into
`ListCreativesRequest.ext` or be dropped as non-spec. That is a wire-contract decision, not
a refactor.

### 6.3 Delete the `GetMediaBuys*` shadow

Delete `src/core/schemas/_base.py:2797-2823` and re-export `adcp.types.GetMediaBuysRequest`
/ `GetMediaBuysResponse` (subclassing only if `account_id` must survive as a deprecated
alias). `include_snapshot` then becomes `req.include_snapshot` and the third `_impl`
parameter disappears from `media_buy_list.py:88`, `a2a:2153-2159`, and the MCP wrapper.
Also unblocks `include_history`, `include_webhook_activity`, `webhook_activity_limit`,
`pagination`, `ext`.

### 6.4 Point the parity guard at the SDK model

Change `collect_divergences()` (`test_architecture_transport_field_parity.py:164-186`) to
compare each transport surface against `TOOL_TO_SDK_REQUEST[tool].model_fields` instead of
against `set().union(*surfaces.values())`. That single substitution converts the guard from
"the three transports agree with each other" to "the three transports agree with the spec",
and surfaces the 45 all-transport drops tabulated in §4.3 as an explicit, shrink-only
allowlist. Also fix `_body_name()` (`:160-161`) or rename `GetCapabilitiesBody` →
`GetAdcpCapabilitiesBody` (`api_v1.py:238`).

### 6.5 Replace the 18-name integer list with a schema-driven walk

`restore_a2a_integer_types` (`a2a:226-252`) already takes the field set as a parameter.
Derive it instead of typing it: walk the response model's `model_fields` for annotations
resolving to `int`, recursively, and coerce by path. The protobuf widening is forced (c);
the enumeration is not. **The exact mechanics of resolving nested/union annotations to a
path set is unverified** — I did not prototype it.

### 6.6 Use the SDK projections

Replace `_scrub_business_entity` (`accounts.py:399-417`) with
`adcp.types.projections.to_account_response`, and type the response account items as
`AccountResponse`. Keep `_scrub_notification_credentials` (SDK says that one is out of
scope, `projections.py:17-20`).

### 6.7 Give `list_tasks` / `get_task` / `complete_task` models

`src/core/tools/task_management.py:28`, `:124`, `:181` return raw dict literals from
registered MCP tools. Bind them to `adcp.types.ListTasksRequest/Response` and
`GetTaskStatusRequest/Response`, or, if our task semantics genuinely differ, declare local
models and route them through `mcp_result`. Either way they must stop returning
`dict[str, Any]` and stop accepting `context: Context` in the same function as `identity`.

### 6.8 Delete the dead declarations

`ProtocolEnvelope` (`src/core/protocol_envelope.py:58-170`) and the 5 docstrings citing it;
the 21 never-constructed `*Request` classes listed in §3.3; `get_media_buys_raw`
(`media_buy_list.py:363`, zero production callers); `tests/harness/media_buy_list.py:24,62`
(route and body class do not exist).

### What prevents "structurally impossible", precisely

After 6.1–6.3, one gap remains that no type can close:

```python
# src/core/mcp_compat_middleware.py:67-77 — production only
if is_production():
    known_params = await self._get_known_params(context, tool_name)
    if known_params is not None:
        normalized, stripped = strip_unknown_params(normalized, known_params)
        if stripped:
            logger.warning("Stripped unknown fields from %s: %s", tool_name, ", ".join(stripped))
```

plus `deep_strip_to_schema` on TypeAdapter rejection (`:97-115`). These deliberately mutate
the buyer's payload before any model sees it, for forward compatibility. With 6.1 they
strip against the SDK model's schema rather than a hand list — which is the correct
behaviour — but they remain a point where wire content is discarded outside the type
system, by design. That is the one honest answer to "could a non-SDK shape still get in":
yes, a *smaller* shape can, on purpose, in production only.

The second irreducible one is A2A's protobuf `Struct` (`a2a:164-171`): it is untyped by
construction, so §6.5 makes the restoration correct but never makes it type-checked.
