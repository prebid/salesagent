"""The ci-test seed puts a resolvable principal in the test's own database.

A fixture that creates rows nothing can read back is the failure mode worth guarding:
``integration_db`` hands each test a UNIQUE database, so a seed committed to the wrong
session or the wrong engine leaves the code under test looking at an empty schema while
the fixture reports success.

So this resolves the seeded token through PRODUCTION's own resolver rather than
re-querying the rows the factory just made — the same distinction the repo draws between
asserting on a response and re-reading the Given.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def test_the_seeded_token_resolves_to_the_seeded_tenant(ci_test_principal):
    """Production's resolver, not a factory read-back."""
    from src.core.resolved_identity import _resolve_identity
    from tests.helpers.credentials import credential_headers

    # The tenant is addressed too, because a token is resolved INSIDE the tenant the
    # request names (PrincipalRepository.find_by_token_hash; the cross-tenant lookup is
    # seed maintenance, not the credential path). A token-only credential resolves no
    # tenant, so the resolver rejects it however well the row is seeded.
    identity = _resolve_identity(
        credential_headers(token=ci_test_principal.token, tenant=ci_test_principal.tenant_id),
        require_valid_token=True,
    )

    assert identity.principal_id == ci_test_principal.principal_id
    assert identity.tenant_id == ci_test_principal.tenant_id


def test_the_tenant_carries_the_deps_a_product_needs(ci_test_principal, factory_session):
    """USD and ``all_inventory`` are prerequisites, not decoration.

    CLAUDE.md's setup order is Tenant -> CurrencyLimit (USD, required for budget
    validation) -> PropertyTag (``all_inventory``, required by ``property_tags``
    references). A seed missing either fails later in ways that read as a product bug.

    Read through the tenant's own ORM relationships on the session the factories are
    bound to: it is the one database this test has, so no second session is opened
    here, and a raw ``get_db_session()`` query in a test body is what the
    repository-pattern guard refuses.
    """
    from src.core.database.models import Tenant

    tenant = factory_session.get(Tenant, ci_test_principal.tenant_id)

    assert tenant is not None
    assert [c.currency_code for c in tenant.currency_limits] == ["USD"]
    assert [t.tag_id for t in tenant.property_tags] == ["all_inventory"]
