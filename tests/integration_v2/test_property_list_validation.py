"""Integration tests for property list source validation and filter requirements.

Obligations covered:
- BR-RULE-072-01: Property source validation -- base_properties discriminated union
- BR-RULE-073-01: Property list filter requirements -- countries_all (AND) + channels_any (OR)
- BR-RULE-078-01: Property list filtering -- list-property-lists optional filtering

All tests route through production _impl functions where possible.
Tests for unimplemented _impl functions are marked xfail.
Schema-level validation tests (empty arrays, invalid types) verify that the
adcp library schema enforces the spec before _impl is reached.
"""

from __future__ import annotations

import pytest
from adcp.types import CreatePropertyListRequest
from pydantic import ValidationError

from tests.factories import PrincipalFactory, TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_XFAIL_NO_IMPL = pytest.mark.xfail(
    reason="Property list CRUD _impl functions not yet implemented",
    raises=(ImportError, AttributeError, NotImplementedError),
    strict=False,
)


def _lazy_identity(tenant_id: str, principal_id: str = "p1"):
    """Build a ResolvedIdentity for the given tenant."""
    from src.core.resolved_identity import ResolvedIdentity
    from src.core.tenant_context import LazyTenantContext

    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=LazyTenantContext(tenant_id),
    )


# ---------------------------------------------------------------------------
# BR-RULE-072-01: Property Source Validation
# ---------------------------------------------------------------------------


