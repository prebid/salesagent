"""A tenant always declares the host it is served at, and every publisher reads that one.

``virtual_host`` is the only statement of where a tenant answers. A tenant holding none is
not merely unpublishable: there are exactly two ways to name a tenant — ``Host`` against
``tenants.virtual_host``, and the ``x-adcp-tenant`` literal id (#2191) — so a host-less
tenant is UNREACHABLE by ``Host`` at all, and a reader handed one can only INVENT a host,
publishing on the card a name nothing on the network serves (#1845).

So the rule is the column's, not each reader's: a tenant cannot exist without a host, and
no code derives one. These tests drive the outer surfaces that rule has to hold at — the
admin form that writes the row and the card route that publishes it.

Two facts about the shared fixtures are recorded here because they decide whether these
tests grade anything at all:

* ``authenticated_admin_session`` puts a DICT in ``session["user"]``, while production
  stores a string email (``src/admin/blueprints/auth.py``, ``oidc.py``). ``create_tenant``
  appends that value to ``authorized_emails``, so under the fixture shape the route writes
  a dict into a list-of-strings column and every later read of the row fails pydantic
  validation. These tests set the production shape; grading the route against the fixture
  shape would grade a route no deployment runs.
* ``create_tenant`` is ``@require_auth(admin_only=True)``, so the session email must be the
  one the fixture seeds as ``super_admin_emails``.
"""

import re

import pytest
from starlette.testclient import TestClient

#: A host under the test TLD the storyboard seed established. Nothing routes to it on a
#: test box; it exists so a stored origin can be compared against what a reader publishes.
ORIGIN = "mandatory-vhost.adcp.test"
ADMIN_EMAIL = "test@example.com"

#: A host no tenant declares, under the TLD RFC 2606 reserves for exactly this. It stands in
#: for the attacker-supplied ``Host`` of a direct connection: every header on one is the
#: caller's to choose, so a reader deriving the published origin from this one publishes it.
UNSERVED_HOST = "attacker.invalid"


def _stored_host(tenant_id: str) -> str | None:
    """The host the row actually holds, read the one way a tenant is loaded by id.

    ``TenantContext.load`` rather than a session of our own: it is the sanctioned read
    (CLAUDE.md Pattern #8 bans ``get_db_session()`` in a test body), and it is the same
    projection every production reader sees, so this grades what the card would publish.
    """
    from src.core.tenant_context import TenantContext

    loaded = TenantContext.load(tenant_id)
    return loaded.virtual_host if loaded else None


def _tenant_exists(tenant_id: str) -> bool:
    from src.core.tenant_context import TenantContext

    return TenantContext.load(tenant_id) is not None


def _as_production_admin(client) -> None:
    """Give the session the shape production writes — a string email, not a dict."""
    with client.session_transaction() as sess:
        sess["user"] = ADMIN_EMAIL


