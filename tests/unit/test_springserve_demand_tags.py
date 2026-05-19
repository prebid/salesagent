"""Tests for SpringServeDemandTagsClient -- typed CRUD over /demand_tags."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from src.adapters.springserve._demand_tags import (
    SpringServeDemandTagsClient,
    _format_ss_datetime,
)
from src.adapters.springserve.entities import DemandTag


@pytest.fixture
def transport():
    return MagicMock()


@pytest.fixture
def client(transport):
    return SpringServeDemandTagsClient(transport)


def _demand_tag_response(demand_tag_id: int = 800001, **overrides) -> dict:
    body = {
        "id": demand_tag_id,
        "campaign_id": 900001,
        "account_id": 1730,
        "demand_partner_id": 88061,
        "name": "adcp_pkg",
        "active": False,
        "is_active": False,
        "rate_currency": "EUR",
        "cost_model_type": 0,
        "format": "video",
        "demand_tag_priorities": [],
        "budgets": [],
        "country_codes": [],
        "country_targeting": "All",
        "state_codes": [],
        "state_targeting": "All",
        "metro_area_codes": [],
        "metro_area_targeting": "All",
        "player_sizes": [],
        "player_size_targeting": "All",
        "user_agent_devices": [],
        "line_item_ratios": [],
    }
    body.update(overrides)
    return body


class TestDatetimeFormat:
    def test_naive_datetime_appends_z(self):
        result = _format_ss_datetime(datetime(2026, 2, 10, 0, 0, 0))
        assert result == "2026-02-10T00:00:00.000000Z"

    def test_aware_datetime_converted_to_utc_z(self):
        """SpringServe uses literal Z suffix for UTC. Aware datetimes get
        converted to UTC; the tzinfo is then dropped before formatting."""
        from datetime import timedelta, timezone

        eastern = timezone(timedelta(hours=-5))
        result = _format_ss_datetime(datetime(2026, 2, 10, 5, 0, 0, tzinfo=eastern))
        assert result == "2026-02-10T10:00:00.000000Z"


class TestCreate:
    def test_required_fields_with_defaults(self, client, transport):
        transport.post_json.return_value = _demand_tag_response()
        start = datetime(2026, 6, 1, tzinfo=UTC)
        end = datetime(2026, 6, 30, tzinfo=UTC)

        result = client.create(
            name="adcp_pkg_1",
            campaign_id=900001,
            demand_partner_id=88061,
            start_date=start,
            end_date=end,
        )

        path, body = transport.post_json.call_args.args
        assert path == "/demand_tags"
        assert body["name"] == "adcp_pkg_1"
        assert body["campaign_id"] == 900001
        assert body["demand_partner_id"] == 88061
        assert body["start_date"] == "2026-06-01T00:00:00.000000Z"
        assert body["end_date"] == "2026-06-30T00:00:00.000000Z"
        assert body["format"] == "video"
        assert body["rate_currency"] == "USD"
        # SpringServe write API uses ``active`` (not ``is_active``); writing
        # ``is_active`` is silently ignored and tag comes back active.
        assert body["active"] is False
        assert "is_active" not in body
        assert isinstance(result, DemandTag)

    def test_rate_encoded_as_string(self, client, transport):
        """SpringServe stores rate as a string -- the client coerces."""
        transport.post_json.return_value = _demand_tag_response()
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
            rate=27.0,
        )
        body = transport.post_json.call_args.args[1]
        assert body["rate"] == "27.0"

    def test_country_codes_emit_include_targeting(self, client, transport):
        """SpringServe wants ``country_targeting: "Include"`` on writes
        (not the ``"White List"`` value that reads suggest). Sending the
        wrong enum value gets HTTP 400 with a confusing
        ``is_country_white_list`` error message."""
        transport.post_json.return_value = _demand_tag_response()
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
            country_codes=["NL", "BE"],
        )
        body = transport.post_json.call_args.args[1]
        assert body["country_codes"] == ["NL", "BE"]
        assert body["country_targeting"] == "Include"

    def test_state_codes_emit_include_targeting(self, client, transport):
        transport.post_json.return_value = _demand_tag_response()
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
            state_codes=["NL-NH"],
        )
        body = transport.post_json.call_args.args[1]
        assert body["state_codes"] == ["NL-NH"]
        assert body["state_targeting"] == "Include"

    def test_metro_codes_emit_include_targeting(self, client, transport):
        transport.post_json.return_value = _demand_tag_response()
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
            metro_area_codes=["501"],
        )
        body = transport.post_json.call_args.args[1]
        assert body["metro_area_codes"] == ["501"]
        assert body["metro_area_targeting"] == "Include"

    def test_player_sizes_emit_include_targeting(self, client, transport):
        transport.post_json.return_value = _demand_tag_response()
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
            player_sizes=["large"],
        )
        body = transport.post_json.call_args.args[1]
        assert body["player_sizes"] == ["large"]
        assert body["player_size_targeting"] == "Include"

    def test_audio_format_passthrough(self, client, transport):
        transport.post_json.return_value = _demand_tag_response(format="audio")
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
            format="audio",
        )
        body = transport.post_json.call_args.args[1]
        assert body["format"] == "audio"

    def test_demand_tag_priorities_pass_through(self, client, transport):
        transport.post_json.return_value = _demand_tag_response()
        priorities = [{"supply_tag_id": 945522, "priority": 1, "tier": 1}]
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
            demand_tag_priorities=priorities,
        )
        body = transport.post_json.call_args.args[1]
        assert body["demand_tag_priorities"] == priorities

    def test_extras_kwargs_merged(self, client, transport):
        transport.post_json.return_value = _demand_tag_response()
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
            skip_enabled=True,
            timeout=5000,
        )
        body = transport.post_json.call_args.args[1]
        assert body["skip_enabled"] is True
        assert body["timeout"] == 5000

    def test_demand_class_line_item_maps_to_id_on_wire(self, client, transport):
        """SpringServe identifies demand class by integer ID on the API body.
        Our internal Python enum uses snake_case; the wire shape uses the
        SpringServe class id."""
        transport.post_json.return_value = _demand_tag_response()
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
            demand_class="line_item",
        )
        body = transport.post_json.call_args.args[1]
        assert body["demand_class"] == 11

    def test_demand_class_tag_maps_to_id_on_wire(self, client, transport):
        transport.post_json.return_value = _demand_tag_response()
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
            demand_class="tag",
        )
        body = transport.post_json.call_args.args[1]
        assert body["demand_class"] == 1

    def test_demand_class_omitted_when_none(self, client, transport):
        """When the caller doesn't specify demand_class, the field is left
        off the body so SpringServe's account-default applies."""
        transport.post_json.return_value = _demand_tag_response()
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
        )
        body = transport.post_json.call_args.args[1]
        assert "demand_class" not in body


