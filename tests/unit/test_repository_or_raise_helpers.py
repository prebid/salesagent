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

    def test_get_by_id_or_raise_echoes_context_into_envelope(self):
        """context= is carried onto the raised error AND echoed into the wire envelope.

        Not just accepted: a regression that takes ``context=`` and drops it would
        still satisfy a signature-only test. Assert the value lands on the exception
        and survives into the two-layer envelope (assert_envelope_shape does not
        check context, so we assert envelope["context"] directly).
        """
        from src.core.exceptions import build_two_layer_error_envelope

        repo = _repo_with_first(MediaBuyRepository, None)
        ctx = {"context_id": "ctx-9"}
        with pytest.raises(AdCPMediaBuyNotFoundError) as exc:
            repo.get_by_id_or_raise("mb-missing", context=ctx)

        assert exc.value.context == ctx
        assert build_two_layer_error_envelope(exc.value)["context"] == ctx

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

    def test_get_package_materializes_row_from_raw_request(self):
        """A package recorded only in MediaBuy.raw_request (pre-dual-write buy,
        or an adapter that returned empty response.packages) is materialized
        into a canonical row so EVERY caller — existence guard, creative
        assignment, targeting mutation — behaves identically on legacy buys."""
        raw_pkg = {"package_id": "pkg-raw-only", "impressions": 100000}
        media_buy = MagicMock()
        media_buy.raw_request = {"packages": [raw_pkg]}
        session = MagicMock()
        # First lookup: no MediaPackage row; second lookup: the owning MediaBuy.
        session.scalars.return_value.first.side_effect = [None, media_buy]
        repo = MediaBuyRepository(session, "tenant-1")

        package = repo.get_package("mb-1", "pkg-raw-only")

        assert package is not None
        assert package.package_id == "pkg-raw-only"
        assert package.package_config == raw_pkg
        # Added to the session (UoW owns the commit), flushed for immediate use
        session.add.assert_called_once_with(package)
        session.flush.assert_called_once_with()

    def test_get_package_or_raise_inherits_raw_request_fallback(self):
        media_buy = MagicMock()
        media_buy.raw_request = {"packages": [{"package_id": "pkg-raw-only"}]}
        session = MagicMock()
        session.scalars.return_value.first.side_effect = [None, media_buy]
        repo = MediaBuyRepository(session, "tenant-1")
        assert repo.get_package_or_raise("mb-1", "pkg-raw-only") is not None  # no raise

    def test_get_package_or_raise_raises_when_absent_everywhere(self):
        from adcp import ErrorCode

        media_buy = MagicMock()
        media_buy.raw_request = {"packages": [{"package_id": "pkg-other"}]}
        session = MagicMock()
        session.scalars.return_value.first.side_effect = [None, media_buy]
        repo = MediaBuyRepository(session, "tenant-1")
        with pytest.raises(AdCPPackageNotFoundError) as exc:
            repo.get_package_or_raise("mb-1", "pkg-missing", context={"context_id": "ctx-7"})
        # SDK enum, .value at the comparison boundary (plain Enum, not StrEnum)
        assert exc.value.error_code == ErrorCode.PACKAGE_NOT_FOUND.value
        assert "pkg-missing" in str(exc.value)
        assert exc.value.context == {"context_id": "ctx-7"}


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