@pytest.mark.requires_db
def test_a_ui_created_tenants_card_publishes_its_stored_origin(authenticated_admin_session, integration_db):
    """THE ACCEPTANCE: drive a UI-created tenant's card and assert it publishes the stored origin.

    The two halves have to be graded together. The form writing the host and the card
    reading it are the only pair that can tell "the operator's host is published" from
    "some host is published" — which is how ``http://localhost:8080`` came to be a
    UI-created tenant's advertised public endpoint.
    """
    from src.app import app

    _as_production_admin(authenticated_admin_session)

    response = authenticated_admin_session.post(
        "/create_tenant",
        data={"name": "Mandatory Vhost", "subdomain": "mandatory_vhost", "virtual_host": ORIGIN},
        follow_redirects=False,
    )
    assert response.status_code in (200, 302), response.data[:500]

    stored = _stored_host("tenant_mandatory_vhost")
    assert stored == ORIGIN, f"the admin form did not store the host the operator submitted, it stored {stored!r}"

    card = TestClient(app).get("/.well-known/agent-card.json", headers={"Host": ORIGIN})
    assert card.status_code == 200, card.text
    interfaces = card.json()["supportedInterfaces"]
    urls = [interface["url"] for interface in interfaces]
    assert urls == [f"https://{ORIGIN}/a2a"], f"the card published {urls}, not the origin the operator stored"

    # A URL nothing selects is as unreachable as a wrong one, so the binding is graded with
    # it. An A2A 1.x client picks its interface with
    # `i.protocolBinding?.toUpperCase() === "JSONRPC"` (@a2a-js/sdk pick_interface.ts) --
    # uppercased there, so this mirrors the comparison the client makes rather than pinning a
    # spelling the client does not care about. A card matching none of its interfaces reports
    # the agent UNREACHABLE without ever sending a request.
    bindings = [(interface.get("protocolBinding") or "").upper() for interface in interfaces]
    assert bindings == ["JSONRPC"], f"the card published protocolBinding {bindings}, which no A2A 1.x client selects"

    # The identity fields a client reads off the card. They had unit graders in
    # ``test_a2a_transport_contract.py``; the card is already parsed here, so they are graded
    # where the real route produced it instead.
    body = card.json()
    assert body["name"] == "Prebid Sales Agent", f"the card published name {body.get('name')!r}"

    # Graded by SHAPE, not against ``get_adcp_spec_version()``: deriving the expected value
    # from the same call production makes moves both sides together, so a mutated version
    # would stay green. A semver reddens on any placeholder.
    extensions = body["capabilities"]["extensions"]
    declared = [e["params"]["adcp_version"] for e in extensions]
    assert len(declared) == 1 and re.fullmatch(r"\d+\.\d+\.\d+", declared[0]), (
        f"the card published adcp_version {declared}, which is not a spec version"
    )
    assert [e["uri"] for e in extensions] == [
        f"https://adcontextprotocol.org/schemas/{declared[0]}/protocols/adcp-extension.json"
    ], "the extension URI names a different version than its own params do"
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", body["version"] or ""), (
        f"the card published version {body.get('version')!r}"
    )

    # A2A joins the interface URL to a method path, so a trailing slash yields `/a2a//...`.
    assert not urls[0].endswith("/"), f"the card published a trailing-slash URL: {urls[0]!r}"


@pytest.mark.requires_db
def test_the_card_publishes_the_stored_origin_when_the_host_header_names_another(integration_db):
    """The one test that can tell a stored read from an echoed header.

    ``_create_dynamic_agent_card`` states that neither the URL nor any field is derived from
    the request, and every other card test sends ``Host: ORIGIN`` where ORIGIN is ALSO the
    stored ``virtual_host`` — so an echo of the header and a read of the column produce the
    same string, and all of them pass either way. Putting the echo back
    (``agent_url=f"https://{request.headers['host']}/a2a"``) left every card test green
    (#2191), which is how the echo got written in the first place.

    Naming the tenant by ``x-adcp-tenant`` is what frees ``Host`` to carry something else:
    ``_detect_tenant`` tries the host against ``tenants.virtual_host`` first and falls through
    to the literal id when no tenant declares it. So the request resolves the seeded tenant
    while its ``Host`` names a host this deployment serves for nobody, and the two candidate
    origins differ. On a direct connection every header is the caller's to choose, which is
    why an echo here would answer ``https://attacker.invalid/a2a`` to whoever asked for it.
    """
    from src.app import app
    from tests.factories import PrincipalFactory, TenantFactory
    from tests.harness import ProductEnv

    with ProductEnv(tenant_id="stored-origin-t", principal_id="stored-origin-p") as env:
        tenant = TenantFactory(tenant_id="stored-origin-t", virtual_host=ORIGIN)
        PrincipalFactory(tenant=tenant, principal_id="stored-origin-p")
        env._commit_factory_data()

        card = TestClient(app).get(
            "/.well-known/agent-card.json",
            headers={"Host": UNSERVED_HOST, "x-adcp-tenant": "stored-origin-t"},
        )

        assert card.status_code == 200, card.text
        urls = [interface["url"] for interface in card.json()["supportedInterfaces"]]
        assert urls == [f"https://{ORIGIN}/a2a"], (
            f"the card published {urls} for a tenant whose stored origin is {ORIGIN!r}: "
            f"a caller sending Host: {UNSERVED_HOST} reads its own header back as this agent's URL"
        )


