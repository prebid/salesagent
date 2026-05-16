"""Unit tests for src/admin/utils/embedded_capabilities.py.

Covers the four corners of the env-var contract:
- Unset → all publisher.
- Set on an open instance → ignored (no-op).
- Set on an embedded instance → reflected.
- Malformed → ValueError at call time.
"""

from __future__ import annotations

import pytest

from src.admin.utils.embedded_capabilities import capability_owner, publisher_owns


class TestCapabilityOwnerOpenInstance:
    """``MANAGED_INSTANCE`` unset/false: the env var is irrelevant."""

    def test_no_env_var_returns_publisher(self, monkeypatch):
        monkeypatch.delenv("MANAGED_INSTANCE", raising=False)
        monkeypatch.delenv("EMBEDDED_CAPABILITIES", raising=False)
        assert capability_owner("creative_approval") == "publisher"

    def test_capabilities_set_but_managed_unset_returns_publisher(self, monkeypatch):
        monkeypatch.delenv("MANAGED_INSTANCE", raising=False)
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"creative_approval": "storefront"}')
        assert capability_owner("creative_approval") == "publisher"

    def test_managed_instance_explicitly_false(self, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "false")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"slack": "storefront"}')
        assert capability_owner("slack") == "publisher"


class TestCapabilityOwnerEmbeddedInstance:
    """``MANAGED_INSTANCE=true``: the env var controls each capability."""

    def test_unset_env_var_defaults_all_to_publisher(self, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.delenv("EMBEDDED_CAPABILITIES", raising=False)
        assert capability_owner("creative_approval") == "publisher"
        assert capability_owner("slack") == "publisher"

    def test_unknown_capability_defaults_to_publisher(self, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"creative_approval": "storefront"}')
        assert capability_owner("not_a_real_capability") == "publisher"

    def test_storefront_owned_returns_storefront(self, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"creative_approval": "storefront"}')
        assert capability_owner("creative_approval") == "storefront"

    def test_explicit_publisher_returns_publisher(self, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"creative_approval": "publisher"}')
        assert capability_owner("creative_approval") == "publisher"

    def test_per_capability_independence(self, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv(
            "EMBEDDED_CAPABILITIES",
            '{"creative_approval": "storefront", "slack": "publisher", "ai_services": "storefront"}',
        )
        assert capability_owner("creative_approval") == "storefront"
        assert capability_owner("slack") == "publisher"
        assert capability_owner("ai_services") == "storefront"
        assert capability_owner("advertising_policy") == "publisher"

    def test_empty_string_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", "")
        assert capability_owner("creative_approval") == "publisher"

    def test_whitespace_only_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", "   ")
        assert capability_owner("creative_approval") == "publisher"


class TestPublisherOwnsSugar:
    """``publisher_owns()`` is the Jinja-friendly inverse."""

    def test_publisher_owns_true_when_publisher(self, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"slack": "publisher"}')
        assert publisher_owns("slack") is True

    def test_publisher_owns_false_when_storefront(self, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"slack": "storefront"}')
        assert publisher_owns("slack") is False

    def test_publisher_owns_true_on_open_instance_even_if_set_to_storefront(self, monkeypatch):
        monkeypatch.delenv("MANAGED_INSTANCE", raising=False)
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"slack": "storefront"}')
        assert publisher_owns("slack") is True


class TestMalformedEnvVarFailsLoud:
    """Misconfiguration must not silently leave every workflow on the
    publisher side. Raise at call time so the operator sees the error
    in logs / pages."""

    def test_invalid_json_raises(self, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", "{not valid json")
        with pytest.raises(ValueError, match="not valid JSON"):
            capability_owner("creative_approval")

    def test_non_object_json_raises(self, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '["creative_approval"]')
        with pytest.raises(ValueError, match="must be a JSON object"):
            capability_owner("creative_approval")

    def test_invalid_value_raises(self, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"creative_approval": "platform"}')
        with pytest.raises(ValueError, match="must be 'publisher' or 'storefront'"):
            capability_owner("creative_approval")

    def test_non_string_value_raises(self, monkeypatch):
        monkeypatch.setenv("MANAGED_INSTANCE", "true")
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", '{"creative_approval": true}')
        with pytest.raises(ValueError, match="must be 'publisher' or 'storefront'"):
            capability_owner("creative_approval")

    def test_invalid_json_is_no_op_on_open_instance(self, monkeypatch):
        """On an open instance we never read the env var, so even bogus
        content is fine. Validation happens lazily."""
        monkeypatch.delenv("MANAGED_INSTANCE", raising=False)
        monkeypatch.setenv("EMBEDDED_CAPABILITIES", "{not valid json")
        assert capability_owner("creative_approval") == "publisher"
