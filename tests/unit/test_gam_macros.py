"""Tests for GAM macro substitution utilities.

Tests AdCP universal macro -> GAM macro substitution for tracking URLs.
Based on:
- AdCP docs: https://docs.adcontextprotocol.org/docs/creative/universal-macros
- GAM docs: https://support.google.com/admanager/answer/2376981
"""

from src.adapters.gam.utils.macros import (
    ADCP_TO_GAM_MACRO_MAP,
    substitute_macros,
    substitute_tracking_urls,
)


class TestMacroMapping:
    """Test the macro mapping dictionary matches official GAM docs."""

    # ==========================================================================
    # Common Macros
    # ==========================================================================
    def test_common_macros_ids(self):
        """ID macros map to GAM expand macros."""
        assert ADCP_TO_GAM_MACRO_MAP["{MEDIA_BUY_ID}"] == "%ebuy!"
        assert ADCP_TO_GAM_MACRO_MAP["{PACKAGE_ID}"] == "%eaid!"
        assert ADCP_TO_GAM_MACRO_MAP["{CREATIVE_ID}"] == "%ecid!"

    def test_common_macros_tracking(self):
        """Standard tracking macros use %%MACRO%% format."""
        assert ADCP_TO_GAM_MACRO_MAP["{CACHEBUSTER}"] == "%%CACHEBUSTER%%"
        assert ADCP_TO_GAM_MACRO_MAP["{TIMESTAMP}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{CLICK_URL}"] == "%%CLICK_URL_ESC%%"
        assert ADCP_TO_GAM_MACRO_MAP["{CLICK_URL_UNESC}"] == "%%CLICK_URL_UNESC%%"

    # ==========================================================================
    # Privacy & Compliance Macros
    # ==========================================================================
    def test_privacy_macros(self):
        """Privacy macros use various GAM formats."""
        assert ADCP_TO_GAM_MACRO_MAP["{GDPR}"] == "${GDPR}"
        assert ADCP_TO_GAM_MACRO_MAP["{GDPR_CONSENT}"] == "${GDPR_CONSENT_XXX}"
        assert ADCP_TO_GAM_MACRO_MAP["{US_PRIVACY}"] == "${US_PRIVACY}"
        assert ADCP_TO_GAM_MACRO_MAP["{GPP_STRING}"] == "${GPP_STRING}"
        assert ADCP_TO_GAM_MACRO_MAP["{GPP_SID}"] == "${GPP_SID}"
        assert ADCP_TO_GAM_MACRO_MAP["{LIMIT_AD_TRACKING}"] == "%%ADVERTISING_IDENTIFIER_IS_LAT%%"

    # ==========================================================================
    # Device & Environment Macros
    # ==========================================================================
    def test_device_environment_macros(self):
        """Device & environment macros are all None (no GAM equivalent)."""
        assert ADCP_TO_GAM_MACRO_MAP["{DEVICE_TYPE}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{OS}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{OS_VERSION}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{DEVICE_MAKE}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{DEVICE_MODEL}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{USER_AGENT}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{APP_BUNDLE}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{APP_NAME}"] is None

    # ==========================================================================
    # Geographic Macros
    # ==========================================================================
    def test_geographic_macros(self):
        """Geographic macros are all None (no GAM equivalent in tracking)."""
        assert ADCP_TO_GAM_MACRO_MAP["{COUNTRY}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{REGION}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{CITY}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{ZIP}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{DMA}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{LAT}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{LONG}"] is None

    # ==========================================================================
    # Identity Macros
    # ==========================================================================
    def test_identity_macros(self):
        """Identity macros use %%ADVERTISING_IDENTIFIER%% format."""
        assert ADCP_TO_GAM_MACRO_MAP["{DEVICE_ID}"] == "%%ADVERTISING_IDENTIFIER_PLAIN%%"
        assert ADCP_TO_GAM_MACRO_MAP["{DEVICE_ID_TYPE}"] == "%%ADVERTISING_IDENTIFIER_TYPE%%"

    # ==========================================================================
    # Web Context Macros
    # ==========================================================================
    def test_web_context_macros(self):
        """Web context macros."""
        assert ADCP_TO_GAM_MACRO_MAP["{DOMAIN}"] == "%%SITE%%"
        assert ADCP_TO_GAM_MACRO_MAP["{PAGE_URL}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{REFERRER}"] == "%%REFERRER_URL_ESC%%"
        assert ADCP_TO_GAM_MACRO_MAP["{KEYWORDS}"] is None

    # ==========================================================================
    # Placement & Position Macros
    # ==========================================================================
    def test_placement_position_macros(self):
        """Placement & position macros."""
        assert ADCP_TO_GAM_MACRO_MAP["{PLACEMENT_ID}"] == "%epid!"
        assert ADCP_TO_GAM_MACRO_MAP["{FOLD_POSITION}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{AD_WIDTH}"] == "%%WIDTH%%"
        assert ADCP_TO_GAM_MACRO_MAP["{AD_HEIGHT}"] == "%%HEIGHT%%"

    # ==========================================================================
    # Video Content Macros
    # ==========================================================================
    def test_video_content_macros(self):
        """Video content macros."""
        assert ADCP_TO_GAM_MACRO_MAP["{VIDEO_ID}"] == "%%VIDEO_ID%%"
        assert ADCP_TO_GAM_MACRO_MAP["{VIDEO_TITLE}"] == "%%VIDEO_TITLE%%"
        assert ADCP_TO_GAM_MACRO_MAP["{VIDEO_DURATION}"] == "%%VIDEO_DURATION%%"
        assert ADCP_TO_GAM_MACRO_MAP["{VIDEO_CATEGORY}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{CONTENT_GENRE}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{CONTENT_RATING}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{PLAYER_WIDTH}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{PLAYER_HEIGHT}"] is None

    # ==========================================================================
    # Video Ad Pod Macros
    # ==========================================================================
    def test_video_ad_pod_macros(self):
        """Video ad pod macros."""
        assert ADCP_TO_GAM_MACRO_MAP["{POD_POSITION}"] == "%%TAG_PARAM:ppos%%"
        assert ADCP_TO_GAM_MACRO_MAP["{POD_SIZE}"] is None
        assert ADCP_TO_GAM_MACRO_MAP["{AD_BREAK_ID}"] is None

    # ==========================================================================
    # Other Macros
    # ==========================================================================
    def test_other_macros(self):
        """Other macros."""
        assert ADCP_TO_GAM_MACRO_MAP["{AXEM}"] == "%%PATTERN:axem%%"


