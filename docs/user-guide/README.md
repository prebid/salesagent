# User Guide

Once you have the Prebid Sales Agent deployed, this guide covers how to configure and use it.

## First Steps

After deployment, you'll need to:

1. **Configure your ad server adapter** - Connect to GAM or use the Mock adapter for testing
2. **Set up products** - Define what inventory you're selling
3. **Add advertisers** - Create principals who can access the MCP API
4. **Configure creatives workflow** - Set up approval rules

## Admin UI Overview

The Admin UI (accessible at `/admin`) provides:

- **Dashboard** - Overview of activity and status
- **Products** - Manage your product catalog
- **Advertisers** - Manage principals and API tokens
- **Media Buys** - View and manage campaigns
- **Creatives** - Review and approve creatives
- **Settings** - Configure adapters, currencies, and more

## Access Levels

| Role | Access |
|------|--------|
| **Tenant Admin** | Full access to manage products, advertisers, campaigns, and users for one tenant |
| **Tenant User** | Read-only access to view products and campaigns |

Users and access levels are managed **per-tenant** via the **Users & Access** page in the Admin UI. See [SSO Setup](sso-setup.md) for configuring authentication.

## Guides

- **[Products](products.md)** - Setting up your product catalog
- **[Advertisers](advertisers.md)** - Managing principals and API access
- **[Creatives](creatives.md)** - Creative approval workflow

## Connecting AI Agents

Once configured, AI agents connect via MCP:

```python
import uuid

from fastmcp.client import Client, StreamableHttpTransport

# The Admin UI shows an advertiser's token once: when you add the advertiser
# (Settings > Advertisers > Add Advertiser) or rotate its token.
transport = StreamableHttpTransport(
    url="https://your-domain.com/mcp/",
    headers={"Authorization": "Bearer your-principal-token"}
)

async with Client(transport=transport) as client:
    # List available products
    products = await client.call_tool("get_products", {"brief": "video ads"})

    # Create a media buy. Each package names a product and one of its pricing options.
    result = await client.call_tool("create_media_buy", {
        # A fresh UUID v4 per request; resend the same key only to retry the identical payload.
        "idempotency_key": str(uuid.uuid4()),
        "account": {"account_id": "acc_123"},  # from sync_accounts or list_accounts
        "brand": {"domain": "acme.example"},
        "start_time": "2030-02-01T00:00:00Z",  # ISO 8601, or "asap"
        "end_time": "2030-02-28T23:59:59Z",
        "packages": [{
            "product_id": "prod_123",
            "pricing_option_id": "cpm_usd_fixed",  # from the product's pricing_options
            "budget": 10000
        }]
    })
```

## Related Documentation

- [Adapters](../adapters/) - Configure your ad server connection
- [Deployment](../deployment/) - Deployment options and configuration
