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
from fastmcp.client import Client, StreamableHttpTransport

# Get your endpoint and token from Admin UI > Advertisers > View Token
transport = StreamableHttpTransport(
    url="https://your-domain.com/mcp/",
    headers={"x-adcp-auth": "your-principal-token"}
)

async with Client(transport=transport) as client:
    # List available products
    products = await client.call_tool("get_products", {"brief": "video ads"})

    # Create a media buy
    result = await client.call_tool("create_media_buy", {
        "product_ids": ["prod_123"],
        "budget": {"amount": 10000, "currency": "USD"},
        "flight_start": "2024-02-01",
        "flight_end": "2024-02-28"
    })
```

## Related Documentation

- [Adapters](../adapters/) - Configure your ad server connection
- [Deployment](../deployment/) - Deployment options and configuration
