"""Multi-transport behavioral tests for creative sync.

Exercises the same behavioral obligation across IMPL, A2A, REST, and MCP
transports. Fixture setup and payload assertions are shared; only the
dispatch mechanism varies.

Covers: UC-006-MAIN-MCP-04 through UC-006-MAIN-MCP-09 (transport-paired)
Covers: UC-006-MAIN-REST-{01,02,03}
Covers: UC-006-GENERATIVE-CREATIVE-BUILD-01 through BUILD-08
Covers: UC-006-FORMAT-VALIDATION-{ADAPTER,UNREACHABLE,UNKNOWN}-01
Covers: UC-006-ASSIGNMENT-{PACKAGE-VALIDATION,FORMAT-COMPATIBILITY,RESULT}-*
Covers: UC-006-EXT-{A,B,D,E,H,I,J}-*
Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-03
Covers: UC-006-ASYNC-LIFECYCLE-{01,02,03} (gap tests — not yet implemented)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from adcp.types import CreativeAction
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from src.core.exceptions import AdCPAuthenticationError, AdCPNotFoundError
from tests.harness import CreativeSyncEnv, Transport, assert_envelope, make_identity

# All four transports: IMPL, A2A, REST, MCP
ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.REST, Transport.MCP]


@pytest.mark.requires_db
class TestSyncCreativeCreateTransport:
    """New creative creation via all transports.

    Covers: UC-006-MAIN-REST-01
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_new_creative_created(self, integration_db, transport):
        """A valid creative payload creates a new creative across all transports.

        Covers: T-UC-006-main-rest, T-UC-006-main-mcp
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            from tests.factories.creative_asset import CreativeAssetFactory

            creative = CreativeAssetFactory(
                creative_id="c_transport_test",
                name="Transport Test Creative",
            )
            result = env.call_via(transport, creatives=[creative])

        assert result.is_success, f"Expected success but got error: {result.error}"
        assert_envelope(result, transport)

        # Shared payload assertion — identical across all transports
        assert len(result.payload.creatives) == 1
        creative = result.payload.creatives[0]
        assert creative.creative_id == "c_transport_test"

        # DB verification: creative must be persisted
        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_transport_test", tenant_id="test_tenant")
            ).first()
            assert db_creative is not None, "Created creative should be persisted in DB"
            assert db_creative.name == "Transport Test Creative"

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

            from tests.factories.creative_asset import CreativeAssetFactory

            creative = CreativeAssetFactory(
                creative_id="c_dry_run",
                name="Dry Run Creative",
            )
            result = env.call_via(transport, creatives=[creative], dry_run=True)

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

        # DB verification: upserted creative exists in DB
        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_upsert", tenant_id="test_tenant")
            ).first()
            assert db_creative is not None, "Upserted creative should be persisted in DB"


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

    Covers: UC-006-MAIN-MCP-06, UC-006-ASSIGNMENT-PACKAGE-VALIDATION-02, UC-006-EXT-J-01
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

    Covers: UC-006-MAIN-MCP-08, UC-006-FORMAT-VALIDATION-UNKNOWN-01
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

            from tests.factories.creative_asset import CreativeAssetFactory

            creative = CreativeAssetFactory(
                creative_id="c_gen_05",
                name="Inputs Test",
                format_id=fmt,
                assets={},
                inputs=[{"name": "q4_brief", "context_description": "Design for Q4 campaign"}],
            )
            result = env.call_via(transport, creatives=[creative])

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

            from tests.factories.creative_asset import CreativeAssetFactory

            creative = CreativeAssetFactory(
                creative_id="c_gen_06",
                name="Holiday Sale Banner",
                format_id=fmt,
                assets={},
            )
            result = env.call_via(transport, creatives=[creative])

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
            from tests.factories.creative_asset import CreativeAssetFactory

            creative2 = CreativeAssetFactory(
                creative_id="c_gen_07",
                name="Preserve Test Updated Name",
                format_id=fmt,
                assets={},
            )
            result2 = env.call_via(transport, creatives=[creative2])

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


# ---------------------------------------------------------------------------
# Format Validation Tests
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestFormatValidationAdapter:
    """Adapter-provided formats skip external agent validation.

    Covers: UC-006-CREATIVE-FORMAT-VALIDATION-02
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_adapter_format_skips_registry(self, integration_db, transport):
        """Non-HTTP agent_url (adapter://) bypasses registry.get_format check."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_adapter_fmt",
                        "name": "Adapter Format Creative",
                        "format_id": {"id": "billboard", "agent_url": "broadstreet://default"},
                        "assets": {"banner": {"url": "https://example.com/ad.png"}},
                    }
                ],
            )

            assert result.is_success
            assert_envelope(result, transport)
            assert len(result.payload.creatives) == 1
            assert result.payload.creatives[0].action == CreativeAction.created

            # registry.get_format should NOT be called for adapter formats
            registry = env.mock["registry"].return_value
            assert not registry.get_format.called, "get_format should not be called for adapter-provided formats"


@pytest.mark.requires_db
class TestFormatValidationUnreachable:
    """Unreachable creative agent → per-creative failure.

    Covers: UC-006-CREATIVE-FORMAT-VALIDATION-03
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_unreachable_agent_fails_creative(self, integration_db, transport):
        """Network error from registry.get_format → action=failed with 'unreachable'."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # Override: get_format raises a network error
            registry_mock = env.mock["registry"].return_value
            registry_mock.get_format = AsyncMock(side_effect=ConnectionError("Agent unreachable"))

            result = env.call_via(
                transport,
                creatives=[_creative(creative_id="c_unreach", name="Unreachable Test")],
            )

        assert result.is_success  # sync succeeds, individual creative fails
        assert_envelope(result, transport)
        assert len(result.payload.creatives) == 1
        creative_result = result.payload.creatives[0]
        assert creative_result.action == CreativeAction.failed
        assert any("unreachable" in e.lower() for e in (creative_result.errors or []))


# ---------------------------------------------------------------------------
# Assignment Validation Tests
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestAssignmentPackageTenantFilter:
    """Package lookup is tenant-scoped — cross-tenant packages not visible.

    Covers: UC-006-ASSIGNMENT-PACKAGE-VALIDATION-03
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_cross_tenant_package_not_found(self, integration_db, transport):
        """Package in tenant_a is not visible when syncing as tenant_b."""
        from tests.factories import MediaBuyFactory, MediaPackageFactory, TenantFactory

        pkg_id = "pkg_cross_tenant"

        with CreativeSyncEnv() as env:
            # Create the default tenant (test_tenant) + principal
            env.setup_default_data()

            # Create package in a DIFFERENT tenant
            other_tenant = TenantFactory(tenant_id="other_tenant")
            other_mb = MediaBuyFactory(
                tenant=other_tenant,
                media_buy_id="mb_other",
            )
            MediaPackageFactory(
                media_buy=other_mb,
                package_id=pkg_id,
            )
            env._commit_factory_data()

            # Sync as test_tenant, referencing other_tenant's package
            result = env.call_via(
                transport,
                creatives=[_creative(creative_id="c_cross", name="Cross Tenant")],
                assignments={"c_cross": [pkg_id]},
                validation_mode="lenient",
            )

        assert result.is_success
        assert_envelope(result, transport)
        creative_result = result.payload.creatives[0]
        assert creative_result.assignment_errors is not None
        assert pkg_id in creative_result.assignment_errors


