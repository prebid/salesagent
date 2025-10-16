# Product Inventory Targeting - Data Flow

## Clear Documentation of Data Storage and Usage

This document explains **exactly** what gets stored in the database for products and how it's used when creating GAM line items.

---

## Database Storage (Product Table)

### Products Table Structure

```sql
products
  ├── product_id (primary key)
  ├── tenant_id
  ├── name
  ├── implementation_config (JSONB)  ← Inventory targeting stored here
  └── ...other fields
```

### implementation_config JSONB Structure

The `implementation_config` column contains a JSON blob with GAM-specific configuration:

```json
{
  "targeted_ad_unit_ids": ["23312403859", "23311659540", "23313239368"],
  "targeted_placement_ids": ["12345678"],
  "include_descendants": true,
  "line_item_type": "PRICE_PRIORITY",
  "priority": 12,
  "cost_type": "CPM",
  "creative_placeholders": [
    {"width": 970, "height": 250}
  ],
  ...other GAM config
}
```

**Critical Fields for Inventory Targeting:**
- `targeted_ad_unit_ids`: **Array of NUMERIC STRING IDs** (e.g., `["23312403859"]`)
  - ❌ NOT ad unit codes like `"ca-pub-7492322059512158"`
  - ❌ NOT ad unit names like `"Top banner"`
  - ✅ ONLY numeric GAM ad unit IDs

- `targeted_placement_ids`: **Array of NUMERIC STRING IDs** (e.g., `["12345678"]`)
  - Optional, can be empty or omitted

- `include_descendants`: **Boolean** (default: `true`)
  - Whether to target child ad units

---

## Data Flow: UI → Database → GAM

### Step 1: User Selects Ad Units in UI

**User Action:** Clicks "Browse Ad Units" in product form

**UI Fetches Inventory:**
```javascript
GET /api/tenant/{tenant_id}/inventory-list?type=ad_unit

Response:
{
  "items": [
    {
      "id": "23312403859",           // ← Numeric ID (what gets saved)
      "name": "Top banner",           // ← Human name (what gets displayed)
      "type": "ad_unit",
      "path": ["ca-pub-7492322059512158", "Top banner"],
      "metadata": {
        "ad_unit_code": "ca-pub-7492322059512158"
      }
    }
  ]
}
```

**UI Shows:**
```
☑ Top banner
  ca-pub-7492322059512158 > Top banner
  Sizes: 970x250
```

**Checkbox Value:** `"23312403859"` (the numeric ID)

### Step 2: User Saves Product

**Form Submission:**
```javascript
// Hidden textarea contains comma-separated IDs
document.getElementById('targeted_ad_unit_ids').value = "23312403859,23311659540"

// Form submits
POST /tenant/{tenant_id}/products/{product_id}
  targeted_ad_unit_ids: "23312403859,23311659540"
```

**Backend Processing:**
```python
# products.py line 716-734
ad_unit_ids = form_data.get("targeted_ad_unit_ids", "").strip()
if ad_unit_ids:
    # Parse comma-separated IDs
    id_list = [id.strip() for id in ad_unit_ids.split(",") if id.strip()]

    # Validate that all IDs are numeric
    invalid_ids = [id for id in id_list if not id.isdigit()]
    if invalid_ids:
        flash(f"Invalid ad unit IDs: {', '.join(invalid_ids)}")
        return redirect(...)

    # Store in implementation_config
    base_config["targeted_ad_unit_ids"] = id_list
```

**Stored in Database:**
```json
{
  "targeted_ad_unit_ids": ["23312403859", "23311659540"]
}
```

### Step 3: Creating Media Buy (Database → GAM)

**When creating a media buy, GAM adapter reads from database:**

```python
# orders.py line 321-342
impl_config = product.implementation_config or {}

if impl_config.get("targeted_ad_unit_ids"):
    # Get numeric IDs from database
    ad_unit_ids = impl_config["targeted_ad_unit_ids"]
    # ad_unit_ids = ["23312403859", "23311659540"]

    # Validate they are numeric (safety check)
    invalid_ids = [id for id in ad_unit_ids if not str(id).isdigit()]
    if invalid_ids:
        raise ValueError(f"Invalid ad unit IDs: {invalid_ids}")

    # Build GAM API request
    line_item_targeting["inventoryTargeting"]["targetedAdUnits"] = [
        {
            "adUnitId": ad_unit_id,
            "includeDescendants": impl_config.get("include_descendants", True)
        }
        for ad_unit_id in ad_unit_ids
    ]
```

**Sent to GAM API:**
```json
{
  "lineItem": {
    "targeting": {
      "inventoryTargeting": {
        "targetedAdUnits": [
          {
            "adUnitId": "23312403859",
            "includeDescendants": true
          },
          {
            "adUnitId": "23311659540",
            "includeDescendants": true
          }
        ]
      }
    }
  }
}
```

**GAM Receives:** Numeric ad unit IDs and creates line item targeting those ad units.

---

## Data Types at Each Stage

### Storage (Database)
```python
product.implementation_config = {
    "targeted_ad_unit_ids": ["23312403859", "23311659540"],  # List[str] - numeric strings
    "targeted_placement_ids": ["12345678"],                   # List[str] - numeric strings
    "include_descendants": True                               # bool
}
```

### Retrieval (Reading from Database)
```python
impl_config = product.implementation_config  # dict
ad_unit_ids = impl_config.get("targeted_ad_unit_ids", [])  # List[str]
# ad_unit_ids = ["23312403859", "23311659540"]
```

### Sent to GAM (API Request)
```python
# GAM expects strings (they convert to long internally)
targetedAdUnits = [
    {"adUnitId": "23312403859", "includeDescendants": True},  # adUnitId is str
    {"adUnitId": "23311659540", "includeDescendants": True}   # adUnitId is str
]
```

---

## Common Mistakes to Avoid

### ❌ WRONG: Storing Ad Unit Codes
```json
{
  "targeted_ad_unit_ids": ["ca-pub-7492322059512158"]  // This is a CODE, not an ID!
}
```

### ❌ WRONG: Storing Ad Unit Names
```json
{
  "targeted_ad_unit_ids": ["Top banner"]  // This is a NAME, not an ID!
}
```

### ❌ WRONG: Storing Integers
```json
{
  "targeted_ad_unit_ids": [23312403859]  // Should be string: "23312403859"
}
```

### ✅ CORRECT: Storing Numeric String IDs
```json
{
  "targeted_ad_unit_ids": ["23312403859", "23311659540"]
}
```

---

## Validation Points

### 1. Form Submit (Backend - products.py)
```python
# Line 722-732
invalid_ids = [id for id in id_list if not id.isdigit()]
if invalid_ids:
    flash(f"Invalid ad unit IDs: {', '.join(invalid_ids)}")
    return redirect(...)
```

### 2. Media Buy Creation (GAM Adapter - orders.py)
```python
# Line 327-337
invalid_ids = [id for id in ad_unit_ids if not str(id).isdigit()]
if invalid_ids:
    raise ValueError(f"Invalid ad unit IDs: {invalid_ids}")
```

### 3. GAM API (Final Validation)
- GAM API will reject non-numeric IDs
- Returns error if ad unit ID doesn't exist

---

## Summary

**What's stored:** Numeric ad unit IDs as strings in `implementation_config.targeted_ad_unit_ids`

**What's displayed:** Human-readable names mapped from IDs via inventory table

**What's sent to GAM:** Same numeric IDs from database, wrapped in GAM API structure

**Key Rule:** ONLY numeric IDs get stored. Names and codes are for display only.