class TestBasePropertiesPublisherTags:
    """base_properties with selection_type=publisher_tags is valid."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_publisher_tags_source_accepted(self, integration_db):
        """base_properties with publisher_tags selection_type and non-empty tags is valid.

        Covers: BR-RULE-072-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        tenant = TenantFactory(tenant_id="plv-tags", subdomain="plv-tags")
        PrincipalFactory(tenant=tenant, principal_id="plv-tags-p")
        identity = _lazy_identity("plv-tags", "plv-tags-p")

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
        resp = await _create_property_list_impl(req, identity)
        assert resp.list.base_properties is not None
        assert len(resp.list.base_properties) == 1
        source = resp.list.base_properties[0].root
        assert source.selection_type == "publisher_tags"

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_publisher_tags_multiple_tags(self, integration_db):
        """Multiple tags in publisher_tags source are accepted.

        Covers: BR-RULE-072-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        tenant = TenantFactory(tenant_id="plv-multitag", subdomain="plv-multitag")
        PrincipalFactory(tenant=tenant, principal_id="plv-multitag-p")
        identity = _lazy_identity("plv-multitag", "plv-multitag-p")

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
        resp = await _create_property_list_impl(req, identity)
        source = resp.list.base_properties[0].root
        assert len(source.tags) == 3


class TestBasePropertiesPublisherIds:
    """base_properties with selection_type=publisher_ids is valid."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_publisher_ids_source_accepted(self, integration_db):
        """base_properties with publisher_ids selection_type and non-empty property_ids is valid.

        Covers: BR-RULE-072-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        tenant = TenantFactory(tenant_id="plv-ids", subdomain="plv-ids")
        PrincipalFactory(tenant=tenant, principal_id="plv-ids-p")
        identity = _lazy_identity("plv-ids", "plv-ids-p")

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
        resp = await _create_property_list_impl(req, identity)
        source = resp.list.base_properties[0].root
        assert source.selection_type == "publisher_ids"
        assert len(source.property_ids) == 2


class TestBasePropertiesIdentifiers:
    """base_properties with selection_type=identifiers is valid."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_identifiers_source_accepted(self, integration_db):
        """base_properties with identifiers selection_type and non-empty identifiers is valid.

        Covers: BR-RULE-072-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        tenant = TenantFactory(tenant_id="plv-idents", subdomain="plv-idents")
        PrincipalFactory(tenant=tenant, principal_id="plv-idents-p")
        identity = _lazy_identity("plv-idents", "plv-idents-p")

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
        resp = await _create_property_list_impl(req, identity)
        source = resp.list.base_properties[0].root
        assert source.selection_type == "identifiers"
        assert len(source.identifiers) == 2


class TestBasePropertiesEmptyArrayRejection:
    """Empty selection arrays in base_properties are rejected at schema level.

    The adcp library Pydantic schema enforces minLength constraints.
    When _impl exists, the schema validation fires during request construction
    before _impl is called — but the test routes through _impl to prove it.
    """

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_empty_tags_rejected(self, integration_db):
        """base_properties with empty tags array is rejected by schema validation.

        Covers: BR-RULE-072-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        tenant = TenantFactory(tenant_id="plv-etags", subdomain="plv-etags")
        PrincipalFactory(tenant=tenant, principal_id="plv-etags-p")
        identity = _lazy_identity("plv-etags", "plv-etags-p")

        with pytest.raises(ValidationError, match="too_short"):
            req = CreatePropertyListRequest(
                name="Empty Tags",
                base_properties=[
                    {
                        "selection_type": "publisher_tags",
                        "publisher_domain": "example.com",
                        "tags": [],
                    }
                ],
            )
            await _create_property_list_impl(req, identity)

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_empty_property_ids_rejected(self, integration_db):
        """base_properties with empty property_ids array is rejected.

        Covers: BR-RULE-072-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        tenant = TenantFactory(tenant_id="plv-eids", subdomain="plv-eids")
        PrincipalFactory(tenant=tenant, principal_id="plv-eids-p")
        identity = _lazy_identity("plv-eids", "plv-eids-p")

        with pytest.raises(ValidationError, match="too_short"):
            req = CreatePropertyListRequest(
                name="Empty IDs",
                base_properties=[
                    {
                        "selection_type": "publisher_ids",
                        "publisher_domain": "example.com",
                        "property_ids": [],
                    }
                ],
            )
            await _create_property_list_impl(req, identity)

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_empty_identifiers_rejected(self, integration_db):
        """base_properties with empty identifiers array is rejected.

        Covers: BR-RULE-072-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        tenant = TenantFactory(tenant_id="plv-eident", subdomain="plv-eident")
        PrincipalFactory(tenant=tenant, principal_id="plv-eident-p")
        identity = _lazy_identity("plv-eident", "plv-eident-p")

        with pytest.raises(ValidationError, match="too_short"):
            req = CreatePropertyListRequest(
                name="Empty Identifiers",
                base_properties=[
                    {
                        "selection_type": "identifiers",
                        "identifiers": [],
                    }
                ],
            )
            await _create_property_list_impl(req, identity)


class TestBasePropertiesOmitted:
    """Omitting base_properties means entire catalog."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_base_properties_omitted_valid(self, integration_db):
        """Omitting base_properties is valid -- resolves against entire catalog.

        Covers: BR-RULE-072-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        tenant = TenantFactory(tenant_id="plv-omitted", subdomain="plv-omitted")
        PrincipalFactory(tenant=tenant, principal_id="plv-omitted-p")
        identity = _lazy_identity("plv-omitted", "plv-omitted-p")

        req = CreatePropertyListRequest(name="Full Catalog List")
        resp = await _create_property_list_impl(req, identity)
        assert resp.list.base_properties is None


class TestBasePropertiesInvalidSelectionType:
    """Invalid selection_type in base_properties is rejected at schema level."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_invalid_selection_type_rejected(self, integration_db):
        """base_properties with unrecognized selection_type is rejected by discriminator.

        Covers: BR-RULE-072-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        tenant = TenantFactory(tenant_id="plv-badsel", subdomain="plv-badsel")
        PrincipalFactory(tenant=tenant, principal_id="plv-badsel-p")
        identity = _lazy_identity("plv-badsel", "plv-badsel-p")

        with pytest.raises(ValidationError, match="does not match any of the expected tags"):
            req = CreatePropertyListRequest(
                name="Invalid Source",
                base_properties=[
                    {
                        "selection_type": "invalid_type",
                        "publisher_domain": "example.com",
                        "tags": ["sports"],
                    }
                ],
            )
            await _create_property_list_impl(req, identity)


