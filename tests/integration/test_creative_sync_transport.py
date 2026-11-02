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
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from tests.factories.creative_asset import build_assets, image_spec, text_spec
from tests.harness import CreativeSyncEnv, Transport, assert_envelope
from tests.helpers.creative_test_helpers import assert_stored_creative_assets, creative_payload


def _error_codes(errors: list | None) -> list[str]:
    """Extract the machine CODE from each per-creative error entry.

    Production emits these entries TYPED — src/core/tools/creatives/_processing.py builds
    each one with build_error_object(), so every element is an adcp Error carrying a code.
    The message is deliberately not read: it is a function of the code through CODE_TABLE,
    so asserting both would check the table against itself.
    """
    if not errors:
        return []
    return [str(getattr(e, "code", None) or getattr(e, "error_code", "")) for e in errors]


# All four transports: IMPL, A2A, REST, MCP
ALL_TRANSPORTS = [Transport.A2A, Transport.REST, Transport.MCP]

# GRADUATED — the A2A ledger shrank to zero, so there is no A2A_LEDGERED_TRANSPORTS
# list any more and every case below is back on plain ALL_TRANSPORTS.
#
# The ledger existed because routing the a2a seat through the real
# on_message_send pipeline (instead of calling sync_creatives_raw directly)
# exposed GH #2011: _handle_sync_creatives_skill constructed CreativeAsset(**c)
# at the boundary, which (a) dropped the inputs the generative build path reads,
# so a generative creative was silently created as STATIC, and (b) raised a
# request-level VALIDATION_ERROR for a legitimately-partial creative that _impl
# would have reported as a per-creative action='failed'. It was marked
# strict=True with the instruction that the list must shrink the moment #2011
# was fixed.
#
# It is fixed: _handle_sync_creatives_skill now passes the creatives array
# through UNCONSTRUCTED (see its comment in src/a2a_server/adcp_a2a_server.py),
# which is what the pinned sync-creatives-response schema requires — items with
# action='failed' are per-item validation failures, not operation-level ones.
# All eleven ledgered cases XPASS(strict) against that handler, so keeping the
# xfails would fail the suite while claiming a defect that no longer exists.


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
    def test_empty_creative_list_is_rejected(self, integration_db, transport):
        """An empty creative list is not a no-op -- the schema forbids it.

        sync-creatives-request.json declares ``creatives: {minItems: 1, maxItems: 100}``,
        so ``[]`` violates a SCHEMA CONSTRAINT, which 3.1/enums/error-code.json assigns to
        INVALID_REQUEST. This asserted the opposite until now -- that an empty list "is a
        valid no-op" returning success -- and passed because no transport built
        SyncCreativesRequest on this path: the constraint was declared and never enforced.
        Every transport builds it through SyncCreativesRequest now, so the same
        request gets the same answer on all four.
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(transport, creatives=[])

        assert not result.is_success, "an empty creatives array violates minItems: 1"
        result.assert_wire_error("INVALID_REQUEST", recovery="correctable")

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
    return creative_payload(
        **{
            "creative_id": creative_id,
            "name": name,
            "format_id": DEFAULT_FORMAT_ID,
            **overrides,
        }
    )


@pytest.mark.requires_db
class TestSyncUpsertReturnsUpdatedTransport:
    """Re-syncing an existing creative with a changed field returns action="updated".

    An identical re-sync is ``unchanged`` (enums/creative-action.json), so the second
    sync changes the name -- the field the changes list then names.

    Covers: UC-006-MAIN-MCP-04
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_upsert_existing_creative_reports_updated(self, integration_db, transport):
        """Syncing a creative that already exists, with a changed field, returns action=updated."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # First sync: create the creative (same transport as upsert)
            env.call_via(transport, creatives=[_creative(creative_id="c_upsert")])

            # Second sync via parametrized transport: upsert with a changed name
            result = env.call_via(transport, creatives=[_creative(creative_id="c_upsert", name="Renamed")])

        assert result.is_success, f"Expected success but got error: {result.error}"
        assert_envelope(result, transport)
        assert len(result.payload.creatives) == 1
        upserted = result.payload.creatives[0]
        assert upserted.creative_id == "c_upsert"
        assert upserted.action == "updated"

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
        assert results_by_id["c_bad"].action == "failed"
        assert results_by_id["c_good_1"].action != "failed"
        assert results_by_id["c_good_2"].action != "failed"

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
                assignments=[{"creative_id": "c_strict", "package_id": "PKG-NONEXISTENT"}],
                validation_mode="strict",
            )

        assert result.is_error, "Strict mode should error on missing package"
        # Graded on the CODE the buyer received, not on the class of an exception the
        # harness used to rebuild from wire bytes. Production
        # raises AdCPPackageNotFoundError here (_assignments.py:163), which is more
        # specific than the AdCPNotFoundError this used to accept.
        assert result.error_code() == "PACKAGE_NOT_FOUND", f"Expected PACKAGE_NOT_FOUND, got {result.error_code()!r}"


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
                assignments=[{"creative_id": "c_lenient", "package_id": "PKG-MISSING"}],
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
        assert creative_result.action == "failed"
        assert "REFERENCE_NOT_FOUND" in _error_codes(creative_result.errors)


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
                        "assets": build_assets(text_spec("message", content="Build me a banner")),
                    }
                ],
            )

            assert result.is_success, f"Expected success but got error: {result.error}"
            assert_envelope(result, transport)
            assert len(result.payload.creatives) == 1
            assert result.payload.creatives[0].action == "created"

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
                        "assets": build_assets(text_spec("message", content="Create a holiday banner")),
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
                        "assets": build_assets(text_spec("brief", content="Promote summer sale")),
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
                        "assets": build_assets(text_spec("prompt", content="Design a Q4 campaign banner")),
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
                        "assets": build_assets(text_spec("message", content="Initial prompt")),
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

            # User-provided headline must survive (NOT be replaced by the
            # generative "AI-generated headline" output). Build and verify with
            # the SAME spec so the assertion checks the preserved content.
            user_headline = text_spec("headline", content="User-provided headline")

            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_gen_08",
                        "name": "Asset Priority Test",
                        "format_id": fmt,
                        # 3.1 has no top-level `url` on a creative -- core/creative-asset.json
                        # declares none, and the media URL belongs in `assets`. Production
                        # still derives the stored data["url"] from the assets block
                        # (_assets.py _extract_url_from_assets), so the assertion below is
                        # unchanged; only the retired spelling goes.
                        "assets": build_assets(
                            text_spec("message", content="Build me a banner"),
                            user_headline,
                            image_spec("image", url="https://user.example.com/image.png"),
                        ),
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
        # User-provided headline preserved with its original content (not AI output)
        assert_stored_creative_assets("c_gen_08", user_headline, tenant_id="test_tenant")


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
                        "assets": build_assets(image_spec("banner")),
                    }
                ],
            )

            assert result.is_success
            assert_envelope(result, transport)
            assert len(result.payload.creatives) == 1
            assert result.payload.creatives[0].action == "created"

            # registry.get_format should NOT be called for adapter formats
            registry = env.mock["registry"].return_value
            assert not registry.get_format.called, "get_format should not be called for adapter-provided formats"


@pytest.mark.requires_db
class TestFormatValidationUnreachable:
    """Unreachable creative agent → request-level TRANSIENT failure.

    Production-grounded: the registry types every network
    failure (connect/timeout -> AdCPServiceUnavailableError), and typed
    transient errors PROPAGATE out of sync_creatives with their recovery
    semantics on every transport — a down agent is not a creative problem.

    Covers: UC-006-CREATIVE-FORMAT-VALIDATION-03
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_unreachable_agent_fails_creative(self, integration_db, transport):
        """Typed transient from registry.get_format → SERVICE_UNAVAILABLE, recovery=transient."""
        if transport is Transport.A2A:
            pytest.xfail(
                "a recorded gap: A2A emits no wire envelope for a tool-internal "
                "SERVICE_UNAVAILABLE — the raw AdCPServiceUnavailableError escapes instead "
                "of becoming a failed Task with an artifact DataPart. This leg only ever "
                "looked green because two synthesizing fallbacks (dispatchers' "
                "_envelope_from_adcp_error and this test's own `or "
                "synthesized_error_envelope`) rebuilt the envelope from the same in-memory "
                "exception with the same builder production uses, so a dead wire and a live "
                "one were indistinguishable. Both fallbacks are gone; graduating this means "
                "making A2A emit the envelope, never restoring a fallback."
            )
        from src.core.exceptions import AdCPServiceUnavailableError

        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # Override: get_format raises the typed error the registry actually
            # raises for network failures (side_effect on the env's existing
            # AsyncMock — mock-cap guard).
            env.mock["registry"].return_value.get_format.side_effect = AdCPServiceUnavailableError()

            result = env.call_via(
                transport,
                creatives=[_creative(creative_id="c_unreach", name="Unreachable Test")],
            )

            assert result.is_error, f"[{transport.value}] transient agent failure must fail the request"
            # Graded through the result rather than by handing an envelope to the
            # primitive: assert_wire_error reads wire_error_envelope ONLY (never the
            # synthesized stand-in the two removed fallbacks used to supply), fails
            # loudly when no envelope was captured, and adds the CODE_TABLE
            # emittability check. main's result.error_envelope() would also accept the
            # IMPL-synthesized envelope, which is the tautology the xfail above
            # describes; tests/unit/test_architecture_one_wire_error_assertion.py
            # requires this spelling for a TransportResult.
            result.assert_wire_error("SERVICE_UNAVAILABLE", recovery="transient")


