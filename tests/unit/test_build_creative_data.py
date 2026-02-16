"""Tests for _build_creative_data helper.

Verifies data dict construction from raw creative dict: standard fields
(url, click_url, width, height, duration), optional fields (assets,
snippet, snippet_type, template_variables), and context.

Beads: salesagent-55b
"""

from src.core.tools.creatives import _build_creative_data


class TestStandardFields:
    """Standard fields are always included."""

    def test_all_standard_fields(self):
        creative = {
            "click_url": "https://example.com/click",
            "width": 300,
            "height": 250,
            "duration": 30,
        }
        data = _build_creative_data(creative, "https://example.com/ad.png")
        assert data["url"] == "https://example.com/ad.png"
        assert data["click_url"] == "https://example.com/click"
        assert data["width"] == 300
        assert data["height"] == 250
        assert data["duration"] == 30

    def test_missing_standard_fields_are_none(self):
        data = _build_creative_data({}, None)
        assert data["url"] is None
        assert data["click_url"] is None
        assert data["width"] is None
        assert data["height"] is None
        assert data["duration"] is None


class TestOptionalFields:
    """Optional fields only included when present in creative dict."""

    def test_assets_included(self):
        creative = {"assets": {"main": {"url": "https://example.com/main.png"}}}
        data = _build_creative_data(creative, None)
        assert data["assets"] == {"main": {"url": "https://example.com/main.png"}}

    def test_assets_excluded_when_missing(self):
        data = _build_creative_data({}, None)
        assert "assets" not in data

    def test_snippet_included(self):
        creative = {"snippet": "<div>ad</div>", "snippet_type": "html"}
        data = _build_creative_data(creative, None)
        assert data["snippet"] == "<div>ad</div>"
        assert data["snippet_type"] == "html"

    def test_snippet_without_type(self):
        creative = {"snippet": "<div>ad</div>"}
        data = _build_creative_data(creative, None)
        assert data["snippet"] == "<div>ad</div>"
        assert data["snippet_type"] is None

    def test_snippet_excluded_when_missing(self):
        data = _build_creative_data({}, None)
        assert "snippet" not in data
        assert "snippet_type" not in data

    def test_template_variables_included(self):
        creative = {"template_variables": {"headline": "Buy Now"}}
        data = _build_creative_data(creative, None)
        assert data["template_variables"] == {"headline": "Buy Now"}

    def test_template_variables_excluded_when_missing(self):
        data = _build_creative_data({}, None)
        assert "template_variables" not in data


class TestContext:
    """Context dict included when provided."""

    def test_context_included(self):
        data = _build_creative_data({}, None, context={"app": "test"})
        assert data["context"] == {"app": "test"}

    def test_context_excluded_when_none(self):
        data = _build_creative_data({}, None, context=None)
        assert "context" not in data

    def test_context_default_is_none(self):
        data = _build_creative_data({}, None)
        assert "context" not in data


class TestCombined:
    """All fields together."""

    def test_full_creative(self):
        creative = {
            "click_url": "https://example.com/click",
            "width": 728,
            "height": 90,
            "duration": 15,
            "assets": {"main": {"url": "https://cdn.example.com/banner.png"}},
            "snippet": "<script>tag</script>",
            "snippet_type": "js",
            "template_variables": {"cta": "Learn More"},
        }
        data = _build_creative_data(creative, "https://example.com/ad.png", context={"campaign": "summer"})
        assert data == {
            "url": "https://example.com/ad.png",
            "click_url": "https://example.com/click",
            "width": 728,
            "height": 90,
            "duration": 15,
            "assets": {"main": {"url": "https://cdn.example.com/banner.png"}},
            "snippet": "<script>tag</script>",
            "snippet_type": "js",
            "template_variables": {"cta": "Learn More"},
            "context": {"campaign": "summer"},
        }
