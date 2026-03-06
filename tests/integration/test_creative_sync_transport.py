"""Multi-transport behavioral tests for creative sync.

Exercises the same behavioral obligation across IMPL, A2A, REST, and MCP
transports. Fixture setup and payload assertions are shared; only the
dispatch mechanism varies.

Covers: UC-006-MAIN-MCP-04 through UC-006-MAIN-MCP-09 (transport-paired)
Covers: UC-006-GENERATIVE-CREATIVE-BUILD-01 through BUILD-08
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from adcp.types import CreativeAction
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from src.core.exceptions import AdCPNotFoundError
from tests.harness import CreativeSyncEnv, Transport, assert_envelope

# All four transports: IMPL, A2A, REST, MCP
ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.REST, Transport.MCP]


@pytest.mark.requires_db
class TestSyncCreativeCreateTransport:
    """New creative creation via all transports."""

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_new_creative_created(self, integration_db, transport):
        """A valid creative payload creates a new creative across all transports.

        Covers: T-UC-006-main-rest, T-UC-006-main-mcp
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_transport_test",
                        "name": "Transport Test Creative",
                        "format_id": {"id": "display_300x250", "agent_url": "https://example.com/agent"},
                        "media_url": "https://example.com/image.png",
                    }
                ],
            )

        assert result.is_success, f"Expected success but got error: {result.error}"
        assert_envelope(result, transport)

        # Shared payload assertion — identical across all transports
        assert len(result.payload.creatives) == 1
        creative = result.payload.creatives[0]
        assert creative.creative_id == "c_transport_test"

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_empty_creative_list_returns_success(self, integration_db, transport):
        """Empty creative list is a valid no-op across all transports."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(transport, creatives=[])

        assert result.is_success
        assert_envelope(result, transport)
        assert len(result.payload.creatives) == 0

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_dry_run_does_not_persist(self, integration_db, transport):
        """Dry run previews changes without persisting across all transports."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_dry_run",
                        "name": "Dry Run Creative",
                        "format_id": {"id": "display_300x250", "agent_url": "https://example.com/agent"},
                        "media_url": "https://example.com/image.png",
                    }
                ],
                dry_run=True,
            )

        assert result.is_success
        assert_envelope(result, transport)
        assert result.payload.dry_run is True

        # DB verification: dry-run creative must NOT be persisted
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_dry_run", tenant_id="test_tenant")
            ).first()
            assert db_creative is None, "Dry-run creative should NOT be in the database"


DEFAULT_AGENT_URL = "https://example.com/agent"
DEFAULT_FORMAT_ID = {"id": "display_300x250", "agent_url": DEFAULT_AGENT_URL}


def _creative(creative_id: str = "c1", name: str = "Test", **overrides) -> dict:
    """Build a minimal creative dict for transport tests."""
    defaults = {
        "creative_id": creative_id,
        "name": name,
        "format_id": DEFAULT_FORMAT_ID,
        "assets": {"banner": {"url": "https://example.com/image.png"}},
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.requires_db
class TestSyncUpsertReturnsUpdatedTransport:
    """Re-syncing an existing creative returns action="updated" with changes list.

    Covers: UC-006-MAIN-MCP-04
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_upsert_existing_creative_reports_updated(self, integration_db, transport):
        """Syncing a creative that already exists returns action=updated."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # First sync: create the creative (same transport as upsert)
            creative_data = _creative(creative_id="c_upsert")
            env.call_via(transport, creatives=[creative_data])

            # Second sync via parametrized transport: upsert
            result = env.call_via(transport, creatives=[creative_data])

        assert result.is_success, f"Expected success but got error: {result.error}"
        assert_envelope(result, transport)
        assert len(result.payload.creatives) == 1
        upserted = result.payload.creatives[0]
        assert upserted.creative_id == "c_upsert"
        assert upserted.action == CreativeAction.updated


@pytest.mark.requires_db
class TestSyncSavepointIsolationTransport:
    """Good creatives persist even when another creative in the batch fails.

    Covers: UC-006-MAIN-MCP-05
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_good_creative_persists_despite_bad_in_batch(self, integration_db, transport):
        """Savepoint isolation: bad creative doesn't roll back good ones."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(
                transport,
                creatives=[
                    _creative(creative_id="c_good_1", name="Good One"),
                    _creative(creative_id="c_bad", name=""),  # empty name → validation fail
                    _creative(creative_id="c_good_2", name="Good Two"),
                ],
                validation_mode="lenient",
            )

        assert result.is_success
        assert_envelope(result, transport)
        assert len(result.payload.creatives) == 3

        results_by_id = {r.creative_id: r for r in result.payload.creatives}
        assert results_by_id["c_bad"].action == CreativeAction.failed
        assert results_by_id["c_good_1"].action != CreativeAction.failed
        assert results_by_id["c_good_2"].action != CreativeAction.failed

        # DB verification: good creatives persisted, bad creative did not
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with get_db_session() as session:
            for cid in ("c_good_1", "c_good_2"):
                db_creative = session.scalars(
                    select(DBCreative).filter_by(creative_id=cid, tenant_id="test_tenant")
                ).first()
                assert db_creative is not None, f"{cid} should be persisted in DB"

            bad_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_bad", tenant_id="test_tenant")
            ).first()
            assert bad_creative is None, "Failed creative should NOT be in the database"


