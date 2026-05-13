"""Pin :class:`SalesAgentProposalStore` class-level attributes against
silent regressions.

These tests are pure attribute checks — no DB needed. The full
behavioral suite lives in
``tests/integration/test_proposal_store.py`` (real Postgres).
"""

from __future__ import annotations

from src.core.database.repositories import SalesAgentProposalStore


def test_durable_flag_is_true():
    """``InMemoryProposalStore.is_durable == False`` triggers a
    production-mode warning from the framework. Our Postgres-backed
    store must declare ``True`` so the warning doesn't fire on
    production deploys — and so the framework's production gate
    doesn't fail closed."""
    assert SalesAgentProposalStore.is_durable is True
