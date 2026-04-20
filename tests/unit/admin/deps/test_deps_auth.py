"""L0-12 — auth dep obligation tests (Pattern a: stub-first + semantic).

Red state: stub module returns None/always-redirect; these tests fail semantically
because behavior assertions don't match. Green state: real impl per
foundation-modules.md §11.4.
"""

from __future__ import annotations

from typing import Annotated, get_args, get_origin
from unittest.mock import MagicMock

import pytest


def _request(session: dict, url: str = "http://host.example/admin/foo"):
    req = MagicMock()
    req.session = session
    req.url = url
    return req


class TestExtractEmail:
    def test_string_input_lowercased(self) -> None:
        from src.admin.deps.auth import _extract_email

        assert _extract_email("USER@X.com ") == "user@x.com"

    def test_dict_input_with_email_key(self) -> None:
        from src.admin.deps.auth import _extract_email

        assert _extract_email({"email": "USER@X.com"}) == "user@x.com"

    def test_none_input_returns_empty(self) -> None:
        from src.admin.deps.auth import _extract_email

        assert _extract_email(None) == ""

    def test_dict_without_email_key_returns_empty(self) -> None:
        from src.admin.deps.auth import _extract_email

        assert _extract_email({"name": "bob"}) == ""


class TestGetAdminUserOrNone:
    def test_no_session_user_returns_none(self) -> None:
        from src.admin.deps.auth import _get_admin_user_or_none

        req = _request({})
        assert _get_admin_user_or_none(req) is None

    def test_test_mode_requires_both_env_and_session(self, monkeypatch) -> None:
        """ADCP_AUTH_TEST_MODE=true alone is not enough — session must also
        carry the test_user key. Guarding prevents stale cookies from leaking
        test access in prod after the env flip.
        """
        from src.admin.deps.auth import _get_admin_user_or_none

        monkeypatch.delenv("ADCP_AUTH_TEST_MODE", raising=False)
        req = _request({"test_user": "bob@example.com"})
        assert _get_admin_user_or_none(req) is None

        monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")
        user = _get_admin_user_or_none(req)
        assert user is not None
        assert user.email == "bob@example.com"
        assert user.is_test_user is True

    def test_authenticated_session_produces_admin_user(self, monkeypatch) -> None:
        from src.admin.deps.auth import _get_admin_user_or_none

        monkeypatch.delenv("SUPER_ADMIN_EMAILS", raising=False)
        monkeypatch.delenv("SUPER_ADMIN_DOMAINS", raising=False)
        req = _request({"user": {"email": "ALICE@x.com"}})
        user = _get_admin_user_or_none(req)
        assert user is not None
        # AdminUser.__post_init__ lowercases email
        assert user.email == "alice@x.com"
        assert user.is_test_user is False


class TestGetAdminUser:
    def test_unauthenticated_raises_admin_redirect(self) -> None:
        from src.admin.deps.auth import AdminRedirect, get_admin_user

        req = _request({})
        with pytest.raises(AdminRedirect) as exc_info:
            get_admin_user(req)
        assert exc_info.value.to == "/admin/login"

    def test_authenticated_returns_admin_user(self, monkeypatch) -> None:
        from src.admin.deps.auth import AdminUser, get_admin_user

        monkeypatch.delenv("SUPER_ADMIN_EMAILS", raising=False)
        monkeypatch.delenv("SUPER_ADMIN_DOMAINS", raising=False)
        req = _request({"user": "alice@x.com"})
        user = get_admin_user(req)
        assert isinstance(user, AdminUser)
        assert user.email == "alice@x.com"


class TestIsSuperAdmin:
    def test_env_emails_match(self, monkeypatch) -> None:
        from src.admin.deps.auth import is_super_admin

        monkeypatch.setenv("SUPER_ADMIN_EMAILS", "alice@x.com, BOB@y.com")
        monkeypatch.delenv("SUPER_ADMIN_DOMAINS", raising=False)
        assert is_super_admin("alice@x.com") is True
        assert is_super_admin("bob@y.com") is True
        assert is_super_admin("nope@z.com") is False

    def test_env_domains_match(self, monkeypatch) -> None:
        from src.admin.deps.auth import is_super_admin

        monkeypatch.delenv("SUPER_ADMIN_EMAILS", raising=False)
        monkeypatch.setenv("SUPER_ADMIN_DOMAINS", "anthropic.com")
        assert is_super_admin("someone@anthropic.com") is True
        assert is_super_admin("someone@other.com") is False

    def test_empty_email_is_never_super(self) -> None:
        from src.admin.deps.auth import is_super_admin

        assert is_super_admin("") is False


class TestDepAliasTypes:
    def test_current_user_dep_is_annotated(self) -> None:
        from src.admin.deps.auth import AdminUser, CurrentUserDep

        assert get_origin(CurrentUserDep) is Annotated or CurrentUserDep is not None
        args = get_args(CurrentUserDep)
        assert args[0] is AdminUser

    def test_require_super_admin_dep_is_annotated(self) -> None:
        from src.admin.deps.auth import AdminUser, RequireSuperAdminDep

        args = get_args(RequireSuperAdminDep)
        assert args[0] is AdminUser


class TestAdminUserNormalization:
    def test_email_is_lowercased_in_post_init(self) -> None:
        from src.admin.deps.auth import AdminUser

        u = AdminUser(email="ALICE@X.COM", role="tenant_user")
        assert u.email == "alice@x.com"