class TestSubstituteMacros:
    """Test single URL macro substitution."""

    # ==========================================================================
    # Common Macros
    # ==========================================================================
    def test_id_macros_substituted(self):
        """ID macros are substituted to GAM expand macros."""
        url = "https://t.com/p?mb={MEDIA_BUY_ID}&pkg={PACKAGE_ID}&cid={CREATIVE_ID}"
        result = substitute_macros(url)
        assert "%ebuy!" in result
        assert "%eaid!" in result
        assert "%ecid!" in result

    def test_cachebuster_substitution(self):
        """CACHEBUSTER uses %%CACHEBUSTER%% format."""
        url = "https://tracker.com/pixel?cb={CACHEBUSTER}"
        result = substitute_macros(url)
        assert result == "https://tracker.com/pixel?cb=%%CACHEBUSTER%%"

    def test_timestamp_passthrough(self):
        """TIMESTAMP passes through (not in GAM docs)."""
        url = "https://t.com/p?ts={TIMESTAMP}"
        result = substitute_macros(url)
        assert "{TIMESTAMP}" in result

    def test_click_url_macros(self):
        """Click URL macros are substituted."""
        url = "https://t.com/p?click={CLICK_URL}&click2={CLICK_URL_UNESC}"
        result = substitute_macros(url)
        assert "%%CLICK_URL_ESC%%" in result
        assert "%%CLICK_URL_UNESC%%" in result

    # ==========================================================================
    # Privacy & Compliance Macros
    # ==========================================================================
    def test_gdpr_uses_dollar_format(self):
        """GDPR and GDPR_CONSENT use ${} format."""
        url = "https://t.com/p?gdpr={GDPR}&consent={GDPR_CONSENT}"
        result = substitute_macros(url)
        assert "${GDPR}" in result
        assert "${GDPR_CONSENT_XXX}" in result

    def test_us_privacy_uses_dollar_format(self):
        """US_PRIVACY uses ${} format."""
        url = "https://t.com/p?usp={US_PRIVACY}"
        result = substitute_macros(url)
        assert result == "https://t.com/p?usp=${US_PRIVACY}"

    def test_gpp_macros(self):
        """GPP_STRING and GPP_SID use ${} format."""
        url = "https://t.com/p?gpp={GPP_STRING}&gpp_sid={GPP_SID}"
        result = substitute_macros(url)
        assert "${GPP_STRING}" in result
        assert "${GPP_SID}" in result

    def test_limit_ad_tracking(self):
        """LIMIT_AD_TRACKING uses advertising identifier LAT macro."""
        url = "https://t.com/p?lat={LIMIT_AD_TRACKING}"
        result = substitute_macros(url)
        assert "%%ADVERTISING_IDENTIFIER_IS_LAT%%" in result

    # ==========================================================================
    # Device & Environment Macros
    # ==========================================================================
    def test_device_environment_passthrough(self):
        """Device & environment macros pass through."""
        url = "https://t.com/p?dt={DEVICE_TYPE}&os={OS}&ua={USER_AGENT}"
        result = substitute_macros(url)
        assert "{DEVICE_TYPE}" in result
        assert "{OS}" in result
        assert "{USER_AGENT}" in result

    # ==========================================================================
    # Geographic Macros
    # ==========================================================================
    def test_geographic_macros_passthrough(self):
        """Geographic macros pass through."""
        url = "https://t.com/p?country={COUNTRY}&city={CITY}&lat={LAT}&long={LONG}"
        result = substitute_macros(url)
        assert "{COUNTRY}" in result
        assert "{CITY}" in result
        assert "{LAT}" in result
        assert "{LONG}" in result

    # ==========================================================================
    # Identity Macros
    # ==========================================================================
    def test_device_id_substituted(self):
        """DEVICE_ID uses GAM advertising identifier macro."""
        url = "https://t.com/p?device={DEVICE_ID}&type={DEVICE_ID_TYPE}"
        result = substitute_macros(url)
        assert "%%ADVERTISING_IDENTIFIER_PLAIN%%" in result
        assert "%%ADVERTISING_IDENTIFIER_TYPE%%" in result

    # ==========================================================================
    # Web Context Macros
    # ==========================================================================
    def test_domain_macro(self):
        """DOMAIN is substituted to %%SITE%%."""
        url = "https://t.com/p?domain={DOMAIN}"
        result = substitute_macros(url)
        assert result == "https://t.com/p?domain=%%SITE%%"

    def test_page_url_passthrough(self):
        """PAGE_URL passes through."""
        url = "https://t.com/p?page={PAGE_URL}"
        result = substitute_macros(url)
        assert "{PAGE_URL}" in result

    def test_referrer_macro(self):
        """REFERRER is substituted to GAM referrer URL."""
        url = "https://t.com/p?ref={REFERRER}"
        result = substitute_macros(url)
        assert result == "https://t.com/p?ref=%%REFERRER_URL_ESC%%"

    # ==========================================================================
    # Placement & Position Macros
    # ==========================================================================
    def test_placement_id_uses_expand_format(self):
        """PLACEMENT_ID uses %epid! expand format."""
        url = "https://t.com/p?pid={PLACEMENT_ID}"
        result = substitute_macros(url)
        assert result == "https://t.com/p?pid=%epid!"

    def test_fold_position_passthrough(self):
        """FOLD_POSITION passes through."""
        url = "https://t.com/p?fold={FOLD_POSITION}"
        result = substitute_macros(url)
        assert "{FOLD_POSITION}" in result

    def test_size_macros(self):
        """Ad size macros are substituted."""
        url = "https://t.com/p?w={AD_WIDTH}&h={AD_HEIGHT}"
        result = substitute_macros(url)
        assert result == "https://t.com/p?w=%%WIDTH%%&h=%%HEIGHT%%"

    # ==========================================================================
    # Video Content Macros
    # ==========================================================================
    def test_video_macros(self):
        """Video macros are substituted."""
        url = "https://t.com/p?vid={VIDEO_ID}&title={VIDEO_TITLE}&dur={VIDEO_DURATION}"
        result = substitute_macros(url)
        assert "%%VIDEO_ID%%" in result
        assert "%%VIDEO_TITLE%%" in result
        assert "%%VIDEO_DURATION%%" in result

    def test_video_content_passthrough(self):
        """Video content macros without GAM equivalent pass through."""
        url = "https://t.com/p?cat={VIDEO_CATEGORY}&genre={CONTENT_GENRE}"
        result = substitute_macros(url)
        assert "{VIDEO_CATEGORY}" in result
        assert "{CONTENT_GENRE}" in result

    # ==========================================================================
    # Video Ad Pod Macros
    # ==========================================================================
    def test_pod_position_uses_tag_param(self):
        """POD_POSITION uses TAG_PARAM format."""
        url = "https://t.com/p?pos={POD_POSITION}"
        result = substitute_macros(url)
        assert result == "https://t.com/p?pos=%%TAG_PARAM:ppos%%"

    def test_pod_macros_passthrough(self):
        """Pod macros without GAM equivalent pass through."""
        url = "https://t.com/p?size={POD_SIZE}&break={AD_BREAK_ID}"
        result = substitute_macros(url)
        assert "{POD_SIZE}" in result
        assert "{AD_BREAK_ID}" in result

    # ==========================================================================
    # Other Macros
    # ==========================================================================
    def test_axem_converted_to_pattern_format(self):
        """AXEM is converted to GAM PATTERN macro format."""
        url = "https://t.com/p?axe={AXEM}"
        result = substitute_macros(url)
        assert "%%PATTERN:axem%%" in result

    # ==========================================================================
    # General Substitution Behavior
    # ==========================================================================
    def test_no_macros_unchanged(self):
        """URL without macros passes through unchanged."""
        url = "https://tracker.com/pixel?static=value"
        result = substitute_macros(url)
        assert result == url

    def test_mixed_mapped_and_unmapped(self):
        """URL with both mapped and unmapped macros."""
        url = "https://t.com/?cb={CACHEBUSTER}&lat={LAT}&w={AD_WIDTH}"
        result = substitute_macros(url)
        assert "%%CACHEBUSTER%%" in result
        assert "%%WIDTH%%" in result
        assert "{LAT}" in result  # passthrough

    def test_multiple_same_macro(self):
        """Multiple instances of same macro are all substituted."""
        url = "https://t.com/?cb1={CACHEBUSTER}&cb2={CACHEBUSTER}"
        result = substitute_macros(url)
        assert result == "https://t.com/?cb1=%%CACHEBUSTER%%&cb2=%%CACHEBUSTER%%"

    def test_full_sample_url(self):
        """Test with real-world sample URL showing all macro types."""
        url = (
            "http://tracker.example.com/agentic/imp?"
            "ttid=p000001&cid={CREATIVE_ID}&mb={MEDIA_BUY_ID}"
            "&cb={CACHEBUSTER}&ts={TIMESTAMP}"
            "&gdpr={GDPR}&gdpr_c={GDPR_CONSENT}&usp={US_PRIVACY}"
            "&country={COUNTRY}&region={REGION}&city={CITY}&zip={ZIP}"
            "&w={AD_WIDTH}&h={AD_HEIGHT}&pid={PLACEMENT_ID}&p=1"
        )
        result = substitute_macros(url)

        # Standard macros (%%MACRO%% format)
        assert "%%CACHEBUSTER%%" in result
        assert "%%WIDTH%%" in result
        assert "%%HEIGHT%%" in result

        # Expand macros (%macro! format)
        assert "%epid!" in result  # PLACEMENT_ID
        assert "%ecid!" in result  # CREATIVE_ID
        assert "%ebuy!" in result  # MEDIA_BUY_ID

        # GDPR/Privacy macros (${} format)
        assert "${GDPR}" in result
        assert "${GDPR_CONSENT_XXX}" in result
        assert "${US_PRIVACY}" in result

        # Unmapped macros should pass through
        assert "{TIMESTAMP}" in result
        assert "{COUNTRY}" in result
        assert "{REGION}" in result
        assert "{CITY}" in result
        assert "{ZIP}" in result


