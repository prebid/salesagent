"""Repository ``*_or_raise`` helpers: real fetch-and-raise semantics.

These exercise the actual helper logic (the plain getter + the typed not-found
raise) against a mocked SQLAlchemy session — no DB required. They back the
tool-level tests, which mock the helpers, with a test of the real behavior:
that the helper returns the entity when present and raises the correct typed
``AdCPNotFoundError`` subclass (with the id in the message) when absent.
"""

from unittest.mock import MagicMock

import pytest

from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.database.repositories.workflow import WorkflowRepository
from src.core.exceptions import (
    AdCPMediaBuyNotFoundError,
    AdCPPackageNotFoundError,
    AdCPTaskNotFoundError,
)


def _repo_with_first(repo_cls, first_value):
    """Build a repository whose ``session.scalars(...).first()`` returns ``first_value``."""
    session = MagicMock()
    session.scalars.return_value.first.return_value = first_value
    return repo_cls(session, "tenant-1")


class TestMediaBuyOrRaise:
    def test_get_by_id_or_raise_returns_when_present(self):
        media_buy = MagicMock()
        repo = _repo_with_first(MediaBuyRepository, media_buy)
        assert repo.get_by_id_or_raise("mb-1") is media_buy

    def test_get_by_id_or_raise_raises_when_absent(self):
        repo = _repo_with_first(MediaBuyRepository, None)
        with pytest.raises(AdCPMediaBuyNotFoundError) as exc:
            repo.get_by_id_or_raise("mb-missing")
        assert exc.value.error_code == "MEDIA_BUY_NOT_FOUND"
        assert "mb-missing" in str(exc.value)

    def test_get_by_id_or_raise_accepts_context(self):
        """context= is plumbed through to the raised error (echoed into the envelope)."""
        repo = _repo_with_first(MediaBuyRepository, None)
        with pytest.raises(AdCPMediaBuyNotFoundError):
            repo.get_by_id_or_raise("mb-missing", context={"context_id": "ctx-9"})

    def test_get_package_or_raise_returns_when_present(self):
        package = MagicMock()
        repo = _repo_with_first(MediaBuyRepository, package)
        assert repo.get_package_or_raise("mb-1", "pkg-1") is package

    def test_get_package_or_raise_raises_when_absent(self):
        repo = _repo_with_first(MediaBuyRepository, None)
        with pytest.raises(AdCPPackageNotFoundError) as exc:
            repo.get_package_or_raise("mb-1", "pkg-missing")
        assert exc.value.error_code == "PACKAGE_NOT_FOUND"
        assert "pkg-missing" in str(exc.value)


class TestWorkflowOrRaise:
    def test_get_by_step_id_or_raise_returns_when_present(self):
        step = MagicMock()
        repo = _repo_with_first(WorkflowRepository, step)
        assert repo.get_by_step_id_or_raise("step-1") is step

    def test_get_by_step_id_or_raise_raises_when_absent(self):
        repo = _repo_with_first(WorkflowRepository, None)
        with pytest.raises(AdCPTaskNotFoundError) as exc:
            repo.get_by_step_id_or_raise("step-missing")
        assert exc.value.error_code == "TASK_NOT_FOUND"
        assert "step-missing" in str(exc.value)