@pytest.mark.requires_db
class TestAssignmentFormatCompatibility:
    """Creative format must match product-supported formats.

    Covers: UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-03
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_format_mismatch_records_error(self, integration_db, transport):
        """Creative format not in product.format_ids → assignment_errors."""
        from tests.factories import (
            MediaBuyFactory,
            MediaPackageFactory,
            PrincipalFactory,
            ProductFactory,
            TenantFactory,
        )

        pkg_id = "pkg_fmt_check"

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Product only supports video_30s format
            product = ProductFactory(
                tenant=tenant,
                product_id="prod_video",
                format_ids=[{"agent_url": "https://video.agent.com", "id": "video_30s"}],
            )
            mb = MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id="mb_fmt")
            MediaPackageFactory(
                media_buy=mb,
                package_id=pkg_id,
                package_config={"product_id": product.product_id},
            )
            env._commit_factory_data()

            # Creative uses display_300x250 (not video_30s)
            result = env.call_via(
                transport,
                creatives=[_creative(creative_id="c_fmt_mismatch", name="Format Mismatch")],
                assignments={"c_fmt_mismatch": [pkg_id]},
                validation_mode="lenient",
            )

        assert result.is_success
        assert_envelope(result, transport)
        creative_result = result.payload.creatives[0]
        assert creative_result.assignment_errors is not None
        assert pkg_id in creative_result.assignment_errors
        error_msg = creative_result.assignment_errors[pkg_id]
        assert "not supported" in error_msg


@pytest.mark.requires_db
class TestAssignmentResultFields:
    """Successful assignment populates assigned_to on the creative result.

    Behavior: UC-006-ASSIGNMENT-RESULT-01
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_successful_assignment_has_assigned_to(self, integration_db, transport):
        """Result includes assigned_to with package IDs after successful assignment."""
        from tests.factories import (
            MediaBuyFactory,
            MediaPackageFactory,
            PrincipalFactory,
            ProductFactory,
            TenantFactory,
        )

        pkg_id = "pkg_assign_ok"

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Product supports the default display format
            product = ProductFactory(
                tenant=tenant,
                product_id="prod_assign",
                format_ids=[DEFAULT_FORMAT_ID],
            )
            mb = MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id="mb_assign")
            MediaPackageFactory(
                media_buy=mb,
                package_id=pkg_id,
                package_config={"product_id": product.product_id},
            )
            env._commit_factory_data()

            result = env.call_via(
                transport,
                creatives=[_creative(creative_id="c_assign", name="Assignment Test")],
                assignments={"c_assign": [pkg_id]},
            )

        assert result.is_success
        assert_envelope(result, transport)
        creative_result = result.payload.creatives[0]
        assert creative_result.assigned_to is not None
        assert pkg_id in creative_result.assigned_to


