"""``identity_for()`` must null out principal_id on a failed token lookup.

Core Invariant (salesagent-z9e0): the in-process BDD/integration harness's
identity resolution must mirror production's ``resolve_identity()`` DB-lookup
semantics exactly — ``principal_id`` is only ever non-null if a real
``Principal`` row was found for the token/principal_id+tenant.

Production reference: ``src/core/resolved_identity.py::resolve_identity``
(129-172) sets ``principal_id=None`` whenever the token->principal DB lookup
finds no row. ``tests/harness/_base.py::BaseTestEnv.identity_for()`` already
performs the same lookup via ``_resolve_auth_token()`` (which correctly
returns ``None`` when no ``Principal`` row matches) but today passes
``principal_id=self._principal_id`` through to
``PrincipalFactory.make_identity()`` UNCONDITIONALLY (tests/harness/_base.py
~line 462), regardless of whether that lookup found a row. This diverges
from production and is what let
``tests/bdd/test_uc003_update_media_buy.py::test_authentication_error__principal_not_found_in_database``
disagree between in-process transports (impl/mcp/a2a/rest, which see the
stale principal_id) and ``e2e_rest`` (which goes through the real
``resolve_identity()`` over HTTP and correctly nulls it).
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestIdentityForNullsPrincipalIdOnFailedTokenLookup:
    """identity_for() must mirror resolve_identity(): no DB row -> principal_id=None."""

    def test_no_principal_row_nulls_principal_id(self, integration_db):
        """A principal_id with no backing Principal row must resolve to None.

        Mirrors the uc003 scenario: a Given step points the env at a
        principal_id whose row was never created (or was deleted) before
        identity is resolved. ``_resolve_auth_token()`` correctly finds no
        row and returns ``auth_token=None`` -- but today ``identity_for()``
        still passes the ghost ``principal_id`` string through to
        ``PrincipalFactory.make_identity()`` instead of nulling it, unlike
        production's ``resolve_identity()``.
        """
        from tests.factories import TenantFactory
        from tests.harness._base import BareIntegrationEnv

        with BareIntegrationEnv(tenant_id="ident_lookup_t1", principal_id="ghost_principal") as env:
            TenantFactory(tenant_id="ident_lookup_t1")
            # Deliberately no PrincipalFactory row for "ghost_principal".

            identity = env.identity

            assert identity.principal_id is None, (
                "identity_for() must null out principal_id when the DB token "
                "lookup finds no matching Principal row, mirroring production's "
                "resolve_identity() (src/core/resolved_identity.py:168-172). Got "
                f"principal_id={identity.principal_id!r} instead."
            )

    def test_existing_principal_row_keeps_principal_id(self, integration_db):
        """Contrast case: a real Principal row must still resolve principal_id.

        Guards against an over-eager fix that nulls principal_id
        unconditionally instead of gating on the token-lookup result.
        """
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness._base import BareIntegrationEnv

        with BareIntegrationEnv(tenant_id="ident_lookup_t2", principal_id="real_principal") as env:
            tenant = TenantFactory(tenant_id="ident_lookup_t2")
            PrincipalFactory(tenant=tenant, principal_id="real_principal")

            identity = env.identity

            assert identity.principal_id == "real_principal"
