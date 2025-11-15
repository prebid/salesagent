"""Unit tests for AXE segment targeting (axe_include_segment, axe_exclude_segment).

Tests that the AdCP 3.0.3 AXE segment fields are properly supported in
targeting_overlay for create_media_buy and update_media_buy operations.
"""

from datetime import UTC

from src.core.schemas import CreateMediaBuyRequest, Package, Targeting, UpdateMediaBuyRequest


def test_targeting_has_axe_segment_fields():
    """Test that Targeting class includes axe_include_segment and axe_exclude_segment fields."""
    targeting = Targeting(
        geo_country_any_of=["US"],
        axe_include_segment="x8dj3k",
        axe_exclude_segment="y9kl4m",
    )

    # Verify fields are present
    assert targeting.axe_include_segment == "x8dj3k"
    assert targeting.axe_exclude_segment == "y9kl4m"

    # Verify serialization includes these fields
    data = targeting.model_dump()
    assert data["axe_include_segment"] == "x8dj3k"
    assert data["axe_exclude_segment"] == "y9kl4m"


def test_targeting_axe_segments_are_optional():
    """Test that AXE segment fields are optional."""
    targeting = Targeting(geo_country_any_of=["US"])

    # Should not raise validation error
    assert targeting.axe_include_segment is None
    assert targeting.axe_exclude_segment is None

    # Verify serialization excludes None values
    data = targeting.model_dump(exclude_none=True)
    assert "axe_include_segment" not in data
    assert "axe_exclude_segment" not in data


def test_package_targeting_overlay_supports_axe_segments():
    """Test that Package.targeting_overlay supports AXE segment targeting."""
    package = Package(
        package_id="pkg_123",  # Required for serialization
        status="active",  # Required for serialization
        product_id="prod_123",
        targeting_overlay=Targeting(
            geo_country_any_of=["US"],
            axe_include_segment="x8dj3k",
        ),
    )

    # Verify targeting overlay is present
    assert package.targeting_overlay is not None
    assert package.targeting_overlay.axe_include_segment == "x8dj3k"

    # Verify serialization
    data = package.model_dump()
    assert data["targeting_overlay"]["axe_include_segment"] == "x8dj3k"


def test_create_media_buy_request_with_axe_segments():
    """Test that CreateMediaBuyRequest supports AXE segment targeting in packages."""
    from datetime import datetime

    request = CreateMediaBuyRequest(
        brand_manifest="https://example.com/manifest.json",
        buyer_ref="buyer_test_001",
        start_time=datetime(2025, 1, 15, 0, 0, 0, tzinfo=UTC),
        end_time=datetime(2025, 2, 15, 23, 59, 59, tzinfo=UTC),
        packages=[
            Package(
                product_id="prod_123",
                targeting_overlay=Targeting(
                    geo_country_any_of=["US"],
                    axe_include_segment="x8dj3k",
                    axe_exclude_segment="y9kl4m",
                ),
            )
        ],
    )

    # Verify request structure
    assert len(request.packages) == 1
    assert request.packages[0].targeting_overlay is not None
    assert request.packages[0].targeting_overlay.axe_include_segment == "x8dj3k"
    assert request.packages[0].targeting_overlay.axe_exclude_segment == "y9kl4m"

    # Verify serialization
    data = request.model_dump()
    targeting = data["packages"][0]["targeting_overlay"]
    assert targeting["axe_include_segment"] == "x8dj3k"
    assert targeting["axe_exclude_segment"] == "y9kl4m"


def test_update_media_buy_request_with_axe_segments():
    """Test that UpdateMediaBuyRequest supports AXE segment targeting in package updates."""
    from src.core.schemas import AdCPPackageUpdate

    request = UpdateMediaBuyRequest(
        media_buy_id="mb_test_001",
        packages=[
            AdCPPackageUpdate(
                package_id="pkg_123",
                targeting_overlay=Targeting(
                    geo_country_any_of=["US", "CA"],
                    axe_include_segment="x8dj3k",
                ),
            )
        ],
    )

    # Verify request structure
    assert len(request.packages) == 1
    assert request.packages[0].targeting_overlay is not None
    assert request.packages[0].targeting_overlay.axe_include_segment == "x8dj3k"
    assert request.packages[0].targeting_overlay.geo_country_any_of == ["US", "CA"]

    # Verify serialization
    data = request.model_dump()
    targeting = data["packages"][0]["targeting_overlay"]
    assert targeting["axe_include_segment"] == "x8dj3k"


def test_axe_segments_survive_roundtrip():
    """Test that AXE segment fields survive serialization/deserialization roundtrip."""
    # Create targeting with AXE segments
    original = Targeting(
        geo_country_any_of=["US"],
        axe_include_segment="x8dj3k",
        axe_exclude_segment="y9kl4m",
    )

    # Serialize to dict
    data = original.model_dump()

    # Deserialize back to Targeting
    reconstructed = Targeting(**data)

    # Verify fields survived
    assert reconstructed.axe_include_segment == "x8dj3k"
    assert reconstructed.axe_exclude_segment == "y9kl4m"
    assert reconstructed.geo_country_any_of == ["US"]


def test_axe_segments_with_other_targeting_dimensions():
    """Test that AXE segments work alongside other targeting dimensions."""
    targeting = Targeting(
        geo_country_any_of=["US"],
        geo_region_any_of=["NY", "CA"],
        device_type_any_of=["mobile", "desktop"],
        axe_include_segment="x8dj3k",
        axe_exclude_segment="y9kl4m",
    )

    # Verify all fields are present
    assert targeting.geo_country_any_of == ["US"]
    assert targeting.geo_region_any_of == ["NY", "CA"]
    assert targeting.device_type_any_of == ["mobile", "desktop"]
    assert targeting.axe_include_segment == "x8dj3k"
    assert targeting.axe_exclude_segment == "y9kl4m"

    # Verify serialization includes all fields
    data = targeting.model_dump(exclude_none=True)
    assert len(data) == 5  # All 5 non-None fields
