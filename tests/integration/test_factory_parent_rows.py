"""A factory builds a parent row only when the caller names none of its keys.

factory_boy evaluates every parent ``SubFactory``, ``Meta.exclude`` included, so a call that
named its parent by id still committed a second tenant, or moved its row into that tenant when
the parent reached the model's relationship (#2658). Each test compares every row in the
database before and after one factory call.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import inspect

from src.core.database.models import Base
from tests.factories import (
    AccountFactory,
    AgentAccountAccessFactory,
    CreativeAssignmentFactory,
    CreativeFactory,
    GAMInventoryFactory,
    MediaBuyFactory,
    MediaPackageFactory,
    PricingOptionFactory,
    PrincipalFactory,
    ProductFactory,
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
            product=ProductFactory(tenant=tenant, product_id="prod1"),
        )


def _key(row):
    return (type(row).__name__, inspect(row).identity)


def _rows(world):
    """Every row in the database, as ``(model, primary key)``."""
    return {_key(row) for mapper in Base.registry.mappers for row in world.env.query(mapper.class_)}


#: Each shape in which a caller names a factory's parent by its keys instead of passing it.
NAMED_BY_ID = {
    "account": lambda w: AccountFactory(tenant_id="t1", account_id="a2"),
    "grant, all ids": lambda w: AgentAccountAccessFactory(tenant_id="t1", principal_id="p1", account_id="a1"),
    "grant, tenant id beside objects": lambda w: AgentAccountAccessFactory(
        tenant_id="t1", principal=w.principal, account=w.account
    ),
    "principal": lambda w: PrincipalFactory(tenant_id="t1", principal_id="p2"),
    "gam inventory": lambda w: GAMInventoryFactory(tenant_id="t1"),
    "creative assignment": lambda w: CreativeAssignmentFactory(
        tenant_id="t1", creative_id="c1", principal_id="p1", media_buy_id="mb1", package_id="pkg_001"
    ),
    "media package": lambda w: MediaPackageFactory(
        media_buy_id="mb1", package_id="pkg_new", package_config={"product_id": "prod1"}
    ),
    "pricing option": lambda w: PricingOptionFactory(tenant_id="t1", product_id="prod1"),
}


@pytest.mark.parametrize("call", NAMED_BY_ID.values(), ids=NAMED_BY_ID.keys())
def test_a_parent_named_by_id_is_not_built(world, call):
    before = _rows(world)
    row = call(world)
    assert _rows(world) - before == {_key(row)}


def test_a_grant_named_by_its_tenant_id_builds_its_principal_and_account_in_that_tenant(world):
    before = _rows(world)
    grant = AgentAccountAccessFactory(tenant_id="t1")
    assert _rows(world) - before == {
        _key(grant),
        ("Principal", ("t1", grant.principal_id)),
        ("Account", ("t1", grant.account_id)),
    }


#: A parent named by some of its keys: the factory can neither build it nor fill the rest.
PARTLY_NAMED = {
    "creative without creative_id": (
        lambda w: CreativeAssignmentFactory(tenant_id="t1", principal_id="p1", media_buy_id="mb1"),
        "creative_id",
    ),
    "media buy beside a creative named by id": (
        lambda w: CreativeAssignmentFactory(tenant_id="t1", creative_id="c1", principal_id="p1"),
        "media_buy_id",
    ),
    "package of a media buy named by id": (
        lambda w: CreativeAssignmentFactory(tenant_id="t1", creative_id="c1", principal_id="p1", media_buy_id="mb1"),
        "package_id",
    ),
    "product without tenant_id": (lambda w: PricingOptionFactory(product_id="prod1"), "tenant_id"),
}


@pytest.mark.parametrize(("call", "missing"), PARTLY_NAMED.values(), ids=PARTLY_NAMED.keys())
def test_a_partly_named_parent_is_refused(world, call, missing):
    before = _rows(world)
    with pytest.raises(TypeError, match=missing):
        call(world)
    assert _rows(world) == before


@pytest.mark.parametrize(
    "factory_class",
    [
        AccountFactory,
        AgentAccountAccessFactory,
        PrincipalFactory,
        GAMInventoryFactory,
        CreativeAssignmentFactory,
        PricingOptionFactory,
    ],
    ids=lambda factory_class: factory_class.__name__,
)
def test_a_parent_nobody_names_is_built(world, factory_class):
    before = _rows(world)
    row = factory_class()
    assert {key for key in _rows(world) - before if key[0] == "Tenant"} == {("Tenant", (row.tenant_id,))}


def test_a_parent_passed_as_an_object_supplies_its_ids(world):
    grant = AgentAccountAccessFactory(tenant=world.tenant, principal=world.principal, account=world.account)
    assignment = CreativeAssignmentFactory(creative=world.creative, media_buy=world.media_buy)
    assert (grant.tenant_id, grant.principal_id, grant.account_id) == ("t1", "p1", "a1")
    assert (
        assignment.tenant_id,
        assignment.creative_id,
        assignment.principal_id,
        assignment.media_buy_id,
        assignment.package_id,
    ) == ("t1", "c1", "p1", "mb1", "pkg_001")


def test_a_media_buy_adds_only_its_own_rows(world):
    """#2658's probe: the buy, its principal, that principal's grant on the account, and its package."""
    before = _rows(world)
    MediaBuyFactory(tenant=world.tenant, media_buy_id="mb2", principal__principal_id="p_probe")
    assert _rows(world) - before == {
        ("MediaBuy", ("mb2",)),
        ("Principal", ("t1", "p_probe")),
        ("AgentAccountAccess", ("t1", "p_probe", "acct_test")),
        ("MediaPackage", ("mb2", "pkg_001")),
    }
