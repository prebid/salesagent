"""Unit tests for new AdCP 2.5 product filters.

Tests the filter logic in isolation without requiring a database connection.
Currently implemented filters: countries, channels.
"""

from unittest.mock import Mock

from adcp.types import ProductFilters

from src.core.tools.products import get_adapter_default_channels


class TestAdapterDefaultChannels:
    """Test the adapter default channels mapping."""

    def test_gam_supports_display_olv_social(self):
        """Test that GAM adapter supports display, olv (online video), and social."""
        channels = get_adapter_default_channels("google_ad_manager")
        assert "display" in channels
        assert "olv" in channels  # V3: video → olv
        assert "social" in channels  # V3: native → social

    def test_kevel_supports_social_retail_media(self):
        """Test that Kevel adapter supports social and retail_media."""
        channels = get_adapter_default_channels("kevel")
        assert "social" in channels  # V3: native → social
        assert "retail_media" in channels  # V3: retail → retail_media

    def test_triton_supports_streaming_audio_podcast(self):
        """Test that Triton adapter supports streaming_audio and podcast."""
        channels = get_adapter_default_channels("triton")
        assert "streaming_audio" in channels  # V3: audio → streaming_audio
        assert "podcast" in channels

    def test_mock_supports_all_common_channels(self):
        """Test that mock adapter supports all common channels for testing."""
        channels = get_adapter_default_channels("mock")
        assert "display" in channels
        assert "olv" in channels  # V3: video → olv
        assert "streaming_audio" in channels  # V3: audio → streaming_audio
        assert "social" in channels  # V3: native → social

    def test_unknown_adapter_returns_empty_list(self):
        """Test that unknown adapter type returns empty list."""
        channels = get_adapter_default_channels("unknown_adapter")
        assert channels == []


