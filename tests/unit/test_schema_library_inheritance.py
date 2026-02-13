#!/usr/bin/env python3
"""
Test that local schemas extend library types when available.

This test prevents the pattern where we duplicate adcp library types
instead of extending them, which leads to type drift and bugs like
the status_filter array issue.

When a library type exists, our local schema should extend it to:
1. Inherit correct field types automatically
2. Get protocol updates when library is upgraded
3. Ensure mypy can catch type mismatches
"""

import inspect

import pytest


class TestSchemaLibraryInheritance:
    """Test that schemas properly extend library types."""

    def test_get_media_buy_delivery_request_extends_library(self):
        """GetMediaBuyDeliveryRequest must extend library type.

        Regression test for: status_filter was str | list[str] | None locally
        but library has MediaBuyStatus | list[MediaBuyStatus] | None.
        """
        from adcp.types import GetMediaBuyDeliveryRequest as LibraryRequest

        from src.core.schemas import GetMediaBuyDeliveryRequest

        assert issubclass(GetMediaBuyDeliveryRequest, LibraryRequest), (
            "GetMediaBuyDeliveryRequest must extend library type to inherit correct field types. "
            "See test_mcp_tool_type_alignment.py for why this matters."
        )

    def test_get_products_response_extends_library(self):
        """GetProductsResponse must extend library type."""
        from adcp.types import GetProductsResponse as LibraryResponse

        from src.core.schemas import GetProductsResponse

        assert issubclass(GetProductsResponse, LibraryResponse), (
            "GetProductsResponse must extend library type to inherit correct field types."
        )

    def test_simple_types_extend_library(self):
        """Simple types with matching fields must extend library types."""
        from adcp.types import AggregatedTotals as LibraryAggregatedTotals
        from adcp.types import DeliveryMeasurement as LibraryDeliveryMeasurement
        from adcp.types import Measurement as LibraryMeasurement
        from adcp.types.generated_poc.media_buy.list_creatives_response import (
            Pagination as LibraryResponsePagination,
        )

        from src.core.schemas import AggregatedTotals, DeliveryMeasurement, Measurement, Pagination

        assert issubclass(Measurement, LibraryMeasurement), "Measurement must extend library type."
        assert issubclass(DeliveryMeasurement, LibraryDeliveryMeasurement), (
            "DeliveryMeasurement must extend library type."
        )
        assert issubclass(AggregatedTotals, LibraryAggregatedTotals), "AggregatedTotals must extend library type."
        # Pagination for list responses uses page-based pagination (limit/offset/total_pages)
        assert issubclass(Pagination, LibraryResponsePagination), "Pagination must extend library response pagination."

    def test_brand_manifest_is_library_type(self):
        """BrandManifest must be the library type directly."""
        from adcp.types import BrandManifest as LibraryBrandManifest

        from src.core.schemas import BrandManifest

        # BrandManifest should be the exact library type (alias, not subclass)
        assert BrandManifest is LibraryBrandManifest, "BrandManifest must be the library type directly."

    def test_property_is_library_type(self):
        """Property must be the library type directly."""
        from adcp.types import Property as LibraryProperty

        from src.core.schemas import Property

        # Property should be the exact library type (alias, not subclass)
        assert Property is LibraryProperty, "Property must be the library type directly."

    def test_property_identifier_is_library_type(self):
        """PropertyIdentifier must be the library property-specific Identifier type.

        V3 Migration: Property class requires property-specific Identifier from
        adcp.types.generated_poc.core.property.Identifier, not generic Identifier.
        """
        from adcp.types.generated_poc.core.property import Identifier as PropertySpecificIdentifier

        from src.core.schemas import PropertyIdentifier

        # PropertyIdentifier should be the property-specific library type (alias)
        assert PropertyIdentifier is PropertySpecificIdentifier, (
            "PropertyIdentifier must be the property-specific Identifier type."
        )

    def test_document_schemas_not_extending_library(self):
        """Document which schemas exist in library but aren't extended locally.

        This is a documentation test that prints schemas needing migration.
        It doesn't fail, but helps track technical debt.
        """
        import adcp.types as adcp_types

        from src.core import schemas

        # Get all Request/Response classes from our schemas
        local_classes = [
            (name, cls)
            for name, cls in inspect.getmembers(schemas, inspect.isclass)
            if hasattr(cls, "__module__")
            and cls.__module__ == "src.core.schemas"
            and ("Request" in name or "Response" in name)
        ]

        # Get all library Request/Response types
        library_types = {
            name: getattr(adcp_types, name)
            for name in dir(adcp_types)
            if ("Request" in name or "Response" in name) and inspect.isclass(getattr(adcp_types, name))
        }

        # Find local classes that have library equivalents but don't extend them
        not_extended = []
        properly_extended = []

        for name, cls in local_classes:
            if name in library_types:
                lib_cls = library_types[name]
                if issubclass(cls, lib_cls):
                    properly_extended.append(name)
                else:
                    not_extended.append(name)

        print("\n" + "=" * 80)
        print("SCHEMA LIBRARY INHERITANCE REPORT")
        print("=" * 80)

        print(f"\n✅ Properly extending library ({len(properly_extended)}):")
        for name in sorted(properly_extended):
            print(f"  {name}")

        print(f"\n⚠️  Need migration - exists in library but not extended ({len(not_extended)}):")
        for name in sorted(not_extended):
            print(f"  {name}")

        # This test documents but doesn't fail - migration is tracked in GitHub issue
        # Once all are migrated, change this to assert not_extended == []

    def test_new_schemas_must_extend_library_when_available(self):
        """Prevent adding new schemas that duplicate library types.

        This test will FAIL if someone adds a new schema that:
        1. Has a matching name in the library
        2. Doesn't extend the library type

        Existing schemas that need migration are excluded.
        """
        import adcp.types as adcp_types

        from src.core import schemas

        # Known schemas that need migration (tracked in GitHub issue #824)
        # Remove from this list as they get migrated
        #
        # Note: Some schemas have nested type incompatibilities with library types:
        # - GetSignalsRequest/Response: Library DeliverTo requires 'deployments', local has 'platforms'
        # - ListAuthorizedPropertiesResponse: Library uses list[PublisherDomain], local uses list[str]
        # - ListCreativesResponse: Library Pagination/QuerySummary/Creative types differ
        # These require upstream coordination or adapter patterns to migrate.
        KNOWN_NOT_EXTENDED = {
            # Core types
            "CreateMediaBuyRequest",
            "GetProductsRequest",
            "UpdateMediaBuyRequest",
            "ListCreativesRequest",
            "ListCreativesResponse",  # Nested type incompatibilities
            "SyncCreativesRequest",
            "SyncCreativesResponse",
            # Signal types - nested DeliverTo type incompatibility
            "ActivateSignalRequest",
            "ActivateSignalResponse",
            "GetSignalsRequest",
            "GetSignalsResponse",
            # Property types - nested PublisherDomain type incompatibility
            "ListAuthorizedPropertiesRequest",
            "ListAuthorizedPropertiesResponse",
            # Response types (may need custom serialization)
            "GetMediaBuyDeliveryResponse",
        }

        # Get all Request/Response classes from our schemas
        local_classes = [
            (name, cls)
            for name, cls in inspect.getmembers(schemas, inspect.isclass)
            if hasattr(cls, "__module__")
            and cls.__module__ == "src.core.schemas"
            and ("Request" in name or "Response" in name)
        ]

        # Get all library Request/Response types
        library_types = {
            name: getattr(adcp_types, name)
            for name in dir(adcp_types)
            if ("Request" in name or "Response" in name) and inspect.isclass(getattr(adcp_types, name))
        }

        # Check for NEW schemas that should extend library but don't
        new_violations = []
        for name, cls in local_classes:
            if name in library_types and name not in KNOWN_NOT_EXTENDED:
                lib_cls = library_types[name]
                if not issubclass(cls, lib_cls):
                    new_violations.append(name)

        assert not new_violations, (
            f"New schemas must extend library types when available:\n"
            f"  {new_violations}\n\n"
            f"To fix: Change 'class {new_violations[0]}(SalesAgentBaseModel)' to "
            f"'class {new_violations[0]}(Library{new_violations[0]})'\n"
            f"See GetMediaBuyDeliveryRequest for example pattern."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
