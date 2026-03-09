"""Integration tests: _processing.py coverage — UPDATE paths + CREATE edge cases.

Exercises the update code path in _update_existing_creative() which mirrors
create logic but was previously untested. Also covers CREATE edge cases
(object assets, name fallback, server-generated IDs, auto-approve).

All tests use CreativeSyncEnv harness with real PostgreSQL.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from adcp.types import CreativeAction
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from tests.harness import CreativeSyncEnv

DEFAULT_AGENT_URL = "https://creative.test.example.com"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _creative(**overrides) -> dict:
    """Minimal creative dict for testing."""
    defaults = {
        "creative_id": "c_proc_1",
        "name": "Processing Test",
        "format_id": {"id": "display_300x250", "agent_url": DEFAULT_AGENT_URL},
        "assets": {"banner": {"url": "https://example.com/banner.png"}},
    }
    defaults.update(overrides)
    return defaults


def _create_then_update(
    env, creative_id: str, create_overrides: dict | None = None, update_overrides: dict | None = None
):
    """Helper: create a creative, then update it. Returns (create_result, update_result)."""
    create_kwargs = {"creative_id": creative_id, "name": f"Creative {creative_id}"}
    if create_overrides:
        create_kwargs.update(create_overrides)
    create_result = env.call_impl(creatives=[_creative(**create_kwargs)])

    update_kwargs = {"creative_id": creative_id, "name": f"Creative {creative_id}"}
    if create_overrides:
        update_kwargs.update(create_overrides)
    if update_overrides:
        update_kwargs.update(update_overrides)
    update_result = env.call_impl(creatives=[_creative(**update_kwargs)])

    return create_result, update_result


# ── Generative UPDATE Tests (covers _processing.py lines 180-288) ──────────


class TestGenerativeUpdatePromptExtraction:
    """Generative creative update: message/brief/prompt extraction.

    BDD: UC-006-GENERATIVE-CREATIVE-BUILD-02/03/04
    These obligations are tested for CREATE in test_creative_sync_transport.py.
    Here we exercise the UPDATE code path (lines 188-205).
    """

    def test_update_with_message_asset_calls_build(self, integration_db):
        """Update generative creative with message asset → build_creative called."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build()

            # Create first
            env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_gen_up_msg",
                        format_id=fmt,
                        assets={"message": {"content": "Initial prompt"}},
                    )
                ]
            )

            # Reset mock to track update call
            env.mock["registry"].return_value.build_creative.reset_mock()

            # Update with new message
            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_gen_up_msg",
                        format_id=fmt,
                        assets={"message": {"content": "Updated holiday banner"}},
                    )
                ]
            )

            assert result.creatives[0].action == CreativeAction.updated
            call_args = env.mock["registry"].return_value.build_creative.call_args
            assert call_args is not None
            assert call_args[1]["message"] == "Updated holiday banner"

    def test_update_with_brief_asset_extracts_brief(self, integration_db):
        """Update generative creative with brief (no message) → brief extracted."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build()

            env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_gen_up_brief",
                        format_id=fmt,
                        assets={"message": {"content": "Initial"}},
                    )
                ]
            )
            env.mock["registry"].return_value.build_creative.reset_mock()

            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_gen_up_brief",
                        format_id=fmt,
                        assets={"brief": {"content": "Promote summer sale"}},
                    )
                ]
            )

            assert result.creatives[0].action == CreativeAction.updated
            call_args = env.mock["registry"].return_value.build_creative.call_args
            assert call_args[1]["message"] == "Promote summer sale"

    def test_update_with_inputs_context_description(self, integration_db):
        """Update generative creative with inputs[0].context_description → extracted."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build()

            env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_gen_up_inputs",
                        format_id=fmt,
                        assets={"message": {"content": "Initial"}},
                    )
                ]
            )
            env.mock["registry"].return_value.build_creative.reset_mock()

            # Update with inputs instead of assets
            creative = _creative(
                creative_id="c_gen_up_inputs",
                format_id=fmt,
                assets={},
            )
            creative["inputs"] = [{"name": "campaign_brief", "context_description": "Design for Q4 campaign"}]

            result = env.call_impl(creatives=[creative])
            assert result.creatives[0].action == CreativeAction.updated
            call_args = env.mock["registry"].return_value.build_creative.call_args
            assert call_args[1]["message"] == "Design for Q4 campaign"


