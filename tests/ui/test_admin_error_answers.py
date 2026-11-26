"""Browser coverage for how an admin page shows a refused JSON answer.

Every admin page that calls a JSON route reads a failure through ONE helper,
``adminErrorText`` (static/js/admin_answer.js, loaded by base.html): the refusal's fixed
``error`` text, then each refused field's issue text and each detail line. The JSON body is
graded by the BR-ADMIN-OPERATOR-REFUSALS scenarios; this grades what the operator READS,
which only a browser running the page's script can show.
"""

import pytest
from playwright.sync_api import Locator, Page

from src.core.operator_errors import InvalidDomainNameError, InvalidFormInput, SuperAdminDomainRefused

pytestmark = pytest.mark.ui

#: The SUPER_ADMIN_DOMAIN docker-compose.e2e.yml gives the stack this suite drives, spelled
#: with capitals and a trailing dot, which the refusal reads as the same domain.
SUPER_ADMIN_DOMAIN_RESPELLED = "Operator.BDD-Refusals.Example."

#: A GAM service account a tenant's onboarding has created, for the page that asks for its
#: network code.
SERVICE_ACCOUNT_EMAIL = "ui-refusal@ui-project.iam.gserviceaccount.com"


def _alert_after_click(page: Page, button: Locator) -> str:
    """The text of the alert *button*'s click raises, once the alert is dismissed."""
    with page.expect_event("dialog") as dialog_info:
        button.click()
    dialog = dialog_info.value
    shown = dialog.message
    dialog.accept()
    return shown


@pytest.mark.parametrize(
    ("domain", "refusal"),
    [("nodot", InvalidDomainNameError), (SUPER_ADMIN_DOMAIN_RESPELLED, SuperAdminDomainRefused)],
    ids=["not-a-domain-name", "super-admin-domain"],
)
def test_a_refused_domain_is_shown_with_its_refusal(authenticated_page: Page, base_url, domain, refusal):
    """The users page adds a domain the server refuses; the alert is that refusal's text alone.

    The page's own check lets any non-blank value through, so the refusal is the server's:
    ``TenantConfigRepository.add_to_authorized_list``, the one seam every page appends a domain
    through. Its refusals name no field, so the alert carries no issue line.
    """
    page = authenticated_page
    page.goto(f"{base_url}/tenant/default/users")
    page.wait_for_load_state("networkidle")

    page.fill("#domain", domain)
    shown = _alert_after_click(page, page.locator("#add-domain-form").get_by_role("button", name="Add Domain"))

    assert shown == refusal.MESSAGE, f"the page showed {shown!r}"
    assert page.js_errors == [], f"JS errors on the users page: {page.js_errors}"


@pytest.fixture
def gam_service_account_tenant(server_seeding, server_tenant):
    """A tenant of its own (``server_tenant``) that sells through GAM and has created its service account, so its page asks for the network code."""
    from tests.factories import AdapterConfigFactory

    with server_seeding():
        tenant = server_tenant("ui_sa", ad_server="google_ad_manager")
        AdapterConfigFactory(
            tenant=tenant, adapter_type="google_ad_manager", gam_service_account_email=SERVICE_ACCOUNT_EMAIL
        )
        return tenant.tenant_id


def test_a_refused_field_is_shown_with_the_refusal_and_its_issue(
    authenticated_page: Page, base_url, gam_service_account_tenant
):
    """The settings page saves a GAM network code that is not a number; the alert carries the refusal and the field's issue.

    The page checks only that the code is not blank, so the refusal is the server's:
    ``InvalidFormInput`` with one issue for ``network_code`` (``validate_gam_config``).
    """
    page = authenticated_page
    page.goto(f"{base_url}/tenant/{gam_service_account_tenant}/settings#adserver")
    page.wait_for_load_state("networkidle")

    page.fill("#service_account_network_code", "not-a-number")
    shown = _alert_after_click(page, page.get_by_role("button", name="Save Network Code"))

    assert shown == f"❌ Failed to save network code:\n\n{InvalidFormInput.MESSAGE}\nNetwork code must be numeric", (
        f"the page showed {shown!r}"
    )
    assert page.js_errors == [], f"JS errors on the settings page: {page.js_errors}"
