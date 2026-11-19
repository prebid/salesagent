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
    assert role_permits("admin", method)
    assert role_permits("admin", method, minimum=UserRole.ADMIN)


@pytest.mark.parametrize("method", READS + WRITES)
def test_manager_may_change_everything_but_admin_only_routes(method):
    assert role_permits("manager", method)
    assert role_permits("manager", method, minimum=UserRole.MANAGER)
    assert not role_permits("manager", method, minimum=UserRole.ADMIN)


@pytest.mark.parametrize("role", ["viewer", "", "super_admin", "Admin"])
def test_viewer_and_unknown_roles_are_read_only(role):
    for method in READS:
        assert role_permits(role, method)
    for method in WRITES:
        assert not role_permits(role, method)
    for method in READS + WRITES:
        assert not role_permits(role, method, minimum=UserRole.MANAGER)
        assert not role_permits(role, method, minimum=UserRole.ADMIN)


# A refusal answers in the form the caller reads: templates/users.html calls the Users-page
# routes with fetch() and response.json(), so an HTML 403 page there shows only a generic alert.
@pytest.mark.parametrize(
    ("headers", "api_mode", "json_body"),
    [
        ({"Accept": "*/*"}, False, True),  # fetch() with no Accept header of its own
        ({"Content-Type": "application/json"}, False, True),
        ({"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}, False, False),  # a page
        ({"Accept": "text/html"}, True, True),  # an API-mode route always answers JSON
    ],
)
def test_a_refusal_is_json_for_a_caller_that_reads_json(headers, api_mode, json_body):
    from flask import Flask
    from werkzeug.exceptions import Forbidden

    from src.admin.utils.helpers import _forbid

    with Flask(__name__).test_request_context("/", method="POST", headers=headers):
        with pytest.raises(Forbidden) as refused:
            _forbid("Your role does not allow this", api_mode=api_mode)
    response = refused.value.get_response()
    assert response.status_code == 403
    if json_body:
        assert response.get_json() == {"error": "Your role does not allow this"}
    else:
        assert response.mimetype == "text/html"


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
