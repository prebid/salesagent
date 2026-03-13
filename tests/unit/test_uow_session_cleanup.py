"""Regression tests for UoW session cleanup on commit failure.

beads-0a2: BaseUoW.__exit__ leaks session on commit failure.
If session.commit() raises in __exit__, the get_db_session() context manager
is never exited, leaking the session and connection.

The fix is to wrap cleanup in try/finally so it always runs.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError


class TestMediaBuyUoWCommitFailureCleanup:
    """MediaBuyUoW must clean up session even when commit() raises."""

    def test_session_cm_exit_called_on_commit_failure(self):
        """When commit() raises, get_db_session()'s __exit__ must still be called.

        Reproduces beads-0a2: if self.session.commit() raises in __exit__,
        self._session_cm.__exit__() is never reached, leaking the session.
        """
        from src.core.database.repositories.uow import MediaBuyUoW

        mock_session = MagicMock()
        mock_session.commit.side_effect = IntegrityError(
            "duplicate key", params=None, orig=Exception("constraint violation")
        )

        # Track whether the session CM's __exit__ was called
        cm_exit_called = False

        @contextmanager
        def fake_get_db_session():
            nonlocal cm_exit_called
            yield mock_session
            # If we reach here, __exit__ was properly called
            cm_exit_called = True

        with patch(
            "src.core.database.repositories.uow.get_db_session",
            side_effect=fake_get_db_session,
        ):
            with pytest.raises(IntegrityError):
                with MediaBuyUoW("test_tenant") as uow:
                    # Simulate clean exit (no exception in with block)
                    # but commit will fail in __exit__
                    pass

        # THIS IS THE BUG: cm_exit_called is False because commit() raised
        # and __exit__ never reached self._session_cm.__exit__()
        assert cm_exit_called, (
            "get_db_session()'s context manager __exit__ was never called — session leaked after commit failure"
        )

    def test_session_ref_cleared_on_commit_failure(self):
        """UoW.session must be None after commit failure (no stale reference)."""
        from src.core.database.repositories.uow import MediaBuyUoW

        mock_session = MagicMock()
        mock_session.commit.side_effect = IntegrityError(
            "duplicate key", params=None, orig=Exception("constraint violation")
        )

        @contextmanager
        def fake_get_db_session():
            yield mock_session

        with patch(
            "src.core.database.repositories.uow.get_db_session",
            side_effect=fake_get_db_session,
        ):
            uow = MediaBuyUoW("test_tenant")
            with pytest.raises(IntegrityError):
                with uow:
                    pass

        # After commit failure, session should still be cleaned up
        assert uow.session is None, "UoW.session not cleared after commit failure"
        assert uow.media_buys is None, "UoW.media_buys not cleared after commit failure"


class TestProductUoWCommitFailureCleanup:
    """ProductUoW must clean up session even when commit() raises."""

    def test_session_cm_exit_called_on_commit_failure(self):
        """When commit() raises, get_db_session()'s __exit__ must still be called."""
        from src.core.database.repositories.uow import ProductUoW

        mock_session = MagicMock()
        mock_session.commit.side_effect = IntegrityError(
            "duplicate key", params=None, orig=Exception("constraint violation")
        )

        cm_exit_called = False

        @contextmanager
        def fake_get_db_session():
            nonlocal cm_exit_called
            yield mock_session
            cm_exit_called = True

        with patch(
            "src.core.database.repositories.uow.get_db_session",
            side_effect=fake_get_db_session,
        ):
            with pytest.raises(IntegrityError):
                with ProductUoW("test_tenant") as uow:
                    pass

        assert cm_exit_called, (
            "get_db_session()'s context manager __exit__ was never called — session leaked after commit failure"
        )

    def test_repos_cleared_on_commit_failure(self):
        """UoW.products must be None after commit failure."""
        from src.core.database.repositories.uow import ProductUoW

        mock_session = MagicMock()
        mock_session.commit.side_effect = IntegrityError(
            "duplicate key", params=None, orig=Exception("constraint violation")
        )

        @contextmanager
        def fake_get_db_session():
            yield mock_session

        with patch(
            "src.core.database.repositories.uow.get_db_session",
            side_effect=fake_get_db_session,
        ):
            uow = ProductUoW("test_tenant")
            with pytest.raises(IntegrityError):
                with uow:
                    pass

        assert uow.session is None, "UoW.session not cleared after commit failure"
        assert uow.products is None, "UoW.products not cleared after commit failure"
