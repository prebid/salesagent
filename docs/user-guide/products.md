# Product management

Products define the inventory you sell through the Prebid Sales Agent. A buyer's AI agent
finds products with `get_products` and names one product in each package of a media buy.

## Create a product

To create a product, follow these steps:

1. Open **Settings > Products**.
2. Click **Add New**. When the tenant has no product, click **Create Product** instead.
3. Fill in the product form.
4. Click **Create Product**.

The **Manage** button in the same section opens the products page. That page lists every
product and also has an **Add Product** button.

The tenant's ad server decides which form opens. A Google Ad Manager tenant gets a form
with Google Ad Manager inventory settings. A tenant on any other ad server gets the
general form. On a Mock or Broadstreet tenant, the general form adds that ad server's
product settings. A tenant with no ad server gets the Mock settings, and a Kevel or Triton
Digital tenant gets none.

## Product form

The sections that follow describe what you set on the product form.

### Name and description

**Product Name** is required. Buyers see the name and the description. On the general
form, **Product ID** is optional: leave it blank and the sales agent generates an ID.

### Creative formats

The **Creative Formats** section holds a JSON array of format declarations. Each
declaration names a `format_kind` and its `params`, as in this example:

```json
[{"format_kind": "image", "params": {"width": 300, "height": 250}}]
```

When a publisher declares format options in its `adagents.json` file, the section lists
them. To use one, reference it by `publisher_domain` and `format_option_id`.

### Pricing options

A product needs at least one pricing option. The form opens with one empty pricing option.
Click **+ Add Pricing Option** for each additional way a buyer can pay. The buyer chooses
one option when it creates a media buy.

The following table lists the pricing models in the form.

| Pricing model | Group in the form | What the buyer pays for |
|---------------|-------------------|-------------------------|
| CPM - Fixed Rate | Guaranteed (Fixed Price) | 1,000 impressions, at a fixed rate |
| Flat Rate | Guaranteed (Fixed Price) | The whole campaign, at a fixed cost |
| CPM - Auction | Non-Guaranteed (Auction/Bidding) | 1,000 impressions, at or over a floor price |
| VCPM - Auction | Non-Guaranteed (Auction/Bidding) | 1,000 viewable impressions, at or over a floor price |
| CPC - Auction | Non-Guaranteed (Auction/Bidding) | A click, at or over a floor price |

Each pricing option also has these fields:

- **Currency**: one of the currencies set under
  **Settings > Policies & Workflows > Budget Controls**. When the tenant sets none, the form
  offers USD.
- **Rate**: the price of a fixed-price model.
- **Floor Price**: the minimum price of an auction model.
- **Min Spend Per Package**: an optional minimum spend.

### Publisher properties

Every product names the publisher properties it covers. You select them by tag or by
property ID.

The form offers only the properties that your publishers authorize in their
`adagents.json` files. When the list is empty, add the publisher first.

### Inventory on Google Ad Manager

On the Google Ad Manager form, **Inventory Configuration** sets the product's inventory, as
[GAM settings on the product form](../adapters/gam/product-configuration.md#gam-settings-on-the-product-form)
describes.

### Targeting

**Channel & Geographic Targeting** sets the advertising channels and the countries of the
product. **Custom Targeting (Optional)** adds key-value pairs that define the audience.

### Access control

**Principal Access Control** limits the product to the advertisers you select. When you
select none, every advertiser sees the product.

### Dynamic products

On the Google Ad Manager form, **Dynamic Products (Signals-Based)** turns the product into
a template. Signals agents then generate variants of it from a buyer's brief. The checkbox
needs at least one signals agent, which you add under **Settings > Integrations**.

### Product details

**Delivery Measurement Provider** is required. Measurement notes and a product image URL
are optional.

## Products that buyers do not see

The products page marks a product with a red **Not offered to buyers** badge when buyers
cannot buy it. Hold the pointer over the badge to read the reason.

The sales agent offers a product only when both of these conditions hold:

- Its property selection names at least one property that a publisher's `adagents.json`
  authorizes for this sales agent.
- Every publisher format option it references still resolves.

A product that fails a condition is absent from `get_products`, and `create_media_buy`
refuses it.

A publisher whose `adagents.json` the sales agent cannot fetch stops authorizing the product
once its last fetched file expires. For how long a fetched file counts and how often the
sales agent fetches it again, see
[Publisher adagents.json](../deployment/vm.md#step-11-publisher-adagentsjson). To fetch every
publisher's file at once, click **Sync All Publishers** on the **Publishers & Properties** tab.

## Sample products

A tenant starts without products. When the deployment sets `CREATE_SAMPLE_DATA` and the
`default` tenant has no products, the sales agent adds two sample products to that tenant.
The products are Premium Display - News and Run of Site Display. See
[environment variables](../deployment/environment-variables.md).

## Adapter-specific configuration

### Google Ad Manager

The sales agent stores Google Ad Manager settings with each product and builds line items
from them. See [GAM product configuration](../adapters/gam/product-configuration.md).

### Mock adapter

On a Mock tenant, the product form adds simulation settings such as daily impressions and
fill rate. See [Mock adapter](../adapters/mock/) for the simulation options.
