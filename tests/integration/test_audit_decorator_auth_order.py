"""An unauthenticated admin POST must not write a ``success=True`` audit row.

``rotate_token`` (``src/admin/blueprints/principals.py``) stacked its decorators as

    @log_admin_action("rotate_principal_token")
    @require_tenant_access()

inverting the convention ``src/admin/utils/audit_decorator.py``'s own module docstring
states ("``@require_tenant_access()``  # First: Check authorization"). Decorators apply
bottom-up, so the audit decorator was OUTER and ran first.

That matters because ``require_tenant_access`` (``src/admin/utils/helpers.py:304``)
**returns** a redirect or a 401 rather than raising, while ``log_admin_action`` sets
``success = True`` and only flips it when the wrapped function RAISES. An unauthenticated
POST therefore wrote an ``audit_logs`` row saying a token rotation succeeded — principal
"unknown", tenant taken from the URL, the caller's own form keys in
``details.request_data`` — while nothing was rotated. Audit-log forgery by an
unauthenticated caller, on the endpoint that issues credentials.

``record_admin_action_failure`` is the sanctioned escape for a handler that catches its own
refusal and returns normally, and it does NOT apply: the handler never runs, because
``require_tenant_access`` short-circuits before ``f`` is called. No handler code is in a
position to declare the failure.

**Reordering alone was not enough, and the positive test below is why that was caught.**
``log_admin_action`` read the tenant from ``kwargs`` only. Flask passes URL parameters to
the OUTERMOST wrapper as kwargs, so that worked while the audit decorator was outermost and
silently yielded ``None`` once it was not — ``require_tenant_access`` passes the tenant id
positionally. Since the row is written only when a tenant id is present, the swap turned
auditing OFF on this route rather than fixing it. The decorator now resolves the tenant from
``request.view_args`` as well, which holds the URL parameters whatever the wrapper chain did
with them. Anyone reordering the remaining 54 sites under GH #2110 needs that fix first, or
they will silently disable admin auditing wholesale.

WHY INTEGRATION AND NOT BDD. The subject is an admin HTML route, so the BDD lane that
parametrizes a2a/mcp/rest does not reach it. This repo does have a hand-authored admin BDD
lane (``BR-ADMIN-ACCOUNTS.feature``, transports ``integration`` + ``e2e``) and covering
this there would additionally grade it against a real deployment; that is follow-up, and
it needs a feature file, a steps module and conftest wiring on top of the env this file
already uses.

Deliberately NOT a decorator-order assertion. Reading the order off the source grades a
spelling: it says nothing about whether a row is written, and would have stayed green
through exactly the kwargs defect described above.
"""

from __future__ import annotations

import pytest

from tests.harness.admin_principal import AdminPrincipalEnv

ROTATE_OPERATION = "AdminUI.rotate_principal_token"


@pytest.mark.requires_db
def test_unauthenticated_rotate_token_writes_no_success_row(integration_db):
    """No session, so no audit row claiming the rotation succeeded."""
    env = AdminPrincipalEnv(tenant_id="audit_order_tenant")
    principal_id = env.seed_principal(principal_id="audit_order_principal")

    response = env.post_rotate_token(
        principal_id,
        authenticated=False,
        form={"attacker_supplied": "value"},
    )

    # Which refusal it is — a redirect to login or a 401 — is the framework's business.
    # What this test owns is that nothing was AUDITED as a success.
    assert response.status_code != 200, (
        f"an unauthenticated POST reached the handler (status {response.status_code}); "
        "require_tenant_access should have refused it"
    )

    forged = [row for row in env.audit_rows(ROTATE_OPERATION) if row.success]
    assert not forged, (
        f"{len(forged)} audit row(s) record {ROTATE_OPERATION} as success=True for an "
        "UNAUTHENTICATED request. The audit decorator is running outside "
        "require_tenant_access, so it logged a refusal as a completed admin action. Put "
        "@require_tenant_access() ABOVE @log_admin_action(...), per the convention in "
        "src/admin/utils/audit_decorator.py's module docstring."
    )


@pytest.mark.requires_db
def test_authorized_rotate_token_still_rotates_and_is_audited(integration_db):
    """The other half, without which the test above is satisfiable by breaking the route.

    Reordering decorators moves what runs when, so a refusal test alone cannot tell a fixed
    endpoint from a broken one: a ``rotate_token`` that 500s, or one whose audit decorator
    no longer fires at all, passes it. This asserts the authorized path still does both
    things — replaces the stored token hash, and records success=True.

    It earned its place immediately: it is what caught the reorder silently disabling
    auditing, which a refusal-only test reported as a clean fix. The endpoint had no test
    of any kind before this file, and it is the route that issues tenant credentials.
    """
    env = AdminPrincipalEnv(tenant_id="audit_order_ok_tenant")
    principal_id = env.seed_principal(principal_id="audit_order_ok_principal")
    before = env.token_hash(principal_id)

    response = env.post_rotate_token(principal_id, authenticated=True)
    assert response.status_code in (200, 302), f"authorized rotation was refused with {response.status_code}"

    after = env.token_hash(principal_id)
    audited = [row for row in env.audit_rows(ROTATE_OPERATION) if row.success]

    assert after != before, "the stored token hash did not change, so nothing was rotated"
    assert audited, (
        "an AUTHORIZED rotation wrote no success=True audit row. Moving "
        "require_tenant_access outside log_admin_action must not stop authorized actions "
        "being audited — that is the whole point of the decorator. Check that "
        "log_admin_action still resolves tenant_id when it is not the outermost wrapper."
    )
