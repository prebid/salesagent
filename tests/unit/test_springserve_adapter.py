"""Tests for the SpringServe adapter -- registry wiring, dry-run, init."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.adapters import get_adapter_default_channels, get_adapter_schemas
from src.adapters.springserve import SpringServeAdapter
from src.adapters.springserve.schemas import SpringServeConnectionConfig, SpringServeProductConfig
from src.core.schemas import CreateMediaBuyError, CreateMediaBuySuccess, FormatId
from tests.helpers.adapter_test_helpers import (
    invoke_create_media_buy,
    make_sample_create_request,
    make_sample_video_package,
)


@pytest.fixture
def mock_principal():
    principal = MagicMock()
    principal.name = "video_advertiser"
    principal.principal_id = "principal_ss_1"
    # SpringServe Demand Partner IDs are integers; the adapter casts the
    # returned value to int.
    principal.get_adapter_id.return_value = "42"
    principal.platform_mappings = {"springserve": {"demand_partner_id": "42"}}
    return principal


@pytest.fixture
def sample_request():
    return make_sample_create_request()


@pytest.fixture
def sample_packages():
    return [make_sample_video_package()]


class TestRegistry:
    def test_get_adapter_schemas_returns_springserve_classes(self):
        schemas = get_adapter_schemas("springserve")
        assert schemas is not None
        assert schemas.connection_config is SpringServeConnectionConfig
        assert schemas.product_config is SpringServeProductConfig
        assert schemas.capabilities.inventory_entity_label == "Supply Tags"

    def test_default_channels_cover_video_and_audio(self):
        channels = get_adapter_default_channels("springserve")
        assert "olv" in channels
        assert "ctv" in channels
        assert "streaming_audio" in channels
        assert "podcast" in channels

    def test_default_delivery_measurement_is_springserve(self):
        adapter = SpringServeAdapter(
            config={"api_token": "tok"},
            principal=MagicMock(get_adapter_id=MagicMock(return_value="42")),
            dry_run=True,
            tenant_id="tenant_ss_1",
        )
        assert adapter.default_delivery_measurement == {"provider": "springserve"}


class TestCapabilities:
    def _dry_run_adapter(self, mock_principal):
        return SpringServeAdapter(
            config={"api_token": "tok"},
            principal=mock_principal,
            dry_run=True,
            tenant_id="tenant_ss_1",
        )

    def test_supported_pricing_models(self, mock_principal):
        adapter = self._dry_run_adapter(mock_principal)
        assert adapter.get_supported_pricing_models() == {"cpm", "flat_rate"}

    def test_targeting_capabilities(self, mock_principal):
        adapter = self._dry_run_adapter(mock_principal)
        caps = adapter.get_targeting_capabilities()
        assert caps.geo_countries is True
        assert caps.geo_regions is True
        assert caps.nielsen_dma is True
        assert caps.us_zip is False  # postal targeting unsupported

    def test_creative_formats_include_video_and_audio(self, mock_principal):
        adapter = self._dry_run_adapter(mock_principal)
        formats = adapter.get_creative_formats()
        format_ids = [f["format_id"]["id"] for f in formats]
        assert format_ids.count("video_vast") == 2
        assert format_ids.count("audio_vast") == 2
        assert {f["format_id"]["duration_ms"] for f in formats} == {15000, 30000}

        video_types = {f["type"] for f in formats}
        assert video_types == {"video", "audio"}


class TestAdapterDryRun:
    def test_dry_run_creates_buy_without_calling_client(self, mock_principal, sample_request, sample_packages):
        adapter = SpringServeAdapter(
            config={"api_token": "test-token"},
            principal=mock_principal,
            dry_run=True,
            tenant_id="tenant_ss_1",
        )
        response = invoke_create_media_buy(adapter, sample_request, sample_packages)
        assert response.packages is not None
        assert len(response.packages) == 1
        assert response.media_buy_id.startswith("springserve_")
        assert adapter._client is None

    def test_dry_run_rejects_postal_targeting(self, mock_principal, sample_request, sample_packages):
        from src.core.schemas import Targeting

        sample_packages[0] = sample_packages[0].model_copy(
            update={
                "targeting_overlay": Targeting(
                    geo_countries=["US"],
                    geo_postal_areas=[{"system": "us_zip", "values": ["10001"]}],
                )
            }
        )
        adapter = SpringServeAdapter(
            config={"api_token": "tok"},
            principal=mock_principal,
            dry_run=True,
            tenant_id="tenant_ss_1",
        )
        response = invoke_create_media_buy(adapter, sample_request, sample_packages)
        assert hasattr(response, "errors")
        assert response.errors[0].code == "unsupported_targeting"

    def test_live_mode_requires_credentials(self, mock_principal):
        with pytest.raises(ValueError, match="email \\+ password.*or api_token"):
            SpringServeAdapter(
                config={},
                principal=mock_principal,
                dry_run=False,
                tenant_id="tenant_ss_1",
            )

    def test_live_mode_requires_demand_partner_id(self):
        principal = MagicMock()
        principal.principal_id = "principal_no_dp"
        principal.get_adapter_id.return_value = None  # no mapping

        with pytest.raises(ValueError, match="demand_partner_id"):
            SpringServeAdapter(
                config={"api_token": "tok"},
                principal=principal,
                dry_run=False,
                tenant_id="tenant_ss_1",
            )

    def test_default_demand_partner_id_fallback(self):
        principal = MagicMock()
        principal.principal_id = "principal_no_mapping"
        principal.get_adapter_id.return_value = None  # no per-principal mapping

        adapter = SpringServeAdapter(
            config={"api_token": "tok", "default_demand_partner_id": 99},
            principal=principal,
            dry_run=False,
            tenant_id="tenant_ss_1",
        )
        assert adapter.demand_partner_id == 99


class TestLiveCreateMediaBuy:
    """Mapping A: AdCP MediaBuy -> SpringServe Campaign,
    AdCP Package -> SpringServe Demand Tag."""

    def _adapter_with_mock_client(self, mock_principal, **extra_config):
        config = {"api_token": "tok"}
        config.update(extra_config)
        adapter = SpringServeAdapter(
            config=config,
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_ss_1",
        )
        adapter._client = MagicMock()
        adapter._client.campaigns.create.return_value = MagicMock(id=900001)
        adapter._client.demand_tags.create.return_value = MagicMock(id=800001)
        return adapter

    def test_creates_one_campaign_per_buy(self, mock_principal, sample_request, sample_packages):
        adapter = self._adapter_with_mock_client(mock_principal)
        invoke_create_media_buy(adapter, sample_request, sample_packages)
        assert adapter._client.campaigns.create.call_count == 1

    def test_campaign_create_uses_principal_demand_partner_and_paused(
        self, mock_principal, sample_request, sample_packages
    ):
        adapter = self._adapter_with_mock_client(mock_principal)
        invoke_create_media_buy(adapter, sample_request, sample_packages)
        kw = adapter._client.campaigns.create.call_args.kwargs
        assert kw["demand_partner_id"] == 42
        assert kw["is_active"] is False  # created paused -- Stage 3 binds creative + activates

    def test_one_demand_tag_per_package_parented_to_new_campaign(self, mock_principal, sample_request, sample_packages):
        adapter = self._adapter_with_mock_client(mock_principal)
        invoke_create_media_buy(adapter, sample_request, sample_packages)

        assert adapter._client.demand_tags.create.call_count == len(sample_packages)
        for package, call in zip(
            sample_packages,
            adapter._client.demand_tags.create.call_args_list,
            strict=True,
        ):
            kw = call.kwargs
            assert kw["campaign_id"] == 900001
            assert kw["demand_partner_id"] == 42
            assert kw["is_active"] is False
            assert kw["secondary_code"] == package.package_id
            assert kw["format"] == "video"  # video format_id on the sample

    def test_media_buy_id_carries_campaign_id(self, mock_principal, sample_request, sample_packages):
        adapter = self._adapter_with_mock_client(mock_principal)
        response = invoke_create_media_buy(adapter, sample_request, sample_packages)
        assert response.media_buy_id == "springserve_900001"

    def test_audio_format_routes_demand_tag_format_to_audio(self, mock_principal, sample_request, sample_packages):
        from src.core.schemas import FormatId

        adapter = self._adapter_with_mock_client(mock_principal)
        sample_packages[0] = sample_packages[0].model_copy(
            update={"format_ids": [FormatId(agent_url="https://creative.adcontextprotocol.org", id="audio_vast")]}
        )
        invoke_create_media_buy(adapter, sample_request, sample_packages)

        kw = adapter._client.demand_tags.create.call_args.kwargs
        assert kw["format"] == "audio"

    def test_upstream_error_returns_error_response(self, mock_principal, sample_request, sample_packages):
        from src.adapters.springserve import SpringServeError

        adapter = self._adapter_with_mock_client(mock_principal)
        adapter._client.campaigns.create.side_effect = SpringServeError("network 503", status_code=503, body="oops")

        response = invoke_create_media_buy(adapter, sample_request, sample_packages)
        assert hasattr(response, "errors")
        assert response.errors[0].code == "upstream_error"
        # No demand tags created if campaign failed.
        adapter._client.demand_tags.create.assert_not_called()

    def test_kv_entry_known_scope_blocker_is_swallowed(self, mock_principal, sample_request, sample_packages):
        """The documented 422 ("Targeter must have key_value_targeting set
        to true") is the one and only KV error we tolerate -- the buy still
        lands so geo/device/supply targeting can take effect."""
        from src.adapters.springserve import SpringServeValidationError

        adapter = self._adapter_with_mock_client(mock_principal, enable_key_value_targeting=True)
        adapter._client.demand_tags.add_kv_entry.side_effect = SpringServeValidationError(
            "POST /demand_tag_keys -> HTTP 422",
            status_code=422,
            body='{"error":"Targeter must have key_value_targeting set to true"}',
        )
        with patch(
            "src.adapters.springserve.adapter.build_demand_tag_kv_entries",
            return_value=[{"key_id": "3997", "list_type": "white_list", "group": "1", "free_values": ["x"]}],
        ):
            response = invoke_create_media_buy(adapter, sample_request, sample_packages)

        # The buy still landed.
        assert isinstance(response, CreateMediaBuySuccess)
        adapter._client.demand_tags.add_kv_entry.assert_called_once_with(
            800001,
            key_id="3997",
            list_type="white_list",
            group="1",
            free_values=["x"],
        )

    def test_kv_entry_other_422_propagates(self, mock_principal, sample_request, sample_packages):
        """A different 422 (not the known blocker) MUST surface as an
        upstream_error -- not silently mask a real validation failure."""
        from src.adapters.springserve import SpringServeValidationError

        adapter = self._adapter_with_mock_client(mock_principal, enable_key_value_targeting=True)
        adapter._client.demand_tags.add_kv_entry.side_effect = SpringServeValidationError(
            "POST /demand_tag_keys -> HTTP 422",
            status_code=422,
            body='{"error":"Free values can\'t be blank"}',
        )
        with patch(
            "src.adapters.springserve.adapter.build_demand_tag_kv_entries",
            return_value=[{"key_id": "3997", "list_type": "white_list", "group": "1", "free_values": []}],
        ):
            response = invoke_create_media_buy(adapter, sample_request, sample_packages)

        assert isinstance(response, CreateMediaBuyError)
        assert response.errors[0].code == "upstream_error"

    @pytest.mark.parametrize(
        "exc_factory,exc_label",
        [
            (
                lambda: __import__(
                    "src.adapters.springserve._transport", fromlist=["SpringServeAuthError"]
                ).SpringServeAuthError("POST /demand_tag_keys -> HTTP 401", status_code=401, body="invalid token"),
                "auth-401",
            ),
            (
                lambda: __import__(
                    "src.adapters.springserve._transport", fromlist=["SpringServeRateLimitError"]
                ).SpringServeRateLimitError(
                    "POST /demand_tag_keys -> HTTP 429", status_code=429, body="too many requests"
                ),
                "rate-limit-429",
            ),
            (
                lambda: __import__(
                    "src.adapters.springserve._transport", fromlist=["SpringServeServerError"]
                ).SpringServeServerError("POST /demand_tag_keys -> HTTP 503", status_code=503, body="upstream timeout"),
                "server-5xx",
            ),
        ],
        ids=["auth-401", "rate-limit-429", "server-5xx"],
    )
    def test_kv_entry_non_validation_errors_propagate(
        self, mock_principal, sample_request, sample_packages, exc_factory, exc_label
    ):
        """Transient or auth-level SpringServe failures (401/429/5xx) MUST
        propagate to the buyer as upstream_error -- they're sibling
        classes of SpringServeValidationError and must not get caught by
        the narrow KV blocker handler. Each error class is exercised
        explicitly so a future refactor that re-broadens the catch fails
        loudly here."""
        adapter = self._adapter_with_mock_client(mock_principal, enable_key_value_targeting=True)
        adapter._client.demand_tags.add_kv_entry.side_effect = exc_factory()
        with patch(
            "src.adapters.springserve.adapter.build_demand_tag_kv_entries",
            return_value=[{"key_id": "3997", "list_type": "white_list", "group": "1", "free_values": ["x"]}],
        ):
            response = invoke_create_media_buy(adapter, sample_request, sample_packages)

        assert isinstance(response, CreateMediaBuyError), f"{exc_label} should surface as upstream_error"
        assert response.errors[0].code == "upstream_error"

    def test_kv_targeting_off_by_default_skips_add_kv_entry(self, mock_principal, sample_request, sample_packages):
        """Default tenant config (enable_key_value_targeting=False) MUST NOT
        POST any demand_tag_keys entries -- supply-tag selection is the
        primary targeting surface for most publishers, and we don't want
        to make sub-resource writes the buyer didn't ask for."""
        adapter = self._adapter_with_mock_client(mock_principal)
        with patch(
            "src.adapters.springserve.adapter.build_demand_tag_kv_entries",
            return_value=[{"key_id": "3997", "list_type": "white_list", "group": "1", "free_values": ["x"]}],
        ) as mock_build:
            response = invoke_create_media_buy(adapter, sample_request, sample_packages)

        assert isinstance(response, CreateMediaBuySuccess)
        adapter._client.demand_tags.add_kv_entry.assert_not_called()
        # And the materializer is never invoked when the flag is off, so we
        # don't pay the cost of resolving signals we'll never write.
        mock_build.assert_not_called()

    def test_demand_class_defaults_to_line_item_on_wire(self, mock_principal, sample_request, sample_packages):
        """The default config maps to SpringServe's "Line Item" demand class
        on the create body -- that's the class that supports hosted creative
        binding via line_item_ratios."""
        adapter = self._adapter_with_mock_client(mock_principal)
        invoke_create_media_buy(adapter, sample_request, sample_packages)

        kw = adapter._client.demand_tags.create.call_args.kwargs
        assert kw["demand_class"] == "line_item"  # internal value; wire mapping done in _demand_tags.py

    def test_demand_class_tag_passes_through_to_demand_tag_create(
        self, mock_principal, sample_request, sample_packages
    ):
        """When the tenant is provisioned for passthrough demand (third-party
        VAST/audio URLs), the adapter ships demand_class='tag' through to
        the demand_tag create body."""
        adapter = self._adapter_with_mock_client(mock_principal, demand_class="tag")
        invoke_create_media_buy(adapter, sample_request, sample_packages)

        kw = adapter._client.demand_tags.create.call_args.kwargs
        assert kw["demand_class"] == "tag"

    def test_demand_class_tag_passes_vast_endpoint_url_from_package_config(
        self, mock_principal, sample_request, sample_packages
    ):
        adapter = self._adapter_with_mock_client(mock_principal, demand_class="tag")
        sample_packages[0] = sample_packages[0].model_copy(
            update={
                "format_ids": [FormatId(agent_url="https://creative.adcontextprotocol.org", id="audio_vast")],
                "implementation_config": {
                    "springserve": {
                        "extra_demand_tag_fields": {"vast_endpoint_url": "https://ads.example.com/audio-vast.xml"}
                    }
                },
            }
        )
        invoke_create_media_buy(adapter, sample_request, sample_packages)

        kw = adapter._client.demand_tags.create.call_args.kwargs
        assert kw["format"] == "audio"
        assert kw["vast_endpoint_url"] == "https://ads.example.com/audio-vast.xml"


class TestLiveCreatives:
    """Stage 3 creative upload + binding."""

    _PUBLIC_DNS_RESULT = [(None, None, None, "", ("93.184.216.34", 443))]

    def _adapter(self, mock_principal, **config_overrides):
        config = {"api_token": "tok"}
        config.update(config_overrides)
        adapter = SpringServeAdapter(
            config=config,
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_ss_1",
        )
        adapter._client = MagicMock()
        return adapter

    def test_stored_audio_creative_builds_adapter_payload_without_dimensions(self):
        from types import SimpleNamespace

        from src.core.helpers.creative_helpers import (
            adapter_asset_requires_dimensions,
            build_adapter_asset_from_stored_creative,
        )

        creative = SimpleNamespace(
            creative_id="talpa_audio_1",
            name="Talpa Audio 30s",
            agent_url="https://creative.adcontextprotocol.org",
            format="audio_30s",
            format_parameters={"duration_ms": 30000},
            data={"assets": {"audio_file": {"asset_type": "audio", "url": "https://cdn.example.com/spot.mp3"}}},
        )

        asset = build_adapter_asset_from_stored_creative(creative, package_id="pkg_audio", format_spec=None)

        assert asset["format_id"] == {
            "agent_url": "https://creative.adcontextprotocol.org",
            "id": "audio_30s",
            "duration_ms": 30000,
        }
        assert asset["url"] == "https://cdn.example.com/spot.mp3"
        assert asset["asset_type"] == "audio"
        assert asset["content_type"] == "audio/mpeg"
        assert asset["duration_seconds"] == 30
        assert adapter_asset_requires_dimensions("springserve", asset) is False

    def test_add_creative_assets_posts_video(self, mock_principal):
        adapter = self._adapter(mock_principal)
        adapter._client.creatives.create.return_value = MagicMock(id=1182735)

        with patch("socket.getaddrinfo", return_value=self._PUBLIC_DNS_RESULT):
            statuses = adapter.add_creative_assets(
                "springserve_900001",
                [
                    {
                        "creative_id": "adcp_creative_1",
                        "name": "Spot 15s",
                        "url": "https://cdn.example.com/spot.mp4",
                        "duration_seconds": 15,
                        "width": 1920,
                        "height": 1080,
                    }
                ],
                today=datetime.now(UTC),
            )

        adapter._client.creatives.create.assert_called_once_with(
            name="Spot 15s",
            demand_partner_id=42,
            creative_remote_url="https://cdn.example.com/spot.mp4",
            creative_format="video",
            creative_content_type="video/mp4",
            duration_seconds=15,
            width=1920,
            height=1080,
            creative_landing_page_url=None,
            secondary_code="adcp_creative_1",
        )
        # Returned creative_id is the SS id (so associate_creatives can use it).
        assert statuses[0].creative_id == "1182735"
        assert statuses[0].status == "approved"

    def test_add_creative_assets_audio_format_id_requires_tag_mode(self, mock_principal):
        adapter = self._adapter(mock_principal)
        adapter._client.creatives.create.return_value = MagicMock(id=1182999)

        statuses = adapter.add_creative_assets(
            "springserve_900001",
            [
                {
                    "creative_id": "adcp_audio_1",
                    "name": "Audio Spot",
                    "url": "https://cdn.example.com/spot.mp3",
                    "format_id": {"id": "audio_30s", "agent_url": "https://creative.adcontextprotocol.org"},
                }
            ],
            today=datetime.now(UTC),
        )

        adapter._client.creatives.create.assert_not_called()
        assert statuses[0].status == "failed"
        assert "hosted audio upload is not supported" in statuses[0].message

    def test_add_creative_assets_audio_format_string_requires_tag_mode(self, mock_principal):
        """The media-buy upload bridge passes both ``format`` and structured
        ``format_id``; SpringServe must honor either shape."""
        adapter = self._adapter(mock_principal)
        adapter._client.creatives.create.return_value = MagicMock(id=1183000)

        statuses = adapter.add_creative_assets(
            "springserve_900001",
            [
                {
                    "creative_id": "adcp_audio_2",
                    "name": "Audio Spot",
                    "url": "https://cdn.example.com/spot.mp3",
                    "format": "audio_30s",
                    "asset_type": "audio",
                    "content_type": "audio/mpeg",
                    "duration_seconds": 30,
                }
            ],
            today=datetime.now(UTC),
        )

        adapter._client.creatives.create.assert_not_called()
        assert statuses[0].status == "failed"
        assert "hosted audio upload is not supported" in statuses[0].message

    def test_add_creative_assets_audio_mime_hint_requires_tag_mode(self, mock_principal):
        """If the asset itself carries an audio/* content_type, route to audio
        even without a format_id hint."""
        adapter = self._adapter(mock_principal)
        adapter._client.creatives.create.return_value = MagicMock(id=1)

        statuses = adapter.add_creative_assets(
            "springserve_900001",
            [{"creative_id": "c1", "url": "https://x", "content_type": "audio/mp4"}],
            today=datetime.now(UTC),
        )

        adapter._client.creatives.create.assert_not_called()
        assert statuses[0].status == "failed"
        assert "hosted audio upload is not supported" in statuses[0].message

    def test_add_creative_assets_tag_mode_validates_url_without_upload(self, mock_principal):
        adapter = self._adapter(mock_principal, demand_class="tag")

        with patch("socket.getaddrinfo", return_value=self._PUBLIC_DNS_RESULT):
            statuses = adapter.add_creative_assets(
                "springserve_900001",
                [
                    {
                        "creative_id": "adcp_audio_vast_1",
                        "url": "https://ads.example.com/audio-vast.xml",
                        "format_id": {"id": "audio_vast", "agent_url": "https://creative.adcontextprotocol.org"},
                    }
                ],
                today=datetime.now(UTC),
            )

        adapter._client.creatives.create.assert_not_called()
        assert statuses[0].status == "approved"
        assert "demand_class=tag" in statuses[0].message

    def test_add_creative_assets_missing_url_marks_failed(self, mock_principal):
        adapter = self._adapter(mock_principal)
        statuses = adapter.add_creative_assets(
            "springserve_900001",
            [{"creative_id": "no_url"}],
            today=datetime.now(UTC),
        )
        adapter._client.creatives.create.assert_not_called()
        assert statuses[0].status == "failed"
        assert statuses[0].creative_id == "no_url"

    def test_add_creative_assets_missing_creative_id_marks_failed(self, mock_principal):
        """A bad asset reports failed for its own slot; later assets keep going."""
        adapter = self._adapter(mock_principal)
        adapter._client.creatives.create.return_value = MagicMock(id=1)

        with patch("socket.getaddrinfo", return_value=self._PUBLIC_DNS_RESULT):
            statuses = adapter.add_creative_assets(
                "springserve_900001",
                [
                    {"url": "https://x/spot.mp4"},  # missing creative_id
                    {"creative_id": "good", "url": "https://cdn.example.com/spot.mp4"},
                ],
                today=datetime.now(UTC),
            )

        # Bad asset fails fast with empty id; good asset is uploaded normally.
        assert statuses[0].status == "failed"
        assert statuses[0].creative_id == ""
        assert statuses[1].status == "approved"
        # Good asset triggered exactly one creates call (the bad asset short-circuited).
        assert adapter._client.creatives.create.call_count == 1

    def test_add_creative_assets_rejects_non_https_url(self, mock_principal):
        adapter = self._adapter(mock_principal)
        statuses = adapter.add_creative_assets(
            "springserve_900001",
            [{"creative_id": "c_http", "url": "http://cdn.example.com/spot.mp4"}],
            today=datetime.now(UTC),
        )
        adapter._client.creatives.create.assert_not_called()
        assert statuses[0].status == "failed"

    def test_add_creative_assets_rejects_loopback_url(self, mock_principal):
        adapter = self._adapter(mock_principal)
        statuses = adapter.add_creative_assets(
            "springserve_900001",
            [{"creative_id": "c_local", "url": "https://localhost:8080/spot.mp4"}],
            today=datetime.now(UTC),
        )
        adapter._client.creatives.create.assert_not_called()
        assert statuses[0].status == "failed"

    def test_add_creative_assets_rejects_rfc1918_url(self, mock_principal):
        adapter = self._adapter(mock_principal)
        statuses = adapter.add_creative_assets(
            "springserve_900001",
            [{"creative_id": "c_priv", "url": "https://10.0.0.5/spot.mp4"}],
            today=datetime.now(UTC),
        )
        adapter._client.creatives.create.assert_not_called()
        assert statuses[0].status == "failed"

    def test_add_creative_assets_upstream_failure_marks_failed(self, mock_principal):
        from src.adapters.springserve import SpringServeError

        adapter = self._adapter(mock_principal)
        adapter._client.creatives.create.side_effect = SpringServeError("rejected", status_code=400, body="bad")
        statuses = adapter.add_creative_assets(
            "springserve_900001",
            [{"creative_id": "c1", "url": "https://x"}],
            today=datetime.now(UTC),
        )
        assert statuses[0].status == "failed"

    def test_associate_creatives_binds_and_activates_demand_tag(self, mock_principal):
        adapter = self._adapter(mock_principal)
        results = adapter.associate_creatives(line_item_ids=["800001"], platform_creative_ids=["1182735"])

        adapter._client.demand_tags.update.assert_called_once_with(
            800001,
            line_item_ratios=[{"creative_id": 1182735, "ratio": 1}],
            is_active=True,
        )
        assert results == [{"line_item_id": "800001", "creative_id": "1182735", "status": "success"}]

    def test_associate_creatives_multiple_per_tag_keeps_last_marks_others_skipped(self, mock_principal):
        adapter = self._adapter(mock_principal)
        results = adapter.associate_creatives(
            line_item_ids=["800001"],
            platform_creative_ids=["1", "2", "3"],
        )

        # Only the last creative is bound; earlier ones recorded as skipped.
        adapter._client.demand_tags.update.assert_called_once_with(
            800001,
            line_item_ratios=[{"creative_id": 3, "ratio": 1}],
            is_active=True,
        )
        statuses = [r["status"] for r in results]
        assert statuses == ["skipped", "skipped", "success"]

    def test_associate_creatives_upstream_error_marks_failed(self, mock_principal):
        from src.adapters.springserve import SpringServeError

        adapter = self._adapter(mock_principal)
        adapter._client.demand_tags.update.side_effect = SpringServeError("rejected", status_code=400, body="bad")
        results = adapter.associate_creatives(line_item_ids=["800001"], platform_creative_ids=["1"])
        assert results[0]["status"] == "failed"

    def test_associate_creatives_demand_class_tag_skips_binding(self, mock_principal):
        """demand_class=tag means the demand tag IS a third-party VAST/audio
        URL -- there's no Creatives surface on the SpringServe side to bind
        to. The adapter must return skipped results and MUST NOT issue a
        PUT against the demand tag."""
        adapter = SpringServeAdapter(
            config={"api_token": "tok", "demand_class": "tag"},
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_ss_1",
        )
        adapter._client = MagicMock()

        results = adapter.associate_creatives(
            line_item_ids=["800001", "800002"],
            platform_creative_ids=["1"],
        )

        adapter._client.demand_tags.update.assert_not_called()
        assert [r["status"] for r in results] == ["skipped", "skipped"]
        assert all("demand_class=tag" in r["message"] for r in results)


class TestLiveCheckStatus:
    def test_active_campaign_reports_active(self, mock_principal):
        adapter = SpringServeAdapter(
            config={"api_token": "tok"},
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_ss_1",
        )
        adapter._client = MagicMock()
        adapter._client.campaigns.get.return_value = MagicMock(is_active=True)

        result = adapter.check_media_buy_status("springserve_900001", today=datetime.now(UTC))

        adapter._client.campaigns.get.assert_called_once_with(900001)
        assert result.status == "active"

    def test_inactive_campaign_reports_paused(self, mock_principal):
        adapter = SpringServeAdapter(
            config={"api_token": "tok"},
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_ss_1",
        )
        adapter._client = MagicMock()
        adapter._client.campaigns.get.return_value = MagicMock(is_active=False)

        result = adapter.check_media_buy_status("springserve_900001", today=datetime.now(UTC))
        assert result.status == "paused"


class TestLiveUpdateMediaBuy:
    def _adapter(self, mock_principal):
        adapter = SpringServeAdapter(
            config={"api_token": "tok"},
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_ss_1",
        )
        adapter._client = MagicMock()
        return adapter

    def test_pause_media_buy_flips_campaign_inactive(self, mock_principal):
        adapter = self._adapter(mock_principal)
        result = adapter.update_media_buy(
            media_buy_id="springserve_900001",
            action="pause_media_buy",
            package_id=None,
            budget=None,
            today=datetime.now(UTC),
        )
        adapter._client.campaigns.update.assert_called_once_with(900001, is_active=False)
        assert not hasattr(result, "errors")

    def test_resume_media_buy_flips_campaign_active(self, mock_principal):
        adapter = self._adapter(mock_principal)
        adapter.update_media_buy(
            media_buy_id="springserve_900001",
            action="resume_media_buy",
            package_id=None,
            budget=None,
            today=datetime.now(UTC),
        )
        adapter._client.campaigns.update.assert_called_once_with(900001, is_active=True)

    def test_pause_package_finds_demand_tag_by_secondary_code(self, mock_principal):
        adapter = self._adapter(mock_principal)
        adapter._client.campaigns.get.return_value = MagicMock(demand_tag_ids=[800001, 800002])
        adapter._client.demand_tags.get.side_effect = [
            MagicMock(id=800001, secondary_code="other_pkg"),
            MagicMock(id=800002, secondary_code="pkg_target"),
        ]

        result = adapter.update_media_buy(
            media_buy_id="springserve_900001",
            action="pause_package",
            package_id="pkg_target",
            budget=None,
            today=datetime.now(UTC),
        )

        adapter._client.demand_tags.update.assert_called_once_with(800002, is_active=False)
        assert not hasattr(result, "errors")

    def test_pause_package_missing_returns_package_not_found(self, mock_principal):
        adapter = self._adapter(mock_principal)
        adapter._client.campaigns.get.return_value = MagicMock(demand_tag_ids=[800001])
        adapter._client.demand_tags.get.return_value = MagicMock(id=800001, secondary_code="other")

        result = adapter.update_media_buy(
            media_buy_id="springserve_900001",
            action="pause_package",
            package_id="missing",
            budget=None,
            today=datetime.now(UTC),
        )
        assert result.errors[0].code == "package_not_found"
        adapter._client.demand_tags.update.assert_not_called()

    def test_update_package_budget_not_yet_supported(self, mock_principal):
        adapter = self._adapter(mock_principal)
        result = adapter.update_media_buy(
            media_buy_id="springserve_900001",
            action="update_package_budget",
            package_id="pkg_1",
            budget=5000,
            today=datetime.now(UTC),
        )
        assert result.errors[0].code == "unsupported_action"
        assert "Stage 4" in result.errors[0].message

    def test_update_media_buy_rejects_unknown_action(self, mock_principal):
        adapter = self._adapter(mock_principal)
        response = adapter.update_media_buy(
            media_buy_id="springserve_900001",
            action="banana",
            package_id=None,
            budget=None,
            today=datetime.now(UTC),
        )
        assert response.errors[0].code == "unsupported_action"


class TestPermissionsProbe:
    def test_dry_run_reports_no_live_client(self, mock_principal):
        adapter = SpringServeAdapter(
            config={"api_token": "tok"},
            principal=mock_principal,
            dry_run=True,
            tenant_id="tenant_ss_1",
        )
        report = adapter.check_permissions()
        assert report.error is not None
        assert "Dry-run" in report.error
        assert report.fully_operational is False
        assert report.checks == []

    def test_probe_walks_every_endpoint(self, mock_principal):
        adapter = SpringServeAdapter(
            config={"api_token": "tok"},
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_ss_1",
        )
        # Every probe returns 200 -> all granted, fully_operational.
        adapter._client = MagicMock()
        adapter._client.probe.return_value = (200, "")

        report = adapter.check_permissions()
        # 6 probes: campaigns, demand_tags, videos, supply_tags, supply_partners, report
        assert len(report.checks) == 6
        assert all(c.granted for c in report.checks)
        assert report.fully_operational is True

    def test_probe_marks_403_as_denied(self, mock_principal):
        adapter = SpringServeAdapter(
            config={"api_token": "tok"},
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_ss_1",
        )
        adapter._client = MagicMock()
        # All endpoints 403 -- token valid but lacks scope on every surface
        adapter._client.probe.return_value = (403, "forbidden")

        report = adapter.check_permissions()
        assert all(not c.granted for c in report.checks)
        assert report.fully_operational is False
