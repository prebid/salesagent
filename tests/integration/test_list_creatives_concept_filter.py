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
   (POST-F3) on REST and A2A, which coerce the wire dict through the shared
   ``coerce_creative_filters`` helper.

MCP is intentionally excluded from layer 2: it types the tool param as
``CreativeFilters`` (required by the wrapper-typed-params guard), so FastMCP's
TypeAdapter rejects ``concept_ids=[]`` *before* the tool body runs — a raw input
ValidationError, not an ``AdCPError`` the MCP boundary could wrap into the envelope.
Translating FastMCP TypeAdapter input errors into the two-layer envelope is a
pre-existing, tool-agnostic MCP-boundary concern (it affects every typed param,
not just concept_ids) and is tracked separately in #1507. Layer 1 still pins that
MCP rejects the malformed filter.

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
# Transports whose dict→CreativeFilters coercion runs through coerce_creative_filters
# (and therefore emit the spec envelope + suggestion). See module docstring re: MCP.
_HELPER_WIRE = [Transport.A2A, Transport.REST]


def _seed_authenticated_principal(env: CreativeListEnv):
    """Seed (and return) the tenant+principal the env authenticates as, so the
    request reaches filter validation / listing rather than failing auth first."""
    from tests.factories import PrincipalFactory, TenantFactory

    tenant = TenantFactory(tenant_id=env._tenant_id)
    principal = PrincipalFactory(tenant=tenant, principal_id=env._principal_id)
    return tenant, principal


class TestConceptIdsFilterValidation:
    """Malformed concept_ids filter is rejected, with a spec envelope on REST/A2A."""

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

    @pytest.mark.parametrize("transport", _HELPER_WIRE)
    def test_empty_concept_ids_emits_validation_envelope(self, integration_db, transport):
        """REST/A2A surface the two-layer VALIDATION_ERROR envelope with a suggestion."""
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

    def test_non_scalar_concept_value_is_dropped(self, integration_db, caplog):
        import logging

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
            with caplog.at_level(logging.WARNING):
                result = env.call_via(Transport.REST)

            assert not result.is_error, f"non-scalar concept value crashed the listing: {result.error!r}"
            creative = result.wire_response["creatives"][0]
            # Dropped to None → exclude_none omits the keys from the wire entirely.
            assert "concept_id" not in creative
            assert "concept_name" not in creative
            # Observability (No Quiet Failures): the drop is surfaced in logs, not silent.
            assert "Dropping non-scalar concept value" in caplog.text


class TestSellerConceptEnrichmentIsFilterable:
    """The seller-side concept the GAM producer writes (#1506) is findable by the
    #1407 concept_ids filter and surfaced on the wire. This chains the real producer
    helper → data blob → filter, so a key-name drift between the producer and the
    reader (e.g. writing ``concept`` while the filter reads ``concept_id``) reddens
    this end-to-end. It is the production data source #1506 exists to provide."""

    def _enriched_data(self, order_id: str):
        from src.core.schemas import AssetStatus
        from src.core.tools.media_buy_create import _merge_creative_enrichment

        # Exactly what the GAM adapter surfaces for a creative pushed into an order,
        # folded into the data blob by the production writeback path.
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
