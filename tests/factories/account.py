"""Factory_boy factories for Account and AgentAccountAccess models.

Also carries the Pydantic ``BusinessEntity`` factory used to build the
``accounts[].billing_entity`` request payload (same split as
``tests/factories/format.py``: ORM factories persist, Pydantic factories
``.build()`` request objects).
"""

from __future__ import annotations

from typing import Any

import factory
from adcp.types.generated_poc.core.business_entity import (  # TODO: no stable alias in adcp.types
    Address,
    Bank,
    BusinessEntity,
)
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import Account, AgentAccountAccess
from tests.factories.core import TenantFactory


class AccountFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = Account
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"
        #: ``tenant`` exists only to derive ``tenant_id``; it must never reach
        #: ``Account(...)``. ``Account.tenant`` is a real relationship, so passing
        #: the SubFactory's throwaway Tenant makes SQLAlchemy re-sync ``tenant_id``
        #: FROM it at flush time — silently relocating the row to that tenant and
        #: leaving an explicit ``tenant_id=`` argument with no effect. Declared on
        #: Meta (as AgentAccountAccessFactory already does) so it holds for BOTH
        #: the create and build strategies; a ``_create``-only override left
        #: ``.build()`` carrying the trap.
        exclude = ["tenant"]

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    account_id = Sequence(lambda n: f"acc_{n:04d}")
    name = LazyAttribute(lambda o: f"Test Account {o.account_id}")
    status = "active"


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


class AddressFactory(factory.Factory):
    """core/business-entity.json #/properties/address — all four fields required."""

    class Meta:
        model = Address

    street = "Maximilianstrasse 13"
    city = "Munich"
    postal_code = "80539"
    country = "DE"


class BankFactory(factory.Factory):
    """core/business-entity.json #/properties/bank — WRITE-ONLY.

    A response that echoes any of this back is a leak: the sync-accounts-response
    account item documents ``billing_entity`` as "echoed from the request. ... Bank
    details are omitted (write-only)".
    """

    class Meta:
        model = Bank

    account_holder = "Acme GmbH"
    iban = "DE89370400440532013000"
    bic = "COBADEFFXXX"


class BusinessEntityFactory(factory.Factory):
    """Pydantic ``BusinessEntity`` for ``sync_accounts`` ``accounts[].billing_entity``.

    Defaults mirror the spec's own DACH B2B provisioning example (legal entity +
    address + VAT/tax ids + bank), so a scenario only overrides what it grades.
    Build the request payload with ``.build()``; it is a request model, not a row.
    """

    class Meta:
        model = BusinessEntity

    legal_name = "Acme GmbH"
    vat_id = "DE123456789"
    tax_id = "DE123456789"
    address = factory.SubFactory(AddressFactory)
    bank = factory.SubFactory(BankFactory)

    @classmethod
    def build_payload(cls, **kwargs: Any) -> dict[str, Any]:
        """Return the JSON-mode dict a request entry carries (no ``None`` padding)."""
        return cls.build(**kwargs).model_dump(mode="json", exclude_none=True)