class TestGet:
    def test_returns_typed_demand_tag(self, client, transport):
        transport.get_json.return_value = _demand_tag_response(800042)
        result = client.get(800042)
        transport.get_json.assert_called_once_with("/demand_tags/800042")
        assert result.id == 800042


class TestUpdate:
    def test_is_active_toggle_writes_active_field(self, client, transport):
        """The public kwarg is ``is_active`` (matches read-side entity) but
        SpringServe's PUT API uses ``active``; writes through ``is_active``
        are silently ignored. We translate at the wire boundary."""
        transport.put_json.return_value = _demand_tag_response(is_active=True)
        client.update(800001, is_active=True)
        transport.put_json.assert_called_once_with("/demand_tags/800001", {"active": True})

    def test_update_dict_spread_is_active_also_translates_to_active(self, client, transport):
        """The original bug -- writes through ``is_active`` silently no-op'd
        on the wire -- is just as easy to hit via ``**entity.model_dump()``
        or any other dict-spread call site. Guard against the regression by
        proving the translation happens for both the named-kwarg path and
        the **fields passthrough."""
        transport.put_json.return_value = _demand_tag_response(is_active=False)
        client.update(800001, **{"is_active": False, "rate": "30.0"})
        transport.put_json.assert_called_once_with("/demand_tags/800001", {"active": False, "rate": "30.0"})

    def test_create_dict_spread_is_active_translates_to_active(self, client, transport):
        """Same guard on the create path -- a caller spreading
        ``**entity.model_dump()`` shouldn't be able to bypass the wire
        translation and silently produce an active demand tag."""
        transport.post_json.return_value = _demand_tag_response()
        client.create(
            name="x",
            campaign_id=1,
            demand_partner_id=2,
            start_date=datetime(2026, 6, 1, tzinfo=UTC),
            end_date=datetime(2026, 6, 30, tzinfo=UTC),
            **{"is_active": False},  # arrives via **extras
        )
        body = transport.post_json.call_args.args[1]
        assert body["active"] is False
        assert "is_active" not in body

    def test_arbitrary_fields_pass_through(self, client, transport):
        transport.put_json.return_value = _demand_tag_response()
        client.update(800001, rate="30.0", end_date="2026-07-31T00:00:00.000000Z")
        body = transport.put_json.call_args.args[1]
        assert body == {"rate": "30.0", "end_date": "2026-07-31T00:00:00.000000Z"}


class TestDelete:
    def test_delete_calls_delete_json(self, client, transport):
        client.delete(800001)
        transport.delete_json.assert_called_once_with("/demand_tags/800001")
