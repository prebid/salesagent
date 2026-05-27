"""Pin tests: each concrete adapter declares ``supports_property_list_targeting=False``.

The base class default is False (see ``src/adapters/base.py:195``). Adapters that
flip this to True without first compiling ``targeting_overlay.property_list``
into their ad-server payload would silently drop the field on every create —
breaking the honest-declaration contract that ``_create_media_buy_impl`` /
``_update_media_buy_impl`` enforce by raising ``AdCPUnsupportedFeatureError``.

Until an adapter implements the compile path, its ClassVar must remain False
so the boundary check fires.
"""

from __future__ import annotations

import pytest

from src.adapters.base import AdServerAdapter
from src.adapters.broadstreet import BroadstreetAdapter
from src.adapters.google_ad_manager import GoogleAdManager
from src.adapters.kevel import Kevel
from src.adapters.mock_ad_server import MockAdServer
from src.adapters.triton_digital import TritonDigital
from src.adapters.xandr import XandrAdapter


@pytest.mark.parametrize(
    "adapter_cls",
    [
        MockAdServer,
        GoogleAdManager,
        XandrAdapter,
        Kevel,
        BroadstreetAdapter,
        TritonDigital,
    ],
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
        f"{adapter_cls.__name__}.supports_property_list_targeting is True but no "
        f"adapter currently compiles property_list into its ad-server payload. "
        f"Implement the compile path before flipping this ClassVar."
    )
