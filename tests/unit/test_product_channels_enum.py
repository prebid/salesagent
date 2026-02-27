"""Tests for Product.channels MediaChannel enum alignment.

Validates that Product.channels uses MediaChannel enum type from the adcp library,
is included in serialization output (public field per AdCP spec), and handles
DB string-to-enum conversion correctly.
"""

from adcp.types._generated import MediaChannel

from src.core.schemas import Product
from tests.helpers.adcp_factories import create_test_product


class TestProductChannelsType:
    """Test that Product.channels uses MediaChannel enum."""

    def test_channels_field_uses_media_channel_type(self):
        """Product schema should declare channels as list[MediaChannel], not list[str]."""
        import typing

        field_info = Product.model_fields["channels"]
        # Get the inner type from Optional[list[X]]
        # annotation is list[MediaChannel] | None
        args = typing.get_args(field_info.annotation)
        list_type = args[0]  # list[MediaChannel]
        inner_args = typing.get_args(list_type)
        assert inner_args[0] is MediaChannel, (
            f"Product.channels should be list[MediaChannel], got list[{inner_args[0]}]"
        )

    def test_channels_not_excluded(self):
        """Product.channels should NOT have exclude=True (public field per AdCP spec)."""
        field_info = Product.model_fields["channels"]
        assert field_info.exclude is not True, "Product.channels should be a public field (no exclude=True)"

    def test_channels_accepts_media_channel_enum(self):
        """Product should accept MediaChannel enum values for channels."""
        product = create_test_product(
            channels=[MediaChannel.display, MediaChannel.olv],
        )
        assert product.channels is not None
        assert len(product.channels) == 2
        assert all(isinstance(c, MediaChannel) for c in product.channels)

    def test_channels_coerces_strings_to_enum(self):
        """Product should coerce string values to MediaChannel enum."""
        product = create_test_product(
            channels=["display", "olv"],
        )
        assert product.channels is not None
        assert all(isinstance(c, MediaChannel) for c in product.channels)
        assert product.channels[0] == MediaChannel.display
        assert product.channels[1] == MediaChannel.olv


class TestProductChannelsSerialization:
    """Test that channels appears in serialized output."""

    def test_channels_included_in_model_dump(self):
        """Channels should NOT be excluded from model_dump (public per AdCP spec)."""
        product = create_test_product(
            channels=[MediaChannel.display],
        )
        data = product.model_dump(mode="json")
        assert "channels" in data, "channels should be a public field in AdCP output"
        assert data["channels"] == ["display"]

    def test_channels_omitted_when_none(self):
        """Channels should be omitted from output when None (null-stripping)."""
        product = create_test_product(channels=None)
        data = product.model_dump(mode="json")
        assert "channels" not in data, "None channels should be omitted per AdCP convention"

    def test_channels_serializes_enum_to_strings(self):
        """MediaChannel enum values should serialize to strings in JSON mode."""
        product = create_test_product(
            channels=[MediaChannel.display, MediaChannel.ctv, MediaChannel.podcast],
        )
        data = product.model_dump(mode="json")
        assert data["channels"] == ["display", "ctv", "podcast"]


class TestProductChannelsConversion:
    """Test DB string-to-enum conversion in product_conversion."""

    def _make_db_product(self, **overrides):
        """Create a DB Product model with pricing for conversion tests."""
        from decimal import Decimal

        from src.core.database.models import PricingOption
        from tests.helpers.adcp_factories import create_test_db_product

        product = create_test_db_product(
            tenant_id="channel_test",
            product_id="channel_test_001",
            name="Channel Test Product",
            delivery_type="non_guaranteed",
            delivery_measurement={"provider": "publisher"},
            **overrides,
        )
        pricing = PricingOption(
            tenant_id="channel_test",
            product_id="channel_test_001",
            pricing_model="cpm",
            rate=Decimal("10.0"),
            currency="USD",
            is_fixed=True,
        )
        product.pricing_options = [pricing]
        return product

    def test_db_strings_converted_to_media_channel(self):
        """product_conversion should convert DB string channels to MediaChannel."""
        from src.core.product_conversion import convert_product_model_to_schema

        product_model = self._make_db_product(channels=["display", "olv"])
        product = convert_product_model_to_schema(product_model)
        assert product.channels is not None
        assert all(isinstance(c, MediaChannel) for c in product.channels)
        assert product.channels[0] == MediaChannel.display
        assert product.channels[1] == MediaChannel.olv

    def test_invalid_db_channel_handled_gracefully(self):
        """Invalid channel strings in DB should be skipped, not crash."""
        from src.core.product_conversion import convert_product_model_to_schema

        product_model = self._make_db_product(channels=["display", "invalid_channel_xyz", "olv"])

        # Should not raise -- invalid channels are skipped
        product = convert_product_model_to_schema(product_model)
        assert product.channels is not None
        # Only valid channels should be present
        assert len(product.channels) == 2
        assert MediaChannel.display in product.channels
        assert MediaChannel.olv in product.channels

    def test_none_channels_remain_none(self):
        """DB product with no channels should convert to None."""
        from src.core.product_conversion import convert_product_model_to_schema

        product_model = self._make_db_product(channels=None)
        product = convert_product_model_to_schema(product_model)
        assert product.channels is None
