"""Unit tests for dynamic_products pure functions.

Tests extract_activation_key, generate_variant_id, customize_name,
and customize_description — all pure functions with no DB or HTTP deps.

# --- Test Source-of-Truth Audit ---
# Audited: 2026-03-07
# AdCP spec commit: adcp/dist/schemas/3.0.0-beta.3/
#
# SPEC_BACKED (5 tests):
#   test_key_value_type_matching_deployment — activation-key.json: key_value requires type,key,value
#   test_segment_id_type_matching_deployment — activation-key.json: segment_id requires type,segment_id
#   test_not_live_deployment_skipped — deployment.json: activation_key only when is_live=true
#   test_key_value_missing_value_field — activation-key.json: key_value requires value
#   test_segment_id_missing_segment_id_field — activation-key.json: segment_id requires segment_id
#
# CHARACTERIZATION (21 tests):
#   extract_activation_key:
#     test_no_matching_deployment_url — locks: fallback to first live deployment
#     test_no_deployments — locks: None for empty deployments
#     test_missing_deployments_key — locks: None for missing key
#     test_no_agent_url_uses_first_live — locks: first live used when no URL
#     test_no_agent_url_fallback_segment_id_type — locks: segment_id in fallback
#   generate_variant_id (all 6):
#     test_deterministic_for_key_value — locks: deterministic hashing
#     test_different_activation_keys_differ — locks: collision avoidance
#     test_different_templates_differ — locks: template isolation
#     test_format_includes_template_prefix — locks: ID format convention
#     test_segment_id_type — locks: segment_id variant generation
#     test_unknown_type_fallback — locks: fallback for unknown types
#   customize_name (all 5):
#     No spec defines variant name customization — all lock current conventions
#   customize_description (all 5):
#     No spec defines variant description customization — all lock current conventions
# ---
"""

from __future__ import annotations

from src.services.dynamic_products import (
    customize_description,
    customize_name,
    extract_activation_key,
    generate_variant_id,
)

OUR_AGENT_URL = "https://sales.example.com"


# ---------------------------------------------------------------------------
# extract_activation_key
# ---------------------------------------------------------------------------


