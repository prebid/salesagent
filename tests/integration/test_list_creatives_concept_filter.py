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
not just concept_ids) and is tracked separately. Layer 1 still pins that MCP
rejects the malformed filter.

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


def _seed_authenticated_principal(env: CreativeListEnv) -> None:
    """Seed the tenant+principal the env authenticates as, so the request reaches
    filter validation rather than failing auth first."""
    from tests.factories import PrincipalFactory, TenantFactory

    tenant = TenantFactory(tenant_id=env._tenant_id)
    PrincipalFactory(tenant=tenant, principal_id=env._principal_id)


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
            # POST-F3: the buyer is told how to recover.
            body = envelope.envelope if hasattr(envelope, "envelope") else envelope
            assert body["errors"][0].get("suggestion"), (
                f"{transport}: VALIDATION_ERROR envelope must carry a recovery suggestion: {body['errors'][0]}"
            )
