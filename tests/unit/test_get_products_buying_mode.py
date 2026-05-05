"""Cross-mode validation and field-name normalization on GetProductsRequest.

Covers the AdCP 3.0 three-mode contract (brief / wholesale / refine) and the rc.3 -> 3.0.6
wire-rename compatibility for refine entry id fields. See
.claude/notes/buying-mode-refine-wireup/PLAN.md Layer 1 for context.

Behavioral obligations:
    Covers: BR-UC-001-MAIN-BRIEF-MODE-01
    Covers: BR-UC-001-ALT-WHOLESALE-MODE-01
    Covers: BR-UC-001-ALT-REFINE-MODE-01
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.schemas import GetProductsRequest

# ---------------------------------------------------------------------------
# Cross-mode invariants (Layer 1.2)
#
# Mirrors the 7 rule rows in
# tests/bdd/features/BR-UC-001-discover-available-inventory.feature:313-319.
# ---------------------------------------------------------------------------


class TestCrossModeHappyPaths:
    """The three valid mode combinations."""

    def test_brief_mode_with_brief_only_is_valid(self):
        req = GetProductsRequest(buying_mode="brief", brief="video ads for sports fans")

        assert req.buying_mode == "brief"
        assert req.brief == "video ads for sports fans"
        assert req.refine is None

    def test_wholesale_mode_minimal_is_valid(self):
        req = GetProductsRequest(buying_mode="wholesale")

        assert req.buying_mode == "wholesale"
        assert req.brief is None
        assert req.refine is None

    def test_refine_mode_with_refine_array_is_valid(self):
        req = GetProductsRequest(
            buying_mode="refine",
            refine=[{"scope": "request", "ask": "more video, less display"}],
        )

        assert req.buying_mode == "refine"
        assert req.brief is None
        assert req.refine is not None
        assert len(req.refine) == 1


class TestCrossModeViolations:
    """The seven rules from BR-UC-001-discover-available-inventory.feature:313-319."""

    def test_missing_buying_mode_v3_rejected(self):
        # buying_mode required when no pre-v3 default-to-brief has been applied
        with pytest.raises(ValidationError, match="buying_mode is required"):
            GetProductsRequest(brief="video ads")

    def test_invalid_buying_mode_value_rejected(self):
        with pytest.raises(ValidationError, match="buying_mode must be one of"):
            GetProductsRequest(buying_mode="bogus", brief="video ads")

    def test_brief_mode_without_brief_rejected(self):
        with pytest.raises(ValidationError, match="brief is required when buying_mode is 'brief'"):
            GetProductsRequest(buying_mode="brief")

    def test_brief_mode_with_refine_rejected(self):
        with pytest.raises(
            ValidationError,
            match="refine must not be provided when buying_mode is 'brief'",
        ):
            GetProductsRequest(
                buying_mode="brief",
                brief="video ads",
                refine=[{"scope": "request", "ask": "more video"}],
            )

    def test_wholesale_mode_with_brief_rejected(self):
        with pytest.raises(
            ValidationError,
            match="brief must not be provided when buying_mode is 'wholesale'",
        ):
            GetProductsRequest(buying_mode="wholesale", brief="video ads")

    def test_wholesale_mode_with_refine_rejected(self):
        with pytest.raises(
            ValidationError,
            match="refine must not be provided when buying_mode is 'wholesale'",
        ):
            GetProductsRequest(
                buying_mode="wholesale",
                refine=[{"scope": "request", "ask": "more video"}],
            )

    def test_refine_mode_with_brief_rejected(self):
        with pytest.raises(
            ValidationError,
            match="brief must not be provided when buying_mode is 'refine'",
        ):
            GetProductsRequest(
                buying_mode="refine",
                brief="video ads",
                refine=[{"scope": "request", "ask": "more video"}],
            )

    def test_refine_mode_without_refine_array_rejected(self):
        with pytest.raises(ValidationError, match="refine array is required"):
            GetProductsRequest(buying_mode="refine")


# ---------------------------------------------------------------------------
# Refine entry field-name normalization (Layer 1.1)
#
# Bridges the rc.3 (id) <-> 3.0.6 (product_id, proposal_id) skew so storyboard
# requests parse against our installed library types.
# ---------------------------------------------------------------------------


class TestRefineEntryFieldNameNormalizer:
    """Storyboard 3.0.6 wire format -> rc.3 library field name."""

    def test_request_scope_passthrough(self):
        req = GetProductsRequest(
            buying_mode="refine",
            refine=[{"scope": "request", "ask": "narrow to guaranteed only"}],
        )
        # Request scope has no id field on either side
        entry = req.refine[0]
        assert entry.scope == "request"
        assert entry.ask == "narrow to guaranteed only"

    def test_product_scope_product_id_renamed_to_id(self):
        req = GetProductsRequest(
            buying_mode="refine",
            refine=[{"scope": "product", "product_id": "sports_preroll_q2", "action": "include"}],
        )
        assert req.refine[0].id == "sports_preroll_q2"

    def test_proposal_scope_proposal_id_renamed_to_id(self):
        req = GetProductsRequest(
            buying_mode="refine",
            refine=[{"scope": "proposal", "proposal_id": "prop_abc", "action": "include"}],
        )
        assert req.refine[0].id == "prop_abc"

    def test_both_id_forms_present_and_equal_accepted(self):
        # Equivalent values: drop the wire-name form, keep id
        req = GetProductsRequest(
            buying_mode="refine",
            refine=[
                {
                    "scope": "product",
                    "product_id": "p1",
                    "id": "p1",
                    "action": "include",
                }
            ],
        )
        assert req.refine[0].id == "p1"

    def test_both_id_forms_present_and_different_rejected(self):
        # Mismatch: the wire is internally inconsistent — reject deterministically
        with pytest.raises(
            ValidationError,
            match="refine entry has both 'id' .* and 'product_id' .* with different values",
        ):
            GetProductsRequest(
                buying_mode="refine",
                refine=[
                    {
                        "scope": "product",
                        "product_id": "p1",
                        "id": "p2",
                        "action": "include",
                    }
                ],
            )

    def test_proposal_scope_both_ids_different_rejected(self):
        with pytest.raises(
            ValidationError,
            match="refine entry has both 'id' .* and 'proposal_id' .* with different values",
        ):
            GetProductsRequest(
                buying_mode="refine",
                refine=[
                    {
                        "scope": "proposal",
                        "proposal_id": "pp1",
                        "id": "pp2",
                        "action": "include",
                    }
                ],
            )

    def test_id_only_no_renamed_form_passes_through(self):
        # If only `id` is supplied (rc.3 native form), pass through
        req = GetProductsRequest(
            buying_mode="refine",
            refine=[{"scope": "product", "id": "p1", "action": "include"}],
        )
        assert req.refine[0].id == "p1"


# ---------------------------------------------------------------------------
# Storyboard payload parses end-to-end
# ---------------------------------------------------------------------------


class TestStoryboardCompliance:
    """The exact refine_products storyboard payload parses without rejection."""

    def test_storyboard_refine_request_parses(self):
        """Mirrors the request body in
        @adcp/sdk@6.11.0 compliance/cache/3.0.6/protocols/media-buy/scenarios/refine_products.yaml.
        """
        req = GetProductsRequest(
            buying_mode="refine",
            account={
                "brand": {"domain": "acmeoutdoor.example"},
                "operator": "pinnacle-agency.example",
            },
            refine=[
                {
                    "scope": "request",
                    "ask": "Only guaranteed packages. Must include completion rate SLA above 80%.",
                },
                {
                    "scope": "product",
                    "product_id": "sports_preroll_q2",
                    "ask": "Increase budget allocation to $30K",
                },
            ],
        )

        assert req.buying_mode == "refine"
        assert len(req.refine) == 2
        assert req.refine[0].scope == "request"
        # Renamed: product_id -> id
        assert req.refine[1].id == "sports_preroll_q2"