@pytest.mark.requires_db
class TestSyncStrictModeAbortTransport:
    """Strict mode aborts the assignment phase on missing package.

    Covers: UC-006-MAIN-MCP-06
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_strict_mode_missing_package_aborts(self, integration_db, transport):
        """Strict validation_mode raises on missing package assignment."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(
                transport,
                creatives=[_creative(creative_id="c_strict", name="Strict Test")],
                assignments={"c_strict": ["PKG-NONEXISTENT"]},
                validation_mode="strict",
            )

        assert result.is_error, "Strict mode should error on missing package"
        assert isinstance(result.error, AdCPNotFoundError)


@pytest.mark.requires_db
class TestSyncLenientModeContinuesTransport:
    """Lenient mode records assignment errors without aborting.

    Covers: UC-006-MAIN-MCP-07
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_lenient_mode_missing_package_records_error(self, integration_db, transport):
        """Lenient validation_mode logs error and continues past missing package."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(
                transport,
                creatives=[_creative(creative_id="c_lenient", name="Lenient Test")],
                assignments={"c_lenient": ["PKG-MISSING"]},
                validation_mode="lenient",
            )

        assert result.is_success
        assert_envelope(result, transport)
        assert len(result.payload.creatives) == 1
        creative_result = result.payload.creatives[0]
        assert creative_result.assignment_errors is not None
        assert "PKG-MISSING" in creative_result.assignment_errors


@pytest.mark.requires_db
class TestSyncFormatValidationTransport:
    """Format validation runs before DB writes — unknown format → failed.

    Covers: UC-006-MAIN-MCP-08
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_unknown_format_fails_before_db_write(self, integration_db, transport):
        """Creative with unknown format_id gets action=failed."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # Override: registry.get_format returns None (format not found)
            registry_mock = env.mock["registry"].return_value
            registry_mock.get_format = AsyncMock(return_value=None)

            result = env.call_via(
                transport,
                creatives=[_creative(creative_id="c_bad_fmt", name="Bad Format")],
            )

        assert result.is_success  # sync itself succeeds, individual creative fails
        assert_envelope(result, transport)
        assert len(result.payload.creatives) == 1
        creative_result = result.payload.creatives[0]
        assert creative_result.action == CreativeAction.failed
        assert any("list_creative_formats" in e for e in (creative_result.errors or []))


@pytest.mark.requires_db
class TestSyncRegistryCachingTransport:
    """Registry is queried once per sync, not per creative.

    Covers: UC-006-MAIN-MCP-09
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_registry_called_once_for_multiple_creatives(self, integration_db, transport):
        """list_all_formats is called once regardless of creative count."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(
                transport,
                creatives=[
                    _creative(creative_id="c_cache_1", name="Cache Test 1"),
                    _creative(creative_id="c_cache_2", name="Cache Test 2"),
                    _creative(creative_id="c_cache_3", name="Cache Test 3"),
                ],
            )

            assert result.is_success
            assert_envelope(result, transport)
            # list_all_formats called once per sync, not per creative
            registry = env.mock["registry"].return_value
            assert registry.list_all_formats.call_count == 1


