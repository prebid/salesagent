"""Integration tests for property list source validation and filter requirements.

Obligations covered:
- BR-RULE-072-01: Property source validation -- base_properties discriminated union
- BR-RULE-073-01: Property list filter requirements -- countries_all (AND) + channels_any (OR)
- BR-RULE-078-01: Property list filtering -- list-property-lists optional filtering
"""

import pytest
from adcp.types import (
    CreatePropertyListRequest,
    ListPropertyListsRequest,
    PropertyListFilters,
)
from pydantic import ValidationError

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# BR-RULE-072-01: Property Source Validation
# ---------------------------------------------------------------------------


class TestBasePropertiesPublisherTags:
    """base_properties with selection_type=publisher_tags is valid."""

    def test_publisher_tags_source_accepted(self, integration_db):
        """base_properties with publisher_tags selection_type and non-empty tags is valid.

        Covers: BR-RULE-072-01
        """
        req = CreatePropertyListRequest(
            name="Sports Properties",
            base_properties=[
                {
                    "selection_type": "publisher_tags",
                    "publisher_domain": "example.com",
                    "tags": ["sports"],
                }
            ],
        )
        assert req.base_properties is not None
        assert len(req.base_properties) == 1
        source = req.base_properties[0].root
        assert source.selection_type == "publisher_tags"
        assert source.publisher_domain == "example.com"
        assert len(source.tags) == 1

    def test_publisher_tags_multiple_tags(self, integration_db):
        """Multiple tags in publisher_tags source are accepted.

        Covers: BR-RULE-072-01
        """
        req = CreatePropertyListRequest(
            name="Multi-Tag Properties",
            base_properties=[
                {
                    "selection_type": "publisher_tags",
                    "publisher_domain": "news.com",
                    "tags": ["sports", "entertainment", "politics"],
                }
            ],
        )
        source = req.base_properties[0].root
        assert len(source.tags) == 3


class TestBasePropertiesPublisherIds:
    """base_properties with selection_type=publisher_ids is valid."""

    def test_publisher_ids_source_accepted(self, integration_db):
        """base_properties with publisher_ids selection_type and non-empty property_ids is valid.

        Covers: BR-RULE-072-01
        """
        req = CreatePropertyListRequest(
            name="Specific Properties",
            base_properties=[
                {
                    "selection_type": "publisher_ids",
                    "publisher_domain": "example.com",
                    "property_ids": ["prop_001", "prop_002"],
                }
            ],
        )
        assert req.base_properties is not None
        source = req.base_properties[0].root
        assert source.selection_type == "publisher_ids"
        assert source.publisher_domain == "example.com"
        assert len(source.property_ids) == 2


class TestBasePropertiesIdentifiers:
    """base_properties with selection_type=identifiers is valid."""

    def test_identifiers_source_accepted(self, integration_db):
        """base_properties with identifiers selection_type and non-empty identifiers is valid.

        Covers: BR-RULE-072-01
        """
        req = CreatePropertyListRequest(
            name="Domain Properties",
            base_properties=[
                {
                    "selection_type": "identifiers",
                    "identifiers": [
                        {"type": "domain", "value": "example.com"},
                        {"type": "domain", "value": "news.org"},
                    ],
                }
            ],
        )
        assert req.base_properties is not None
        source = req.base_properties[0].root
        assert source.selection_type == "identifiers"
        assert len(source.identifiers) == 2


class TestBasePropertiesEmptyArrayRejection:
    """Empty selection arrays in base_properties are rejected."""

    def test_empty_tags_rejected(self, integration_db):
        """base_properties with empty tags array is rejected by schema validation.

        Covers: BR-RULE-072-01
        """
        with pytest.raises(ValidationError, match="too_short"):
            CreatePropertyListRequest(
                name="Empty Tags",
                base_properties=[
                    {
                        "selection_type": "publisher_tags",
                        "publisher_domain": "example.com",
                        "tags": [],
                    }
                ],
            )

    def test_empty_property_ids_rejected(self, integration_db):
        """base_properties with empty property_ids array is rejected.

        Covers: BR-RULE-072-01
        """
        with pytest.raises(ValidationError, match="too_short"):
            CreatePropertyListRequest(
                name="Empty IDs",
                base_properties=[
                    {
                        "selection_type": "publisher_ids",
                        "publisher_domain": "example.com",
                        "property_ids": [],
                    }
                ],
            )

    def test_empty_identifiers_rejected(self, integration_db):
        """base_properties with empty identifiers array is rejected.

        Covers: BR-RULE-072-01
        """
        with pytest.raises(ValidationError, match="too_short"):
            CreatePropertyListRequest(
                name="Empty Identifiers",
                base_properties=[
                    {
                        "selection_type": "identifiers",
                        "identifiers": [],
                    }
                ],
            )


class TestBasePropertiesOmitted:
    """Omitting base_properties means entire catalog."""

    def test_base_properties_omitted_valid(self, integration_db):
        """Omitting base_properties is valid -- resolves against entire catalog.

        Covers: BR-RULE-072-01
        """
        req = CreatePropertyListRequest(name="Full Catalog List")
        assert req.base_properties is None


