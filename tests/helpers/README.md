# Test Helpers & Factories

This directory contains test utilities and factory functions for creating AdCP-compliant objects and database records consistently across the test suite.

## 📦 Available Factories

### `adcp_factories.py`

Factory functions for creating test objects. Use these instead of manual construction to:
- Ensure all required fields are present
- Maintain consistency across tests
- Reduce boilerplate code
- Adapt automatically when schemas change

## 🎯 Quick Reference

### When to Use Which Factory

| Factory | Use Case | Example |
|---------|----------|---------|
| `create_test_product()` | AdCP-compliant Product objects (API responses, schema tests) | Testing product serialization, API responses |
| `create_test_db_product()` | Database Product records with `tenant_id` | Integration tests that insert Products into database |
| `create_test_package_request()` | PackageRequest for CreateMediaBuyRequest | Building media buy requests in tests |
| `create_test_package()` | Package response objects | Testing media buy responses |
| `create_test_creative_asset()` | CreativeAsset objects | Creative synchronization tests |
| `create_test_format()` | Format objects | Format catalog tests |
| `create_test_format_id()` | FormatId objects | Product format references |

## 📚 Factory Cookbook

### Products

#### AdCP-Compliant Product (for API responses)

```python
from tests.helpers.adcp_factories import create_test_product

# Minimal product (all defaults)
product = create_test_product()

# Custom product with specific fields
product = create_test_product(
    product_id="video_premium",
    name="Premium Video Ads",
    format_ids=["video_1920x1080", "video_1280x720"],
    delivery_type="non_guaranteed",
)

# Use in schema tests
from src.core.schemas import Product
product = create_test_product(product_id="test_prod")
serialized = product.model_dump()  # AdCP-compliant dict
```

**When to use:** Testing schema serialization, API responses, AdCP compliance.

#### Database Product Record (for integration tests)

```python
from tests.helpers.adcp_factories import create_test_db_product
from src.core.database.database_session import get_db_session

# Minimal database product
product = create_test_db_product(tenant_id="test_tenant")

# Custom database product with inventory profile
product = create_test_db_product(
    tenant_id="test_tenant",
    product_id="video_premium",
    name="Premium Video Ads",
    format_ids=[{"agent_url": "https://creative.example.com", "id": "video_1920x1080"}],
    property_tags=["premium", "sports"],
    inventory_profile_id=123,
)

# Add to database
with get_db_session() as session:
    session.add(product)
    session.commit()
```

**When to use:** Integration tests that need to insert Products into the database.

**Key differences from `create_test_product()`:**
- Requires `tenant_id` parameter
- Uses legacy database field names: `property_tags`, `property_ids`, `properties`
- Returns database model instance (not AdCP schema object)
- Use for database operations, not API serialization

### Packages

#### PackageRequest (for media buy requests)

```python
from tests.helpers.adcp_factories import create_test_package_request

# Minimal package request (all defaults)
pkg = create_test_package_request()

# Custom package request
pkg = create_test_package_request(
    product_id="prod_video",
    buyer_ref="buyer_pkg_001",
    budget=5000.0,
    pricing_option_id="premium_cpm",
    creative_ids=["creative_1", "creative_2"],
    targeting_overlay={"geo_country_any_of": ["US", "CA"]},
)

# Use in CreateMediaBuyRequest
from src.core.schemas import CreateMediaBuyRequest
request = CreateMediaBuyRequest(
    buyer_ref="test_buy",
    brand_manifest={"name": "Test Brand"},
    packages=[pkg],
    start_time="asap",
    end_time="2025-12-31T23:59:59Z",
)
```

**When to use:** Building media buy requests in integration tests.

#### Package (for media buy responses)

```python
from tests.helpers.adcp_factories import create_test_package

# Minimal package
package = create_test_package()

# Custom package with specific fields
package = create_test_package(
    package_id="pkg_001",
    status="active",
    products=["prod_1", "prod_2"],
    impressions=10000,
    clicks=250,
)
```

**When to use:** Testing media buy responses, package status updates.

### Creatives

#### CreativeAsset

```python
from tests.helpers.adcp_factories import create_test_creative_asset

# Minimal creative
creative = create_test_creative_asset()

# Custom creative
creative = create_test_creative_asset(
    creative_id="creative_001",
    name="Summer Campaign Banner",
    format_id="display_728x90",
    assets={
        "primary": {
            "url": "https://cdn.example.com/banner.jpg",
            "mime_type": "image/jpeg",
            "width": 728,
            "height": 90,
        }
    },
)
```

