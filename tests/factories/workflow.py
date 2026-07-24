"""Factory_boy factories for Context and WorkflowStep models."""

from __future__ import annotations

from datetime import UTC, datetime

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import Context, WorkflowStep
from tests.factories.core import TenantFactory
from tests.factories.principal import PrincipalFactory


class ContextFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = Context
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    principal = SubFactory(PrincipalFactory, tenant=factory.SelfAttribute("..tenant"))

    context_id = Sequence(lambda n: f"ctx_{n:08x}")
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    principal_id = LazyAttribute(lambda o: o.principal.principal_id)
    conversation_history = factory.LazyFunction(list)
    created_at = factory.LazyFunction(lambda: datetime.now(UTC))
    last_activity_at = factory.LazyFunction(lambda: datetime.now(UTC))


class WorkflowStepFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = WorkflowStep
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    context = SubFactory(ContextFactory)
    step_id = Sequence(lambda n: f"step_{n:08x}")
    context_id = LazyAttribute(lambda o: o.context.context_id)
    step_type = "approval"
    tool_name = "create_media_buy"
    request_data = factory.LazyFunction(dict)
    response_data = None
    status = "pending_approval"
    owner = "principal"
    created_at = factory.LazyFunction(lambda: datetime.now(UTC))
