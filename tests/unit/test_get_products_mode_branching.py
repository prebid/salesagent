"""Unit tests for buying_mode behavior in _get_products_impl.

Covers Layer 6 of the buying_mode/refine wireup:
- Mode branching (brief / wholesale / refine)
- brief_relevance plumbing from ranker output (brief mode only)
- refinement_applied builder (refine mode)
- Audit log extension (buying_mode, refine_count, pre_v3_defaulted)
- Outbound 3.0.6 wire compat in GetProductsResponse.model_dump (Layer 7)

Covers: UC-001-MODE-BRIEF-01
Covers: UC-001-MODE-WHOLESALE-01
Covers: UC-001-MODE-REFINE-01
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from adcp.types.generated_poc.media_buy.get_products_request import Refine

from src.core.schemas import GetProductsResponse
from src.core.tools.products import _build_refinement_applied_unable
from tests.harness.product_unit import ProductEnv

# ---------------------------------------------------------------------------
# refinement_applied builder (Layer 6.4)
# ---------------------------------------------------------------------------


class TestBuildRefinementAppliedUnable:
    """Helper builds the refinement_applied list with status='unable' for each entry."""

    def test_empty_refine_returns_empty_list(self):
        assert _build_refinement_applied_unable(None) == []
        assert _build_refinement_applied_unable([]) == []

    def test_request_scope_entry_has_no_id_field(self):
        items = _build_refinement_applied_unable([_fake_entry("request", None)])

        assert len(items) == 1
        assert items[0].root.status.value == "unable"
        assert items[0].root.scope == "request"
        # Request-scope variant has no id field at all
        assert not hasattr(items[0].root, "product_id")
        assert not hasattr(items[0].root, "proposal_id")

    def test_product_scope_entry_id_echoed(self):
        items = _build_refinement_applied_unable([_fake_entry("product", "sports_preroll_q2")])

        assert items[0].root.scope == "product"
        assert items[0].root.product_id == "sports_preroll_q2"
        assert items[0].root.status.value == "unable"

    def test_proposal_scope_entry_id_echoed(self):
        items = _build_refinement_applied_unable([_fake_entry("proposal", "prop_abc")])

        assert items[0].root.scope == "proposal"
        assert items[0].root.proposal_id == "prop_abc"

    def test_positional_matching_with_three_entries(self):
        entries = [_fake_entry("request"), _fake_entry("product", "p1"), _fake_entry("proposal", "pp1")]

        items = _build_refinement_applied_unable(entries)

        assert len(items) == 3
        assert [i.root.scope for i in items] == ["request", "product", "proposal"]
        # Each scope variant carries the right id field (or none for request)
        assert items[1].root.product_id == "p1"
        assert items[2].root.proposal_id == "pp1"
        # Every status is 'unable' until proposal-state persistence ships
        assert all(i.root.status.value == "unable" for i in items)
        # Notes describe the gap functionally (issue numbers are kept out of code per
        # project convention; git history carries traceability).
        assert all(i.root.notes and "Proposal-state persistence is not yet implemented" in i.root.notes for i in items)


# ---------------------------------------------------------------------------
# Refinement-applied wire shape (regression — library handles this natively in 4.3+)
# ---------------------------------------------------------------------------


class TestRefinementAppliedWireShape:
    """GetProductsResponse serializes refinement_applied with scope-keyed id fields per AdCP spec.

    Adcp library 4.3+ produces this wire shape natively (RefinementApplied2.product_id,
    RefinementApplied3.proposal_id). These tests act as regression guards on our integration
    with the library — they should keep passing without any wire-compat shim.
    """

    def test_request_scope_serializes_without_id(self):
        items = _build_refinement_applied_unable([_fake_entry("request", None)])
        resp = GetProductsResponse(products=[], refinement_applied=items)

        applied = resp.model_dump(mode="json")["refinement_applied"]

        assert applied[0]["scope"] == "request"
        assert "id" not in applied[0]
        assert "product_id" not in applied[0]
        assert "proposal_id" not in applied[0]

    def test_product_scope_serializes_as_product_id(self):
        items = _build_refinement_applied_unable([_fake_entry("product", "p1")])
        resp = GetProductsResponse(products=[], refinement_applied=items)

        applied = resp.model_dump(mode="json")["refinement_applied"]

        assert applied[0]["product_id"] == "p1"
        assert "id" not in applied[0]
        assert "proposal_id" not in applied[0]

    def test_proposal_scope_serializes_as_proposal_id(self):
        items = _build_refinement_applied_unable([_fake_entry("proposal", "pp1")])
        resp = GetProductsResponse(products=[], refinement_applied=items)

        applied = resp.model_dump(mode="json")["refinement_applied"]

        assert applied[0]["proposal_id"] == "pp1"
        assert "id" not in applied[0]
        assert "product_id" not in applied[0]

    def test_no_refinement_applied_passes_through(self):
        # When refinement_applied is None (brief/wholesale modes), nothing changes
        resp = GetProductsResponse(products=[], refinement_applied=None)
        result = resp.model_dump(mode="json")
        assert result.get("refinement_applied") is None

    def test_mixed_scopes_each_gets_correct_field(self):
        entries = [_fake_entry("request"), _fake_entry("product", "p1"), _fake_entry("proposal", "pp1")]
        items = _build_refinement_applied_unable(entries)
        resp = GetProductsResponse(products=[], refinement_applied=items)

        applied = resp.model_dump(mode="json")["refinement_applied"]

        assert applied[0]["scope"] == "request"
        assert "id" not in applied[0] and "product_id" not in applied[0] and "proposal_id" not in applied[0]
        assert applied[1]["product_id"] == "p1"
        assert applied[2]["proposal_id"] == "pp1"


# ---------------------------------------------------------------------------
# Mode branching at the impl level (Layer 6.1, 6.2)
# ---------------------------------------------------------------------------


class TestModeBranching:
    """_get_products_impl branches on req.buying_mode for ranker, brief_relevance, refinement_applied."""

    async def test_wholesale_mode_skips_ranker_and_omits_brief_relevance(self):
        # tenant_overrides go through ProductEnv kwargs
        with ProductEnv(product_ranking_prompt="rank these") as env:
            env.add_product(product_id="prod_001", name="Display Ad")

            response = await env.call_impl(buying_mode="wholesale", brief="")

            # No ranker call (wholesale skips ranking)
            assert env.mock["ranking_factory"].called is False  # type: ignore[attr-defined]
            # No brief_relevance on products (wholesale: no brief)
            assert all(p.brief_relevance is None for p in response.products)
            # No refinement_applied on wholesale response
            assert response.refinement_applied is None

    async def test_refine_mode_returns_refinement_applied_unable(self):
        with ProductEnv() as env:
            env.add_product(product_id="prod_001")

            response = await env.call_impl(
                buying_mode="refine",
                brief="",
                refine=[{"scope": "request", "ask": "more video"}],
            )

            assert response.refinement_applied is not None
            assert len(response.refinement_applied) == 1
            assert response.refinement_applied[0].root.status.value == "unable"
            assert response.refinement_applied[0].root.scope == "request"
            # Refine mode does not run the ranker (no brief_relevance until proposal-state
            # persistence ships and refine entries can produce relevance-aware results)
            assert all(p.brief_relevance is None for p in response.products)

    async def test_brief_mode_runs_ranker_when_configured(self):
        """Brief mode invokes the ranker when tenant has product_ranking_prompt + AI enabled."""
        with ProductEnv(product_ranking_prompt="rank these") as env:
            env.add_product(product_id="prod_001", name="High-relevance product")

            # Wire the ranker mock to claim AI is enabled and return a ranking
            mock_factory = MagicMock()
            mock_factory.is_ai_enabled.return_value = True
            mock_factory.create_model.return_value = MagicMock()
            env.mock["ranking_factory"].return_value = mock_factory  # type: ignore[attr-defined]

            with (
                patch(
                    "src.services.ai.agents.ranking_agent.create_ranking_agent",
                    return_value=MagicMock(),
                ),
                patch(
                    "src.services.ai.agents.ranking_agent.rank_products_async",
                    new_callable=AsyncMock,
                    return_value=_ranking_result_for(["prod_001"], reason="explains the brief match"),
                ),
            ):
                response = await env.call_impl(buying_mode="brief", brief="display ads")

            assert len(response.products) == 1
            # brief_relevance is plumbed from ranker.reason
            assert response.products[0].brief_relevance == "explains the brief match"
            assert response.refinement_applied is None


# ---------------------------------------------------------------------------
# Audit log extension (Layer 6.5)
# ---------------------------------------------------------------------------


class TestAuditLogExtension:
    """Audit log details include buying_mode, refine_count, pre_v3_defaulted."""

    async def test_brief_mode_audit_includes_new_fields(self):
        with ProductEnv() as env, patch("src.core.tools.products.get_audit_logger") as mock_get_audit:
            mock_audit = MagicMock()
            mock_get_audit.return_value = mock_audit

            await env.call_impl(buying_mode="brief", brief="display ads")

            # Find the get_products audit call (other operations may also log)
            calls = [c for c in mock_audit.log_operation.call_args_list if c.kwargs.get("operation") == "get_products"]
            assert calls, "Expected at least one get_products audit call"
            details = calls[0].kwargs["details"]

            assert details["buying_mode"] == "brief"
            assert details["refine_count"] == 0
            assert details["pre_v3_defaulted"] is False

    async def test_refine_mode_audit_records_refine_count(self):
        with ProductEnv() as env, patch("src.core.tools.products.get_audit_logger") as mock_get_audit:
            mock_audit = MagicMock()
            mock_get_audit.return_value = mock_audit

            await env.call_impl(
                buying_mode="refine",
                brief="",
                refine=[
                    {"scope": "request", "ask": "more video"},
                    {"scope": "product", "product_id": "p1", "ask": "include this"},
                ],
            )

            calls = [c for c in mock_audit.log_operation.call_args_list if c.kwargs.get("operation") == "get_products"]
            assert calls
            details = calls[0].kwargs["details"]
            assert details["buying_mode"] == "refine"
            assert details["refine_count"] == 2

    async def test_pre_v3_defaulted_flag_propagates(self):
        with ProductEnv() as env, patch("src.core.tools.products.get_audit_logger") as mock_get_audit:
            mock_audit = MagicMock()
            mock_get_audit.return_value = mock_audit

            await env.call_impl(buying_mode="brief", brief="display ads", pre_v3_defaulted=True)

            calls = [c for c in mock_audit.log_operation.call_args_list if c.kwargs.get("operation") == "get_products"]
            assert calls
            assert calls[0].kwargs["details"]["pre_v3_defaulted"] is True


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _fake_entry(scope: str, entry_id: str | None = None) -> Refine:
    """Build a REAL Refine RootModel (not a mock) so the builder reads real fields.

    Adcp library Refine is a discriminated-union root over Refine1 (request), Refine2
    (product, product_id), Refine3 (proposal, proposal_id). A MagicMock satisfies a read
    of ANY attribute — including a field the variant does not carry — so a wrong-field
    regression in _build_refinement_applied_unable would pass silently. A real model
    fails that read, which is the point of exercising the builder here.
    """
    payload: dict[str, Any] = {"scope": scope}
    if scope == "request":
        payload["ask"] = "narrow the results"
    elif scope == "product":
        payload["product_id"] = entry_id
    elif scope == "proposal":
        payload["proposal_id"] = entry_id
    return Refine.model_validate(payload)


def _ranking_result_for(product_ids: list[str], reason: str = "matched") -> Any:
    """Build a fake ProductRankingResult with rankings for the given product_ids."""
    rankings = []
    for pid in product_ids:
        r = MagicMock()
        r.product_id = pid
        r.relevance_score = 0.9
        r.reason = reason
        rankings.append(r)
    result = MagicMock()
    result.rankings = rankings
    return result
