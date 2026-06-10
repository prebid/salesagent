"""Wire envelope tests for ``AdCPCapabilityNotSupportedError`` on property_list.

The ``_impl``-level reject contract is proven in
``test_property_list_unsupported_capability.py``. These tests prove the boundary
check (``raise_if_property_list_unsupported``) surfaces a spec-compliant
two-layer envelope at the transport boundary the buyer actually reaches —
``code=UNSUPPORTED_FEATURE``, ``recovery=correctable``, plus the
machine-actionable ``field`` + ``suggestion``.

Transport coverage, and why it is split:

- **create / REST + MCP** — driven through ``MediaBuyCreateEnv.call_via`` (the
  canonical harness, ``tests/CLAUDE.md``) and asserted on the real-wire
  ``TransportResult.wire_error_envelope`` (REST HTTP body; MCP ``ToolError``
  JSON). The env mocks ``get_adapter``, and the boundary helper reads
  ``adapter.__class__.supports_property_list_targeting`` — a MagicMock class
  lacks that attribute, so the gate sees ``False`` and rejects. The seeded
  product sets ``property_targeting_allowed=True`` so the #1276 product-flag
  gate passes first and the adapter-incapacity gate is what fires.

- **create + update / A2A** — driven explicitly through
  ``handler.on_message_send``. The harness A2A path routes ``call_a2a`` through
  ``create_media_buy_raw``, which *raises* — so the dispatcher *synthesizes* the
  envelope from the exception and never exercises the
  ``on_message_send`` → failed-``Task`` → artifact ``DataPart`` framing. That
  framing is the layer where ``field``/``suggestion`` can be dropped, so it must
  be driven directly. ``assert_envelope_shape`` pins code/recovery/message;
  ``field`` + ``suggestion`` are pinned on ``errors[0]`` (the shape helper does
  not cover them).

Covers: UC-002 honest-declaration property_list reject — wire shape (create)
Covers: UC-003 honest-declaration property_list reject — wire shape (update)
"""

from __future__ import annotations

import uuid

import pytest
from a2a.types import Message, Task

