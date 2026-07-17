"""Integration tests for the list_creatives concept_ids filter error path (#1407).

The happy path (filtering + concept_id/concept_name exposure) is covered by the
``@concept-id`` BDD storyboard scenario across a2a/mcp/rest. This module pins the
*error* path Chris flagged in review: a malformed ``filters.concept_ids`` (empty
array) violates the schema's ``minItems: 1`` and must be rejected — never silently
returning the whole library.

Two layers of guarantee:

1. **Rejected on every wire transport** (a2a/mcp/rest) — the malformed filter never
   degrades to "return everything".
2. **Spec two-layer ``VALIDATION_ERROR`` envelope with a recovery suggestion**
   (POST-F3) on every wire transport. REST and A2A coerce the wire dict through
   the shared ``coerce_creative_filters`` helper; MCP catches FastMCP TypeAdapter
   validation at the boundary and emits the same AdCP envelope.

Spec: ``core/creative-filters.json`` (concept_ids ``minItems: 1``) + the BR-UC-018
ext-c contract (validation failure → VALIDATION_ERROR + suggestion).
"""

import pytest

from tests.harness import CreativeListEnv
from tests.harness.transport import Transport
from tests.helpers import assert_envelope_shape

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Wire transports only — IMPL has no wire envelope (and the dict→CreativeFilters
# coercion under test happens at the transport boundary, not in _impl).
_ALL_WIRE = [Transport.A2A, Transport.MCP, Transport.REST]


def _seed_authenticated_principal(env: CreativeListEnv):
    """Seed (and return) the tenant+principal the env authenticates as, so the
    request reaches filter validation / listing rather than failing auth first."""
    from tests.factories import PrincipalFactory, TenantFactory

    tenant = TenantFactory(tenant_id=env._tenant_id)
    principal = PrincipalFactory(tenant=tenant, principal_id=env._principal_id)
    return tenant, principal


class TestConceptIdsFilterValidation:
    """Malformed concept_ids filter is rejected, with a spec envelope on every wire transport."""

    @pytest.mark.parametrize("transport", _ALL_WIRE)
    def test_empty_concept_ids_array_is_rejected(self, integration_db, transport):
        """filters={'concept_ids': []} violates minItems:1 → rejected on every transport."""
        with CreativeListEnv() as env:
            _seed_authenticated_principal(env)

            result = env.call_via(transport, filters={"concept_ids": []})

            assert result.is_error, (
                f"{transport}: empty concept_ids must be rejected, not silently return the library; "
                f"got payload {result.payload!r}"
            )

    @pytest.mark.parametrize("transport", _ALL_WIRE)
    def test_empty_concept_ids_emits_validation_envelope(self, integration_db, transport):
        """Wire transports surface the two-layer VALIDATION_ERROR envelope with a suggestion."""
        with CreativeListEnv() as env:
            _seed_authenticated_principal(env)

            result = env.call_via(transport, filters={"concept_ids": []})

            envelope = result.wire_error_envelope
            assert envelope is not None, f"{transport}: no wire error envelope captured"
            assert_envelope_shape(envelope, "VALIDATION_ERROR", recovery="correctable")
            # POST-F3: the buyer is told how to recover. wire_error_envelope is always
            # a dict here (the AdCPToolError accessor lives in assert_envelope_shape).
            assert envelope["errors"][0].get("suggestion"), (
                f"{transport}: VALIDATION_ERROR envelope must carry a recovery suggestion: {envelope['errors'][0]}"
            )


class TestNumericConceptCoercion:
    """A numeric concept_id/concept_name in the data blob (CM360-style group ids) is
    coerced to the spec's string type, not crashed on. Regression guard for the #1
    fix: reverting the str()-coercion reddens this (the listing raises mid-build)."""

    def test_numeric_concept_id_is_coerced_to_string(self, integration_db):
        from tests.factories import CreativeFactory

        with CreativeListEnv() as env:
            tenant, principal = _seed_authenticated_principal(env)
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                format="display_300x250",
                status="approved",
                data={"assets": {}, "concept_id": 12345, "concept_name": 678},
            )
            result = env.call_via(Transport.REST)
            assert not result.is_error, f"listing errored on a numeric concept_id: {result.error!r}"
            creative = result.wire_response["creatives"][0]
            assert creative["concept_id"] == "12345"
            assert creative["concept_name"] == "678"


