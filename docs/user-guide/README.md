# User guide

Once you have the Prebid Sales Agent deployed, this guide covers how to configure and use it.

## First steps

After deployment, you'll need to:

1. **Configure your ad server adapter** - Connect to GAM or use the Mock adapter for testing
2. **Set up products** - Define what inventory you're selling
3. **Add advertisers** - Create principals who can access the MCP API
4. **Configure creatives workflow** - Set up approval rules

## Admin UI overview

The Admin UI is served at `/admin`. Each tenant has a dashboard and a settings page.

The dashboard shows revenue, live media buys, active advertisers, and recent activity. The
**Needs Attention** card opens the media buys list. The **Operations** panel opens the
following pages:

- **Uploaded Creatives** - Review, approve, and reject creatives
- **Reports** - Read delivery reports, for a tenant that uses Google Ad Manager
- **Workflows** - Approve or reject pending workflow steps
- **Webhooks** - Read the delivery webhook log
- **Settings** - Configure the tenant

The settings page has the following sections:

- **Account** - Organization name, branding, custom domain, and authorized domains and emails
- **Setup Checklist** - The tasks that remain before the tenant can take orders
- **Users & Access** - Users, allowed domains, and single sign-on (SSO)
- **Ad Server** - The ad server adapter and its connection
- **Policies & Workflows** - Budget controls, naming conventions, approval workflow, creative review, and advertising policy
- **Integrations** - Slack, AI services, creative agents, and signals agents
- **Publishers** - The publishers whose `adagents.json` authorizes this sales agent
- **Products** - The product catalog
- **Inventory** - Inventory sync status, the inventory browser, and the targeting browser
- **Advertisers** - Principals and their API tokens
- **Danger Zone** - Deactivation of the sales agent, hidden in single-tenant mode

A super admin also sees **Accounts** and **Create Account** in the page header. **Accounts**
lists every tenant.

## Access levels

The Admin UI recognizes two kinds of user:

- **Super admin** - An address listed in `SUPER_ADMIN_EMAILS`, or an address in a domain listed in `SUPER_ADMIN_DOMAINS`. A super admin reaches every tenant and passes every role check.
- **Tenant user** - An active user on the tenant's **Users & Access** page. You add a user by email address, or admit a whole email domain. A tenant user reaches that tenant only.

A tenant user's role decides what that user can do in the tenant:

- **Viewer** - Opens pages. The role check refuses its `POST`, `PUT`, `PATCH`, and `DELETE` requests.
- **Manager** - Changes settings, products, and campaigns, but not the admin-only pages.
- **Admin** - Also manages users and roles, authorized domains and emails, Setup Mode, SSO,
  and the deactivation of the tenant.

**Users & Access** adds a user as a viewer unless an admin picks another role. Someone who
signs in from an authorized domain or email without an invitation becomes an admin. To
revoke access, deactivate the user on **Users & Access**.

See [SSO setup](sso-setup.md) for configuring authentication, and
[Security and authentication](../security.md) for how the Admin UI checks access.

## Guides

- **[Products](products.md)** - Setting up your product catalog
- **[Advertisers](advertisers.md)** - Managing principals and API access
- **[Creatives](creatives.md)** - Creative approval workflow
- **[Publishers and authorized properties](publishers.md)** - Adding the publishers you sell for
- **[Inventory and inventory profiles](inventory.md)** - Syncing and browsing ad server inventory
- **[Media buys, approvals, and workflows](media-buys.md)** - Reviewing, approving, and rejecting media buys
- **[Tenant settings](settings.md)** - The settings page, section by section

## Connect AI agents

Once configured, AI agents connect via MCP:

```python
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
        "idempotency_key": "acme-video-0001-2f9c",  # 16-255 characters; repeat it only to retry
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

## Related documentation

- [Adapters](../adapters/) - Configure your ad server connection
- [Deployment](../deployment/) - Deployment options and configuration
