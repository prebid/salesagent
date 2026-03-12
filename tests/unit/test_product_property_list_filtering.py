"""Unit tests for property list filtering in get_products.

Tests the filtering logic that restricts products based on buyer property lists.
The property_list parameter on get_products allows buyers to specify which
publisher properties they want to target. Products are filtered based on
overlap between the buyer's allowed properties and the product's
publisher_properties.

Filtering rules:
- Products with selection_type="all" always match (they cover all properties)
- Products with no property overlap are EXCLUDED
- Products with property_targeting_allowed=false require ALL product properties
  to be in the allowed set (full subset match)
- Products with property_targeting_allowed=true require ANY intersection
"""

from unittest.mock import MagicMock, Mock, patch

from adcp.types.generated_poc.core.property_id import PropertyId
from adcp.types.generated_poc.core.publisher_property_selector import (
    PublisherPropertySelector,
    PublisherPropertySelector1,
    PublisherPropertySelector2,
)


def _make_selector_all(domain: str = "example.com") -> PublisherPropertySelector:
    """Create a 'select all' publisher property selector."""
    return PublisherPropertySelector(
        root=PublisherPropertySelector1(
            publisher_domain=domain,
            selection_type="all",
        )
    )


def _make_selector_by_id(property_ids: list[str], domain: str = "example.com") -> PublisherPropertySelector:
    """Create a 'by_id' publisher property selector."""
    return PublisherPropertySelector(
        root=PublisherPropertySelector2(
            publisher_domain=domain,
            selection_type="by_id",
            property_ids=[PropertyId(root=pid) for pid in property_ids],
        )
    )


def _make_mock_product(
    product_id: str,
    publisher_properties: list[PublisherPropertySelector],
    property_targeting_allowed: bool = False,
) -> Mock:
    """Create a mock product with publisher_properties and property_targeting_allowed."""
    product = Mock()
    product.product_id = product_id
    product.publisher_properties = publisher_properties
    product.property_targeting_allowed = property_targeting_allowed
    return product


class TestExtractPropertyIds:
    """Test extraction of property IDs from publisher_properties."""

    def test_extract_from_by_id_selector(self):
        from src.core.tools.products import extract_product_property_ids

        selectors = [_make_selector_by_id(["prop_1", "prop_2"])]
        result = extract_product_property_ids(selectors)
        assert result == {"prop_1", "prop_2"}

    def test_extract_from_multiple_by_id_selectors(self):
        from src.core.tools.products import extract_product_property_ids

        selectors = [
            _make_selector_by_id(["prop_1", "prop_2"], domain="sitea.com"),
            _make_selector_by_id(["prop_3"], domain="siteb.com"),
        ]
        result = extract_product_property_ids(selectors)
        assert result == {"prop_1", "prop_2", "prop_3"}

    def test_all_selector_returns_none(self):
        """selection_type='all' means the product covers ALL properties, return None to signal this."""
        from src.core.tools.products import extract_product_property_ids

        selectors = [_make_selector_all()]
        result = extract_product_property_ids(selectors)
        assert result is None

    def test_mixed_all_and_by_id_returns_none(self):
        """If any selector is 'all', the product covers everything."""
        from src.core.tools.products import extract_product_property_ids

        selectors = [
            _make_selector_by_id(["prop_1"]),
            _make_selector_all(),
        ]
        result = extract_product_property_ids(selectors)
        assert result is None

    def test_empty_selectors_returns_empty_set(self):
        from src.core.tools.products import extract_product_property_ids

        result = extract_product_property_ids([])
        assert result == set()


class TestShouldIncludeProduct:
    """Test the product inclusion logic for property list filtering."""

    def test_product_with_all_selector_always_included(self):
        from src.core.tools.products import should_include_product_for_property_list

        product = _make_mock_product(
            "prod_all",
            publisher_properties=[_make_selector_all()],
            property_targeting_allowed=False,
        )
        allowed = {"prop_1", "prop_2"}
        assert should_include_product_for_property_list(product, allowed) is True

    def test_no_overlap_excluded(self):
        from src.core.tools.products import should_include_product_for_property_list

        product = _make_mock_product(
            "prod_no_overlap",
            publisher_properties=[_make_selector_by_id(["prop_a", "prop_b"])],
            property_targeting_allowed=True,
        )
        allowed = {"prop_x", "prop_y"}
        assert should_include_product_for_property_list(product, allowed) is False

    def test_targeting_allowed_partial_overlap_included(self):
        """property_targeting_allowed=true: any intersection is enough."""
        from src.core.tools.products import should_include_product_for_property_list

        product = _make_mock_product(
            "prod_partial",
            publisher_properties=[_make_selector_by_id(["prop_a", "prop_b", "prop_c"])],
            property_targeting_allowed=True,
        )
        allowed = {"prop_a"}  # Only one overlaps
        assert should_include_product_for_property_list(product, allowed) is True

    def test_targeting_not_allowed_partial_overlap_excluded(self):
        """property_targeting_allowed=false: ALL product properties must be in allowed set."""
        from src.core.tools.products import should_include_product_for_property_list

        product = _make_mock_product(
            "prod_partial_strict",
            publisher_properties=[_make_selector_by_id(["prop_a", "prop_b"])],
            property_targeting_allowed=False,
        )
        allowed = {"prop_a", "prop_c"}  # prop_b not in allowed
        assert should_include_product_for_property_list(product, allowed) is False

    def test_targeting_not_allowed_full_subset_included(self):
        """property_targeting_allowed=false: include if all product props are in allowed set."""
        from src.core.tools.products import should_include_product_for_property_list

        product = _make_mock_product(
            "prod_full_subset",
            publisher_properties=[_make_selector_by_id(["prop_a", "prop_b"])],
            property_targeting_allowed=False,
        )
        allowed = {"prop_a", "prop_b", "prop_c"}  # Both product props are in allowed
        assert should_include_product_for_property_list(product, allowed) is True

    def test_targeting_not_allowed_exact_match_included(self):
        """property_targeting_allowed=false: exact match is a subset."""
        from src.core.tools.products import should_include_product_for_property_list

        product = _make_mock_product(
            "prod_exact",
            publisher_properties=[_make_selector_by_id(["prop_a"])],
            property_targeting_allowed=False,
        )
        allowed = {"prop_a"}
        assert should_include_product_for_property_list(product, allowed) is True

    def test_empty_product_properties_excluded(self):
        """Products with no property IDs (empty selectors) should be excluded."""
        from src.core.tools.products import should_include_product_for_property_list

        product = _make_mock_product(
            "prod_empty",
            publisher_properties=[],
            property_targeting_allowed=True,
        )
        allowed = {"prop_a"}
        assert should_include_product_for_property_list(product, allowed) is False

    def test_targeting_allowed_default_false(self):
        """property_targeting_allowed defaults to False (strict matching)."""
        from src.core.tools.products import should_include_product_for_property_list

        product = _make_mock_product(
            "prod_default",
            publisher_properties=[_make_selector_by_id(["prop_a", "prop_b"])],
            property_targeting_allowed=False,  # default
        )
        allowed = {"prop_a"}  # Only one overlaps - not enough for strict
        assert should_include_product_for_property_list(product, allowed) is False


