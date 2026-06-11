"""Accept-with-context wire coverage: property_list advisories reach the buyer.

The reject half (UNSUPPORTED_FEATURE on non-compiling adapters) is pinned by
``test_property_list_unsupported_wire.py``. This file pins the ACCEPT half on
the capability-true mock adapter — the ``inventory_list_no_match`` storyboard's
"a silently-successful buy with normal numbers" is the named not-acceptable
outcome, so every channel the advisory rides is asserted at the wire:

- create (completed): machine detail under ``ext.prebid.property_list_advisories``;
  the human-readable text surfaces through ``str(response)`` (each transport's
  protocol-message source). Pinned at impl level for both completed sites
  (adapter path and dry_run) and at the REAL A2A boundary, where the DataPart
  must carry ``success: true`` — the serializer derives the flag from the
  response TYPE, and a booked buy with an advisory wiring as a failure
  invited retry double-booking.
- create (pending approval): the spec ``submitted`` variant — ``task_id`` +
  advisory ``errors[]`` (the variant's spec-blessed slot), with
  ``media_buy_id``/``packages`` absent per the schema's ``not`` constraint.
  Pinned at impl level for both submitted sites (tenant approval and
  config approval) and at the real A2A boundary with the advisory present.
- get_products: ``property_list_applied`` + per-product ``errors[]``
  advisories survive every transport's serialization.

Reverting any single attachment site turns at least one test here red.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant as TenantModel
from src.core.schemas import CreateMediaBuyRequest, CreateMediaBuySubmitted, CreateMediaBuySuccess
from tests.factories import AuthorizedPropertyFactory, TenantAuthConfigFactory
from tests.harness._base import IntegrationEnv
from tests.harness.product import ProductEnv
from tests.harness.transport import Transport
from tests.helpers.adcp_factories import (
    create_test_identifiers,
    create_test_property_list_create_params,
)
from tests.utils.a2a_helpers import drive_a2a_skill, extract_data_from_artifact
from tests.utils.database_helpers import seed_property_list_capability_tenant

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "test_property_list_advisory_wire"
SUBDOMAIN = "prop-list-adv-wire"
ACCESS_TOKEN = "test_token_property_list_advisory"
PRODUCT_ID = "prod_advisory_targeting"

# The async resolver both get_products and the create pre-fetch import lazily
# from this source module.
_RESOLVE_ASYNC = "src.core.property_list_resolver.resolve_property_list_typed"

# The seeded AuthorizedProperty row exposes this domain; the buyer's list
# resolves to a DIFFERENT domain, so the intersection is a genuine
# no_property_overlap (not the degenerate no-rows case).
_PROPERTY_DOMAIN = "advisory-site.example"
_NON_OVERLAPPING = "nonoverlap.example"


@pytest.fixture
def advisory_tenant(integration_db):
    """Capability-true tenant whose product covers one real AuthorizedProperty."""
    with get_db_session() as session:
        seed_property_list_capability_tenant(
            session,
            tenant_id=TENANT_ID,
            tenant_name="Property List Advisory Publisher",
            subdomain=SUBDOMAIN,
            access_token=ACCESS_TOKEN,
            product_id=PRODUCT_ID,
            product_name="Advisory Targeting Product",
            property_targeting_allowed=True,
        )
        # The Tenant model defaults human_review_required=True (and the
        # submitted-variant test sets it explicitly); the auto-approve tests
        # need a committed False, independent of test order.
        tenant = session.get(TenantModel, TENANT_ID)
        tenant.human_review_required = False
        session.commit()
    with IntegrationEnv() as _env:
        tenant = _env._session.scalars(select(TenantModel).filter_by(tenant_id=TENANT_ID)).first()
        # The product's synthesized by_tag selector carries
        # publisher_domain = "<subdomain>.example.com"; the row must match it
        # for the tag resolution to cover this property.
        AuthorizedPropertyFactory(
            tenant=tenant,
            property_id="advisory_wire_site",
            publisher_domain=f"{SUBDOMAIN}.example.com",
            identifiers=[{"type": "domain", "value": _PROPERTY_DOMAIN}],
            tags=["all_inventory"],
        )
    return TENANT_ID


def _patched_resolver():
    return patch(
        _RESOLVE_ASYNC,
        new_callable=AsyncMock,
        return_value=create_test_identifiers(_NON_OVERLAPPING),
    )


def _build_create_request() -> CreateMediaBuyRequest:
    # Idempotency keys are per-call-unique (reused keys replay the cached
    # response once the required-key change lands; spec shape: min 16,
    # charset [A-Za-z0-9_.:-]). The MCP/A2A wire dicts stay keyless until
    # the wrappers accept the parameter.
    return CreateMediaBuyRequest(
        idempotency_key=f"prop-list-advisory-{uuid.uuid4().hex}",
        **create_test_property_list_create_params(PRODUCT_ID),
    )


def _make_identity(*, human_review_required: bool = False):
    """Identity whose tenant dict carries the approval flag explicitly.

    The approval branch reads ``human_review_required`` off the IDENTITY's
    tenant dict (``tenant.get(..., True)``), so the impl-level tests drive the
    completed-vs-submitted split here rather than mutating the DB row. The
    ``test_session_id`` bypasses the production setup-checklist gate the same
    way the wire tests' ``x-test-session-id`` header does.
    """
    from src.core.testing_hooks import AdCPTestContext
    from tests.factories import PrincipalFactory

    return PrincipalFactory.make_identity(
        principal_id="test_adv",
        tenant_id=TENANT_ID,
        protocol="mcp",
        testing_context=AdCPTestContext(test_session_id="prop-list-advisory-impl"),
        human_review_required=human_review_required,
    )


def _advisory_entries(ext) -> list[dict]:
    vendor = getattr(ext, "prebid", None)
    entries = vendor.get("property_list_advisories") if isinstance(vendor, dict) else None
    assert entries, f"expected ext.prebid.property_list_advisories on the success payload; got ext={ext!r}"
    return entries


class TestCreateAdvisoryAttachment:
    """Zero-overlap creates carry buyer-visible context on every envelope variant."""

    @pytest.mark.asyncio
    async def test_completed_create_carries_ext_advisory(self, advisory_tenant):
        """Auto-approve create: ext.property_list_advisories + str(response) text.

        Reverting the ext attachment at the adapter-path construction site
        turns this red; so does the advisory builder returning [] for a
        genuine zero-overlap.
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        with _patched_resolver():
            result = await _create_media_buy_impl(req=_build_create_request(), identity=_make_identity())

        assert result.status == "completed"
        assert isinstance(result.response, CreateMediaBuySuccess)
        entries = _advisory_entries(result.response.ext)
        assert entries[0]["code"] == "PRODUCT_UNAVAILABLE"
        assert entries[0]["details"]["reason"] == "no_property_overlap"
        assert "zero overlap" in str(result.response)

    @pytest.mark.asyncio
    async def test_submitted_create_carries_advisory_errors(self, advisory_tenant):
        """Manual-approval create: the spec submitted variant carries the advisory.

        ``task_id`` is the buyer's tracking handle; ``media_buy_id``/``packages``
        are forbidden on this variant by the response schema's ``not`` clause.
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        with _patched_resolver():
            result = await _create_media_buy_impl(
                req=_build_create_request(), identity=_make_identity(human_review_required=True)
            )

        assert result.status == "submitted"
        assert isinstance(result.response, CreateMediaBuySubmitted)
        assert result.response.task_id
        assert result.response.errors and result.response.errors[0].code == "PRODUCT_UNAVAILABLE"
        assert "zero overlap" in (result.response.message or "")
        dumped = result.response.model_dump(mode="json", exclude_none=True)
        assert "media_buy_id" not in dumped
        assert "packages" not in dumped

    @pytest.mark.asyncio
    async def test_dry_run_create_carries_ext_advisory(self, advisory_tenant):
        """The dry_run simulated response carries the same ext advisory.

        A dry-run probe is exactly where a buyer checks what a real buy would
        return; the advisory silently vanishing there is the storyboard's
        named not-acceptable outcome. Kills the dry_run-site attachment
        mutation.
        """
        from src.core.testing_hooks import AdCPTestContext
        from src.core.tools.media_buy_create import _create_media_buy_impl
        from tests.factories import PrincipalFactory

        identity = PrincipalFactory.make_identity(
            principal_id="test_adv",
            tenant_id=TENANT_ID,
            protocol="mcp",
            testing_context=AdCPTestContext(dry_run=True, test_session_id="prop-list-advisory-dryrun"),
            human_review_required=False,
        )
        with _patched_resolver():
            result = await _create_media_buy_impl(req=_build_create_request(), identity=identity)

        assert result.status == "completed"
        assert result.response.media_buy_id.startswith("dry_run_")
        entries = _advisory_entries(result.response.ext)
        assert entries[0]["details"]["reason"] == "no_property_overlap"

    @pytest.mark.asyncio
    async def test_config_approval_create_returns_submitted_with_advisory(self, advisory_tenant):
        """The auto_create-disabled path returns the same submitted contract.

        This is the second Submitted construction site; without this pin the
        branch is dead in the suite and its shape can drift from the spec
        variant unnoticed.
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        identity = _make_identity()
        identity.tenant["auto_create_media_buys"] = False

        with _patched_resolver():
            result = await _create_media_buy_impl(req=_build_create_request(), identity=identity)

        assert result.status == "submitted"
        assert isinstance(result.response, CreateMediaBuySubmitted)
        assert result.response.task_id
        assert result.response.errors and result.response.errors[0].code == "PRODUCT_UNAVAILABLE"
        dumped = result.response.model_dump(mode="json", exclude_none=True)
        assert "media_buy_id" not in dumped and "packages" not in dumped

    @pytest.mark.asyncio
    async def test_a2a_submitted_create_wire_carries_advisory(self, advisory_tenant):
        """The submitted variant WITH advisory crosses the real A2A boundary intact.

        Pins the converged ``message`` contract: the serializer must not
        clobber the payload's own message (REST/MCP and A2A carry the same
        advisory text), and the artifact's errors[] slot survives framing.
        """
        with get_db_session() as session:
            tenant = session.get(TenantModel, TENANT_ID)
            tenant.human_review_required = True
            session.commit()
        try:
            headers = {
                "x-adcp-auth": ACCESS_TOKEN,
                "x-adcp-tenant": TENANT_ID,
                "x-test-session-id": "prop-list-advisory-a2a-submitted",
            }
            with _patched_resolver():
                result = await drive_a2a_skill(
                    "create_media_buy",
                    create_test_property_list_create_params(PRODUCT_ID),
                    headers,
                    auth_token=ACCESS_TOKEN,
                )
        finally:
            with get_db_session() as session:
                tenant = session.get(TenantModel, TENANT_ID)
                tenant.human_review_required = False
                session.commit()

        assert result.artifacts, "submitted Task must carry its tracking artifact"
        payload = extract_data_from_artifact(result.artifacts[0])
        assert payload["status"] == "submitted"
        assert payload["task_id"]
        assert payload["success"] is True
        assert payload["errors"][0]["code"] == "PRODUCT_UNAVAILABLE"
        # Converged message: the payload's own advisory text, NOT the
        # serializer-synthesized tracking sentence.
        assert "zero overlap" in payload["message"]
        assert not payload["message"].startswith("Media buy submitted for approval")
        assert "media_buy_id" not in payload and "packages" not in payload

    def test_rest_submitted_create_wire_carries_advisory(self, advisory_tenant):
        """The submitted variant WITH advisory crosses the real HTTP boundary intact.

        Drives TestClient -> route -> raw wrapper -> _impl with the real
        token->DB->identity chain; the manual-approval gate is the tenant's
        ``human_review_required`` DB flag, exactly as the A2A wire test forces
        it. Pins the spec shape at real HTTP bytes: ``status="submitted"``,
        ``task_id`` present, ``media_buy_id``/``packages`` absent per the
        variant's ``not`` constraint, advisory on ``errors[]``.
        """
        from starlette.testclient import TestClient

        from src.app import app

        with get_db_session() as session:
            tenant = session.get(TenantModel, TENANT_ID)
            tenant.human_review_required = True
            # REST has no x-test-session-id seam (the testing-context bypass the
            # MCP/A2A wire tests use — flagged as a transport-parity follow-up),
            # so the production setup-checklist gate runs for real here: satisfy
            # its SSO item the production way.
            tenant.auth_setup_mode = False
            session.commit()
        with IntegrationEnv() as _env:
            tenant_row = _env._session.scalars(select(TenantModel).filter_by(tenant_id=TENANT_ID)).first()
            TenantAuthConfigFactory(tenant=tenant_row, oidc_enabled=True)
        try:
            with _patched_resolver():
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/api/v1/media-buys",
                    json=create_test_property_list_create_params(PRODUCT_ID),
                    headers={
                        "x-adcp-auth": ACCESS_TOKEN,
                        "x-adcp-tenant": TENANT_ID,
                        "x-test-session-id": "prop-list-advisory-rest-submitted",
                    },
                )
        finally:
            with get_db_session() as session:
                tenant = session.get(TenantModel, TENANT_ID)
                tenant.human_review_required = False
                session.commit()

        assert response.status_code == 200, (
            f"submitted create must serialize, got {response.status_code}: {response.text}"
        )
        payload = response.json()
        assert payload["status"] == "submitted"
        assert payload["task_id"]
        assert payload["errors"][0]["code"] == "PRODUCT_UNAVAILABLE"
        assert "zero overlap" in payload["message"]
        assert "media_buy_id" not in payload and "packages" not in payload

    @pytest.mark.asyncio
    async def test_mcp_submitted_create_wire_carries_advisory(self, advisory_tenant):
        """The submitted variant WITH advisory crosses the real FastMCP Client boundary intact.

        The harness ``call_impl`` observes no wire bytes; this drives the real
        ``Client(mcp)`` pipeline (middleware -> TypeAdapter -> wrapper -> _impl
        -> ToolResult framing) with the production-shaped auth and
        setup-checklist bypass (``x-test-session-id``), and reads the
        structured content the buyer would.
        """
        from fastmcp import Client

        from src.core.main import mcp

        headers = {
            "x-adcp-auth": ACCESS_TOKEN,
            "x-adcp-tenant": TENANT_ID,
            "x-test-session-id": "prop-list-advisory-mcp-submitted",
        }
        arguments = create_test_property_list_create_params(PRODUCT_ID)

        with get_db_session() as session:
            tenant = session.get(TenantModel, TENANT_ID)
            tenant.human_review_required = True
            session.commit()
        try:
            # Each module binds get_http_headers via ``from ... import`` so each
            # needs its own patch; testing_hooks turns x-test-session-id into the
            # production-shaped setup-checklist bypass.
            with (
                patch("src.core.auth.get_http_headers", return_value=headers),
                patch("src.core.transport_helpers.get_http_headers", return_value=headers),
                patch("src.core.testing_hooks.get_http_headers", return_value=headers),
                patch("src.core.mcp_auth_middleware.get_http_headers", return_value=headers),
                _patched_resolver(),
            ):
                async with Client(mcp) as client:
                    result = await client.call_tool("create_media_buy", arguments)
        finally:
            with get_db_session() as session:
                tenant = session.get(TenantModel, TENANT_ID)
                tenant.human_review_required = False
                session.commit()

        payload = result.structured_content
        assert isinstance(payload, dict), f"expected structured content dict, got {type(payload)}"
        payload = payload.get("result", payload)
        assert payload["status"] == "submitted"
        assert payload["task_id"]
        assert payload["errors"][0]["code"] == "PRODUCT_UNAVAILABLE"
        assert "zero overlap" in payload["message"]
        assert "media_buy_id" not in payload and "packages" not in payload

    @pytest.mark.asyncio
    async def test_a2a_completed_create_wire_success_true_with_advisory(self, advisory_tenant):
        """The booked-buy artifact wires success:true WITH the advisory present.

        End-to-end regression pin for the errors-presence success flip: drives
        the real ``on_message_send`` → token→DB→identity → ``_impl`` → mock
        adapter → ``_serialize_for_a2a`` chain and reads the DataPart.
        """
        skill_params = create_test_property_list_create_params(PRODUCT_ID)
        headers = {
            "x-adcp-auth": ACCESS_TOKEN,
            "x-adcp-tenant": TENANT_ID,
            "x-test-session-id": "prop-list-advisory-a2a",
        }
        with _patched_resolver():
            result = await drive_a2a_skill("create_media_buy", skill_params, headers, auth_token=ACCESS_TOKEN)

        assert result.artifacts, "completed create must carry its artifact"
        payload = extract_data_from_artifact(result.artifacts[0])
        assert payload["success"] is True, "advisory-bearing booked buy must not wire as a failure"
        assert payload["media_buy_id"]
        assert "zero overlap" in payload["message"]
        assert payload["ext"]["prebid"]["property_list_advisories"][0]["code"] == "PRODUCT_UNAVAILABLE"