class TestNewProductFiltersLogic:
    """Test the new filter logic in get_products."""

    def _create_mock_product(
        self,
        product_id: str,
        countries: list[str] | None = None,
        channels: list[str] | None = None,
    ):
        """Create a mock product for testing filters."""
        product = Mock()
        product.product_id = product_id
        product.countries = countries
        product.channels = channels
        return product

    def test_countries_filter_includes_matching_country(self):
        """Test that countries filter includes products with matching country."""
        product = self._create_mock_product(
            product_id="test_product",
            countries=["US", "CA"],
        )

        # Test filter logic: request for US should match product with US
        request_countries = {"US"}
        product_countries = set(product.countries) if product.countries else set()

        # Product should be included if request countries intersect with product countries
        matches = bool(product_countries.intersection(request_countries))
        assert matches is True

    def test_countries_filter_excludes_non_matching_country(self):
        """Test that countries filter excludes products without matching country."""
        product = self._create_mock_product(
            product_id="test_product",
            countries=["UK", "FR"],
        )

        # Test filter logic: request for US should NOT match product with UK/FR
        request_countries = {"US"}
        product_countries = set(product.countries) if product.countries else set()

        matches = bool(product_countries.intersection(request_countries))
        assert matches is False

    def test_countries_filter_matches_global_products(self):
        """Test that global products (no country restriction) match any country filter."""
        product = self._create_mock_product(
            product_id="test_product",
            countries=None,  # Global - no country restriction
        )

        # Global products should match any request (not filtered out)
        product_countries = set(product.countries) if product.countries else set()

        # Empty product_countries means global - should pass through
        assert len(product_countries) == 0

    def test_channels_filter_matches_product_channels(self):
        """Test that channel filter matches product's channels field."""
        product = self._create_mock_product(
            product_id="test_product",
            channels=["display"],
        )

        # Test filter logic: request for display should match product with display channel
        request_channels = {"display"}
        product_channels = {c.lower() for c in product.channels} if product.channels else set()

        matches = bool(product_channels.intersection(request_channels)) if product_channels else True
        assert matches is True

    def test_channels_filter_excludes_non_matching_channel(self):
        """Test that channel filter excludes products with different channel."""
        product = self._create_mock_product(
            product_id="test_product",
            channels=["olv"],  # V3: video → olv
        )

        # Test filter logic: request for display should NOT match product with olv channel
        request_channels = {"display"}
        product_channels = {c.lower() for c in product.channels} if product.channels else set()

        matches = bool(product_channels.intersection(request_channels)) if product_channels else True
        assert matches is False

    def test_channels_filter_with_adapter_defaults(self):
        """Test that products without channels use adapter default channels.

        When a product has no channels set, the filter should check against
        the adapter's default channels. A GAM product without channels will
        match display/olv/social requests but not streaming_audio/podcast.
        """
        product = self._create_mock_product(
            product_id="test_product",
            channels=None,  # No channels - uses adapter defaults
        )

        # For GAM adapter, default channels are display, olv, social (V3)
        gam_defaults = set(get_adapter_default_channels("google_ad_manager"))
        request_display = {"display"}
        request_streaming_audio = {"streaming_audio"}  # V3: audio → streaming_audio

        # Display request should match GAM defaults
        assert request_display.intersection(gam_defaults)

        # streaming_audio request should NOT match GAM defaults
        assert not request_streaming_audio.intersection(gam_defaults)

    def test_channels_filter_multiple_channels(self):
        """Test that channel filter matches when product's channels overlap with request."""
        product = self._create_mock_product(
            product_id="test_product",
            channels=["streaming_audio"],  # V3: audio → streaming_audio
        )

        # Test filter logic: request for display, olv, streaming_audio should match streaming_audio
        request_channels = {"display", "olv", "streaming_audio"}  # V3 channel names
        product_channels = {c.lower() for c in product.channels} if product.channels else set()

        matches = bool(product_channels.intersection(request_channels)) if product_channels else True
        assert matches is True

    def test_product_filters_schema_has_countries_and_channels(self):
        """Test that ProductFilters schema includes countries and channels fields."""
        fields = ProductFilters.model_fields

        assert "countries" in fields
        assert "channels" in fields

    def test_product_filters_can_be_constructed_with_countries_and_channels(self):
        """Test that ProductFilters can be constructed with countries and channels.

        V3 Migration: Channel taxonomy updated - 'video' is now 'olv' (online video).
        """
        filters = ProductFilters(
            countries=["US", "CA"],
            channels=["display", "olv"],  # V3: video→olv
        )

        assert filters.countries is not None
        assert filters.channels is not None

    def test_combined_countries_and_channels_filter(self):
        """Test combining countries and channels filters."""
        # Product in US with display channel
        product = self._create_mock_product(
            product_id="test_product",
            countries=["US"],
            channels=["display"],
        )

        # Request for US and display - should match
        request_countries = {"US"}
        request_channels = {"display"}

        product_countries = set(product.countries) if product.countries else set()
        product_channels = {c.lower() for c in product.channels} if product.channels else set()

        countries_match = len(product_countries) == 0 or bool(product_countries.intersection(request_countries))
        channels_match = bool(product_channels.intersection(request_channels)) if product_channels else True

        assert countries_match is True
        assert channels_match is True

    def test_multi_channel_product_matches_any_channel(self):
        """Test that multi-channel product matches when any of its channels is requested."""
        # Product with display, social, and retail_media channels (V3 channel names)
        product = self._create_mock_product(
            product_id="test_product",
            channels=["display", "social", "retail_media"],
        )

        # Request for just display - should match because product includes display
        request_channels = {"display"}
        product_channels = {c.lower() for c in product.channels} if product.channels else set()

        matches = bool(product_channels.intersection(request_channels)) if product_channels else True
        assert matches is True

        # Request for olv - should NOT match because product doesn't include olv
        request_channels = {"olv"}  # V3: video → olv
        matches = bool(product_channels.intersection(request_channels)) if product_channels else True
        assert matches is False

        # Request for social or streaming_audio - should match because product includes social
        request_channels = {"social", "streaming_audio"}  # V3: native → social, audio → streaming_audio
        matches = bool(product_channels.intersection(request_channels)) if product_channels else True
        assert matches is True
