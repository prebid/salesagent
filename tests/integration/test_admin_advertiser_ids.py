"""Transaction races, database faults and real adapter use supplement the admin BDD flows."""

from datetime import UTC, datetime

import pytest

from src.adapters.mock_ad_server import MockAdServer
from src.admin.blueprints import principals as principals_blueprint
from src.core.schemas import Principal
from tests.factories import PrincipalFactory
from tests.harness.admin_principal import AdminPrincipalEnv
from tests.helpers import concurrent_commit_in_write_window
from tests.helpers.mock_adapter import create_mock_media_buy

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]
CREATE_OPERATION = "AdminUI.create_principal"


@pytest.fixture
def creation_env(integration_db):
    with AdminPrincipalEnv() as env:
        yield env


def test_race_loser_gets_the_precheck_answer(creation_env):
    env = creation_env
    tenant = env.ensure_tenant()
    with concurrent_commit_in_write_window(
        principals_blueprint, lambda: PrincipalFactory(tenant=tenant, principal_id="buyer")
    ):
        loser = env.post_create(principal_id="buyer")
    loser_page = env.follow(loser)
    duplicate = env.post_create(principal_id="buyer")
    duplicate_page = env.follow(duplicate)
    assert (loser.status_code, loser.headers["Location"]) == (duplicate.status_code, duplicate.headers["Location"])
    assert b"Advertiser ID is already in use" in loser_page.data
    assert b"Advertiser ID is already in use" in duplicate_page.data
    assert env.principals(name="Created Advertiser") == []
    assert [row.success for row in env.audit_rows(CREATE_OPERATION, method="POST")] == [False, False]


def test_database_error_is_audited_without_the_driver_message(creation_env):
    env = creation_env
    env.interrupt_next_statement(terminate=False, table="principals", sending="fault_buyer")
    response = env.post_create(principal_id="fault_buyer")
    assert response.status_code == 302
    assert b"Error creating advertiser" in env.follow(response).data
    (row,) = env.audit_rows(CREATE_OPERATION, method="POST")
    assert (row.success, row.error_message) == (False, "A database error occurred. The details have been logged.")
    assert env.principals(name="Created Advertiser") == []


def test_longest_id_fits_the_real_mock_adapter_audit(creation_env):
    env = creation_env
    principal_id = "a" * principals_blueprint.ADVERTISER_ID_MAX_LENGTH
    assert env.post_create(principal_id=principal_id).status_code == 302
    (row,) = env.principals(name="Created Advertiser")
    adapter = MockAdServer(
        config={},
        principal=Principal(principal_id=row.principal_id, name=row.name, platform_mappings=row.platform_mappings),
        tenant_id=env.tenant_id,
    )
    buy_id = create_mock_media_buy(adapter, package_id="id_width_package")
    adapter.add_creative_assets(buy_id, [], datetime.now(UTC))
    (audit,) = env.audit_rows("mock.add_creative_assets")
    assert audit.success
    assert audit.principal_id == principal_id
    assert audit.adapter_id == row.platform_mappings["mock"]["advertiser_id"]
