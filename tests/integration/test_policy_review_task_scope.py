"""The policy routes are tenant-scoped, and review settles only pending policy reviews.

``POST /tenant/<tenant_id>/policy/review/<task_id>`` used to load any workflow step of the
tenant by id and mark it ``completed`` or ``failed`` for any signed-in user. Now it is under
``require_tenant_access()``: any user of the tenant may read the review form, the POST needs a
manager (``role_permits``), and it reaches only a ``policy_review`` step that is still
``pending``. A media buy approval answers 404 even for a manager, and a viewer or a user of
another tenant gets 403 and changes nothing. ``policy.index`` is under
the same decorator, and ``policy.update`` needs a tenant admin: a user of another tenant cannot
read or change the tenant's policy, and a tenant admin, locked out before by a session-role
check, can change it.
"""

from __future__ import annotations

import pytest

from src.core.database.models import AuditLog, Tenant, UserRole, WorkflowStep
from tests.helpers.admin_session import tenant_user_client

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "policy_review_tenant"
OTHER_TENANT_ID = "policy_review_other"
EMAILS = {role: f"{role}@policy-review.example" for role in UserRole}
OUTSIDER = "manager@other-tenant.example"


@pytest.fixture
def seeded(integration_db):
    """The tenant, one user per role, a manager of another tenant, and four steps of the tenant."""
    from tests.factories import ContextFactory, PrincipalFactory, TenantFactory, UserFactory, WorkflowStepFactory
    from tests.harness._base import IntegrationEnv

    with IntegrationEnv(tenant_id=TENANT_ID) as env:
        tenant = TenantFactory(tenant_id=TENANT_ID, subdomain="policy-review")
        for role in UserRole:
            UserFactory(tenant=tenant, user_id=f"pr_{role}", email=EMAILS[role], role=role)
        other = TenantFactory(tenant_id=OTHER_TENANT_ID, subdomain="policy-review-other")
        UserFactory(tenant=other, user_id="pr_outsider", email=OUTSIDER, role=UserRole.MANAGER)

        context = ContextFactory(principal=PrincipalFactory(tenant=tenant))
        steps = {
            "media_buy": WorkflowStepFactory(
                context=context, step_type="media_buy_creation", status="requires_approval"
            ).step_id,
            # Pending like the queue but another type, so only the step_type half of the queue
            # condition refuses it (the mock adapter creates pending steps of its own types).
            "pending_other_type": WorkflowStepFactory(
                context=context, step_type="media_buy_creation", status="pending"
            ).step_id,
            "policy_review": WorkflowStepFactory(context=context, step_type="policy_review", status="pending").step_id,
            "settled_review": WorkflowStepFactory(
                context=context, step_type="policy_review", status="completed"
            ).step_id,
        }
        yield env, steps


_client = tenant_user_client


def _post(app, email, step_id, action="reject"):
    form = {"review_notes": "x"} if action is None else {"action": action, "review_notes": "x"}
    # the field names policy_review.html posts
    return _client(app, email).post(f"/tenant/{TENANT_ID}/policy/review/{step_id}", data=form)


def _step(env, step_id):
    env.get_session().expire_all()
    step = env.get_one(WorkflowStep, step_id=step_id)
    return step.status, step.response_data


@pytest.mark.parametrize("email", [EMAILS[UserRole.VIEWER], OUTSIDER])
@pytest.mark.parametrize("step", ["media_buy", "policy_review"])
def test_a_viewer_or_outsider_is_refused_and_nothing_changes(seeded, test_admin_app, email, step):
    env, steps = seeded
    before = _step(env, steps[step])
    assert _post(test_admin_app, email, steps[step]).status_code == 403
    assert _step(env, steps[step]) == before


@pytest.mark.parametrize("step", ["media_buy", "pending_other_type", "settled_review"])
@pytest.mark.parametrize("action", ["approve", "reject"])
def test_a_manager_cannot_settle_a_step_outside_the_pending_policy_queue(seeded, test_admin_app, step, action):
    env, steps = seeded
    before = _step(env, steps[step])
    assert _post(test_admin_app, EMAILS[UserRole.MANAGER], steps[step], action).status_code == 404
    assert _step(env, steps[step]) == before


@pytest.mark.parametrize(
    ("action", "status", "approved"), [("approve", "completed", True), ("reject", "failed", False)]
)
def test_a_manager_settles_a_pending_policy_review(seeded, test_admin_app, action, status, approved):
    env, steps = seeded
    response = _post(test_admin_app, EMAILS[UserRole.MANAGER], steps["policy_review"], action)
    assert response.status_code == 302, response.get_data(as_text=True)[:500]
    assert _step(env, steps["policy_review"]) == (status, {"approved": approved, "notes": "x"})


def test_a_viewer_may_read_the_review_form_but_an_outsider_may_not(seeded, test_admin_app):
    _, steps = seeded
    url = f"/tenant/{TENANT_ID}/policy/review/{steps['policy_review']}"
    assert _client(test_admin_app, EMAILS[UserRole.VIEWER]).get(url).status_code == 200
    assert _client(test_admin_app, OUTSIDER).get(url).status_code == 403


def test_an_outsider_cannot_read_the_policy_page(seeded, test_admin_app):
    assert _client(test_admin_app, OUTSIDER).get(f"/tenant/{TENANT_ID}/policy/").status_code == 403


def _policy(env):
    env.get_session().expire_all()
    return env.get_one(Tenant, tenant_id=TENANT_ID).policy_settings


@pytest.mark.parametrize("email", [EMAILS[UserRole.VIEWER], EMAILS[UserRole.MANAGER], OUTSIDER])
def test_only_a_tenant_admin_may_update_the_policy(seeded, test_admin_app, email):
    env, _ = seeded
    form = {"prohibited_advertisers": "blocked.example"}
    url = f"/tenant/{TENANT_ID}/policy/update"
    before = _policy(env)
    assert _client(test_admin_app, email).post(url, data=form).status_code == 403
    assert _policy(env) == before
    assert _client(test_admin_app, EMAILS[UserRole.ADMIN]).post(url, data=form).status_code == 302
    assert _policy(env)["prohibited_advertisers"] == ["blocked.example"]


@pytest.mark.parametrize("action", ["", "bogus", None])
def test_an_unknown_action_is_refused_and_changes_nothing(seeded, test_admin_app, action):
    env, steps = seeded
    before = _step(env, steps["policy_review"])
    assert _post(test_admin_app, EMAILS[UserRole.MANAGER], steps["policy_review"], action).status_code == 400
    assert _step(env, steps["policy_review"]) == before


def test_a_refused_review_is_audited_as_a_failure(seeded, test_admin_app):
    env, steps = seeded
    assert _post(test_admin_app, EMAILS[UserRole.MANAGER], steps["media_buy"], "approve").status_code == 404
    env.get_session().expire_all()
    (row,) = env.query(AuditLog, tenant_id=TENANT_ID, operation="AdminUI.review_policy_task")
    assert (row.success, row.details["task_id"]) == (False, steps["media_buy"])


def test_a_second_decision_on_a_settled_review_is_refused(seeded, test_admin_app):
    env, steps = seeded
    manager = EMAILS[UserRole.MANAGER]
    assert _post(test_admin_app, manager, steps["policy_review"], "approve").status_code == 302
    assert _post(test_admin_app, manager, steps["policy_review"], "reject").status_code == 404
    assert _step(env, steps["policy_review"]) == ("completed", {"approved": True, "notes": "x"})
