"""A defect in get_products' own AI code fails the request; only a provider failure degrades.

Ranking and the policy check fail open when the PROVIDER fails: ``run_agent``
(``src/services/ai/factory.py``) raises ``AIProviderError`` for that, and those are the only
exceptions either consumer catches. BR-UC-001's INV-4 and BR-RULE-002 rows grade the
provider failures on every transport. What they cannot show is the other half: an exception
raised by our own code on that path (here a ``ValueError`` out of the prompt builder) used
to be caught as well and answered with catalog order, or with an unscreened brief, and
nothing on the wire said so. It now reaches the buyer as the server fault it is.

Integration rather than BDD because the subject is a defect: producing one means replacing a
piece of our own code, which a scenario must not do and the e2e leg cannot.

The code on the wire is the boundary's for an untyped exception (``adcp_error_for``,
``src/core/exceptions.py``): a plain ``ValueError`` is VALIDATION_ERROR, anything else
INTERNAL_ERROR. This module grades that the request FAILS, with that code, instead of
answering as though the AI step had run or been skipped.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_SELLER_AI = {"provider": "gemini", "model": "gemini-2.0-flash", "api_key": "seller-key-defect-test"}

#: A defect, and the code the boundary answers an untyped one with.
_DEFECTS = pytest.mark.parametrize(
    ("defect", "code"),
    [(ValueError("defect"), "VALIDATION_ERROR"), (KeyError("defect"), "INTERNAL_ERROR")],
    ids=["ValueError", "KeyError"],
)


def _seed(env) -> None:
    """The tenant and principal the env's identity names, with one priced product and the seller's AI."""
    tenant = TenantFactory(tenant_id="test_tenant")
    PrincipalFactory(tenant=tenant, principal_id="test_principal")
    PricingOptionFactory(product=ProductFactory(tenant=tenant, product_id="prod_a", name="Homepage Banner"))
    env.configure_tenant_field("ai_config", _SELLER_AI)


@_DEFECTS
def test_a_defect_in_the_ranking_code_fails_the_request(integration_db, defect, code):
    from tests.harness.product import ProductEnv

    with ProductEnv() as env:
        _seed(env)
        env.set_tenant_ai_ranking("Prefer video inventory")
        with patch("src.services.ai.agents.ranking_agent.build_ranking_prompt", side_effect=defect):
            result = env.call_via(Transport.REST, brief="video campaign")

    assert result.is_error, f"a defect in the ranking code returned {result.wire_response!r}"
    result.assert_wire_error(code)


@_DEFECTS
def test_a_defect_in_the_policy_check_fails_the_request(integration_db, defect, code):
    from src.services.policy_check_service import PolicyCheckService
    from tests.harness.product import ProductEnv

    with ProductEnv() as env:
        _seed(env)
        env.configure_tenant_field("advertising_policy", {"enabled": True})
        env.set_ranking_disabled()
        # The real service on the seller's model; only the prompt builder is broken.
        env.mock["policy_service"].side_effect = lambda model: PolicyCheckService(model=model)
        with patch("src.services.ai.agents.policy_agent.build_policy_prompt", side_effect=defect):
            result = env.call_via(Transport.REST, brief="video campaign")

    assert result.is_error, f"a defect in the policy check returned {result.wire_response!r}"
    result.assert_wire_error(code)
