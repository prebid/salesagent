"""Tenant-scoped access to the operator-configured agent rows.

``creative_agents`` and ``signals_agents`` are the same shape of thing: rows an
operator registers per tenant, each with an ``enabled`` flag, read by a registry that
then calls out to them. Three sites asked the same question of them —
"the enabled agents of this tenant" — each with its own ``select()``, each importing
the model under an ALIAS (``CreativeAgent as CreativeAgentModel``), which is precisely
what the raw-select guard keys on model class names and therefore could not see.

The fix per CLAUDE.md pattern 3 is the repository, not a cleverer guard: with the query
here there is one tenant predicate rather than three remembered ones, and the aliased
import has nowhere left to live.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import CreativeAgent, SignalsAgent


class CreativeAgentRepository:
    """Tenant-scoped access for ``creative_agents``.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def get_enabled(self) -> list[CreativeAgent]:
        """This tenant's enabled creative agents, highest priority (lowest number) first.

        Ordered here rather than by each caller: the registry and the admin format-search
        endpoint both present agents in priority order, and one of them was sorting in
        Python after the fact.
        """
        return list(
            self._session.scalars(
                select(CreativeAgent)
                .where(CreativeAgent.tenant_id == self._tenant_id, CreativeAgent.enabled.is_(True))
                .order_by(CreativeAgent.priority)
            ).all()
        )


class SignalsAgentRepository:
    """Tenant-scoped access for ``signals_agents``.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def get_enabled(self) -> list[SignalsAgent]:
        """This tenant's enabled signals agents, by name.

        Signals agents carry no priority column — the registry sorted them by name, so
        that ordering is what the query states.
        """
        return list(
            self._session.scalars(
                select(SignalsAgent)
                .where(SignalsAgent.tenant_id == self._tenant_id, SignalsAgent.enabled.is_(True))
                .order_by(SignalsAgent.name)
            ).all()
        )
