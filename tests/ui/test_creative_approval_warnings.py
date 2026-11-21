"""The seller who approves a creative is told what the approval's push withheld.

``approve_creative`` answers ``{"success": true, "status": "approved", "warnings": [...]}``
when a push it triggered did not reach a live buy — for example
``Creative <id> was not pushed to buy <id>: CREATIVE_SIZE_MISMATCH``. Which warning the
route writes, and when, is graded by the BDD scenario
``T-UC-002-local-creative-kind-size-refused-retroactive-push`` and by
``tests/admin/test_creatives_blueprint.py``. What those cannot see is the page: the seller
reads the answer in a browser, through the page's own JavaScript.

WHY THE RESPONSE IS AMENDED AT THE NETWORK LAYER. Producing a withheld push in this stack
takes a live booked buy, its product's format options and an ad server that refuses the
size. The subject here is the seller's browser, so the test lets the page's request reach
the real route (the URL the page builds, the session, the approval itself all run for
real), checks the route answered a clean approval, and adds the warnings to the response
the browser receives. No production code is patched.
"""

import uuid
from collections.abc import Iterator

import pytest
from playwright.sync_api import Page, Route
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.database.models import Tenant
from tests.factories import CreativeFactory, PrincipalFactory
from tests.utils.database_helpers import bind_factories_to_session

pytestmark = pytest.mark.ui

TENANT_ID = "default"


@pytest.fixture
def pending_creative_ids(server_db_url) -> Iterator[list[str]]:
    """Two creatives awaiting review on the default tenant, in the server's database."""
    run = uuid.uuid4().hex[:8]
    engine = create_engine(server_db_url)
    session = Session(bind=engine)
    try:
        with bind_factories_to_session(session):
            tenant = session.get(Tenant, TENANT_ID)
            assert tenant is not None, f"tenant {TENANT_ID!r} is not in the server's database"
            principal = PrincipalFactory(tenant=tenant, principal_id=f"ui-approval-{run}")
            ids = [
                CreativeFactory(tenant=tenant, principal=principal, creative_id=f"cr-ui-approval-{run}-{n}").creative_id
                for n in range(2)
            ]
        yield ids
    finally:
        session.close()
        engine.dispose()


def _approve_on_page(page: Page, base_url: str, creative_id: str, added_warnings: list[str] | None) -> list[str]:
    """Click the creative's Approve button, accept every dialog, and return their messages.

    ``added_warnings`` is appended to the route's real answer, which must itself be a clean
    approval; ``None`` hands the browser the route's answer untouched.
    """
    route_answers: list[dict] = []

    def amend(route: Route) -> None:
        response = route.fetch()
        body = response.json()
        route_answers.append(body)
        if added_warnings is None:
            route.fulfill(response=response)
        else:
            route.fulfill(response=response, json={**body, "warnings": added_warnings})

    page.route(f"**/tenant/{TENANT_ID}/creatives/review/{creative_id}/approve", amend)

    dialogs: list[str] = []

    def accept(dialog) -> None:
        dialogs.append(dialog.message)
        dialog.accept()

    page.on("dialog", accept)

    page.goto(f"{base_url}/tenant/{TENANT_ID}/creatives/review")
    page.wait_for_load_state("networkidle")
    button = page.locator(f"button[onclick=\"approveCreative('{creative_id}')\"]")
    # The page reloads once the seller has dismissed the answer, so the reload marks the end.
    with page.expect_navigation():
        button.click()

    assert route_answers == [{"success": True, "status": "approved"}], (
        f"the route did not answer a clean approval of {creative_id}: {route_answers}"
    )
    return dialogs


def test_seller_sees_each_push_warning_after_approving(authenticated_page: Page, base_url, pending_creative_ids):
    creative_id = pending_creative_ids[0]
    warnings = [
        f"Creative {creative_id} was not pushed to buy mb-ui-live: CREATIVE_SIZE_MISMATCH",
        "Creative push to buy mb-ui-other failed — see server logs for details",
    ]

    dialogs = _approve_on_page(authenticated_page, base_url, creative_id, warnings)

    assert len(dialogs) == 2, f"expected the confirmation and one answer, got {dialogs}"
    answer = dialogs[1]
    for warning in warnings:
        assert warning in answer, f"the seller was not shown {warning!r}; the page said {answer!r}"
    assert "approved successfully" not in answer, f"the page reported a clean success: {answer!r}"
    assert authenticated_page.js_errors == [], f"JS errors: {authenticated_page.js_errors}"


def test_approval_without_warnings_reports_success(authenticated_page: Page, base_url, pending_creative_ids):
    dialogs = _approve_on_page(authenticated_page, base_url, pending_creative_ids[1], added_warnings=None)

    assert dialogs == ["Are you sure you want to approve this creative?", "✅ Creative approved successfully!"]
    assert authenticated_page.js_errors == [], f"JS errors: {authenticated_page.js_errors}"
