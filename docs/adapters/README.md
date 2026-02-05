# Adapters

Adapters connect the Prebid Sales Agent to ad servers. Choose the adapter that matches your ad server platform.

## Available Adapters

### [Google Ad Manager (GAM)](gam/)

Connect to Google Ad Manager to create and manage line items programmatically.

- Service account authentication
- Line item creation and management
- Creative trafficking
- Reporting integration

[Get started with GAM](gam/)

### [Mock Adapter](mock/)

A simulated ad server for testing and development.

- No external dependencies
- Simulates all AdCP operations
- Configurable delivery simulation
- Ideal for evaluation and testing

[Get started with Mock](mock/)

## Choosing an Adapter

| Adapter | Use Case |
|---------|----------|
| **GAM** | Production deployments with Google Ad Manager |
| **Mock** | Testing, demos, development |

## Multi-Tenant Considerations

In multi-tenant mode, each tenant can have their own adapter configuration:

- Different GAM network codes per tenant
- Mix of GAM and Mock adapters
- Per-tenant service accounts

See [Multi-Tenant Setup](../deployment/multi-tenant.md) for configuration details.

## Related Documentation

- [Adapter Architecture](../development/architecture.md#adapter-pattern) - How adapters work internally
- [Security](../security.md) - Adapter security boundaries