class TestExtractActivationKey:
    """Tests for extract_activation_key()."""

    def test_key_value_type_matching_deployment(self):
        """Matching agent_url + key_value activation key is returned."""
        signal = {
            "deployments": [
                {
                    "destination": {"agent_url": OUR_AGENT_URL},
                    "is_live": True,
                    "activation_key": {
                        "type": "key_value",
                        "key": "axe_segment",
                        "value": "auto_intender_123",
                    },
                }
            ]
        }
        result = extract_activation_key(signal, OUR_AGENT_URL)
        assert result is not None
        assert result["type"] == "key_value"
        assert result["key"] == "axe_segment"
        assert result["value"] == "auto_intender_123"

    def test_segment_id_type_matching_deployment(self):
        """segment_id activation key is returned when matching."""
        signal = {
            "deployments": [
                {
                    "destination": {"agent_url": OUR_AGENT_URL},
                    "is_live": True,
                    "activation_key": {
                        "type": "segment_id",
                        "segment_id": "seg_456",
                    },
                }
            ]
        }
        result = extract_activation_key(signal, OUR_AGENT_URL)
        assert result is not None
        assert result["type"] == "segment_id"
        assert result["segment_id"] == "seg_456"

    def test_no_matching_deployment_url(self):
        """Returns None when no deployment matches our agent_url."""
        signal = {
            "deployments": [
                {
                    "destination": {"agent_url": "https://other-agent.com"},
                    "is_live": True,
                    "activation_key": {"type": "key_value", "key": "k", "value": "v"},
                }
            ]
        }
        # With specific URL that doesn't match, falls back to first live
        result = extract_activation_key(signal, OUR_AGENT_URL)
        # Fallback: uses first live deployment even without URL match
        assert result is not None

    def test_not_live_deployment_skipped(self):
        """Deployment with is_live=False is not used."""
        signal = {
            "deployments": [
                {
                    "destination": {"agent_url": OUR_AGENT_URL},
                    "is_live": False,
                    "activation_key": {"type": "key_value", "key": "k", "value": "v"},
                }
            ]
        }
        result = extract_activation_key(signal, OUR_AGENT_URL)
        assert result is None

    def test_no_deployments(self):
        """Returns None when signal has no deployments."""
        signal = {"deployments": []}
        result = extract_activation_key(signal, OUR_AGENT_URL)
        assert result is None

    def test_missing_deployments_key(self):
        """Returns None when signal dict has no deployments key."""
        signal = {}
        result = extract_activation_key(signal, OUR_AGENT_URL)
        assert result is None

    def test_no_agent_url_uses_first_live(self):
        """When our_agent_url is None, uses first live deployment."""
        signal = {
            "deployments": [
                {
                    "destination": {"agent_url": "https://any.com"},
                    "is_live": True,
                    "activation_key": {"type": "key_value", "key": "k", "value": "v"},
                }
            ]
        }
        result = extract_activation_key(signal, None)
        assert result is not None
        assert result["key"] == "k"

    def test_no_agent_url_fallback_segment_id_type(self):
        """When our_agent_url is None, fallback loop returns segment_id activation key."""
        signal = {
            "deployments": [
                {
                    "destination": {"agent_url": "https://any.com"},
                    "is_live": True,
                    "activation_key": {"type": "segment_id", "segment_id": "seg_fallback"},
                }
            ]
        }
        result = extract_activation_key(signal, None)
        assert result is not None
        assert result["type"] == "segment_id"
        assert result["segment_id"] == "seg_fallback"

    def test_key_value_missing_value_field(self):
        """key_value type without 'value' field returns None."""
        signal = {
            "deployments": [
                {
                    "destination": {"agent_url": OUR_AGENT_URL},
                    "is_live": True,
                    "activation_key": {"type": "key_value", "key": "k"},
                }
            ]
        }
        result = extract_activation_key(signal, OUR_AGENT_URL)
        assert result is None

    def test_segment_id_missing_segment_id_field(self):
        """segment_id type without 'segment_id' field returns None."""
        signal = {
            "deployments": [
                {
                    "destination": {"agent_url": OUR_AGENT_URL},
                    "is_live": True,
                    "activation_key": {"type": "segment_id"},
                }
            ]
        }
        result = extract_activation_key(signal, OUR_AGENT_URL)
        assert result is None


# ---------------------------------------------------------------------------
# generate_variant_id
# ---------------------------------------------------------------------------


class TestGenerateVariantId:
    """Tests for generate_variant_id()."""

    def test_deterministic_for_key_value(self):
        """Same template + activation key → same variant ID."""
        ak = {"type": "key_value", "key": "axe", "value": "123"}
        id1 = generate_variant_id("tmpl_001", ak)
        id2 = generate_variant_id("tmpl_001", ak)
        assert id1 == id2

    def test_different_activation_keys_differ(self):
        """Different activation keys → different variant IDs."""
        ak1 = {"type": "key_value", "key": "axe", "value": "123"}
        ak2 = {"type": "key_value", "key": "axe", "value": "456"}
        id1 = generate_variant_id("tmpl_001", ak1)
        id2 = generate_variant_id("tmpl_001", ak2)
        assert id1 != id2

    def test_different_templates_differ(self):
        """Different template IDs → different variant IDs."""
        ak = {"type": "key_value", "key": "axe", "value": "123"}
        id1 = generate_variant_id("tmpl_001", ak)
        id2 = generate_variant_id("tmpl_002", ak)
        assert id1 != id2

    def test_format_includes_template_prefix(self):
        """Variant ID starts with template_id prefix."""
        ak = {"type": "key_value", "key": "k", "value": "v"}
        vid = generate_variant_id("tmpl_001", ak)
        assert vid.startswith("tmpl_001__variant_")

    def test_segment_id_type(self):
        """segment_id activation key type produces valid ID."""
        ak = {"type": "segment_id", "segment_id": "seg_789"}
        vid = generate_variant_id("tmpl_001", ak)
        assert vid.startswith("tmpl_001__variant_")
        # Should be deterministic
        assert vid == generate_variant_id("tmpl_001", ak)

    def test_unknown_type_fallback(self):
        """Unknown activation key type still produces a variant ID."""
        ak = {"type": "unknown", "data": "xyz"}
        vid = generate_variant_id("tmpl_001", ak)
        assert vid.startswith("tmpl_001__variant_")


