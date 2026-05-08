"""Tests for the guaranteed-line-item pre-flight check on update_media_buy.

Covers tescoboy issue #156: pre-fix, an `update_media_buy` against a
GAM-guaranteed line item (`STANDARD` / `SPONSORSHIP`) succeeded against
the Order in GAM but was rejected on the LineItem leg with
`UPDATE_RESERVATION_NOT_ALLOWED` after ~3 min of `NO_FORECAST_YET`
retries. Result: Order new + LineItem stale + DB new — three-way drift.

The fix is a pre-flight check that returns
`code='guaranteed_line_item_immutable'` before any GAM mutation when
the request touches a reservation field on a guaranteed product. These
tests target `_check_guaranteed_immutable` directly with mocked
`uow.media_buys.get_packages` and a mocked SQLAlchemy session.
"""

from unittest.mock import MagicMock

from src.core.schemas import UpdateMediaBuyError, UpdateMediaBuyRequest
from src.core.tools.media_buy_update import _check_guaranteed_immutable


def _make_uow(packages):
    uow = MagicMock()
    uow.media_buys = MagicMock()
    uow.media_buys.get_packages.return_value = packages
    return uow


def _make_session(products):
    """Build a session whose `scalars().all()` returns the given products."""
    scalars = MagicMock()
    scalars.all.return_value = products
    session = MagicMock()
    session.scalars.return_value = scalars
    return session


def _pkg(product_id):
    p = MagicMock()
    p.package_config = {"product_id": product_id}
    return p


def _product(product_id, line_item_type):
    p = MagicMock()
    p.product_id = product_id
    p.implementation_config = {"line_item_type": line_item_type} if line_item_type else None
    return p


class TestGuaranteedTypesBlockReservationFields:
    def test_standard_blocks_start_time_change(self):
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", start_time="2026-06-01T00:00:00Z")
        uow = _make_uow([_pkg("prod_1")])
        session = _make_session([_product("prod_1", "STANDARD")])

        result = _check_guaranteed_immutable(req, "mb_1", uow, session, "t_1")

        assert isinstance(result, UpdateMediaBuyError)
        assert len(result.errors) == 1
        err = result.errors[0]
        assert err.code == "guaranteed_line_item_immutable"
        assert err.details == {"line_item_type": "STANDARD", "blocked_fields": ["start_time"]}
        assert "STANDARD" in err.message

    def test_sponsorship_blocks_end_time_change(self):
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", end_time="2026-06-01T00:00:00Z")
        uow = _make_uow([_pkg("prod_1")])
        session = _make_session([_product("prod_1", "SPONSORSHIP")])

        result = _check_guaranteed_immutable(req, "mb_1", uow, session, "t_1")

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].details["line_item_type"] == "SPONSORSHIP"
        assert result.errors[0].details["blocked_fields"] == ["end_time"]

    def test_standard_blocks_budget_change(self):
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", budget=5000.0)
        uow = _make_uow([_pkg("prod_1")])
        session = _make_session([_product("prod_1", "STANDARD")])

        result = _check_guaranteed_immutable(req, "mb_1", uow, session, "t_1")

        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].details["blocked_fields"] == ["budget"]

    def test_multiple_reservation_fields_listed_alphabetically(self):
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_1",
            start_time="2026-06-01T00:00:00Z",
            end_time="2026-07-01T00:00:00Z",
        )
        uow = _make_uow([_pkg("prod_1")])
        session = _make_session([_product("prod_1", "STANDARD")])

        result = _check_guaranteed_immutable(req, "mb_1", uow, session, "t_1")

        assert result.errors[0].details["blocked_fields"] == ["end_time", "start_time"]

    def test_one_guaranteed_package_blocks_even_if_others_non_guaranteed(self):
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", start_time="2026-06-01T00:00:00Z")
        uow = _make_uow([_pkg("prod_1"), _pkg("prod_2")])
        session = _make_session(
            [
                _product("prod_1", "NETWORK"),
                _product("prod_2", "STANDARD"),
            ]
        )

        result = _check_guaranteed_immutable(req, "mb_1", uow, session, "t_1")
        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].details["line_item_type"] == "STANDARD"


class TestNonReservationFieldsAllowed:
    def test_paused_field_does_not_trigger_check(self):
        # `paused` is not in _RESERVATION_FIELDS — guaranteed buys can be
        # paused/resumed even though they can't have flight bounds changed.
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", paused=True)
        uow = _make_uow([_pkg("prod_1")])
        session = _make_session([_product("prod_1", "STANDARD")])

        result = _check_guaranteed_immutable(req, "mb_1", uow, session, "t_1")
        assert result is None
        # Without reservation fields requested, the helper short-circuits
        # and never calls the package or product lookup.
        uow.media_buys.get_packages.assert_not_called()

    def test_no_fields_requested_short_circuits(self):
        req = UpdateMediaBuyRequest(media_buy_id="mb_1")
        uow = _make_uow([_pkg("prod_1")])
        session = _make_session([_product("prod_1", "STANDARD")])

        result = _check_guaranteed_immutable(req, "mb_1", uow, session, "t_1")
        assert result is None


class TestNonGuaranteedTypesPassThrough:
    def test_network_allows_start_time_change(self):
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", start_time="2026-06-01T00:00:00Z")
        uow = _make_uow([_pkg("prod_1")])
        session = _make_session([_product("prod_1", "NETWORK")])

        assert _check_guaranteed_immutable(req, "mb_1", uow, session, "t_1") is None

    def test_price_priority_allows_end_time_change(self):
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", end_time="2026-06-01T00:00:00Z")
        uow = _make_uow([_pkg("prod_1")])
        session = _make_session([_product("prod_1", "PRICE_PRIORITY")])

        assert _check_guaranteed_immutable(req, "mb_1", uow, session, "t_1") is None

    def test_unknown_type_default_allowed(self):
        # Default-allow on unknown types so future GAM additions don't
        # accidentally trip the guard.
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", start_time="2026-06-01T00:00:00Z")
        uow = _make_uow([_pkg("prod_1")])
        session = _make_session([_product("prod_1", "FUTURE_TYPE_GAM_ADDED")])

        assert _check_guaranteed_immutable(req, "mb_1", uow, session, "t_1") is None


class TestEdgeCases:
    def test_no_packages_does_not_block(self):
        # Without packages we have nothing to look up — pass through and
        # let downstream code surface the inconsistency.
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", start_time="2026-06-01T00:00:00Z")
        uow = _make_uow([])
        session = _make_session([])

        assert _check_guaranteed_immutable(req, "mb_1", uow, session, "t_1") is None

    def test_package_without_product_id_skipped(self):
        # Package_config without a product_id is not actionable here.
        bad_pkg = MagicMock()
        bad_pkg.package_config = {}
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", start_time="2026-06-01T00:00:00Z")
        uow = _make_uow([bad_pkg])
        session = _make_session([])

        assert _check_guaranteed_immutable(req, "mb_1", uow, session, "t_1") is None

    def test_product_without_implementation_config_treated_as_unknown(self):
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", start_time="2026-06-01T00:00:00Z")
        uow = _make_uow([_pkg("prod_1")])
        session = _make_session([_product("prod_1", None)])

        assert _check_guaranteed_immutable(req, "mb_1", uow, session, "t_1") is None
