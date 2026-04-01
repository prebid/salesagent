"""Factory_boy factories for Account and AgentAccountAccess models."""

from __future__ import annotations

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import Account, AgentAccountAccess
from tests.factories.core import TenantFactory


class AccountFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = Account
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    account_id = Sequence(lambda n: f"acc_{n:04d}")
    name = LazyAttribute(lambda o: f"Test Account {o.account_id}")
    status = "active"

    class Params:
        """Exclude tenant from model construction (it's only for deriving tenant_id)."""

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        kwargs.pop("tenant", None)
        return super()._create(model_class, *args, **kwargs)


class AgentAccountAccessFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = AgentAccountAccess
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"
        exclude = ["tenant", "principal", "account"]

    tenant = SubFactory(TenantFactory)
    principal = SubFactory("tests.factories.principal.PrincipalFactory", tenant=factory.SelfAttribute("..tenant"))
    account = SubFactory(AccountFactory, tenant=factory.SelfAttribute("..tenant"))

    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    principal_id = LazyAttribute(lambda o: o.principal.principal_id)
    account_id = LazyAttribute(lambda o: o.account.account_id)