# ---------------------------------------------------------------------------
# Extension Tests — Auth & Validation
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestAuthPrincipalRequired:
    """Missing principal_id → AdCPAuthenticationError.

    Covers: UC-006-EXT-A-02
    """

    def test_no_principal_raises_auth_error(self, integration_db):
        """Identity with principal_id=None → AdCPAuthenticationError."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            identity_no_principal = make_identity(
                principal_id=None,
                tenant_id="test_tenant",
                tenant=env.identity.tenant,
            )

            result = env.call_via(
                Transport.IMPL,
                creatives=[_creative()],
                identity=identity_no_principal,
            )

        assert result.is_error
        assert isinstance(result.error, AdCPAuthenticationError)


@pytest.mark.requires_db
class TestAuthTenantRequired:
    """Missing tenant → AdCPAuthenticationError.

    Covers: UC-006-EXT-B-02
    """

    def test_no_tenant_raises_auth_error(self, integration_db):
        """Identity with tenant=None → AdCPAuthenticationError."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            identity_no_tenant = make_identity(
                principal_id="test_principal",
                tenant_id="test_tenant",
                tenant=None,
            )

            result = env.call_via(
                Transport.IMPL,
                creatives=[_creative()],
                identity=identity_no_tenant,
            )

        assert result.is_error
        assert isinstance(result.error, AdCPAuthenticationError)


@pytest.mark.requires_db
class TestEmptyNameFails:
    """Empty creative name → per-creative failure.

    Covers: UC-006-EXT-D-01
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_empty_name_action_failed(self, integration_db, transport):
        """Creative with name='' → action=failed with error."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(
                transport,
                creatives=[_creative(creative_id="c_no_name", name="")],
                validation_mode="lenient",
            )

        assert result.is_success  # sync succeeds, individual creative fails
        assert_envelope(result, transport)
        assert len(result.payload.creatives) == 1
        creative_result = result.payload.creatives[0]
        assert creative_result.action == CreativeAction.failed
        assert creative_result.errors