**When to use:** Creative synchronization tests, format validation tests.

### Formats

#### FormatId (for product format references)

```python
from tests.helpers.adcp_factories import create_test_format_id

# Standard format ID
format_id = create_test_format_id("display_300x250")

# Custom format ID with specific agent URL
format_id = create_test_format_id(
    format_id="video_1920x1080",
    agent_url="https://my-creative-agent.example.com"
)

# Use in product creation
product = create_test_product(
    format_ids=[
        create_test_format_id("display_300x250"),
        create_test_format_id("display_728x90"),
    ]
)
```

**When to use:** Product format references, format validation tests.

#### Format (for format catalog)

```python
from tests.helpers.adcp_factories import create_test_format

# Minimal format
format = create_test_format()

# Custom format
format = create_test_format(
    format_id="video_1920x1080",
    name="Full HD Video",
    type="video",
    is_standard=True,
)
```

**When to use:** Format catalog tests, creative format validation.

### Publisher Properties

#### By Tag Variant (most common)

```python
from tests.helpers.adcp_factories import create_test_publisher_properties_by_tag

# Default all_inventory tag
props = create_test_publisher_properties_by_tag()

# Custom tags
props = create_test_publisher_properties_by_tag(
    publisher_domain="news.example.com",
    property_tags=["premium", "sports", "homepage"]
)

# Use in product creation
product = create_test_product(
    publisher_properties=[props]
)
```

**When to use:** Products that use property tags for inventory selection.

#### By ID Variant

```python
from tests.helpers.adcp_factories import create_test_publisher_properties_by_id

# Custom property IDs
props = create_test_publisher_properties_by_id(
    publisher_domain="news.example.com",
    property_ids=["homepage_slot_1", "article_sidebar"]
)

# Use in product creation
product = create_test_product(
    publisher_properties=[props]
)
```

**When to use:** Products that use specific property IDs for inventory selection.

### Pricing Options

#### CPM Fixed Rate (most common)

```python
from tests.helpers.adcp_factories import create_test_cpm_pricing_option

# Default $10 CPM
pricing = create_test_cpm_pricing_option()

# Custom CPM rate
pricing = create_test_cpm_pricing_option(
    pricing_option_id="premium_cpm",
    currency="EUR",
    rate=15.0,
    min_spend_per_package=1000.0,
)

# Use in product creation
product = create_test_product(
    pricing_options=[pricing]
)
```

**When to use:** Products with fixed CPM pricing.

### Brand Manifests

```python
from tests.helpers.adcp_factories import create_test_brand_manifest

# Default brand
brand = create_test_brand_manifest()

# Custom brand
brand = create_test_brand_manifest(
    name="Nike",
    promoted_offering="Air Jordan 2025 Basketball Shoes",
    tagline="Just Do It",
    category="Sporting Goods",
)

# Use in media buy requests
request = CreateMediaBuyRequest(
    buyer_ref="test_buy",
    brand_manifest=brand,
    packages=[...],
)
```

**When to use:** Media buy requests, brand manifest validation tests.

## 🔧 Common Patterns

### Pattern 1: Integration Test with Database Product

```python
from tests.helpers.adcp_factories import create_test_db_product
from src.core.database.database_session import get_db_session

def test_media_buy_with_custom_product(integration_db):
    """Test media buy creation with custom product."""
    with get_db_session() as session:
        # Create test product in database
        product = create_test_db_product(
            tenant_id="test_tenant",
            product_id="video_premium",
            name="Premium Video Ads",
            format_ids=[{"agent_url": "https://creative.example.com", "id": "video_1920x1080"}],
            property_tags=["premium"],
        )
        session.add(product)
        session.commit()

        # Use product in media buy
        # ... rest of test
```

### Pattern 2: Media Buy Request with Multiple Packages

