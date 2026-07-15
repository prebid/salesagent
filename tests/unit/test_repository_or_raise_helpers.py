"""Repository ``*_or_raise`` helpers: real fetch-and-raise semantics.

These exercise the actual helper logic (the plain getter + the typed not-found
raise) against a mocked SQLAlchemy session — no DB required. They back the
tool-level tests, which mock the helpers, with a test of the real behavior:
that the helper returns the entity when present and raises the correct typed
``AdCPNotFoundError`` subclass (with the id in the message) when absent.
"""

from decimal import Decimal
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


def _repo_with_raw_packages(raw_packages: list[dict]) -> tuple[MediaBuyRepository, MagicMock]:
    """Build a repo whose row lookup misses, then resolves the owning MediaBuy.

    Encodes the repository's internal query ORDER once: ``get_package`` /
    ``_find_raw_package`` first query the ``media_packages`` row (miss → None),
    then load the ``MediaBuy`` to read ``raw_request`` — hence
    ``side_effect = [None, media_buy]``. Returns ``(repo, session)`` so callers
    can assert on ``session.add`` / ``session.flush``.
    """
    media_buy = MagicMock()
    media_buy.raw_request = {"packages": raw_packages}
    session = MagicMock()
    session.scalars.return_value.first.side_effect = [None, media_buy]
    return MediaBuyRepository(session, "tenant-1"), session


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

    def test_get_package_is_a_pure_read(self):
        """``get_package`` must never write: a raw_request-only package returns
        ``None`` (no lazy materialization). This is the no-write oracle for
        the pre-dry_run guard family — the end-to-end dry_run pin in
        test_update_media_buy_package_guard.py cannot catch a writing reader
        by itself (see its module docstring), this assertion can."""
        repo, session = _repo_with_raw_packages([{"package_id": "pkg-raw-only"}])

        assert repo.get_package("mb-1", "pkg-raw-only") is None
        session.add.assert_not_called()
        session.flush.assert_not_called()

    def test_package_exists_or_raise_tolerates_raw_only_without_writing(self):
        """The pre-dry_run existence guard: raw_request-only package passes
        (no raise) AND nothing is written to the session."""
        repo, session = _repo_with_raw_packages([{"package_id": "pkg-raw-only"}])

        repo.package_exists_or_raise("mb-1", "pkg-raw-only")  # no raise

        session.add.assert_not_called()
        session.flush.assert_not_called()

    def test_package_exists_or_raise_raises_when_absent_everywhere(self):
        repo, session = _repo_with_raw_packages([{"package_id": "pkg-other"}])
        with pytest.raises(AdCPPackageNotFoundError) as exc:
            repo.package_exists_or_raise("mb-1", "pkg-missing", context={"context_id": "ctx-3"})
        assert exc.value.error_code == "PACKAGE_NOT_FOUND"
        assert exc.value.context == {"context_id": "ctx-3"}

    def test_get_package_config_reads_raw_request_without_writing(self):
        raw_pkg = {"package_id": "pkg-raw-only", "product_id": "prod-7"}
        repo, session = _repo_with_raw_packages([raw_pkg])

        assert repo.get_package_config("mb-1", "pkg-raw-only") == raw_pkg
        session.add.assert_not_called()
        session.flush.assert_not_called()  # a stray flush is a write under the UoW

    def test_materialize_package_creates_row_from_raw_request(self):
        """A package recorded only in MediaBuy.raw_request (pre-dual-write buy,
        or an adapter that returned empty response.packages) is materialized
        into a canonical row — including the dedicated columns the create
        path's dual-write populates, not just package_config."""
        raw_pkg = {
            "package_id": "pkg-raw-only",
            "budget": {"total": 5000, "pacing": "even"},
            "bid_price": 12.5,
        }
        repo, session = _repo_with_raw_packages([raw_pkg])

        package = repo.materialize_package("mb-1", "pkg-raw-only")

        assert package is not None
        assert package.package_id == "pkg-raw-only"
        assert package.package_config == raw_pkg
        assert package.budget == Decimal("5000")
        assert package.bid_price == Decimal("12.5")
        assert package.pacing == "even"
        # Added to the session (UoW owns the commit), flushed for immediate use
        session.add.assert_called_once_with(package)
        session.flush.assert_called_once_with()

    def test_get_package_or_raise_materializes_raw_request_fallback(self):
        repo, session = _repo_with_raw_packages([{"package_id": "pkg-raw-only"}])
        package = repo.get_package_or_raise("mb-1", "pkg-raw-only")  # no raise
        assert package is not None
        session.add.assert_called_once_with(package)

    def test_get_package_or_raise_raises_when_absent_everywhere(self):
        repo, session = _repo_with_raw_packages([{"package_id": "pkg-other"}])
        with pytest.raises(AdCPPackageNotFoundError) as exc:
            repo.get_package_or_raise("mb-1", "pkg-missing", context={"context_id": "ctx-7"})
        # String literal per local convention; test_error_code_sdk_conformance
        # pins every spec-claimed _default_error_code against the SDK enum.
        assert exc.value.error_code == "PACKAGE_NOT_FOUND"
        assert "pkg-missing" in str(exc.value)
        assert exc.value.context == {"context_id": "ctx-7"}


class TestBuildPackageRowNumericCoercion:
    """_build_package_row must tolerate the untrusted legacy raw_request numerics
    it exists to rescue — never 500 on a malformed value, never read a bool as 1/0."""

    def test_to_decimal_or_none_rejects_bool_and_malformed(self):
        from src.core.database.repositories.media_buy import _to_decimal_or_none

        assert _to_decimal_or_none(True) is None  # bool is an int subtype — not 1
        assert _to_decimal_or_none(False) is None
        assert _to_decimal_or_none("not-a-number") is None  # would 500 via Decimal(str(...))
        assert _to_decimal_or_none(object()) is None
        assert _to_decimal_or_none(None) is None

    def test_to_decimal_or_none_coerces_valid(self):
        from src.core.database.repositories.media_buy import _to_decimal_or_none

        assert _to_decimal_or_none(5000) == Decimal("5000")
        assert _to_decimal_or_none(12.5) == Decimal("12.5")
        assert _to_decimal_or_none("7.25") == Decimal("7.25")

    def test_materialize_tolerates_malformed_legacy_budget(self):
        # A pre-dual-write buy with a garbage scalar budget / bool bid_price must
        # materialize to None columns, not raise InvalidOperation (a 500).
        repo, session = _repo_with_raw_packages(
            [{"package_id": "pkg-bad", "budget": "garbage", "bid_price": True}]
        )
        package = repo.materialize_package("mb-1", "pkg-bad")
        assert package is not None
        assert package.budget is None
        assert package.bid_price is None


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
