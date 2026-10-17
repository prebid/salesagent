"""Steps for local-publisher-authorization.feature.

WHAT MAKES THESE NON-VACUOUS. The publisher's entry is written from the tenant's own
``agent_url``, read off its row, so every accepted spelling names the agent the admin
route compares against and every refused one differs from it by exactly what its row
says. Each Then reads what the route wrote (the partner row, the property rows) or what
it answered (the properties view's JSON), never a value a Given set: the partner factory
seeds ``is_verified=True``, so "verified" is read together with ``last_synced_at``, which
only a successful sync writes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.domain.admin_accounts import _require_admin_page
from tests.bdd.steps.generic._table import quoted_list

if TYPE_CHECKING:
    from tests.harness.publisher_authorization import PublisherAuthorizationEnv


def _env(ctx: dict) -> PublisherAuthorizationEnv:
    return ctx["env"]


def _spell(template: str, agent_url: str) -> str:
    """*template* with ``{origin}``, ``{ORIGIN}`` and ``{host}`` taken from *agent_url*."""
    return template.format(origin=agent_url, ORIGIN=agent_url.upper(), host=urlsplit(agent_url).netloc)


# ── Given ─────────────────────────────────────────────────────────────────


@given("the seller does not auto-verify publisher partners")
def given_no_publisher_auto_verify(ctx: dict) -> None:
    _env(ctx).disable_publisher_auto_verify()


@given(parsers.parse('the tenant runs the "{adapter_type}" ad server'))
def given_ad_server(ctx: dict, adapter_type: str) -> None:
    _env(ctx).run_ad_server(adapter_type)


@given(parsers.parse('the publisher "{publisher}" lists property "{property_id}" named "{name}" in its adagents.json'))
def given_publisher_lists_property(ctx: dict, publisher: str, property_id: str, name: str) -> None:
    _env(ctx).adagents_document(publisher)["properties"].append(
        {
            "property_id": property_id,
            "property_type": "website",
            "name": name,
            # The domain the file lists is the domain it is served from: property sync keeps
            # only the properties whose domain identifier matches the publisher it fetched.
            "identifiers": [{"type": "domain", "value": _env(ctx).publisher_address(publisher)}],
        }
    )


@given(parsers.parse('the publisher "{publisher}" authorizes "{entry}" for property "{property_id}"'))
def given_publisher_authorizes(ctx: dict, publisher: str, entry: str, property_id: str) -> None:
    _env(ctx).adagents_document(publisher)["authorized_agents"].append(
        {
            "url": _spell(entry, ctx["tenant"].agent_url),
            "authorized_for": "Display inventory",
            "authorization_type": "property_ids",
            "property_ids": [property_id],
        }
    )


@given(parsers.parse('the tenant has authorized property "{property_id}" of "{publisher}" pending verification'))
def given_pending_property(ctx: dict, property_id: str, publisher: str) -> None:
    _env(ctx).pending_property(property_id=property_id, publisher=publisher)


# ── When ──────────────────────────────────────────────────────────────────


@when("the operator syncs publisher partners")
def when_sync(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).sync_publisher_partners()


@when("the operator verifies the pending authorized properties")
def when_verify(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).verify_pending_properties()


@when(parsers.parse('the operator opens the properties of the partnership with "{publisher}"'))
def when_open_properties(ctx: dict, publisher: str) -> None:
    ctx["admin_page"] = _env(ctx).open_partner_properties(publisher)


# ── Then ──────────────────────────────────────────────────────────────────


def _admin_body(ctx: dict) -> dict:
    response = _require_admin_page(ctx)
    assert response.status_code == 200, f"the admin route answered {response.status_code}: {response.data[:300]!r}"
    return dict(response.get_json())


@then(parsers.parse('the partnership with "{publisher}" is verified'))
def then_partnership_verified(ctx: dict, publisher: str) -> None:
    body = _admin_body(ctx)
    partner = _env(ctx).partner(publisher)
    assert (partner.is_verified, partner.sync_status, partner.sync_error) == (True, "success", None), (
        f"the sync recorded {publisher!r} as is_verified={partner.is_verified}, "
        f"sync_status={partner.sync_status!r}, sync_error={partner.sync_error!r} (response {body})"
    )
    assert partner.last_synced_at is not None, f"no successful sync of {publisher!r} was recorded (response {body})"


@then(parsers.parse('the partnership with "{publisher}" is refused'))
def then_partnership_refused(ctx: dict, publisher: str) -> None:
    body = _admin_body(ctx)
    agent_url = ctx["tenant"].agent_url
    partner = _env(ctx).partner(publisher)
    assert (partner.is_verified, partner.sync_status) == (False, "error"), (
        f"the sync recorded {publisher!r} as is_verified={partner.is_verified}, "
        f"sync_status={partner.sync_status!r} (response {body})"
    )
    assert partner.sync_error == f"Agent {agent_url} is not authorized by this publisher", (
        f"the partnership was refused for another reason: {partner.sync_error!r}"
    )


@then(parsers.parse('the tenant holds properties {names} from "{publisher}"'))
def then_holds_properties(ctx: dict, names: str, publisher: str) -> None:
    held = sorted(prop.name for prop in _env(ctx).properties_from(publisher))
    assert held == sorted(quoted_list(names)), f"the tenant holds {held} from {publisher!r}"


@then(parsers.parse('the tenant holds the fallback property from "{publisher}"'))
def then_holds_fallback_property(ctx: dict, publisher: str) -> None:
    """The one property a sync names after the publisher when the file gives this agent none."""
    address = _env(ctx).publisher_address(publisher)
    held = [(prop.name, prop.verification_status) for prop in _env(ctx).properties_from(publisher)]
    assert held == [(address, "verified")], f"the tenant holds {held} from {publisher!r}, expected only the fallback"


@then(parsers.parse('the tenant holds no properties from "{publisher}"'))
def then_holds_no_properties(ctx: dict, publisher: str) -> None:
    held = [prop.name for prop in _env(ctx).properties_from(publisher)]
    assert held == [], f"the tenant holds {held} from {publisher!r}, from a file that does not authorize it"


@then(parsers.parse('authorized property "{property_id}" is {status}'))
def then_property_status(ctx: dict, property_id: str, status: str) -> None:
    prop = _env(ctx).authorized_property(property_id)
    assert prop.verification_status == status, (
        f"property {property_id!r} is {prop.verification_status!r} "
        f"(verification_error={prop.verification_error!r}), expected {status!r}"
    )


@then(parsers.parse('the view reports this agent authorized for "{property_id}"'))
def then_view_authorized(ctx: dict, property_id: str) -> None:
    body = _admin_body(ctx)
    assert (body.get("is_authorized"), body.get("property_ids")) == (True, [property_id]), body


@then("the view reports this agent not authorized")
def then_view_not_authorized(ctx: dict) -> None:
    body = _admin_body(ctx)
    agent_url = ctx["tenant"].agent_url
    assert body == {"error": f"Agent {agent_url} is not authorized by this publisher", "is_authorized": False}, body
