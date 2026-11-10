"""Steps for what publishers serve in their adagents.json, and what the seller made of it.

Shared by local-publisher-authorization.feature and the get_products / admin scenarios.
Every ingest goes through the operator's admin routes on the env's own transport; on e2e
the live server fetches the file itself (``PublisherAdagentsMixin``).

WHAT MAKES THESE NON-VACUOUS. A publisher's entry is written from the tenant's own
``agent_url``, read off its row, so every accepted spelling names the agent the ingest
compares against and every refused one differs from it by exactly what its row says. Each
Then reads what the ingest stored (the publisher row, the derived property rows) or what a
route answered, never a value a Given set.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.domain.admin_accounts import _require_admin_page
from tests.bdd.steps.domain.uc010_capabilities import _quoted_list

if TYPE_CHECKING:
    from tests.harness.publisher_authorization import PublisherAdagentsMixin, PublisherAuthorizationEnv


def _env(ctx: dict) -> PublisherAuthorizationEnv:
    return ctx["env"]


def _publishers(ctx: dict) -> PublisherAdagentsMixin:
    return ctx["env"]


def _spell(template: str, agent_url: str) -> str:
    """*template* with ``{origin}``, ``{ORIGIN}`` and ``{host}`` taken from *agent_url*."""
    return template.format(origin=agent_url, ORIGIN=agent_url.upper(), host=urlsplit(agent_url).netloc)


def _agent_url(ctx: dict) -> str:
    return ctx["tenant"].agent_url


# ── Given: what the publisher's file says ────────────────────────────────


@given(parsers.parse('the publisher "{publisher}" lists property "{property_id}" named "{name}" in its adagents.json'))
def given_publisher_lists_property(ctx: dict, publisher: str, property_id: str, name: str) -> None:
    _publishers(ctx).list_property(publisher, property_id)
    _publishers(ctx).adagents_document(publisher)["properties"][-1]["name"] = name


@given(parsers.parse('the publisher "{publisher}" lists property "{property_id}" tagged "{tag}" in its adagents.json'))
def given_publisher_lists_tagged_property(ctx: dict, publisher: str, property_id: str, tag: str) -> None:
    _publishers(ctx).list_property(publisher, property_id, tags=[tag])


@given(
    parsers.parse('the publisher "{publisher}" lists property "{property_id}" without identifiers in its adagents.json')
)
def given_publisher_lists_property_without_identifiers(ctx: dict, publisher: str, property_id: str) -> None:
    """A property core/property.json refuses: ``identifiers`` is required."""
    _publishers(ctx).list_property(publisher, property_id)
    del _publishers(ctx).adagents_document(publisher)["properties"][-1]["identifiers"]


@given(parsers.parse('the publisher "{publisher}" serves an adagents.json without authorized_agents'))
def given_publisher_serves_no_authorized_agents(ctx: dict, publisher: str) -> None:
    """A file-level failure: the required top-level ``authorized_agents`` is absent."""
    del _publishers(ctx).adagents_document(publisher)["authorized_agents"]


@given(parsers.parse('the publisher "{publisher}" authorizes "{entry}" for property "{property_id}"'))
def given_publisher_authorizes(ctx: dict, publisher: str, entry: str, property_id: str) -> None:
    _publishers(ctx).authorize(
        publisher,
        _spell(entry, _agent_url(ctx)),
        authorization_type="property_ids",
        property_ids=[property_id],
    )


@given(parsers.re(r'the publisher "(?P<publisher>[^"]+)" authorizes this agent for properties (?P<property_ids>".+)'))
def given_publisher_authorizes_ids(ctx: dict, publisher: str, property_ids: str) -> None:
    """A ``property_ids`` entry: resolved against the file's top-level ``properties`` (adagents.json :261)."""
    _publishers(ctx).authorize(
        publisher, _agent_url(ctx), authorization_type="property_ids", property_ids=_quoted_list(property_ids)
    )