class TestSubstituteTrackingUrls:
    """Test multiple URL processing."""

    def test_empty_list(self):
        """Empty list returns empty list."""
        result = substitute_tracking_urls([])
        assert result == []

    def test_single_url(self):
        """Single URL is processed."""
        urls = ["https://t.com/p?cb={CACHEBUSTER}"]
        result = substitute_tracking_urls(urls)
        assert len(result) == 1
        assert "%%CACHEBUSTER%%" in result[0]

    def test_multiple_urls(self):
        """Multiple URLs are all processed."""
        urls = [
            "https://t1.com/?cb={CACHEBUSTER}",
            "https://t2.com/?pid={PLACEMENT_ID}",
            "https://t3.com/?w={AD_WIDTH}",
        ]
        result = substitute_tracking_urls(urls)

        assert len(result) == 3
        assert "%%CACHEBUSTER%%" in result[0]
        assert "%epid!" in result[1]
        assert "%%WIDTH%%" in result[2]

    def test_preserves_order(self):
        """URL order is preserved."""
        urls = ["https://first.com/", "https://second.com/", "https://third.com/"]
        result = substitute_tracking_urls(urls)

        assert result[0].startswith("https://first")
        assert result[1].startswith("https://second")
        assert result[2].startswith("https://third")


# =============================================================================
# Tests for _add_tracking_urls_to_creative (GAM creative type field mapping)
# =============================================================================
class TestAddTrackingUrlsToCreative:
    """Test _add_tracking_urls_to_creative handles different GAM creative types correctly."""

    def _get_manager(self):
        """Create a GAMCreativesManager instance for testing."""
        from unittest.mock import MagicMock

        from src.adapters.gam.managers.creatives import GAMCreativesManager

        mock_client = MagicMock()
        return GAMCreativesManager(mock_client, "12345", dry_run=True)

    # -------------------------------------------------------------------------
    # ThirdPartyCreative - uses thirdPartyImpressionTrackingUrls
    # -------------------------------------------------------------------------
    def test_third_party_creative_uses_impression_tracking_urls(self):
        """ThirdPartyCreative uses thirdPartyImpressionTrackingUrls field."""
        manager = self._get_manager()
        creative = {"xsi_type": "ThirdPartyCreative", "name": "Test"}
        asset = {"delivery_settings": {"tracking_urls": {"impression": ["https://tracker.com/pixel?cb={CACHEBUSTER}"]}}}

        manager._add_tracking_urls_to_creative(creative, asset)

        assert "thirdPartyImpressionTrackingUrls" in creative
        assert len(creative["thirdPartyImpressionTrackingUrls"]) == 1
        assert "%%CACHEBUSTER%%" in creative["thirdPartyImpressionTrackingUrls"][0]
        assert "trackingUrls" not in creative

    # -------------------------------------------------------------------------
    # ImageRedirectCreative - uses thirdPartyImpressionTrackingUrls
    # -------------------------------------------------------------------------
    def test_image_redirect_creative_uses_impression_tracking_urls(self):
        """ImageRedirectCreative uses thirdPartyImpressionTrackingUrls field."""
        manager = self._get_manager()
        creative = {"xsi_type": "ImageRedirectCreative", "name": "Test Image"}
        asset = {"delivery_settings": {"tracking_urls": {"impression": ["https://tracker.com/img?pid={PLACEMENT_ID}"]}}}

        manager._add_tracking_urls_to_creative(creative, asset)

        assert "thirdPartyImpressionTrackingUrls" in creative
        assert len(creative["thirdPartyImpressionTrackingUrls"]) == 1
        assert "%epid!" in creative["thirdPartyImpressionTrackingUrls"][0]
        assert "trackingUrls" not in creative

    # -------------------------------------------------------------------------
    # CustomCreative (HTML5) - uses thirdPartyImpressionTrackingUrls
    # -------------------------------------------------------------------------
    def test_custom_creative_uses_impression_tracking_urls(self):
        """CustomCreative (HTML5) uses thirdPartyImpressionTrackingUrls field."""
        manager = self._get_manager()
        creative = {"xsi_type": "CustomCreative", "name": "Test HTML5"}
        asset = {"delivery_settings": {"tracking_urls": {"impression": ["https://tracker.com/html5?gdpr={GDPR}"]}}}

        manager._add_tracking_urls_to_creative(creative, asset)

        assert "thirdPartyImpressionTrackingUrls" in creative
        assert len(creative["thirdPartyImpressionTrackingUrls"]) == 1
        assert "${GDPR}" in creative["thirdPartyImpressionTrackingUrls"][0]
        assert "trackingUrls" not in creative

    # -------------------------------------------------------------------------
    # VideoRedirectCreative - uses trackingUrls with ConversionEvent format
    # -------------------------------------------------------------------------
    def test_video_redirect_creative_uses_tracking_urls_with_event(self):
        """VideoRedirectCreative uses trackingUrls with CREATIVE_VIEW event."""
        manager = self._get_manager()
        creative = {"xsi_type": "VideoRedirectCreative", "name": "Test Video"}
        asset = {"delivery_settings": {"tracking_urls": {"impression": ["https://tracker.com/video?vid={VIDEO_ID}"]}}}

        manager._add_tracking_urls_to_creative(creative, asset)

        assert "trackingUrls" in creative
        assert "thirdPartyImpressionTrackingUrls" not in creative
        assert len(creative["trackingUrls"]) == 1

        entry = creative["trackingUrls"][0]
        assert entry["key"] == "CREATIVE_VIEW"
        assert "urls" in entry["value"]
        assert len(entry["value"]["urls"]) == 1
        assert "%%VIDEO_ID%%" in entry["value"]["urls"][0]

    def test_video_creative_appends_to_existing_creative_view(self):
        """VideoRedirectCreative appends URLs to existing CREATIVE_VIEW entry."""
        manager = self._get_manager()
        creative = {
            "xsi_type": "VideoRedirectCreative",
            "name": "Test Video",
            "trackingUrls": [{"key": "CREATIVE_VIEW", "value": {"urls": ["https://existing.com/pixel"]}}],
        }
        asset = {"delivery_settings": {"tracking_urls": {"impression": ["https://new-tracker.com/pixel"]}}}

        manager._add_tracking_urls_to_creative(creative, asset)

        assert len(creative["trackingUrls"]) == 1
        entry = creative["trackingUrls"][0]
        assert entry["key"] == "CREATIVE_VIEW"
        assert len(entry["value"]["urls"]) == 2
        assert "https://existing.com/pixel" in entry["value"]["urls"]
        assert "https://new-tracker.com/pixel" in entry["value"]["urls"]

    # -------------------------------------------------------------------------
    # Click tracking (via redirection: GAM → click tracker → landing page)
    # -------------------------------------------------------------------------
    def test_click_url_sets_destination_url(self):
        """Click tracking URL is set as destinationUrl for redirection flow."""
        manager = self._get_manager()
        creative = {"xsi_type": "ThirdPartyCreative", "name": "Test"}
        asset = {"delivery_settings": {"tracking_urls": {"click": ["https://click-tracker.com/click?url={CLICK_URL}"]}}}

        manager._add_tracking_urls_to_creative(creative, asset)

        assert "destinationUrl" in creative
        assert "%%CLICK_URL_ESC%%" in creative["destinationUrl"]

    def test_click_url_without_redirect_macro_preserves_landing_page(self):
        """Click tracker without {REDIRECT_URL} is ignored to preserve landing page."""
        manager = self._get_manager()
        creative = {
            "xsi_type": "ImageRedirectCreative",
            "name": "Test Image",
            "destinationUrl": "https://landing-page.com/",
        }
        asset = {"delivery_settings": {"tracking_urls": {"click": ["https://click-tracker.com/click"]}}}

        manager._add_tracking_urls_to_creative(creative, asset)

        # Landing page should be preserved, click tracker ignored (would lose landing page)
        assert creative["destinationUrl"] == "https://landing-page.com/"

    def test_click_url_without_landing_page_sets_destination(self):
        """Click tracker without landing page sets destinationUrl directly."""
        manager = self._get_manager()
        creative = {
            "xsi_type": "ImageRedirectCreative",
            "name": "Test Image",
        }
        asset = {
            "delivery_settings": {"tracking_urls": {"click": ["https://click-tracker.com/click?cb={CACHEBUSTER}"]}}
        }

        manager._add_tracking_urls_to_creative(creative, asset)

        # No landing page, so click tracker becomes destinationUrl
        assert creative["destinationUrl"] == "https://click-tracker.com/click?cb=%%CACHEBUSTER%%"

    def test_click_url_with_redirect_url_macro(self):
        """Click tracker URL has {REDIRECT_URL} replaced with original destination."""
        manager = self._get_manager()
        creative = {
            "xsi_type": "ImageRedirectCreative",
            "name": "Test Image",
            "destinationUrl": "https://landing-page.com/path?param=value",
        }
        asset = {
            "delivery_settings": {"tracking_urls": {"click": ["https://click-tracker.com/c?redir={REDIRECT_URL}"]}}
        }

        manager._add_tracking_urls_to_creative(creative, asset)

        assert "destinationUrl" in creative
        # Original URL should be URL-encoded
        assert "https%3A%2F%2Flanding-page.com%2Fpath%3Fparam%3Dvalue" in creative["destinationUrl"]

    def test_click_url_with_macros_substituted(self):
        """Click tracking URL has macros substituted."""
        manager = self._get_manager()
        creative = {"xsi_type": "ImageRedirectCreative", "name": "Test"}
        asset = {
            "delivery_settings": {"tracking_urls": {"click": ["https://tracker.com/c?cb={CACHEBUSTER}&axem={AXEM}"]}}
        }

        manager._add_tracking_urls_to_creative(creative, asset)

        assert "destinationUrl" in creative
        assert "%%CACHEBUSTER%%" in creative["destinationUrl"]
        assert "%%PATTERN:axem%%" in creative["destinationUrl"]

    # -------------------------------------------------------------------------
    # Edge cases
    # -------------------------------------------------------------------------
    def test_no_tracking_urls_does_nothing(self):
        """Creative without tracking URLs is not modified."""
        manager = self._get_manager()
        creative = {"xsi_type": "ThirdPartyCreative", "name": "Test"}
        asset = {}

        manager._add_tracking_urls_to_creative(creative, asset)

        assert "thirdPartyImpressionTrackingUrls" not in creative
        assert "trackingUrls" not in creative

    def test_empty_tracking_urls_does_nothing(self):
        """Creative with empty tracking URLs is not modified."""
        manager = self._get_manager()
        creative = {"xsi_type": "ThirdPartyCreative", "name": "Test"}
        asset = {"delivery_settings": {"tracking_urls": {"impression": []}}}

        manager._add_tracking_urls_to_creative(creative, asset)

        assert "thirdPartyImpressionTrackingUrls" not in creative

    def test_multiple_impression_urls_all_added(self):
        """Multiple impression tracking URLs are all added."""
        manager = self._get_manager()
        creative = {"xsi_type": "ImageRedirectCreative", "name": "Test"}
        asset = {
            "delivery_settings": {
                "tracking_urls": {
                    "impression": [
                        "https://tracker1.com/pixel",
                        "https://tracker2.com/pixel",
                        "https://tracker3.com/pixel",
                    ]
                }
            }
        }

        manager._add_tracking_urls_to_creative(creative, asset)

        assert len(creative["thirdPartyImpressionTrackingUrls"]) == 3
