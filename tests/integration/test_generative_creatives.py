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
from tests.factories.creative_asset import build_assets, image_spec, text_spec
from tests.factories.format import make_static_format
from tests.harness import CreativeSyncEnv
from tests.helpers.creative_test_helpers import creative_payload

DEFAULT_AGENT_URL = "https://creative.test.example.com"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _creative(**overrides) -> dict:
    """Minimal creative dict for testing."""
    return creative_payload(
        **{
            "creative_id": "gen-creative-001",
            "name": "Test Generative Creative",
            "format_id": {"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250_generative"},
            "assets": build_assets(text_spec("message", content="Create a banner ad for eco-friendly products")),
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
                        "assets": {"headline": {"text": "Generated headline"}},
                        "output_format": {"url": "https://example.com/generated.html"},
                    },
                },
            )

            result = env.call_impl(creatives=[_creative(format_id=fmt)])

            # Verify build_creative was called (not preview_creative)
            registry = env.mock["registry"].return_value
            assert registry.build_creative.called
            assert not registry.preview_creative.called

            # The EXACT keyword set the business layer sends. Pinned as a whole,
            # not field by field, because the failure mode this PR fixed was a
            # parameter that existed, was computed and passed, and was silently
            # ignored by the callee (promoted_offerings / context_id — #2143): a
            # per-field assertion cannot see an argument nobody reads, and a
            # future re-addition of one must go through this line.
            # gemini_api_key is gone with Change 3 (ADCPMultiAgentClient needs no key);
            # creative_manifest is gone because the registry renders the wire
            # objects itself from format_id + assets.
            call_args = registry.build_creative.call_args
            assert call_args.args == ()
            assert set(call_args.kwargs) == {"format_id", "message", "brand", "assets"}, (
                f"build_creative's arguments are the domain values only, got {sorted(call_args.kwargs)}"
            )
            agent_format = call_args.kwargs["format_id"]
            assert agent_format.id == "display_300x250_generative"
            assert str(agent_format.agent_url).rstrip("/") == DEFAULT_AGENT_URL
            assert call_args.kwargs["message"] == "Create a banner ad for eco-friendly products"
            assert call_args.kwargs["assets"].keys() == {"message"}

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
        """Test that static formats (without output_format_ids) call preview_creative."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # A real static Format: production routes on output_format_ids, and a
            # Mock auto-creates that attribute as truthy (see make_static_format).
            static_format = make_static_format("display_300x250", agent_url=DEFAULT_AGENT_URL)
            env.set_run_async_result([static_format])

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
            registry.get_format = AsyncMock(return_value=static_format)

            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="static-creative-001",
                        name="Test Static Creative",
                        format_id={"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250"},
                        assets=build_assets(image_spec("image", url="https://example.com/banner.png")),
                    )
                ]
            )

        assert isinstance(result, SyncCreativesResponse)
        assert len(result.creatives) == 1
        assert result.creatives[0].action == "created"
        assert registry.preview_creative.called
        assert not registry.build_creative.called

        # preview_creative's twin of the build_creative call-args guard above: the
        # static path sends the same domain values (identity + validated assets,
        # plus the existing media url), never a pre-built manifest.
        preview_call_args = registry.preview_creative.call_args
        assert preview_call_args is not None, "preview_creative should have been called"
        assert set(preview_call_args.kwargs) == {"format_id", "assets", "url"}, (
            f"preview_creative's arguments are the domain values only, got {sorted(preview_call_args.kwargs)}"
        )
        agent_format = preview_call_args.kwargs["format_id"]
        assert agent_format.id == "display_300x250"
        assert str(agent_format.agent_url).rstrip("/") == DEFAULT_AGENT_URL
        assert preview_call_args.kwargs["assets"].keys() == {"image"}
        assert preview_call_args.kwargs["url"] == "https://example.com/banner.png"

    def test_missing_gemini_api_key_does_not_fail(self, integration_db):
        """Change 3: missing GEMINI_API_KEY no longer fails generative creatives.

        Before Change 3, build_creative checked config.gemini_api_key and raised
        AdCPConfigurationError when absent.  After Change 3, build_creative uses
        ADCPMultiAgentClient directly — the key is never read, so its absence must
        NOT cause a per-creative failure.
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build(
                format_id="display_300x250_generative",
                gemini_api_key=None,  # No API key — must not matter
            )
            # Explicitly remove the key to confirm it is not consulted
            env.mock["config"].return_value.gemini_api_key = None

            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="gen-creative-002",
                        format_id=fmt,
                        assets=build_assets(text_spec("message", content="Test message")),
                    )
                ]
            )

        assert isinstance(result, SyncCreativesResponse)
        assert len(result.creatives) == 1
        # Change 3: build succeeds — ADCPMultiAgentClient does not need gemini_api_key
        assert result.creatives[0].action == "created"

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
                        assets=build_assets(text_spec("brief", content="Message from brief")),
                    )
                ]
            )

            registry = env.mock["registry"].return_value
            call_args = registry.build_creative.call_args
            assert call_args[1]["message"] == "Message from brief"

    def test_message_fallback_to_creative_name(self, integration_db):
        """Test that creative name is used as fallback when no message provided."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build(format_id="display_300x250_generative")

            env.call_impl(
                creatives=[
                    _creative(
                        creative_id="gen-creative-004",
                        name="Eco-Friendly Products Banner",
                        format_id=fmt,
                        assets={},
                    )
                ]
            )

            registry = env.mock["registry"].return_value
            call_args = registry.build_creative.call_args
            assert call_args[1]["message"] == "Create a creative for: Eco-Friendly Products Banner"

    def test_refinement_update_rebuilds_and_keeps_the_agent_session_id(self, integration_db):
        """A second sync with new instructions dials build_creative again.

        The pre-3.1 call passed the stored ``context_id`` back as a refinement
        session id. That argument is gone (#2143): the pinned request schema's
        refinement handle is ``refine_from_build_variant_id``, sourced from a
        response ``build_variant_id`` the reference agent does not emit, so
        replaying a ``context_id`` as one would be an invented mapping. What must
        still hold is that the refinement reaches the agent as a fresh build with
        the new message, and that the agent's session id stays on the record.
        """
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
                        assets=build_assets(text_spec("message", content="Initial message")),
                    )
                ]
            )

            # Update with refinement instructions
            env.set_build_creative_result(
                {
                    "status": "draft",
                    "context_id": "ctx-original",
                    "creative_output": {
                        "output_format": {"url": "https://example.com/generated-refined.html"},
                    },
                }
            )
            registry = env.mock["registry"].return_value

            env.call_impl(
                creatives=[
                    _creative(
                        creative_id="gen-creative-005",
                        format_id=fmt,
                        assets=build_assets(text_spec("message", content="Refined message")),
                    )
                ]
            )

            call_args = registry.build_creative.call_args
            assert call_args.kwargs["message"] == "Refined message"
            assert "context_id" not in call_args.kwargs, (
                "context_id has no home on build-creative-request.json @ 3.1.1 — see #2143"
            )

            db_creative = env.get_one(DBCreative, creative_id="gen-creative-005")
            assert db_creative is not None
            assert db_creative.data.get("generative_context_id") == "ctx-original"
            assert db_creative.data.get("url") == "https://example.com/generated-refined.html"

    def test_buyer_input_assets_reach_the_agent(self, integration_db):
        """Every buyer asset slot travels to the agent in ``assets``.

        The pre-3.1 call plucked one slot (``promoted_offerings``) into its own
        argument. That argument is gone — ``build-creative-request.json @ 3.1.1``
        has no such property, and its ``creative_manifest`` is documented as
        carrying "any required input assets" — so the slot must arrive with the
        rest of the assets rather than being dropped (#2143).
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build(format_id="display_300x250_generative")

            env.call_impl(
                creatives=[
                    _creative(
                        creative_id="gen-creative-006",
                        format_id=fmt,
                        assets=build_assets(
                            text_spec("message", content="Test message"),
                            text_spec("promoted_offerings", content="Eco-friendly running shoes"),
                        ),
                    )
                ]
            )

            registry = env.mock["registry"].return_value
            call_args = registry.build_creative.call_args
            assert call_args is not None, "build_creative should have been called"
            assets = call_args.kwargs["assets"]
            assert assets.keys() == {"message", "promoted_offerings"}
            # The slot arrives as the typed asset the buyer sent (an SDK AssetVariant),
            # so read it the way the manifest renderer does — through the model.
            offering = assets["promoted_offerings"]
            assert offering.model_dump(mode="json", exclude_none=True)["content"] == "Eco-friendly running shoes"