@pytest.mark.requires_db
def test_a_tool_call_naming_no_served_tenant_is_refused_with_its_status(integration_db):
    """THE REFUSAL, on a tool surface: 421 and a TENANT_UNDEFINED / terminal envelope.

    The refusal answers every tool on every transport, and the only thing grading it was one
    e2e assertion on the agent CARD -- so the status was ungraded on every tool-call surface,
    and changing 500 to anything else left the in-process suites green.

    REST is the transport that carries a status, so it is where the status is graded. The
    421 Misdirected Request (RFC 9110 S15.5.20) is the status for a request "directed at a
    server that is unable or unwilling to produce an authoritative response for the target
    URI's origin", which is this condition. The envelope carries no ``details``: the address
    the caller supplied is the one thing it must not be handed back, and it reaches the
    operator through the raise's ``__cause__`` instead.
    """
    from tests.harness.capabilities import CapabilitiesEnv
    from tests.helpers.credentials import credential_headers
    from tests.helpers.envelope_assertions import assert_envelope_shape

    with CapabilitiesEnv() as env:
        env.setup_default_data()

        response = env.get_rest_client().post(
            "/api/v1/capabilities",
            json={},
            headers=credential_headers(host=UNSERVED_HOST),
        )

        assert response.status_code == 421, (
            f"a request naming a host this deployment serves for nobody answered "
            f"{response.status_code}; the refusal's status is part of its contract"
        )
        assert_envelope_shape(response.json(), "TENANT_UNDEFINED", recovery="terminal")

        # The key is ABSENT, not an empty object: the error declares no details shape at all
        # (``AdCPSalesAgentError[ErrorDetails]``), which is what the pinned text asks of a
        # code like this one. Reddens if any reflection of the request comes back.
        assert "details" not in response.json()["adcp_error"], (
            f"TENANT_UNDEFINED carried details {response.json()['adcp_error'].get('details')!r}; "
            f"the address the caller supplied reaches the operator's record, never the buyer"
        )
        assert UNSERVED_HOST not in response.text, (
            f"the refusal reflected {UNSERVED_HOST!r} back to the caller somewhere in its body"
        )


@pytest.mark.requires_db
def test_the_refused_host_reaches_the_operators_record_and_not_the_buyer(integration_db, caplog):
    """The two halves of the refusal's disclosure, graded together.

    The operator has to know which address was dialled -- it is the only thing that
    distinguishes an edge misrouting from a buyer with a stale URL -- and the buyer must not
    be told it back. Only asserting the wire half leaves the channel free to deliver nothing,
    which is what handing the ``LookupError`` to ``internal_detail`` did: the boundary logs
    ``exc_info=error``, which formats the ``__cause__`` chain, and a bare attribute is not in
    it. The host appeared in no record on any surface.
    """
    import logging

    from tests.harness.capabilities import CapabilitiesEnv
    from tests.helpers.credentials import credential_headers

    with CapabilitiesEnv() as env:
        env.setup_default_data()
        with caplog.at_level(logging.DEBUG):
            response = env.get_rest_client().post(
                "/api/v1/capabilities", json={}, headers=credential_headers(host=UNSERVED_HOST)
            )

    assert response.status_code == 421, response.text
    assert UNSERVED_HOST not in response.text, (
        f"the refusal reflected {UNSERVED_HOST!r} back to the caller: {response.text[:300]!r}"
    )
    captured = "\n".join(
        record.getMessage() + "\n" + (logging.Formatter().formatException(record.exc_info) if record.exc_info else "")
        for record in caplog.records
    )
    assert UNSERVED_HOST in captured, (
        f"the host the request named reached no log record, so an operator cannot tell which "
        f"address was dialled. Captured {len(captured)} characters across {len(caplog.records)} records"
    )


@pytest.mark.requires_db
@pytest.mark.parametrize("fault", ["typed", "untyped"])
def test_the_card_route_records_one_operation_whatever_fails(integration_db, caplog, monkeypatch, fault):
    """One route, one recorded operation — for a typed refusal and for a crash alike.

    The route used to carry its own ``except AdCPSalesAgentError``, so a tenant refusal was
    recorded as ``A2A … operation=agent_card`` while anything else escaped to the app's
    catch-all and became ``REST … operation=/.well-known/agent-card.json``. One route
    reporting two operations splits its own error rate across two names, so neither is the
    route's — and the untyped half is the one on-call reads.

    Both halves are graded because catching every exception IN the route fixes only this
    pair; the operation is resolved from the path in ``_envelope_response`` instead, so the
    route catches nothing and every handler funnels to one label.
    """
    import logging

    from src.core.agent_identity import AGENT_CARD_PATH
    from tests.harness.capabilities import CapabilitiesEnv

    if fault == "untyped":
        monkeypatch.setattr(
            "src.app._create_dynamic_agent_card",
            lambda request: (_ for _ in ()).throw(RuntimeError("card construction blew up")),
        )

    with CapabilitiesEnv() as env:
        env.setup_default_data()
        # The env's own client, not an ad hoc TestClient: the harness owns REST dispatch, and
        # this flag is how it lets an untyped fault become a response rather than propagate
        # (``inject_untyped_exception`` sets the same one for a tool).
        env.REST_RAISE_SERVER_EXCEPTIONS = False
        with caplog.at_level(logging.DEBUG):
            response = env.fetch_agent_card(path=AGENT_CARD_PATH, host=UNSERVED_HOST)

    assert response.status_code >= 400, response.text
    recorded = "\n".join(record.getMessage() for record in caplog.records)
    assert "operation=agent_card" in recorded, (
        f"the {fault} fault on the card route was recorded under another operation; records: {recorded[-400:]!r}"
    )
    assert "operation=/.well-known/agent-card.json" not in recorded, (
        f"the {fault} fault was recorded by URL path, so this route reports two identities"
    )


