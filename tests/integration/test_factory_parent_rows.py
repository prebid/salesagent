"""A factory builds a parent row only when the caller names no id for it.

factory_boy evaluates every parent ``SubFactory``, ``Meta.exclude`` included, so a call that
named its parent by id still committed a second tenant, or moved its row into that tenant when
the parent reached the model's relationship (#2658).
"""

from types import SimpleNamespace

import pytest

from src.core.database.models import Tenant
from tests.factories import (
    AccountFactory,
    AgentAccountAccessFactory,
    CreativeAssignmentFactory,
    CreativeFactory,
    GAMInventoryFactory,
    MediaBuyFactory,
    PrincipalFactory,
    TenantFactory,
)
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def world(integration_db):
    with IntegrationEnv() as env:
        tenant = TenantFactory(tenant_id="t1")
        principal = PrincipalFactory(tenant=tenant, principal_id="p1")
        yield SimpleNamespace(
            env=env,
            tenant=tenant,
            principal=principal,
            account=AccountFactory(tenant=tenant, account_id="a1"),
            creative=CreativeFactory(tenant=tenant, principal=principal, creative_id="c1"),
            media_buy=MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id="mb1"),
        )


def _tenant_ids(world):
    return {tenant.tenant_id for tenant in world.env.query(Tenant)}


#: Each shape in which a caller names a factory's parent by id instead of passing the parent.
NAMED_BY_ID = {
    "account": lambda w: AccountFactory(tenant_id="t1", account_id="a2"),
    "grant, all ids": lambda w: AgentAccountAccessFactory(tenant_id="t1", principal_id="p1", account_id="a1"),
    "grant, tenant id beside objects": lambda w: AgentAccountAccessFactory(
        tenant_id="t1", principal=w.principal, account=w.account
    ),
    "grant, tenant id alone": lambda w: AgentAccountAccessFactory(tenant_id="t1"),
    "principal": lambda w: PrincipalFactory(tenant_id="t1", principal_id="p2"),
    "gam inventory": lambda w: GAMInventoryFactory(tenant_id="t1"),
    "creative assignment": lambda w: CreativeAssignmentFactory(
        tenant_id="t1", creative_id="c1", principal_id="p1", media_buy_id="mb1"
    ),
}


@pytest.mark.parametrize("call", NAMED_BY_ID.values(), ids=NAMED_BY_ID.keys())
def test_a_parent_named_by_id_is_not_built(world, call):
    before = _tenant_ids(world)
    row = call(world)
    assert _tenant_ids(world) == before
    assert row.tenant_id == "t1"


@pytest.mark.parametrize(
    "factory_class",
    [AccountFactory, AgentAccountAccessFactory, PrincipalFactory, GAMInventoryFactory, CreativeAssignmentFactory],
    ids=lambda factory_class: factory_class.__name__,
)
def test_a_parent_nobody_names_is_built(world, factory_class):
    before = _tenant_ids(world)
    row = factory_class()
    assert _tenant_ids(world) - before == {row.tenant_id}


def test_a_parent_passed_as_an_object_supplies_its_ids(world):
    grant = AgentAccountAccessFactory(tenant=world.tenant, principal=world.principal, account=world.account)
    assignment = CreativeAssignmentFactory(creative=world.creative, media_buy=world.media_buy)
    assert (grant.tenant_id, grant.principal_id, grant.account_id) == ("t1", "p1", "a1")
    assert (assignment.tenant_id, assignment.creative_id, assignment.principal_id, assignment.media_buy_id) == (
        "t1",
        "c1",
        "p1",
        "mb1",
    )
