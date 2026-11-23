"""BDD step definitions for BR-ADMIN-WORKFLOW-STEP-REVIEW: the step review page (GHSA-2968-2px9-6rjm).

The harness (``AdminWorkflowStepReviewEnv``) is provided by the ``_harness_env`` fixture in
tests/bdd/conftest.py. The Background's tenant and viewer login come from
tests/bdd/steps/domain/admin_tenant_scoping.py, and ``the page returns status`` from
tests/bdd/steps/domain/admin_accounts.py; all of them read the same ``ctx["admin_page"]`` key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.domain.admin_accounts import _require_admin_page
from tests.harness.admin_workflow_step_review import STEP_NAME, WEBHOOK_CREDENTIAL, WEBHOOK_TOKEN, webhook_url

if TYPE_CHECKING:
    from tests.harness.admin_workflow_step_review import AdminWorkflowStepReviewEnv


def _env(ctx: dict) -> AdminWorkflowStepReviewEnv:
    """Get the step review harness from context."""
    return ctx["env"]


def _page_html(ctx: dict) -> str:
    """The HTML of the page the last When requested."""
    return _require_admin_page(ctx).data.decode()


@given(
    parsers.parse(
        "the target tenant has a {tool} workflow step whose stored {document} holds a {registration} "
        "with a token and a credential"
    )
)
def given_step_with_webhook_secrets(ctx: dict, tool: str, document: str, registration: str) -> None:
    """Seed one step awaiting approval whose request or response stores the registration and its two secrets."""
    _env(ctx).seed_step_with_webhook_secrets(tool_name=tool, document=document, registration=registration)


@when("the caller opens the review page of that workflow step")
def when_open_step_review(ctx: dict) -> None:
    """GET the review page of the seeded step."""
    ctx["admin_page"] = _env(ctx).open_step_review_page()


@then(parsers.parse("the page shows the name and the {registration} url the step stores"))
def then_page_shows_payload(ctx: dict, registration: str) -> None:
    """The stored payload is rendered: the step's name and the registration's url are both on the page."""
    html = _page_html(ctx)
    missing = [value for value in (STEP_NAME, webhook_url(registration)) if value not in html]
    assert missing == [], f"The review page does not show {missing!r} from the stored step"


@then(parsers.parse("the page shows neither the token nor the credential of the {registration}"))
def then_page_hides_webhook_secrets(ctx: dict, registration: str) -> None:
    """Neither secret of the registration appears anywhere in the page's HTML."""
    html = _page_html(ctx)
    shown = {
        name: value for name, value in (("token", WEBHOOK_TOKEN), ("credentials", WEBHOOK_CREDENTIAL)) if value in html
    }
    assert shown == {}, f"The review page shows the {registration} secrets {sorted(shown)}: {shown!r}"
