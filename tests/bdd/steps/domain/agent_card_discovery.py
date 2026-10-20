"""Steps for the root documents a seller publishes at its host: the agent card and adagents.json.

Both are fetched the same way (``env.fetch_agent_card``, which takes any root path), read
the same way (:func:`_fetched_document`), and refused the same way ("no <document> is
published"), so one step serves each feature; only the document's path, the key that only
a real document carries, and the status of its refusal differ (:data:`_DOCUMENTS`).

WHAT MAKES THESE NON-VACUOUS. Every value asserted is read off the FETCHED document, and the
expected value is read from the tenant's row, so the assertions compare two
independently-sourced strings. The card scenario that matters names a Host the tenant does
NOT store, which is the only shape that tells a read of the column from an echo of the
request. The adagents.json scenarios always seed a property on ANOTHER publisher's domain,
so a lookup that dropped the domain filter would claim it; the own-host property is
pending, so a lookup that filtered on verification would serve nothing; and the
host-on-a-port scenario stores the port in ``virtual_host``, so a lookup by
``virtual_host`` instead of its hostname finds no property and answers 404.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pytest_bdd import given, parsers, then, when

#: The canonical A2A discovery path (A2A §8.2, §14.3). One path, because the scenario
#: grades what the card SAYS; that every declared path serves the same bytes is graded by
#: ``tests/e2e/test_a2a_endpoints_working.py``.
CARD_PATH = "/.well-known/agent-card.json"

ADAGENTS_PATH = "/.well-known/adagents.json"


@dataclass(frozen=True)
class _RootDocument:
    path: str
    #: A key only a real document carries, so a refusal body that carries it published one.
    marker: str
    #: What the route answers when it publishes no document. The card's refusal is
    #: TENANT_UNDEFINED (421, CODE_TABLE); adagents.json at a host that owns no property is
    #: a missing file (404), which a buyer reads as "not authorized here"
    #: (authorized-properties.mdx "Missing adagents.json: Treat as unauthorized").
    absent_status: int


_DOCUMENTS = {
    "agent card": _RootDocument(path=CARD_PATH, marker="supportedInterfaces", absent_status=421),
    "adagents.json": _RootDocument(path=ADAGENTS_PATH, marker="authorized_agents", absent_status=404),
}
_DOCUMENT_NAMES = "|".join(name.replace(".", r"\.") for name in _DOCUMENTS)

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


def _fetch(ctx: dict, document: str, **naming: Any) -> None:
    """GET *document*, naming the seller as *naming* says, and keep the response."""
    ctx["document"] = document
    ctx["document_response"] = ctx["env"].fetch_agent_card(path=_DOCUMENTS[document].path, **naming)


def _fetched_document(ctx: dict) -> dict:
    """The parsed document body, or a loud failure naming what came back instead."""
    response = ctx["document_response"]
    assert response.status_code == 200, (
        f"GET {_DOCUMENTS[ctx['document']].path} returned {response.status_code}, so there is no "
        f"{ctx['document']} to read: {response.text[:300]!r}"
    )
    return dict(response.json())


def _published_urls(card: dict) -> list[str]:
    return [interface["url"] for interface in card.get("supportedInterfaces") or []]


@when(parsers.re(rf"the buyer fetches (?:the )?(?P<document>{_DOCUMENT_NAMES}) naming the seller by Host"))
def when_fetch_by_host(ctx: dict, document: str) -> None:
    _fetch(ctx, document, host=_tenant_row(ctx).virtual_host)


@when("the buyer fetches the agent card naming the seller by tenant header from another host")
def when_fetch_from_another_host(ctx: dict) -> None:
    """THE SCENARIO THAT SEPARATES A READ FROM AN ECHO.

    The tenant is named by ``x-adcp-tenant``, which frees the Host to carry something this
    deployment serves for nobody — so the stored origin and the requested host are two
    different strings and a reader that echoes the request is visible.
    """
    ctx["requested_host"] = UNSERVED_HOST
    _fetch(ctx, "agent card", host=UNSERVED_HOST, tenant=_tenant_row(ctx).tenant_id, host_resolves_nothing=True)


@when("the buyer fetches the agent card naming a seller nobody serves")
def when_fetch_unserved(ctx: dict) -> None:
    ctx["requested_host"] = UNSERVED_HOST
    _fetch(ctx, "agent card", host=UNSERVED_HOST)


@then("the card publishes the origin the tenant stores")
def then_card_publishes_stored_origin(ctx: dict) -> None:
    stored = _tenant_row(ctx).virtual_host
    urls = _published_urls(_fetched_document(ctx))
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
    interfaces = _fetched_document(ctx).get("supportedInterfaces") or []
    bindings = [(interface.get("protocolBinding") or "").upper() for interface in interfaces]
    assert bindings == ["JSONRPC"], f"the card published protocolBinding {bindings}, which no A2A 1.x client selects"


@then("the card does not publish the host the request named")
def then_card_does_not_echo_the_host(ctx: dict) -> None:
    requested = ctx["requested_host"]
    body = ctx["document_response"].text
    assert requested not in body, (
        f"the card echoed {requested!r} — the caller's own Host came back as this agent's "
        f"advertised address, which on a direct connection is a value the caller chose"
    )


@then(parsers.re(rf"no (?P<document>{_DOCUMENT_NAMES}) is published"))
def then_no_document(ctx: dict, document: str) -> None:
    """The route publishes nothing: its refusal status, and no document's body.

    A card needs a tenant, so a request naming none this deployment serves gets nothing
    truthful. An adagents.json needs the host to own a property: with neither sales
    authorization nor catalog content the pinned schema rejects the file, and the missing
    file is what tells a buyer "not authorized here".
    """
    expected = _DOCUMENTS[document]
    assert ctx["document"] == document, f"the step grades the {document}, but the fetch was of the {ctx['document']}"
    response = ctx["document_response"]
    assert response.status_code == expected.absent_status, (
        f"GET {expected.path} must publish no {document} here and answer {expected.absent_status}; "
        f"got {response.status_code} {response.text[:300]!r}"
    )
    assert expected.marker not in response.text, (
        f"the refusal at {expected.path} carried a {document} body ({expected.marker!r}): {response.text[:300]!r}"
    )


@then("the refusal names a seller-side misconfiguration")
def then_refusal_is_configuration_error(ctx: dict) -> None:
    """The deployment cannot tell which seller the request is for, so it answers.

    Graded on the envelope rather than the status alone, and the host the request named
    must not appear: the pinned error-handling text gives this code no ``details`` shape,
    and the value reaches the operator through ``internal_detail``, never a wire.
    """
    from tests.helpers.envelope_assertions import assert_envelope_shape

    response = ctx["document_response"]
    # ``assert_envelope_shape`` rather than ``result.assert_wire_error``: the card is not
    # dispatched through ``call_via``, so there is no ``TransportResult`` -- this is the
    # bare-envelope case that helper exists for (tests/CLAUDE.md § Error verification).
    assert_envelope_shape(response.json(), "TENANT_UNDEFINED", recovery="terminal")
    assert ctx["requested_host"] not in response.text, (
        f"the refusal echoed the caller's own Host back to it: {response.text[:300]!r}"
    )


@given(parsers.parse("the tenant is served on port {port:d} of its host"))
def given_tenant_served_on_port(ctx: dict, port: int) -> None:
    """Store the origin with a port, the way an operator declares a non-default one.

    ``configure_tenant_field`` writes the ``tenants`` row, so its ``virtual_host``
    validator re-derives the stored hostname, and the live server reads the same row.
    """
    ctx["env"].configure_tenant_field("virtual_host", f"{_tenant_row(ctx).virtual_host_name}:{port}")


@then("the adagents.json validates against the pinned schema")
def then_adagents_schema_valid(ctx: dict) -> None:
    """Graded twice on purpose: jsonschema against the pinned 3.1.1 file, and
    ``validate_adagents_structure``, the function this seller's own admin code runs when it
    consumes a PUBLISHER's adagents.json."""
    from adcp.adagents import validate_adagents_structure

    from tests.helpers.adcp_pin import EXPECTED_SPEC_VERSION
    from tests.helpers.pinned_schema import validate_against_pinned_schema

    document = _fetched_document(ctx)
    validate_against_pinned_schema(f"{EXPECTED_SPEC_VERSION}/adagents.json", document)
    report = validate_adagents_structure(document)
    assert report.schema_valid, f"this seller's own consumer path rejects the document it publishes: {report.errors}"


@then("the adagents.json claims only the property on the tenant's own host")
def then_adagents_claims_own_host_only(ctx: dict) -> None:
    own_host = _tenant_row(ctx).virtual_host_name
    claimed = [
        prop["publisher_domain"]
        for entry in _fetched_document(ctx)["authorized_agents"]
        for prop in entry.get("properties") or []
    ]
    assert claimed == [own_host], (
        f"the document served at {own_host!r} claimed {claimed}. It speaks for the one property on that "
        f"host and no other: a property on another publisher's domain is authorized by that "
        f"publisher's own adagents.json"
    )
