"""Unit tests for normalize_request_params() — AdCP backward compatibility.

TDD RED: these tests define the contract for src/core/request_compat.py
which does not exist yet. All tests should fail with ImportError initially.

The normalizer translates known deprecated AdCP field names to their current
equivalents, mirroring the JS adcp-client's normalizeRequestParams() logic.
"""

from src.core.request_compat import normalize_request_params, strip_unknown_params

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
            {"brand_manifest": {"url": "https://nike.com/brand"}, "buyer_ref": "ref1"},
        )
        assert result.params["brand"] == {"domain": "nike.com"}
        assert "brand_manifest" not in result.params

    def test_brand_manifest_only_for_applicable_tools(self):
        """brand_manifest translation only applies to get_products and create_media_buy."""
        result = normalize_request_params(
            "update_media_buy",
            {"brand_manifest": "https://acme.com/brand", "media_buy_id": "mb-1"},
        )
        # update_media_buy has no brand field — leave brand_manifest visible
        # so strict dev-mode validation can reject it as unknown.
        assert "brand" not in result.params
        assert result.params["brand_manifest"] == "https://acme.com/brand"

    def test_brand_manifest_invalid_url_stripped(self):
        """A brand_manifest that isn't a valid URL is stripped without crashing."""
        result = normalize_request_params(
            "get_products",
            {"brand_manifest": "not-a-url", "brief": "ads"},
        )
        # Invalid URL cannot derive a domain → no brand set, brand_manifest removed
        assert "brand" not in result.params
        assert "brand_manifest" not in result.params


# ---------------------------------------------------------------------------
# 2. campaign_ref removed from create_media_buy
# ---------------------------------------------------------------------------


class TestCampaignRefTranslation:
    """campaign_ref remains visible so strict validation rejects it."""

    def test_campaign_ref_stays_visible_for_create_media_buy(self):
        result = normalize_request_params(
            "create_media_buy",
            {"campaign_ref": "camp-123", "buyer_ref": "ref1"},
        )
        assert result.params["campaign_ref"] == "camp-123"
        assert "buyer_campaign_ref" not in result.params

    def test_campaign_ref_not_renamed_for_other_tools(self):
        """campaign_ref stays visible for all tools."""
        result = normalize_request_params(
            "get_media_buys",
            {"campaign_ref": "camp-123"},
        )
        assert result.params["campaign_ref"] == "camp-123"
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
                "buyer_ref": "ref1",
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
                "buyer_ref": "ref1",
                "packages": [
                    {"product_id": "p1", "catalog": {"id": "cat-1"}},
                ],
            },
        )
        pkg = result.params["packages"][0]
        assert pkg["catalogs"] == [{"id": "cat-1"}]
        assert "catalog" not in pkg


# ---------------------------------------------------------------------------
# 6. format_id alias → id inside format references
# ---------------------------------------------------------------------------


class TestFormatReferenceTranslation:
    """Legacy format reference objects use format_id where current spec uses id."""

    def test_sync_creatives_format_id_alias_normalized(self):
        result = normalize_request_params(
            "sync_creatives",
            {
                "creatives": [
                    {
                        "creative_id": "c1",
                        "name": "Creative",
                        "format_id": {
                            "agent_url": "https://creative.adcontextprotocol.org/mcp",
                            "format_id": "display_300x250_image",
                        },
                    }
                ]
            },
        )

        fmt = result.params["creatives"][0]["format_id"]
        assert fmt == {
            "agent_url": "https://creative.adcontextprotocol.org/mcp",
            "id": "display_300x250_image",
        }

    def test_package_format_ids_alias_normalized(self):
        result = normalize_request_params(
            "create_media_buy",
            {
                "packages": [
                    {
                        "product_id": "p1",
                        "format_ids": [
                            {
                                "agent_url": "https://creative.adcontextprotocol.org",
                                "format_id": "display_300x250_image",
                            }
                        ],
                    }
                ]
            },
        )

        assert result.params["packages"][0]["format_ids"][0]["id"] == "display_300x250_image"
        assert "format_id" not in result.params["packages"][0]["format_ids"][0]


# ---------------------------------------------------------------------------
# 7. promoted_offerings → catalogs (top-level, get_products only)
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

    def test_promoted_offerings_stays_visible_for_other_tools(self):
        result = normalize_request_params(
            "list_creatives",
            {"promoted_offerings": [{"id": "po-1"}]},
        )
        assert result.params["promoted_offerings"] == [{"id": "po-1"}]
        assert "catalogs" not in result.params


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

    def test_buyer_campaign_ref_does_not_hide_campaign_ref(self):
        result = normalize_request_params(
            "create_media_buy",
            {
                "buyer_campaign_ref": "new-ref",
                "campaign_ref": "old-ref",
                "buyer_ref": "ref1",
            },
        )
        assert result.params["buyer_campaign_ref"] == "new-ref"
        assert result.params["campaign_ref"] == "old-ref"

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
                "buyer_ref": "ref1",
            },
        )
        assert result.params["brand"] == {"domain": "acme.com"}
        assert result.params["account"] == {"account_id": "acc-1"}
        assert result.params["campaign_ref"] == "camp-1"
        assert "buyer_campaign_ref" not in result.params
        assert "brand_manifest" not in result.params
        assert "account_id" not in result.params
        assert len(result.translations_applied) == 2


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
        cleaned, stripped = strip_unknown_params(
            {"brief": "ads", "brand": {"domain": "acme.com"}},
            {"brief", "brand"},
        )
        assert cleaned == {"brief": "ads", "brand": {"domain": "acme.com"}}
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