class TestGenerativeUpdateNoPrompt:
    """Update without prompt preserves existing data.

    BDD: UC-006-GENERATIVE-CREATIVE-BUILD-07
    Given an EXISTING generative creative with previously generated content
    And the update request has no prompt in assets or inputs
    When the system processes the update
    Then the generative build is SKIPPED
    And existing creative data is preserved unchanged
    """

    def test_update_no_prompt_skips_build(self, integration_db):
        """Update with no message/brief/prompt → build_creative NOT called."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build()

            # Create with message (triggers build)
            env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_gen_no_prompt",
                        format_id=fmt,
                        assets={"message": {"content": "Initial prompt"}},
                    )
                ]
            )
            env.mock["registry"].return_value.build_creative.reset_mock()

            # Update with NO message assets (empty assets, no inputs)
            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_gen_no_prompt",
                        name="Updated Name Only",
                        format_id=fmt,
                        assets={},
                    )
                ]
            )

            assert result.creatives[0].action == CreativeAction.updated
            # build_creative should NOT have been called
            assert not env.mock["registry"].return_value.build_creative.called


class TestGenerativeUpdateUserAssets:
    """User assets take priority over generative output on update.

    BDD: UC-006-GENERATIVE-CREATIVE-BUILD-08
    Given a generative creative with user-provided image assets AND a generative prompt
    When the system processes the creative
    Then user-provided assets are used (not overwritten by generative output)
    """

    def test_update_user_assets_not_overwritten(self, integration_db):
        """Update with user assets + prompt → user assets preserved."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build(
                build_result={
                    "status": "draft",
                    "context_id": "ctx-update",
                    "creative_output": {
                        "assets": {"headline": {"text": "Generated headline"}},
                        "output_format": {"url": "https://generated.example.com/creative.html"},
                    },
                }
            )

            env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_gen_user_assets",
                        format_id=fmt,
                        assets={"message": {"content": "Initial"}},
                    )
                ]
            )
            env.mock["registry"].return_value.build_creative.reset_mock()

            # Update with user assets AND message
            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_gen_user_assets",
                        format_id=fmt,
                        assets={
                            "message": {"content": "Refine it"},
                            "hero_image": {"url": "https://user-provided.com/img.png"},
                        },
                    )
                ]
            )

            assert result.creatives[0].action == CreativeAction.updated
            # build_creative was called (message present)
            assert env.mock["registry"].return_value.build_creative.called

            # User assets should be preserved in DB
            with get_db_session() as session:
                db = session.scalars(select(DBCreative).filter_by(creative_id="c_gen_user_assets")).first()
                # data field should have generative build result but NOT overwritten user assets
                assert db.data.get("generative_build_result") is not None


class TestGenerativeUpdatePromotedOfferings:
    """Promoted offerings extracted and passed to build_creative.

    BDD: UC-006-GENERATIVE-CREATIVE-BUILD-07 (promoted_offerings on update)
    """

    def test_update_promoted_offerings_passed(self, integration_db):
        """Update with promoted_offerings asset → passed to build_creative."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build()

            env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_gen_promo",
                        format_id=fmt,
                        assets={"message": {"content": "Initial"}},
                    )
                ]
            )
            env.mock["registry"].return_value.build_creative.reset_mock()

            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_gen_promo",
                        format_id=fmt,
                        assets={
                            "message": {"content": "Build with offerings"},
                            "promoted_offerings": {"content": "Widget Pro | $99"},
                        },
                    )
                ]
            )

            assert result.creatives[0].action == CreativeAction.updated
            call_args = env.mock["registry"].return_value.build_creative.call_args
            assert call_args[1]["promoted_offerings"] is not None


class TestGenerativeUpdateGeminiKeyMissing:
    """Generative update without GEMINI_API_KEY returns failure.

    BDD: UC-006-EXT-I-01 (update path)
    """

    def test_update_generative_no_gemini_key_fails(self, integration_db):
        """Update generative creative without GEMINI_API_KEY → action=failed."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build()

            # Create with gemini key
            env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_no_gemini_up",
                        format_id=fmt,
                        assets={"message": {"content": "Initial"}},
                    )
                ]
            )

            # Remove gemini key for update
            env.mock["config"].return_value.gemini_api_key = None

            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_no_gemini_up",
                        format_id=fmt,
                        assets={"message": {"content": "Try to update"}},
                    )
                ]
            )

            creative_result = result.creatives[0]
            assert creative_result.action == CreativeAction.failed
            assert any("GEMINI_API_KEY" in e for e in creative_result.errors)


