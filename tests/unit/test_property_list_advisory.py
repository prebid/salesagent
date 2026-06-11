"""Unit pins for the property_list zero-overlap advisory in ``_create_media_buy_impl``.

``_build_property_list_advisories`` is accept-with-CONTEXT: zero overlap
between a buyer's ``property_list`` and a package's product RETURNS buyer-
visible ``Error`` advisories (the caller attaches them to the create response)
and logs an operator WARNING. It NEVER raises — intersection errors are
swallowed so ``create_media_buy`` proceeds; advisory computation must never
veto a booking.

Buyer property_lists are resolved over HTTP by
``_resolve_property_list_identifiers`` BEFORE the validation transaction opens
(so no DB connection is held across a network call, and the event loop isn't
blocked). That pre-fetch is also best-effort — a failed resolution is logged
and omitted, and the advisory simply skips a list it didn't receive.

These pins cover the helper's return contract; the envelope attachment and
wire shape are pinned by the create-side integration/wire tests.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from adcp.types import PropertyListReference

from src.core.tools.media_buy_create import (
    _build_property_list_advisories,
    _resolve_property_list_identifiers,
)
from src.services.property_intersection import DroppedProduct, DropReason, IntersectionResult
from tests.helpers.adcp_factories import create_test_identifiers as _buyers

pytestmark = pytest.mark.unit

_LOGGER = "src.core.tools.media_buy_create"
# The pre-fetch helper imports the async resolver lazily from its source module.
_RESOLVE_ASYNC = "src.core.property_list_resolver.resolve_property_list_typed"
_FILTER = "src.services.property_intersection.PropertyIntersection.filter_products"
# The advisory converts each ORM product to its schema form before intersecting;
# these control-flow unit tests use mock products, so the conversion is stubbed to
# identity. The real ORM-product → schema → intersection path is covered by an
# integration test in tests/integration/test_product_property_list_filtering.py.
_CONVERT = "src.core.product_conversion.convert_product_model_to_schema"


def _package(product_id: str, *, with_property_list: bool = True) -> MagicMock:
    pkg = MagicMock()
    pkg.product_id = product_id
    pkg.package_id = f"pkg_{product_id}"
    pkg.targeting_overlay = MagicMock()
    pkg.targeting_overlay.property_list = (
        PropertyListReference(agent_url="https://gov.example", list_id="L1") if with_property_list else None
    )
    return pkg


def _allowed_key(pkg: MagicMock) -> tuple[str, str]:
    """The (agent_url, list_id) key the advisory uses, computed from a package's ref."""
    ref = pkg.targeting_overlay.property_list
    return (str(ref.agent_url), ref.list_id)


