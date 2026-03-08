"""Tests for adapter default functions (channels, delivery_measurement).

These functions should live in src/adapters/, not src/core/tools/products.py.
Per 'tools are leaf nodes' principle, adapter lookup logic belongs in the
adapters package.

Task: salesagent-swi0
"""


def test_get_adapter_default_channels_importable_from_adapters():
    """get_adapter_default_channels must be importable from src.adapters."""
    from src.adapters import get_adapter_default_channels

    assert callable(get_adapter_default_channels)


def test_get_adapter_default_delivery_measurement_importable_from_adapters():
    """get_adapter_default_delivery_measurement must be importable from src.adapters."""
    from src.adapters import get_adapter_default_delivery_measurement

    assert callable(get_adapter_default_delivery_measurement)


def test_get_adapter_default_channels_mock():
    """Mock adapter should return its known default channels."""
    from src.adapters import get_adapter_default_channels

    channels = get_adapter_default_channels("mock")
    assert "display" in channels
    assert isinstance(channels, list)


def test_get_adapter_default_channels_unknown_adapter():
    """Unknown adapter type should return empty list."""
    from src.adapters import get_adapter_default_channels

    assert get_adapter_default_channels("nonexistent") == []


def test_get_adapter_default_delivery_measurement_gam():
    """GAM adapter should return google_ad_manager as provider."""
    from src.adapters import get_adapter_default_delivery_measurement

    result = get_adapter_default_delivery_measurement("google_ad_manager")
    assert result["provider"] == "google_ad_manager"


def test_get_adapter_default_delivery_measurement_unknown_adapter():
    """Unknown adapter type should return publisher fallback."""
    from src.adapters import get_adapter_default_delivery_measurement

    assert get_adapter_default_delivery_measurement("nonexistent") == {"provider": "publisher"}