class TestBasePropertiesInvalidSelectionType:
    """Invalid selection_type in base_properties is rejected."""

    def test_invalid_selection_type_rejected(self, integration_db):
        """base_properties with unrecognized selection_type is rejected by discriminator.

        Covers: BR-RULE-072-01
        """
        with pytest.raises(ValidationError, match="does not match any of the expected tags"):
            CreatePropertyListRequest(
                name="Invalid Source",
                base_properties=[
                    {
                        "selection_type": "invalid_type",
                        "publisher_domain": "example.com",
                        "tags": ["sports"],
                    }
                ],
            )


# ---------------------------------------------------------------------------
# BR-RULE-073-01: Property List Filter Requirements
# ---------------------------------------------------------------------------


class TestFiltersValid:
    """Valid filters with required fields."""

    def test_filters_with_countries_and_channels_valid(self, integration_db):
        """filters with countries_all and channels_any as non-empty arrays is valid.

        Covers: BR-RULE-073-01
        """
        req = CreatePropertyListRequest(
            name="Filtered List",
            filters={
                "countries_all": ["US", "UK"],
                "channels_any": ["display"],
            },
        )
        assert req.filters is not None
        assert len(req.filters.countries_all) == 2
        assert len(req.filters.channels_any) == 1


class TestFiltersEmptyRejection:
    """Empty required filter arrays are rejected."""

    def test_empty_countries_all_rejected(self, integration_db):
        """filters with empty countries_all is rejected.

        Covers: BR-RULE-073-01
        """
        with pytest.raises(ValidationError, match="too_short"):
            PropertyListFilters(
                countries_all=[],
                channels_any=["display"],
            )

    def test_empty_channels_any_rejected(self, integration_db):
        """filters with empty channels_any is rejected.

        Covers: BR-RULE-073-01
        """
        with pytest.raises(ValidationError, match="too_short"):
            PropertyListFilters(
                countries_all=["US"],
                channels_any=[],
            )


class TestFiltersOmitted:
    """Omitting filters means no filtering applied."""

    def test_filters_omitted_valid(self, integration_db):
        """Omitting filters is valid -- no filtering applied at resolution time.

        Covers: BR-RULE-073-01
        """
        req = CreatePropertyListRequest(name="No Filters")
        assert req.filters is None


class TestFiltersAndSemantics:
    """countries_all uses AND semantics (property must match ALL countries)."""

    def test_countries_all_and_semantics(self, integration_db):
        """countries_all combines as AND -- all country values are preserved in the model.

        Covers: BR-RULE-073-01
        """
        filters = PropertyListFilters(
            countries_all=["US", "UK", "DE"],
            channels_any=["display"],
        )
        country_values = [c.root for c in filters.countries_all]
        assert country_values == ["US", "UK", "DE"]
        # AND semantics: property must match ALL countries (US AND UK AND DE)
        assert len(filters.countries_all) == 3


class TestFiltersOrSemantics:
    """channels_any uses OR semantics (property matches ANY channel)."""

    def test_channels_any_or_semantics(self, integration_db):
        """channels_any combines as OR -- all channel values are preserved in the model.

        Covers: BR-RULE-073-01
        """
        filters = PropertyListFilters(
            countries_all=["US"],
            channels_any=["display", "olv", "ctv"],
        )
        channel_values = [c.value for c in filters.channels_any]
        assert set(channel_values) == {"display", "olv", "ctv"}
        # OR semantics: property matches ANY channel (display OR olv OR ctv)
        assert len(filters.channels_any) == 3


# ---------------------------------------------------------------------------
# BR-RULE-078-01: Property List Filtering (list-property-lists)
# ---------------------------------------------------------------------------


class TestListPropertyListsFiltering:
    """list-property-lists supports optional filtering by principal and name."""

    def test_no_filters_returns_all(self, integration_db):
        """ListPropertyListsRequest with no filters is valid -- returns all tenant lists.

        Covers: BR-RULE-078-01
        """
        req = ListPropertyListsRequest()
        assert req.name_contains is None
        assert req.principal is None

    def test_name_contains_filter(self, integration_db):
        """ListPropertyListsRequest accepts name_contains for substring filtering.

        Covers: BR-RULE-078-01
        """
        req = ListPropertyListsRequest(name_contains="sports")
        assert req.name_contains == "sports"
        assert req.principal is None

    def test_principal_filter(self, integration_db):
        """ListPropertyListsRequest accepts principal for exact match filtering.

        Covers: BR-RULE-078-01
        """
        req = ListPropertyListsRequest(principal="buyer-123")
        assert req.principal == "buyer-123"
        assert req.name_contains is None

    def test_both_filters_combined(self, integration_db):
        """ListPropertyListsRequest accepts both name_contains and principal simultaneously.

        Covers: BR-RULE-078-01
        """
        req = ListPropertyListsRequest(
            name_contains="sports",
            principal="buyer-123",
        )
        assert req.name_contains == "sports"
        assert req.principal == "buyer-123"
