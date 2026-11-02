"""Unit tests for Broadstreet Advertisement Manager.

The manager has no dry-run mode: an advertisement exists only once Broadstreet has
been called, so what these tests stand in for is the VENDOR CLIENT, and what they
grade is the call the manager makes on it.
"""

import pytest

from src.adapters.broadstreet.managers.advertisements import (
    FORMAT_TO_AD_TYPE,
    AdvertisementInfo,
    BroadstreetAdvertisementManager,
)
from tests.helpers.broadstreet_client import stub_broadstreet_client


@pytest.fixture
def client():
    """A stand-in Broadstreet API client that hands back a distinct id per ad."""
    return stub_broadstreet_client()


class TestAdvertisementInfo:
    """Tests for AdvertisementInfo class."""

    def test_init_minimal(self):
        """Test AdvertisementInfo with minimal parameters."""
        info = AdvertisementInfo(
            creative_id="creative_1",
            broadstreet_id="bs_ad_1",
            name="Test Ad",
            ad_type="static",
        )

        assert info.creative_id == "creative_1"
        assert info.broadstreet_id == "bs_ad_1"
        assert info.name == "Test Ad"
        assert info.ad_type == "static"
        assert info.status == "pending"
        assert info.placement_ids == []

    def test_init_with_status(self):
        """Test AdvertisementInfo with custom status."""
        info = AdvertisementInfo(
            creative_id="creative_1",
            broadstreet_id="bs_ad_1",
            name="Test Ad",
            ad_type="html",
            status="approved",
        )

        assert info.status == "approved"

    def test_to_dict(self):
        """Test AdvertisementInfo serialization."""
        info = AdvertisementInfo(
            creative_id="creative_1",
            broadstreet_id="bs_ad_1",
            name="Test Ad",
            ad_type="static",
            status="approved",
        )
        info.placement_ids = ["placement_1", "placement_2"]

        result = info.to_dict()

        assert result["creative_id"] == "creative_1"
        assert result["broadstreet_id"] == "bs_ad_1"
        assert result["name"] == "Test Ad"
        assert result["ad_type"] == "static"
        assert result["status"] == "approved"
        assert result["placement_ids"] == ["placement_1", "placement_2"]


class TestFormatToAdType:
    """Tests for FORMAT_TO_AD_TYPE mapping."""

    def test_display_formats_map_to_static(self):
        """Test display formats map to static."""
        assert FORMAT_TO_AD_TYPE["display"] == "static"
        assert FORMAT_TO_AD_TYPE["image"] == "static"
        assert FORMAT_TO_AD_TYPE["static"] == "static"
        assert FORMAT_TO_AD_TYPE["banner"] == "static"

    def test_html_formats_map_to_html(self):
        """Test HTML formats map to html."""
        assert FORMAT_TO_AD_TYPE["html"] == "html"
        assert FORMAT_TO_AD_TYPE["html5"] == "html"
        assert FORMAT_TO_AD_TYPE["rich_media"] == "html"
        assert FORMAT_TO_AD_TYPE["custom"] == "html"

    def test_text_formats_map_to_text(self):
        """Test text formats map to text."""
        assert FORMAT_TO_AD_TYPE["text"] == "text"
        assert FORMAT_TO_AD_TYPE["native_text"] == "text"


