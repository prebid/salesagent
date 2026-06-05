"""Unit pins for property_list-related get_products surfaces.

The legacy per-product filter functions (extract/should_include/filter) were
removed in favor of ``src.services.property_intersection.PropertyIntersection``;
their behavioral coverage now lives in ``tests/unit/test_property_intersection.py``
and ``tests/integration/test_product_property_list_filtering.py``. This file
retains the get_products request-forwarding and capability-honesty pins.
"""

from unittest.mock import MagicMock, patch


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
    """capabilities reports property_list_filtering per-adapter (not hardcoded).

    ``_get_adcp_capabilities_impl`` derives the flag from
    ``supports_property_list_targeting(adapter)`` (capabilities.py). This test
    patches ``get_principal_object`` to None, so no adapter is resolved and the
    flag is False — the honest answer when the caller has no bound adapter.
    (Kevel, which compiles property_list to siteIds, reports True for
    Kevel-bound tenants; that capability is pinned in
    test_kevel_property_list_compilation.py.)
    """

    def test_capabilities_reports_property_list_filtering(self):
        from src.core.tools.capabilities import _get_adcp_capabilities_impl
        from tests.factories import PrincipalFactory

        identity = PrincipalFactory.make_identity(
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
        assert features.property_list_filtering is False
