# Google Ad Manager (GAM) Adapter

Connect the Prebid Sales Agent to Google Ad Manager to create and manage line items programmatically.

## Getting Started

1. **[Service Account Setup](service-account-setup.md)** - Configure authentication (start here)
2. **[Product Configuration](product-configuration.md)** - Map AdCP products to GAM line item templates
3. **[Testing Setup](testing-setup.md)** - Set up a GAM test environment

## Authentication Options

| Method | Use Case | Maintenance |
|--------|----------|-------------|
| **Service Account** (recommended) | Production | Automatic - no token refresh |
| **OAuth Refresh Token** | Development/testing | Manual - tokens expire |

For multi-tenant deployments, each tenant needs their own service account. See [GCP Provisioning](gcp-provisioning.md) for automatic service account creation.

## Supported Features

The GAM adapter supports:

- **Line Item Types**: Standard, Sponsorship, Network, House
- **Pricing Models**: CPM, vCPM, CPC, Flat Rate
- **Targeting**: Geography, device, custom key-values
- **Creatives**: Display, video (VAST), native

## Pricing Model Mapping

| AdCP Pricing | GAM Line Item Type | Notes |
|--------------|-------------------|-------|
| CPM | Standard or Sponsorship | Based on guarantees |
| vCPM | Standard only | GAM requirement |
| CPC | Standard | |
| Flat Rate | Sponsorship | Translated to CPD |

## Documentation

- [Service Account Setup](service-account-setup.md) - Authentication configuration
- [Product Configuration](product-configuration.md) - Mapping products to GAM
- [Testing Setup](testing-setup.md) - Test environment configuration
- [GCP Provisioning](gcp-provisioning.md) - Automatic service account creation