@pytest.mark.requires_db
def test_the_host_wins_when_both_headers_name_a_served_tenant(integration_db):
    """THE RESOLUTION ORDER: ``Host`` is tried first, and this is the only test that proves it.

    The order was stated and ungraded. Every other request carries ONE of the two names --
    ``credential_headers`` sends a Host or an ``x-adcp-tenant``, never both -- and the one
    request that sends both (above) names an UNSERVED host, so the header is the only thing
    that can resolve and the fallback runs either way. Swapping ``_detect_tenant`` to try the
    header first left the whole suite green.

    Both names here resolve a DIFFERENT seeded tenant, so each header alone would succeed and
    only the precedence decides which. The card's published origin decides it: that value is read
    from the row that resolved, so it names the winner.
    """
    from src.app import app
    from tests.factories import PrincipalFactory, TenantFactory
    from tests.harness import ProductEnv

    host_origin = "host-wins.adcp.test"
    header_origin = "header-loses.adcp.test"

    with ProductEnv(tenant_id="order-host-t", principal_id="order-p") as env:
        by_host = TenantFactory(tenant_id="order-host-t", virtual_host=host_origin)
        PrincipalFactory(tenant=by_host, principal_id="order-p")
        TenantFactory(tenant_id="order-header-t", virtual_host=header_origin)
        env._commit_factory_data()

        card = TestClient(app).get(
            "/.well-known/agent-card.json",
            headers={"Host": host_origin, "x-adcp-tenant": "order-header-t"},
        )

        assert card.status_code == 200, card.text
        urls = [interface["url"] for interface in card.json()["supportedInterfaces"]]
        assert urls == [f"https://{host_origin}/a2a"], (
            f"the card published {urls}; the Host named a tenant this deployment serves, so it "
            f"decides, and the x-adcp-tenant fallback must not have been consulted"
        )


@pytest.mark.requires_db
def test_the_admin_form_refuses_to_create_a_tenant_with_no_host(authenticated_admin_session, integration_db):
    """A submission naming no host creates no tenant.

    Asks whether the TENANT exists rather than whether its host is None, deliberately: once
    the column refuses NULL those two questions collapse, and a host-is-None assertion could
    not tell "refused" from "created host-less" while the defect was present.
    """
    _as_production_admin(authenticated_admin_session)

    authenticated_admin_session.post(
        "/create_tenant",
        data={"name": "No Host", "subdomain": "no_host"},
        follow_redirects=False,
    )

    assert not _tenant_exists("tenant_no_host"), "a tenant was created despite naming no host"


@pytest.mark.requires_db
def test_the_column_itself_refuses_a_tenant_with_no_host(integration_db):
    """The rule lives on the column, so a creation path added later cannot miss it.

    Every creation path writes through the ORM validator, which is why refusing there — and
    not in each of the eight paths — is what makes a host-less tenant unrepresentable.
    """
    from src.core.database.models import Tenant

    with pytest.raises(ValueError):
        Tenant(tenant_id="host_less", name="Host Less", subdomain="host_less", virtual_host=None)

    with pytest.raises(ValueError):
        Tenant(tenant_id="host_blank", name="Host Blank", subdomain="host_blank", virtual_host="   ")


