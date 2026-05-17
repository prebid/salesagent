"""Tests for B4: 4 adapters explicitly raise UNSUPPORTED_FEATURE on property_list.

AdCP #1302 contract 5: each enabled adapter MUST translate OR raise
UNSUPPORTED_FEATURE for unsupported targeting features. Mock/Broadstreet/Xandr/
Triton don't compile ``targeting_overlay.property_list`` today, so they must
return a clean ``UNSUPPORTED_FEATURE`` envelope rather than silently dropping.

The check lives in ``AdServerAdapter._check_property_list_supported`` so
adapters share one implementation and Kevel (B3) can opt in by setting
``supports_property_list_filtering = True``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from adcp.types import PropertyListReference

from src.adapters.broadstreet.adapter import BroadstreetAdapter
from src.adapters.mock_ad_server import MockAdServer
from src.adapters.triton_digital import TritonDigital
from src.adapters.xandr import XandrAdapter
from src.core.schemas import CreateMediaBuyError, CreateMediaBuyRequest, MediaPackage, Principal

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _principal(adapter_id_field: str = "advertiser_id") -> Principal:
    return Principal(
        principal_id="p_test",
        name="Test Principal",
        platform_mappings={
            "mock": {adapter_id_field: "mock_adv_1"},
            "google_ad_manager": {"advertiser_id": "gam_adv_1"},
            "broadstreet": {"advertiser_id": "broadstreet_adv_1"},
            "xandr": {"advertiser_id": "xandr_adv_1"},
            "triton": {"advertiser_id": "triton_adv_1"},
        },
    )


def _package_with_property_list(package_id: str = "pkg_pl") -> MediaPackage:
    """Build a MediaPackage carrying a PropertyListReference on targeting_overlay."""
    pkg = MagicMock(spec=MediaPackage)
    pkg.package_id = package_id
    pkg.product_id = "prod_pl"
    pkg.budget = Decimal("1000.00")
    pkg.pricing_model = "cpm"
    pkg.bid_price = None
    targeting = MagicMock()
    targeting.property_list = PropertyListReference(
        agent_url="https://gov.example",
        list_id="cb_list_001",
    )
    pkg.targeting_overlay = targeting
    return pkg


def _package_without_property_list(package_id: str = "pkg_clean") -> MediaPackage:
    pkg = MagicMock(spec=MediaPackage)
    pkg.package_id = package_id
    pkg.product_id = "prod_clean"
    pkg.budget = Decimal("1000.00")
    pkg.pricing_model = "cpm"
    pkg.bid_price = None
    targeting = MagicMock()
    targeting.property_list = None
    pkg.targeting_overlay = targeting
    return pkg


def _request() -> CreateMediaBuyRequest:
    # Minimal valid request — the adapter never reaches the real flow because
    # the property_list check returns early before any side effects.
    return MagicMock(spec=CreateMediaBuyRequest, po_number="po_test", brand=None)


# ---------------------------------------------------------------------------
# Base helper unit tests
# ---------------------------------------------------------------------------


class TestBaseHelperCheckPropertyListSupported:
    """AdServerAdapter._check_property_list_supported behavior."""

    def _instance(self, *, supports: bool):
        """Build a minimally-concrete adapter without instantiating any subclass.

        We can't trivially subclass ``AdServerAdapter`` (many abstract methods).
        Instead we instantiate ``MockAdServer`` — the smallest concrete subclass —
        and patch the class attribute on a per-instance basis via ``object.__setattr__``
        on a freshly-constructed subclass to test the helper's branching logic
        without the rest of the Mock adapter doing anything.
        """
        adapter = MockAdServer(config={}, principal=_principal(), tenant_id="t_test")
        # Override the class attribute on a unique subclass so we don't bleed
        # state between tests (mutating MockAdServer.supports_property_list_filtering
        # would leak into TestMockAdapterRejectsPropertyList).
        new_cls = type("_TestAdapter", (MockAdServer,), {"supports_property_list_filtering": supports})
        adapter.__class__ = new_cls
        return adapter

    def test_returns_none_when_property_list_absent(self):
        adapter = self._instance(supports=False)
        result = adapter._check_property_list_supported([_package_without_property_list()])
        assert result is None

    def test_returns_error_when_property_list_present_and_unsupported(self):
        adapter = self._instance(supports=False)
        result = adapter._check_property_list_supported([_package_with_property_list()])

        assert isinstance(result, CreateMediaBuyError)
        assert len(result.errors) == 1
        assert result.errors[0].code == "UNSUPPORTED_FEATURE"
        assert "property_list" in result.errors[0].message

    def test_returns_none_when_adapter_advertises_native_support(self):
        adapter = self._instance(supports=True)
        result = adapter._check_property_list_supported([_package_with_property_list()])
        assert result is None, "Adapters with supports_property_list_filtering=True must accept property_list"

    def test_error_message_names_the_adapter(self):
        adapter = self._instance(supports=False)
        result = adapter._check_property_list_supported([_package_with_property_list()])
        assert result is not None
        # MockAdServer subclass — error message should name it
        assert "mock" in result.errors[0].message.lower() or "Mock" in result.errors[0].message

    def test_iterates_all_packages_until_first_violation(self):
        adapter = self._instance(supports=False)
        result = adapter._check_property_list_supported(
            [_package_without_property_list("clean"), _package_with_property_list("violator")]
        )
        assert result is not None
        assert result.errors[0].code == "UNSUPPORTED_FEATURE"


# ---------------------------------------------------------------------------
# Per-adapter regression tests
# ---------------------------------------------------------------------------


class TestMockAdapterRejectsPropertyList:
    """Mock adapter has no property_list compilation path → UNSUPPORTED_FEATURE."""

    def test_class_default_unsupported(self):
        assert MockAdServer.supports_property_list_filtering is False

    def test_create_media_buy_returns_unsupported_envelope(self):
        adapter = MockAdServer(config={}, principal=_principal(), tenant_id="t_mock")
        result = adapter.create_media_buy(
            request=_request(),
            packages=[_package_with_property_list()],
            start_time=datetime(2026, 1, 1, tzinfo=UTC),
            end_time=datetime(2026, 1, 31, tzinfo=UTC),
        )

        assert isinstance(result, CreateMediaBuyError)
        assert result.errors[0].code == "UNSUPPORTED_FEATURE"
        assert "MockAdServer" in result.errors[0].message or "mock" in result.errors[0].message.lower()


class TestBroadstreetAdapterRejectsPropertyList:
    """Broadstreet is single-network → no native property_list path → UNSUPPORTED_FEATURE."""

    def test_class_default_unsupported(self):
        assert BroadstreetAdapter.supports_property_list_filtering is False


class TestXandrAdapterRejectsPropertyList:
    """Xandr adapter is stubbed → preventive UNSUPPORTED_FEATURE raise."""

    def test_class_default_unsupported(self):
        assert XandrAdapter.supports_property_list_filtering is False


class TestTritonAdapterRejectsPropertyList:
    """Triton: audio-type accept path is gated on B3's resolver.

    Until the property_list → identifier resolver lands, Triton rejects all
    property_list targeting with UNSUPPORTED_FEATURE. The class attribute will
    flip to True (with an override of _check_property_list_supported that
    inspects identifier types) once B3 wires the resolver.
    """

    def test_class_default_unsupported(self):
        assert TritonDigital.supports_property_list_filtering is False
