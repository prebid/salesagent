"""Unit coverage for the Mock adapter's server-side delivery seeding (#1418).

The adapter reads a per-(tenant, media_buy) row FIRST and returns it verbatim;
absent a row its existing in-memory / fallback behavior is unchanged. These
tests mock ``get_db_session`` (unit convention) and assert the read short-circuit
plus the payload roundtrip; the real-Postgres path is covered in
``tests/integration/test_delivery_simulation_config.py``.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from src.adapters.mock_ad_server import MockAdServer
from src.core.schemas import Principal
from src.core.schemas.delivery import (
    AdapterGetMediaBuyDeliveryResponse,
    AdapterPackageDelivery,
    DeliveryTotals,
    ReportingPeriod,
)


def _principal() -> Principal:
    return Principal(principal_id="principal_test", name="Test Principal", platform_mappings={})


def _adapter(tenant_id: str | None = "tenant_test") -> MockAdServer:
    return MockAdServer(config={}, principal=_principal(), tenant_id=tenant_id)


def _seeded_response(media_buy_id: str = "mb_seed") -> AdapterGetMediaBuyDeliveryResponse:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return AdapterGetMediaBuyDeliveryResponse(
        media_buy_id=media_buy_id,
        reporting_period=ReportingPeriod(start=start, end=start + timedelta(days=7)),
        totals=DeliveryTotals(impressions=5000, spend=50.0, clicks=25),
        by_package=[AdapterPackageDelivery(package_id="pkg_001", impressions=5000, spend=50.0)],
        currency="USD",
    )


@contextmanager
def _patched_session(row):
    """Patch get_db_session so the repository .get() returns ``row``."""
    fake_session = MagicMock()

    @contextmanager
    def _cm():
        yield fake_session

    with (
        patch("src.core.database.database_session.get_db_session", _cm),
        patch("src.core.database.repositories.delivery_simulation.DeliverySimulationConfigRepository") as repo_cls,
    ):
        repo_cls.return_value.get.return_value = row
        yield fake_session, repo_cls


class TestDeliverySimulationRead:
    def test_row_present_returns_seeded_response_exactly(self):
        """A seeded row is returned verbatim — in-memory _media_buys is not consulted."""
        adapter = _adapter()
        seeded = _seeded_response("mb_seed")
        row = MagicMock()
        row.response_payload = seeded.model_dump(mode="json")

        # Poison the in-memory path: if it were consulted, totals would differ.
        adapter._media_buys = {}

        with _patched_session(row) as (_session, repo_cls):
            today = datetime(2026, 1, 5, tzinfo=UTC)
            result = adapter.get_media_buy_delivery(
                "mb_seed",
                ReportingPeriod(start=datetime(2026, 1, 1, tzinfo=UTC), end=today),
                today,
            )

        repo_cls.assert_called_once_with(_session_arg(repo_cls), "tenant_test")
        assert result.media_buy_id == "mb_seed"
        assert result.totals.impressions == 5000
        assert result.totals.spend == 50.0
        assert result.currency == "USD"
        assert len(result.by_package) == 1
        assert result.by_package[0].package_id == "pkg_001"
        assert result.by_package[0].impressions == 5000

    def test_row_absent_falls_through_to_legacy_behavior(self):
        """No seeded row -> existing fallback path runs (random fill, empty by_package)."""
        adapter = _adapter()
        adapter._media_buys = {}

        with _patched_session(None):
            today = datetime(2026, 1, 5, tzinfo=UTC)
            result = adapter.get_media_buy_delivery(
                "mb_unseeded",
                ReportingPeriod(start=datetime(2026, 1, 1, tzinfo=UTC), end=today),
                today,
            )

        # Legacy fallback returns a valid response with random impressions and no packages.
        assert result.media_buy_id == "mb_unseeded"
        assert result.by_package == []
        assert result.totals.impressions >= 0

    def test_no_tenant_id_skips_db_read(self):
        """tenant_id falsy -> no repository / session is touched.

        The base adapter requires a tenant_id at construction, so we null it
        afterwards to exercise the defensive guard in _load_delivery_simulation.
        """
        adapter = _adapter()
        adapter.tenant_id = None
        # Force the legacy fallback by leaving _media_buys empty.
        adapter._media_buys = {}

        with patch("src.core.database.database_session.get_db_session") as get_session:
            today = datetime(2026, 1, 5, tzinfo=UTC)
            result = adapter.get_media_buy_delivery(
                "mb_no_tenant",
                ReportingPeriod(start=datetime(2026, 1, 1, tzinfo=UTC), end=today),
                today,
            )
            get_session.assert_not_called()
        assert result.media_buy_id == "mb_no_tenant"


class TestPayloadRoundtrip:
    def test_model_dump_json_then_validate_is_lossless(self):
        """The stored wire dump round-trips back into an identical response."""
        original = _seeded_response("mb_round")
        dumped = original.model_dump(mode="json")
        restored = AdapterGetMediaBuyDeliveryResponse.model_validate(dumped)

        assert restored.media_buy_id == original.media_buy_id
        assert restored.reporting_period.start == original.reporting_period.start
        assert restored.reporting_period.end == original.reporting_period.end
        assert restored.totals.impressions == original.totals.impressions
        assert restored.totals.spend == original.totals.spend
        assert restored.totals.clicks == original.totals.clicks
        assert restored.currency == original.currency
        assert [p.package_id for p in restored.by_package] == [p.package_id for p in original.by_package]


def _session_arg(repo_cls):
    """The session object the repository was constructed with."""
    return repo_cls.call_args.args[0]
