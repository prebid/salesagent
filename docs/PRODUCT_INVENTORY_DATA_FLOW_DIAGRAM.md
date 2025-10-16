# Product Inventory Targeting - Visual Data Flow

## Quick Reference Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    GAM INVENTORY TABLE                          │
├─────────────────────────────────────────────────────────────────┤
│ inventory_id  │ name           │ metadata.ad_unit_code          │
│ "23312403859" │ "Top banner"   │ "ca-pub-7492322059512158"      │
│ "23311659540" │ "Sidebar Ad"   │ "ca-pub-7492322059512158:"     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ↓ (User selects in UI)
                              │
┌─────────────────────────────────────────────────────────────────┐
│                      UI DISPLAY (Modal)                         │
├─────────────────────────────────────────────────────────────────┤
│  ☑ Top banner                                                   │
│    ca-pub-7492322059512158 > Top banner                         │
│    Sizes: 970x250                                               │
│                                                                 │
│  <checkbox value="23312403859">  ← VALUE IS THE ID!            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ↓ (Form submits)
                              │
┌─────────────────────────────────────────────────────────────────┐
│                    FORM SUBMISSION                              │
├─────────────────────────────────────────────────────────────────┤
│  targeted_ad_unit_ids = "23312403859,23311659540"              │
│                                                                 │
│  Backend validates: Are these numeric? ✓                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ↓ (Stored in database)
                              │
┌─────────────────────────────────────────────────────────────────┐
│              PRODUCTS TABLE (Database)                          │
├─────────────────────────────────────────────────────────────────┤
│ product_id: "prod_abc123"                                       │
│ name: "Display Banner Network"                                  │
│ implementation_config: {                                        │
│   "targeted_ad_unit_ids": [                                     │
│     "23312403859",  ← NUMERIC ID STRINGS                        │
│     "23311659540"                                               │
│   ],                                                            │
│   "include_descendants": true                                   │
│ }                                                               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ↓ (Read when creating media buy)
                              │
┌─────────────────────────────────────────────────────────────────┐
│                 GAM ADAPTER (orders.py)                         │
├─────────────────────────────────────────────────────────────────┤
│ impl_config = product.implementation_config                     │
│ ad_unit_ids = impl_config["targeted_ad_unit_ids"]              │
│ # ["23312403859", "23311659540"]                                │
│                                                                 │
│ Validates: Are these numeric? ✓                                │
│                                                                 │
│ Builds GAM request:                                             │
│ targetedAdUnits = [                                             │
│   {"adUnitId": "23312403859", "includeDescendants": true},     │
│   {"adUnitId": "23311659540", "includeDescendants": true}      │
│ ]                                                               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ↓ (API call)
                              │
┌─────────────────────────────────────────────────────────────────┐
│                       GAM API                                   │
├─────────────────────────────────────────────────────────────────┤
│ POST /lineItems                                                 │
│ {                                                               │
│   "targeting": {                                                │
│     "inventoryTargeting": {                                     │
│       "targetedAdUnits": [                                      │
│         {"adUnitId": "23312403859"},  ← NUMERIC ID              │
│         {"adUnitId": "23311659540"}                             │
│       ]                                                         │
│     }                                                           │
│   }                                                             │
│ }                                                               │
│                                                                 │
│ ✓ Line item created targeting those ad units                   │
└─────────────────────────────────────────────────────────────────┘
```

## Key Points

1. **Inventory Table** stores:
   - `inventory_id` = numeric ID (what we use)
   - `name` = human name (what we display)
   - `metadata.ad_unit_code` = GAM code (stored but not used for targeting)

2. **UI displays** names but **form submits** IDs

3. **Database stores** numeric IDs as strings in JSONB

4. **GAM adapter reads** IDs from database and **sends** to GAM API

5. **GAM API receives** numeric IDs and creates targeting

## Data Types Through the Flow

| Stage                | Type              | Example                          |
|---------------------|-------------------|----------------------------------|
| Inventory Table     | `str` (numeric)   | `"23312403859"`                  |
| UI Checkbox Value   | `str` (numeric)   | `"23312403859"`                  |
| Form Submission     | `str` (CSV)       | `"23312403859,23311659540"`      |
| Backend Validation  | `List[str]`       | `["23312403859", "23311659540"]` |
| Database Storage    | `List[str]`       | `["23312403859", "23311659540"]` |
| GAM Adapter         | `List[str]`       | `["23312403859", "23311659540"]` |
| GAM API             | `str` per unit    | `"23312403859"`                  |

**Everything is strings. Numeric strings specifically.**
