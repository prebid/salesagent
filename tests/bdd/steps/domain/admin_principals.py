"""Read principal form responses and their persisted effects on either admin transport."""

import html
import json
import re
from urllib.parse import urlsplit

from pytest_bdd import given, parsers, then, when

from src.core.credentials import hash_token
from tests.bdd.steps._outcome_helpers import _require


def _page(ctx):
    return _require(ctx, "admin_page", hint="The advertiser form did not return a page.")


@given("the advertiser form is open")
def open_form(ctx):
    ctx["admin_page"] = ctx["env"].get_create_form()
    assert _page(ctx).status_code == 200


@given(parsers.parse('advertiser "{principal_id}" exists in {where} tenant'))
def seed_advertiser(ctx, principal_id, where):
    env = ctx["env"]
    tenant_id = f"{env.tenant_id}_other" if where == "another" else env.tenant_id
    env.seed_principal(principal_id=principal_id, tenant_id=tenant_id)
    if where == "this":
        ctx["original_token_hash"] = env.token_hash(principal_id)


@given("the advertiser operator signs out")
def sign_out(ctx):
    ctx["env"].clear_auth()


@when(parsers.parse("the advertiser form submits ID {id}"))
def submit_id(ctx, id):
    fields = {} if id == "omitted" else {"principal_id": json.loads(id)}
    ctx["admin_page"] = ctx["env"].post_create(**fields)


@when(parsers.re(r'the advertiser form submits field "(?P<field>[^"]+)" as "(?P<value>.*)"'))
def submit_field(ctx, field, value):
    ctx["admin_page"] = ctx["env"].post_create(principal_id="buyer", **{field: value})


@when(parsers.re(r'the advertiser edit submits field "(?P<field>[^"]+)" as "(?P<value>.*)"'))
def submit_edit(ctx, field, value):
    ctx["admin_page"] = ctx["env"].post_edit("buyer", **{"name": "Replacement", "enable_mock": "1", field: value})


@then(parsers.parse("the advertiser is stored with ID {id} and the displayed token"))
def accepted(ctx, id):
    env = ctx["env"]
    response = _page(ctx)
    assert response.status_code == 302
    assert urlsplit(response.headers["Location"]).path == f"/tenant/{env.tenant_id}/settings/advertisers"
    page = env.follow(response)
    assert page.status_code == 200
    token = re.search(r"shown only now: (tok_[A-Za-z0-9_-]+)", page.data.decode())
    assert token is not None, "The successful form must show the actual issued token"
    (row,) = env.principals(name="Created Advertiser")
    assert row.principal_id == json.loads(id)
    assert row.token_hash == hash_token(token.group(1))
    assert row.platform_mappings["mock"]["advertiser_id"] == f"mock_{row.principal_id}"
    assert token.group(1).encode() not in env.follow(response).data, "The plaintext token is shown once"


@then("the advertiser create audit records success")
def create_success(ctx):
    assert [row.success for row in ctx["env"].audit_rows("AdminUI.create_principal", method="POST")] == [True]


@then(parsers.parse('the advertiser {operation} is refused with "{message}"'))
def refused(ctx, operation, message):
    env = ctx["env"]
    response = _page(ctx)
    assert response.status_code == 302
    suffix = "create" if operation == "create" else "buyer/edit"
    assert urlsplit(response.headers["Location"]).path == f"/tenant/{env.tenant_id}/principals/{suffix}"
    page = env.follow(response)
    assert page.status_code == 200
    assert message in html.unescape(page.data.decode())
    assert b"shown only now:" not in page.data
    assert env.principals(name="Created Advertiser") == []
    assert env.principals(name="Replacement") == []
    (row,) = env.audit_rows(f"AdminUI.{operation}_principal", method="POST")
    assert not row.success
    assert row.error_message == message


@then("the original advertiser credential is unchanged")
def unchanged(ctx):
    assert ctx["env"].token_hash("buyer") == ctx["original_token_hash"]


@then("the advertiser post requires login without an audit success")
def requires_login(ctx):
    response = _page(ctx)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert ctx["env"].principals(name="Created Advertiser") == []
    assert ctx["env"].audit_rows("AdminUI.create_principal", method="POST") == []


@then("the advertiser form exposes the shared ID rule")
def form_rule(ctx):
    from src.admin.blueprints.principals import ADVERTISER_ID_MAX_LENGTH, ADVERTISER_ID_PATTERN

    (field,) = re.findall(r'<input[^>]*\bid="principal_id"[^>]*>', _page(ctx).data.decode())
    assert f'maxlength="{ADVERTISER_ID_MAX_LENGTH}"' in field
    assert f'pattern="{ADVERTISER_ID_PATTERN}"' in field
