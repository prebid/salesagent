"""Regression: then_proceed_with_resolved_account must not silently skip verification.

The BDD step ``the request should proceed with resolved account`` claims that
account resolution succeeded and scoped processing to the resolved principal.
Its DB verification was guarded by ``if creative is not None and
expected_principal:`` — and ``expected_principal`` was computed as ``None``
whenever the ctx carried the principal in a shape the step did not read, so the
assertion was silently skipped in every realistic scenario. The step passed
without ever verifying the resolved-account claim.

This test pins the corrected behavior: when the persisted creative is scoped to
a DIFFERENT principal than the resolved one, the step MUST fail. The resolved
principal reaches the step as ``ctx["principal_id"]``, the key the Given steps
write; there is no identity object in ctx for it to fall back to.

"""

from __future__ import annotations

import pytest

from src.core.schemas import SyncCreativesResponse
from src.core.schemas.creative import SyncCreativeResult
from tests.bdd.steps.domain.uc006_sync_creatives import then_proceed_with_resolved_account
from tests.factories import CreativeFactory, PrincipalFactory, TenantFactory
from tests.harness import CreativeSyncEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def test_step_fails_when_creative_scoped_to_wrong_principal(integration_db):
    """Step must reject when persisted creative belongs to a different principal.

    Reproduces the silent-skip bug: the step used to compute its expected principal
    from a ctx shape it never received and skip the DB check when that came back None,
    so it passed even though account resolution scoped the creative to the wrong
    principal.
    """
    with CreativeSyncEnv() as env:
        tenant = TenantFactory()
        expected = PrincipalFactory(tenant=tenant)
        wrong = PrincipalFactory(tenant=tenant)
        CreativeFactory(tenant=tenant, principal=wrong, creative_id="cr1")

        resp = SyncCreativesResponse(creatives=[SyncCreativeResult(creative_id="cr1", action="created")])
        ctx = {
            "env": env,
            # The payload key for a value produced WITHOUT dispatching — which is
            # what this test does: it drives the step directly to exercise its
            # principal-scoping logic, not a transport.
            "self_dispatched_response": resp,
            "creatives": [{"creative_id": "cr1"}],
            "principal_id": expected.principal_id,
            "tenant_id": tenant.tenant_id,
        }

        with pytest.raises(AssertionError, match="principal"):
            then_proceed_with_resolved_account(ctx)


def test_step_passes_when_creative_scoped_to_resolved_principal(integration_db):
    """Step passes when the persisted creative is scoped to the resolved principal.

    Guards against over-correction: the fix must still accept the legitimate case.
    """
    with CreativeSyncEnv() as env:
        tenant = TenantFactory()
        resolved = PrincipalFactory(tenant=tenant)
        CreativeFactory(tenant=tenant, principal=resolved, creative_id="cr1")

        resp = SyncCreativesResponse(creatives=[SyncCreativeResult(creative_id="cr1", action="created")])
        ctx = {
            "env": env,
            # The payload key for a value produced WITHOUT dispatching — which is
            # what this test does: it drives the step directly to exercise its
            # principal-scoping logic, not a transport.
            "self_dispatched_response": resp,
            "creatives": [{"creative_id": "cr1"}],
            "principal_id": resolved.principal_id,
            "tenant_id": tenant.tenant_id,
        }

        # Must not raise — account resolution scoped the creative correctly.
        then_proceed_with_resolved_account(ctx)
