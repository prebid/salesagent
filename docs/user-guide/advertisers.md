# Advertiser Management

Advertisers (also called "principals") are the entities that can access your MCP API to create media buys. Each advertiser gets their own API token.

## Creating Advertisers

### Via Admin UI

1. Open **Settings > Advertisers** in the Admin UI
2. Click **Add Advertiser**
3. Enter advertiser details:
   - **Advertiser ID**: A unique identifier, lowercase with underscores
   - **Advertiser Name**: The name shown in reports
   - **GAM Advertiser** (only when the tenant uses GAM): Links to an existing GAM advertiser

### API Token

Each advertiser gets a unique API token, sent as `Authorization: Bearer <token>` on every
transport. The server stores only a hash of it (`principals.token_hash`), so the token is shown once: in the
confirmation message after you create the advertiser. Copy it then. The **Current
Advertisers** list shows the first characters of each token so you can tell them apart,
never the whole token.

If a token is lost or compromised, rotate it:

1. Open **Settings > Advertisers**
2. In the advertiser's row, click the rotate button (🔄) next to the token in the
   **Access Token** column, and confirm
3. Copy the new token from the dialog now; it is not shown again, and the old one stops
   working at once

## Platform Mappings

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

## Access Control

Advertisers can only:
- View products available to them
- Create media buys under their account
- Manage their own creatives
- View their own campaigns

They cannot access other advertisers' data or system configuration.

## MCP Client Configuration

Give the advertiser its token and the first call in
[quickstart step 7](../quickstart.md#7-connect-an-ai-agent).

## Notifications

Configure email notifications for advertisers:
- Media buy status changes
- Creative approval/rejection
- Campaign delivery alerts
