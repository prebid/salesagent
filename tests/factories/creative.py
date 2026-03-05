"""Factory-boy factories for Creative and CreativeAssignment ORM models."""

from __future__ import annotations

import factory
from factory.alchemy import SQLAlchemyModelFactory

from src.core.database.models import Creative, CreativeAssignment

from .core import TenantFactory
from .media_buy import MediaBuyFactory
from .principal import PrincipalFactory


class CreativeFactory(SQLAlchemyModelFactory):
    class Meta:
        model = Creative
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    # Composite PK fields
    creative_id = factory.Sequence(lambda n: f"creative_{n:04d}")
    tenant = factory.SubFactory(TenantFactory)
    tenant_id = factory.LazyAttribute(lambda o: o.tenant.tenant_id)
    principal = factory.SubFactory(PrincipalFactory, tenant=factory.SelfAttribute("..tenant"))
    principal_id = factory.LazyAttribute(lambda o: o.principal.principal_id)

    # Required fields
    name = factory.LazyAttribute(lambda o: f"Test Creative {o.creative_id}")
    agent_url = "https://creative.adcontextprotocol.org"
    format = "display_300x250"
    status = "pending"
    data = factory.LazyFunction(dict)

    class Params:
        """Traits for common creative configurations."""

        approved = factory.Trait(status="approved")


class CreativeAssignmentFactory(SQLAlchemyModelFactory):
    class Meta:
        model = CreativeAssignment
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    assignment_id = factory.Sequence(lambda n: f"assignment_{n:04d}")
    creative = factory.SubFactory(CreativeFactory)
    tenant_id = factory.LazyAttribute(lambda o: o.creative.tenant_id)
    creative_id = factory.LazyAttribute(lambda o: o.creative.creative_id)
    principal_id = factory.LazyAttribute(lambda o: o.creative.principal_id)
    media_buy = factory.SubFactory(MediaBuyFactory, tenant=factory.SelfAttribute("..creative.tenant"))
    media_buy_id = factory.LazyAttribute(lambda o: o.media_buy.media_buy_id)
    package_id = factory.Sequence(lambda n: f"pkg_{n:04d}")
    weight = 100
