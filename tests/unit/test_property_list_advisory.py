"""Unit pins for the B5 property_list zero-overlap advisory in ``_create_media_buy_impl``.

``_emit_property_list_advisories`` is accept-with-context (inventory-targeting
plan SD2): it logs a WARNING when a buyer's ``property_list`` has zero overlap
with a package's product, and it NEVER raises — intersection errors are
swallowed so ``create_media_buy`` proceeds (the adapter boundary emits the
user-facing envelope: Kevel resolves to empty siteIds, B4 adapters raise
UNSUPPORTED_FEATURE).

Buyer property_lists are resolved over HTTP by
``_resolve_property_list_allowed_sets`` BEFORE the validation transaction opens
(so no DB connection is held across a network call, and the event loop isn't
blocked). That pre-fetch is also best-effort — a failed resolution is logged
and omitted, and the advisory simply skips a list it didn't receive.

Without these pins, reverting the advisory to raise — or deleting the call —
would not turn any test red.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from adcp.types import Identifier, PropertyIdentifierTypes, PropertyListReference

from src.core.tools.media_buy_create import (
    _emit_property_list_advisories,
    _resolve_property_list_allowed_sets,
)
from src.services.property_intersection import DroppedProduct, DropReason, IntersectionResult

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
    def test_zero_overlap_logs_warning_and_does_not_raise(self, caplog):
        """Zero-overlap is surfaced as an advisory WARNING, not a raise (SD2 accept-with-context)."""
        product = MagicMock()
        product.product_id = "p1"
        pkg = _package("p1")
        zero = IntersectionResult(
            kept_products=[],
            dropped_products=[DroppedProduct(product=product, reason=DropReason.NO_PROPERTY_OVERLAP)],
        )
        with (
            patch(_CONVERT, side_effect=lambda p: p),
            patch(_FILTER, return_value=zero),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            # Must return None without raising.
            assert (
                _emit_property_list_advisories(
                    [pkg], {"p1": product}, MagicMock(), {_allowed_key(pkg): {"nomatch.example"}}
                )
                is None
            )

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
            # Must not raise despite the intersection blowing up.
            assert (
                _emit_property_list_advisories([pkg], {"p1": product}, MagicMock(), {_allowed_key(pkg): {"x"}}) is None
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
            # Empty resolved map → nothing to intersect, no filter call, no advisory log.
            assert _emit_property_list_advisories([pkg], {"p1": product}, MagicMock(), {}) is None
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
                _emit_property_list_advisories(
                    [_package("p1", with_property_list=False)], {"p1": product}, MagicMock(), {}
                )
                is None
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
            new=AsyncMock(
                return_value=[
                    Identifier(type=PropertyIdentifierTypes.domain, value="espn.com"),
                    Identifier(type=PropertyIdentifierTypes.domain, value="cnn.com"),
                ]
            ),
        ):
            resolved = await _resolve_property_list_allowed_sets([pkg])

        assert resolved == {_allowed_key(pkg): {"espn.com", "cnn.com"}}

    @pytest.mark.asyncio
    async def test_resolution_failure_is_swallowed_and_omitted(self, caplog):
        """A fetch failure is logged and the list is omitted — never raised (best-effort)."""
        pkg = _package("p1")
        with (
            patch(_RESOLVE_ASYNC, new=AsyncMock(side_effect=RuntimeError("agent unreachable"))),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            resolved = await _resolve_property_list_allowed_sets([pkg])

        assert resolved == {}
        messages = [r.getMessage() for r in caplog.records]
        assert any("INTERSECTION-ADVISORY" in m and "Failed to resolve" in m for m in messages), (
            f"expected a swallowed-resolution WARNING; got {messages}"
        )

    @pytest.mark.asyncio
    async def test_no_property_list_yields_empty_without_fetch(self):
        """Packages without property_list trigger no resolver call and an empty result."""
        with patch(_RESOLVE_ASYNC, new=AsyncMock()) as resolve:
            resolved = await _resolve_property_list_allowed_sets([_package("p1", with_property_list=False)])
        assert resolved == {}
        resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_same_list_fetched_once_across_packages(self):
        """Two packages referencing the same list resolve it a single time."""
        pkg_a = _package("p1")
        pkg_b = _package("p2")  # same agent_url/list_id as pkg_a via _package defaults
        resolve = AsyncMock(return_value=[Identifier(type=PropertyIdentifierTypes.domain, value="espn.com")])
        with patch(_RESOLVE_ASYNC, new=resolve):
            resolved = await _resolve_property_list_allowed_sets([pkg_a, pkg_b])

        assert resolved == {_allowed_key(pkg_a): {"espn.com"}}
        assert resolve.await_count == 1
