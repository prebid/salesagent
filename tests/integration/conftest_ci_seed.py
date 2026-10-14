"""The ci-test tenant and principal, seeded into every integration test database.

WHY A FIXTURE AND NOT A MIGRATION. ``alembic upgrade`` runs on container start in every
environment, so a tenant carrying the well-known ``ci-test-token`` in the migration chain
would exist in production with a live principal row — an auth bypass, not a hygiene
problem. And it would not work anyway: ``integration_db`` creates a UNIQUE database per
test (``tests/conftest_db.py``), so nothing seeded once on the container or in the
migration history reaches the database a test actually runs against.

WHY A FIXTURE AND NOT init_database_ci.py. That script seeds the Docker stack's single
shared ``adcp`` database with raw ``session.add`` calls. It is the right tool there and
the wrong one here: this needs to happen per test database, and this repo's rule is that
test data comes from factory-boy factories, never inline construction
(CLAUDE.md pattern #8).

This is pytest's setUp. Teardown is free: ``integration_db`` drops the whole database
afterwards, so there is nothing to undo and no state to leak between tests.

NOT autouse. Seeding a tenant into every database would change what tests that count or
enumerate tenants see, and the factories those tests use build their own. A test that
wants the well-known credential asks for ``ci_test_principal``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

#: The credential the storyboard runner, the e2e clients and the CLI examples all present,
#: and the subdomain that addresses the tenant holding it. NEITHER is restated here: both
#: are imported from the seeding script, which owns them, so the Docker stack and a local
#: integration run cannot answer different identities. The two seeds have different
#: MECHANISMS -- raw ``session.add`` there, factories here -- which is the whole reason
#: they must not also have different values.
from scripts.setup.init_database_ci import CI_TEST_TENANT_ID, CI_TEST_TOKEN


@dataclass(frozen=True)
class CiTestSeed:
    """What the seed created, so a test can assert against it without re-querying."""

    tenant_id: str
    principal_id: str
    #: The PLAINTEXT credential to present. Named for what it is: the row stores only
    #: sha256(token) and a prefix, so there is no access_token to read back.
    token: str
    subdomain: str


@pytest.fixture
def ci_test_principal(factory_session) -> CiTestSeed:
    """Seed the ci-test tenant + principal (and the deps a tenant needs to be usable).

    ``factory_session`` supplies the per-test database and the factory binding, so the
    rows land in the same database the code under test reads.

    CLAUDE.md's tenant setup order is Tenant -> CurrencyLimit (USD, required for budget
    validation) -> PropertyTag ("all_inventory", required by ``property_tags``
    references). Only the tag is created here: ``TenantFactory`` already attaches the USD
    limit through a ``RelatedFactory``, and creating a second violates
    ``uq_currency_limit``.
    """
    from src.core.credentials import hash_token, token_prefix
    from tests.factories import PrincipalFactory, PropertyTagFactory, TenantFactory

    tenant = TenantFactory(tenant_id=CI_TEST_TENANT_ID, subdomain=CI_TEST_TENANT_ID, name="CI Test Tenant")
    PropertyTagFactory(tenant=tenant, tag_id="all_inventory")
    # The hash and prefix of the DOCUMENTED token, not the factory's derived-from-id
    # default: this seed's whole purpose is that one known credential resolves here, and
    # the plaintext cannot be read back out of the row. `access_token=` used to be passed
    # instead -- a column that no longer exists and a field the factory does not declare,
    # so this fixture could not produce a principal answering ci-test-token at all.
    principal = PrincipalFactory(
        tenant=tenant,
        name="CI Test Principal",
        token_hash=hash_token(CI_TEST_TOKEN),
        token_prefix=token_prefix(CI_TEST_TOKEN),
    )
    factory_session.commit()

    return CiTestSeed(
        tenant_id=tenant.tenant_id,
        principal_id=principal.principal_id,
        token=CI_TEST_TOKEN,
        subdomain=tenant.subdomain,
    )