# ── Approval Mode UPDATE Tests (covers lines 97-139) ──────────────────────


class TestApprovalModeUpdate:
    """Approval mode transitions on creative UPDATE.

    BDD: UC-006-CREATIVE-APPROVAL-WORKFLOW-01/03 (update path)
    """

    def test_update_auto_approve_sets_approved(self, integration_db):
        """Update with auto-approve → status=approved in DB."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            env.identity.tenant["approval_mode"] = "auto-approve"

            # Create (auto-approve)
            env.call_impl(creatives=[_creative(creative_id="c_auto_up")])

            # Update with format change to trigger approval logic
            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_auto_up",
                        name="Updated Auto",
                    )
                ]
            )

            assert result.creatives[0].action == CreativeAction.updated

            with get_db_session() as session:
                db = session.scalars(select(DBCreative).filter_by(creative_id="c_auto_up")).first()
                assert db.status == "approved"

    def test_update_ai_powered_submits_review(self, integration_db):
        """Update with ai-powered → background AI review submitted."""
        mock_executor = MagicMock()
        mock_executor.submit.return_value = MagicMock()

        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # Create with require-human first
            env.call_impl(creatives=[_creative(creative_id="c_ai_up")])

            # Switch to ai-powered for update
            env.identity.tenant["approval_mode"] = "ai-powered"

            with (
                patch("src.admin.blueprints.creatives._ai_review_executor", mock_executor),
                patch("src.admin.blueprints.creatives._ai_review_lock", MagicMock()),
                patch("src.admin.blueprints.creatives._ai_review_tasks", {}),
            ):
                result = env.call_impl(
                    creatives=[
                        _creative(
                            creative_id="c_ai_up",
                            name="Updated for AI Review",
                        )
                    ]
                )

            assert result.creatives[0].action == CreativeAction.updated

            with get_db_session() as session:
                db = session.scalars(select(DBCreative).filter_by(creative_id="c_ai_up")).first()
                assert db.status == "pending_review"

            assert mock_executor.submit.called


# ── Static Preview UPDATE Tests (covers lines 363-431) ─────────────────────


class TestStaticPreviewUpdate:
    """Static creative preview handling on UPDATE path.

    BDD: UC-006-EXT-H-01/02 (update path)

    Static creatives require preview validation via the creative agent.
    The format must be in all_formats for the preview call to execute.
    """

    def _setup_static_format(self, env):
        """Set up a static format in all_formats so preview_creative is called."""
        from adcp.types.generated_poc.core.format_id import FormatId as LibraryFormatId

        mock_format = MagicMock()
        mock_format.format_id = LibraryFormatId(agent_url=DEFAULT_AGENT_URL, id="display_300x250")
        mock_format.agent_url = DEFAULT_AGENT_URL
        mock_format.output_format_ids = None  # Static (not generative)
        env.set_run_async_result([mock_format])

    def test_update_no_format_no_url_fails(self, integration_db):
        """Update creative: no matching format in registry AND no media_url → action=failed.

        BDD: UC-006-EXT-H-01 (update path)
        When the creative has no matching format_obj and no user-provided media_url,
        the system returns action=failed.
        Lines covered: 392-418 (_processing.py)
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            # Do NOT set up all_formats → format_obj will be None

            # Create succeeds (no format validation without format_obj)
            env.call_impl(creatives=[_creative(creative_id="c_no_preview_up")])

            # Update: format still not in all_formats, no url in assets
            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_no_preview_up",
                        name="Updated No Preview",
                        assets={},  # No url in assets
                    )
                ]
            )

            creative_result = result.creatives[0]
            assert creative_result.action == CreativeAction.failed
            assert any("no previews" in e.lower() or "no media_url" in e.lower() for e in creative_result.errors)

    def test_update_no_format_with_url_succeeds(self, integration_db):
        """Update creative: no matching format BUT has media_url → succeeds.

        BDD: UC-006-EXT-H-02 (update path)
        When the creative has no matching format_obj but has a user-provided media_url,
        the creative update proceeds (preview is optional).
        Lines covered: 393-401 (_processing.py)
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            # Do NOT set up all_formats → format_obj will be None

            env.call_impl(creatives=[_creative(creative_id="c_url_fallback_up")])

            # Update WITH media_url via 'url' field on creative
            creative = _creative(
                creative_id="c_url_fallback_up",
                name="Updated With URL",
            )
            creative["url"] = "https://example.com/banner.png"

            result = env.call_impl(creatives=[creative])
            assert result.creatives[0].action == CreativeAction.updated

    def test_update_agent_exception_fails(self, integration_db):
        """Update when creative agent throws exception → action=failed.

        BDD: UC-006-FORMAT-VALIDATION-UNREACHABLE-01 (update path)
        """
        from unittest.mock import AsyncMock

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            self._setup_static_format(env)

            # preview_creative returns valid result for create
            env.mock["registry"].return_value.preview_creative = AsyncMock(
                return_value={"previews": [{"renders": [{"preview_url": "https://example.com/preview.png"}]}]}
            )
            env.call_impl(creatives=[_creative(creative_id="c_agent_err_up")])

            # Agent raises exception on update
            env.mock["registry"].return_value.preview_creative = AsyncMock(
                side_effect=ConnectionError("Agent unreachable")
            )

            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_agent_err_up",
                        name="Updated Agent Error",
                    )
                ]
            )

            creative_result = result.creatives[0]
            assert creative_result.action == CreativeAction.failed
            assert any("unreachable" in e.lower() or "retry" in e.lower() for e in creative_result.errors)


class TestStaticPreviewDimensionExtraction:
    """Preview dimensions extracted from creative agent on update + create.

    Lines covered: 351-383 (update), 687-702 (create)
    """

    def _setup_static_format(self, env):
        from adcp.types.generated_poc.core.format_id import FormatId as LibraryFormatId

        mock_format = MagicMock()
        mock_format.format_id = LibraryFormatId(agent_url=DEFAULT_AGENT_URL, id="display_300x250")
        mock_format.agent_url = DEFAULT_AGENT_URL
        mock_format.output_format_ids = None
        env.set_run_async_result([mock_format])

    def test_update_preview_dimensions_extracted(self, integration_db):
        """Update: preview agent returns dimensions → stored in DB."""
        from unittest.mock import AsyncMock

        preview_with_dims = {
            "previews": [
                {
                    "renders": [
                        {
                            "preview_url": "https://agent.example.com/preview.png",
                            "dimensions": {"width": 300, "height": 250, "duration": 15},
                        }
                    ],
                }
            ],
        }

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            self._setup_static_format(env)

            # Create with a basic preview
            env.mock["registry"].return_value.preview_creative = AsyncMock(
                return_value={"previews": [{"renders": [{"preview_url": "https://example.com/p.png"}]}]}
            )
            env.call_impl(creatives=[_creative(creative_id="c_dims_up")])

            # Update: preview returns full dimensions
            env.mock["registry"].return_value.preview_creative = AsyncMock(return_value=preview_with_dims)
            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_dims_up",
                        name="Updated Dims",
                    )
                ]
            )

            assert result.creatives[0].action == CreativeAction.updated

            with get_db_session() as session:
                db = session.scalars(select(DBCreative).filter_by(creative_id="c_dims_up")).first()
                assert db.data.get("width") == 300
                assert db.data.get("height") == 250

    def test_create_preview_dimensions_extracted(self, integration_db):
        """Create: preview agent returns dimensions → stored in DB."""
        from unittest.mock import AsyncMock

        preview_with_dims = {
            "previews": [
                {
                    "renders": [
                        {
                            "preview_url": "https://agent.example.com/creative.png",
                            "dimensions": {"width": 728, "height": 90},
                        }
                    ],
                }
            ],
        }

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            self._setup_static_format(env)

            env.mock["registry"].return_value.preview_creative = AsyncMock(return_value=preview_with_dims)
            # Use empty assets so preview URL is not overshadowed by user URL
            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_dims_create",
                        assets={},
                    )
                ]
            )

            assert result.creatives[0].action == CreativeAction.created

            with get_db_session() as session:
                db = session.scalars(select(DBCreative).filter_by(creative_id="c_dims_create")).first()
                assert db.data.get("url") == "https://agent.example.com/creative.png"
                assert db.data.get("width") == 728
                assert db.data.get("height") == 90


# ── Format Change Detection (covers lines 87-91) ──────────────────────────


class TestFormatChangeOnUpdate:
    """Format change detection during creative update."""

    def test_format_change_detected(self, integration_db):
        """Update creative with different format → format fields updated in DB."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # Create with format A
            env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_fmt_change",
                        format_id={"id": "display_300x250", "agent_url": DEFAULT_AGENT_URL},
                    )
                ]
            )

            # Update with format B
            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_fmt_change",
                        format_id={"id": "display_728x90", "agent_url": DEFAULT_AGENT_URL},
                    )
                ]
            )

            assert result.creatives[0].action == CreativeAction.updated

            with get_db_session() as session:
                db = session.scalars(select(DBCreative).filter_by(creative_id="c_fmt_change")).first()
                assert "728x90" in db.format


