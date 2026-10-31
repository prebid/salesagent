"""UC-005: a creative agent's catalog is parsed entry by entry, and a discard is reported.

Scenarios ``T-UC-005-peer-catalog-partial-discard``, ``-empty`` and ``-all-discarded``
(#1333), ``T-UC-005-peer-catalog-unreachable`` (an agent that does not answer costs its own
formats and is reported), and ``T-UC-005-own-agents-only``: a seller that does not
federate the standard creative agent lists, and refers buyers to, only its own. The peer-catalog Givens hand
``CreativeFormatsEnv.set_peer_format_payload`` the agent's ``formats`` array as RAW JSON,
because the obligation is about what the seller does with
a document it did not write: in process the env serves it from a real MCP endpoint and
production's own registry dials it, and over e2e_rest the compose stack's capture service
serves it to the live server. Either way the seller's parse is the one under test.

The Thens read the wire. The agent's formats are located by the ``agent_url`` the agent
published inside each ``format_id``, so the reference catalog the default agent answers
with never stands in for them.

@source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/list-creative-formats-response.json pointer=/properties/errors
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, then

from src.core.schemas import FormatId, canonical_agent_url
from tests.bdd.steps._outcome_helpers import wire_advisory_errors, wire_field
from tests.factories.format import FormatFactory

#: The identity the agent publishes on its formats. It is not where the agent is
#: dialled -- the env decides that -- which is what lets one value serve both worlds.
PEER_AGENT_URL = "https://peer-creatives.example"


def _peer_entry(format_id: str, **fields: Any) -> dict[str, Any]:
    """One entry of the agent's catalog, exactly as the agent sends it."""
    return {"format_id": {"agent_url": PEER_AGENT_URL, "id": format_id}, **fields}


#: Conforming, with members this pin's enums do not model in two closed-enum lists. Its
#: name sorts ahead of every reference-catalog name, so the default page carries it.
_CONFORMING = _peer_entry(
    "acme_peer_banner",
    name="Acme Peer Banner",
    accepts_parameters=["future_parameter"],
    reported_metrics=["impressions", "future_metric"],
)
#: ``name`` is required by core/format.json.
_MISSING_NAME = _peer_entry("acme_peer_unnamed")
#: A non-string member is a type error, not an unknown enum value, so it is not tolerated.
_NON_STRING_PARAMETER = _peer_entry("acme_peer_numeric", name="Acme Peer Numeric", accepts_parameters=[42])


@given("a creative agent whose catalog includes one entry the pinned schema cannot validate")
def given_peer_catalog_with_one_invalid_entry(ctx: dict) -> None:
    ctx["env"].set_peer_format_payload([_CONFORMING, _MISSING_NAME])


@given("a creative agent whose catalog has only entries the pinned schema cannot validate")
def given_peer_catalog_all_invalid(ctx: dict) -> None:
    ctx["env"].set_peer_format_payload([_MISSING_NAME, _NON_STRING_PARAMETER])


@given("a creative agent whose catalog has no entries")
def given_peer_catalog_empty(ctx: dict) -> None:
    ctx["env"].set_peer_format_payload([])


@given(
    parsers.parse(
        'the seller does not federate the standard creative agent and its own creative agent serves format "{name}"'
    )
)
def given_seller_lists_only_its_own_agent(ctx: dict, name: str) -> None:
    """The seller's catalog is its own agent's, and the standard agent is turned off.

    ``set_registry_formats`` is that configuration on every transport: the tenant's
    ``use_default_creative_agent`` column is false, its one ``creative_agents`` row names
    the peer, and the peer serves exactly this format.
    """
    own = FormatFactory(name=name, format_id=FormatId(agent_url=PEER_AGENT_URL, id=name.replace("-", "_")))
    ctx["env"].set_registry_formats([own])


def _peer_formats(ctx: dict) -> list[dict[str, Any]]:
    """The formats on the wire that the agent published, by the agent_url in their format_id."""
    formats = wire_field(ctx, "formats")
    assert isinstance(formats, list), f"formats is not a JSON array on the wire: {formats!r}"
    peer = canonical_agent_url(PEER_AGENT_URL)
    return [f for f in formats if canonical_agent_url(f["format_id"]["agent_url"]) == peer]


def _peer_format_ids(ctx: dict) -> list[str]:
    return [f["format_id"]["id"] for f in _peer_formats(ctx)]


@then(parsers.parse('only the creative agent\'s format "{format_id}" should be returned'))
def then_only_peer_format_returned(ctx: dict, format_id: str) -> None:
    returned = _peer_format_ids(ctx)
    assert returned == [format_id], f"expected exactly the agent's format {format_id!r}, got {returned}"


@then("no formats from the creative agent should be returned")
def then_no_peer_formats_returned(ctx: dict) -> None:
    returned = _peer_format_ids(ctx)
    assert returned == [], f"expected no formats from the agent, got {returned}"


@then(parsers.parse('the creative agent\'s format should carry {field} "{values}"'))
def then_peer_format_carries(ctx: dict, field: str, values: str) -> None:
    """The list arrives exactly as the agent sent it, members the pin does not model included."""
    (entry,) = _peer_formats(ctx)
    expected = values.split(", ")
    assert entry.get(field) == expected, f"{field}: expected {expected}, got {entry.get(field)!r}"


@then("the creative_agents referrals should name only the seller's own creative agent")
def then_referrals_name_only_own_agent(ctx: dict) -> None:
    """The one referral is the agent the tenant configured; the standard agent is not referred to."""
    referrals = wire_field(ctx, "creative_agents")
    assert isinstance(referrals, list), f"creative_agents is not a JSON array on the wire: {referrals!r}"
    referred = [canonical_agent_url(str(referral["agent_url"])) for referral in referrals]
    expected = [canonical_agent_url(ctx["env"].peer_agent_url())]
    assert referred == expected, f"expected referrals to name only {expected}, got {referred}"


@given(parsers.parse("the seller's products declare the standard creative agent's format \"{format_id}\""))
def given_products_declare_reference_format(ctx: dict, format_id: str) -> None:
    """The tenant's product declares *format_id* at the standard creative agent, which stays federated."""
    ctx["env"].declare_reference_formats(format_id)


@given("a creative agent the seller configured does not answer")
def given_configured_agent_unreachable(ctx: dict) -> None:
    """The tenant's one peer ``creative_agents`` row names an agent nothing answers at.

    ``unreachable_creative_agent_url`` is that agent in both worlds: on e2e nothing listens
    on its port, and in process its connect is refused below the seller's own code, so the
    listing's MCP dial to it fails exactly where a real dial would.
    """
    env = ctx["env"]
    env.configure_peer_agent(env.unreachable_creative_agent_url(), owner="unreachable creative agent")


@then(
    parsers.re(
        r'the (?:discarded entries|unreachable creative agent) should be reported as "(?P<code>[^"]+)"'
        r' on "(?P<section>[^"]+)" with recovery "(?P<recovery>[^"]+)"'
    )
)
def then_agent_loss_reported(ctx: dict, code: str, section: str, recovery: str) -> None:
    """Exactly one advisory per agent that cost the buyer formats, however many: it names the loss."""
    reported = [{key: error.get(key) for key in ("code", "field", "recovery")} for error in wire_advisory_errors(ctx)]
    assert reported == [{"code": code, "field": section, "recovery": recovery}], (
        f"expected one {code} advisory on {section!r}, got {reported}"
    )
