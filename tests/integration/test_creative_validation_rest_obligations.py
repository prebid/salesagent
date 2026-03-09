"""Integration tests: creative validation + REST route obligations.

Behavioral tests using CreativeSyncEnv, CreativeListEnv, and CreativeFormatsEnv
with real PostgreSQL + factory_boy. Replaces allowlisted unit tests that only
exercised schema construction or route introspection.

Covers: salesagent-e2u, salesagent-axm, salesagent-dd6, salesagent-46w, salesagent-bpq
"""

from __future__ import annotations

import pytest
from adcp.types import CreativeAction

from tests.harness import (
    CreativeFormatsEnv,
    CreativeListEnv,
    CreativeSyncEnv,
    Transport,
    assert_envelope,
)

DEFAULT_AGENT_URL = "https://creative.test.example.com"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# salesagent-e2u: Missing format_id rejected through sync impl
# Obligation: UC-006-EXT-E-01
# ---------------------------------------------------------------------------


class TestMissingFormatIdRejectedThroughImpl:
    """Missing format_id is caught by _sync_creatives_impl as a failed creative."""

    def test_missing_format_id_produces_failed_result(self, integration_db):
        """Covers: UC-006-EXT-E-01 — creative without format_id fails through impl.

        Unlike the unit test which just checks Pydantic schema construction,
        this exercises the full _sync_creatives_impl code path: dict normalization,
        CreativeAsset parsing, validation, and result assembly.
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            # Pass a creative dict missing format_id entirely
            response = env.call_impl(
                creatives=[
                    {
                        "creative_id": "c_no_format",
                        "name": "Missing Format Creative",
                        # format_id intentionally omitted
                        "assets": {"banner": {"url": "https://example.com/banner.png"}},
                    }
                ],
            )

        # The impl catches the ValidationError from CreativeAsset parsing
        # and produces a failed result instead of raising
        assert len(response.creatives) == 1
        result = response.creatives[0]
        assert result.creative_id == "c_no_format"
        assert result.action == CreativeAction.failed
        assert result.errors is not None
        assert len(result.errors) > 0
        assert any("format_id" in err.lower() for err in result.errors), (
            f"Expected error about format_id, got: {result.errors}"
        )


# ---------------------------------------------------------------------------
# salesagent-axm: creative_ids scope — empty list filters all
# Obligation: UC-006-CREATIVE-IDS-SCOPE-01
# ---------------------------------------------------------------------------


class TestCreativeIdsScopeFiltering:
    """creative_ids filter scopes which creatives are processed."""

    def test_creative_ids_filter_scopes_to_matching(self, integration_db):
        """Covers: UC-006-CREATIVE-IDS-SCOPE-01 — creative_ids limits processing scope.

        When creative_ids is provided with specific IDs, only creatives whose
        IDs appear in both the payload AND the filter are processed. Creatives
        not in the filter are silently skipped.
        Unlike the unit test which only constructs a SyncCreativesRequest schema,
        this exercises the actual _sync_creatives_impl filtering logic with real DB.
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            response = env.call_impl(
                creatives=[
                    {
                        "creative_id": "c_included",
                        "name": "Should Be Included",
                        "format_id": {"id": "display_300x250", "agent_url": DEFAULT_AGENT_URL},
                        "assets": {"banner": {"url": "https://example.com/banner.png"}},
                    },
                    {
                        "creative_id": "c_excluded",
                        "name": "Should Be Excluded",
                        "format_id": {"id": "display_300x250", "agent_url": DEFAULT_AGENT_URL},
                        "assets": {"banner": {"url": "https://example.com/other.png"}},
                    },
                ],
                creative_ids=["c_included"],  # Only process c_included
            )

        # Only the creative matching the filter should be processed
        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "c_included"


# ---------------------------------------------------------------------------
# salesagent-dd6: creative_formats REST route works
# Obligation: UC-006-MAIN-REST-01
# ---------------------------------------------------------------------------


class TestCreativeFormatsRESTRoute:
    """creative_formats REST endpoint returns real response."""

    def test_creative_formats_rest_returns_response(self, integration_db):
        """Covers: UC-006-MAIN-REST-01 — POST /api/v1/creative-formats returns 200.

        Unlike the unit test which just checks route registration via
        introspection, this dispatches an actual HTTP request through
        FastAPI TestClient and verifies a real JSON response.
        """
        with CreativeFormatsEnv() as env:
            env.setup_default_data()

            result = env.call_via(Transport.REST)

        assert result.is_success, f"Expected success but got error: {result.error}"
        assert_envelope(result, Transport.REST)
        # Response should have a formats list (empty is fine — no agents configured)
        assert hasattr(result.payload, "formats")
        assert isinstance(result.payload.formats, list)


# ---------------------------------------------------------------------------
# salesagent-46w: list_creatives REST route works
# Obligation: UC-006-MAIN-REST-01
# ---------------------------------------------------------------------------


class TestListCreativesRESTRoute:
    """list_creatives REST endpoint returns real response."""

    def test_list_creatives_rest_returns_response(self, integration_db):
        """Covers: UC-006-MAIN-REST-01 — POST /api/v1/creatives returns 200.

        Unlike the unit test which just checks route registration via
        introspection, this dispatches an actual HTTP request through
        FastAPI TestClient and verifies a real JSON response with
        expected structure.
        """
        with CreativeListEnv() as env:
            env.setup_default_data()

            result = env.call_via(Transport.REST)

        assert result.is_success, f"Expected success but got error: {result.error}"
        assert_envelope(result, Transport.REST)
        assert hasattr(result.payload, "creatives")
        assert isinstance(result.payload.creatives, list)


# ---------------------------------------------------------------------------
# salesagent-bpq: sync_creatives REST route works
# Obligation: UC-006-MAIN-REST-01
# ---------------------------------------------------------------------------


class TestSyncCreativesRESTRoute:
    """sync_creatives REST endpoint returns real response."""

    def test_sync_creatives_rest_creates_creative(self, integration_db):
        """Covers: UC-006-MAIN-REST-01 — POST /api/v1/creatives/sync returns 200.

        Unlike the unit test which just checks route registration via
        introspection, this dispatches an actual HTTP request through
        FastAPI TestClient with a valid creative payload and verifies the
        creative is processed and returned in the response.
        """
        with CreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(
                Transport.REST,
                creatives=[
                    {
                        "creative_id": "c_rest_sync_test",
                        "name": "REST Sync Test Creative",
                        "format_id": {"id": "display_300x250", "agent_url": DEFAULT_AGENT_URL},
                        "media_url": "https://example.com/image.png",
                    }
                ],
            )

        assert result.is_success, f"Expected success but got error: {result.error}"
        assert_envelope(result, Transport.REST)
        assert len(result.payload.creatives) == 1
        creative = result.payload.creatives[0]
        assert creative.creative_id == "c_rest_sync_test"