class TestFilterProductsByPropertyList:
    """Test the complete filter function that processes a list of products."""

    def test_filters_correctly(self):
        from src.core.tools.products import filter_products_by_property_list

        products = [
            _make_mock_product(
                "all_props",
                publisher_properties=[_make_selector_all()],
            ),
            _make_mock_product(
                "matching",
                publisher_properties=[_make_selector_by_id(["prop_a"])],
                property_targeting_allowed=False,
            ),
            _make_mock_product(
                "no_match",
                publisher_properties=[_make_selector_by_id(["prop_x"])],
            ),
            _make_mock_product(
                "partial_targeting_allowed",
                publisher_properties=[_make_selector_by_id(["prop_a", "prop_b"])],
                property_targeting_allowed=True,
            ),
            _make_mock_product(
                "partial_targeting_not_allowed",
                publisher_properties=[_make_selector_by_id(["prop_a", "prop_b"])],
                property_targeting_allowed=False,
            ),
        ]
        allowed = {"prop_a"}

        result = filter_products_by_property_list(products, allowed)
        result_ids = [p.product_id for p in result]

        assert "all_props" in result_ids
        assert "matching" in result_ids
        assert "partial_targeting_allowed" in result_ids
        assert "no_match" not in result_ids
        assert "partial_targeting_not_allowed" not in result_ids

    def test_empty_allowed_excludes_all_by_id(self):
        """Empty allowed set excludes all by_id products but not 'all' products."""
        from src.core.tools.products import filter_products_by_property_list

        products = [
            _make_mock_product(
                "all_props",
                publisher_properties=[_make_selector_all()],
            ),
            _make_mock_product(
                "by_id",
                publisher_properties=[_make_selector_by_id(["prop_a"])],
            ),
        ]
        allowed: set[str] = set()

        result = filter_products_by_property_list(products, allowed)
        result_ids = [p.product_id for p in result]

        assert "all_props" in result_ids
        assert "by_id" not in result_ids

    def test_empty_products_list(self):
        from src.core.tools.products import filter_products_by_property_list

        result = filter_products_by_property_list([], {"prop_a"})
        assert result == []


class TestCreateGetProductsRequestWithPropertyList:
    """Test that create_get_products_request forwards property_list."""

    def test_property_list_forwarded(self):
        from adcp.types import PropertyListReference

        from src.core.schema_helpers import create_get_products_request

        ref = PropertyListReference(
            agent_url="https://example.com",
            list_id="list_1",
            auth_token="token_123",
        )
        req = create_get_products_request(
            brief="test",
            property_list=ref,
        )
        assert req.property_list is not None
        assert req.property_list.list_id == "list_1"

    def test_property_list_none_by_default(self):
        from src.core.schema_helpers import create_get_products_request

        req = create_get_products_request(brief="test")
        assert req.property_list is None


class TestCapabilitiesPropertyListFiltering:
    """Test that capabilities reports property_list_filtering=True."""

    def test_capabilities_reports_property_list_filtering(self):
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test_tenant",
            tenant={
                "tenant_id": "test_tenant",
                "name": "Test Tenant",
                "subdomain": "test",
            },
        )

        mock_repo = MagicMock()
        mock_repo.list_publisher_partners.return_value = []
        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow.tenant_config = mock_repo

        with (
            patch("src.core.tools.capabilities.get_principal_object", return_value=None),
            patch("src.core.tools.capabilities.TenantConfigUoW", return_value=mock_uow),
        ):
            response = _get_adcp_capabilities_impl(None, identity)

        features = response.media_buy.features
        assert features.property_list_filtering is True
