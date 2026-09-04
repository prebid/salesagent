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


def _workflow_repo_with_execute_row(first_value):
    """Build WorkflowRepository whose ``session.execute(...).first()`` returns ``first_value``.

    ``get_by_step_id_or_raise`` uses ``execute`` (resolve-then-authorize two-step)
    rather than ``scalars``.
    """
    session = MagicMock()
    session.execute.return_value.first.return_value = first_value
    return WorkflowRepository(session, "tenant-1")


def _compiled_last_select(session: MagicMock) -> str:
    """Compile the most recent ``session.scalars(...)`` SELECT with literal binds."""
    return str(session.scalars.call_args[0][0].compile(compile_kwargs={"literal_binds": True}))


def _compiled_last_execute(session: MagicMock) -> str:
    """Compile the most recent ``session.execute(...)`` SELECT with literal binds."""
    return str(session.execute.call_args[0][0].compile(compile_kwargs={"literal_binds": True}))


def _expected_scoped_clause(step_id: str, tenant_id: str, principal_id: str) -> str:
    """Expected FROM..WHERE tail for a step_id/tenant/principal-scoped SELECT.

    Includes the JOIN (not just the WHERE tail): ``select(...).join(DBContext).where(*c)``
    and ``select(...).where(*c)`` (a cartesian product that re-leaks across tenants)
    produce byte-identical WHERE tails, so a WHERE-only slice cannot see a dropped
    ``.join(DBContext)``.
    """
    return (
        "workflow_steps JOIN contexts ON contexts.context_id = workflow_steps.context_id \n"
        f"WHERE workflow_steps.step_id = '{step_id}' AND contexts.tenant_id = '{tenant_id}' "
        f"AND contexts.principal_id = '{principal_id}'"
    )


def _expected_tenant_resolve_clause(step_id: str, tenant_id: str) -> str:
    """Expected FROM..WHERE for resolve-then-authorize step 1 (no principal SQL)."""
    return (
        "workflow_steps JOIN contexts ON contexts.context_id = workflow_steps.context_id \n"
        f"WHERE workflow_steps.step_id = '{step_id}' AND contexts.tenant_id = '{tenant_id}'"
    )


def _from_tail(compiled: str) -> str:
    return compiled.split("FROM", 1)[1].strip()


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


class TestWorkflowOrRaise:
    def test_get_by_step_id_or_raise_returns_when_present(self):
        step = MagicMock()
        repo = _workflow_repo_with_execute_row((step, "principal-a"))
        assert repo.get_by_step_id_or_raise("step-1", principal_id="principal-a") is step

    def test_get_by_step_id_or_raise_raises_when_absent(self):
        repo = _workflow_repo_with_execute_row(None)
        with pytest.raises(AdCPTaskNotFoundError) as exc:
            repo.get_by_step_id_or_raise("step-missing", principal_id="principal-a")
        assert exc.value.error_code == "TASK_NOT_FOUND"
        assert str(exc.value) == "Reference not found"

    def test_get_by_step_id_or_raise_resolve_then_authorize(self):
        """Resolve is tenant-only SQL; authorize compares principal in Python.

        Sibling ownership (row exists, wrong principal) must raise the same
        AdCPTaskNotFoundError without a principal predicate in the SELECT.
        """
        step = MagicMock()
        repo = _workflow_repo_with_execute_row((step, "owner-a"))
        with pytest.raises(AdCPTaskNotFoundError):
            repo.get_by_step_id_or_raise("step-1", principal_id="sibling-b")
        compiled = _compiled_last_execute(repo._session)
        assert _from_tail(compiled) == _expected_tenant_resolve_clause("step-1", "tenant-1")
        where_tail = _from_tail(compiled).split("WHERE", 1)[1]
        assert "principal_id" not in where_tail

    def test_get_by_step_id_or_raise_rejects_falsy_principal_id(self):
        """Explicit None/empty must not silently tenant-scope via get_by_step_id."""
        repo = _workflow_repo_with_execute_row((MagicMock(), "p"))
        with pytest.raises(ValueError, match="Owner scope is required"):
            repo.get_by_step_id_or_raise("step-1", principal_id=None)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="Owner scope is required"):
            repo.get_by_step_id_or_raise("step-1", principal_id="")
        repo._session.execute.assert_not_called()

    def test_get_by_step_id_filters_principal_in_sql(self):
        """Principal filter is applied in the WHERE clause (not post-fetch)."""
        repo = _repo_with_first(WorkflowRepository, None)
        repo.get_by_step_id("step-1", principal_id="principal-a")
        compiled = _compiled_last_select(repo._session)
        assert _from_tail(compiled) == _expected_scoped_clause("step-1", "tenant-1", "principal-a")

    def test_get_by_step_id_rejects_empty_principal_id(self):
        """Explicit "" must raise — not silently widen to tenant-only (Chris R4 NIT)."""
        repo = _repo_with_first(WorkflowRepository, None)
        with pytest.raises(ValueError, match="Owner scope is required"):
            repo.get_by_step_id("step-1", principal_id="")
        repo._session.scalars.assert_not_called()

    def test_update_status_sibling_principal_returns_none(self):
        """Write-side ownership: sibling principal_id must not update the row.

        Grades the SQL seam (same scoped WHERE as read), not a mocked kwarg
        forward. Reverting update_status to tenant-only get_by_step_id must fail.
        """
        repo = _repo_with_first(WorkflowRepository, None)
        assert repo.update_status("step-1", status="completed", principal_id="sibling-b") is None
        compiled = _compiled_last_select(repo._session)
        assert _from_tail(compiled) == _expected_scoped_clause("step-1", "tenant-1", "sibling-b")

    def test_update_status_owner_principal_updates(self):
        step = MagicMock()
        repo = _repo_with_first(WorkflowRepository, step)
        assert repo.update_status("step-1", status="completed", principal_id="owner-a") is step
        assert step.status == "completed"
        repo._session.flush.assert_called_once_with()
        compiled = _compiled_last_select(repo._session)
        assert _from_tail(compiled) == _expected_scoped_clause("step-1", "tenant-1", "owner-a")

    def test_update_status_rejects_empty_principal_id(self):
        """Write gate: empty-string principal_id raises like the read gate."""
        repo = _repo_with_first(WorkflowRepository, MagicMock())
        with pytest.raises(ValueError, match="Owner scope is required"):
            repo.update_status("step-1", status="completed", principal_id="")
        repo._session.scalars.assert_not_called()

    def test_get_by_step_id_or_raise_default_message_from_spec_supplement(self):
        """Argument-less raise must still emit the REFERENCE_NOT_FOUND uniform message."""
        from src.core.exceptions import REFERENCE_NOT_FOUND_MESSAGE

        repo = _workflow_repo_with_execute_row(None)
        with pytest.raises(AdCPTaskNotFoundError) as exc:
            repo.get_by_step_id_or_raise("step-missing", principal_id="principal-a")
        # Literal sibling grades the constant; this pins the raise uses it.
        assert str(exc.value) == "Reference not found"
        assert str(exc.value) == REFERENCE_NOT_FOUND_MESSAGE
