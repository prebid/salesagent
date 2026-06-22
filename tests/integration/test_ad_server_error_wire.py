"""Wire envelope tests for an ad-server credential 403 (operator access denied).

The unit translation (``AdCPConfigurationError`` -> wire ``SERVICE_UNAVAILABLE`` /
``terminal``) is pinned in ``test_typed_error_wire_codes.py``. These tests prove
the same ad-server 403 — raised deep in the adapter write
(``_execute_adapter_media_buy_creation``, where ``wrap_request_errors`` maps a
403 HTTPError) and propagated through ``create_media_buy`` — reaches the buyer
with the SAME spec-correct two-layer envelope on every transport the buyer
actually uses:

- ``code = SERVICE_UNAVAILABLE`` — the leak-safe wire translation of the internal
  ``CONFIGURATION_ERROR`` (which is overloaded with the tenant-secret-decrypt
  path, so the raw internal code must never reach the wire).
- ``recovery = terminal`` — the spec's "requires human action" carrier. A buyer
  has no lever to fix the tenant operator's ad-server credential, so it must NOT
  be told to retry (transient) or fix-and-resend (correctable); an operator must
  fix the tenant config. Pinned spec-uniformly across adapters by the
  status->recovery tables in ``src/core/exceptions.py``.

The injection point is ``_execute_adapter_media_buy_creation`` raising the exact
error an ad-server 403 produces — the same class, on the real create path, so the
adapter-error propagation (the bare re-raise) and the per-transport boundary
serialization are both exercised. A per-transport drop of the wire code or the
recovery carrier reddens here; the unit envelope test cannot see a transport that
re-serializes.

Covers: ad-server 403 wire shape (create) across REST / MCP / A2A.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from a2a.types import Message, Task

from src.core.database.database_session import get_db_session
from src.core.exceptions import adcp_adapter_error_for_http_status
from src.core.schemas import CreateMediaBuyRequest
from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.transport import Transport
from tests.helpers import assert_envelope_shape
from tests.helpers.adcp_factories import create_test_package_request_dict
from tests.utils.a2a_helpers import drive_a2a_skill, extract_data_from_artifact
from tests.utils.database_helpers import future_iso_date_range, seed_property_list_capability_tenant

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "test_ad_server_403_wire"
ACCESS_TOKEN = "test_token_ad_server_403_wire"
PRODUCT_ID = "prod_ad_server_403_wire"

# The exact error an ad-server 403 produces (wrap_request_errors / the httpx dual
# both route a 403 through this ad-server factory): CONFIGURATION_ERROR / terminal.
_AD_SERVER_403 = adcp_adapter_error_for_http_status(403, "Ad-server flight POST denied (HTTP 403)")
_EXECUTE_ADAPTER = "src.core.tools.media_buy_create._execute_adapter_media_buy_creation"


def _plain_create_params(product_id: str) -> dict:
    """create_media_buy params with one plain package (no property_list overlay).

    A 403 is a generic ad-server write failure, not property_list-specific; a plain
    package reaches ``_execute_adapter_media_buy_creation`` without the capability gate.
    """
    start, end = future_iso_date_range()
    return {
        "brand": {"domain": "testbrand.com"},
        "packages": [
            create_test_package_request_dict(product_id=product_id, pricing_option_id="cpm_usd_fixed", budget=5000.0)
        ],
        "start_time": start,
        "end_time": end,
        "idempotency_key": f"ad-server-403-{product_id}",
    }


def _assert_ad_server_403_envelope(envelope: dict) -> None:
    """Assert the spec two-layer ad-server-403 envelope: leak-safe code + terminal recovery."""
    assert_envelope_shape(envelope, "SERVICE_UNAVAILABLE", recovery="terminal")
    # Leak-safe: the raw internal code (shared with the secret-decrypt path) is never on the wire.
    assert "CONFIGURATION_ERROR" not in (envelope["adcp_error"]["code"], envelope["errors"][0]["code"])


@pytest.fixture
def wire_tenant(integration_db):
    """Seed an AUTO-APPROVE create-capable tenant + ``test_adv`` principal bound to ACCESS_TOKEN.

    ``human_review_required=False`` so the create calls the adapter synchronously
    (rather than returning the ``submitted`` approval variant before the adapter
    runs) — the ad-server 403 only surfaces when the adapter write is actually made.
    """
    from sqlalchemy import select

    from src.core.database.models import Tenant

    with get_db_session() as session:
        seed_property_list_capability_tenant(
            session,
            tenant_id=TENANT_ID,
            tenant_name="Ad-Server 403 Wire Publisher",
            subdomain="ad-server-403-wire",
            access_token=ACCESS_TOKEN,
            product_id=PRODUCT_ID,
            product_name="Display Ads (ad-server 403 wire)",
            property_targeting_allowed=True,
        )
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=TENANT_ID)).one()
        tenant.human_review_required = False
        session.commit()
    yield TENANT_ID


def test_rest_create_media_buy_ad_server_403_envelope(integration_db):
    """REST surfaces the two-layer SERVICE_UNAVAILABLE/terminal envelope via the canonical harness."""
    with MediaBuyCreateEnv(tenant_id="adserver403wire") as env:
        tenant, _principal = env.setup_default_data()
        env.setup_product_chain(tenant, product_id="prod_1")
        req = CreateMediaBuyRequest(**_plain_create_params("prod_1"))

        with patch(_EXECUTE_ADAPTER, side_effect=_AD_SERVER_403):
            result = env.call_via(Transport.REST, req=req)

        assert result.is_error, f"REST: an ad-server 403 must reject; got {result.payload!r}"
        _assert_ad_server_403_envelope(result.wire_error_envelope)


@pytest.mark.asyncio
async def test_mcp_create_media_buy_ad_server_403_envelope(wire_tenant):
    """MCP surfaces the two-layer SERVICE_UNAVAILABLE/terminal envelope through the real FastMCP Client.

    The raised AdCPConfigurationError becomes the FastMCP ToolError whose serialized
    payload is the two-layer envelope JSON — parsed structurally, not asserted as a
    str(exc) substring the request body alone could satisfy.
    """
    import json

    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from src.core.main import mcp

    headers = {
        "x-adcp-auth": ACCESS_TOKEN,
        "x-adcp-tenant": TENANT_ID,
        "x-test-session-id": "ad-server-403-wire-mcp-session",
    }
    arguments = _plain_create_params(PRODUCT_ID)
    with (
        patch("src.core.auth.get_http_headers", return_value=headers),
        patch("src.core.transport_helpers.get_http_headers", return_value=headers),
        patch("src.core.testing_hooks.get_http_headers", return_value=headers),
        patch("src.core.mcp_auth_middleware.get_http_headers", return_value=headers),
        patch(_EXECUTE_ADAPTER, side_effect=_AD_SERVER_403),
    ):
        async with Client(mcp) as client:
            with pytest.raises(ToolError) as excinfo:
                await client.call_tool("create_media_buy", arguments)

    _assert_ad_server_403_envelope(json.loads(str(excinfo.value)))


@pytest.mark.asyncio
async def test_a2a_create_media_buy_ad_server_403_envelope(wire_tenant):
    """A2A surfaces the ad-server 403 as a failed-Task envelope artifact.

    Drives the real ``on_message_send`` -> auth chain -> skill dispatch; the skill's
    create path raises the 403-class error, which the A2A boundary captures into a
    ``Task`` whose ``artifacts[0]`` DataPart must carry SERVICE_UNAVAILABLE/terminal —
    pinning that the failed-Task framing preserves the recovery carrier.
    """
    skill_params = _plain_create_params(PRODUCT_ID)
    headers = {
        "x-adcp-auth": ACCESS_TOKEN,
        "x-adcp-tenant": TENANT_ID,
        "x-test-session-id": "ad-server-403-wire-a2a-session",
    }
    with patch(_EXECUTE_ADAPTER, side_effect=_AD_SERVER_403):
        result: Task | Message = await drive_a2a_skill(
            "create_media_buy", skill_params, headers, auth_token=ACCESS_TOKEN
        )

    assert isinstance(result, Task), f"A2A error must surface as a failed Task; got {type(result)!r}"
    assert result.artifacts, "A2A error Task must carry an artifact with the envelope"
    _assert_ad_server_403_envelope(extract_data_from_artifact(result.artifacts[0]))
