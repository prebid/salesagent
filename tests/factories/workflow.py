"""Factories for the async-task tables: a principal's Context and its WorkflowSteps.

Ids come from uuid4 rather than a ``Sequence``: both tables are keyed globally, not per
tenant, and on e2e_rest every xdist worker writes into the one live-stack database, where
two workers' sequence counters would hand out the same key.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import factory
from factory import LazyAttribute, LazyFunction, SubFactory

from src.core.database.models import Context, WorkflowStep
from tests.factories.principal import PrincipalFactory


class ContextFactory(factory.alchemy.SQLAlchemyModelFactory):
    """A conversation context, owned by one principal: the row list_tasks scopes on."""

    class Meta:
        model = Context
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"
        exclude = ("principal",)

    principal = SubFactory(PrincipalFactory)

    context_id = LazyFunction(lambda: f"ctx_{uuid4().hex[:12]}")
    tenant_id = LazyAttribute(lambda o: o.principal.tenant_id)
    principal_id = LazyAttribute(lambda o: o.principal.principal_id)
    conversation_history = LazyFunction(list)
    last_activity_at = LazyFunction(lambda: datetime.now(UTC))


class WorkflowStepFactory(factory.alchemy.SQLAlchemyModelFactory):
    """One workflow step: what list_tasks reports as a task.

    ``created_at`` is set here rather than left to the column's ``now()``, which is the
    TRANSACTION start time: steps written in one transaction would otherwise all share it,
    and an ordering over them would grade nothing.
    """

    class Meta:
        model = WorkflowStep
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"
        exclude = ("context",)

    context = SubFactory(ContextFactory)

    step_id = LazyFunction(lambda: f"step_{uuid4().hex[:12]}")
    context_id = LazyAttribute(lambda o: o.context.context_id)
    step_type = "tool_call"
    owner = "principal"
    status = "pending"
    tool_name = "create_media_buy"
    created_at = LazyFunction(lambda: datetime.now(UTC))
    completed_at = None
    comments = LazyFunction(list)
