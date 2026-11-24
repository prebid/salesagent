# User guide

Once you have the Prebid Sales Agent deployed, this guide covers how to configure and use it.

## First steps

After deployment, you'll need to:

1. **Configure your ad server adapter** - Connect Google Ad Manager, Kevel, Broadstreet, or Triton Digital, or use the Mock adapter for testing
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
- **Workflows** - Read the media buys, the workflow steps, and the audit log
- **Webhooks** - Read the audit-log entries of the `send_delivery_webhook` operation
- **Settings** - Configure the tenant

The sales agent records delivery webhooks in a separate delivery log, which the **Webhooks**
page does not read. Known issue: [#2604](https://github.com/prebid/salesagent/issues/2604).

The settings page has a navigation menu. **Setup Checklist**, **Users & Access**,
**Signing Keys**, and **Buyer Accounts** open separate pages, and each other entry opens a
section of the settings page. The menu has the following entries:

- **Account** - Organization name, branding, custom domain, and authorized domains and emails
- **Setup Checklist** - Every setup task and whether it is complete, including the critical tasks that must be complete before the tenant can take orders
- **Users & Access** - Users, allowed domains, and single sign-on (SSO)
- **Ad Server** - The ad server adapter and its connection
- **Policies & Workflows** - Budget controls, brand manifest policy, naming conventions, measurement providers, approval workflow, creative review, advertising policy, and product ranking
- **Integrations** - Slack, AI services, creative agents, and signals agents
- **Signing Keys** - The RFC 9421 keys that the tenant signs with, published at `/.well-known/jwks.json`
- **Publishers** - The publishers you represent
- **Products** - The product catalog
- **Inventory** - For a Google Ad Manager tenant, the inventory sync status and links to the inventory browser and the targeting browser
- **Advertisers** - Principals, the first characters of each API token, and token rotation
- **Buyer Accounts** - Buyers' accounts, with their brand, operator, billing, and status
- **Danger Zone** - Deactivation of the sales agent, hidden in single-tenant mode

In multi-tenant mode, `/admin` opens the **Accounts** page for a super admin. The page lists
every active tenant, 50 per page, and has a **Create New Account** button.

## Access levels

The Admin UI has two kinds of user: a super admin, who reaches every tenant, and a tenant
user, who reaches one tenant with a viewer, manager, or admin role. On the tenant's
**Users & Access** page, you add a user by email address, and you revoke a user's access
by deactivating the user.

[Super admins](../security.md#super-admins) and [Tenant users](../security.md#tenant-users)
describe who counts as a super admin, what each role can do, and who can sign in without
an invitation. See [SSO setup](sso-setup.md) for configuring authentication.

## Guides

- **[Products](products.md)** - Setting up your product catalog
- **[Advertisers](advertisers.md)** - Managing principals and API access
- **[Creatives](creatives.md)** - Creative approval workflow

## Connect AI agents

An AI agent connects over MCP with an advertiser's API token, as
[MCP client configuration](advertisers.md#mcp-client-configuration) shows.

## Related documentation

- [Adapters](../adapters/) - Configure your ad server connection
- [Deployment](../deployment/) - Deployment options and configuration