```python
from tests.helpers.adcp_factories import (
    create_test_brand_manifest,
    create_test_package_request,
)
from src.core.schemas import CreateMediaBuyRequest

request = CreateMediaBuyRequest(
    buyer_ref="multi_package_buy",
    brand_manifest=create_test_brand_manifest(name="Nike"),
    packages=[
        create_test_package_request(
            product_id="display_news",
            buyer_ref="pkg_1",
            budget=10000.0,
        ),
        create_test_package_request(
            product_id="video_sports",
            buyer_ref="pkg_2",
            budget=15000.0,
        ),
    ],
    start_time="asap",
    end_time="2025-12-31T23:59:59Z",
)
```

### Pattern 3: Product with Inventory Profile

```python
from tests.helpers.adcp_factories import create_test_db_product
from src.core.database.database_session import get_db_session
from src.core.database.models import InventoryProfile

def test_product_with_inventory_profile(integration_db):
    """Test product linked to inventory profile."""
    with get_db_session() as session:
        # Create inventory profile
        profile = InventoryProfile(
            tenant_id="test_tenant",
            name="Premium Inventory",
            publisher_properties=[{
                "publisher_domain": "news.example.com",
                "property_tags": ["premium", "homepage"],
                "selection_type": "by_tag",
            }],
        )
        session.add(profile)
        session.flush()

        # Create product linked to profile
        product = create_test_db_product(
            tenant_id="test_tenant",
            product_id="premium_display",
            inventory_profile_id=profile.id,
        )
        session.add(product)
        session.commit()

        # Product now uses profile's publisher_properties
        # ... rest of test
```

## ⚠️ Common Mistakes

### ❌ Mixing Database and Schema Products

```python
# WRONG - Using schema Product for database operations
from src.core.schemas import Product
product = create_test_product()  # Schema Product
session.add(product)  # ERROR: Schema objects can't be added to database

# CORRECT - Use database factory for database operations
from tests.helpers.adcp_factories import create_test_db_product
product = create_test_db_product(tenant_id="test_tenant")
session.add(product)  # Works!
```

### ❌ Forgetting tenant_id for Database Products

```python
# WRONG - Database Product requires tenant_id
product = create_test_db_product(product_id="test")  # ERROR: Missing required tenant_id

# CORRECT - Always provide tenant_id for database products
product = create_test_db_product(tenant_id="test_tenant", product_id="test")
```

### ❌ Using Wrong Field Names for Database Products

```python
# WRONG - Database Product uses legacy field names
product = create_test_db_product(
    tenant_id="test_tenant",
    publisher_properties=[...]  # ERROR: Database model uses property_tags/property_ids
)

# CORRECT - Use legacy field names for database products
product = create_test_db_product(
    tenant_id="test_tenant",
    property_tags=["all_inventory"]  # Database model field
)
```

### ❌ Manual Construction Instead of Factory

```python
# WRONG - Manual construction duplicates boilerplate
from src.core.database.models import Product

product = Product(
    tenant_id="test_tenant",
    product_id="test",
    name="Test",
    description="Test product",
    format_ids=[{"agent_url": "https://...", "id": "display_300x250"}],
    targeting_template={},
    delivery_type="guaranteed",
    property_tags=["all_inventory"],
)

# CORRECT - Use factory
from tests.helpers.adcp_factories import create_test_db_product

product = create_test_db_product(
    tenant_id="test_tenant",
    product_id="test",
    name="Test",
    description="Test product",
    # All other fields use sensible defaults
)
```

## 📊 Factory Adoption Status

As of 2025-01-17:

- ✅ **PackageRequest**: 40+ uses across 7 integration test files
- ✅ **Database Product**: 40+ uses across 10 integration/E2E test files
- 🔄 **AdCP Product**: Used in schema tests and conftest.py fixtures
- 🔄 **CreativeAsset**: Partially adopted in creative tests
- 📝 **Format/FormatId**: Available but adoption pending

**Goal**: 80%+ of integration/E2E tests using factories by end of Q1 2025.

## 🚀 Contributing

When adding new factory functions:

1. **Follow naming convention**: `create_test_{entity_name}()`
2. **Provide sensible defaults**: Tests should work with minimal arguments
3. **Support customization**: Accept `**kwargs` for optional fields
4. **Add docstring**: Include examples and explain when to use
5. **Add to this README**: Update Quick Reference and Cookbook sections

## 📚 Further Reading

- [AdCP Specification](https://adcontextprotocol.org/docs/)
- [Testing Guide](../../docs/testing/README.md)
- [AdCP Compliance Tests](../../docs/testing/adcp-compliance.md)