# ---------------------------------------------------------------------------
# Generative Creative Build Tests
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestGenerativeBuildClassification:
    """Format with output_format_ids classified as generative."""

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_generative_format_calls_build_creative(self, integration_db, transport):
        """Covers: UC-006-GENERATIVE-CREATIVE-BUILD-01

        A format with output_format_ids triggers build_creative, not preview_creative.
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build()

            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_gen_01",
                        "name": "Generative Banner",
                        "format_id": fmt,
                        "assets": {"message": {"content": "Build me a banner"}},
                    }
                ],
            )

            assert result.is_success, f"Expected success but got error: {result.error}"
            assert_envelope(result, transport)
            assert len(result.payload.creatives) == 1
            assert result.payload.creatives[0].action == CreativeAction.created

            # Verify build_creative was called (generative path)
            registry = env.mock["registry"].return_value
            assert registry.build_creative.called, "build_creative should be called for generative format"

        # Verify DB has generative data
        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_gen_01", tenant_id="test_tenant")
            ).first()
            assert db_creative is not None
            assert db_creative.data.get("generative_status") == "draft"
            assert db_creative.data.get("generative_context_id") == "ctx-test-123"


@pytest.mark.requires_db
class TestGenerativeBuildPromptMessage:
    """Prompt extracted from message asset role."""

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_message_role_used_as_prompt(self, integration_db, transport):
        """Covers: UC-006-GENERATIVE-CREATIVE-BUILD-02

        The 'message' asset role content is passed as the build prompt.
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build()

            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_gen_02",
                        "name": "Message Test",
                        "format_id": fmt,
                        "assets": {"message": {"content": "Create a holiday banner"}},
                    }
                ],
            )

            assert result.is_success
            assert_envelope(result, transport)

            call_args = env.mock["registry"].return_value.build_creative.call_args
            assert call_args is not None
            assert call_args[1]["message"] == "Create a holiday banner"


@pytest.mark.requires_db
class TestGenerativeBuildPromptBrief:
    """Prompt extracted from brief asset role (fallback)."""

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_brief_role_used_when_no_message(self, integration_db, transport):
        """Covers: UC-006-GENERATIVE-CREATIVE-BUILD-03

        When no 'message' asset, 'brief' role content is used as prompt.
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build()

            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_gen_03",
                        "name": "Brief Test",
                        "format_id": fmt,
                        "assets": {"brief": {"content": "Promote summer sale"}},
                    }
                ],
            )

            assert result.is_success
            assert_envelope(result, transport)

            call_args = env.mock["registry"].return_value.build_creative.call_args
            assert call_args is not None
            assert call_args[1]["message"] == "Promote summer sale"


@pytest.mark.requires_db
class TestGenerativeBuildPromptRole:
    """Prompt extracted from prompt asset role (fallback)."""

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_prompt_role_used_when_no_message_or_brief(self, integration_db, transport):
        """Covers: UC-006-GENERATIVE-CREATIVE-BUILD-04

        When no 'message' or 'brief' asset, 'prompt' role content is used.
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build()

            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_gen_04",
                        "name": "Prompt Role Test",
                        "format_id": fmt,
                        "assets": {"prompt": {"content": "Design a Q4 campaign banner"}},
                    }
                ],
            )

            assert result.is_success
            assert_envelope(result, transport)

            call_args = env.mock["registry"].return_value.build_creative.call_args
            assert call_args is not None
            assert call_args[1]["message"] == "Design a Q4 campaign banner"


