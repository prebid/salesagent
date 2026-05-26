"""Tests for the FreeWheel adapter — factory wiring + dry-run + client construction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

import pytest

from src.adapters import get_adapter_default_channels, get_adapter_schemas
from src.adapters.freewheel import FreeWheelAdapter, FreeWheelClient
from src.adapters.freewheel.schemas import FreeWheelConnectionConfig, FreeWheelProductConfig
from tests.helpers.adapter_test_helpers import (
    invoke_create_media_buy,
    make_sample_create_request,
    make_sample_video_package,
)


@pytest.fixture
def mock_principal():
    principal = MagicMock()
    principal.name = "video_advertiser"
    principal.principal_id = "principal_fw_1"
    # FW advertiser IDs are integers in the live API; tests use a numeric
    # string so the adapter can cast cleanly when calling create_campaign.
    principal.get_adapter_id.return_value = "1356511"
    principal.platform_mappings = {"freewheel": {"advertiser_id": "1356511"}}
    return principal


@pytest.fixture
def sample_request():
    return make_sample_create_request()


@pytest.fixture
def sample_packages():
    return [make_sample_video_package()]


class TestRegistry:
    def test_get_adapter_schemas_returns_freewheel_classes(self):
        schemas = get_adapter_schemas("freewheel")
        assert schemas is not None
        assert schemas.connection_config is FreeWheelConnectionConfig
        assert schemas.product_config is FreeWheelProductConfig
        assert schemas.capabilities.inventory_entity_label == "Placements"

    def test_default_channels_emphasise_video(self):
        channels = get_adapter_default_channels("freewheel")
        assert "olv" in channels
        assert "ctv" in channels


class TestAdapterDryRun:
    def test_dry_run_creates_buy_without_calling_client(self, mock_principal, sample_request, sample_packages):
        adapter = FreeWheelAdapter(
            config={"api_token": "test-bearer-token"},
            principal=mock_principal,
            dry_run=True,
            tenant_id="tenant_fw_1",
        )
        response = invoke_create_media_buy(adapter, sample_request, sample_packages)
        assert response.packages is not None
        assert len(response.packages) == 1
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
        adapter = FreeWheelAdapter(
            config={"api_token": "test-bearer-token"},
            principal=mock_principal,
            dry_run=True,
            tenant_id="tenant_fw_1",
        )
        response = invoke_create_media_buy(adapter, sample_request, sample_packages)
        assert hasattr(response, "errors")
        assert response.errors[0].code == "unsupported_targeting"

    def test_live_mode_requires_api_token(self, mock_principal):
        with pytest.raises(ValueError, match="api_token"):
            FreeWheelAdapter(config={}, principal=mock_principal, dry_run=False, tenant_id="tenant_fw_1")


class TestLiveCreateMediaBuy:
    """Mapping A: AdCP MediaBuy -> FW IO, AdCP Package -> FW Placement,
    with FW Campaign as a per-buy wrapper above the IO."""

    def _adapter_with_mock_client(self, mock_principal, **client_overrides):
        adapter = FreeWheelAdapter(
            config={"api_token": "test-bearer-token"},
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_fw_1",
        )
        adapter._client = MagicMock()
        # Default happy-path return values; tests can override.
        adapter._client.commercial.create_campaign.return_value = MagicMock(id=900001)
        adapter._client.commercial.create_insertion_order.return_value = MagicMock(id=900002)
        adapter._client.commercial.create_placement.return_value = MagicMock(id=900003)
        for attr, value in client_overrides.items():
            setattr(adapter._client.commercial, attr, value)
        return adapter

    def test_creates_campaign_io_and_placement_per_package(self, mock_principal, sample_request, sample_packages):
        adapter = self._adapter_with_mock_client(mock_principal)

        response = invoke_create_media_buy(adapter, sample_request, sample_packages)

        # Campaign + IO each created once. Names are derived from po_number /
        # timestamp (unpredictable from the test's POV) so we use ANY there,
        # but the ID linkage and advertiser scoping are exact.
        adapter._client.commercial.create_campaign.assert_called_once_with(name=ANY, advertiser_id=1356511)
        adapter._client.commercial.create_insertion_order.assert_called_once_with(name=ANY, campaign_id=900001)

        # One placement per AdCP package, all parented to the new IO.
        assert adapter._client.commercial.create_placement.call_count == len(sample_packages)
        for package, call in zip(
            sample_packages, adapter._client.commercial.create_placement.call_args_list, strict=True
        ):
            assert call == ((), {"name": package.name, "insertion_order_id": 900002})

        # media_buy_id reflects the IO (not the Campaign) — IO is the
        # commercial transaction in Mapping A.
        assert response.media_buy_id == "freewheel_900002"
        assert response._platform_line_item_ids == {sample_packages[0].package_id: "900003"}
        assert response.packages is not None
        assert response.packages[0].platform_line_item_id == "900003"

    def test_upstream_error_returns_error_response(self, mock_principal, sample_request, sample_packages):
        from src.adapters.freewheel import FreeWheelError

        bad_create = MagicMock(side_effect=FreeWheelError("network 503", status_code=503, body="oops"))
        adapter = self._adapter_with_mock_client(mock_principal, create_campaign=bad_create)

        response = invoke_create_media_buy(adapter, sample_request, sample_packages)

        assert hasattr(response, "errors")
        assert response.errors[0].code == "upstream_error"
        # No IO/placement created if campaign failed.
        adapter._client.commercial.create_insertion_order.assert_not_called()
        adapter._client.commercial.create_placement.assert_not_called()


class TestLiveCreatives:
    def _adapter(self, mock_principal):
        adapter = FreeWheelAdapter(
            config={"api_token": "test-bearer-token"},
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_fw_1",
        )
        adapter._client = MagicMock()
        return adapter

    def test_add_creative_assets_posts_canonical_vast_rendition(self, mock_principal):
        adapter = self._adapter(mock_principal)
        adapter._client.creatives.create_creative.return_value = MagicMock(id=1182735)

        statuses = adapter.add_creative_assets(
            "freewheel_900002",
            [
                {
                    "creative_id": "fw_vast_1",
                    "name": "FW VAST 30s",
                    "url": "https://ads.example.com/vast.xml",
                    "format_id": {
                        "agent_url": "https://creative.adcontextprotocol.org",
                        "id": "video_vast",
                        "duration_ms": 30000,
                    },
                    "content_type": "application/xml",
                }
            ],
            today=datetime.now(UTC),
        )

        adapter._client.creatives.create_creative.assert_called_once_with(
            name="FW VAST 30s",
            advertiser_ids=[1356511],
            base_ad_unit_id=None,
            external_id="fw_vast_1",
            renditions=[
                {
                    "uri": "https://ads.example.com/vast.xml",
                    "content_type": "application/xml",
                    "vast_rendition": True,
                    "https_compatibility": "compatible",
                }
            ],
            duration=30,
        )
        assert statuses[0].creative_id == "1182735"
        assert statuses[0].status == "approved"

    def test_add_creative_assets_missing_vast_url_marks_failed(self, mock_principal):
        adapter = self._adapter(mock_principal)

        statuses = adapter.add_creative_assets(
            "freewheel_900002",
            [{"creative_id": "missing_url", "format": "video_vast"}],
            today=datetime.now(UTC),
        )

        adapter._client.creatives.create_creative.assert_not_called()
        assert statuses[0].creative_id == "missing_url"
        assert statuses[0].status == "failed"


class TestCheckMediaBuyStatus:
    def test_live_mode_reads_insertion_order_stage(self, mock_principal):
        adapter = FreeWheelAdapter(
            config={"api_token": "t"},
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_fw_1",
        )
        adapter._client = MagicMock()
        adapter._client.commercial.get_insertion_order.return_value = MagicMock(stage="BOOKED", status=None)

        result = adapter.check_media_buy_status("freewheel_900002", today=datetime.now(UTC))

        adapter._client.commercial.get_insertion_order.assert_called_once_with(900002)
        assert result.status == "booked"


class TestNightlyForecastDelivery:
    def test_get_media_buy_delivery_fetches_nightly_forecast_when_cache_empty(self, mock_principal, monkeypatch):
        from src.adapters.freewheel.entities import NightlyForecast
        from src.core.schemas import ReportingPeriod

        adapter = FreeWheelAdapter(
            config={"api_token": "t"},
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_fw_1",
        )
        adapter._client = MagicMock()
        adapter._client.forecasting.nightly_forecast.return_value = NightlyForecast(
            placement_id=900003,
            run_time="2026-05-22T12:00:00Z",
            delivered_impressions=12_345,
            delivered_budget="67.89",
            exchange_currency="EUR",
        )

        saved_rows: list[dict] = []

        class FakeStatsRepo:
            def __init__(self, session, tenant_id):
                self.tenant_id = tenant_id

            def list_by_insertion_order(self, insertion_order_id):
                assert insertion_order_id == "900002"
                return []

            def bulk_upsert(self, rows):
                saved_rows.extend(rows)
                return len(rows)

            def get_by_placement_ids(self, placement_ids):
                ids = set(placement_ids)
                return {row["placement_id"]: SimpleNamespace(**row) for row in saved_rows if row["placement_id"] in ids}

        class FakeMediaBuyRepo:
            def __init__(self, session, tenant_id):
                self.tenant_id = tenant_id

            def get_packages(self, media_buy_id):
                assert media_buy_id == "freewheel_900002"
                return [
                    SimpleNamespace(
                        package_id="pkg_1",
                        package_config={"platform_line_item_id": "900003"},
                    )
                ]

        monkeypatch.setattr("src.adapters.freewheel.adapter.FreeWheelPlacementStatsRepository", FakeStatsRepo)
        monkeypatch.setattr("src.adapters.freewheel.adapter.MediaBuyRepository", FakeMediaBuyRepo)
        monkeypatch.setattr(
            "src.adapters.freewheel.adapter.get_db_session",
            lambda: __import__("contextlib").nullcontext(MagicMock()),
        )

        date_range = ReportingPeriod(start=datetime.now(UTC), end=datetime.now(UTC))
        response = adapter.get_media_buy_delivery("freewheel_900002", date_range, datetime.now(UTC))

        adapter._client.forecasting.nightly_forecast.assert_called_once_with("900003")
        assert saved_rows[0]["insertion_order_id"] == "900002"
        assert saved_rows[0]["impressions"] == 12_345
        assert saved_rows[0]["spend_micros"] == 67_890_000
        assert response.totals.impressions == 12_345.0
        assert response.totals.spend == 67.89
        assert response.currency == "EUR"
        assert response.ext == {
            "data_source": "freewheel_nightly_forecast",
            "partial_data": True,
            "note": "Latest FreeWheel nightly forecast snapshot, not an exact report for the requested period.",
        }

    def test_get_media_buy_delivery_merges_stale_cache_with_partial_refresh(self, mock_principal, monkeypatch):
        from src.core.schemas import ReportingPeriod

        adapter = FreeWheelAdapter(
            config={"api_token": "t"},
            principal=mock_principal,
            dry_run=False,
            tenant_id="tenant_fw_1",
        )
        adapter._client = MagicMock()

        now = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
        stale_row = SimpleNamespace(
            placement_id="900003",
            impressions=100,
            completed_views=None,
            spend_micros=1_000_000,
            currency="EUR",
            as_of=now - timedelta(days=2),
            last_synced_at=now - timedelta(days=2),
        )
        retained_row = SimpleNamespace(
            placement_id="900004",
            impressions=200,
            completed_views=None,
            spend_micros=2_000_000,
            currency="EUR",
            as_of=now - timedelta(days=2),
            last_synced_at=now - timedelta(days=2),
        )
        fresh_row = SimpleNamespace(
            placement_id="900003",
            impressions=12_345,
            completed_views=None,
            spend_micros=67_890_000,
            currency="EUR",
            as_of=now,
            last_synced_at=now,
        )

        class FakeStatsRepo:
            def __init__(self, session, tenant_id):
                self.tenant_id = tenant_id

            def list_by_insertion_order(self, insertion_order_id):
                assert insertion_order_id == "900002"
                return [stale_row, retained_row]

        refresh = MagicMock(return_value=[fresh_row])
        monkeypatch.setattr("src.adapters.freewheel.adapter.FreeWheelPlacementStatsRepository", FakeStatsRepo)
        monkeypatch.setattr(
            "src.adapters.freewheel.adapter.get_db_session",
            lambda: __import__("contextlib").nullcontext(MagicMock()),
        )
        monkeypatch.setattr(adapter, "_refresh_nightly_forecasts_for_media_buy", refresh)

        date_range = ReportingPeriod(start=now, end=now)
        response = adapter.get_media_buy_delivery("freewheel_900002", date_range, now)

        refresh.assert_called_once_with("freewheel_900002")
        assert response.totals.impressions == 12_545.0
        assert response.totals.spend == 69.89
        assert {package.package_id for package in response.by_package} == {"900003", "900004"}
        assert response.ext is not None
        assert response.ext["partial_data"] is True


class TestClientConstruction:
    def test_client_composes_inventory_and_commercial(self):
        client = FreeWheelClient(api_token="test-bearer-token", base_url="https://api.stg.freewheel.tv")
        assert client.inventory is not None
        assert client.commercial is not None

    def test_client_token_info_calls_auth_endpoint(self):
        """token_info() proves the bearer is valid; uses /auth/token/info."""
        from src.adapters.freewheel._transport import FreeWheelTransport

        mock_session = MagicMock()
        mock_session.request.return_value = MagicMock(
            status_code=200,
            ok=True,
            content=b'{"user_id": 0, "expires_in": 604800, "created_at": 1700000000}',
            text='{"user_id": 0, "expires_in": 604800, "created_at": 1700000000}',
            json=lambda: {"user_id": 0, "expires_in": 604800, "created_at": 1700000000},
        )
        transport = FreeWheelTransport(api_token="t", session=mock_session)
        info = transport.token_info()

        assert info["expires_in"] == 604800
        call_kwargs = mock_session.request.call_args.kwargs
        assert call_kwargs["url"].endswith("/auth/token/info")
        assert call_kwargs["headers"]["Authorization"] == "Bearer t"
        assert call_kwargs["headers"]["accept"] == "application/json"


class TestGetAvailableInventory:
    """``get_available_inventory()`` surfaces the locally-synced FW taxonomy
    so the AI product configurator can recommend targeting without round-trips
    to the FW API."""

    @pytest.fixture
    def mock_inventory_rows(self):
        """Build a small set of FreeWheelInventory-shaped rows covering each
        entity_type the adapter consumes. Returned as MagicMocks so the test
        doesn't need a live DB."""

        def row(entity_type, entity_id, name, parent_id=None):
            r = MagicMock()
            r.entity_type = entity_type
            r.entity_id = entity_id
            r.name = name
            r.parent_id = parent_id
            return r

        return {
            "site": [row("site", "973371", "Talpa NL | Site"), row("site", "973372", "Sanoma NL | Site")],
            "site_section": [row("site_section", "12345", "Sports Section", parent_id="973371")],
            "video_group": [row("video_group", "1843152716", "Soccer Highlights")],
            "series": [row("series", "1824258494", "Soccer Show")],
            "ad_unit_package": [row("ad_unit_package", "51949", "Pre-Mid Bundle")],
            "standard_attribute": [
                row("standard_attribute", "1", "American English", parent_id="languages"),
                row("standard_attribute", "11", "TV-14", parent_id="tv_ratings"),
                row("standard_attribute", "100", "Action", parent_id="genres"),
            ],
        }

    @pytest.mark.asyncio
    async def test_returns_synced_inventory_shape(self, mock_principal, mock_inventory_rows, monkeypatch):
        """The adapter pulls from FreeWheelInventoryRepository, grouped by entity_type."""
        from src.adapters.freewheel import FreeWheelAdapter

        # Patch the repository so we don't need a real DB session.
        mock_repo = MagicMock()

        def list_by_type(entity_type, parent_id=None):
            return mock_inventory_rows.get(entity_type, [])

        mock_repo.list_by_type.side_effect = list_by_type

        from tests.helpers.freewheel_adapter_patches import patch_freewheel_db

        patch_freewheel_db(monkeypatch, mock_repo)

        adapter = FreeWheelAdapter(
            config={"api_token": "t"},
            principal=mock_principal,
            dry_run=True,
            tenant_id="t1",
        )
        inventory = await adapter.get_available_inventory()

        # ad_units = FW sites + site_sections (where ads can run)
        ad_unit_paths = [u["path"] for u in inventory["ad_units"]]
        assert "site:973371" in ad_unit_paths
        assert "site_section:12345" in ad_unit_paths

        # placements = FW ad_unit_packages (the buyer-facing inventory bundles)
        placement_ids = [p["id"] for p in inventory["placements"]]
        assert "ad_unit_package:51949" in placement_ids

        # targeting_options derived from standard_attributes, grouped by parent_id
        # (parent_id is the taxonomy key, e.g. "tv_ratings")
        assert "tv_ratings" in inventory["targeting_options"]
        assert any(opt["name"] == "TV-14" for opt in inventory["targeting_options"]["tv_ratings"])
        assert "genres" in inventory["targeting_options"]
        assert "languages" in inventory["targeting_options"]

        # creative_specs surfaces canonical VAST declarations; slot position is inventory targeting.
        assert len(inventory["creative_specs"]) == 2
        assert {s["format_id"]["id"] for s in inventory["creative_specs"]} == {"video_vast"}
        assert {s["format_id"]["duration_ms"] for s in inventory["creative_specs"]} == {15000, 30000}

        # properties carries network/inventory metadata
        assert inventory["properties"]["sites_count"] == 2
        assert inventory["properties"]["series_count"] == 1
        assert inventory["properties"]["video_groups_count"] == 1
