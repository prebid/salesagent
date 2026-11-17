"""Which admin-UI requests each tenant role may make (src/admin/utils/helpers.py:role_permits).

Roles were stored but never checked, so a viewer could do anything an admin could,
including adding an admin. ``require_tenant_access`` now asks this function with the
role on the caller's ``User`` row.
"""

from __future__ import annotations

import pytest

from src.admin.utils.helpers import role_permits
from src.core.database.models import UserRole

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


def test_the_users_page_describes_every_role_least_access_first():
    """The role pickers in templates/users.html list ROLE_DESCRIPTIONS; the first is the default."""
    from src.admin.blueprints.users import ROLE_DESCRIPTIONS

    assert list(ROLE_DESCRIPTIONS) == list(UserRole)
    assert list(UserRole)[0] == UserRole.VIEWER


def test_the_role_check_constraint_is_derived_from_user_role():
    """The SQL keeps the text the migrations created, so the schema does not drift."""
    from sqlalchemy import CheckConstraint

    from src.core.database.models import User

    (constraint,) = [c for c in User.__table__.constraints if isinstance(c, CheckConstraint)]
    assert str(constraint.sqltext) == "role IN ('admin', 'manager', 'viewer')"
