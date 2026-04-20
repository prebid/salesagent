"""L0-12 — audit dep obligation tests (Pattern a: stub-first + semantic).

Red state: ``audit_action`` dep body is a no-op (no BackgroundTasks schedule);
``_write_audit`` swallows but records nothing. Semantic tests assert the
BackgroundTasks scheduling + request-metadata capture obligations defined in
foundation-modules.md §11.5.

Green: real impl wires background.add_task with action / user_email /
tenant_id / path / method.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _request(path: str = "/tenant/t1/users", method: str = "POST", tenant_id: str | None = "t1"):
    req = MagicMock()
    req.url = MagicMock()
    req.url.path = path
    req.method = method
    req.path_params = {"tenant_id": tenant_id} if tenant_id is not None else {}
    return req


class TestAuditActionDep:
    def test_schedules_background_task_with_action_metadata(self) -> None:
        """``audit_action("create_user")`` returns a dep that, when called,
        adds a single background task with the action name, user email, and
        request path/method."""
        from src.admin.deps.audit import _write_audit, audit_action
        from src.admin.deps.auth import AdminUser

        dep = audit_action("create_user")
        background = MagicMock()
        background.add_task = MagicMock()
        user = AdminUser(email="admin@x.com", role="super_admin")

        dep(_request(), background, user)

        background.add_task.assert_called_once()
        # First positional arg should be the _write_audit callable.
        call = background.add_task.call_args
        assert call.args[0] is _write_audit
        assert call.kwargs["action"] == "create_user"
        assert call.kwargs["user_email"] == "admin@x.com"
        assert call.kwargs["tenant_id"] == "t1"
        assert call.kwargs["path"] == "/tenant/t1/users"
        assert call.kwargs["method"] == "POST"

    def test_missing_tenant_id_is_none(self) -> None:
        """Routes without a tenant_id path param yield tenant_id=None in the
        audit payload (not KeyError)."""
        from src.admin.deps.audit import audit_action
        from src.admin.deps.auth import AdminUser

        dep = audit_action("super_admin_action")
        background = MagicMock()
        background.add_task = MagicMock()
        user = AdminUser(email="root@x.com", role="super_admin")

        dep(
            _request(path="/admin/tenants", method="GET", tenant_id=None),
            background,
            user,
        )

        call = background.add_task.call_args
        assert call.kwargs["tenant_id"] is None


class TestWriteAuditFailureIsolation:
    def test_write_audit_swallows_exceptions(self) -> None:
        """A failing DB write MUST NOT propagate — audit logging is
        fire-and-forget."""
        from src.admin.deps.audit import _write_audit

        with patch(
            "src.admin.deps.audit.AuditLogger",
            side_effect=RuntimeError("db down"),
        ):
            # Must not raise.
            _write_audit(
                action="x",
                user_email="a@b",
                tenant_id=None,
                path="/",
                method="GET",
            )


class TestAuditLoggerDepAlias:
    def test_alias_is_callable_returning_dep(self) -> None:
        """``AuditLoggerDep`` is the canonical factory — it accepts an action
        name and returns a dep-callable."""
        from src.admin.deps.audit import AuditLoggerDep

        dep = AuditLoggerDep("login")
        assert callable(dep)
