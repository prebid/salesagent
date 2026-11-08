"""Integration tests for generative creative support.

Tests the flow where sync_creatives detects generative formats (those with output_format_ids)
and calls build_creative instead of preview_creative, using mocked Gemini API.

Refactored to use CreativeSyncEnv harness (factory_boy + real PostgreSQL).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from src.core.schemas import SyncCreativesResponse
from tests.factories.creative_asset import named_format_assets, prompt_assets, text_spec
from tests.harness import CreativeSyncEnv
from tests.helpers.creative_test_helpers import creative_payload

#: The agent the committed capture was taken FROM, which is the only agent_url whose
#: ``(agent_url, id)`` pair ``find_format`` matches against the capture and the only one
#: ``is_operator_agent`` admits -- so the one that resolves without dialling. The
#: previous value ``https://creative.test.example.com`` did neither; it worked only while
#: the registry was a stand-in (salesagent-6xtxb).
DEFAULT_AGENT_URL = CreativeSyncEnv.DEFAULT_AGENT_URL

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _creative(**overrides) -> dict:
    """Minimal creative dict for testing."""
    return creative_payload(
        **{
            "creative_id": "gen-creative-001",
            "name": "Test Generative Creative",
            "format_id": {"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250_generative"},
            "assets": prompt_assets(
                "Create a banner ad for eco-friendly products",
                text_spec("message", content="Create a banner ad for eco-friendly products"),
            ),
            **overrides,
        }
    )


class TestGenerativeCreatives:
    """Integration tests for generative creative functionality."""

    def test_generative_format_detection_calls_build_creative(self, integration_db):
        """Test that generative formats (with output_format_ids) call build_creative."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build(
                format_id="display_300x250_generative",
                build_result={
                    "status": "draft",
                    "context_id": "ctx-123",
                    "creative_output": {
                        "assets": {"headline": {"asset_type": "text", "content": "Generated headline"}},
                        "output_format": {"url": "https://example.com/generated.html"},
                    },
                },
            )

            result = env.call_impl(creatives=[_creative(format_id=fmt)])

            # Verify build_creative was called (not preview_creative)
            registry = env.mock["registry"].return_value
            assert registry.build_creative.called
            assert not registry.preview_creative.called

            # Verify build_creative args
            call_args = registry.build_creative.call_args
            assert call_args[1]["agent_url"] == DEFAULT_AGENT_URL
            assert "display_300x250_generative" in str(call_args[1]["format_id"])
            assert call_args[1]["message"] == "Create a banner ad for eco-friendly products"
            assert call_args[1]["gemini_api_key"] == "test-gemini-key"

        # Verify result
        assert isinstance(result, SyncCreativesResponse)
        assert len(result.creatives) == 1
        assert result.creatives[0].action == "created"

        # Verify creative stored with generative data
        with get_db_session() as session:
            db_creative = session.scalars(select(DBCreative).filter_by(creative_id="gen-creative-001")).first()
            assert db_creative is not None
            assert db_creative.data.get("generative_status") == "draft"
            assert db_creative.data.get("generative_context_id") == "ctx-123"
            assert db_creative.data.get("url") == "https://example.com/generated.html"

    def test_static_format_calls_preview_creative(self, integration_db):
        """Test that static formats (without output_format_ids) call preview_creative.

        The served format is the CAPTURE's own, read through the env: the sync path hands
        the resolved spec to ``canonical_kind_for``, whose first step reads
        ``spec.canonical.kind``, and a hand-rolled ``MagicMock`` answered a child mock
        there -- which ``CreativeFormatIdentity`` refuses as an out-of-vocabulary kind, so
        the creative came back ``action='failed'``. ``CreativeSyncEnv.served_format`` now
        FILTERS the committed capture, so the kind is the reference agent's own and no
        test states it.
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # A static format -- no output_format_ids in the capture, so production takes
            # the preview_creative branch rather than the generative build. Asserted
            # through ``generative=False`` rather than assumed.
            served = env.served_format("display_300x250_image", generative=False)

            registry = env.mock["registry"].return_value
            registry.preview_creative = AsyncMock(
                return_value={
                    "previews": [
                        {
                            "renders": [
                                {
                                    "preview_url": "https://example.com/preview.png",
                                    "dimensions": {"width": 300, "height": 250},
                                }
                            ]
                        }
                    ]
                }
            )
            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="static-creative-001",
                        name="Test Static Creative",
                        format_id={"agent_url": env.DEFAULT_AGENT_URL, "id": served.format_id.id},
                        assets=named_format_assets(served.format_id.id, media_url="https://example.com/banner.png"),
                    )
                ]
            )

        assert isinstance(result, SyncCreativesResponse)
        assert len(result.creatives) == 1
        assert result.creatives[0].action == "created"
        assert registry.preview_creative.called
        assert not registry.build_creative.called

    def test_missing_gemini_api_key_raises_error(self, integration_db):
        """Test that missing GEMINI_API_KEY fails the creative with clear error."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build(
                format_id="display_300x250_generative",
                gemini_api_key=None,  # No API key — setup_generative_build clears it
            )

            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="gen-creative-002",
                        format_id=fmt,
                        assets=prompt_assets("Test message", text_spec("message", content="Test message")),
                    )
                ]
            )

        assert isinstance(result, SyncCreativesResponse)
        assert len(result.creatives) == 1
        assert result.creatives[0].action == "failed"
        assert result.creatives[0].errors

    def test_message_extraction_from_assets(self, integration_db):
        """Test that message is correctly extracted from various asset roles."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build(format_id="display_300x250_generative")

            # Test with "brief" role
            env.call_impl(
                creatives=[
                    _creative(
                        creative_id="gen-creative-003",
                        format_id=fmt,
                        assets=prompt_assets("Message from brief", text_spec("brief", content="Message from brief")),
                    )
                ]
            )

            registry = env.mock["registry"].return_value
            call_args = registry.build_creative.call_args
            assert call_args[1]["message"] == "Message from brief"

    def test_creative_without_prompt_asset_is_refused(self, integration_db):
        """A generative creative with no prompt asset is refused, not built from its name.

        The captured generative format REQUIRES ``generation_prompt``, and the seller holds a
        creative to its named format's required assets.
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build(format_id="display_300x250_generative")

            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="gen-creative-004",
                        name="Eco-Friendly Products Banner",
                        format_id=fmt,
                        assets={},
                    )
                ]
            )

            assert result.creatives[0].action == "failed"
            assert [error.code for error in result.creatives[0].errors] == ["CREATIVE_VALIDATION_FAILED_GENERIC"]
            assert not env.mock["registry"].return_value.build_creative.called

    def test_context_id_reuse_for_refinement(self, integration_db):
        """Test that context_id is reused for iterative refinement."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build(
                format_id="display_300x250_generative",
                build_result={
                    "status": "draft",
                    "context_id": "ctx-original",
                    "creative_output": {
                        "output_format": {"url": "https://example.com/generated-initial.html"},
                    },
                },
            )

            # Create initial creative
            env.call_impl(
                creatives=[
                    _creative(
                        creative_id="gen-creative-005",
                        format_id=fmt,
                        assets=prompt_assets("Initial message", text_spec("message", content="Initial message")),
                    )
                ]
            )

            # Update with refinement - context_id should be reused
            registry = env.mock["registry"].return_value
            registry.build_creative = AsyncMock(
                return_value={
                    "status": "draft",
                    "context_id": "ctx-original",
                    "creative_output": {
                        "output_format": {"url": "https://example.com/generated-refined.html"},
                    },
                }
            )

            env.call_impl(
                creatives=[
                    _creative(
                        creative_id="gen-creative-005",
                        format_id=fmt,
                        assets=prompt_assets("Refined message", text_spec("message", content="Refined message")),
                    )
                ]
            )

            call_args = registry.build_creative.call_args
            assert call_args[1]["context_id"] == "ctx-original"
            assert call_args[1]["message"] == "Refined message"

    def test_promoted_offerings_extraction(self, integration_db):
        """Test that build_creative receives promoted_offerings=None when not in assets.

        In adcp v3.6, PromotedOfferings is no longer a valid asset type
        in the CreativeAsset.assets dict. This test verifies build_creative
        is still called and receives promoted_offerings=None.
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build(format_id="display_300x250_generative")

            env.call_impl(
                creatives=[
                    _creative(
                        creative_id="gen-creative-006",
                        format_id=fmt,
                        assets=prompt_assets("Test message", text_spec("message", content="Test message")),
                    )
                ]
            )

            registry = env.mock["registry"].return_value
            call_args = registry.build_creative.call_args
            assert call_args is not None, "build_creative should have been called"
            po = call_args[1]["promoted_offerings"]
            assert po is None