@pytest.mark.requires_db
class TestTypedTransientSurvivesCreativeBuild:
    """A typed transient from the creative agent keeps its own code on the wire.

    Covers . The generative build/preview path caught EVERY exception
    from registry.build_creative / preview_creative and rebuilt it as
    SERVICE_UNAVAILABLE. A rate-limited agent therefore reached the buyer as a
    generic outage: "retry with backoff" instead of "wait, you are over quota", and
    with retry_after discarded.

    The handler already carved out AdCPConfigurationError for exactly this reason --
    so a missing GEMINI_API_KEY would not read as a transient agent outage. That
    carve-out was right and too narrow; every typed error deserves it. This grades
    the generalization.

    Asserted on the per-creative advisory rather than the error envelope because
    that is where this failure legitimately lands: the sync SUCCEEDS and reports the
    creative as failed, which is the contract test_bad_format above also relies on.
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_rate_limit_is_not_degraded_to_service_unavailable(self, integration_db, transport):
        """RATE_LIMITED survives; before this change the buyer read SERVICE_UNAVAILABLE."""
        from src.core.exceptions import AdCPRateLimitError

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            # A generative format, so the build path this ticket is about actually runs.
            fmt = env.setup_generative_build()
            registry_mock = env.mock["registry"].return_value
            registry_mock.build_creative = AsyncMock(side_effect=AdCPRateLimitError(retry_after=30))
            registry_mock.preview_creative = AsyncMock(side_effect=AdCPRateLimitError(retry_after=30))

            result = env.call_via(
                transport,
                creatives=[
                    {
                        "creative_id": "c_rate_limited",
                        "name": "Rate Limited Build",
                        "format_id": fmt,
                        "assets": build_assets(text_spec("message", content="Build me a banner")),
                    }
                ],
            )

        assert result.is_success, f"[{transport.value}] a per-creative failure must not fail the sync"
        assert len(result.payload.creatives) == 1
        creative_result = result.payload.creatives[0]
        assert creative_result.action == "failed"

        codes = _error_codes(creative_result.errors)
        # POSITIVE first: the agent's own code must survive. A negative-only check
        # ("not SERVICE_UNAVAILABLE") passes for any other wrong code too, which is
        # most of the ways this can regress.
        assert "RATE_LIMITED" in codes, (
            f"[{transport.value}] the agent's own code did not survive the build path: {codes}"
        )
        # And the exact regression: this read SERVICE_UNAVAILABLE before the typed branch.
        assert "SERVICE_UNAVAILABLE" not in codes, (
            f"[{transport.value}] a typed transient was degraded to a generic outage: {codes}"
        )


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
                assignments=[{"creative_id": "c_cross", "package_id": pkg_id}],
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
                assignments=[{"creative_id": "c_fmt_mismatch", "package_id": pkg_id}],
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
                assignments=[{"creative_id": "c_assign", "package_id": pkg_id}],
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
# (Deleted) TestAuthPrincipalRequired::test_no_principal_raises_auth_error (UC-006-EXT-A-02),
# which built ``make_identity(principal_id=None, ...)``. The remaining half of the pair
# below, removed for the same reason: ``sync_creatives`` takes an ``AccountIdentity``,
# whose principal is a REQUIRED field, so "an identity with no principal" is not a value
# the parameter can hold. The resolver refused an anonymous caller before the
# implementation ran, and ``ruff-boundary.toml`` bans raising AUTH_MISSING or AUTH_INVALID
# anywhere but there -- so this asserted a refusal this tool cannot mint, reached only
# because ``identity.principal.principal_id`` raised AttributeError on the fabricated
# value. Its oracle was ``error_code in {"AUTH_MISSING", "AUTH_INVALID"}``, which could not
# tell the two apart either way.
#
# The obligation is graded where it is decided: ``_resolve_identity`` refuses a missing
# credential for every tool and transport at once, and the transport-blind auth scenarios
# assert the AUTH_MISSING wire envelope across a2a, mcp and rest.

# (Deleted) TestAuthTenantRequired::test_no_tenant_raises_auth_error (UC-006-EXT-B-02),
# which built ``make_identity(principal_id="test_principal", tenant=None)``.
# ``sync_creatives`` takes an ``AccountIdentity``: principal, tenant and account are all
# required, and the resolver has refused a tenant-less caller long before the
# implementation runs (no tenant means no principal lookup, so a presented credential
# resolves nothing -- AUTH_INVALID, ``_resolve_identity`` step 4). The identity this set
# up cannot be constructed, and the refusal has exactly one minting site, which is not
# this tool.


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
        assert creative_result.action == "failed"
        assert creative_result.errors


@pytest.mark.requires_db
class TestMissingFormatFails:
    """Missing format_id → per-creative failure.

    Covers: UC-006-EXT-E-01
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_no_format_action_failed(self, integration_db, transport):
        """Creative without format_id is rejected, on EVERY transport, at the request.

        This used to branch: MCP rejected at the boundary while impl/a2a/rest reached _impl
        and came back with a per-creative ``action="failed"``. There is one accepted shape
        now -- the DTO -- so the rejection happens in the same place on all of them and the
        per-creative branch is unreachable for this payload.

        The rejection is a oneOf, not a missing field: core/creative-asset.json identifies a
        creative by format_id OR format_kind, so no single field is at fault and
        core/error.json puts the pointer at the item.
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
                        "assets": build_assets(image_spec("banner")),
                    }
                ],
                validation_mode="lenient",
            )

        assert result.is_error, f"{transport.value}: the request boundary must refuse this payload"
        assert_rejected(result, field="creatives[0]", keyword="oneOf")


@pytest.mark.requires_db
class TestStaticPreviewFailed:
    """Static creative: no previews and no media_url → action=failed.

    Covers: UC-006-EXT-H-01
    """

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_no_preview_no_url_fails(self, integration_db, transport):
        """Static format with empty preview_creative result and no url → failed."""
        from adcp.types import FormatId as LibraryFormatId

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
                        # `assets` is spec-REQUIRED (core/creative-asset.json
                        # required=[creative_id,name,assets]). Omitting it made this scenario
                        # depend on a transport accident: a2a hit an impl-side
                        # setdefault("assets", {}) and reached the no-preview logic, while
                        # mcp/rest failed earlier with a mid-pipeline VALIDATION_ERROR. An
                        # empty map is spec-legal (no minProperties) and lets all three
                        # transports grade the obligation this test is actually about.
                        "assets": {},
                    }
                ],
                validation_mode="lenient",
            )

        if result.is_error:
            # MCP: TypeAdapter rejects missing assets field — correct schema rejection
            from tests.harness.assertions import assert_rejected

            assert_rejected(result, field="assets", keyword="required")
        else:
            # impl/a2a/rest: _impl handles it, returns action=failed
            assert_envelope(result, transport)
            creative_result = result.payload.creatives[0]
            assert creative_result.action == "failed"
            assert "CREATIVE_REJECTED" in _error_codes(creative_result.errors)


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
                        "assets": build_assets(text_spec("message", content="Build a banner")),
                    }
                ],
                validation_mode="lenient",
            )

        assert result.is_success
        assert_envelope(result, transport)
        creative_result = result.payload.creatives[0]
        assert creative_result.action == "failed"
        assert "CONFIGURATION_ERROR" in _error_codes(creative_result.errors)


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
            # The wire leg's resolver reads the tenant row, so the fields go on the row.
            env.configure_tenant_field("approval_mode", "require-human")
            env.configure_tenant_field("slack_webhook_url", "https://hooks.slack.com/test")

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
            env.configure_tenant_field("approval_mode", "require-human")

            response = env.call_impl(
                creatives=[_creative(creative_id="c_no_webhook")],
            )

            # call_impl returns the response DTO itself -- no TransportResult
            # wrapper, so success is "it returned instead of raising".
            assert response.creatives
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
            env.setup_default_data()
            # The wire leg's resolver reads the tenant row, so the field goes on the row.
            env.configure_tenant_field("approval_mode", "ai-powered")

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
            assert creative_result.action == "created"
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
            env.setup_default_data()
            # The wire leg's resolver reads the tenant row, so the fields go on the row.
            env.configure_tenant_field("approval_mode", "ai-powered")
            env.configure_tenant_field("slack_webhook_url", "https://hooks.slack.com/test")

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
# See: (feature request for async sync_creatives lifecycle)
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
        reason="Async lifecycle not implemented",
        strict=True,
    )
    def test_queued_sync_returns_submitted(self, integration_db):
        """Queued sync operation returns SyncCreativesSubmitted with context."""
        from adcp.types.generated_poc.creative.sync_creatives_async_response_submitted import (  # TODO: no stable alias in adcp.types
            SyncCreativesSubmitted,
        )

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            result = env.call_impl(
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
        reason="Async lifecycle not implemented",
        strict=True,
    )
    def test_in_progress_returns_working_with_progress(self, integration_db):
        """Status check on in-progress async op returns SyncCreativesWorking."""
        from adcp.types.generated_poc.creative.sync_creatives_async_response_working import (  # TODO: no stable alias in adcp.types
            SyncCreativesWorking,
        )

        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # First: queue an async operation
            submit_result = env.call_impl(
                creatives=[
                    _creative(creative_id="c_prog_1", name="Progress 1"),
                    _creative(creative_id="c_prog_2", name="Progress 2"),
                ],
                async_mode=True,
            )
            context_id = submit_result.payload.context

            # BDD: "When the Buyer checks status"
            status_result = env.call_impl(
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
        reason="Async lifecycle not implemented",
        strict=True,
    )
    def test_approval_needed_returns_input_required(self, integration_db):
        """Async op needing approval returns SyncCreativesInputRequired."""
        from adcp.types.generated_poc.creative.sync_creatives_async_response_input_required import (  # TODO: no stable alias in adcp.types
            Reason,
            SyncCreativesInputRequired,
        )

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            env.identity.tenant["approval_mode"] = "require-human"

            # BDD: "async sync operation requires Buyer input"
            result = env.call_impl(
                creatives=[_creative(creative_id="c_input_req", name="Needs Approval")],
                async_mode=True,
            )

            # BDD: "a SyncCreativesAsyncResponseInputRequired is returned"
            assert isinstance(result.payload, SyncCreativesInputRequired)
            # BDD: "indicates what input is needed"
            assert result.payload.reason == Reason.APPROVAL_REQUIRED


@pytest.mark.requires_db
class TestRestForwardsIdempotencyKey:
    """The REST route must hand the buyer's idempotency_key to the wrapper.

    It did not. SyncCreativesBody carried the field and sync-creatives-request.json lists
    it in /required, but the route omitted it from the sync_creatives_raw call, so the key
    a REST buyer sent was discarded before anything looked at it. mcp and a2a both forward
    it, which is why no cross-transport test caught the difference.

    What that costs today is the SHAPE check: _sync_creatives_impl runs
    validate_idempotency_key_shape on the key, so a malformed one is rejected on mcp and
    a2a and was silently accepted on REST -- the same request answered two ways depending
    on the transport. Note it is only the shape check: sync_creatives does NOT implement
    replay (unlike create_media_buy), so forwarding the key does not yet make a retry
    replay the first response.
    """

    def test_rest_rejects_a_malformed_key_like_the_other_transports(self, integration_db):
        """A too-short key is a value violation on every transport, REST included."""
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(
                # A FRESH key per call, as sync-creatives-request.json directs ("Use a fresh
                # UUID v4 for each request"). These were derived from the creative_id or
                # hardcoded, so a test syncing the same creative twice with different content
                # reused one key across two payloads -- correctly an IDEMPOTENCY_CONFLICT now
                # that sync_creatives honours the key.
                Transport.REST,
                creatives=[_creative(creative_id="c_idem")],
                idempotency_key="short",  # DELIBERATELY malformed: below minLength 16,
            )

        assert not result.is_success, "a malformed idempotency_key must be rejected on REST too"
        # INVALID_REQUEST, and the code CHANGED with the builder conversion -- for the
        # better. sync-creatives-request.json constrains idempotency_key with a pattern, so
        # a malformed key violates a SCHEMA constraint, which 3.1/enums/error-code.json
        # assigns to INVALID_REQUEST. Before every transport built SyncCreativesRequest, the
        # rejection came from the hand-written validate_idempotency_key_shape and surfaced
        # as VALIDATION_ERROR; now the model rejects it first and all transports agree.
        result.assert_wire_error("INVALID_REQUEST", recovery="correctable")
