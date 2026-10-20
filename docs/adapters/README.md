# Adapters

Adapters connect the Prebid Sales Agent to ad servers. Choose the adapter that matches your ad server platform.

## Available Adapters

### [Google Ad Manager (GAM)](gam/)

Connect to Google Ad Manager to create and manage line items programmatically.

- Service account authentication
- Line item creation and management
- Creative trafficking
- Reporting integration
- Pricing models: CPM, vCPM, CPC, flat rate (sponsorship line items)

[Get started with GAM](gam/)

### Broadstreet

Connect to [Broadstreet](https://broadstreetads.com), a simple ad server focused on local and B2B publishers.

- API key authentication (`network_id`, `api_key`)
- Zone-based inventory with inventory sync and inventory profiles
- Geo targeting; no custom key-value targeting
- Pricing models: CPM, flat rate
- Synchronous operation (no webhooks)
- Zone discovery in the admin UI

Source: [`src/adapters/broadstreet/`](../../src/adapters/broadstreet/)

### Kevel

Connect to [Kevel](https://www.kevel.com), an API-first ad server for custom ad platforms.

- API key authentication (`network_id`, `api_key`)
- Default channels: social, retail media
- Optional audience targeting through Kevel UserDB (`userdb_enabled`)
- Optional frequency capping (`frequency_capping_enabled`)
- Rejects targeting the platform cannot express with a capability error

Source: [`src/adapters/kevel.py`](../../src/adapters/kevel.py)

### Triton Digital

Connect to the [Triton Digital](https://www.tritondigital.com) TAP API for audio advertising.

- Token authentication (`auth_token`; optional `base_url`)
- Default channels: streaming audio, podcast
- Audio-only: non-audio media types are rejected
- IAB content categories are not supported; use custom genres

Source: [`src/adapters/triton_digital.py`](../../src/adapters/triton_digital.py)

### [Mock Adapter](mock/)

A simulated ad server for testing and development.

- No external dependencies
- Simulates all AdCP operations
- Configurable delivery simulation
- Supports every pricing model, for exercising buyer flows
- Ideal for evaluation and testing

[Get started with Mock](mock/)

## Choosing an Adapter

| Adapter | Registry key | Default channels | Use Case |
|---------|--------------|------------------|----------|
| **GAM** | `google_ad_manager` (or `gam`) | display, olv, social | Production deployments with Google Ad Manager |
| **Broadstreet** | `broadstreet` | display | Local and B2B publishers using Broadstreet |
| **Kevel** | `kevel` | social, retail_media | Custom or retail ad platforms built on Kevel |
| **Triton Digital** | `triton` (or `triton_digital`) | streaming_audio, podcast | Audio and podcast publishers using Triton |
| **Mock** | `mock` | display, olv, streaming_audio, social | Testing, demos, development |

Every adapter needs the advertiser's ID on that platform, stored on the principal
(`principal.get_adapter_id(<adapter name>)`). The registry key is the adapter type
stored in tenant configuration; see `ADAPTER_REGISTRY` in
[`src/adapters/__init__.py`](../../src/adapters/__init__.py).

Each adapter documents its security boundary next to its code
(for example [`kevel_security.md`](../../src/adapters/kevel_security.md));
[`SECURITY_OVERVIEW.md`](../../src/adapters/SECURITY_OVERVIEW.md) describes the shared model.

## Multi-Tenant Considerations

In multi-tenant mode, each tenant can have their own adapter configuration:

- Different GAM network codes per tenant
- A different adapter per tenant (for example GAM for one, Broadstreet for another)
- Per-tenant service accounts and API keys

See [Multi-Tenant Setup](../deployment/multi-tenant.md) for configuration details.

## Building Your Own Adapter

To connect an ad server that has no adapter yet, implement the
`AdServerAdapter` base class and register it. See
[Creating an ad server adapter](creating-an-adapter.md) for the base-class
contract, registration, and targeting translation. The Broadstreet adapter is a
compact, modular example to start from.

## Related Documentation

- [Adapter Architecture](../development/architecture.md#adapter-pattern) - How adapters work internally
- [Creating an ad server adapter](creating-an-adapter.md) - The base-class contract and registration
- [Security](../security.md) - Adapter security boundaries
