# Advertiser management

Advertisers (also called "principals") are the entities that can access your MCP API to create media buys. Each advertiser gets their own API token.

## Create advertisers

To add an advertiser in the Admin UI, do the following:

1. Open **Settings** and select the **Advertisers** section.
2. Click **Add Advertiser**.
3. Enter the advertiser details:
   - **Advertiser ID**: The form requires a value made of letters, digits, underscores, and hyphens. The sales agent does not store it.
   - **Advertiser Name**: The company name as it appears in reports.
   - **GAM Advertiser**: The GAM advertiser that owns this advertiser's campaigns. Only a Google Ad Manager tenant has this field.
4. Click **Add Advertiser**.

The sales agent assigns the principal ID itself: `prin_` followed by eight hexadecimal
characters.

### API token

Each advertiser gets a unique API token, sent as `Authorization: Bearer <token>` on every
transport. The server stores only a hash of it, so the token is shown exactly once, when the
advertiser is created. The advertisers list shows the first characters of each token so you
can tell them apart, never the whole token.

If a token is lost or compromised, rotate it:

1. Open **Settings** and select the **Advertisers** section.
2. In the **Access Token** column, click the rotate button next to the token, and confirm.
3. Copy the new token now; it is not shown again, and the old one stops working at once.

## Platform mappings

For ad server integration, advertisers need platform mappings:

### Google Ad Manager

```json
{
  "platform_mappings": {
    "google_ad_manager": {
      "advertiser_id": "123456",
      "enabled": true
    }
  }
}
```

The `advertiser_id` links to the GAM advertiser for order creation.

## Access control

Advertisers can only:
- View products available to them
- Create media buys under their account
- Manage their own creatives
- View their own campaigns

They cannot access other advertisers' data or system configuration.

## MCP client configuration

Advertisers connect via MCP using their token:

```python
from fastmcp.client import Client, StreamableHttpTransport

transport = StreamableHttpTransport(
    url="https://your-domain.com/mcp/",
    headers={"Authorization": "Bearer advertiser-api-token"}
)

async with Client(transport=transport) as client:
    # Advertiser can now call MCP tools
    products = await client.call_tool("get_products", {})
```

## Notifications

The sales agent sends notices through Slack and webhooks. It sends no email.

- **Slack**: Under **Settings** > **Integrations** > **Slack Integration**, set the **Task Notifications Webhook URL**. That channel receives notices about new tasks and creative reviews.
- **Buyer webhooks**: A buyer names its own webhook in the `push_notification_config` field of a `create_media_buy`, `update_media_buy`, `sync_creatives`, or `sync_accounts` request. The sales agent posts status changes for that request there.
- **Delivery reports**: A buyer sets `reporting_webhook` on `create_media_buy` to receive delivery reports. The dashboard's **Webhooks** page lists the delivery webhooks the sales agent sent.