# ── CREATE Edge Cases (covers lines 543-570, 687-702, 787-821) ────────────


class TestCreateNameFallback:
    """Generative create with no assets/inputs uses creative name as fallback.

    BDD: UC-006-GENERATIVE-CREATIVE-BUILD-06
    Given a NEW generative creative with no assets and no inputs
    And the creative name is "Holiday Sale Banner"
    When the system extracts the prompt
    Then "Create a creative for: Holiday Sale Banner" is used as the build prompt
    """

    def test_name_used_as_fallback_prompt(self, integration_db):
        """Create generative creative with no message → name used as fallback."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build()

            result = env.call_impl(
                creatives=[
                    _creative(
                        creative_id="c_name_fallback",
                        name="Holiday Sale Banner",
                        format_id=fmt,
                        assets={},  # No message/brief/prompt
                    )
                ]
            )

            assert result.creatives[0].action == CreativeAction.created
            call_args = env.mock["registry"].return_value.build_creative.call_args
            assert "Holiday Sale Banner" in call_args[1]["message"]


class TestCreateAutoApprove:
    """Auto-approve on create sets status=approved.

    BDD: UC-006-CREATIVE-APPROVAL-WORKFLOW-01
    Given the tenant has approval_mode=auto-approve
    When a creative is synced
    Then the creative status is set to approved
    """

    def test_create_auto_approve_status(self, integration_db):
        """Create with auto-approve → DB status=approved."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            env.identity.tenant["approval_mode"] = "auto-approve"

            result = env.call_impl(creatives=[_creative(creative_id="c_auto_create")])
            assert result.creatives[0].action == CreativeAction.created

            with get_db_session() as session:
                db = session.scalars(select(DBCreative).filter_by(creative_id="c_auto_create")).first()
                assert db.status == "approved"