@given(parsers.parse('the publisher "{publisher}" authorizes this agent for properties tagged "{tag}"'))
def given_publisher_authorizes_tag(ctx: dict, publisher: str, tag: str) -> None:
    """A ``property_tags`` entry: every top-level property carrying the tag (adagents.json :347)."""
    _publishers(ctx).authorize(publisher, _agent_url(ctx), authorization_type="property_tags", property_tags=[tag])


@given(parsers.parse('the publisher "{publisher}" authorizes this agent for an inline property "{property_id}"'))
def given_publisher_authorizes_inline(ctx: dict, publisher: str, property_id: str) -> None:
    """An ``inline_properties`` entry: the entry's own ``properties`` (adagents.json :433)."""
    _publishers(ctx).authorize(
        publisher,
        _agent_url(ctx),
        authorization_type="inline_properties",
        properties=[
            {
                "property_id": property_id,
                "property_type": "website",
                "name": property_id,
                "identifiers": [{"type": "domain", "value": _publishers(ctx).publisher_address(publisher)}],
            }
        ],
    )


@given(
    parsers.parse(
        'the publisher "{publisher}" authorized this agent for property "{property_id}" until {hours:d} hours ago'
    )
)
def given_publisher_authorization_expired(ctx: dict, publisher: str, property_id: str, hours: int) -> None:
    """A ``property_ids`` entry whose ``effective_until`` is in the past."""
    until = (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _publishers(ctx).authorize(
        publisher,
        _agent_url(ctx),
        authorization_type="property_ids",
        property_ids=[property_id],
        effective_until=until,
    )


@given(parsers.parse('the publisher "{publisher}" lists its own domain in revoked_publisher_domains'))
def given_publisher_revokes_itself(ctx: dict, publisher: str) -> None:
    _publishers(ctx).adagents_document(publisher)["revoked_publisher_domains"] = [
        {"publisher_domain": _publishers(ctx).publisher_address(publisher), "revoked_at": "2026-01-01T00:00:00Z"}
    ]


@given(parsers.parse('the publisher "{publisher}" authorizes no agent in its adagents.json'))
def given_publisher_authorizes_nobody(ctx: dict, publisher: str) -> None:
    _publishers(ctx).adagents_document(publisher)["authorized_agents"] = []


@given(parsers.parse('the publisher "{publisher}" declares format option {declaration} in its adagents.json'))
def given_publisher_declares_format_option(ctx: dict, publisher: str, declaration: str) -> None:
    """One ``formats[]`` entry: a product-format-declaration, its scope included when written."""
    _publishers(ctx).declare_format_option(publisher, json.loads(declaration))


@given(parsers.parse('the publisher "{publisher}" dates its adagents.json "{last_updated}"'))
def given_publisher_last_updated(ctx: dict, publisher: str, last_updated: str) -> None:
    _publishers(ctx).adagents_document(publisher)["last_updated"] = last_updated


@given(parsers.parse('the publisher "{publisher}" stops listing property "{property_id}"'))
def given_publisher_unlists(ctx: dict, publisher: str, property_id: str) -> None:
    _publishers(ctx).unlist_property(publisher, property_id)


@given(parsers.parse('the publisher "{publisher}" now answers {status:d}'))
def given_publisher_answers(ctx: dict, publisher: str, status: int) -> None:
    _publishers(ctx).answer_with_status(publisher, status)


# ── Given / When: the seller's ingest ───────────────────────────────────


@given(parsers.parse('the seller ingested the adagents.json of "{publisher}"'))
@when(parsers.parse('the seller refreshes the adagents.json of "{publisher}"'))
def when_seller_ingests(ctx: dict, publisher: str) -> None:
    """The seller ingests *publisher*'s file; what it held before is kept for the Thens that compare."""
    stored = _publishers(ctx).stored_publisher_or_none(publisher)
    if stored is not None:
        ctx.setdefault("stored_before", {})[publisher] = (stored.adagents, stored.fetched_at)
    _publishers(ctx).ingest_publisher(publisher)


@given(parsers.parse('the seller\'s last successful fetch of "{publisher}" was {hours:d} hours ago'))
def given_last_fetch_moved_back(ctx: dict, publisher: str, hours: int) -> None:
    """Move time on through the harness clock: the stored document's ``fetched_at`` moves back."""
    stored = _publishers(ctx).stored_publisher(publisher)
    assert stored.fetched_at is not None, f"the seller never fetched {publisher!r}"
    elapsed = (datetime.now(UTC) - stored.fetched_at).total_seconds() / 3600
    _publishers(ctx).move_last_fetch_back(publisher, hours - elapsed)


# ── When: the operator's actions ────────────────────────────────────────


@given(parsers.parse('the operator added the publisher "{publisher}"'))
@when(parsers.parse('the operator adds the publisher "{publisher}"'))
def when_add_publisher(ctx: dict, publisher: str) -> None:
    ctx["admin_page"] = _env(ctx).add_publisher(publisher)


@when(parsers.parse('the operator removes the publisher "{publisher}"'))
def when_remove_publisher(ctx: dict, publisher: str) -> None:
    response = _publishers(ctx).remove_publisher(publisher)
    assert response.status_code == 200, f"removing {publisher!r} answered {response.status_code}: {response.data!r}"


@when("the operator syncs publisher partners")
def when_sync(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).sync_publisher_partners()


@when("the operator opens the publishers page")
def when_open_publishers(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).open_publishers()


@when("the operator opens the authorized properties page")
def when_open_authorized_properties(ctx: dict) -> None:
    ctx["admin_page"] = _env(ctx).open_authorized_properties()


@when(parsers.parse('the operator opens the properties of the partnership with "{publisher}"'))
def when_open_properties(ctx: dict, publisher: str) -> None:
    ctx["admin_page"] = _env(ctx).open_partner_properties(publisher)


# ── Then ──────────────────────────────────────────────────────────────────


def _admin_body(ctx: dict, *statuses: int) -> dict:
    response = _require_admin_page(ctx)
    assert response.status_code in (statuses or (200,)), (
        f"the admin route answered {response.status_code}: {response.data[:300]!r}"
    )
    return dict(response.get_json())


@then(parsers.parse('the seller stored the adagents.json of "{publisher}"'))
def then_document_stored(ctx: dict, publisher: str) -> None:
    body = _admin_body(ctx, 200, 201)
    stored = _publishers(ctx).stored_publisher(publisher)
    served = _publishers(ctx).adagents_document(publisher)
    assert (stored.fetch_status, stored.fetch_error, stored.adagents) == ("ok", None, served), (
        f"the seller recorded {publisher!r} as fetch_status={stored.fetch_status!r}, "
        f"fetch_error={stored.fetch_error!r}, document={stored.adagents!r} (response {body})"
    )
    assert stored.fetched_at is not None, f"no successful fetch of {publisher!r} was recorded (response {body})"


@then(parsers.parse('the seller holds properties {names} from "{publisher}"'))
def then_holds_properties(ctx: dict, names: str, publisher: str) -> None:
    held = sorted(prop.name for prop in _publishers(ctx).stored_properties(publisher))
    assert held == sorted(_quoted_list(names)), f"the seller holds {held} from {publisher!r}"


@then(parsers.parse('the seller holds no properties from "{publisher}"'))
def then_holds_no_properties(ctx: dict, publisher: str) -> None:
    held = [prop.name for prop in _publishers(ctx).stored_properties(publisher)]
    assert held == [], f"the seller holds {held} from {publisher!r}, from a file that does not authorize it"


@then(parsers.parse('the seller kept the adagents.json of "{publisher}" it stored before, recording "{status}"'))
def then_document_kept(ctx: dict, publisher: str, status: str) -> None:
    """The failed fetch is recorded, and neither the stored document nor the last successful fetch moved."""
    before_document, before_fetched_at = ctx["stored_before"][publisher]
    stored = _publishers(ctx).stored_publisher(publisher)
    assert (stored.fetch_status, stored.fetch_error is not None) == (status, True), (
        f"the last fetch of {publisher!r} is recorded as {stored.fetch_status!r} ({stored.fetch_error!r})"
    )
    assert stored.adagents == before_document, f"the seller replaced the stored adagents.json of {publisher!r}"
    assert stored.fetched_at == before_fetched_at, (
        f"the failed fetch moved the last successful fetch of {publisher!r} to {stored.fetched_at}"
    )


@then(parsers.parse('the seller stored format options {option_ids} of "{publisher}"'))
def then_format_options_stored(ctx: dict, option_ids: str, publisher: str) -> None:
    stored = sorted(option.format_option_id for option in _publishers(ctx).stored_publisher(publisher).format_options)
    assert stored == sorted(_quoted_list(option_ids)), f"the seller stored format options {stored} of {publisher!r}"


@then(parsers.parse('the view reports this agent authorized for "{property_id}"'))
def then_view_authorized(ctx: dict, property_id: str) -> None:
    body = _admin_body(ctx)
    assert (body.get("is_authorized"), body.get("property_ids")) == (True, [property_id]), body


@then("the view reports this agent not authorized")
def then_view_not_authorized(ctx: dict) -> None:
    body = _admin_body(ctx)
    assert body == {"error": f"Agent {_agent_url(ctx)} is not authorized by this publisher", "is_authorized": False}, (
        body
    )


def _listed_publisher(ctx: dict, publisher: str) -> dict:
    """The publishers page's row for *publisher*."""
    domain = _publishers(ctx).publisher_address(publisher)
    rows = [row for row in _admin_body(ctx)["partners"] if row["publisher_domain"] == domain]
    assert len(rows) == 1, f"the publishers page lists {domain!r} {len(rows)} times"
    return rows[0]


@then(
    parsers.re(
        r'the publishers page shows "(?P<publisher>[^"]+)" last fetched "(?P<status>[a-z_]+)" with '
        r"(?P<properties>\d+) authorized propert(?:y|ies) and (?P<options>\d+) format options?"
    )
)
def then_publishers_page_shows_fetched(ctx: dict, publisher: str, status: str, properties: str, options: str) -> None:
    row = _listed_publisher(ctx, publisher)
    shown = (row["fetch_status"], row["fetched_at"] is not None, row["property_count"], row["format_option_count"])
    assert shown == (status, True, int(properties), int(options)), f"the publishers page shows {row}"


@then(
    parsers.re(
        r'the publishers page shows "(?P<publisher>[^"]+)" never fetched, recording "(?P<status>[a-z_]+)", with '
        r"(?P<properties>\d+) authorized propert(?:y|ies) and (?P<options>\d+) format options?"
    )
)
def then_publishers_page_shows_never_fetched(
    ctx: dict, publisher: str, status: str, properties: str, options: str
) -> None:
    row = _listed_publisher(ctx, publisher)
    shown = (row["fetch_status"], row["fetched_at"], row["property_count"], row["format_option_count"])
    assert shown == (status, None, int(properties), int(options)), f"the publishers page shows {row}"


@then("the page offers no way to add a property by hand")
def then_page_offers_no_property_authoring(ctx: dict) -> None:
    """No link to, and no form posting to, an authored-property route."""
    page = _require_admin_page(ctx)
    assert page.status_code == 200, f"the page answered {page.status_code}"
    html = page.data.decode()
    authoring = [
        marker
        for marker in ("authorized-properties/create", "authorized-properties/add", "authorized-properties/upload")
        if marker in html
    ]
    posting = re.findall(r'<form[^>]*method="post"[^>]*action="[^"]*authorized-properties[^"]*"', html, re.IGNORECASE)
    assert authoring == [] and posting == [], f"the page offers property authoring: {authoring + posting}"
