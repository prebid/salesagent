"""Regression: then_proceed_with_resolved_account must not silently skip verification.

The BDD step ``the request should proceed with resolved account`` claims that
account resolution succeeded and scoped processing to the resolved principal.
Its DB verification was guarded by ``if creative is not None and
expected_principal:`` — and ``expected_principal`` was computed as ``None``
whenever ``ctx["identity"]`` was not a ``dict`` (it is always a
``ResolvedIdentity`` object), so the assertion was silently skipped in every
realistic scenario. The step passed without ever verifying the resolved-account
claim.

This test pins the corrected behavior: when the persisted creative is scoped to
a DIFFERENT principal than the resolved one, the step MUST fail.

beads: salesagent-txo1
"""

from __future__ import annotations

import pytest

from src.core.schemas import SyncCreativesResponse
from src.core.schemas.creative import SyncCreativeResult
from tests.bdd.steps.domain.uc006_sync_creatives import then_proceed_with_resolved_account
from tests.factories import CreativeFactory, PrincipalFactory, TenantFactory
from tests.harness import CreativeSyncEnv, make_identity

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def test_step_fails_when_creative_scoped_to_wrong_principal(integration_db):
    """Step must reject when persisted creative belongs to a different principal.

    Reproduces the silent-skip bug: ctx["identity"] is a ResolvedIdentity object
    (not a dict), so the old expected_principal computation yielded None and the
    DB check was skipped — the step passed even though account resolution scoped
    the creative to the wrong principal.
    """
    with CreativeSyncEnv() as env:
        tenant = TenantFactory()
        expected = PrincipalFactory(tenant=tenant)
        wrong = PrincipalFactory(tenant=tenant)
        CreativeFactory(tenant=tenant, principal=wrong, creative_id="cr1")

        resp = SyncCreativesResponse(creatives=[SyncCreativeResult(creative_id="cr1", action="created")])
        ctx = {
            "env": env,
            "response": resp,
            "creatives": [{"creative_id": "cr1"}],
            "principal_id": expected.principal_id,
            "tenant_id": tenant.tenant_id,
            "identity": make_identity(
                principal_id=expected.principal_id,
                tenant_id=tenant.tenant_id,
                tenant={"tenant_id": tenant.tenant_id, "name": tenant.name},
            ),
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
            "response": resp,
            "creatives": [{"creative_id": "cr1"}],
            "principal_id": resolved.principal_id,
            "tenant_id": tenant.tenant_id,
            "identity": make_identity(
                principal_id=resolved.principal_id,
                tenant_id=tenant.tenant_id,
                tenant={"tenant_id": tenant.tenant_id, "name": tenant.name},
            ),
        }

        # Must not raise — account resolution scoped the creative correctly.
        then_proceed_with_resolved_account(ctx)
