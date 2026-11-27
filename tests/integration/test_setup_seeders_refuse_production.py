"""The setup seeders that write repository-published credentials refuse a production deployment.

All three refuse through ``scripts.setup.production_guard.refuse_on_production`` before they
touch the database, and each test injects production through the typed settings.

``init_database_ci`` and ``seed_storyboard_tenant`` create principals whose bearer tokens
(``CI_TEST_TOKEN``, ``STORYBOARD_TOKEN``) are written in this repository. Their tests assert
that no tenant and no principal answering to that token was written.

``scripts.setup.storyboard_signing.seed()`` makes a principal of the storyboard tenant trust
the signed-requests test kit: it onboards the kit's keyids for that principal and writes a
``signed_requests_grading`` record whose accepted JWKS holds the kit's PUBLIC keys. Anyone
holding the kit's published private keys can then sign as that principal. That is the
conformance contract on a grading target and a key-trust bypass anywhere else, so the seed
refuses under ``RuntimeSettings.is_production`` before it opens a session.

The storyboard tenant and the runner's bearer principal are seeded first, so without the
refusal the seed gets past its own "no tenant" and "no principal" checks and commits the
writes: the absence asserted below is not the absence of a tenant to write into. Like the
storyboard tox env, it reads the pinned compliance bundle's test kits.
"""

from __future__ import annotations

import pytest

from tests.harness._base import BareIntegrationEnv
from tests.helpers.settings_injection import inject_runtime

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def test_seed_refuses_under_a_production_signal_and_writes_no_trust_material(integration_db, monkeypatch):
    from scripts.setup.seed_storyboard_tenant import STORYBOARD_TOKEN
    from scripts.setup.storyboard_signing import (
        COUNTERPARTY_AGENT_URL,
        COUNTERPARTY_PRINCIPAL_ID,
        STORYBOARD_VIRTUAL_HOST,
        counterparty_kids,
        seed,
    )
    from src.core.credentials import hash_token
    from src.core.database.repositories.principal import PrincipalRepository
    from tests.factories import PrincipalFactory, TenantFactory

    with BareIntegrationEnv() as env:
        tenant = TenantFactory(virtual_host=STORYBOARD_VIRTUAL_HOST)
        PrincipalFactory(tenant=tenant, token_hash=hash_token(STORYBOARD_TOKEN))
        session = env.get_session()
        inject_runtime(monkeypatch, production=True)

        with pytest.raises(SystemExit, match="production"):
            seed()

        principals = PrincipalRepository(session, tenant.tenant_id)
        assert principals.get(COUNTERPARTY_PRINCIPAL_ID) is None, "the test counterparty principal was written"
        assert principals.find_by_agent_url(COUNTERPARTY_AGENT_URL) is None, "a principal at the kit's URL was written"
        onboarded = [kid for kid in counterparty_kids() if principals.find_by_signing_keyid(kid) is not None]
        assert onboarded == [], f"kit keyids were onboarded: {onboarded}"
        graded = [p.principal_id for p in principals.list_all() if p.signed_requests_grading is not None]
        assert graded == [], f"a grading record was written on {graded}"


def test_init_database_ci_refuses_under_a_production_signal_and_writes_no_tenant_or_token(integration_db, monkeypatch):
    from scripts.setup.init_database_ci import (
        CI_TEST_TENANT_ID,
        CI_TEST_TOKEN,
        CI_TEST_VIRTUAL_HOST,
        ISO_TEST_VIRTUAL_HOST,
        init_db_ci,
    )
    from src.core.credentials import hash_token
    from src.core.database.repositories.principal_lookup import find_principal_by_token_hash
    from src.core.database.repositories.tenant_lookup import TenantLookupRepository

    with BareIntegrationEnv() as env:
        session = env.get_session()
        inject_runtime(monkeypatch, production=True)

        with pytest.raises(SystemExit, match="production"):
            init_db_ci()

        tenants = TenantLookupRepository(session)
        assert tenants.find_by_id(CI_TEST_TENANT_ID) is None, "the CI tenant was written"
        assert tenants.find_by_virtual_host(CI_TEST_VIRTUAL_HOST) is None, "a tenant at the CI host was written"
        assert tenants.find_by_virtual_host(ISO_TEST_VIRTUAL_HOST) is None, "the isolation tenant was written"
        assert find_principal_by_token_hash(session, hash_token(CI_TEST_TOKEN)) is None, (
            "a principal answering to the repository's CI token was written"
        )


def test_seed_storyboard_tenant_refuses_under_a_production_signal_and_writes_no_tenant_or_token(
    integration_db, monkeypatch
):
    from scripts.setup.seed_storyboard_tenant import STORYBOARD_TOKEN, STORYBOARD_VIRTUAL_HOST, seed_storyboard_tenant
    from src.core.credentials import hash_token
    from src.core.database.repositories.principal_lookup import find_principal_by_token_hash
    from src.core.database.repositories.tenant_lookup import TenantLookupRepository

    with BareIntegrationEnv() as env:
        session = env.get_session()
        inject_runtime(monkeypatch, production=True)

        with pytest.raises(SystemExit, match="production"):
            seed_storyboard_tenant()

        assert TenantLookupRepository(session).find_by_virtual_host(STORYBOARD_VIRTUAL_HOST) is None, (
            "the storyboard tenant was written"
        )
        assert find_principal_by_token_hash(session, hash_token(STORYBOARD_TOKEN)) is None, (
            "a principal answering to the repository's storyboard token was written"
        )
