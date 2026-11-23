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

from typing import NamedTuple

import pytest
from playwright.sync_api import Page, Route

from tests.factories import CreativeFactory, PrincipalFactory
from tests.factories.mint import mint_id

pytestmark = pytest.mark.ui


class PendingCreatives(NamedTuple):
    tenant_id: str
    creative_ids: list[str]


@pytest.fixture
def pending_creatives(server_seeding, server_tenant) -> PendingCreatives:
    """A tenant of its own (``server_tenant``) holding two creatives awaiting review."""
    with server_seeding():
        tenant = server_tenant("ui_approval")
        principal = PrincipalFactory(tenant=tenant, principal_id=mint_id("ui_approval"))
        ids = [
            CreativeFactory(tenant=tenant, principal=principal, creative_id=mint_id("cr_ui_approval")).creative_id
            for _ in range(2)
        ]
        return PendingCreatives(tenant.tenant_id, ids)


def _approve_on_page(
    page: Page, base_url: str, tenant_id: str, creative_id: str, added_warnings: list[str] | None
) -> list[str]:
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

    page.route(f"**/tenant/{tenant_id}/creatives/review/{creative_id}/approve", amend)

    dialogs: list[str] = []

    def accept(dialog) -> None:
        dialogs.append(dialog.message)
        dialog.accept()

    page.on("dialog", accept)

    page.goto(f"{base_url}/tenant/{tenant_id}/creatives/review")
    page.wait_for_load_state("networkidle")
    card = page.locator(f'.creative-card[data-creative-id="{creative_id}"]')
    button = card.get_by_role("button", name="✅ Approve", exact=True)
    # The page reloads once the seller has dismissed the answer, so the reload marks the end.
    with page.expect_navigation():
        button.click()

    assert route_answers == [{"success": True, "status": "approved"}], (
        f"the route did not answer a clean approval of {creative_id}: {route_answers}"
    )
    return dialogs


def test_seller_sees_each_push_warning_after_approving(authenticated_page: Page, base_url, pending_creatives):
    creative_id = pending_creatives.creative_ids[0]
    warnings = [
        f"Creative {creative_id} was not pushed to buy mb-ui-live: CREATIVE_SIZE_MISMATCH",
        "Creative push to buy mb-ui-other failed — see server logs for details",
    ]

    dialogs = _approve_on_page(authenticated_page, base_url, pending_creatives.tenant_id, creative_id, warnings)

    assert len(dialogs) == 2, f"expected the confirmation and one answer, got {dialogs}"
    answer = dialogs[1]
    for warning in warnings:
        assert warning in answer, f"the seller was not shown {warning!r}; the page said {answer!r}"
    assert "approved successfully" not in answer, f"the page reported a clean success: {answer!r}"
    assert authenticated_page.js_errors == [], f"JS errors: {authenticated_page.js_errors}"


def test_approval_without_warnings_reports_success(authenticated_page: Page, base_url, pending_creatives):
    tenant_id, (_, creative_id) = pending_creatives
    dialogs = _approve_on_page(authenticated_page, base_url, tenant_id, creative_id, added_warnings=None)

    assert dialogs == ["Are you sure you want to approve this creative?", "✅ Creative approved successfully!"]
    assert authenticated_page.js_errors == [], f"JS errors: {authenticated_page.js_errors}"
