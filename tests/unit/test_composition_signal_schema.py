import pytest
from pydantic import ValidationError

from src.admin.api_schemas.composition import TenantSignalCreate


def _valid_signal_payload(**overrides):
    payload = {
        "signal_id": "audience_sports-fans",
        "name": "Sports Fans",
        "value_type": "binary",
        "adapter_config": {"kind": "custom_key_value", "key_id": "123"},
    }
    payload.update(overrides)
    return payload


def test_tenant_signal_create_accepts_adcp_wire_safe_signal_id() -> None:
    signal = TenantSignalCreate.model_validate(_valid_signal_payload())

    assert signal.signal_id == "audience_sports-fans"


def test_tenant_signal_create_rejects_dotted_signal_id() -> None:
    with pytest.raises(ValidationError):
        TenantSignalCreate.model_validate(_valid_signal_payload(signal_id="audience.sports_fans"))
