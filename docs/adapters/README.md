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

### [FreeWheel](freewheel/)

Connect to Comcast/FreeWheel's Publisher API for video and CTV advertising.

- OAuth2 password-grant authentication (with pre-minted bearer escape hatch)
- Campaign + Insertion Order + Placement creation against `api.freewheel.tv`
- 18-dimension product targeting (inventory, audience, content, delivery, privacy)
- Local cache of the full FreeWheel inventory taxonomy (2,500+ entities synced)
- CPM and FLAT_RATE pricing

[Get started with FreeWheel](freewheel/)

### [SpringServe (Magnite)](springserve/)

Connect to Magnite's SpringServe ad server for direct-sold CTV, online video, and audio inventory.

- Email + password authentication (with pre-minted token escape hatch)
- Campaign + Demand Tag creation against `console.springserve.com`
- Audio + video on the same demand-tag API surface (streaming audio + podcast supported)
- CPM and FLAT_RATE pricing
- Stage 1 ships skeleton + auth + dry-run; live writes land in subsequent stages

[Get started with SpringServe](springserve/)

## Adding a New Adapter

Building support for a new ad server? See the [adapter playbook](adding-a-new-adapter.md) —
a phase-by-phase checklist of every file you need to touch (registry, typed
API config, admin UI, migrations, repository, tests, docs) plus common
gotchas. FreeWheel is the reference implementation.

## Choosing an Adapter

| Adapter | Use Case |
|---------|----------|
| **GAM** | Production deployments with Google Ad Manager |
| **FreeWheel** | Video + CTV inventory via Comcast/FreeWheel Publisher API |
| **SpringServe** | Direct-sold CTV / OLV / audio inventory via Magnite SpringServe |
| **Mock** | Testing, demos, development |

> Triton Digital is currently parked while their APIs aren't production-ready.
> Source remains under `src/adapters/triton/`; restoring is a one-commit revert.

## Creative Formats

All adapters should advertise canonical AdCP creative formats to buyers and map
ad-server-specific variants internally. See the
[creative format contract](creative-format-contract.md) for the current mapping
rules and adapter coverage.

## Multi-Tenant Considerations

In multi-tenant mode, each tenant can have their own adapter configuration:

- Different GAM network codes per tenant
- Mix of GAM and Mock adapters
- Per-tenant service accounts

See [Multi-Tenant Setup](../deployment/multi-tenant.md) for configuration details.

## Related Documentation

- [Adapter Architecture](../development/architecture.md#adapter-pattern) - How adapters work internally
- [Security](../security.md) - Adapter security boundaries
