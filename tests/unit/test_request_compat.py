"""Unit tests for normalize_request_params() — AdCP backward compatibility.

TDD RED: these tests define the contract for src/core/request_compat.py
which does not exist yet. All tests should fail with ImportError initially.

The normalizer translates known deprecated AdCP field names to their current
equivalents, mirroring the JS adcp-client's normalizeRequestParams() logic.
"""

import logging

from src.core.request_compat import (
    ADCP_NEGOTIATION_FIELDS,
    _log_dropped_fields,
    _strip_fields,
    normalize_request_params,
    strip_negotiation_fields,
    strip_undeclared_envelope_fields,
    strip_unknown_params,
)

# ---------------------------------------------------------------------------
# 1. brand_manifest → brand (BrandReference)
# ---------------------------------------------------------------------------


class TestBrandManifestTranslation:
    """brand_manifest URL → brand: {domain: hostname}."""

    def test_brand_manifest_url_string(self):
        """A bare URL string is converted to BrandReference with domain."""
        result = normalize_request_params(
            "get_products",
            {"brand_manifest": "https://acme.com/.well-known/brand.json", "brief": "ads"},
        )
        assert result.params["brand"] == {"domain": "acme.com"}
        assert "brand_manifest" not in result.params

    def test_brand_manifest_dict_with_url(self):
        """A dict with 'url' key is converted to BrandReference with domain."""
        result = normalize_request_params(
            "create_media_buy",
            {"brand_manifest": {"url": "https://nike.com/brand"}},
        )
        assert result.params["brand"] == {"domain": "nike.com"}
        assert "brand_manifest" not in result.params

    def test_brand_manifest_only_for_applicable_tools(self):
        """brand_manifest translation only applies to get_products and create_media_buy."""
        result = normalize_request_params(
            "update_media_buy",
            {"brand_manifest": "https://acme.com/brand", "media_buy_id": "mb-1"},
        )
        # update_media_buy has no brand field — brand_manifest is stripped but not translated
        assert "brand" not in result.params
        assert "brand_manifest" not in result.params

    def test_brand_manifest_invalid_url_stripped(self):
        """A brand_manifest that isn't a valid URL is stripped without crashing."""
        result = normalize_request_params(
            "get_products",
            {"brand_manifest": "not-a-url", "brief": "ads"},
        )
        # Invalid URL cannot derive a domain → no brand set, brand_manifest removed
        assert "brand" not in result.params
        assert "brand_manifest" not in result.params

    def test_brand_manifest_malformed_ipv6_url_string_stripped(self):
        """Malformed URL strings must not crash compat middleware (#1537 review)."""
        result = normalize_request_params(
            "get_products",
            {"brand_manifest": "https://[", "brief": "ads"},
        )
        assert "brand" not in result.params
        assert "brand_manifest" not in result.params

    def test_brand_manifest_malformed_ipv6_url_dict_stripped(self):
        """Malformed URL in brand_manifest dict must not crash compat middleware."""
        result = normalize_request_params(
            "get_products",
            {"brand_manifest": {"url": "http://[::1"}, "brief": "ads"},
        )
        assert "brand" not in result.params
        assert "brand_manifest" not in result.params

    def test_brand_manifest_bare_domain_dict_stripped(self):
        """Dict branch must reject bare domains like the string branch (URL-only guard)."""
        result = normalize_request_params(
            "get_products",
            {"brand_manifest": {"url": "acme.com"}, "brief": "ads"},
        )
        assert "brand" not in result.params
        assert "brand_manifest" not in result.params


# ---------------------------------------------------------------------------
# 2. campaign_ref → buyer_campaign_ref
# ---------------------------------------------------------------------------


class TestCampaignRefTranslation:
    """campaign_ref → ext.buyer_campaign_ref (create_media_buy only)."""

    def test_campaign_ref_renamed(self):
        result = normalize_request_params(
            "create_media_buy",
            {"campaign_ref": "camp-123"},
        )
        # AdCP 3.12 removed top-level buyer_campaign_ref; the migration path
        # is the ext extension object, not a top-level field.
        assert result.params["ext"]["buyer_campaign_ref"] == "camp-123"
        assert "campaign_ref" not in result.params
        assert "buyer_campaign_ref" not in result.params

    def test_campaign_ref_not_renamed_for_other_tools(self):
        """campaign_ref is deleted but not translated for non-create_media_buy tools."""
        result = normalize_request_params(
            "get_media_buys",
            {"campaign_ref": "camp-123"},
        )
        assert "campaign_ref" not in result.params
        assert "buyer_campaign_ref" not in result.params


