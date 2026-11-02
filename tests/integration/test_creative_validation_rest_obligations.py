"""Integration tests: creative validation + REST route obligations.

Behavioral tests using CreativeSyncEnv, CreativeListEnv, and CreativeFormatsEnv
with real PostgreSQL + factory_boy. Replaces allowlisted unit tests that only
exercised schema construction or route introspection.

Covers:
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.exceptions import first_validation_error_field
from tests.factories.creative_asset import build_assets, image_spec
from tests.harness import (
    CreativeFormatsEnv,
    CreativeListEnv,
    CreativeSyncEnv,
    Transport,
    assert_envelope,
)
from tests.helpers.creative_test_helpers import sync_creatives_request

DEFAULT_AGENT_URL = "https://creative.test.example.com"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# : Missing format_id refused when the sync REQUEST is built
# Obligation: UC-006-EXT-E-01
# ---------------------------------------------------------------------------


class TestMissingFormatIdRejectedAtTheRequestBoundary:
    """Missing format_id is refused when the sync request is BUILT."""

    def test_missing_format_id_is_refused_naming_format_id(self, integration_db):
        """Covers: UC-006-EXT-E-01 — a creative that identifies no format is rejected.

        This asserted a per-creative ``action="failed"`` coming out of
        ``_sync_creatives_impl``. Every caller -- all three transports and both in-process
        media-buy uploads -- builds a SyncCreativesRequest before ``_impl`` runs, so the
        request boundary refuses the omission and the per-creative branch never runs. No
        transport could deliver the payload the old assertion described.

        The obligation reads "naming it", meaning format_id. That over-specifies the pin:
        core/creative-asset.json identifies a creative by format_id OR by format_kind, so
        omitting format_id breaks the oneOf rather than a required-field rule, and neither
        field is individually at fault. The buyer is owed the item and the keyword, which
        is what the assertion below grades.
        """
        # Graded on the pydantic rejection and the FIELD PATH production derives from it.
        # This used to open adcp_validation_boundary itself to reproduce what the transports
        # did; they no longer do, so the wrapper simulated a frame that is gone. The typed
        # error and its code are produced at the transport boundary and graded there
        # (tests/unit/test_validation_error_at_the_boundary.py).
        with pytest.raises(ValidationError) as exc_info:
            sync_creatives_request(
                creatives=[
                    {
                        "creative_id": "c_no_format",
                        "name": "Missing Format Creative",
                        # format_id intentionally omitted
                        "assets": build_assets(image_spec("banner")),
                    }
                ],
            )

        # WHICH field was rejected is graded on the structured `field` pointer, not on the
        # sentence — the sentence is a CODE_TABLE function of the code and cannot name it.
        # The CODE this becomes is INVALID_REQUEST and is graded once, on the wire, in
        # tests/unit/test_validation_error_at_the_boundary.py; asserting it off the pydantic
        # exception here is not possible (it carries no code) and would not grade the wire.
        #
        # THE ITEM, not "format_id". core/creative-asset.json identifies a creative by
        # format_id OR format_kind, so omitting format_id is a oneOf failure, and
        # core/error.json puts a oneOf's RFC 6901 pointer at the object -- no single field is
        # at fault when both branches are legal. Asserting "format_id" demands that the seller
        # name a field the buyer was never required to send. The obligation's "naming it" is
        # over-specified against the pin; what the buyer is owed, and gets, is the item plus
        # the keyword that says which rule it broke.
        assert first_validation_error_field(exc_info.value) == "creatives[0]"
        assert exc_info.value.errors()[0]["type"] == "oneOf"


# ---------------------------------------------------------------------------
# : creative_ids scope — empty list filters all
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
                        "assets": build_assets(image_spec("banner")),
                    },
                    {
                        "creative_id": "c_excluded",
                        "name": "Should Be Excluded",
                        "format_id": {"id": "display_300x250", "agent_url": DEFAULT_AGENT_URL},
                        "assets": build_assets(image_spec("banner")),
                    },
                ],
                creative_ids=["c_included"],  # Only process c_included
            )

        # Only the creative matching the filter should be processed
        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "c_included"


# ---------------------------------------------------------------------------
# : creative_formats REST route works
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
# : list_creatives REST route works
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
# : sync_creatives REST route works
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
                        # `assets` is the AdCP 3.1.1 spelling; `media_url` is not a Creative
                        # field in the pinned schema and never was one this route implemented.
                        # It reached _impl only because the hand-written REST body typed
                        # creatives as list[dict[str, Any]], so any key passed the boundary
                        # untouched. The body is derived from the DTO now, and MCP has always
                        # announced a typed Creative array, so both transports reject it in
                        # dev/CI (extra="forbid"); production would ignore it (extra="ignore").
                        "assets": build_assets(image_spec("image", url="https://example.com/image.png")),
                    }
                ],
            )

        assert result.is_success, f"Expected success but got error: {result.error}"
        assert_envelope(result, Transport.REST)
        assert len(result.payload.creatives) == 1
        creative = result.payload.creatives[0]
        assert creative.creative_id == "c_rest_sync_test"
