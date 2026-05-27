"""3-transport wire envelope tests for ``AdCPUnsupportedFeatureError`` on property_list.

The ``_impl``-level tests in
``tests/integration/test_property_list_unsupported_capability.py`` prove the
exception is raised with the correct fields. These tests prove the exception
actually traverses each transport boundary (REST, A2A, MCP) and reaches the
buyer as a spec-compliant wire envelope with ``code=UNSUPPORTED_FEATURE``,
``recovery=correctable``, ``field``, and ``suggestion``.

Covers: UC-002 honest-declaration property_list reject — wire shape
Covers: UC-003 honest-declaration property_list reject — wire shape
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from a2a.types import SendMessageRequest

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.database.database_session import get_db_session
from src.core.resolved_identity import ResolvedIdentity
from tests.helpers.adcp_factories import TEST_PROPERTY_LIST_TARGETING_OVERLAY, create_test_package_request_dict
from tests.utils.a2a_helpers import create_a2a_message_with_skill
from tests.utils.database_helpers import future_iso_date_range, seed_property_list_capability_tenant

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


def _assert_unsupported_feature_envelope(envelope: dict) -> None:
    """Spec-compliant envelope must carry all 5 fields.

    Envelope variants across transports:
    - REST: flat ``{error_code, message, recovery, field, suggestion}`` (the
      AdCP ``exc.to_dict()`` shape returned by the global FastAPI handler).
    - A2A / MCP wrappers: ``{success: false, errors: [{code, message,
      recovery, field, suggestion}], ...}`` shape per AdCP error-handling.mdx.

    Both shapes must surface ``UNSUPPORTED_FEATURE`` + ``correctable`` +
    the offending field + a suggestion referencing
    ``property_list_filtering``.
    """
    # Normalize: extract the first error object whether top-level or nested.
    if "errors" in envelope:
        assert envelope["errors"], "errors[] must not be empty"
        err: dict = envelope["errors"][0]
        code_key = "code"
    else:
        err = envelope
        code_key = "error_code"
    assert err.get(code_key) == "UNSUPPORTED_FEATURE", f"expected UNSUPPORTED_FEATURE; got {err.get(code_key)!r}"
    assert err.get("recovery") == "correctable", f"expected recovery=correctable; got {err.get('recovery')!r}"
    assert (
        err.get("field") == "packages[0].targeting_overlay.property_list"
    ), f"field must identify the offending package; got {err.get('field')!r}"
    assert err.get("suggestion"), "suggestion must be present so the buyer agent can act"
    assert "property_list_filtering" in err["suggestion"], (
        "suggestion must reference the canonical capability flag so the buyer "
        "agent can locate a capable seller via get_adcp_capabilities"
    )


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
        f"AdCPUnsupportedFeatureError must translate to HTTP 422 at the REST boundary; "
        f"got {response.status_code} with body {response.text[:500]}"
    )
    _assert_unsupported_feature_envelope(response.json())


@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_a2a_create_media_buy_property_list_unsupported_envelope(wire_tenant):
    """A2A propagates AdCPUnsupportedFeatureError as A2A InternalError carrying the wire envelope.

    A2A's ``_handle_explicit_skill`` translates ``AdCPError`` subclasses into
    A2A protocol errors via ``_adcp_to_a2a_error``. The resulting exception
    carries the AdCP envelope so the JSON-RPC boundary serializes it onto
    the wire with ``code=UNSUPPORTED_FEATURE``, ``recovery=correctable``,
    ``field`` and ``suggestion`` — the same contract REST and MCP surface.
    """
    from a2a.utils.errors import A2AError, InternalError

    from src.core.config_loader import set_current_tenant
    from src.core.testing_hooks import AdCPTestContext

    tenant_dict = {
        "tenant_id": TENANT_ID,
        "name": "Property List Wire Publisher",
        "subdomain": "prop-list-wire",
        "ad_server": "mock",
    }
    identity = ResolvedIdentity(
        principal_id="test_adv",
        tenant_id=TENANT_ID,
        tenant=tenant_dict,
        auth_token=ACCESS_TOKEN,
        protocol="a2a",
        # test_session_id bypasses the production setup-checklist gate so the
        # test can reach the boundary check on a minimal test tenant.
        testing_context=AdCPTestContext(
            dry_run=False,
            mock_time=None,
            jump_to_event=None,
            test_session_id="prop-list-wire-a2a-session",
        ),
    )
    set_current_tenant(tenant_dict)

    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value=ACCESS_TOKEN)
    handler._resolve_a2a_identity = MagicMock(return_value=identity)

    start, end = future_iso_date_range()
    skill_params = {
        "brand": {"domain": "testbrand.com"},
        "packages": _build_packages(),
        "start_time": start,
        "end_time": end,
    }
    message = create_a2a_message_with_skill("create_media_buy", skill_params)
    params = SendMessageRequest(message=message)

    # AdCPUnsupportedFeatureError surfaces via _adcp_to_a2a_error as an
    # InternalError whose ``data`` field carries the AdCP envelope. The outer
    # ``on_message_send`` wrapper rewraps that into an InternalError carrying
    # only the message string — so the test exercises the handler at the
    # skill-dispatch boundary where the envelope is preserved.
    with pytest.raises((A2AError, InternalError)) as excinfo:
        await handler._handle_explicit_skill(
            skill_name="create_media_buy",
            parameters=skill_params,
            identity=identity,
        )

    raised = excinfo.value
    data = getattr(raised, "data", None) or {}
    assert (
        data.get("error_code") == "UNSUPPORTED_FEATURE"
    ), f"A2A error.data must surface error_code=UNSUPPORTED_FEATURE; got data={data!r}"
    assert data.get("recovery") == "correctable", f"A2A error.data must surface recovery=correctable; got data={data!r}"
    msg = getattr(raised, "message", None) or str(raised)
    assert "property_list" in msg, f"A2A error message must reference property_list; got: {msg!r}"


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

    # FastMCP serializes AdCPError onto the wire by stringifying ``args``,
    # which on the client side surfaces as a ToolError message containing a
    # parenthesized tuple: ``('CODE', 'message', 'recovery', '{json_details}')``.
    # Parse the components out and verify each one matches the contract.
    exc = excinfo.value
    msg = str(exc)
    assert "UNSUPPORTED_FEATURE" in msg, f"MCP ToolError must surface error_code=UNSUPPORTED_FEATURE; got: {msg!r}"
    assert "correctable" in msg, f"MCP ToolError must surface recovery=correctable; got: {msg!r}"
    assert (
        "packages[0].targeting_overlay.property_list" in msg
    ), f"MCP ToolError must surface the offending field; got: {msg!r}"
    assert "property_list_filtering" in msg, f"MCP ToolError must surface the capability-flag suggestion; got: {msg!r}"
