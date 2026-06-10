"""Pin tests: adapters without a compile path declare ``supports_property_list_targeting=False``.

The base class default is False (see ``src/adapters/base.py:195``). Adapters that
flip this to True without first compiling ``targeting_overlay.property_list``
into their ad-server payload would silently drop the field on every create —
breaking the honest-declaration contract that ``_create_media_buy_impl`` /
``_update_media_buy_impl`` enforce by raising ``AdCPCapabilityNotSupportedError``.

Until an adapter implements the compile path, its ClassVar must remain False
so the boundary check fires. Two adapters legitimately declare True: Kevel
compiles ``targeting_overlay.property_list`` to native ``siteIds`` (see
``test_kevel_property_list_compilation.py``), and MockAdServer's simulation
IS its compile path (the overlay persists and round-trips; there is no native
payload to drop the field from) — both are excluded from the False pin and
positively pinned below.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.adapters.base import AdServerAdapter
from src.adapters.kevel import Kevel
from src.adapters.mock_ad_server import MockAdServer
from src.core.exceptions import AdCPCapabilityNotSupportedError
from src.services.targeting_capabilities import raise_if_property_list_unsupported

# Positively-declared adapters (compile path exists): Kevel compiles to
# siteIds; MockAdServer's simulation is its compile path.
_DECLARED_CAPABLE = {Kevel, MockAdServer}


def _non_compiling_adapters() -> list[type[AdServerAdapter]]:
    """Every concrete adapter without a declared compile path — derived, not
    hand-enumerated, so adapter #7 joins the False pin automatically."""
    return sorted(
        (cls for cls in AdServerAdapter.__subclasses__() if cls not in _DECLARED_CAPABLE),
        key=lambda cls: cls.__name__,
    )


@pytest.mark.parametrize(
    "adapter_cls",
    _non_compiling_adapters(),
    ids=lambda cls: cls.__name__,
)
def test_adapter_does_not_advertise_property_list_targeting_support(
    adapter_cls: type[AdServerAdapter],
) -> None:
    """No concrete adapter sets ``supports_property_list_targeting=True``.

    If you need to flip this to True, you MUST first ship a compile path for
    ``targeting_overlay.property_list`` into your adapter's create payload.
    Otherwise the boundary check in ``_create_media_buy_impl`` will be
    silently bypassed and buyers' property_list filters will be dropped.
    """
    assert adapter_cls.supports_property_list_targeting is False, (
        f"{adapter_cls.__name__}.supports_property_list_targeting is True but this "
        f"adapter has no property_list compile path into its ad-server payload. "
        f"Implement the compile path before flipping this ClassVar."
    )


def test_mock_adapter_declares_property_list_targeting_support() -> None:
    """MockAdServer's simulation is its compile path — the declaration is honest.

    The simulated server persists targeting_overlay.property_list via _impl and
    round-trips it through get_media_buys; declaring support makes the
    inventory_list storyboards and round-trip obligations exercisable through
    the real tool path on test tenants.
    """
    assert MockAdServer.supports_property_list_targeting is True


def _pkg(*, property_list: bool) -> MagicMock:
    pkg = MagicMock()
    pkg.targeting_overlay = MagicMock()
    pkg.targeting_overlay.property_list = MagicMock() if property_list else None
    return pkg


class _UnsupportedAdapter:
    """Adapter class declaring no property_list compile path (like every adapter but Kevel)."""

    supports_property_list_targeting = False


class TestBoundaryGateFieldIndex:
    """``raise_if_property_list_unsupported`` tags the offending package by index, not always [0]."""

    def test_field_index_identifies_offending_package(self):
        # packages[0] has no property_list (skipped); packages[1] is the offender.
        packages = [_pkg(property_list=False), _pkg(property_list=True)]

        with pytest.raises(AdCPCapabilityNotSupportedError) as exc_info:
            raise_if_property_list_unsupported(packages, _UnsupportedAdapter())

        assert exc_info.value.field == "packages[1].targeting_overlay.property_list", (
            f"field must identify the offending package index, not packages[0]; got {exc_info.value.field!r}"
        )
