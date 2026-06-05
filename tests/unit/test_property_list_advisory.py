"""Unit pins for the B5 property_list zero-overlap advisory in ``_create_media_buy_impl``.

``_emit_property_list_advisories`` is accept-with-context (inventory-targeting
plan SD2): it logs a WARNING when a buyer's ``property_list`` has zero overlap
with a package's product, and it NEVER raises — resolution/intersection errors
are swallowed so ``create_media_buy`` proceeds (the adapter boundary emits the
user-facing envelope: Kevel resolves to empty siteIds, B4 adapters raise
UNSUPPORTED_FEATURE).

Without these pins, reverting the advisory to raise — or deleting the call —
would not turn any test red.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from adcp.types import Identifier, PropertyIdentifierTypes, PropertyListReference

from src.core.tools.media_buy_create import _emit_property_list_advisories
from src.services.property_intersection import DroppedProduct, DropReason, IntersectionResult

pytestmark = pytest.mark.unit

_LOGGER = "src.core.tools.media_buy_create"
# The advisory imports these lazily from their source modules, so patch at the source.
_RESOLVE = "src.core.property_list_resolver.resolve_property_list_typed_sync"
_FILTER = "src.services.property_intersection.PropertyIntersection.filter_products"


def _package(product_id: str, *, with_property_list: bool = True) -> MagicMock:
    pkg = MagicMock()
    pkg.product_id = product_id
    pkg.package_id = f"pkg_{product_id}"
    pkg.targeting_overlay = MagicMock()
    pkg.targeting_overlay.property_list = (
        PropertyListReference(agent_url="https://gov.example", list_id="L1") if with_property_list else None
    )
    return pkg


class TestEmitPropertyListAdvisories:
    def test_zero_overlap_logs_warning_and_does_not_raise(self, caplog):
        """Zero-overlap is surfaced as an advisory WARNING, not a raise (SD2 accept-with-context)."""
        product = MagicMock()
        product.product_id = "p1"
        zero = IntersectionResult(
            kept_products=[],
            dropped_products=[DroppedProduct(product=product, reason=DropReason.NO_PROPERTY_OVERLAP)],
        )
        with (
            patch(_RESOLVE, return_value=[Identifier(type=PropertyIdentifierTypes.domain, value="nomatch.example")]),
            patch(_FILTER, return_value=zero),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            # Must return None without raising.
            assert _emit_property_list_advisories([_package("p1")], {"p1": product}, MagicMock()) is None

        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "INTERSECTION-ADVISORY" in m and "zero overlap" in m and "no_property_overlap" in m for m in messages
        ), f"expected a zero-overlap advisory WARNING; got {messages}"

    def test_resolver_exception_is_swallowed(self, caplog):
        """A resolution failure must be swallowed so the buy proceeds — never raised."""
        product = MagicMock()
        product.product_id = "p1"
        with (
            patch(_RESOLVE, side_effect=RuntimeError("agent unreachable")),
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            # Must not raise despite the resolver blowing up.
            assert _emit_property_list_advisories([_package("p1")], {"p1": product}, MagicMock()) is None

        messages = [r.getMessage() for r in caplog.records]
        assert any("INTERSECTION-ADVISORY" in m and "Failed to resolve" in m for m in messages), (
            f"expected a swallowed-resolution WARNING; got {messages}"
        )

    def test_no_property_list_is_a_noop(self, caplog):
        """Packages without property_list are skipped: no resolve call, no advisory log."""
        product = MagicMock()
        product.product_id = "p1"
        with (
            patch(_RESOLVE) as resolve,
            caplog.at_level(logging.WARNING, logger=_LOGGER),
        ):
            assert (
                _emit_property_list_advisories([_package("p1", with_property_list=False)], {"p1": product}, MagicMock())
                is None
            )
        resolve.assert_not_called()
        assert not [r for r in caplog.records if "INTERSECTION-ADVISORY" in r.getMessage()]
