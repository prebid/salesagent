"""Which admin-UI requests each tenant role may make (src/admin/utils/helpers.py:role_permits).

Roles were stored but never checked, so a viewer could do anything an admin could,
including adding an admin. ``require_tenant_access`` now asks this function with the
role on the caller's ``User`` row.
"""

from __future__ import annotations

import pytest

from src.admin.utils.helpers import role_permits

READS = ["GET", "HEAD", "OPTIONS"]
WRITES = ["POST", "PUT", "PATCH", "DELETE"]


@pytest.mark.parametrize("method", READS + WRITES)
def test_admin_may_do_anything(method):
    assert role_permits("admin", method, admin_only=False)
    assert role_permits("admin", method, admin_only=True)


@pytest.mark.parametrize("method", READS + WRITES)
def test_manager_may_change_everything_but_admin_only_routes(method):
    assert role_permits("manager", method, admin_only=False)
    assert not role_permits("manager", method, admin_only=True)


@pytest.mark.parametrize("role", ["viewer", "", "super_admin", "Admin"])
def test_viewer_and_unknown_roles_are_read_only(role):
    for method in READS:
        assert role_permits(role, method, admin_only=False)
    for method in WRITES:
        assert not role_permits(role, method, admin_only=False)
    for method in READS + WRITES:
        assert not role_permits(role, method, admin_only=True)
