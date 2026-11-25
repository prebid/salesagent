"""BDD step definitions for BR-ADMIN-SUPER-ADMIN.

The routes are driven through ``AdminAccountEnv`` on both admin transports.
"""

from __future__ import annotations

import time

from pytest_bdd import given, parsers, when

from src.admin.utils.helpers import SUPER_ADMIN_RECHECK_SECONDS
from tests.bdd.steps.domain.admin_accounts import _env

#: How long ago each scenario's sign-in cached its super-admin answer. The older one is a
#: minute past the interval, so a clock that drifts between the test and the server
#: cannot move it back inside. ``None``: a session cached before the time was recorded.
_CACHED_AGO: dict[str, float | None] = {
    "just now": 0,
    "longer than the recheck interval ago": SUPER_ADMIN_RECHECK_SECONDS + 60,
    "with no recorded time": None,
}


@given("the deployment serves many tenants")
def given_multi_tenant_deployment(ctx: dict) -> None:
    _env(ctx).serve_many_tenants()


@given(
    parsers.re(
        r'the operator is signed in as "(?P<email>[^"]+)", found a super admin '
        r"(?P<when>just now|longer than the recheck interval ago|with no recorded time)$"
    )
)
def given_signed_in_super_admin(ctx: dict, email: str, when: str) -> None:
    ago = _CACHED_AGO[when]
    _env(ctx).authenticate_as_super_admin_checked_at(email, None if ago is None else time.time() - ago)


@when("the operator opens the admin home page")
def when_open_admin_home(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).open_admin_home()
