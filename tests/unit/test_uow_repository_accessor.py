"""The UoW repository accessor's contract.

``BaseUoW`` subclasses used to type their repositories ``Repository | None``,
which pushed a fact the UoW already knows — repositories exist for exactly the
lifetime of the ``with`` block — onto every call site.  Those sites then invented
their own narrowing, in two spellings: a bare ``assert`` (stripped by
``python -O``; raises ``AssertionError`` → an un-enveloped 500 on a protocol
surface) and a typed ``raise``.  One feature grew sixteen of them (#1197 review).

``RepositoryAccessor`` answers it once.  These tests pin both halves of that
answer, because both are load-bearing: inside the block callers get the concrete
repository with no guard, and outside it they get the typed AdCP error the
surfaces would have raised by hand.
"""

from __future__ import annotations

import pytest

from src.core.database.repositories.uow import TenantConfigUoW, TMPProviderUoW
from src.core.exceptions import AdCPServiceUnavailableError


class TestOutsideAnOpenSession:
    """Reading a repository outside the ``with`` block raises the typed error."""

    @pytest.mark.parametrize("repo_name", ["tmp_providers", "tenant_config"])
    def test_tmp_provider_uow_raises_service_unavailable(self, repo_name):
        uow = TMPProviderUoW("tenant_x")

        with pytest.raises(AdCPServiceUnavailableError) as exc_info:
            getattr(uow, repo_name)

        # The message names the repository, so an operator reading the envelope
        # knows which session failed to open.
        assert repo_name in str(exc_info.value)

    def test_tenant_config_uow_raises_service_unavailable(self):
        uow = TenantConfigUoW("tenant_x")

        with pytest.raises(AdCPServiceUnavailableError):
            _ = uow.tenant_config

    def test_error_carries_a_retry_suggestion(self):
        """Every AdCP envelope this produces has a next step for the caller."""
        uow = TMPProviderUoW("tenant_x")

        with pytest.raises(AdCPServiceUnavailableError) as exc_info:
            _ = uow.tmp_providers

        assert "Retry shortly" in (exc_info.value.suggestion or "")

    def test_raises_again_after_the_block_has_exited(self, monkeypatch):
        """_clear_repos restores the closed state — the accessor is not one-shot."""
        uow = TMPProviderUoW("tenant_x")
        uow.tmp_providers = object()  # simulate _init_repos
        uow._clear_repos()

        with pytest.raises(AdCPServiceUnavailableError):
            _ = uow.tmp_providers


class TestInsideAnOpenSession:
    """Inside the block the accessor hands back the concrete repository.

    The real-session counterpart — that an open UoW yields the concrete
    repository TYPES — lives in ``tests/integration/test_tmp_provider_repository.py``.
    It needs Postgres, and the tier boundary decides placement: it was the only
    ``requires_db`` test under ``tests/unit/``, so the Unit Tests job (which runs
    without a database service) errored on it (#1197 review).
    """

    def test_returns_the_repository_with_no_narrowing(self):
        uow = TMPProviderUoW("tenant_x")
        sentinel = object()
        uow.tmp_providers = sentinel

        # No `assert ... is not None`, no `if ... is None: raise` — the read is
        # the whole call site.
        assert uow.tmp_providers is sentinel