class TestEmitPropertyListAdvisories:
    def test_zero_overlap_returns_buyer_advisory_and_logs(self, caplog):
        """Zero-overlap is accept-with-CONTEXT: a buyer-visible Error advisory is
        returned (the caller attaches it to the success envelope's errors[]) and
        the operator WARNING is still logged. Never a raise."""
        product = MagicMock()
        product.product_id = "p1"
        pkg = _package("p1")
        zero = IntersectionResult(
            kept_products=(),
            dropped_products=(DroppedProduct(product=product, reason=DropReason.NO_PROPERTY_OVERLAP),),
        )
        with (
            patch(_CONVERT, side_effect=lambda p: p),
            patch(_FILTER, return_value=zero),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            advisories = _build_property_list_advisories(
                [pkg], {"p1": product}, MagicMock(), {_allowed_key(pkg): _buyers("nomatch.example")}
            )

        assert len(advisories) == 1
        advisory = advisories[0]
        assert advisory.code == "PRODUCT_UNAVAILABLE"
        assert "zero overlap" in advisory.message and "p1" in advisory.message
        assert advisory.field == "packages[0].targeting_overlay.property_list"
        assert advisory.suggestion is not None
        assert advisory.details == {
            "product_id": "p1",
            "package_id": "pkg_p1",
            "reason": "no_property_overlap",
            "list_id": "L1",
        }

        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "INTERSECTION-ADVISORY" in m and "zero overlap" in m and "no_property_overlap" in m for m in messages
        ), f"expected a zero-overlap advisory WARNING; got {messages}"

    def test_intersection_exception_is_swallowed(self, caplog):
        """An intersection failure must be swallowed so the buy proceeds — never raised."""
        product = MagicMock()
        product.product_id = "p1"
        pkg = _package("p1")
        with (
            patch(_CONVERT, side_effect=lambda p: p),
            patch(_FILTER, side_effect=RuntimeError("intersection boom")),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            # Must not raise despite the intersection blowing up; no advisory emitted.
            assert (
                _build_property_list_advisories([pkg], {"p1": product}, MagicMock(), {_allowed_key(pkg): _buyers("x")})
                == []
            )

        messages = [r.getMessage() for r in caplog.records]
        assert any("INTERSECTION-ADVISORY" in m and "Intersection failed" in m for m in messages), (
            f"expected a swallowed-intersection WARNING; got {messages}"
        )

    def test_unresolved_list_is_skipped(self, caplog):
        """A package whose list isn't in resolved_allowed_sets (fetch failed earlier) is skipped, no intersection."""
        product = MagicMock()
        product.product_id = "p1"
        pkg = _package("p1")
        with (
            patch(_FILTER) as filt,
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            # Empty resolved map → nothing to intersect, no filter call, no advisory.
            assert _build_property_list_advisories([pkg], {"p1": product}, MagicMock(), {}) == []
        filt.assert_not_called()
        assert not [r for r in caplog.records if "INTERSECTION-ADVISORY" in r.getMessage()]

    def test_no_property_list_is_a_noop(self, caplog):
        """Packages without property_list are skipped: no intersection, no advisory log."""
        product = MagicMock()
        product.product_id = "p1"
        with (
            patch(_FILTER) as filt,
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            assert (
                _build_property_list_advisories(
                    [_package("p1", with_property_list=False)], {"p1": product}, MagicMock(), {}
                )
                == []
            )
        filt.assert_not_called()
        assert not [r for r in caplog.records if "INTERSECTION-ADVISORY" in r.getMessage()]


class TestResolvePropertyListAllowedSets:
    """The pre-fetch helper resolves buyer lists async, best-effort, keyed by (agent_url, list_id)."""

    @pytest.mark.asyncio
    async def test_resolves_to_identifier_value_set(self):
        pkg = _package("p1")
        with patch(
            _RESOLVE_ASYNC,
            new=AsyncMock(return_value=_buyers("espn.com", "cnn.com")),
        ):
            resolved = await _resolve_property_list_identifiers([pkg])

        assert {(i.type.value, i.value) for i in resolved[_allowed_key(pkg)]} == {
            ("domain", "espn.com"),
            ("domain", "cnn.com"),
        }

    @pytest.mark.asyncio
    async def test_resolution_failure_is_swallowed_and_omitted(self, caplog):
        """A fetch failure is logged and the list is omitted — never raised (best-effort)."""
        pkg = _package("p1")
        with (
            patch(_RESOLVE_ASYNC, new=AsyncMock(side_effect=RuntimeError("agent unreachable"))),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            resolved = await _resolve_property_list_identifiers([pkg])

        assert resolved == {}
        messages = [r.getMessage() for r in caplog.records]
        assert any("INTERSECTION-ADVISORY" in m and "Failed to resolve" in m for m in messages), (
            f"expected a swallowed-resolution WARNING; got {messages}"
        )

    @pytest.mark.asyncio
    async def test_no_property_list_yields_empty_without_fetch(self):
        """Packages without property_list trigger no resolver call and an empty result."""
        with patch(_RESOLVE_ASYNC, new=AsyncMock()) as resolve:
            resolved = await _resolve_property_list_identifiers([_package("p1", with_property_list=False)])
        assert resolved == {}
        resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_same_list_fetched_once_across_packages(self):
        """Two packages referencing the same list resolve it a single time."""
        pkg_a = _package("p1")
        pkg_b = _package("p2")  # same agent_url/list_id as pkg_a via _package defaults
        resolve = AsyncMock(return_value=_buyers("espn.com"))
        with patch(_RESOLVE_ASYNC, new=resolve):
            resolved = await _resolve_property_list_identifiers([pkg_a, pkg_b])

        assert list(resolved) == [_allowed_key(pkg_a)]
        assert [i.value for i in resolved[_allowed_key(pkg_a)]] == ["espn.com"]
        assert resolve.await_count == 1


class TestSuccessMessageSurfacesAdvisories:
    """``str(CreateMediaBuySuccess)`` carries the advisory text for BOTH ext shapes.

    The protocol ``message`` on every transport derives from ``str(response)``;
    the advisory silently vanishing is the storyboard's named not-acceptable
    outcome. ``ext`` is an ExtensionObject model on the construction path but
    can be a plain dict (e.g. ``model_construct`` / round-trip shapes) — both
    must surface the text.
    """

    _EXT = {
        "prebid": {
            "property_list_advisories": [
                {"code": "PRODUCT_UNAVAILABLE", "message": "list has zero overlap with product prod_1"}
            ]
        }
    }

    def test_model_ext_surfaces_in_message(self):
        from src.core.schemas import CreateMediaBuySuccess

        response = CreateMediaBuySuccess(media_buy_id="mb_adv_1", packages=[], ext=self._EXT)
        assert "zero overlap" in str(response)

    def test_plain_dict_ext_surfaces_in_message(self):
        from src.core.schemas import CreateMediaBuySuccess

        response = CreateMediaBuySuccess.model_construct(media_buy_id="mb_adv_2", packages=[], ext=self._EXT)
        assert isinstance(response.ext, dict), "precondition: ext must be the uncoerced dict shape"
        assert "zero overlap" in str(response)
