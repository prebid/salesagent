"""Unit tests for the creative-agent seam in ``_processing.py``.

Covers:
- ``_resolve_agent_format``: the format reference → ``(format, canonical agent
  identity)`` step both sync paths run before dialling. The MATCHING itself is
  ``format_resolver.find_format`` (graded in ``test_format_resolver.py``); what
  is graded here is the identity this layer hands the registry — one value, so a
  request cannot carry two spellings of the same agent_url.
- ``_render_creative_manifest`` (the registry's single manifest renderer):
  AdCP-compliant ``creative_manifest`` structure (``format_id`` as an object,
  ``assets`` always present, no ``creative_id``/``name``).

The format fixtures are REAL ``Format`` models, not ``Mock``s: production
branches on ``format_obj.output_format_ids`` (generative vs static), and a
``Mock`` auto-creates that attribute as truthy, which silently routes a
static-format test down the generative path.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.core.creative_agent_registry import _render_creative_manifest
from src.core.exceptions import AdCPValidationError
from src.core.schemas import Format, FormatId
from src.core.tools.creatives._processing import _resolve_agent_format
from tests.factories.creative_asset import build_assets, image_spec

AGENT = "https://creative.example.com"


def _structured_format(agent_url: str, format_id: str) -> Format:
    """A real SDK-shaped ``Format`` (``format_id`` is a ``FormatId`` object)."""
    return Format(format_id=FormatId(agent_url=agent_url, id=format_id), name="Test Format")


class TestResolveAgentFormat:
    """_resolve_agent_format returns the format plus the ONE identity to dial with.

    Both agent calls (``preview_creative`` / ``build_creative``) are addressed
    with this single ``FormatId``, so a request cannot carry two spellings of
    the same agent_url.
    """

    def test_returns_format_and_canonical_identity(self):
        fmt = _structured_format(AGENT, "display_300x250")

        resolved = _resolve_agent_format([fmt], FormatId(agent_url=AGENT + "/", id="display_300x250"))

        assert resolved is not None
        format_obj, agent_format = resolved
        assert format_obj is fmt
        assert agent_format.id == "display_300x250"
        assert str(agent_format.agent_url).rstrip("/") == AGENT

    def test_unresolvable_reference_returns_none(self):
        """A reference to a format the agent list does not carry resolves to None."""
        fmt = _structured_format(AGENT, "display_300x250")

        assert _resolve_agent_format([fmt], FormatId(agent_url=AGENT, id="display_728x90")) is None

    def test_format_without_agent_url_returns_none(self):
        """A format carrying no agent_url has no agent to dial, so it does not resolve."""

        @dataclass(frozen=True)
        class _NoAgentUrlFormat:
            format_id: str

        assert (
            _resolve_agent_format(
                [_NoAgentUrlFormat("display_300x250")], FormatId(agent_url=AGENT, id="display_300x250")
            )
            is None
        )


class TestRenderCreativeManifest:
    """_render_creative_manifest produces the AdCP-compliant creative_manifest.

    AdCP 3.1 requires ``format_id`` as a structured object (never a bare
    string), ``assets`` always present, and no ``creative_id``/``name`` at the
    top level. Rendering lives in the registry (the adapter that owns the wire
    contract), which is why this is graded against the serialized payload.
    """

    @pytest.fixture
    def manifest(self) -> dict:
        return _render_creative_manifest(
            FormatId(agent_url=AGENT, id="display_300x250"),
            build_assets(image_spec("banner")),
        ).model_dump(mode="json", exclude_none=True)

    def test_format_id_is_structured_object(self, manifest):
        assert isinstance(manifest["format_id"], dict), (
            "format_id must be a structured object (dict), not a bare string"
        )
        assert manifest["format_id"]["id"] == "display_300x250"
        assert str(manifest["format_id"]["agent_url"]).rstrip("/") == AGENT

    def test_assets_are_carried(self, manifest):
        assert manifest["assets"]["banner"]["asset_type"] == "image"

    def test_no_creative_id_or_name(self, manifest):
        assert "creative_id" not in manifest, "AdCP 3.1 removed creative_id from the manifest"
        assert "name" not in manifest, "AdCP 3.1 removed name from the manifest"

    def test_no_url_key_without_one(self, manifest):
        """``url`` is a static-preview extra — absent unless a media URL is passed."""
        assert "url" not in manifest

    def test_assets_empty_dict_when_no_assets(self):
        """A generative build with no buyer assets still sends ``assets`` as ``{}``."""
        manifest = _render_creative_manifest(FormatId(agent_url=AGENT, id="gen"), None).model_dump(
            mode="json", exclude_none=True
        )

        assert manifest["assets"] == {}, "assets must be {} when the creative has none, not None or missing"

    def test_static_preview_url_rides_through(self):
        """The static path's existing media URL reaches the agent as a manifest extra."""
        manifest = _render_creative_manifest(
            FormatId(agent_url=AGENT, id="display_300x250"),
            build_assets(image_spec("banner")),
            url="https://cdn.example.com/banner.png",
        ).model_dump(mode="json", exclude_none=True)

        assert manifest["url"] == "https://cdn.example.com/banner.png"

    def test_malformed_asset_is_rejected_before_the_request_goes_out(self):
        """model_validate (not model_construct) — a bad asset fails here, not at the agent.

        Typed as ``VALIDATION_ERROR`` / ``correctable``: the assets are buyer input,
        so the rejection must not reach the buyer as a retryable agent outage.
        """
        with pytest.raises(AdCPValidationError):
            _render_creative_manifest(
                FormatId(agent_url=AGENT, id="display_300x250"),
                {"banner": {"asset_type": "image"}},  # image asset with no url/width/height
            )
