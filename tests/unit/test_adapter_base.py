"""Unit tests for shared AdServerAdapter base helpers."""

from typing import cast

import pytest

from src.adapters.base import AdServerAdapter
from src.core.exceptions import AdCPConfigurationError

# AdServerAdapter is abstract and _require_config never reads ``self``, so the
# guard is exercised unbound against a sentinel rather than standing up a
# concrete adapter (which needs config + principal + client wiring).
_SELF = cast(AdServerAdapter, object())


class TestRequireConfig:
    """AdServerAdapter._require_config — the shared __init__ config-presence guard.

    Centralizes the "required config value is absent" check so every adapter
    raises AdCPConfigurationError with the missing field attached, and returns
    the value with None stripped so callers can rebind to narrow the type.
    """

    def test_returns_present_value_unchanged(self):
        assert AdServerAdapter._require_config(_SELF, "abc123", field="api_key") == "abc123"

    def test_missing_value_raises_configuration_error_with_field(self):
        with pytest.raises(AdCPConfigurationError) as exc_info:
            AdServerAdapter._require_config(_SELF, None, field="api_key")

        assert exc_info.value.error_code == "CONFIGURATION_ERROR"
        assert exc_info.value.field == "api_key"
        # The class, the code and ``field`` are the whole diagnosis: nothing was
        # caught, so nothing rides internal_detail.
        assert exc_info.value.internal_detail is None

    def test_missing_value_uses_default_message_naming_the_field(self):
        with pytest.raises(AdCPConfigurationError) as exc_info:
            AdServerAdapter._require_config(_SELF, None, field="network_id")

        assert exc_info.value.field == "network_id"

    def test_empty_string_is_treated_as_missing(self):
        # Falsy values (including "") raise — this is what lets callers rebind
        # self.x = self._require_config(self.x, ...) to narrow the type.
        with pytest.raises(AdCPConfigurationError):
            AdServerAdapter._require_config(_SELF, "", field="auth_token")