# ---------------------------------------------------------------------------
# 3. account_id → account
# ---------------------------------------------------------------------------


class TestAccountIdTranslation:
    """account_id (bare string) → account: {account_id: str}."""

    def test_account_id_wrapped(self):
        result = normalize_request_params(
            "get_products",
            {"account_id": "acc-456", "brief": "ads"},
        )
        assert result.params["account"] == {"account_id": "acc-456"}
        assert "account_id" not in result.params


# ---------------------------------------------------------------------------
# 4. optimization_goal → optimization_goals (package-level)
# ---------------------------------------------------------------------------


class TestOptimizationGoalTranslation:
    """optimization_goal (scalar) → optimization_goals (array), inside packages."""

    def test_optimization_goal_wrapped_in_array(self):
        result = normalize_request_params(
            "create_media_buy",
            {
                "packages": [
                    {"product_id": "p1", "optimization_goal": "ctr"},
                ],
            },
        )
        pkg = result.params["packages"][0]
        assert pkg["optimization_goals"] == ["ctr"]
        assert "optimization_goal" not in pkg


# ---------------------------------------------------------------------------
# 5. catalog → catalogs (package-level)
# ---------------------------------------------------------------------------


class TestCatalogTranslation:
    """catalog (scalar object) → catalogs (array), inside packages."""

    def test_catalog_wrapped_in_array(self):
        result = normalize_request_params(
            "create_media_buy",
            {
                "packages": [
                    {"product_id": "p1", "catalog": {"id": "cat-1"}},
                ],
            },
        )
        pkg = result.params["packages"][0]
        assert pkg["catalogs"] == [{"id": "cat-1"}]
        assert "catalog" not in pkg


# ---------------------------------------------------------------------------
# 6. promoted_offerings → catalogs (top-level, get_products only)
# ---------------------------------------------------------------------------


class TestPromotedOfferingsTranslation:
    """promoted_offerings → catalogs rename for get_products."""

    def test_promoted_offerings_renamed(self):
        result = normalize_request_params(
            "get_products",
            {"promoted_offerings": [{"id": "po-1"}], "brief": "ads"},
        )
        assert result.params["catalogs"] == [{"id": "po-1"}]
        assert "promoted_offerings" not in result.params


# ---------------------------------------------------------------------------
# 7–8. Version inference
# ---------------------------------------------------------------------------


class TestVersionInference:
    """Infer caller's AdCP version from deprecated field names."""

    def test_v25_signals_detected(self):
        result = normalize_request_params(
            "get_products",
            {"brand_manifest": "https://acme.com/brand", "brief": "ads"},
        )
        assert result.inferred_version == "2.5"

    def test_v3_when_no_deprecated_fields(self):
        result = normalize_request_params(
            "get_products",
            {"brand": {"domain": "acme.com"}, "brief": "ads"},
        )
        assert result.inferred_version == "3.0"


# ---------------------------------------------------------------------------
# 9. No-op when params use only current fields
# ---------------------------------------------------------------------------


class TestNoOp:
    """Params with only current field names pass through unchanged."""

    def test_current_fields_unchanged(self):
        original = {"brand": {"domain": "acme.com"}, "brief": "video ads"}
        result = normalize_request_params("get_products", original)
        assert result.params == original
        assert result.translations_applied == []


# ---------------------------------------------------------------------------
# 10. Precedence: new field wins over deprecated
# ---------------------------------------------------------------------------