@pytest.mark.requires_db
class TestGenerativeBuildPromptInputs:
    """Prompt from inputs[0].context_description."""

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_inputs_context_description_as_prompt(self, integration_db, transport):
        """Covers: UC-006-GENERATIVE-CREATIVE-BUILD-05

        When no message/brief/prompt assets, inputs[0].context_description is used.
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build()

            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_gen_05",
                        "name": "Inputs Test",
                        "format_id": fmt,
                        "inputs": [{"name": "q4_brief", "context_description": "Design for Q4 campaign"}],
                    }
                ],
            )

            assert result.is_success
            assert_envelope(result, transport)

            call_args = env.mock["registry"].return_value.build_creative.call_args
            assert call_args is not None
            assert call_args[1]["message"] == "Design for Q4 campaign"


@pytest.mark.requires_db
class TestGenerativeBuildNameFallback:
    """Creative name as fallback prompt on CREATE."""

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_name_used_as_fallback_prompt(self, integration_db, transport):
        """Covers: UC-006-GENERATIVE-CREATIVE-BUILD-06

        When no assets and no inputs, 'Create a creative for: {name}' is used.
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build()

            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_gen_06",
                        "name": "Holiday Sale Banner",
                        "format_id": fmt,
                    }
                ],
            )

            assert result.is_success
            assert_envelope(result, transport)

            call_args = env.mock["registry"].return_value.build_creative.call_args
            assert call_args is not None
            assert call_args[1]["message"] == "Create a creative for: Holiday Sale Banner"


@pytest.mark.requires_db
class TestGenerativeBuildUpdatePreserve:
    """Update without prompt preserves existing data."""

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_update_without_prompt_skips_build(self, integration_db, transport):
        """Covers: UC-006-GENERATIVE-CREATIVE-BUILD-07

        An UPDATE with no prompt in assets/inputs skips build_creative
        and preserves existing generative data.
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build()

            # First sync: CREATE with a prompt
            result1 = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_gen_07",
                        "name": "Preserve Test",
                        "format_id": fmt,
                        "assets": {"message": {"content": "Initial prompt"}},
                    }
                ],
            )
            assert result1.is_success

            # Record build call count after first sync
            registry = env.mock["registry"].return_value
            build_calls_after_create = registry.build_creative.call_count

            # Second sync: UPDATE with no prompt assets
            result2 = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_gen_07",
                        "name": "Preserve Test Updated Name",
                        "format_id": fmt,
                    }
                ],
            )

            assert result2.is_success
            assert_envelope(result2, transport)

            # build_creative should NOT be called again (no prompt → skip build)
            assert registry.build_creative.call_count == build_calls_after_create, (
                "build_creative should not be called on update without prompt"
            )

        # Verify existing generative data is preserved in DB
        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_gen_07", tenant_id="test_tenant")
            ).first()
            assert db_creative is not None
            assert db_creative.data.get("generative_status") == "draft"
            assert db_creative.data.get("generative_context_id") == "ctx-test-123"


@pytest.mark.requires_db
class TestGenerativeBuildUserAssetPriority:
    """User assets take priority over generative output."""

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_user_assets_not_overwritten(self, integration_db, transport):
        """Covers: UC-006-GENERATIVE-CREATIVE-BUILD-08

        When user provides assets AND a generative prompt, user assets
        are preserved (not overwritten by generative output).
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build(
                build_result={
                    "status": "draft",
                    "context_id": "ctx-priority",
                    "creative_output": {
                        "assets": {"headline": {"text": "AI-generated headline"}},
                        "output_format": {"url": "https://generated.example.com/ai.html"},
                    },
                },
            )

            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_gen_08",
                        "name": "Asset Priority Test",
                        "format_id": fmt,
                        "assets": {
                            "message": {"content": "Build me a banner"},
                            "headline": {"content": "User-provided headline"},
                        },
                        "url": "https://user.example.com/image.png",
                    }
                ],
            )

            assert result.is_success
            assert_envelope(result, transport)

            # build_creative is still called (we have a message prompt)
            registry = env.mock["registry"].return_value
            assert registry.build_creative.called

        # Verify user assets preserved in DB (not overwritten by generative output)
        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_gen_08", tenant_id="test_tenant")
            ).first()
            assert db_creative is not None
            # User-provided URL should be preserved (not overwritten by generative output)
            assert db_creative.data.get("url") == "https://user.example.com/image.png"
            # User-provided assets should be preserved
            assets = db_creative.data.get("assets", {})
            assert "headline" in assets
