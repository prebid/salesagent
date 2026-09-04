"""Flask admin test client, authenticated as super admin.

Lives in ``tests/helpers/`` because it has nothing to do with any one feature.
It previously sat in ``tests/unit/_tmp_helpers.py`` — a TMP-private module — and
was imported from ``tests/unit/test_ssrf_url_validator.py``, so a non-TMP suite
depended on a feature's private helpers while roughly ten other sites hand-rolled
the same app-creation + session-setup block and would never have found it there
(#1197 review).
"""

from __future__ import annotations

from typing import Any


def make_super_admin_client() -> Any:
    """Create a Flask test client authenticated as super admin.

    The shared builder for the plain super-admin case: create the app, set the
    test-user session keys, hand back the client.

    Not "the one implementation for every admin test" — 13 other hand-rolled
    super-admin blocks exist and differ materially (tenant-scoped session keys,
    per-test DB patching), so they are not a mechanical re-point. Converging them
    is a separate piece of work; this is here so new tests and the two suites that
    do fit have one place to call (#1197 review).
    """
    from src.admin.app import create_app

    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret", "WTF_CSRF_ENABLED": False})
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["test_user"] = "test_super_admin@example.com"
        sess["test_user_role"] = "super_admin"
        sess["authenticated"] = True
    return client