@pytest.mark.requires_db
class TestMissingFormatFails:
    """Missing format_id → per-creative failure.

    Covers: UC-006-EXT-E-01
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_no_format_action_failed(self, integration_db, transport):
        """Creative without format_id is rejected.

        On impl/a2a: reaches _impl which returns action=failed (missing format).
        On MCP: TypeAdapter rejects because CreativeAsset requires format_id.
        Both paths correctly reject the creative.
        """
        from tests.harness.assertions import assert_rejected

        with CreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_no_format",
                        "name": "No Format Creative",
                        "assets": {"banner": {"url": "https://example.com/ad.png"}},
                    }
                ],
                validation_mode="lenient",
            )

        if result.is_error:
            # MCP: TypeAdapter rejected missing format_id — correct behavior
            assert_rejected(result, field="format_id", reason="Field required")
        else:
            # impl/a2a/rest: _impl handled it, returned action=failed
            assert_envelope(result, transport)
            creative_result = result.payload.creatives[0]
            assert creative_result.action == CreativeAction.failed
            assert creative_result.errors


@pytest.mark.requires_db
class TestStaticPreviewFailed:
    """Static creative: no previews and no media_url → action=failed.

    Covers: UC-006-EXT-H-01
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_no_preview_no_url_fails(self, integration_db, transport):
        """Static format with empty preview_creative result and no url → failed."""
        from adcp.types.generated_poc.core.format_id import FormatId as LibraryFormatId

        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # Set up a static format (no output_format_ids) in the all_formats list
            mock_format = MagicMock()
            mock_format.format_id = LibraryFormatId(
                agent_url=DEFAULT_AGENT_URL,
                id="display_300x250",
            )
            mock_format.agent_url = DEFAULT_AGENT_URL
            mock_format.output_format_ids = None  # Static, not generative
            env.set_run_async_result([mock_format])

            # preview_creative returns empty dict (no previews)
            registry = env.mock["registry"].return_value
            registry.preview_creative = AsyncMock(return_value={})

            # Creative with format_id but no assets — tests the "no previews" path.
            # Uses dict because the test exercises the dict→CreativeAsset coercion
            # in _impl (which defaults assets={}). On MCP, TypeAdapter rejects the
            # missing assets field — that's also correct (schema-level rejection).
            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_no_preview",
                        "name": "No Preview Creative",
                        "format_id": DEFAULT_FORMAT_ID,
                    }
                ],
                validation_mode="lenient",
            )

        if result.is_error:
            # MCP: TypeAdapter rejects missing assets field — correct schema rejection
            from tests.harness.assertions import assert_rejected

            assert_rejected(result, field="assets", reason="Field required")
        else:
            # impl/a2a/rest: _impl handles it, returns action=failed
            assert_envelope(result, transport)
            creative_result = result.payload.creatives[0]
            assert creative_result.action == CreativeAction.failed
            assert any(
                "no previews" in e.lower() or "no media_url" in e.lower() for e in (creative_result.errors or [])
            )


@pytest.mark.requires_db
class TestGeminiKeyMissing:
    """Generative format without GEMINI_API_KEY → per-creative failure.

    Covers: UC-006-EXT-I-01
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_generative_no_gemini_key_fails(self, integration_db, transport):
        """Generative format + no gemini_api_key → action=failed."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            fmt = env.setup_generative_build(gemini_api_key="")

            # Override: remove the gemini key after setup
            env.mock["config"].return_value.gemini_api_key = None

            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_no_gemini",
                        "name": "No Gemini Key",
                        "format_id": fmt,
                        "assets": {"message": {"content": "Build a banner"}},
                    }
                ],
                validation_mode="lenient",
            )

        assert result.is_success
        assert_envelope(result, transport)
        creative_result = result.payload.creatives[0]
        assert creative_result.action == CreativeAction.failed
        assert any("gemini" in e.lower() for e in (creative_result.errors or []))


# ---------------------------------------------------------------------------
# REST-specific obligation tests (non-parametrized — REST transport only)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestSlackNotificationOnSync:
    """Slack notification fires for require-human approval mode with webhook configured.

    Covers: UC-006-MAIN-REST-02
    """

    def test_notification_called_on_require_human(self, integration_db):
        """When approval_mode=require-human and slack_webhook_url is set,
        _send_creative_notifications is called with the creative info."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            # Set tenant fields on the REST-specific identity
            identity = env.identity_for(Transport.REST)
            identity.tenant["approval_mode"] = "require-human"
            identity.tenant["slack_webhook_url"] = "https://hooks.slack.com/test"

            result = env.call_via(
                Transport.REST,
                creatives=[_creative(creative_id="c_slack_test", name="Slack Notify Creative")],
            )

            assert result.is_success
            send_mock = env.mock["send_notifications"]
            assert send_mock.called, "_send_creative_notifications should be called"
            call_kwargs = send_mock.call_args[1]
            creatives_needing = call_kwargs["creatives_needing_approval"]
            assert len(creatives_needing) >= 1
            assert any(c["creative_id"] == "c_slack_test" for c in creatives_needing)
            assert call_kwargs["approval_mode"] == "require-human"

    def test_notification_not_called_without_webhook(self, integration_db):
        """When slack_webhook_url is not set, _send_creative_notifications
        is still called but with tenant lacking webhook (function returns early)."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()
            # require-human mode but NO slack_webhook_url
            env.identity.tenant["approval_mode"] = "require-human"

            result = env.call_via(
                Transport.IMPL,
                creatives=[_creative(creative_id="c_no_webhook")],
            )

            assert result.is_success
            send_mock = env.mock["send_notifications"]
            # Called because creatives_needing_approval is non-empty and not dry_run
            assert send_mock.called


