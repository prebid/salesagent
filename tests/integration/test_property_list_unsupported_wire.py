"""Wire envelope tests for ``AdCPCapabilityNotSupportedError`` on property_list.

The ``_impl``-level tests in
``tests/integration/test_property_list_unsupported_capability.py`` prove the
exception is raised with the correct fields on both create and update. These
tests prove it actually traverses the transport boundaries and reaches the
buyer as a spec-compliant two-layer envelope (``code=UNSUPPORTED_FEATURE``,
``recovery=correctable``, ``field``, ``suggestion``):

- create: REST, A2A, and MCP.
- update: A2A only — per-package property_list targeting is unreachable via the
  REST update body (no ``packages`` field) and the MCP update wrapper's
  PackageUpdate schema-alignment, a pre-existing transport asymmetry.

The A2A subtests drive ``handler.on_message_send`` and assert on the real
``Task`` artifact (DataPart), because the A2A boundary's ``_adcp_to_a2a_error``
is where ``field``/``suggestion`` could be dropped — a test stopping at the
skill handler would never observe that loss.

Covers: UC-002 honest-declaration property_list reject — wire shape (create)
Covers: UC-003 honest-declaration property_list reject — wire shape (update)
"""

from __future__ import annotations

from types import MappingProxyType

import pytest
from a2a.server.context import ServerCallContext
from a2a.types import Message, SendMessageRequest, Task

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.auth_context import AUTH_CONTEXT_STATE_KEY, AuthContext
from src.core.database.database_session import get_db_session
from tests.helpers import assert_envelope_shape
from tests.helpers.adcp_factories import TEST_PROPERTY_LIST_TARGETING_OVERLAY, create_test_package_request_dict
from tests.utils.a2a_helpers import create_a2a_message_with_skill, extract_data_from_artifact
from tests.utils.database_helpers import (
    future_iso_date_range,
    seed_media_buy_with_package,
    seed_property_list_capability_tenant,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "test_property_list_wire"
ACCESS_TOKEN = "test_token_property_list_wire"


@pytest.fixture
def wire_tenant(integration_db, monkeypatch):
    """Tenant configured exactly like the ``_impl`` tests but with a token usable by every transport.

    The production setup-checklist gate (SSO + Authorized Properties) runs in
    ``_create_media_buy_impl`` before the property_list boundary check. Wire
    tests need to reach the property_list check on a minimal test tenant, so
    we patch the setup-checklist validator to a no-op for the duration of
    the fixture. This is the same shape ``test_session_id`` uses internally
    in ``_create_media_buy_impl``, but the patch keeps the test transport-
    agnostic (every transport surface honors it identically without needing
    each route to wire ``x-test-session-id`` plumbing).
    """
    monkeypatch.setattr(
        "src.core.tools.media_buy_create.validate_setup_complete",
        lambda tenant_id: None,
    )
    with get_db_session() as session:
        seed_property_list_capability_tenant(
            session,
            tenant_id=TENANT_ID,
            tenant_name="Property List Wire Publisher",
            subdomain="prop-list-wire",
            access_token=ACCESS_TOKEN,
            product_id="prod_property_targeting_allowed",
            product_name="Display Ads (property targeting allowed)",
            property_targeting_allowed=True,
        )
        session.commit()
    yield TENANT_ID


def _build_packages() -> list[dict]:
    """Single-package payload carrying property_list — same shape across all 3 transports."""
    return [
        create_test_package_request_dict(
            product_id="prod_property_targeting_allowed",
            pricing_option_id="cpm_usd_fixed",
            budget=5000.0,
            targeting_overlay=TEST_PROPERTY_LIST_TARGETING_OVERLAY,
        )
    ]


@pytest.fixture
def wire_media_buy(wire_tenant):
    """Seed a media buy + package under the wire tenant's ``test_adv`` principal.

    ``seed_property_list_capability_tenant`` creates principal ``test_adv`` with
    the wire ACCESS_TOKEN, so the token→DB→identity chain resolves to the same
    principal that owns this buy — the update reaches the capability gate rather
    than an ownership/not-found rejection. Returns ``(media_buy_id, package_id)``.
    """
    media_buy_id = "mb_wire_update"
    package_id = "pkg_wire_update"
    with get_db_session() as session:
        seed_media_buy_with_package(
            session,
            tenant_id=wire_tenant,
            principal_id="test_adv",
            product_id="prod_property_targeting_allowed",
            media_buy_id=media_buy_id,
            package_id=package_id,
        )
        session.commit()
    return media_buy_id, package_id


def _build_update_packages(package_id: str) -> list[dict]:
    """Update-request packages that add property_list targeting to an existing package."""
    return [{"package_id": package_id, "targeting_overlay": TEST_PROPERTY_LIST_TARGETING_OVERLAY}]


def _assert_unsupported_envelope(envelope: dict, *, package_index: int = 0) -> None:
    """Assert the spec two-layer UNSUPPORTED_FEATURE envelope + machine-actionable fields.

    Uses the canonical ``assert_envelope_shape`` (two-layer invariant
    ``adcp_error.code == errors[0].code``, recovery, message) and additionally
    pins ``field`` + ``suggestion`` on ``errors[0]`` — the buyer-agent correction
    context that the A2A boundary's ``_adcp_to_a2a_error`` is prone to dropping,
    so the wire test must observe it on the real envelope, not a reconstruction.
    """
    assert_envelope_shape(
        envelope,
        "UNSUPPORTED_FEATURE",
        recovery="correctable",
        message_substr="does not support property_list",
    )
    err = envelope["errors"][0]
    assert err.get("field") == f"packages[{package_index}].targeting_overlay.property_list", (
        f"field must identify the offending package; got {err.get('field')!r}"
    )
    assert err.get("suggestion") and "property_list_filtering" in err["suggestion"], (
        f"suggestion must reference the property_list_filtering capability flag; got {err.get('suggestion')!r}"
    )


def _assert_unsupported_mcp_tool_error(exc: Exception, *, package_index: int = 0) -> None:
    """Assert the MCP ToolError surfaces the UNSUPPORTED_FEATURE envelope content.

    FastMCP serializes the AdCPError onto the wire by stringifying its args, so
    the client-side ``ToolError`` carries the envelope fields in its message
    (there is no structured ``.envelope`` on the client side). Asserting on that
    serialized content pins OUR envelope fields, not framework-internal text.
    """
    msg = str(exc)
    assert "UNSUPPORTED_FEATURE" in msg, f"MCP ToolError must surface error_code=UNSUPPORTED_FEATURE; got: {msg!r}"
    assert "correctable" in msg, f"MCP ToolError must surface recovery=correctable; got: {msg!r}"
    assert f"packages[{package_index}].targeting_overlay.property_list" in msg, (
        f"MCP ToolError must surface the offending field; got: {msg!r}"
    )
    assert "property_list_filtering" in msg, f"MCP ToolError must surface the capability-flag suggestion; got: {msg!r}"


async def _drive_a2a_skill(skill_name: str, skill_params: dict, headers: dict[str, str]) -> Task | Message:
    """Drive a skill through the real A2A boundary (on_message_send + token->DB->identity).

    Populates the AuthContext the SDK transport middleware would build from the
    wire so the auth chain resolves with no mocked identity seams, then returns
    the resulting Task for envelope/artifact assertions.
    """
    message = create_a2a_message_with_skill(skill_name, skill_params)
    server_context = ServerCallContext(
        state={AUTH_CONTEXT_STATE_KEY: AuthContext(auth_token=ACCESS_TOKEN, headers=MappingProxyType(headers))}
    )
    return await AdCPRequestHandler().on_message_send(SendMessageRequest(message=message), server_context)


def _assert_a2a_unsupported(result: object, *, package_index: int = 0) -> None:
    """Assert an A2A result is a failed Task whose artifact carries the UNSUPPORTED_FEATURE envelope."""
    assert isinstance(result, Task), f"A2A error must surface as a failed Task; got {type(result)!r}"
    assert result.artifacts, "A2A error Task must carry an artifact with the envelope"
    _assert_unsupported_envelope(extract_data_from_artifact(result.artifacts[0]), package_index=package_index)


@pytest.mark.requires_db
def test_rest_create_media_buy_property_list_unsupported_envelope(wire_tenant):
    """REST POST /api/v1/media-buys returns 422 + UNSUPPORTED_FEATURE envelope."""
    from starlette.testclient import TestClient

    from src.app import app

    start, end = future_iso_date_range()
    body = {
        "brand": {"domain": "testbrand.com"},
        "packages": _build_packages(),
        "start_time": start,
        "end_time": end,
    }

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/media-buys",
        json=body,
        headers={
            "x-adcp-auth": ACCESS_TOKEN,
            # x-test-session-id bypasses the production setup-checklist gate
            # (SSO + Authorized Properties) so the test can reach the boundary
            # check on a minimal test tenant.
            "x-test-session-id": "prop-list-wire-rest-session",
        },
    )

    assert response.status_code == 422, (
        f"AdCPCapabilityNotSupportedError must translate to HTTP 422 at the REST boundary; "
        f"got {response.status_code} with body {response.text[:500]}"
    )
    _assert_unsupported_envelope(response.json())


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_a2a_create_media_buy_property_list_unsupported_envelope(wire_tenant):
    """A2A surfaces AdCPCapabilityNotSupportedError as a failed-Task envelope artifact.

    Drives the canonical ``on_message_send`` entry point (not
    ``_handle_explicit_skill`` directly) with the real token -> DB -> identity
    auth chain. The skill handler raises ``AdCPCapabilityNotSupportedError``; the
    A2A boundary captures it into a ``Task`` whose ``artifacts[0]`` DataPart
    carries the spec two-layer envelope — ``code=UNSUPPORTED_FEATURE``,
    ``recovery=correctable``, ``field``, and ``suggestion`` — the same
    machine-actionable contract REST and MCP surface.
    """
    start, end = future_iso_date_range()
    skill_params = {
        "brand": {"domain": "testbrand.com"},
        "packages": _build_packages(),
        "start_time": start,
        "end_time": end,
    }
    # x-test-session-id bypasses the setup-checklist gate, same as the REST test.
    headers = {
        "x-adcp-auth": ACCESS_TOKEN,
        "x-adcp-tenant": TENANT_ID,
        "x-test-session-id": "prop-list-wire-a2a-session",
    }
    result = await _drive_a2a_skill("create_media_buy", skill_params, headers)
    _assert_a2a_unsupported(result)


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_mcp_create_media_buy_property_list_unsupported_envelope(wire_tenant):
    """MCP create_media_buy raises ToolError; the envelope carries UNSUPPORTED_FEATURE.

    The MCP boundary translates AdCPError subclasses into ``ToolError`` whose
    structured data carries the AdCP envelope. Buyers parse that envelope via
    the fastmcp client.
    """
    from unittest.mock import patch

    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from src.core.main import mcp

    start, end = future_iso_date_range()
    # Auth headers are patched into both modules that read get_http_headers,
    # mirroring the harness pattern in tests/harness/_base.py.
    # x-test-session-id bypasses the production setup-checklist gate.
    headers = {
        "x-adcp-auth": ACCESS_TOKEN,
        "x-adcp-tenant": TENANT_ID,
        "x-test-session-id": "prop-list-wire-mcp-session",
    }
    arguments = {
        "brand": {"domain": "testbrand.com"},
        "packages": _build_packages(),
        "start_time": start,
        "end_time": end,
    }

    with patch("src.core.transport_helpers.get_http_headers", return_value=headers):
        with patch("src.core.mcp_auth_middleware.get_http_headers", return_value=headers):
            async with Client(mcp) as client:
                with pytest.raises(ToolError) as excinfo:
                    await client.call_tool("create_media_buy", arguments)

    _assert_unsupported_mcp_tool_error(excinfo.value)


