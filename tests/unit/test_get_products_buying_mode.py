"""Cross-mode validation on GetProductsRequest.

Covers the AdCP 3.0 three-mode contract (brief / wholesale / refine). The library handles
basic schema validation (buying_mode required, enum values, refine entry shape); our
validator adds the cross-mode invariants the library does not enforce. See
.claude/notes/buying-mode-refine-wireup/PLAN.md Layer 1 for context.

Covers: UC-001-MODE-VALIDATION-01
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

        assert req.buying_mode.value == "brief"
        assert req.brief == "video ads for sports fans"
        assert req.refine is None

    def test_wholesale_mode_minimal_is_valid(self):
        req = GetProductsRequest(buying_mode="wholesale")

        assert req.buying_mode.value == "wholesale"
        assert req.brief is None
        assert req.refine is None

    def test_refine_mode_with_refine_array_is_valid(self):
        req = GetProductsRequest(
            buying_mode="refine",
            refine=[{"scope": "request", "ask": "more video, less display"}],
        )

        assert req.buying_mode.value == "refine"
        assert req.brief is None
        assert req.refine is not None
        assert len(req.refine) == 1


class TestCrossModeViolations:
    """The seven rules from BR-UC-001-discover-available-inventory.feature:313-319."""

    def test_missing_buying_mode_v3_rejected(self):
        # The library's required-field validator catches this — message is the standard
        # pydantic "Field required". The pre-v3 default shim runs at the wrapper, not here.
        with pytest.raises(ValidationError, match="buying_mode"):
            GetProductsRequest(brief="video ads")

    def test_invalid_buying_mode_value_rejected(self):
        # The library's enum validator catches this — message is "Input should be 'brief',
        # 'wholesale' or 'refine'".
        with pytest.raises(ValidationError, match="brief.*wholesale.*refine"):
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
# Refine entry wire shape — adcp library 4.3+ accepts product_id / proposal_id natively.
# These tests pin our integration against that shape so a future library regression that
# renames the field surfaces here.
# ---------------------------------------------------------------------------


class TestRefineEntryParsing:
    """Refine entries parse via the library's discriminated-union (Refine1/2/3)."""

    def test_request_scope_parses(self):
        req = GetProductsRequest(
            buying_mode="refine",
            refine=[{"scope": "request", "ask": "narrow to guaranteed only"}],
        )
        inner = req.refine[0].root
        assert inner.scope == "request"
        assert inner.ask == "narrow to guaranteed only"

    def test_product_scope_with_product_id_parses(self):
        req = GetProductsRequest(
            buying_mode="refine",
            refine=[{"scope": "product", "product_id": "sports_preroll_q2"}],
        )
        inner = req.refine[0].root
        assert inner.scope == "product"
        assert inner.product_id == "sports_preroll_q2"

    def test_proposal_scope_with_proposal_id_parses(self):
        req = GetProductsRequest(
            buying_mode="refine",
            refine=[{"scope": "proposal", "proposal_id": "prop_abc"}],
        )
        inner = req.refine[0].root
        assert inner.scope == "proposal"
        assert inner.proposal_id == "prop_abc"

    def test_product_scope_action_defaults_to_include(self):
        # Spec 3.0.6 default; the library carries it.
        req = GetProductsRequest(
            buying_mode="refine",
            refine=[{"scope": "product", "product_id": "p1"}],
        )
        assert req.refine[0].root.action.value == "include"

    def test_product_scope_without_product_id_rejected(self):
        # Library catches missing required field on the discriminated variant.
        with pytest.raises(ValidationError, match="product_id"):
            GetProductsRequest(
                buying_mode="refine",
                refine=[{"scope": "product"}],
            )


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

        assert req.buying_mode.value == "refine"
        assert len(req.refine) == 2
        assert req.refine[0].root.scope == "request"
        # Library 4.3 keeps the spec wire field name: product_id (no rename needed)
        assert req.refine[1].root.product_id == "sports_preroll_q2"
