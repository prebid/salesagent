"""The principal form enforces IDs and persists the issued token in a real browser."""

import re
import uuid

import pytest

from src.core.credentials import hash_token
from src.core.database.repositories.principal import PrincipalRepository
from tests.factories import UserFactory
from tests.helpers.credentials import displayed_token

pytestmark = pytest.mark.ui


def test_create_principal_browser_flow(member_page, base_url, server_seeding, server_tenant):
    principal_id = f"Buyer_{uuid.uuid4().hex}_longid"
    principal_name = f"Browser Principal {uuid.uuid4().hex[:6]}"
    email = f"principal-{uuid.uuid4().hex}@example.com"
    with server_seeding():
        tenant = server_tenant("principal", ad_server="mock")
        tenant_id = tenant.tenant_id
        UserFactory(tenant=tenant, role="admin", email=email)

    page = member_page(email, tenant_id)
    page.goto(f"{base_url}/tenant/{tenant_id}/principals/create", wait_until="domcontentloaded")
    field = page.locator("#principal_id")
    for invalid in (" buyer ", "two words", "a/b", "☃"):
        assert not field.evaluate("(el, value) => { el.value = value; return el.checkValidity(); }", invalid)
    field.fill("a" * 46)
    assert field.input_value() == "a" * 45
    field.fill(principal_id)
    assert field.evaluate("el => el.checkValidity()")
    page.locator("#name").fill(principal_name)
    page.get_by_role("button", name="Add Advertiser").click()
    page.wait_for_url(f"**/tenant/{tenant_id}/settings/advertisers", wait_until="domcontentloaded")
    page.wait_for_selector(f"text={principal_name}")
    token_notice = page.get_by_text(re.compile("Its API token, shown only now:")).inner_text()
    issued_token = displayed_token(token_notice)

    with server_seeding() as session:
        record = PrincipalRepository(session, tenant_id).get(principal_id)
        assert record is not None
        assert record.name == principal_name
        assert record.token_hash == hash_token(issued_token)
