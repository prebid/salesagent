"""Steps for the locally-added agent-card discovery feature.

WHAT MAKES THESE NON-VACUOUS. Every value asserted is read off the FETCHED CARD, and the
expected value is the tenant's stored ``virtual_host`` read from its row — so the
assertions compare two independently-sourced strings. The scenario that matters names a
Host the tenant does NOT store, which is the only shape that tells a read of the column
from an echo of the request.
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import then, when

#: The canonical A2A discovery path (A2A §8.2, §14.3). One path, because the scenario
#: grades what the card SAYS; that every declared path serves the same bytes is graded by
#: ``tests/e2e/test_a2a_endpoints_working.py``.
CARD_PATH = "/.well-known/agent-card.json"

#: A host this deployment serves no tenant at, under the TLD RFC 2606 reserves so it can
#: never resolve anywhere.
UNSERVED_HOST = "nobody-serves-this.invalid"


def _tenant_row(ctx: dict) -> Any:
    """The env's tenant row — the source of the origin these steps expect."""
    from sqlalchemy import select

    from src.core.database.models import Tenant

    env = ctx["env"]
    env._commit_factory_data()
    return env._session.scalars(select(Tenant).filter_by(tenant_id=env._tenant_id)).one()


def _fetched_card(ctx: dict) -> dict:
    """The parsed card body, or a loud failure naming what came back instead."""
    response = ctx["card_response"]
    assert response.status_code == 200, (
        f"the card fetch returned {response.status_code}, so there is no card to read: {response.text[:300]!r}"
    )
    return dict(response.json())


def _published_urls(card: dict) -> list[str]:
    return [interface["url"] for interface in card.get("supportedInterfaces") or []]


@when("the buyer fetches the agent card naming the seller by Host")
def when_fetch_by_host(ctx: dict) -> None:
    ctx["card_response"] = ctx["env"].fetch_agent_card(path=CARD_PATH, host=_tenant_row(ctx).virtual_host)


@when("the buyer fetches the agent card naming the seller by tenant header from another host")
def when_fetch_from_another_host(ctx: dict) -> None:
    """THE SCENARIO THAT SEPARATES A READ FROM AN ECHO.

    The tenant is named by ``x-adcp-tenant``, which frees the Host to carry something this
    deployment serves for nobody — so the stored origin and the requested host are two
    different strings and a reader that echoes the request is visible.
    """
    ctx["requested_host"] = UNSERVED_HOST
    ctx["card_response"] = ctx["env"].fetch_agent_card(
        path=CARD_PATH,
        host=UNSERVED_HOST,
        tenant=_tenant_row(ctx).tenant_id,
        host_resolves_nothing=True,
    )


@when("the buyer fetches the agent card naming a seller nobody serves")
def when_fetch_unserved(ctx: dict) -> None:
    ctx["requested_host"] = UNSERVED_HOST
    ctx["card_response"] = ctx["env"].fetch_agent_card(path=CARD_PATH, host=UNSERVED_HOST)


@then("the card publishes the origin the tenant stores")
def then_card_publishes_stored_origin(ctx: dict) -> None:
    stored = _tenant_row(ctx).virtual_host
    urls = _published_urls(_fetched_card(ctx))
    assert urls == [f"https://{stored}/a2a"], (
        f"the card published {urls}, but the host this tenant declares is {stored!r}. A card "
        f"naming anywhere else sends every A2A client to an address this seller does not answer"
    )


@then("the card's interface is one an A2A client selects")
def then_interface_is_selectable(ctx: dict) -> None:
    """A URL nothing selects is as unreachable as a wrong one.

    An A2A 1.x client picks its interface with
    ``i.protocolBinding?.toUpperCase() === "JSONRPC"`` (@a2a-js/sdk pick_interface.ts), so
    this mirrors the comparison the client makes. A card matching none of its own
    interfaces reports the agent UNREACHABLE without sending a request.
    """
    interfaces = _fetched_card(ctx).get("supportedInterfaces") or []
    bindings = [(interface.get("protocolBinding") or "").upper() for interface in interfaces]
    assert bindings == ["JSONRPC"], f"the card published protocolBinding {bindings}, which no A2A 1.x client selects"


@then("the card does not publish the host the request named")
def then_card_does_not_echo_the_host(ctx: dict) -> None:
    requested = ctx["requested_host"]
    body = ctx["card_response"].text
    assert requested not in body, (
        f"the card echoed {requested!r} — the caller's own Host came back as this agent's "
        f"advertised address, which on a direct connection is a value the caller chose"
    )


@then("no card is published")
def then_no_card(ctx: dict) -> None:
    response = ctx["card_response"]
    assert response.status_code != 200, (
        f"a request naming no tenant this deployment serves got a card: {response.text[:300]!r}. "
        f"With no tenant there is nothing truthful to publish"
    )
    assert "supportedInterfaces" not in response.text, (
        "the refusal published an agent interface built from the caller's own Host"
    )


@then("the refusal names a seller-side misconfiguration")
def then_refusal_is_configuration_error(ctx: dict) -> None:
    """The deployment cannot tell which seller the request is for, so it answers.

    Graded on the envelope rather than the status alone, and the host the request named
    must not appear: the pinned error-handling text gives this code no ``details`` shape,
    and the value reaches the operator through ``internal_detail``, never a wire.
    """
    from tests.helpers.envelope_assertions import assert_envelope_shape

    response = ctx["card_response"]
    # ``assert_envelope_shape`` rather than ``result.assert_wire_error``: the card is not
    # dispatched through ``call_via``, so there is no ``TransportResult`` -- this is the
    # bare-envelope case that helper exists for (tests/CLAUDE.md § Error verification).
    assert_envelope_shape(response.json(), "TENANT_UNDEFINED", recovery="terminal")
    assert ctx["requested_host"] not in response.text, (
        f"the refusal echoed the caller's own Host back to it: {response.text[:300]!r}"
    )
