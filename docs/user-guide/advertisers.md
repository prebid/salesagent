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
transport. The database keeps a hash of the token and the token's first 12 characters. The
Admin UI shows the full token once, when you create the advertiser. The advertisers list
shows the first characters of each token so you can tell them apart, never the whole token.

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

An advertiser's token reaches every tool that the sales agent serves. With the token, an
advertiser can do the following:

- Read the sales agent's capabilities and creative formats
- Discover products
- Manage accounts
- Create and update media buys, and read their delivery
- Upload and list creatives
- Track and complete tasks

`get_products` returns only the products open to all advertisers or to that advertiser.
`get_media_buys` and `list_creatives` return only that advertiser's media buys and creatives.

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

- **Slack**: The tenant can post notices about tasks, media buys, and creatives to a Slack channel, as [Creative management](creatives.md#notifications) describes.
- **Buyer webhooks**: A buyer names its own webhook in the `push_notification_config` field of a `create_media_buy`, `update_media_buy`, or `sync_creatives` request. The sales agent posts status changes for that request there. For `sync_creatives`, see [Creative management](creatives.md#notifications). A `sync_accounts` request also accepts the field, but the sales agent sends nothing to it. Known issue: [#1608](https://github.com/prebid/salesagent/issues/1608).
- **Delivery reports**: A buyer sets `reporting_webhook` on `create_media_buy` to receive delivery reports.
