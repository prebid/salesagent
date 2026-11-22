"""Browser coverage for how an admin page shows a refused JSON answer.

Every admin page that calls a JSON route reads a failure through ONE helper,
``adminErrorText`` (static/js/admin_answer.js, loaded by base.html): the refusal's fixed
``error`` text, then each refused field's issue text and each detail line. The JSON body is
graded by the BR-ADMIN-OPERATOR-REFUSALS scenarios; this grades what the operator READS,
which only a browser running the page's script can show.
"""

import pytest
from playwright.sync_api import Page

from src.core.operator_errors import InvalidFormInput

pytestmark = pytest.mark.ui


def test_a_refused_field_is_shown_with_the_refusal_and_its_issue(authenticated_page: Page, base_url):
    """The users page adds a domain the server refuses; the alert carries the refusal and the field's issue.

    The page's own check lets any non-blank value through, so the refusal is the server's:
    a domain with no dot is ``InvalidFormInput`` with one issue for ``domain``.
    """
    page = authenticated_page
    page.goto(f"{base_url}/tenant/default/users")
    page.wait_for_load_state("networkidle")

    page.fill("#domain", "nodot")
    with page.expect_event("dialog") as dialog_info:
        page.locator("#add-domain-form").get_by_role("button", name="Add Domain").click()
    dialog = dialog_info.value
    shown = dialog.message
    dialog.accept()

    assert shown == f"{InvalidFormInput.MESSAGE}\nInvalid domain format", f"the page showed {shown!r}"
    assert page.js_errors == [], f"JS errors on the users page: {page.js_errors}"