class TestBroadstreetAdvertisementManager:
    """Tests for BroadstreetAdvertisementManager.

    No test here builds the manager with ``client=None``. One briefly did, asserting the
    ``AdCPConfigurationError`` that ``create_advertisement`` raises in that case — but
    that state is unreachable: ``BroadstreetAdapter.__init__`` always constructs a real
    ``BroadstreetClient`` (after requiring ``api_key`` and ``network_id``) and hands it
    to all four managers. The optional client and its two guards are dry-run residue —
    ``client=None`` WAS the dry-run signal — so a test for them grades dead code.
    """

    @pytest.fixture
    def manager(self, client):
        """Create an advertisement manager over the stand-in client."""
        return BroadstreetAdvertisementManager(
            client=client,
            advertiser_id="adv_123",
        )

    def test_get_ad_type_from_format(self, manager):
        """Test ad type detection from format field."""
        # Display format
        assert manager._get_ad_type({"format": "display"}) == "static"
        assert manager._get_ad_type({"format": "image"}) == "static"

        # HTML format
        assert manager._get_ad_type({"format": "html"}) == "html"
        assert manager._get_ad_type({"format": "html5"}) == "html"

        # Text format
        assert manager._get_ad_type({"format": "text"}) == "text"

    def test_get_ad_type_from_html_content(self, manager):
        """Test ad type detection from HTML content."""
        assert manager._get_ad_type({"html": "<div>Ad</div>"}) == "html"
        assert manager._get_ad_type({"snippet": "<script>...</script>"}) == "html"

    def test_get_ad_type_from_image_url(self, manager):
        """Test ad type detection from image URL."""
        assert manager._get_ad_type({"media_url": "https://example.com/ad.png"}) == "static"
        assert manager._get_ad_type({"image_url": "https://example.com/ad.jpg"}) == "static"

    def test_get_ad_type_html_extension(self, manager):
        """Test HTML file extensions detected as html type."""
        assert manager._get_ad_type({"media_url": "https://example.com/ad.html"}) == "html"
        assert manager._get_ad_type({"media_url": "https://example.com/ad.htm"}) == "html"
        assert manager._get_ad_type({"media_url": "https://example.com/ad.zip"}) == "html"

    def test_get_ad_type_from_text_content(self, manager):
        """Test ad type detection from text content."""
        assert manager._get_ad_type({"default_text": "Buy now!"}) == "text"
        assert manager._get_ad_type({"headline": "Special Offer"}) == "text"

    def test_get_ad_type_defaults_to_static(self, manager):
        """Test that unknown formats default to static."""
        assert manager._get_ad_type({}) == "static"
        assert manager._get_ad_type({"unknown_field": "value"}) == "static"

    def test_build_ad_params_html(self, manager):
        """Test building params for HTML ad."""
        asset = {"html": "<div>HTML Ad</div>"}
        params = manager._build_ad_params(asset, "html")

        assert params["html"] == "<div>HTML Ad</div>"

    def test_build_ad_params_html_from_snippet(self, manager):
        """Test building params for HTML ad from snippet."""
        asset = {"snippet": "<script>...</script>"}
        params = manager._build_ad_params(asset, "html")

        assert params["html"] == "<script>...</script>"

    def test_build_ad_params_html_from_url(self, manager):
        """Test building params for HTML ad from URL."""
        asset = {"media_url": "https://example.com/ad.html"}
        params = manager._build_ad_params(asset, "html")

        assert "iframe" in params["html"]
        assert "https://example.com/ad.html" in params["html"]

    def test_build_ad_params_static(self, manager):
        """Test building params for static image ad."""
        asset = {"media_url": "https://example.com/ad.png", "click_url": "https://example.com"}
        params = manager._build_ad_params(asset, "static")

        assert params["image"] == "https://example.com/ad.png"
        assert params["url"] == "https://example.com"

    def test_build_ad_params_static_with_base64(self, manager):
        """Test building params for static ad with base64 image."""
        asset = {"image_base64": "data:image/png;base64,ABC123"}
        params = manager._build_ad_params(asset, "static")

        assert params["image_base64"] == "data:image/png;base64,ABC123"

    def test_build_ad_params_text(self, manager):
        """Test building params for text ad."""
        asset = {"default_text": "Buy now!"}
        params = manager._build_ad_params(asset, "text")

        assert params["default_text"] == "Buy now!"

    def test_build_ad_params_text_from_structured(self, manager):
        """Test building params for text ad from headline and description."""
        asset = {"headline": "Special Offer", "description": "50% off today!"}
        params = manager._build_ad_params(asset, "text")

        assert "Special Offer" in params["default_text"]
        assert "50% off today!" in params["default_text"]

    def test_create_advertisement_static(self, manager, client):
        """A static ad is created through the client and keeps the returned id."""
        asset = {
            "creative_id": "creative_1",
            "name": "Test Ad",
            "format": "display",
            "media_url": "https://example.com/ad.png",
        }

        info = manager.create_advertisement("mb_1", asset)

        client.create_advertisement.assert_called_once_with(
            advertiser_id="adv_123",
            name="Test Ad",
            ad_type="static",
            params={"image": "https://example.com/ad.png"},
        )
        assert info.creative_id == "creative_1"
        assert info.name == "Test Ad"
        assert info.ad_type == "static"
        assert info.status == "approved"
        assert info.broadstreet_id == "bs_1"

    def test_create_advertisement_records_failure_when_the_client_raises(self, manager, client):
        """A vendor failure is recorded on the ad, not swallowed into a success."""
        client.create_advertisement.side_effect = RuntimeError("Broadstreet said no")

        info = manager.create_advertisement(
            "mb_1", {"creative_id": "creative_1", "media_url": "https://example.com/ad.png"}
        )

        assert info.status == "failed"
        assert info.broadstreet_id is None

    def test_create_advertisement_html(self, manager):
        """Test creating HTML advertisement."""
        asset = {
            "creative_id": "creative_html",
            "name": "HTML Ad",
            "html": "<div>Rich Ad</div>",
        }

        info = manager.create_advertisement("mb_1", asset)

        assert info.ad_type == "html"
        assert info.status == "approved"

    def test_create_advertisement_text(self, manager):
        """Test creating text advertisement."""
        asset = {
            "creative_id": "creative_text",
            "name": "Text Ad",
            "default_text": "Call to action!",
        }

        info = manager.create_advertisement("mb_1", asset)

        assert info.ad_type == "text"
        assert info.status == "approved"

    def test_create_advertisements_multiple(self, manager):
        """Test creating multiple advertisements."""
        assets = [
            {"creative_id": "c1", "name": "Ad 1", "media_url": "https://example.com/1.png"},
            {"creative_id": "c2", "name": "Ad 2", "html": "<div>Ad 2</div>"},
            {"creative_id": "c3", "name": "Ad 3", "default_text": "Text Ad"},
        ]

        results = manager.create_advertisements("mb_1", assets)

        assert len(results) == 3
        assert results[0].ad_type == "static"
        assert results[1].ad_type == "html"
        assert results[2].ad_type == "text"

    def test_get_advertisement(self, manager):
        """Test getting advertisement by ID."""
        asset = {"creative_id": "creative_1", "name": "Test", "media_url": "https://example.com/ad.png"}
        manager.create_advertisement("mb_1", asset)

        info = manager.get_advertisement("mb_1", "creative_1")
        assert info is not None
        assert info.creative_id == "creative_1"

        # Non-existent
        info = manager.get_advertisement("mb_1", "creative_unknown")
        assert info is None

    def test_get_all_advertisements(self, manager):
        """Test getting all advertisements for a media buy."""
        assets = [
            {"creative_id": "c1", "name": "Ad 1", "media_url": "https://example.com/1.png"},
            {"creative_id": "c2", "name": "Ad 2", "media_url": "https://example.com/2.png"},
        ]
        manager.create_advertisements("mb_1", assets)

        all_ads = manager.get_all_advertisements("mb_1")
        assert len(all_ads) == 2

        # Different media buy
        all_ads = manager.get_all_advertisements("mb_unknown")
        assert len(all_ads) == 0

    def test_get_broadstreet_ids(self, manager):
        """Test getting Broadstreet IDs for a media buy."""
        assets = [
            {"creative_id": "c1", "name": "Ad 1", "media_url": "https://example.com/1.png"},
            {"creative_id": "c2", "name": "Ad 2", "media_url": "https://example.com/2.png"},
        ]
        manager.create_advertisements("mb_1", assets)

        ids = manager.get_broadstreet_ids("mb_1")
        assert ids == ["bs_1", "bs_2"]

    def test_update_advertisement(self, manager, client):
        """An update reaches the client under the ad's Broadstreet id."""
        asset = {"creative_id": "c1", "name": "Ad", "media_url": "https://example.com/ad.png"}
        manager.create_advertisement("mb_1", asset)

        result = manager.update_advertisement("mb_1", "c1", {"name": "Updated Ad"})

        assert result is True
        client.update_advertisement.assert_called_once_with(
            advertiser_id="adv_123",
            advertisement_id="bs_1",
            params={"name": "Updated Ad"},
        )

    def test_update_advertisement_not_found(self, manager):
        """Test updating non-existent advertisement."""
        result = manager.update_advertisement("mb_1", "c_unknown", {"name": "New"})
        assert result is False

    def test_delete_advertisement(self, manager, client):
        """A delete reaches the client and drops the ad from the cache."""
        asset = {"creative_id": "c1", "name": "Ad", "media_url": "https://example.com/ad.png"}
        manager.create_advertisement("mb_1", asset)

        result = manager.delete_advertisement("mb_1", "c1")
        assert result is True
        client.delete_advertisement.assert_called_once_with(advertiser_id="adv_123", advertisement_id="bs_1")

        # Should be removed from cache
        info = manager.get_advertisement("mb_1", "c1")
        assert info is None

    def test_delete_advertisement_not_found(self, manager):
        """Test deleting non-existent advertisement."""
        result = manager.delete_advertisement("mb_1", "c_unknown")
        assert result is False

    def test_validate_asset_html_valid(self, manager):
        """Test validating valid HTML asset."""
        asset = {"format": "html", "html": "<div>Ad</div>"}
        is_valid, error = manager.validate_asset(asset)

        assert is_valid is True
        assert error is None

    def test_validate_asset_html_invalid(self, manager):
        """Test validating invalid HTML asset."""
        asset = {"format": "html"}  # Missing html content
        is_valid, error = manager.validate_asset(asset)

        assert is_valid is False
        assert "html" in error.lower()

    def test_validate_asset_static_valid(self, manager):
        """Test validating valid static asset."""
        asset = {"format": "display", "media_url": "https://example.com/ad.png"}
        is_valid, error = manager.validate_asset(asset)

        assert is_valid is True
        assert error is None

    def test_validate_asset_static_invalid(self, manager):
        """Test validating invalid static asset."""
        asset = {"format": "display"}  # Missing image
        is_valid, error = manager.validate_asset(asset)

        assert is_valid is False
        assert "image" in error.lower() or "media" in error.lower()

    def test_validate_asset_text_valid(self, manager):
        """Test validating valid text asset."""
        asset = {"format": "text", "default_text": "Buy now!"}
        is_valid, error = manager.validate_asset(asset)

        assert is_valid is True
        assert error is None

    def test_validate_asset_text_invalid(self, manager):
        """Test validating invalid text asset."""
        asset = {"format": "text"}  # Missing text
        is_valid, error = manager.validate_asset(asset)

        assert is_valid is False
        assert "text" in error.lower()

    def test_get_delivery_report(self, manager, client):
        """The report is the client's, asked for under the ad's id and date range."""
        client.get_advertisement_report.return_value = [{"impressions": 1000, "clicks": 7}]
        asset = {"creative_id": "c1", "name": "Ad", "media_url": "https://example.com/ad.png"}
        manager.create_advertisement("mb_1", asset)

        report = manager.get_delivery_report("mb_1", "c1", "2024-01-01", "2024-01-31")

        client.get_advertisement_report.assert_called_once_with(
            advertiser_id="adv_123",
            advertisement_id="bs_1",
            start_date="2024-01-01",
            end_date="2024-01-31",
        )
        assert report == [{"impressions": 1000, "clicks": 7}]

    def test_get_delivery_report_not_found(self, manager):
        """Test getting delivery report for non-existent ad."""
        report = manager.get_delivery_report("mb_1", "c_unknown")
        assert report == []

    def test_isolation_between_media_buys(self, manager):
        """Test that advertisements are isolated between media buys."""
        manager.create_advertisement("mb_1", {"creative_id": "c1", "name": "Ad 1", "media_url": "https://1.png"})
        manager.create_advertisement("mb_2", {"creative_id": "c1", "name": "Ad 2", "media_url": "https://2.png"})

        # Same creative_id, different media buys
        info1 = manager.get_advertisement("mb_1", "c1")
        info2 = manager.get_advertisement("mb_2", "c1")

        assert info1 is not None
        assert info2 is not None
        assert info1.name == "Ad 1"
        assert info2.name == "Ad 2"

    def test_auto_generated_creative_id(self, manager):
        """Test that creative_id is auto-generated if not provided."""
        asset = {"name": "Ad", "media_url": "https://example.com/ad.png"}
        info = manager.create_advertisement("mb_1", asset)

        assert info.creative_id is not None
        assert info.creative_id.startswith("creative_")