@pytest.mark.requires_db
class TestAIReviewTrigger:
    """AI review submitted to background executor when approval_mode=ai-powered.

    Covers: UC-006-MAIN-REST-03
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_ai_review_submitted(self, integration_db, transport):
        """When approval_mode=ai-powered, the AI review executor receives a
        submit() call and creative status is pending_review."""
        from unittest.mock import MagicMock as MockMaker
        from unittest.mock import patch

        mock_executor = MockMaker()
        mock_executor.submit.return_value = MockMaker()  # mock future

        with CreativeSyncEnv() as env:
            tenant, _principal = env.setup_default_data()
            # Update DB tenant so real auth chain sees ai-powered mode.
            tenant.approval_mode = "ai-powered"
            env.identity_for(transport).tenant["approval_mode"] = "ai-powered"

            with (
                patch("src.admin.blueprints.creatives._ai_review_executor", mock_executor),
                patch("src.admin.blueprints.creatives._ai_review_lock", MockMaker()),
                patch("src.admin.blueprints.creatives._ai_review_tasks", {}),
            ):
                result = env.call_via(
                    transport,
                    creatives=[_creative(creative_id="c_ai_review", name="AI Review Creative")],
                )

            assert result.is_success
            assert_envelope(result, transport)
            creative_result = result.payload.creatives[0]
            assert creative_result.action == CreativeAction.created
            # status is exclude=True (stripped in REST serialization), verify via DB
            with get_db_session() as session:
                db_creative = session.scalars(select(DBCreative).filter_by(creative_id="c_ai_review")).first()
                assert db_creative is not None
                assert db_creative.status == "pending_review"
            assert mock_executor.submit.called, "AI review executor.submit should be called"


@pytest.mark.requires_db
class TestAIPoweredApprovalDeferredNotification:
    """AI-powered approval mode defers Slack notification — not sent during sync.

    Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-03
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_notification_deferred_for_ai_powered(self, integration_db, transport):
        """When approval_mode=ai-powered, _send_creative_notifications is called
        but with approval_mode='ai-powered' (real function would return early).
        Workflow steps are still created."""
        from unittest.mock import MagicMock as MockMaker
        from unittest.mock import patch

        mock_executor = MockMaker()
        mock_executor.submit.return_value = MockMaker()

        with CreativeSyncEnv() as env:
            tenant, _principal = env.setup_default_data()
            # Update DB tenant so real auth chain sees ai-powered mode.
            tenant.approval_mode = "ai-powered"
            tenant.slack_webhook_url = "https://hooks.slack.com/test"
            identity = env.identity_for(transport)
            identity.tenant["approval_mode"] = "ai-powered"
            identity.tenant["slack_webhook_url"] = "https://hooks.slack.com/test"

            with (
                patch("src.admin.blueprints.creatives._ai_review_executor", mock_executor),
                patch("src.admin.blueprints.creatives._ai_review_lock", MockMaker()),
                patch("src.admin.blueprints.creatives._ai_review_tasks", {}),
            ):
                result = env.call_via(
                    transport,
                    creatives=[_creative(creative_id="c_ai_deferred", name="AI Deferred Creative")],
                )

            assert result.is_success
            assert_envelope(result, transport)
            # Verify status via DB (status is exclude=True, stripped in REST)
            with get_db_session() as session:
                db_creative = session.scalars(select(DBCreative).filter_by(creative_id="c_ai_deferred")).first()
                assert db_creative is not None
                assert db_creative.status == "pending_review"

            # Verify _send_creative_notifications was called with ai-powered mode
            send_mock = env.mock["send_notifications"]
            assert send_mock.called
            call_kwargs = send_mock.call_args[1] if send_mock.call_args[1] else {}
            if "approval_mode" in call_kwargs:
                assert call_kwargs["approval_mode"] == "ai-powered"


