"""A server-side property-list failure must not reach the buyer as their mistake.

``_get_products_impl`` wrapped every non-``AdCPError`` out of
``resolve_property_list`` in ``AdCPValidationError(..., recovery="transient")``.
Two things are wrong with that on the wire, and only the wire can show them:

1. The buyer is told ``VALIDATION_ERROR`` — "your request is malformed" — for a
   fault entirely on our side (an ``AttributeError``, a ``TypeError``, a DNS
   failure inside the resolver).
2. The arm forced ``recovery="transient"`` while the pinned AdCP enum classifies
   ``VALIDATION_ERROR`` as ``correctable``. The buyer is simultaneously told the
   request is invalid AND that retrying the identical request may work.

Graded on ``wire_error_envelope`` across every transport that can carry this
request — see the REST note on ``_WIRE_TRANSPORTS`` below.
Asserting on the raised exception object instead would grade the plumbing, not
the contract the buyer reads — and an ``_impl``-level ``pytest.raises`` cannot
see the recovery hint at all, which is half the defect.
"""

from __future__ import annotations

import pytest

from tests.factories import PrincipalFactory, TenantFactory
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# IMPL has no wire envelope by definition, so it cannot grade this at all.
#
# REST is absent for a REASON, not an oversight: GetProductsBody
# (src/routes/api_v1.py) declares no property_list field, so the REST transport
# structurally cannot carry this request -- the filter is unreachable there and
# a REST row here would grade an empty success. Tracked as a production bug
# (get_products property_list unreachable over REST); add the row when it lands.
_WIRE_TRANSPORTS = [Transport.MCP, Transport.A2A]


@pytest.mark.parametrize("transport", _WIRE_TRANSPORTS, ids=lambda t: t.value)
def test_resolver_crash_is_not_reported_as_the_buyers_validation_error(integration_db, transport):
    """A crash inside property-list resolution reaches the buyer as a server fault."""
    from tests.harness.product import ProductEnv
    from tests.helpers import assert_envelope_shape

    with ProductEnv() as env:
        tenant = TenantFactory(tenant_id="test_tenant")
        PrincipalFactory(tenant=tenant, principal_id="test_principal")
        env.set_policy_approved()
        env.set_ranking_disabled()
        # An implementation bug inside the resolver, not a bad request.
        env.mock["resolve_property_list"].side_effect = RuntimeError("DNS resolution failed")

        result = env.call_via(
            transport,
            brief="video ads",
            property_list={"agent_url": "https://example.com", "list_id": "test-list"},
        )

        assert result.is_error, (
            "a crash inside property-list resolution must fail the request, got "
            f"{getattr(result, 'wire_response', None) or result.payload!r}"
        )
        assert_envelope_shape(
            result.wire_error_envelope,
            "SERVICE_UNAVAILABLE",
            recovery="transient",
        )
