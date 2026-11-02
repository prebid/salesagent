# Creating an ad server adapter

An adapter translates AdCP operations into one ad server's API. This guide
covers the base-class contract, registration, targeting translation, and the
rules every adapter must follow. For what an adapter is responsible for and
how the platform selects one per tenant, see
[Adapter pattern](../development/architecture.md#adapter-pattern) in the
architecture guide.

## Subclass the base class

Every adapter subclasses `AdServerAdapter` in
[`src/adapters/base.py`](../../src/adapters/base.py) and declares its
identity and capabilities as class attributes:

- `adapter_name` — the identifier the platform uses to look up per-principal
  ad server IDs (`principal.get_adapter_id(adapter_name)`) and to tag audit
  logs.
- `capabilities` — an `AdapterCapabilities` instance describing what the
  adapter supports.
- `connection_config_class` and `product_config_class` — Pydantic schemas
  for tenant connection settings and product settings, subclassing
  `BaseConnectionConfig` and `BaseProductConfig`.
- `default_channels` — the advertising channels the adapter supports.
- `default_delivery_measurement` — the delivery measurement provider for
  products this adapter creates. AdCP requires `delivery_measurement` on
  every product.

The base constructor takes `(config, principal, creative_engine, tenant_id)`
and requires `tenant_id` — every adapter operation is tenant-scoped
(`src/adapters/base.py:339-345`). There is no `dry_run` parameter: a preview is
a unit of work that rolls back rather than an adapter mode, as
[Rules that bind every adapter](#rules-that-bind-every-adapter) restates.
Validate required configuration in your `__init__` with
`self._require_config(...)`, which raises `AdCPConfigurationError` with the
missing field attached:

```python
from src.adapters.base import AdServerAdapter

class MyPlatformAdapter(AdServerAdapter):
    adapter_name = "myplatform"

    def __init__(self, config, principal, creative_engine=None, tenant_id=None):
        super().__init__(config, principal, creative_engine, tenant_id)
        self.api_key = self._require_config(config.get("api_key"), field="api_key")
```

## Implement the abstract methods

`AdServerAdapter` declares six abstract methods. An adapter must implement all
of them:

- `create_media_buy(request, packages, start_time, end_time, package_pricing_info)`
  — creates orders or campaigns on the ad server from the selected packages.
  It takes an `AdapterCreateRequest` (the buy to place, never the buyer's
  request DTO) and returns an `AdapterCreateResult`.
- `add_creative_assets(media_buy_id, assets, today)` — uploads creative
  assets to an existing media buy, returning `list[AssetStatus]`.
- `associate_creatives(line_item_ids, platform_creative_ids)` — associates
  already-uploaded creatives with line items, used when the buyer supplies
  `creative_ids` in a create request.
- `check_media_buy_status(media_buy_id, today)` — reports the platform
  status of a media buy.
- `get_media_buy_delivery(media_buy_id, date_range, today)` — reports
  delivery data for a reporting period.
- `update_media_buy(media_buy_id, action, package_id, budget, today)` —
  applies an update action to a media buy and returns an
  `AdapterUpdateResult`.

`src/adapters/base.py` declares both carriers with `extra="forbid"`. An adapter
**raises** failures as `AdCPSalesAgentError` subclasses and never returns them.
[Building a tool § Adapters take and return a carrier, not a wire model](../development/building-tools.md#adapters-take-and-return-a-carrier-not-a-wire-model)
explains the reasoning behind the typed seam.

## Override the defaults that don't fit your platform

The base class ships conservative defaults. Override the ones that
under-report your platform:

- `get_supported_pricing_models()` — returns `{"cpm"}` by default; return
  every pricing model the platform supports.
- `get_targeting_capabilities()` — returns country-level geo only by
  default; see [Translate targeting](#translate-targeting).
- `validate_media_buy_request(...)` — pre-validates a request before any
  platform call (including dry runs). The default checks pricing-model
  compatibility; add platform-specific constraint checks here so violations
  appear early.
- `get_packages_snapshot(...)` — near-real-time delivery snapshots. The
  default raises `NotImplementedError`; override only when the platform can
  serve snapshots.
- `get_available_inventory()` — inventory discovery for AI-driven product
  configuration; returns empty inventory by default.
- `get_creative_formats()` — creative format definitions, for adapters that
  also act as creative agents.

## Register the adapter

Add the class to `ADAPTER_REGISTRY` in
[`src/adapters/__init__.py`](../../src/adapters/__init__.py). The registry
key is the adapter type stored in tenant configuration; `get_adapter()`
instantiates the class from it, and `get_adapter_schemas()` exposes the
declared config schemas and capabilities to the admin UI.

## Translate targeting

Targeting dimensions have a two-tier access model: buyer-settable **overlay**
dimensions and internal **managed-only** dimensions.
`src/services/targeting_capabilities.py` defines them, and
[Targeting](../development/architecture.md#targeting) in the architecture guide
summarizes them.

An adapter participates in two ways:

1. **Declare support.** Return a `TargetingCapabilities` instance from
   `get_targeting_capabilities()` naming the dimensions the platform can
   express. The platform uses the declaration to validate requests before
   they reach the adapter.
2. **Translate accepted dimensions.** Inside `create_media_buy` and
   `update_media_buy`, convert the accepted overlay into the platform's own
   targeting structures. The GAM implementation in
   [`src/adapters/gam/managers/targeting.py`](../../src/adapters/gam/managers/targeting.py)
   is the most complete example, including custom-targeting key and value
   resolution.

## Optional: Provide a configuration UI

An adapter can ship its own admin configuration pages:

- `get_config_ui_endpoint()` — returns the endpoint path for the adapter's
  configuration UI, or `None` when the adapter has none.
- `register_ui_routes(app)` — registers Flask routes for those pages during
  app initialization.
- `validate_product_config(config)` — validates adapter-specific product
  configuration and returns `(is_valid, error_message)`.

## Rules that bind every adapter

- **Outbound HTTP goes through the egress gateway.** Adapters never import
  `httpx` or `requests`; vendor calls go through `VendorHttpClient`
  ([`src/adapters/vendor_http.py`](../../src/adapters/vendor_http.py)),
  which wraps the gateway and proves at construction time that the adapter
  can reach its vendor. See
  [Outbound egress](../security/outbound-egress.md).
- **A preview belongs to the unit of work, not to the adapter.** An adapter
  carries no `dry_run` flag and needs no second code path: a preview is a unit
  of work constructed with `dry_run=True`, which rolls its transaction back and
  defers or suppresses the outbound effects registered inside it
  (`src/core/database/repositories/effects.py`). Write one path, the live one.
- **No database access.** Adapters call external APIs and translate
  protocols; persistence belongs to repositories. The
  [layer table](../development/engineering-standards.md#put-logic-in-its-layer)
  in the engineering standards states what each layer may and may not do.
- **Audit through the provided logger.** The base constructor initializes
  `self.audit_logger` scoped to the adapter and tenant; log operations
  through it rather than constructing your own.
- **Document the security boundary.** Each adapter carries a security note
  alongside its code (for example `src/adapters/kevel_security.md`);
  [`src/adapters/SECURITY_OVERVIEW.md`](../../src/adapters/SECURITY_OVERVIEW.md)
  describes the shared model.

## Related documentation

- [Adapter pattern](../development/architecture.md#adapter-pattern) — responsibilities and the registry
- [Adapter overview](README.md) — the shipped adapters and how to choose one
- [Engineering standards](../development/engineering-standards.md) — the standards every change is held to