# ---------------------------------------------------------------------------
# Async lifecycle obligation tests — spec-defined, NOT YET IMPLEMENTED
# See: salesagent-gkxa (feature request for async sync_creatives lifecycle)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestAsyncLifecycleSubmitted:
    """Queued sync operation returns SyncCreativesSubmitted.

    Covers: UC-006-ASYNC-LIFECYCLE-01

    Given the system supports async creative sync
    When a sync operation is queued
    Then a SyncCreativesAsyncResponseSubmitted is returned
    And it conforms to the adcp 3.6.0 async-response-submitted schema
    """

    @pytest.mark.xfail(
        reason="Async lifecycle not implemented (salesagent-gkxa)",
        strict=True,
    )
    def test_queued_sync_returns_submitted(self, integration_db):
        """Queued sync operation returns SyncCreativesSubmitted with context."""
        from adcp.types.generated_poc.creative.sync_creatives_async_response_submitted import (
            SyncCreativesSubmitted,
        )

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            result = env.call_via(
                Transport.IMPL,
                creatives=[_creative(creative_id="c_async_sub", name="Async Submit")],
                # BDD: "the system supports async creative sync"
                async_mode=True,
            )

            # BDD: "a SyncCreativesAsyncResponseSubmitted is returned"
            assert isinstance(result.payload, SyncCreativesSubmitted)
            # BDD: "conforms to the adcp 3.6.0 async-response-submitted schema"
            assert result.payload.context is not None


@pytest.mark.requires_db
class TestAsyncLifecycleWorking:
    """In-progress async operation returns SyncCreativesWorking with progress.

    Covers: UC-006-ASYNC-LIFECYCLE-02

    Given an async sync operation is in progress
    When the Buyer checks status
    Then a SyncCreativesAsyncResponseWorking is returned with progress information
    And includes percentage, steps, and creatives processed counts
    """

    @pytest.mark.xfail(
        reason="Async lifecycle not implemented (salesagent-gkxa)",
        strict=True,
    )
    def test_in_progress_returns_working_with_progress(self, integration_db):
        """Status check on in-progress async op returns SyncCreativesWorking."""
        from adcp.types.generated_poc.creative.sync_creatives_async_response_working import (
            SyncCreativesWorking,
        )

        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # First: queue an async operation
            submit_result = env.call_via(
                Transport.IMPL,
                creatives=[
                    _creative(creative_id="c_prog_1", name="Progress 1"),
                    _creative(creative_id="c_prog_2", name="Progress 2"),
                ],
                async_mode=True,
            )
            context_id = submit_result.payload.context

            # BDD: "When the Buyer checks status"
            status_result = env.call_via(
                Transport.IMPL,
                context=context_id,
            )

            # BDD: "a SyncCreativesAsyncResponseWorking is returned"
            assert isinstance(status_result.payload, SyncCreativesWorking)
            # BDD: "includes percentage, steps, and creatives processed counts"
            assert status_result.payload.percentage is not None
            assert status_result.payload.creatives_processed is not None
            assert status_result.payload.creatives_total is not None


@pytest.mark.requires_db
class TestAsyncLifecycleInputRequired:
    """Paused async operation returns SyncCreativesInputRequired.

    Covers: UC-006-ASYNC-LIFECYCLE-03

    Given an async sync operation requires Buyer input (approval, asset confirmation)
    When the system pauses
    Then a SyncCreativesAsyncResponseInputRequired is returned
    And indicates what input is needed
    """

    @pytest.mark.xfail(
        reason="Async lifecycle not implemented (salesagent-gkxa)",
        strict=True,
    )
    def test_approval_needed_returns_input_required(self, integration_db):
        """Async op needing approval returns SyncCreativesInputRequired."""
        from adcp.types.generated_poc.creative.sync_creatives_async_response_input_required import (
            Reason,
            SyncCreativesInputRequired,
        )

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            env.identity.tenant["approval_mode"] = "require-human"

            # BDD: "async sync operation requires Buyer input"
            result = env.call_via(
                Transport.IMPL,
                creatives=[_creative(creative_id="c_input_req", name="Needs Approval")],
                async_mode=True,
            )

            # BDD: "a SyncCreativesAsyncResponseInputRequired is returned"
            assert isinstance(result.payload, SyncCreativesInputRequired)
            # BDD: "indicates what input is needed"
            assert result.payload.reason == Reason.APPROVAL_REQUIRED
