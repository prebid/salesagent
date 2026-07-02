"""Unit tests for _processing.py helper functions (Change 1 & 2).

Covers:
- ``_find_format``: normalized composite (agent_url, id) key lookup per
  AdCP URL canonicalization rules (RFC 3986 §6.2.2/§6.2.3).
- ``_build_generative_manifest``: AdCP-compliant creative_manifest structure
  (format_id as object, assets required, no creative_id/name).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.core.tools.creatives._processing import _build_generative_manifest, _find_format

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_format(agent_url: str, format_id: str) -> MagicMock:
    """Build a mock Format object with a FormatId-shaped format_id attribute."""
    fmt = MagicMock()
    fmt.format_id = MagicMock()
    fmt.format_id.agent_url = agent_url
    fmt.format_id.id = format_id
    fmt.agent_url = agent_url
    return fmt


def _make_legacy_format(agent_url: str, format_id: str) -> MagicMock:
    """Build a mock Format object with legacy shape: format_id is a plain string."""
    fmt = MagicMock()
    fmt.format_id = format_id  # bare string, not an object
    fmt.agent_url = agent_url  # top-level attribute
    return fmt


def _make_creative_format(agent_url: str, format_id: str) -> MagicMock:
    """Build a mock FormatId (creative.format_id) with agent_url and id."""
    cf = MagicMock()
    cf.agent_url = agent_url
    cf.id = format_id
    return cf


# ---------------------------------------------------------------------------
# _find_format — Change 1
# ---------------------------------------------------------------------------


class TestFindFormat:
    """_find_format uses normalized composite (agent_url, id) key.

    AdCP URL canonicalization (RFC 3986 §6.2.2/§6.2.3): URLs differing only
    by trailing slash, case, or default port must compare equal.
    """

    def test_exact_match_returns_format(self):
        """Exact URL + id match returns the correct format object."""
        fmt = _make_format("https://creative.example.com", "display_300x250")
        creative_format = _make_creative_format("https://creative.example.com", "display_300x250")

        result = _find_format([fmt], creative_format)

        assert result is fmt

    def test_trailing_slash_normalized(self):
        """Trailing slash on agent_url is normalized before comparison.

        ``https://creative.example.com/`` and ``https://creative.example.com``
        must match (RFC 3986 §6.2.3 path normalization).
        """
        fmt = _make_format("https://creative.example.com", "display_300x250")
        creative_format = _make_creative_format("https://creative.example.com/", "display_300x250")

        result = _find_format([fmt], creative_format)

        assert result is fmt, (
            "_find_format must normalize trailing slash: "
            "'https://creative.example.com/' should match 'https://creative.example.com'"
        )

    def test_trailing_slash_on_stored_format_normalized(self):
        """Trailing slash on the stored format's agent_url is also normalized."""
        fmt = _make_format("https://creative.example.com/", "display_300x250")
        creative_format = _make_creative_format("https://creative.example.com", "display_300x250")

        result = _find_format([fmt], creative_format)

        assert result is fmt

    def test_both_have_trailing_slash(self):
        """Both sides having trailing slash still matches."""
        fmt = _make_format("https://creative.example.com/", "display_300x250")
        creative_format = _make_creative_format("https://creative.example.com/", "display_300x250")

        result = _find_format([fmt], creative_format)

        assert result is fmt

    def test_mcp_suffix_normalized(self):
        """``/mcp`` suffix is stripped by normalize_agent_url before comparison."""
        fmt = _make_format("https://creative.example.com", "display_300x250")
        creative_format = _make_creative_format("https://creative.example.com/mcp", "display_300x250")

        result = _find_format([fmt], creative_format)

        assert result is fmt, (
            "_find_format must normalize /mcp suffix: "
            "'https://creative.example.com/mcp' should match 'https://creative.example.com'"
        )

    def test_no_match_returns_none(self):
        """No matching format returns None."""
        fmt = _make_format("https://creative.example.com", "display_300x250")
        creative_format = _make_creative_format("https://creative.example.com", "display_728x90")

        result = _find_format([fmt], creative_format)

        assert result is None

    def test_wrong_agent_url_returns_none(self):
        """Different agent_url (after normalization) returns None."""
        fmt = _make_format("https://creative.example.com", "display_300x250")
        creative_format = _make_creative_format("https://other.example.com", "display_300x250")

        result = _find_format([fmt], creative_format)

        assert result is None

    def test_empty_list_returns_none(self):
        """Empty format list returns None."""
        creative_format = _make_creative_format("https://creative.example.com", "display_300x250")

        result = _find_format([], creative_format)

        assert result is None

    def test_first_matching_format_returned(self):
        """When multiple formats match, the first one is returned."""
        fmt_a = _make_format("https://creative.example.com", "display_300x250")
        fmt_b = _make_format("https://creative.example.com", "display_300x250")
        creative_format = _make_creative_format("https://creative.example.com", "display_300x250")

        result = _find_format([fmt_a, fmt_b], creative_format)

        assert result is fmt_a

    def test_selects_correct_format_from_multiple(self):
        """Correct format is selected when multiple formats are present."""
        fmt_a = _make_format("https://creative.example.com", "display_300x250")
        fmt_b = _make_format("https://creative.example.com", "display_728x90")
        creative_format = _make_creative_format("https://creative.example.com", "display_728x90")

        result = _find_format([fmt_a, fmt_b], creative_format)

        assert result is fmt_b

    def test_legacy_string_format_id_matches(self):
        """Legacy shape: format_id is a plain string, agent_url is top-level attribute.

        Some test mocks and older code paths use this shape. _find_format must
        handle it without AttributeError.
        """
        fmt = _make_legacy_format("https://creative.example.com", "display_300x250")
        creative_format = _make_creative_format("https://creative.example.com", "display_300x250")

        result = _find_format([fmt], creative_format)

        assert result is fmt

    def test_legacy_string_format_id_trailing_slash_normalized(self):
        """Legacy shape: trailing slash on agent_url is normalized."""
        fmt = _make_legacy_format("https://creative.example.com", "display_300x250")
        creative_format = _make_creative_format("https://creative.example.com/", "display_300x250")

        result = _find_format([fmt], creative_format)

        assert result is fmt

    def test_legacy_string_format_id_no_match(self):
        """Legacy shape: wrong format_id string returns None."""
        fmt = _make_legacy_format("https://creative.example.com", "display_300x250")
        creative_format = _make_creative_format("https://creative.example.com", "display_728x90")

        result = _find_format([fmt], creative_format)

        assert result is None