class TestPrecedence:
    """When both deprecated and current field are present, current wins."""

    def test_brand_takes_precedence_over_brand_manifest(self):
        result = normalize_request_params(
            "get_products",
            {
                "brand": {"domain": "new.com"},
                "brand_manifest": "https://old.com/brand",
                "brief": "ads",
            },
        )
        assert result.params["brand"] == {"domain": "new.com"}
        assert "brand_manifest" not in result.params

    def test_existing_ext_buyer_campaign_ref_takes_precedence_over_campaign_ref(self):
        result = normalize_request_params(
            "create_media_buy",
            {
                "ext": {"buyer_campaign_ref": "new-ref"},
                "campaign_ref": "old-ref",
            },
        )
        assert result.params["ext"]["buyer_campaign_ref"] == "new-ref"
        assert "campaign_ref" not in result.params
        assert "buyer_campaign_ref" not in result.params

    def test_account_takes_precedence_over_account_id(self):
        result = normalize_request_params(
            "get_products",
            {
                "account": {"account_id": "new-acc"},
                "account_id": "old-acc",
                "brief": "ads",
            },
        )
        assert result.params["account"] == {"account_id": "new-acc"}
        assert "account_id" not in result.params


# ---------------------------------------------------------------------------
# 11. Multiple deprecated fields in one call
# ---------------------------------------------------------------------------


class TestMultipleTranslations:
    """Multiple deprecated fields translated in a single call."""

    def test_all_top_level_deprecated_fields_translated(self):
        result = normalize_request_params(
            "create_media_buy",
            {
                "brand_manifest": "https://acme.com/brand",
                "campaign_ref": "camp-1",
                "account_id": "acc-1",
            },
        )
        assert result.params["brand"] == {"domain": "acme.com"}
        assert result.params["ext"]["buyer_campaign_ref"] == "camp-1"
        assert result.params["account"] == {"account_id": "acc-1"}
        assert "brand_manifest" not in result.params
        assert "campaign_ref" not in result.params
        assert "buyer_campaign_ref" not in result.params
        assert "account_id" not in result.params
        assert len(result.translations_applied) == 3


# ---------------------------------------------------------------------------
# 12. Empty / None params
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases: empty params, None values."""

    def test_empty_params(self):
        result = normalize_request_params("get_products", {})
        assert result.params == {}
        assert result.translations_applied == []

    def test_none_brand_manifest_ignored(self):
        """A None-valued brand_manifest is cleaned up but doesn't create brand."""
        result = normalize_request_params(
            "get_products",
            {"brand_manifest": None, "brief": "ads"},
        )
        assert "brand" not in result.params
        assert "brand_manifest" not in result.params


# ---------------------------------------------------------------------------
# 13–17. strip_unknown_params
# ---------------------------------------------------------------------------


class TestStripUnknownParams:
    """strip_unknown_params removes fields not in the known set."""

    def test_all_known_fields_pass_through(self):
        params = {"brief": "ads", "brand": {"domain": "acme.com"}}
        cleaned, stripped = strip_unknown_params(params, {"brief", "brand"})
        assert cleaned is params
        assert stripped == []

    def test_unknown_fields_removed(self):
        cleaned, stripped = strip_unknown_params(
            {"brief": "ads", "foo": "bar", "baz": 123},
            {"brief"},
        )
        assert cleaned == {"brief": "ads"}
        assert set(stripped) == {"foo", "baz"}

    def test_all_unknown_returns_empty(self):
        cleaned, stripped = strip_unknown_params(
            {"foo": 1, "bar": 2},
            {"brief", "brand"},
        )
        assert cleaned == {}
        assert set(stripped) == {"foo", "bar"}

    def test_empty_params_returns_empty(self):
        cleaned, stripped = strip_unknown_params({}, {"brief"})
        assert cleaned == {}
        assert stripped == []

    def test_preserves_none_values_for_known_fields(self):
        cleaned, stripped = strip_unknown_params(
            {"brief": None, "unknown": "x"},
            {"brief"},
        )
        assert cleaned == {"brief": None}
        assert stripped == ["unknown"]


class TestSharedStripFields:
    """The shared strip primitive preserves ordering and object identity."""

    def test_removes_requested_fields_and_sorts_names(self):
        cleaned, stripped = _strip_fields(
            {"keep": 1, "z_field": 2, "a_field": 3},
            {"z_field", "a_field"},
        )
        assert cleaned == {"keep": 1}
        assert stripped == ["a_field", "z_field"]

    def test_no_match_returns_original_object(self):
        params = {"keep": 1}
        cleaned, stripped = _strip_fields(params, {"missing"})
        assert cleaned is params
        assert stripped == []


