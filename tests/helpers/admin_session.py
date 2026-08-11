"""Admin blueprint test-session helper.

Canonical home for the super-admin test-mode session populator that the admin
blueprint tests need before POSTing to ``require_tenant_access`` routes. Nine
older blueprint test modules still carry their own private ``_auth_session``
copy (pre-existing duplication debt); new modules must import this one.
"""

from __future__ import annotations

from typing import Any


def admin_auth_session(client: Any, tenant_id: str, *, auth_method: str | None = None) -> None:
    """Populate a super-admin test-mode session on a Flask test client.

    Pass ``auth_method='oidc'`` to exercise routes that gate on SSO login
    (e.g. ``disable-setup-mode``).
    """
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {"email": "test@example.com", "is_super_admin": True}
        sess["email"] = "test@example.com"
        sess["tenant_id"] = tenant_id
        sess["test_user"] = "test@example.com"
        sess["test_user_role"] = "super_admin"
        sess["test_user_name"] = "Test User"
        sess["test_tenant_id"] = tenant_id
        if auth_method is not None:
            sess["auth_method"] = auth_method