class TestTemplateAdvertisements:
    """Tests for template-based advertisement support."""

    @pytest.fixture
    def manager(self, client):
        """Create a manager over the stand-in client."""
        return BroadstreetAdvertisementManager(
            client=client,
            advertiser_id="test_advertiser",
            log_func=lambda msg: None,
        )

    def test_is_template_ad_explicit(self, manager):
        """Test explicit template_type detection."""
        asset = {"template_type": "cube_3d", "front_image": "https://1.png"}
        is_template, template_type = manager.is_template_ad(asset)

        assert is_template is True
        assert template_type == "cube_3d"

    def test_is_template_ad_cube_auto_detect(self, manager):
        """Test auto-detection of 3D cube from 6 face images."""
        asset = {
            "front_image": "https://front.png",
            "back_image": "https://back.png",
            "left_image": "https://left.png",
            "right_image": "https://right.png",
            "top_image": "https://top.png",
            "bottom_image": "https://bottom.png",
        }
        is_template, template_type = manager.is_template_ad(asset)

        assert is_template is True
        assert template_type == "cube_3d"

    def test_is_template_ad_youtube_auto_detect(self, manager):
        """Test auto-detection of YouTube video."""
        asset = {"youtube_url": "https://www.youtube.com/watch?v=abc123"}
        is_template, template_type = manager.is_template_ad(asset)

        assert is_template is True
        assert template_type == "youtube_video"

    def test_is_template_ad_gallery_auto_detect(self, manager):
        """Test auto-detection of gallery from multiple images."""
        asset = {
            "images": [
                "https://1.png",
                "https://2.png",
                "https://3.png",
            ]
        }
        is_template, template_type = manager.is_template_ad(asset)

        assert is_template is True
        assert template_type == "gallery"

    def test_is_template_ad_standard(self, manager):
        """Test standard ad is not detected as template."""
        asset = {"media_url": "https://banner.png"}
        is_template, template_type = manager.is_template_ad(asset)

        assert is_template is False
        assert template_type is None

    def test_build_template_source_params_cube(self, manager):
        """Test building cube template source params."""
        asset = {
            "front_image": "https://front.png",
            "back_image": "https://back.png",
            "left_image": "https://left.png",
            "right_image": "https://right.png",
            "top_image": "https://top.png",
            "bottom_image": "https://bottom.png",
            "front_caption": "Front side",
            "click_url": "https://example.com",
        }
        params = manager._build_template_source_params("cube_3d", asset)

        assert params["front_image"] == "https://front.png"
        assert params["back_image"] == "https://back.png"
        assert params["front_caption"] == "Front side"
        assert params["url"] == "https://example.com"

    def test_build_template_source_params_youtube(self, manager):
        """Test building YouTube template source params."""
        asset = {
            "youtube_url": "https://www.youtube.com/watch?v=abc123",
            "headline": "Check this out!",
            "body": "Amazing video content",
        }
        params = manager._build_template_source_params("youtube_video", asset)

        assert params["url"] == "https://www.youtube.com/watch?v=abc123"
        assert params["headline"] == "Check this out!"
        assert params["body"] == "Amazing video content"

    def test_build_template_source_params_gallery(self, manager):
        """Test building gallery template source params."""
        asset = {
            "images": ["https://1.png", "https://2.png", "https://3.png"],
            "captions": ["Image 1", "Image 2", "Image 3"],
            "auto_rotate_ms": 3000,
        }
        params = manager._build_template_source_params("gallery", asset)

        assert params["image_1"] == "https://1.png"
        assert params["image_2"] == "https://2.png"
        assert params["caption_1"] == "Image 1"
        assert params["timeout"] == 3000

    def test_create_template_advertisement(self, manager, client):
        """A template ad is a base HTML ad plus a source set to the template type."""
        asset = {
            "creative_id": "cube_ad_1",
            "name": "3D Cube Ad",
            "template_type": "cube_3d",
            "front_image": "https://front.png",
            "back_image": "https://back.png",
            "left_image": "https://left.png",
            "right_image": "https://right.png",
            "top_image": "https://top.png",
            "bottom_image": "https://bottom.png",
        }
        info = manager.create_advertisement("mb_1", asset)

        client.create_advertisement.assert_called_once_with(
            advertiser_id="test_advertiser",
            name="3D Cube Ad",
            ad_type="html",
            params={},
        )
        client.set_advertisement_source.assert_called_once_with(
            advertiser_id="test_advertiser",
            advertisement_id="bs_1",
            source_type="cube",
            params={
                "front_image": "https://front.png",
                "back_image": "https://back.png",
                "left_image": "https://left.png",
                "right_image": "https://right.png",
                "top_image": "https://top.png",
                "bottom_image": "https://bottom.png",
            },
        )
        assert info.creative_id == "cube_ad_1"
        assert info.ad_type == "template:cube_3d"
        assert info.status == "approved"
        assert info.broadstreet_id == "bs_1"

    def test_create_template_advertisement_explicit_type(self, manager, client):
        """Test creating template ad with explicit template_type parameter."""
        asset = {
            "creative_id": "gallery_1",
            "name": "Gallery Ad",
            "images": ["https://1.png", "https://2.png"],
        }
        # Even though content auto-detects as gallery, we can override
        info = manager.create_advertisement("mb_1", asset, template_type="gallery")

        assert info.ad_type == "template:gallery"
        client.set_advertisement_source.assert_called_once_with(
            advertiser_id="test_advertiser",
            advertisement_id="bs_1",
            source_type="gallery",
            params={"image_1": "https://1.png", "image_2": "https://2.png"},
        )

    def test_standard_ad_not_affected_by_template_detection(self, manager, client):
        """A standard ad takes the one-call path: no template source is ever set."""
        asset = {
            "creative_id": "banner_1",
            "name": "Standard Banner",
            "media_url": "https://banner.png",
        }
        info = manager.create_advertisement("mb_1", asset)

        assert info.ad_type == "static"
        assert info.broadstreet_id == "bs_1"
        client.set_advertisement_source.assert_not_called()