class TestGetProductsAdvisoryWire:
    """property_list_applied + errors[] advisories survive every transport."""

    def _assert_payload(self, payload) -> None:
        assert payload.property_list_applied is True
        assert payload.errors, "dropped products must surface as errors[] advisories"
        assert payload.errors[0].code == "PRODUCT_UNAVAILABLE"
        assert payload.products == []

    def test_applied_true_with_zero_drops_and_absent_without_list(self, integration_db):
        """property_list_applied reflects that the filter RAN, not that it dropped.

        Spec: "True if the agent filtered products based on the provided
        property_list. Absent or false if property_list was not provided."
        """
        with ProductEnv(tenant_id="adv-flag-gp", principal_id="adv-flag-gp-p") as env:
            from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory

            tenant = TenantFactory(tenant_id="adv-flag-gp", subdomain="adv-flag-gp")
            PrincipalFactory(tenant=tenant, principal_id="adv-flag-gp-p")
            AuthorizedPropertyFactory(
                tenant=tenant,
                property_id="flag_site",
                publisher_domain="adv-flag-gp.example.com",
                identifiers=[{"type": "domain", "value": _PROPERTY_DOMAIN}],
                tags=["all_inventory"],
            )
            product = ProductFactory(tenant=tenant, product_id="flag_prod", name="Flag Product")
            PricingOptionFactory(product=product, pricing_model="cpm", is_fixed=True)

            # Overlapping list: the filter runs, nothing drops → applied True, no advisories.
            env.set_property_list([_PROPERTY_DOMAIN])
            kept = env.call_via(
                Transport.REST,
                brief="flag test",
                property_list={"agent_url": "https://propertylist.example.com", "list_id": "flag_list"},
            )
            assert kept.is_success
            assert kept.payload.property_list_applied is True
            assert kept.payload.errors is None
            assert [p.product_id for p in kept.payload.products] == ["flag_prod"]

            # No property_list in the request → the field stays absent.
            plain = env.call_via(Transport.REST, brief="flag test")
            assert plain.is_success
            assert plain.payload.property_list_applied is None

    @pytest.mark.parametrize("transport", [Transport.REST, Transport.MCP, Transport.A2A])
    def test_get_products_advisory_fields_survive_transport(self, integration_db, transport):
        with ProductEnv(tenant_id="adv-wire-gp", principal_id="adv-wire-gp-p") as env:
            from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory

            tenant = TenantFactory(tenant_id="adv-wire-gp", subdomain="adv-wire-gp")
            PrincipalFactory(tenant=tenant, principal_id="adv-wire-gp-p")
            AuthorizedPropertyFactory(
                tenant=tenant,
                property_id="gp_wire_site",
                publisher_domain="adv-wire-gp.example.com",
                identifiers=[{"type": "domain", "value": _PROPERTY_DOMAIN}],
                tags=["all_inventory"],
            )
            product = ProductFactory(tenant=tenant, product_id="gp_wire_prod", name="GP Wire Product")
            PricingOptionFactory(product=product, pricing_model="cpm", is_fixed=True)

            env.set_property_list([_NON_OVERLAPPING])
            result = env.call_via(
                transport,
                brief="advisory wire test",
                property_list={"agent_url": "https://propertylist.example.com", "list_id": "adv_list"},
            )

        assert result.is_success, f"expected success but got: {result.error}"
        self._assert_payload(result.payload)
