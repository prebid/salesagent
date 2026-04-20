"""L0-12 — tenant dep obligation tests (Pattern a: stub-first + semantic).

Red state: ``get_current_tenant`` returns ``{}`` without access check; tests
fail because they assert access control + tenant payload fidelity.

Green state: real impl per foundation-modules.md §11.4 — super admin bypass,
per-user ``User`` row lookup with ``is_active=True`` filter, 403 on denial.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


def _request(session: dict | None = None):
    req = MagicMock()
    req.session = session if session is not None else {}
    return req


class TestSuperAdminBypass:
    def test_super_admin_bypasses_user_row(self) -> None:
        """Super admin role skips the User access check entirely — just
        loads the tenant by ID."""
        from src.admin.deps.auth import AdminUser
        from src.admin.deps.tenant import get_current_tenant

        user = AdminUser(email="root@x.com", role="super_admin")
        payload = {"tenant_id": "t1", "name": "T1", "is_active": True}

        with patch("src.admin.deps.tenant._load_tenant", return_value=payload) as mock_load:
            result = get_current_tenant(_request(), user, "t1")

        assert result == payload
        mock_load.assert_called_once_with("t1")


class TestRegularUserAccessCheck:
    def test_user_with_active_row_gets_tenant(self) -> None:
        from src.admin.deps.auth import AdminUser
        from src.admin.deps.tenant import get_current_tenant

        user = AdminUser(email="alice@x.com", role="tenant_user")
        payload = {"tenant_id": "t1", "name": "T1", "is_active": True}

        with (
            patch("src.admin.deps.tenant._user_has_tenant_access", return_value=True),
            patch("src.admin.deps.tenant._load_tenant", return_value=payload),
        ):
            result = get_current_tenant(_request(), user, "t1")

        assert result == payload

    def test_user_without_access_gets_403(self) -> None:
        from src.admin.deps.auth import AdminUser
        from src.admin.deps.tenant import get_current_tenant

        user = AdminUser(email="alice@x.com", role="tenant_user")

        with (
            patch("src.admin.deps.tenant._user_has_tenant_access", return_value=False),
            patch("src.admin.deps.tenant._tenant_has_auth_setup_mode", return_value=False),
        ):
            with pytest.raises(HTTPException) as exc_info:
                get_current_tenant(_request(), user, "t1")

        assert exc_info.value.status_code == 403


class TestTestUserBypass:
    def test_test_user_with_matching_test_tenant_id(self) -> None:
        from src.admin.deps.auth import AdminUser
        from src.admin.deps.tenant import get_current_tenant

        user = AdminUser(email="qa@x.com", role="test", is_test_user=True)
        payload = {"tenant_id": "t1", "name": "T1", "is_active": True}
        req = _request({"test_tenant_id": "t1"})

        with patch("src.admin.deps.tenant._load_tenant", return_value=payload):
            result = get_current_tenant(req, user, "t1")

        assert result == payload

    def test_test_user_from_other_tenant_rejected(self) -> None:
        from src.admin.deps.auth import AdminUser
        from src.admin.deps.tenant import get_current_tenant

        user = AdminUser(email="qa@x.com", role="test", is_test_user=True)
        req = _request({"test_tenant_id": "other_tenant"})

        with patch("src.admin.deps.tenant._tenant_has_auth_setup_mode", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                get_current_tenant(req, user, "t1")

        assert exc_info.value.status_code == 403


class TestTenantDepAlias:
    def test_current_tenant_dep_resolves_to_dict(self) -> None:
        from typing import get_args

        from src.admin.deps.tenant import CurrentTenantDep

        args = get_args(CurrentTenantDep)
        assert args[0] is dict
