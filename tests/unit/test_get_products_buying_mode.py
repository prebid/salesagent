"""Cross-mode validation on GetProductsRequest.

Covers the AdCP 3.0 three-mode contract (brief / wholesale / refine). Validation is split
by layer: the version-agnostic model (``_validate_buying_mode_invariants``) enforces the
cross-mode invariants once a mode is declared; the version-aware wrapper
(``create_get_products_request``) owns buying_mode required-ness ("v3 clients MUST include
buying_mode; pre-v3 clients without it SHOULD default to 'brief'"). The seven cross-mode
rules mirror tests/bdd/features/BR-UC-001-discover-available-inventory.feature:313-319.

Covers: UC-001-MODE-VALIDATION-01
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.schemas import GetProductsRequest
from src.core.validation_helpers import extract_buying_mode_suggestion

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

    def test_missing_buying_mode_accepted_by_model(self):
        # The model is version-agnostic: a missing buying_mode is accepted (stays None)
        # so direct/forward-compat construction works. Whether None is allowed is
        # version-keyed and decided at the wrapper — see
        # TestWrapperOwnsBuyingModeRequiredness below.
        req = GetProductsRequest(brief="video ads")

        assert req.buying_mode is None
        assert req.brief == "video ads"


class TestCrossModeViolations:
    """The seven rules from BR-UC-001-discover-available-inventory.feature:313-319.

    These are the version-agnostic invariants the model enforces once a mode is declared.
    Missing-mode required-ness is the wrapper's job — see
    TestWrapperOwnsBuyingModeRequiredness.
    """

    def test_invalid_buying_mode_value_rejected(self):
        # Our validator catches this: buying_mode is widened to str|None, so the library's
        # enum check is bypassed and the custom validator rejects non-mode values.
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


class TestWrapperOwnsBuyingModeRequiredness:
    """buying_mode required-ness is version-keyed, so the version-aware wrapper owns it.

    Spec 3.0.1: "v3 clients MUST include buying_mode; pre-v3 clients without it SHOULD
    default to 'brief'." The version-agnostic model cannot make this call (no adcp_version),
    so ``create_get_products_request`` does: reject v3 omissions, default pre-v3 clients.
    """

    def test_v3_missing_buying_mode_rejected_with_suggestion(self):
        from src.core.exceptions import AdCPInvalidRequestError
        from src.core.schema_helpers import create_get_products_request
        from src.core.validation_helpers import _BUYING_MODE_SUGGESTIONS

        with pytest.raises(AdCPInvalidRequestError, match="buying_mode is required") as exc_info:
            create_get_products_request(brief="video ads", adcp_version="3.0.6")
        assert exc_info.value.error_code == "INVALID_REQUEST"
        assert exc_info.value.suggestion == _BUYING_MODE_SUGGESTIONS[0][1]

    def test_pre_v3_missing_buying_mode_defaults_to_brief(self):
        from src.core.schema_helpers import create_get_products_request

        build = create_get_products_request(brief="video ads", adcp_version="2.5.0")
        assert build.request.buying_mode == "brief"
        assert build.pre_v3_defaulted is True


# Each validator violation (cross-mode rule or invalid mode value) → the actionable buyer
# suggestion extract_buying_mode_suggestion returns. Pins the buyer-facing wire contract. The
# missing-mode suggestion is exercised at the wrapper layer (TestWrapperOwnsBuyingModeRequiredness),
# not here, since the version-agnostic model accepts a None mode.
_SUGGESTION_CASES = [
    (
        {"buying_mode": "brief"},
        "Provide a brief describing your campaign requirements, or use buying_mode='wholesale' for raw inventory.",
    ),
    (
        {"buying_mode": "brief", "brief": "video ads", "refine": [{"scope": "request", "ask": "more video"}]},
        "Remove refine, or use buying_mode='refine' to iterate on a previous response.",
    ),
    (
        {"buying_mode": "wholesale", "brief": "video ads"},
        "Remove brief, or use buying_mode='brief' to discover via a brief.",
    ),
    (
        {"buying_mode": "wholesale", "refine": [{"scope": "request", "ask": "more video"}]},
        "Remove refine, or use buying_mode='refine' to iterate on a previous response.",
    ),
    (
        {"buying_mode": "refine", "brief": "video ads", "refine": [{"scope": "request", "ask": "more video"}]},
        "Remove brief, or use buying_mode='brief' to discover via a brief.",
    ),
    (
        {"buying_mode": "refine"},
        "Provide a refine array with at least one entry, or use a different buying_mode.",
    ),
    # Invalid mode value (not a cross-mode rule) — pins the 7th validator message so a reword
    # of "buying_mode must be one of" can't silently drop the wire suggestion.
    (
        {"buying_mode": "bogus_mode"},
        "Use buying_mode='brief', 'wholesale', or 'refine'.",
    ),
]


class TestBuyingModeSuggestions:
    """Each cross-mode violation maps to its actionable buyer suggestion (pins the wire contract)."""

    @pytest.mark.parametrize(("kwargs", "expected_suggestion"), _SUGGESTION_CASES)
    def test_violation_maps_to_suggestion(self, kwargs, expected_suggestion):
        with pytest.raises(ValidationError) as exc_info:
            GetProductsRequest(**kwargs)
        assert extract_buying_mode_suggestion(exc_info.value) == expected_suggestion


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

        assert req.buying_mode == "refine"
        assert len(req.refine) == 2
        assert req.refine[0].root.scope == "request"
        # Library 4.3 keeps the spec wire field name: product_id (no rename needed)
        assert req.refine[1].root.product_id == "sports_preroll_q2"