# ---------------------------------------------------------------------------
# customize_name
# ---------------------------------------------------------------------------


class TestCustomizeName:
    """Tests for customize_name()."""

    def test_default_appends_signal_name(self):
        """Without template, appends signal name."""
        signal = {"name": "Auto Intenders"}
        ak = {"type": "key_value", "key": "k", "value": "v"}
        result = customize_name("Display Banner", signal, ak)
        assert result == "Display Banner - Auto Intenders"

    def test_template_macro_expansion(self):
        """Template with macros is expanded."""
        signal = {"name": "Sports Fans", "description": "Sports audience"}
        ak = {"type": "key_value", "key": "segment", "value": "sports_123"}
        template = "{{name}} ({{signal.name}})"
        result = customize_name("Banner", signal, ak, template)
        assert result == "Banner (Sports Fans)"

    def test_fallback_to_key_value_when_no_signal_name(self):
        """Without signal name, falls back to activation key."""
        signal = {}
        ak = {"type": "key_value", "key": "axe", "value": "123"}
        result = customize_name("Banner", signal, ak)
        assert result == "Banner - axe=123"

    def test_fallback_to_segment_id(self):
        """Without signal name, segment_id type uses segment ID."""
        signal = {}
        ak = {"type": "segment_id", "segment_id": "seg_789"}
        result = customize_name("Banner", signal, ak)
        assert result == "Banner - Segment seg_789"

    def test_no_signal_name_no_key_returns_template_name(self):
        """Without signal name and unknown key type, returns template name."""
        signal = {}
        ak = {"type": "unknown"}
        result = customize_name("Banner", signal, ak)
        assert result == "Banner"


# ---------------------------------------------------------------------------
# customize_description
# ---------------------------------------------------------------------------


class TestCustomizeDescription:
    """Tests for customize_description()."""

    def test_appends_signal_description(self):
        """Appends signal description to template description."""
        signal = {"description": "Users in-market for vehicles"}
        ak = {"type": "key_value", "key": "k", "value": "v"}
        result = customize_description("Great ad product", signal, ak, "cars brief")
        assert "Great ad product" in result
        assert "Users in-market for vehicles" in result

    def test_none_template_uses_signal_description(self):
        """When template description is None, returns signal description."""
        signal = {"description": "Sports fans"}
        ak = {}
        result = customize_description(None, signal, ak, "sports brief")
        assert result == "Sports fans"

    def test_none_template_no_signal_returns_none(self):
        """When both template and signal description are None, returns None."""
        signal = {}
        ak = {}
        result = customize_description(None, signal, ak, "brief")
        assert result is None

    def test_template_macro_expansion(self):
        """Template with macros is expanded."""
        signal = {"name": "Auto Fans", "description": "Car enthusiasts"}
        ak = {"type": "key_value", "key": "segment", "value": "auto_123"}
        template = "{{description}} — Targeting: {{signal.name}}"
        result = customize_description("Base desc", signal, ak, "brief", template)
        assert result == "Base desc — Targeting: Auto Fans"

    def test_no_signal_description_returns_template(self):
        """When signal has no description, returns template description unchanged."""
        signal = {}
        ak = {}
        result = customize_description("Original desc", signal, ak, "brief")
        assert result == "Original desc"