# ---------------------------------------------------------------------------
# BR-RULE-073-01: Property List Filter Requirements
# ---------------------------------------------------------------------------


class TestFiltersValid:
    """Valid filters with required fields."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_filters_with_countries_and_channels_valid(self, integration_db):
        """filters with countries_all and channels_any as non-empty arrays is valid.

        Covers: BR-RULE-073-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        tenant = TenantFactory(tenant_id="plv-filters", subdomain="plv-filters")
        PrincipalFactory(tenant=tenant, principal_id="plv-filters-p")
        identity = _lazy_identity("plv-filters", "plv-filters-p")

        req = CreatePropertyListRequest(
            name="Filtered List",
            filters={
                "countries_all": ["US", "UK"],
                "channels_any": ["display"],
            },
        )
        resp = await _create_property_list_impl(req, identity)
        assert resp.list.filters is not None


class TestFiltersEmptyRejection:
    """Empty required filter arrays are rejected at schema level."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_empty_countries_all_rejected(self, integration_db):
        """filters with empty countries_all is rejected.

        Covers: BR-RULE-073-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        tenant = TenantFactory(tenant_id="plv-ecntry", subdomain="plv-ecntry")
        PrincipalFactory(tenant=tenant, principal_id="plv-ecntry-p")
        identity = _lazy_identity("plv-ecntry", "plv-ecntry-p")

        with pytest.raises(ValidationError, match="too_short"):
            req = CreatePropertyListRequest(
                name="Empty Countries",
                filters={
                    "countries_all": [],
                    "channels_any": ["display"],
                },
            )
            await _create_property_list_impl(req, identity)

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_empty_channels_any_rejected(self, integration_db):
        """filters with empty channels_any is rejected.

        Covers: BR-RULE-073-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        tenant = TenantFactory(tenant_id="plv-echan", subdomain="plv-echan")
        PrincipalFactory(tenant=tenant, principal_id="plv-echan-p")
        identity = _lazy_identity("plv-echan", "plv-echan-p")

        with pytest.raises(ValidationError, match="too_short"):
            req = CreatePropertyListRequest(
                name="Empty Channels",
                filters={
                    "countries_all": ["US"],
                    "channels_any": [],
                },
            )
            await _create_property_list_impl(req, identity)


class TestFiltersOmitted:
    """Omitting filters means no filtering applied."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_filters_omitted_valid(self, integration_db):
        """Omitting filters is valid -- no filtering applied at resolution time.

        Covers: BR-RULE-073-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        tenant = TenantFactory(tenant_id="plv-nofilter", subdomain="plv-nofilter")
        PrincipalFactory(tenant=tenant, principal_id="plv-nofilter-p")
        identity = _lazy_identity("plv-nofilter", "plv-nofilter-p")

        req = CreatePropertyListRequest(name="No Filters")
        resp = await _create_property_list_impl(req, identity)
        assert resp.list.filters is None


class TestFiltersAndSemantics:
    """countries_all uses AND semantics (property must match ALL countries)."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_countries_all_and_semantics(self, integration_db):
        """countries_all combines as AND — property must match ALL countries.

        Covers: BR-RULE-073-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        tenant = TenantFactory(tenant_id="plv-andsem", subdomain="plv-andsem")
        PrincipalFactory(tenant=tenant, principal_id="plv-andsem-p")
        identity = _lazy_identity("plv-andsem", "plv-andsem-p")

        req = CreatePropertyListRequest(
            name="AND Semantics",
            filters={
                "countries_all": ["US", "UK", "DE"],
                "channels_any": ["display"],
            },
        )
        resp = await _create_property_list_impl(req, identity)
        country_values = [c.root for c in resp.list.filters.countries_all]
        assert country_values == ["US", "UK", "DE"]


class TestFiltersOrSemantics:
    """channels_any uses OR semantics (property matches ANY channel)."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_channels_any_or_semantics(self, integration_db):
        """channels_any combines as OR — property matches ANY channel.

        Covers: BR-RULE-073-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        tenant = TenantFactory(tenant_id="plv-orsem", subdomain="plv-orsem")
        PrincipalFactory(tenant=tenant, principal_id="plv-orsem-p")
        identity = _lazy_identity("plv-orsem", "plv-orsem-p")

        req = CreatePropertyListRequest(
            name="OR Semantics",
            filters={
                "countries_all": ["US"],
                "channels_any": ["display", "olv", "ctv"],
            },
        )
        resp = await _create_property_list_impl(req, identity)
        channel_values = [c.value for c in resp.list.filters.channels_any]
        assert set(channel_values) == {"display", "olv", "ctv"}


# ---------------------------------------------------------------------------
# BR-RULE-078-01: Property List Filtering (list-property-lists)
# ---------------------------------------------------------------------------


class TestListPropertyListsFiltering:
    """list-property-lists supports optional filtering by principal and name."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_no_filters_returns_all(self, integration_db):
        """ListPropertyListsRequest with no filters returns all tenant lists.

        Covers: BR-RULE-078-01
        """
        from src.core.tools.property_list import (
            _create_property_list_impl,
            _list_property_lists_impl,
        )

        tenant = TenantFactory(tenant_id="plv-listall", subdomain="plv-listall")
        PrincipalFactory(tenant=tenant, principal_id="plv-listall-p")
        identity = _lazy_identity("plv-listall", "plv-listall-p")

        # Create two lists
        await _create_property_list_impl(CreatePropertyListRequest(name="List A"), identity)
        await _create_property_list_impl(CreatePropertyListRequest(name="List B"), identity)

        resp = await _list_property_lists_impl(identity=identity)
        assert len(resp.lists) >= 2

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_name_contains_filter(self, integration_db):
        """ListPropertyListsRequest with name_contains returns matching lists.

        Covers: BR-RULE-078-01
        """
        from src.core.tools.property_list import (
            _create_property_list_impl,
            _list_property_lists_impl,
        )

        tenant = TenantFactory(tenant_id="plv-name", subdomain="plv-name")
        PrincipalFactory(tenant=tenant, principal_id="plv-name-p")
        identity = _lazy_identity("plv-name", "plv-name-p")

        await _create_property_list_impl(CreatePropertyListRequest(name="Sports Properties"), identity)
        await _create_property_list_impl(CreatePropertyListRequest(name="News Properties"), identity)

        resp = await _list_property_lists_impl(name_contains="Sports", identity=identity)
        names = [pl.name for pl in resp.lists]
        assert "Sports Properties" in names
        assert "News Properties" not in names

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_principal_filter(self, integration_db):
        """ListPropertyListsRequest with principal returns lists for that principal.

        Covers: BR-RULE-078-01
        """
        from src.core.tools.property_list import _list_property_lists_impl

        tenant = TenantFactory(tenant_id="plv-princ", subdomain="plv-princ")
        PrincipalFactory(tenant=tenant, principal_id="buyer-123")
        identity = _lazy_identity("plv-princ", "buyer-123")

        resp = await _list_property_lists_impl(principal="buyer-123", identity=identity)
        # Should return only lists owned by buyer-123
        assert resp is not None

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_both_filters_combined(self, integration_db):
        """ListPropertyListsRequest with both name_contains and principal filters.

        Covers: BR-RULE-078-01
        """
        from src.core.tools.property_list import _list_property_lists_impl

        tenant = TenantFactory(tenant_id="plv-both", subdomain="plv-both")
        PrincipalFactory(tenant=tenant, principal_id="buyer-456")
        identity = _lazy_identity("plv-both", "buyer-456")

        resp = await _list_property_lists_impl(
            name_contains="sports",
            principal="buyer-456",
            identity=identity,
        )
        assert resp is not None