# ─── update_media_buy boundary ──────────────────────────────────────────────
#
# Per-package property_list targeting on update is reachable only via A2A: the
# REST update body (UpdateMediaBuyBody) carries no ``packages`` field, and the
# MCP update wrapper's PackageUpdate schema-alignment rejects the package shape
# that ``_update_media_buy_impl`` and the A2A skill accept — a pre-existing
# three-transport asymmetry tracked separately. The update path runs the SAME
# raise_if_property_list_unsupported gate as create (after adapter resolution,
# before the dry_run branch) and has no setup-checklist gate.


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_a2a_update_media_buy_property_list_unsupported_envelope(wire_media_buy):
    """A2A update_media_buy surfaces UNSUPPORTED_FEATURE as a failed-Task envelope artifact.

    Drives the real ``on_message_send`` -> auth chain -> update-skill dispatch (no
    mocked identity seams). The skill raises ``AdCPCapabilityNotSupportedError``;
    the A2A boundary captures it into a ``Task`` whose ``artifacts[0]`` DataPart
    must carry ``field`` + ``suggestion`` — pinning that ``_adcp_to_a2a_error``
    does not drop them on the update path.
    """
    media_buy_id, package_id = wire_media_buy
    skill_params = {"media_buy_id": media_buy_id, "packages": _build_update_packages(package_id)}
    headers = {"x-adcp-auth": ACCESS_TOKEN, "x-adcp-tenant": TENANT_ID}
    result = await _drive_a2a_skill("update_media_buy", skill_params, headers)
    _assert_a2a_unsupported(result)