class TestDroppedFieldLogging:
    """All transports share the same dropped-field audit message."""

    def test_logs_canonical_message(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="src.core.request_compat"):
            _log_dropped_fields(
                "get_products",
                "AdCP negotiation",
                ["adcp_major_version", "adcp_version"],
            )

        assert caplog.messages == [
            "Dropped AdCP negotiation fields from get_products: adcp_major_version, adcp_version"
        ]

    def test_empty_drop_does_not_log(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="src.core.request_compat"):
            _log_dropped_fields("get_products", "AdCP negotiation", [])

        assert caplog.messages == []


# ---------------------------------------------------------------------------
# strip_negotiation_fields — AdCP version-negotiation envelope (#1512)
# ---------------------------------------------------------------------------


class TestStripNegotiationFields:
    """adcp_version / adcp_major_version are stripped so strict MCP arg
    validation does not reject conformant AdCP SDK clients (#1512)."""

    def test_strips_both_negotiation_fields(self):
        cleaned, stripped = strip_negotiation_fields(
            {"brief": "ads", "adcp_version": "3.1", "adcp_major_version": 3},
        )
        assert cleaned == {"brief": "ads"}
        assert stripped == ["adcp_major_version", "adcp_version"]

    def test_strips_version_only(self):
        cleaned, stripped = strip_negotiation_fields({"brief": "ads", "adcp_version": "3.1-beta.3"})
        assert cleaned == {"brief": "ads"}
        assert stripped == ["adcp_version"]

    def test_no_negotiation_fields_returns_original_object(self):
        params = {"brief": "ads"}
        cleaned, stripped = strip_negotiation_fields(params)
        assert cleaned is params  # unchanged object, no copy
        assert stripped == []

    def test_does_not_touch_real_tool_params(self):
        cleaned, _ = strip_negotiation_fields({"account": {"account_id": "a1"}, "adcp_version": "3.1"})
        assert cleaned == {"account": {"account_id": "a1"}}

    def test_constant_holds_exactly_the_two_envelope_fields(self):
        assert ADCP_NEGOTIATION_FIELDS == frozenset({"adcp_version", "adcp_major_version"})


class TestStripUndeclaredEnvelopeFields:
    """context/ext/push_notification_config are stripped only when the tool
    does not declare them (schema-aware), so conformant clients aren't rejected
    while tools that use these fields still receive them (#1512)."""

    def test_strips_context_when_tool_does_not_declare_it(self):
        cleaned, stripped = strip_undeclared_envelope_fields(
            {"brief": "ads", "context": {"correlation_id": "c1"}},
            known_params={"brief"},  # e.g. get_adcp_capabilities has no `context`
        )
        assert cleaned == {"brief": "ads"}
        assert stripped == ["context"]

    def test_keeps_context_when_tool_declares_it(self):
        params = {"brief": "ads", "context": {"correlation_id": "c1"}}
        cleaned, stripped = strip_undeclared_envelope_fields(
            params,
            known_params={"brief", "context"},  # e.g. get_products declares `context`
        )
        assert cleaned is params  # untouched
        assert stripped == []

    def test_strips_multiple_undeclared_envelope_fields(self):
        cleaned, stripped = strip_undeclared_envelope_fields(
            {"brief": "ads", "context": {}, "ext": {}, "push_notification_config": {}},
            known_params={"brief"},
        )
        assert cleaned == {"brief": "ads"}
        assert stripped == ["context", "ext", "push_notification_config"]

    def test_none_known_params_strips_nothing(self):
        params = {"brief": "ads", "context": {}}
        cleaned, stripped = strip_undeclared_envelope_fields(params, known_params=None)
        assert cleaned is params
        assert stripped == []

    def test_does_not_touch_non_envelope_unknowns(self):
        # Only the standard envelope set is in scope here; other unknowns are
        # left for the production strip / dev fail-loud path.
        cleaned, stripped = strip_undeclared_envelope_fields(
            {"context": {}, "some_business_field": 1}, known_params={"brief"}
        )
        assert cleaned == {"some_business_field": 1}
        assert stripped == ["context"]
