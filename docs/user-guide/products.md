# Product Management

Products define what inventory you're selling through the Prebid Sales Agent. Each product maps to a line item template in your ad server.

## Creating Products

### Via Admin UI

1. Navigate to **Products** in the Admin UI
2. Click **Add Product**
3. Fill in product details:
   - **Name**: Display name for AI agents
   - **Description**: What this product offers
   - **Pricing Model**: CPM, vCPM, CPC, or Flat Rate
   - **Base Rate**: Default price
   - **Formats**: Supported creative formats
   - **Targeting**: Available targeting options

### Default Products

New tenants with demo data get standard products:
- Premium Display (guaranteed)
- Standard Display (non-guaranteed)
- Video Pre-Roll (guaranteed)
- Native Content (guaranteed)
- Mobile Display (non-guaranteed)
- Newsletter Sponsorship (guaranteed)

## Product Configuration

### Pricing Models

| Model | Description | Use Case |
|-------|-------------|----------|
| **CPM** | Cost per thousand impressions | Standard display/video |
| **vCPM** | Viewable CPM | Premium placements |
| **CPC** | Cost per click | Performance campaigns |
| **Flat Rate** | Fixed price for period | Sponsorships |

### Guarantees

- **Guaranteed**: Committed impressions, higher priority
- **Non-Guaranteed**: Best effort delivery, lower priority

### Targeting Options

Products can specify available targeting:
- **Geography**: Countries, regions, cities
- **Device**: Desktop, mobile, tablet
- **Custom**: Key-value targeting specific to your GAM setup

## Adapter-Specific Configuration

### Google Ad Manager

Products map to GAM line item templates. See [GAM Product Configuration](../adapters/gam/product-configuration.md) for details on:
- Line item type selection
- Order templates
- Custom targeting keys

### Mock Adapter

The mock adapter accepts all products for testing. See [Mock Adapter](../adapters/mock/) for simulation options.

## Bulk Operations

### CSV Import

Upload products via CSV:

```csv
name,description,pricing_model,base_rate,currency
"Premium Display","Above-the-fold display ads",CPM,15.00,USD
"Video Pre-Roll","15-30 second video ads",CPM,25.00,USD
```

### JSON Import

Import via API:

```bash
curl -X POST "/api/products/import" \
  -H "Content-Type: application/json" \
  -d @products.json
```