from src.core.database.database_session import get_db_session
from src.core.schemas import CreateMediaBuyRequest
from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.transport import Transport
from tests.helpers import assert_envelope_shape
from tests.helpers.adcp_factories import (
    TEST_PROPERTY_LIST_TARGETING_OVERLAY,
    create_test_property_list_create_params,
)
from tests.utils.a2a_helpers import drive_a2a_skill, extract_data_from_artifact
from tests.utils.database_helpers import (
    seed_media_buy_with_package,
    seed_property_list_capability_tenant,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "test_property_list_wire"
ACCESS_TOKEN = "test_token_property_list_wire"
PRODUCT_ID = "prod_property_targeting_allowed"
_FIELD = "packages[0].targeting_overlay.property_list"


def _build_create_request() -> CreateMediaBuyRequest:
    """A create request whose single package carries property_list targeting."""
    # Per-call-unique idempotency key: #1312 makes the field REQUIRED
    # (min 16, charset [A-Za-z0-9_.:-]); unique-per-call matters because
    # reused keys replay the cached response once that lands. The MCP/A2A
    # wire dicts stay keyless until the wrappers accept the parameter.
    return CreateMediaBuyRequest(
        idempotency_key=f"prop-list-wire-{uuid.uuid4().hex}",
        **create_test_property_list_create_params(PRODUCT_ID),
    )


def _build_update_packages(package_id: str) -> list[dict]:
    """Update-request packages that add property_list targeting to an existing package."""
    return [{"package_id": package_id, "targeting_overlay": TEST_PROPERTY_LIST_TARGETING_OVERLAY}]


def _assert_unsupported_envelope(envelope: dict) -> None:
    """Assert the spec two-layer UNSUPPORTED_FEATURE envelope + machine-actionable fields.

    ``assert_envelope_shape`` enforces the two-layer invariant
    (``adcp_error.code == errors[0].code``), recovery, and message. ``field`` +
    ``suggestion`` are pinned separately on ``errors[0]`` — the buyer-agent
    correction context the shape helper does not assert.
    """
    assert_envelope_shape(
        envelope,
        "UNSUPPORTED_FEATURE",
        recovery="correctable",
        message_substr="does not support property_list",
    )
    err = envelope["errors"][0]
    assert err.get("field") == _FIELD, f"field must identify the offending package; got {err.get('field')!r}"
    assert err.get("suggestion") and "property_list_filtering" in err["suggestion"], (
        f"suggestion must reference the property_list_filtering capability flag; got {err.get('suggestion')!r}"
    )


def _seed_property_list_product(env: MediaBuyCreateEnv, tenant: object) -> None:
    """Seed a product that ALLOWS property_targeting onto the env's tenant.

    ``property_targeting_allowed=True`` lets the request past the #1276
    product-flag gate so it reaches the adapter-incapacity boundary gate
    (the env's mock adapter declares no property_list support → reject).
    """
    from tests.factories import PricingOptionFactory, ProductFactory
    from tests.factories.core import PropertyTagFactory

    PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
    product = ProductFactory(
        tenant=tenant,
        product_id=PRODUCT_ID,
        delivery_type="non_guaranteed",
        format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
        property_tags=["all_inventory"],
        property_targeting_allowed=True,
    )
    PricingOptionFactory(product=product, pricing_model="cpm", currency="USD", is_fixed=True)


async def _drive_a2a_skill(skill_name: str, skill_params: dict, headers: dict[str, str]) -> Task | Message:
    """Drive a skill through the real A2A boundary with this module's token."""
    return await drive_a2a_skill(skill_name, skill_params, headers, auth_token=ACCESS_TOKEN)


def _assert_a2a_unsupported(result: object) -> None:
    """Assert an A2A result is a failed Task whose artifact carries the UNSUPPORTED_FEATURE envelope."""
    assert isinstance(result, Task), f"A2A error must surface as a failed Task; got {type(result)!r}"
    assert result.artifacts, "A2A error Task must carry an artifact with the envelope"
    _assert_unsupported_envelope(extract_data_from_artifact(result.artifacts[0]))


@pytest.fixture
def wire_tenant(integration_db):
    """Seed the property_list-capable tenant + ``test_adv`` principal for the A2A subtests.

    ``seed_property_list_capability_tenant`` creates principal ``test_adv`` with
    ACCESS_TOKEN, so the A2A token->DB->identity chain resolves to the owner.
    The create path's setup-checklist gate is bypassed by ``x-test-session-id``
    (the production-shaped path), not a ``validate_setup_complete`` monkeypatch.
    """
    with get_db_session() as session:
        seed_property_list_capability_tenant(
            session,
            tenant_id=TENANT_ID,
            tenant_name="Property List Wire Publisher",
            subdomain="prop-list-wire",
            access_token=ACCESS_TOKEN,
            product_id=PRODUCT_ID,
            product_name="Display Ads (property targeting allowed)",
            property_targeting_allowed=True,
        )
        session.commit()
    yield TENANT_ID


@pytest.fixture
def non_compiling_adapter(monkeypatch):
    """Pin the mock adapter to a no-compile-path declaration for reject-path legs.

    (The REST leg's harness mocks ``get_adapter`` with a bare MagicMock whose
    class lacks the attribute, so it is pin-independent by construction.)
    """
    from src.adapters.mock_ad_server import MockAdServer

    monkeypatch.setattr(MockAdServer, "supports_property_list_targeting", False)


@pytest.fixture
def wire_media_buy(wire_tenant):
    """Seed a media buy + package under the wire tenant's ``test_adv`` principal.

    Returns ``(media_buy_id, package_id)`` so the A2A update subtest reaches the
    capability gate rather than an ownership/not-found rejection.
    """
    media_buy_id = "mb_wire_update"
    package_id = "pkg_wire_update"
    with get_db_session() as session:
        seed_media_buy_with_package(
            session,
            tenant_id=wire_tenant,
            principal_id="test_adv",
            product_id=PRODUCT_ID,
            media_buy_id=media_buy_id,
            package_id=package_id,
        )
        session.commit()
    return media_buy_id, package_id


def test_rest_create_media_buy_property_list_unsupported_envelope(integration_db):
    """REST surfaces the two-layer UNSUPPORTED_FEATURE envelope via the canonical harness.

    ``MediaBuyCreateEnv.call_via(REST)`` drives FastAPI TestClient -> route ->
    _impl; ``wire_error_envelope`` is the real HTTP response body. The env mocks
    ``get_adapter`` (a MagicMock class lacks ``supports_property_list_targeting``
    → the boundary helper reads ``False`` → reject), and the seeded product sets
    ``property_targeting_allowed=True`` so the #1276 product gate passes first.
    """
    with MediaBuyCreateEnv(tenant_id="proplistwire") as env:
        tenant, _principal = env.setup_default_data()
        _seed_property_list_product(env, tenant)

        result = env.call_via(Transport.REST, req=_build_create_request())

        assert result.is_error, f"REST: property_list on an incapable adapter must reject; got {result.payload!r}"
        _assert_unsupported_envelope(result.wire_error_envelope)


@pytest.mark.asyncio
async def test_mcp_create_media_buy_property_list_unsupported_envelope(wire_tenant, non_compiling_adapter):
    """MCP surfaces the two-layer UNSUPPORTED_FEATURE envelope through the real FastMCP Client.

    The harness ``call_mcp`` invokes the tool function directly, so the raised
    ``AdCPError`` never becomes the ``ToolError`` the FastMCP server emits on the
    wire (the translation lives at the server layer). Driving the real
    ``Client(mcp)`` pipeline produces that ``ToolError``, whose serialized payload
    is the two-layer envelope JSON — parsed structurally here (not asserted as
    ``str(exc)`` substrings, which the request body alone could satisfy) and
    pinned via ``assert_envelope_shape``.
    """
    import json
    from unittest.mock import patch

    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from src.core.main import mcp

    headers = {
        "x-adcp-auth": ACCESS_TOKEN,
        "x-adcp-tenant": TENANT_ID,
        "x-test-session-id": "prop-list-wire-mcp-session",
    }
    arguments = create_test_property_list_create_params(PRODUCT_ID)
    # Each module binds get_http_headers via ``from … import`` so each needs its
    # own patch. testing_hooks is the one that turns x-test-session-id into
    # ``testing_ctx.test_session_id`` — the production-shaped setup-checklist
    # bypass (no validate_setup_complete monkeypatch).
    with (
        patch("src.core.auth.get_http_headers", return_value=headers),
        patch("src.core.transport_helpers.get_http_headers", return_value=headers),
        patch("src.core.testing_hooks.get_http_headers", return_value=headers),
        patch("src.core.mcp_auth_middleware.get_http_headers", return_value=headers),
    ):
        async with Client(mcp) as client:
            with pytest.raises(ToolError) as excinfo:
                await client.call_tool("create_media_buy", arguments)

    _assert_unsupported_envelope(json.loads(str(excinfo.value)))


@pytest.mark.asyncio
async def test_a2a_create_media_buy_property_list_unsupported_envelope(wire_tenant, non_compiling_adapter):
    """A2A create surfaces UNSUPPORTED_FEATURE as a failed-Task envelope artifact.

    Drives the real ``on_message_send`` -> auth chain -> skill dispatch. The
    skill raises ``AdCPCapabilityNotSupportedError``; the A2A boundary captures
    it into a ``Task`` whose ``artifacts[0]`` DataPart must carry ``field`` +
    ``suggestion`` — pinning that the failed-Task framing does not drop them.
    ``x-test-session-id`` bypasses the create setup-checklist gate.
    """
    skill_params = create_test_property_list_create_params(PRODUCT_ID)
    headers = {
        "x-adcp-auth": ACCESS_TOKEN,
        "x-adcp-tenant": TENANT_ID,
        "x-test-session-id": "prop-list-wire-a2a-session",
    }
    result = await _drive_a2a_skill("create_media_buy", skill_params, headers)
    _assert_a2a_unsupported(result)


@pytest.mark.asyncio
async def test_a2a_update_media_buy_property_list_unsupported_envelope(wire_media_buy, non_compiling_adapter):
    """A2A update surfaces UNSUPPORTED_FEATURE as a failed-Task envelope artifact.

    The update path has no setup-checklist gate, so no ``x-test-session-id`` is
    needed. Same failed-Task framing contract as create: ``field`` +
    ``suggestion`` must survive on the update path's ``on_message_send`` artifact.
    """
    media_buy_id, package_id = wire_media_buy
    skill_params = {"media_buy_id": media_buy_id, "packages": _build_update_packages(package_id)}
    headers = {"x-adcp-auth": ACCESS_TOKEN, "x-adcp-tenant": TENANT_ID}
    result = await _drive_a2a_skill("update_media_buy", skill_params, headers)
    _assert_a2a_unsupported(result)