class TestNonScalarConceptValueDropped:
    """A non-scalar concept_id/concept_name (corrupt external value) is dropped to
    null and logged, not projected as a repr and not crashed on — regression guard
    for the _coerce_concept_value non-scalar branch (the symmetric half of the
    numeric-coercion fix: reverting `return None` to a passthrough 500s the listing)."""

    def test_non_scalar_concept_value_is_dropped(self, integration_db):
        from unittest.mock import patch

        from tests.factories import CreativeFactory

        with CreativeListEnv() as env:
            tenant, principal = _seed_authenticated_principal(env)
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                format="display_300x250",
                status="approved",
                data={"assets": {}, "concept_id": ["x"], "concept_name": {"k": "v"}},
            )
            # Assert the code EMITS the warning by patching the module logger, not by
            # capturing log records: the REST path runs in-process, so the patch applies,
            # and this is immune to the tox/integration logging config (levels, handlers,
            # propagation, logging.disable) that suppressed capture-based approaches.
            with patch("src.core.tools.creatives.listing.logger") as mock_logger:
                result = env.call_via(Transport.REST)

            assert not result.is_error, f"non-scalar concept value crashed the listing: {result.error!r}"
            creative = result.wire_response["creatives"][0]
            # Dropped to None → exclude_none omits the keys from the wire entirely.
            assert "concept_id" not in creative
            assert "concept_name" not in creative
            # Observability (No Quiet Failures): the drop is surfaced in logs, not silent.
            warnings_logged = " ".join(str(c) for c in mock_logger.warning.call_args_list)
            assert "Dropping non-scalar concept value" in warnings_logged, (
                f"expected the non-scalar drop warning; logger.warning calls: {mock_logger.warning.call_args_list}"
            )


class TestSellerConceptEnrichmentIsFilterable:
    """The concept the #1506 merge helper writes is findable by the #1407 concept_ids
    filter and surfaced on the wire. This chains the merge helper's output → data blob
    → filter, so a key-name drift between the helper and the reader (e.g. writing
    ``concept`` while the filter reads ``concept_id``) reddens here. The full
    producer → real writeback → DB → reader chain (which this does NOT exercise — it
    calls the merge helper directly, not a writeback site) is pinned separately by
    ``test_execute_approved_platform_ids.py``."""

    def _enriched_data(self, order_id: str):
        from src.core.schemas import AssetStatus
        from src.core.tools.media_buy_create import _merge_creative_enrichment

        # The AssetStatus the GAM producer surfaces for a creative pushed into an order,
        # run through the real merge helper directly (the production writeback sites are
        # covered by test_execute_approved_platform_ids.py).
        status = AssetStatus(
            creative_id=f"gam_{order_id}",
            status="approved",
            concept_id=f"gam-order-{order_id}",
            concept_name=f"GAM Order {order_id}",
            concept_source="gam_order",
        )
        return _merge_creative_enrichment({"assets": {}}, status)

    def test_gam_order_concept_is_filterable_end_to_end(self, integration_db):
        from tests.factories import CreativeFactory

        with CreativeListEnv() as env:
            tenant, principal = _seed_authenticated_principal(env)
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                format="display_300x250",
                status="approved",
                data=self._enriched_data("789"),
            )
            # A creative enriched from a different order must NOT match the filter.
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                format="display_300x250",
                status="approved",
                data=self._enriched_data("000"),
            )

            result = env.call_via(Transport.REST, filters={"concept_ids": ["gam-order-789"]})

            assert not result.is_error, f"concept filter errored: {result.error!r}"
            creatives = result.wire_response["creatives"]
            assert len(creatives) == 1, f"expected only the order-789 creative, got {creatives!r}"
            assert creatives[0]["concept_id"] == "gam-order-789"
            assert creatives[0]["concept_name"] == "GAM Order 789"
            # The internal provenance marker must never reach the wire. Today it can't
            # (the reader projects explicit kwargs + the subclass extra="ignore" policy),
            # but nothing else pins it — a future schema change that echoed data must redden.
            assert "concept_source" not in creatives[0]
