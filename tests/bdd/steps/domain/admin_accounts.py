"""BDD step definitions for BR-ADMIN-ACCOUNTS: Admin Account Management.

Steps for testing the admin accounts blueprint via Flask test client.
The harness (AdminAccountEnv) is provided by the _harness_env fixture
in tests/bdd/conftest.py.

beads: salesagent-oj0.1.2
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import given, parsers, then, when

if TYPE_CHECKING:
    from tests.harness.admin_accounts import AdminAccountEnv


def _env(ctx: dict) -> AdminAccountEnv:
    """Get the admin harness from context."""
    return ctx["env"]


def _datatable_to_form(datatable: list) -> dict[str, str]:
    """Convert field/value data table to form dict.

    Expects: | field | value | header row, then one row per field.
    """
    headers = [str(h) for h in datatable[0]]
    field_idx = headers.index("field")
    value_idx = headers.index("value")
    return {str(row[field_idx]).strip(): str(row[value_idx]).strip() for row in datatable[1:]}


def _find_account_id_in_ctx(ctx: dict) -> str | None:
    """Find an account_id stored in context by a previous step."""
    for key, val in ctx.items():
        if key.startswith("account_id:"):
            return val
    return None


# ═══════════════════════════════════════════════════════════════════════════
# GIVEN steps
# ═══════════════════════════════════════════════════════════════════════════


@given(parsers.parse('an admin user is authenticated for tenant "{tenant_id}"'))
def given_admin_authenticated(ctx: dict, tenant_id: str) -> None:
    """Authenticate the admin user for the given tenant.

    Overrides the harness default tenant to match the Gherkin scenario.
    """
    env = _env(ctx)
    # Use the harness's actual tenant (already created in DB)
    env.authenticate(env.tenant_id)


@given(parsers.parse('the tenant "{tenant_id}" exists in the database'))
def given_tenant_exists(ctx: dict, tenant_id: str) -> None:
    """Ensure the specified tenant exists in the database."""
    env = _env(ctx)
    env._ensure_tenant_for_id(tenant_id)


@given("the tenant has the following accounts:")
def given_tenant_has_accounts(ctx: dict, datatable: list) -> None:
    """Create accounts from a data table."""
    env = _env(ctx)
    headers = datatable[0]
    for row in datatable[1:]:
        data = dict(zip(headers, row, strict=True))
        env.create_account(
            name=data["name"],
            status=data.get("status", "active"),
            brand_domain=data.get("brand_domain"),
        )


@given(parsers.parse('the tenant has an account "{name}" with status "{status}"'))
def given_tenant_has_account(ctx: dict, name: str, status: str) -> None:
    """Create a single account with the given name and status."""
    env = _env(ctx)
    account_id = env.create_account(name=name, status=status, brand_domain=f"{name.lower().replace(' ', '-')}.com")
    ctx[f"account_id:{name}"] = account_id


@given("the admin user is not authenticated")
def given_admin_not_authenticated(ctx: dict) -> None:
    """Clear any existing authentication."""
    env = _env(ctx)
    env.clear_auth()


# ═══════════════════════════════════════════════════════════════════════════
# WHEN steps
# ═══════════════════════════════════════════════════════════════════════════


@when("the admin navigates to the accounts list page")
def when_navigate_list(ctx: dict) -> None:
    """GET the accounts list page."""
    env = _env(ctx)
    ctx["response"] = env.get_list_page()


@when(parsers.parse('the admin navigates to the accounts list page with status filter "{status}"'))
def when_navigate_list_filtered(ctx: dict, status: str) -> None:
    """GET the accounts list page with a status filter."""
    env = _env(ctx)
    ctx["response"] = env.get_list_page(status_filter=status)


@when("the admin navigates to the create account page")
def when_navigate_create(ctx: dict) -> None:
    """GET the create account form."""
    env = _env(ctx)
    ctx["response"] = env.get_create_page()


@when("the admin submits the create account form with:")
def when_submit_create_form(ctx: dict, datatable: list) -> None:
    """POST the create account form with data table values.

    Data table has field/value columns (one row per field).
    """
    env = _env(ctx)
    form_data = _datatable_to_form(datatable)
    ctx["response"] = env.post_create(form_data)


@when(parsers.parse('the admin navigates to the account detail page for "{name}"'))
def when_navigate_detail(ctx: dict, name: str) -> None:
    """GET the account detail page."""
    env = _env(ctx)
    account_id = ctx.get(f"account_id:{name}") or env.get_account_id_by_name(name)
    assert account_id, f"No account found with name '{name}'"
    ctx["response"] = env.get_detail_page(account_id)


@when(parsers.parse('the admin navigates to the edit page for "{name}"'))
def when_navigate_edit(ctx: dict, name: str) -> None:
    """GET the account edit form."""
    env = _env(ctx)
    account_id = ctx.get(f"account_id:{name}") or env.get_account_id_by_name(name)
    assert account_id, f"No account found with name '{name}'"
    ctx["response"] = env.get_edit_page(account_id)


@when("the admin submits the edit form with:")
def when_submit_edit_form(ctx: dict, datatable: list) -> None:
    """POST the edit form with data table values."""
    env = _env(ctx)
    form_data = _datatable_to_form(datatable)

    # Get account_id from a previous Given/When step
    account_id = _find_account_id_in_ctx(ctx)
    assert account_id, "No account_id in context — navigate to detail/edit first"
    ctx["response"] = env.post_edit(account_id, form_data)


@when(parsers.parse('the admin sends a status change request for "{name}" to "{new_status}"'))
def when_status_change(ctx: dict, name: str, new_status: str) -> None:
    """POST a JSON status change request."""
    env = _env(ctx)
    account_id = ctx.get(f"account_id:{name}") or env.get_account_id_by_name(name)
    assert account_id, f"No account found with name '{name}'"
    ctx["response"] = env.post_status_change(account_id, new_status)


# ═══════════════════════════════════════════════════════════════════════════
# THEN steps
# ═══════════════════════════════════════════════════════════════════════════


@then(parsers.parse("the page returns status {status_code:d}"))
def then_page_status(ctx: dict, status_code: int) -> None:
    """Assert HTTP status code."""
    response = ctx["response"]
    assert response.status_code == status_code, f"Expected status {status_code}, got {response.status_code}"


@then(parsers.parse('the page contains "{text}"'))
def then_page_contains(ctx: dict, text: str) -> None:
    """Assert page HTML contains the given text."""
    html = ctx["response"].data.decode()
    assert text in html, f"Page does not contain '{text}'"


@then(parsers.parse("the page shows {count:d} accounts"))
def then_page_shows_n_accounts(ctx: dict, count: int) -> None:
    """Assert the number of account rows shown on the list page."""
    html = ctx["response"].data.decode()
    # Count table rows in the tbody (each account is a <tr> in the accounts table)
    row_count = html.count("<tr onclick=")
    assert row_count == count, f"Expected {count} accounts, found {row_count}"


@then(parsers.parse('the page shows account "{name}" with status "{status}"'))
def then_page_shows_account(ctx: dict, name: str, status: str) -> None:
    """Assert the page shows an account with the given name and status badge."""
    html = ctx["response"].data.decode()
    assert name in html, f"Account '{name}' not found on page"
    # Status badge should be present
    badge_class = f"status-{status}"
    assert badge_class in html, f"Status badge '{badge_class}' not found"


@then(parsers.parse('the page does not show account "{name}"'))
def then_page_does_not_show_account(ctx: dict, name: str) -> None:
    """Assert the page does NOT show the named account."""
    html = ctx["response"].data.decode()
    assert name not in html, f"Account '{name}' should not be on page but was found"


@then(parsers.parse('the page shows the account status as "{status}"'))
def then_page_shows_status(ctx: dict, status: str) -> None:
    """Assert the detail page shows the expected status badge."""
    html = ctx["response"].data.decode()
    badge_class = f"status-{status}"
    assert badge_class in html, f"Status badge '{badge_class}' not found"


@then(parsers.parse('the page shows action buttons for "{buttons_str}"'))
def then_page_shows_action_buttons(ctx: dict, buttons_str: str) -> None:
    """Assert action buttons are present on the detail page."""
    html = ctx["response"].data.decode()
    buttons = [b.strip().strip('"') for b in buttons_str.split(" and ")]
    for button_text in buttons:
        assert f"onclick=\"changeStatus('{button_text.lower()}')" in html, (
            f"Action button for '{button_text}' not found"
        )


@then(parsers.parse('the page does not show action button for "{button_text}"'))
def then_page_no_action_button(ctx: dict, button_text: str) -> None:
    """Assert an action button is NOT present."""
    html = ctx["response"].data.decode()
    assert f"onclick=\"changeStatus('{button_text.lower()}')" not in html, (
        f"Action button for '{button_text}' should not be present"
    )


@then("the page does not show any status action buttons")
def then_page_no_action_buttons(ctx: dict) -> None:
    """Assert no status action buttons are shown (terminal state).

    Checks for onclick="changeStatus(...)" on buttons, not the JS function definition.
    """
    html = ctx["response"].data.decode()
    assert 'onclick="changeStatus(' not in html, "Found action buttons on a terminal-state account"


@then("the admin is redirected to the accounts list")
def then_redirected_to_list(ctx: dict) -> None:
    """Assert redirect to accounts list page."""
    response = ctx["response"]
    assert response.status_code in (302, 303), f"Expected redirect, got {response.status_code}"
    location = response.headers.get("Location", "")
    assert "/accounts/" in location or location.endswith("/accounts"), (
        f"Expected redirect to accounts list, got: {location}"
    )


@then("the admin is redirected to the account detail page")
def then_redirected_to_detail(ctx: dict) -> None:
    """Assert redirect to account detail page."""
    response = ctx["response"]
    assert response.status_code in (302, 303), f"Expected redirect, got {response.status_code}"
    location = response.headers.get("Location", "")
    assert "/accounts/" in location, f"Expected redirect to account page, got: {location}"


@then("the admin is redirected back to the create page")
def then_redirected_to_create(ctx: dict) -> None:
    """Assert redirect back to create page (validation failure)."""
    response = ctx["response"]
    assert response.status_code in (302, 303), f"Expected redirect, got {response.status_code}"
    location = response.headers.get("Location", "")
    assert "create" in location, f"Expected redirect to create page, got: {location}"


@then("the page returns a redirect to the login page")
def then_redirect_to_login(ctx: dict) -> None:
    """Assert unauthenticated users are redirected."""
    response = ctx["response"]
    assert response.status_code in (302, 303, 401), f"Expected redirect/unauthorized, got {response.status_code}"


@then(parsers.parse('the database contains an account named "{name}"'))
def then_db_has_account(ctx: dict, name: str) -> None:
    """Assert an account with the given name exists in DB."""
    env = _env(ctx)
    account = env.get_account_from_db(name=name)
    assert account is not None, f"Account '{name}' not found in database"


@then(parsers.parse('the database does not contain an account with brand domain "{domain}"'))
def then_db_no_account_with_domain(ctx: dict, domain: str) -> None:
    """Assert no account with the given brand domain exists."""
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Account

    env = _env(ctx)
    with get_db_session() as session:
        accounts = session.scalars(select(Account).where(Account.tenant_id == env.tenant_id)).all()
        for acct in accounts:
            if acct.brand and acct.brand.domain == domain:
                raise AssertionError(f"Found account with brand domain '{domain}' — should not exist")


@then(parsers.parse('the account "{name}" has brand domain "{domain}"'))
def then_account_has_brand_domain(ctx: dict, name: str, domain: str) -> None:
    """Assert account brand domain matches."""
    env = _env(ctx)
    account = env.get_account_from_db(name=name)
    assert account is not None, f"Account '{name}' not found"
    assert account.brand is not None, f"Account '{name}' has no brand"
    assert account.brand.domain == domain, f"Expected brand domain '{domain}', got '{account.brand.domain}'"


@then(parsers.parse('the database shows account "{name}" with billing "{billing}"'))
def then_db_account_billing(ctx: dict, name: str, billing: str) -> None:
    """Assert account billing field in DB."""
    env = _env(ctx)
    account = env.get_account_from_db(name=name)
    assert account is not None, f"Account '{name}' not found"
    assert account.billing == billing, f"Expected billing '{billing}', got '{account.billing}'"


@then(parsers.parse('the database shows account "{name}" with status "{status}"'))
def then_db_account_status(ctx: dict, name: str, status: str) -> None:
    """Assert account status in DB."""
    env = _env(ctx)
    account = env.get_account_from_db(name=name)
    assert account is not None, f"Account '{name}' not found"
    assert account.status == status, f"Expected status '{status}', got '{account.status}'"


@then(parsers.parse('the JSON response has "{key}" as true'))
def then_json_key_true(ctx: dict, key: str) -> None:
    """Assert JSON response key is true."""
    data = ctx["response"].get_json()
    assert data[key] is True, f"Expected {key}=true, got {data.get(key)}"


@then(parsers.parse('the JSON response has "{key}" as false'))
def then_json_key_false(ctx: dict, key: str) -> None:
    """Assert JSON response key is false."""
    data = ctx["response"].get_json()
    assert data[key] is False, f"Expected {key}=false, got {data.get(key)}"


@then(parsers.parse('the JSON response has "{key}" as "{value}"'))
def then_json_key_value(ctx: dict, key: str, value: str) -> None:
    """Assert JSON response key has specific value."""
    data = ctx["response"].get_json()
    assert data[key] == value, f"Expected {key}='{value}', got '{data.get(key)}'"


@then(parsers.parse('the JSON response has "{key}" containing "{substring}"'))
def then_json_key_contains(ctx: dict, key: str, substring: str) -> None:
    """Assert JSON response key contains a substring."""
    data = ctx["response"].get_json()
    assert substring in str(data.get(key, "")), f"Expected {key} to contain '{substring}', got '{data.get(key)}'"


@then(parsers.parse("the JSON response returns status {status_code:d}"))
def then_json_status(ctx: dict, status_code: int) -> None:
    """Assert JSON response HTTP status code."""
    response = ctx["response"]
    assert response.status_code == status_code, f"Expected status {status_code}, got {response.status_code}"