# ---------------------------------------------------------------------------
# _build_generative_manifest — Change 2
# ---------------------------------------------------------------------------


class TestBuildGenerativeManifest:
    """_build_generative_manifest produces AdCP-compliant creative_manifest.

    AdCP 3.1 requires:
    - ``format_id`` as a structured object (not a bare string)
    - ``assets`` field always present (empty dict if no assets)
    - No ``creative_id`` or ``name`` at the top level
    """

    def _make_creative(self, assets=None) -> MagicMock:
        creative = MagicMock()
        creative.assets = assets
        return creative

    def test_format_id_is_structured_object(self):
        """format_id must be a dict with 'id' and 'agent_url' keys (not a bare string)."""
        creative_format = _make_creative_format("https://creative.example.com", "display_300x250")
        format_obj = MagicMock()
        format_obj.agent_url = "https://creative.example.com"
        creative = self._make_creative()

        manifest = _build_generative_manifest(creative_format, format_obj, creative)

        assert isinstance(manifest["format_id"], dict), (
            "format_id must be a structured object (dict), not a bare string"
        )
        assert manifest["format_id"]["id"] == "display_300x250"
        assert manifest["format_id"]["agent_url"] == "https://creative.example.com"

    def test_assets_always_present(self):
        """'assets' key must always be present in the manifest."""
        creative_format = _make_creative_format("https://creative.example.com", "display_300x250")
        format_obj = MagicMock()
        format_obj.agent_url = "https://creative.example.com"
        creative = self._make_creative(assets=None)

        manifest = _build_generative_manifest(creative_format, format_obj, creative)

        assert "assets" in manifest, "manifest must always contain 'assets' key"

    def test_assets_empty_dict_when_no_assets(self):
        """When creative has no assets, manifest['assets'] is an empty dict."""
        creative_format = _make_creative_format("https://creative.example.com", "display_300x250")
        format_obj = MagicMock()
        format_obj.agent_url = "https://creative.example.com"
        creative = self._make_creative(assets=None)

        manifest = _build_generative_manifest(creative_format, format_obj, creative)

        assert manifest["assets"] == {}, (
            "manifest['assets'] must be {} when creative has no assets, not None or missing"
        )

    def test_no_creative_id_in_manifest(self):
        """Manifest must NOT contain 'creative_id' at the top level."""
        creative_format = _make_creative_format("https://creative.example.com", "display_300x250")
        format_obj = MagicMock()
        format_obj.agent_url = "https://creative.example.com"
        creative = self._make_creative()

        manifest = _build_generative_manifest(creative_format, format_obj, creative)

        assert "creative_id" not in manifest, "manifest must NOT contain 'creative_id' — AdCP 3.1 removed this field"

    def test_no_name_in_manifest(self):
        """Manifest must NOT contain 'name' at the top level."""
        creative_format = _make_creative_format("https://creative.example.com", "display_300x250")
        format_obj = MagicMock()
        format_obj.agent_url = "https://creative.example.com"
        creative = self._make_creative()

        manifest = _build_generative_manifest(creative_format, format_obj, creative)

        assert "name" not in manifest, "manifest must NOT contain 'name' — AdCP 3.1 removed this field"

    def test_agent_url_from_format_obj(self):
        """agent_url in format_id comes from format_obj.agent_url (not creative_format)."""
        creative_format = _make_creative_format("https://creative.example.com/mcp", "display_300x250")
        format_obj = MagicMock()
        format_obj.agent_url = "https://creative.example.com"  # canonical form
        creative = self._make_creative()

        manifest = _build_generative_manifest(creative_format, format_obj, creative)

        # The agent_url in the manifest comes from format_obj (canonical), not creative_format
        assert manifest["format_id"]["agent_url"] == "https://creative.example.com"

    def test_format_id_from_creative_format(self):
        """format_id.id in manifest comes from creative_format.id."""
        creative_format = _make_creative_format("https://creative.example.com", "video_standard_30s")
        format_obj = MagicMock()
        format_obj.agent_url = "https://creative.example.com"
        creative = self._make_creative()

        manifest = _build_generative_manifest(creative_format, format_obj, creative)

        assert manifest["format_id"]["id"] == "video_standard_30s"