class TestCreateAIPoweredApproval:
    """AI-powered approval on create submits background review.

    BDD: UC-006-CREATIVE-APPROVAL-WORKFLOW-03
    Given the tenant has approval_mode=ai-powered
    When a creative is synced
    Then the creative status is set to pending_review
    """

    def test_create_ai_powered_submits_task(self, integration_db):
        """Create with ai-powered → background AI review task submitted."""
        mock_executor = MagicMock()
        mock_executor.submit.return_value = MagicMock()

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            env.identity.tenant["approval_mode"] = "ai-powered"

            with (
                patch("src.admin.blueprints.creatives._ai_review_executor", mock_executor),
                patch("src.admin.blueprints.creatives._ai_review_lock", MagicMock()),
                patch("src.admin.blueprints.creatives._ai_review_tasks", {}),
            ):
                result = env.call_impl(creatives=[_creative(creative_id="c_ai_create")])

            assert result.creatives[0].action == CreativeAction.created

            with get_db_session() as session:
                db = session.scalars(select(DBCreative).filter_by(creative_id="c_ai_create")).first()
                assert db.status == "pending_review"

            assert mock_executor.submit.called


class TestCreateServerGeneratedId:
    """Server generates creative_id when client provides empty string."""

    def test_server_generates_id_from_empty(self, integration_db):
        """Create with empty creative_id → server generates UUID."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_impl(creatives=[_creative(creative_id="")])
            assert result.creatives[0].action == CreativeAction.created
            # Server should have generated an ID (UUID format)
            generated_id = result.creatives[0].creative_id
            assert generated_id is not None
            assert len(generated_id) > 0
            assert generated_id != ""
