"""Admin harness for the workflow step review page (GHSA-2968-2px9-6rjm).

Drives ``GET /tenant/<tenant_id>/workflows/<workflow_id>/steps/<step_id>/review`` on the two
admin transports, for a step that stores a buyer's webhook registration with both of its
secrets. Subclasses ``AdminTenantScopingEnv`` for its target tenant and its member logins:
the caller is a tenant member holding the role the scenario names, and the target tenant's
``User`` row is what ``require_tenant_access`` decides on, on both transports.

The contract is stated in ``tests/bdd/features/BR-ADMIN-WORKFLOW-STEP-REVIEW.feature`` and
runs through the ``admin-workflow-step-review`` row of ``ENV_ROUTES`` in
tests/bdd/conftest.py.
"""

from __future__ import annotations

from typing import Any

from src.core.database.models import Tenant
from tests.factories import WorkflowStepFactory
from tests.factories.mint import mint_id
from tests.harness.admin_client import AdminResponse
from tests.harness.admin_tenant_scoping import AdminTenantScopingEnv
from tests.utils.database_helpers import bound_factory_session

#: The step's own name, stored beside the registration. Plain words, so the page's HTML
#: escaping cannot hide it: a page that rendered no payload would not contain it.
STEP_NAME = "Spring launch flight"
#: The two secrets of every registration the scenarios store. Letters and digits only, so
#: neither ``json.dumps`` nor HTML escaping can change how either is spelled on the page,
#: and their absence means the value is gone rather than re-encoded. Lengths meet the
#: pinned ``minLength`` of each field (16 for ``token``, 32 for ``credentials``).
WEBHOOK_TOKEN = "stepReviewToken0123456789"
WEBHOOK_CREDENTIAL = "stepReviewCredential0123456789abcdefXYZ"

#: The fields each registration requires besides ``url`` and ``authentication``, from the
#: pinned schemas: ``reporting_frequency`` (core/reporting-webhook.json) and
#: ``delivery_mode`` (media-buy/create-media-buy-request.json ``artifact_webhook``).
_REQUIRED_EXTRAS: dict[str, dict[str, str]] = {
    "push_notification_config": {},
    "reporting_webhook": {"reporting_frequency": "daily"},
    "artifact_webhook": {"delivery_mode": "realtime"},
}


def webhook_url(registration: str) -> str:
    """The url the scenarios register for *registration*, distinct per registration."""
    return f"https://buyer.example/hooks/{registration.replace('_', '-')}"


def _registration(registration: str) -> dict[str, Any]:
    """*registration* as a step stores it: the wire shape, with a Bearer credential and a token."""
    return {
        "url": webhook_url(registration),
        "token": WEBHOOK_TOKEN,
        "authentication": {"schemes": ["Bearer"], "credentials": WEBHOOK_CREDENTIAL},
        **_REQUIRED_EXTRAS[registration],
    }


def _stored_documents(document: str, registration: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The step's ``(request_data, response_data)``, with the registration in *document*.

    ``request``: the request the tool stored, as ``build_workflow_step`` writes it.
    ``response``: an update awaiting approval, whose response carries the request under
    ``request_data`` (``ContextManager.audit_workflow_step_result``). Its own
    ``request_data`` leaves the registration out, so the response is the only place the
    page can read the url from.
    """
    request = {"name": STEP_NAME, registration: _registration(registration)}
    if document == "request":
        return request, None
    if document == "response":
        return {"name": STEP_NAME}, {"status": "submitted", "request_data": request}
    raise ValueError(f"unknown stored document {document!r}")


class AdminWorkflowStepReviewEnv(AdminTenantScopingEnv):
    """Test environment for the workflow step review page.

    Same two transports and member logins as ``AdminTenantScopingEnv``; adds one workflow
    step in the target tenant and the request that opens its review page.
    """

    def __init__(self, *, mode: str = "integration", base_url: str | None = None) -> None:
        super().__init__(mode=mode, base_url=base_url)
        self._step: tuple[str, str] | None = None

    def seed_step_with_webhook_secrets(self, *, tool_name: str, document: str, registration: str) -> str:
        """A step awaiting approval in the target tenant whose *document* stores *registration*."""
        request_data, response_data = _stored_documents(document, registration)
        with bound_factory_session() as session:
            tenant = session.get(Tenant, self.target.tenant_id)
            step = WorkflowStepFactory(
                context__principal__tenant=tenant,
                context__principal__principal_id=mint_id("principal"),
                step_type="approval",
                owner="publisher",
                status="requires_approval",
                tool_name=tool_name,
                request_data=request_data,
                response_data=response_data,
            )
            self._step = (step.context_id, step.step_id)
        return step.step_id

    def open_step_review_page(self) -> AdminResponse:
        """``GET`` the review page of the seeded step, under its context id as the workflow id."""
        assert self._step is not None, "seed_step_with_webhook_secrets() has not run"
        context_id, step_id = self._step
        path = f"/tenant/{self.target.tenant_id}/workflows/{context_id}/steps/{step_id}/review"
        return self.client.request("GET", path)