@pytest.mark.requires_db
def test_the_settings_page_hands_the_operator_the_stored_origin(authenticated_admin_session, integration_db):
    """The settings page's copy-paste MCP endpoint names the host the tenant declares.

    It is a publisher of the agent URL like the card and the adagents.json verifier, and the
    one an operator actually copies out of, so a wrong host here reaches a buyer by hand.
    The page had no test of any kind, which is how its endpoint block kept a
    ``http://localhost:<port>/mcp/`` arm two lines under one saying the domain was not
    configured (#2191).

    Asserts BOTH halves for the same tenant: the stored origin appears, and no localhost MCP
    URL does. The absence half is what a fabricated or dev-default host would break; the
    presence half is what an empty block would.
    """
    from src.core.tenant_context import TenantContext
    from tests.factories import TenantFactory
    from tests.harness import ProductEnv

    _as_production_admin(authenticated_admin_session)

    with ProductEnv(tenant_id="settings-origin-t", principal_id="settings-origin-p") as env:
        TenantFactory(tenant_id="settings-origin-t", virtual_host=ORIGIN)
        env._commit_factory_data()

        assert TenantContext.load("settings-origin-t") is not None, "the tenant was not committed"

        page = authenticated_admin_session.get("/tenant/settings-origin-t/settings/api")
        assert page.status_code == 200, page.data[:500]
        rendered = page.data.decode()

    assert f"https://{ORIGIN}/mcp/" in rendered, "the settings page does not name the host the tenant declares"
    assert "localhost" not in rendered, "the settings page hands the operator a localhost URL to copy"


@pytest.mark.requires_db
def test_every_publisher_of_this_tenants_agent_url_names_the_same_origin(monkeypatch, integration_db):
    """The adagents.json verifier and the card name ONE origin for one tenant.

    A counterparty fetches adagents.json and byte-matches the agent URL it finds there
    against the one the card published. Two derivations of "where is this tenant" are two
    chances to disagree, and there is no diagnostic when they do — the check simply fails.

    ``ADCP_AGENT_URL`` is set on the settings OBJECT rather than the environment: production
    reads a field off settings built once, so an env write lands only before the first read.
    A deployment-wide override must not outrank the tenant's own stored host, because that
    is how every tenant collapses onto one URL.
    """
    from src.admin.blueprints.authorized_properties import _construct_agent_url
    from src.core.agent_identity import canonical_agent_url
    from src.core.config import get_settings
    from tests.factories import TenantFactory
    from tests.harness import ProductEnv

    monkeypatch.setattr(get_settings().runtime, "adcp_agent_url", "https://some-other-deployment.example.org")

    with ProductEnv(tenant_id="pub-origin-t", principal_id="pub-origin-p") as env:
        tenant = TenantFactory(tenant_id="pub-origin-t", virtual_host=ORIGIN)
        env._commit_factory_data()

        from src.core.tenant_context import TenantContext

        published = canonical_agent_url(TenantContext(tenant_id=tenant.tenant_id, virtual_host=tenant.virtual_host))
        verified = _construct_agent_url("pub-origin-t")

        assert verified == published, (
            f"the adagents.json verifier derived {verified!r} while the card publishes {published!r}"
        )
        assert published == f"https://{ORIGIN}", f"the published origin is not the stored host: {published!r}"


@pytest.mark.requires_db
def test_a_seller_with_no_publisher_partners_names_its_own_domain(integration_db):
    """With no partners, the seller's portfolio names the domain it is actually served at.

    Asserts EQUALITY with the tenant's own hostname, not a ``.example.com`` suffix. The BDD
    scenarios that touch this placeholder assert only the suffix, and ``TenantFactory``
    mints ``vhost-NNNN.example.com`` — so they pass both with the fabricated
    ``{subdomain}.example.com`` and with the fix, and nothing else in the suite can tell the
    two apart.
    """
    from src.core.http_utils import hostname_of
    from src.core.resolved_identity import public_identity_for
    from src.services.seller_capabilities import describe_seller
    from tests.factories import PrincipalFactory, TenantFactory
    from tests.harness import ProductEnv

    with ProductEnv(tenant_id="placeholder-t", principal_id="placeholder-p") as env:
        tenant = TenantFactory(tenant_id="placeholder-t", subdomain="placeholdert", virtual_host=ORIGIN)
        PrincipalFactory(tenant=tenant, principal_id="placeholder-p")
        env._commit_factory_data()

        seller = describe_seller(public_identity_for({"host": ORIGIN}))
        assert seller.media_buy is not None, "the seller declared no media_buy block, so nothing names a domain"
        domains = [str(domain.root) for domain in seller.media_buy.portfolio.publisher_domains]

        assert domains == [hostname_of(ORIGIN)], f"the portfolio named {domains}, not the seller's own domain"
