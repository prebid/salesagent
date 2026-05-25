"""Cross-transport wire envelope contract test for delegate error translation.

In-process integration test: builds the real ASGI app via
:func:`core.main.build_app` and drives both transports through
:class:`httpx.ASGITransport`. Asserts that a typed/validation error raised
inside ``_delegate_*`` projects onto the spec-mandated AdCP error envelope
on BOTH wire paths:

* **MCP** (``/mcp/``): ``CallToolResult.structuredContent.adcp_error.code``
* **A2A** (host root, JSON-RPC ``message/send``):
  ``result.artifacts[0].parts[0].data.adcp_error.code``

Why this exists alongside ``tests/unit/test_delegate_typed_error_translation.py``:
the unit test asserts on the in-process framework :class:`AdcpError` the
delegate re-raises. It does NOT exercise the A2A executor's catch path
(``_send_adcp_error``) or the MCP dispatcher's projection — a regression
that mangles the wire envelope on either side (or wraps the typed error
as ``INTERNAL_ERROR`` at the framework boundary) passes the unit test
and fails buyers. This test pins the wire surface end-to-end.

The first case (``ValidationError → INVALID_REQUEST``) is the regression
test for the fix that catches pydantic ``ValidationError`` inside the
:func:`core.platforms._delegate.translate_adcp_errors` decorator. Without
that catch, the framework's generic ``except Exception`` wraps it as
``INTERNAL_ERROR: "Platform method 'update_media_buy' raised ValidationError"``
— on A2A this lands as ``"Task failed"`` with no actionable signal.
"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.harness._asgi_app import run_on_app_loop


def _call_mcp_raw(tool_name: str, arguments: dict[str, Any], authenticated_principal: dict[str, str]) -> Any:
    """Drive a raw MCP tool call through the in-process ASGI app.

    Uses ``call_tool_mcp`` so tests can inspect ``CallToolResult`` error
    envelopes instead of FastMCP raising ``ToolError`` and dropping structured
    fields such as ``recovery``.
    """
    token = authenticated_principal["access_token"]
    tenant_id = authenticated_principal["tenant_id"]

    def _factory(app: Any) -> Any:
        def httpx_factory(**hk: Any) -> httpx.AsyncClient:
            hk.setdefault("timeout", 30.0)
            hk["transport"] = httpx.ASGITransport(app=app)
            hk["base_url"] = "http://testserver"
            return httpx.AsyncClient(**hk)

        transport = StreamableHttpTransport(
            url="http://testserver/mcp/",
            headers={
                "x-adcp-auth": token,
                "x-adcp-tenant": tenant_id,
            },
            httpx_client_factory=httpx_factory,
        )

        async def _call() -> Any:
            async with Client(transport) as client:
                return await client.call_tool_mcp(tool_name, arguments)

        return _call()

    return run_on_app_loop(_factory)


def _call_a2a_raw(skill: str, parameters: dict[str, Any], authenticated_principal: dict[str, str]) -> dict[str, Any]:
    """Drive an A2A JSON-RPC ``message/send`` request through the ASGI app."""
    token = authenticated_principal["access_token"]
    tenant_id = authenticated_principal["tenant_id"]
    message = {
        "messageId": str(uuid.uuid4()),
        "contextId": str(uuid.uuid4()),
        "role": "user",
    }
    message["parts"] = [{"kind": "data", "data": {"skill": skill, "parameters": parameters}}]
    jsonrpc_body: dict[str, Any] = {}
    jsonrpc_body["jsonrpc"] = "2.0"
    jsonrpc_body["id"] = str(uuid.uuid4())
    jsonrpc_body["method"] = "message/send"
    jsonrpc_body["params"] = {"message": message}

    def _factory(app: Any) -> Any:
        async def _call() -> dict[str, Any]:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=30.0) as client:
                response = await client.post(
                    "/",
                    json=jsonrpc_body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "x-adcp-tenant": tenant_id,
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                return response.json()

        return _call()

    return run_on_app_loop(_factory)


def _extract_a2a_data(body: dict[str, Any], *, expected_state: str) -> dict[str, Any]:
    """Extract the A2A task DataPart and assert the task state."""
    assert "error" not in body, f"A2A response should not be a top-level JSON-RPC error; got: {body!r}"
    assert "result" in body, f"A2A wire response missing 'result': {json.dumps(body)[:500]}"
    result = body["result"]
    state = (result.get("status") or {}).get("state")
    expected_states = {expected_state, expected_state.upper(), f"TASK_STATE_{expected_state.upper()}"}
    assert state in expected_states, (
        f"Expected A2A task state {expected_state!r}, got {state!r}: {json.dumps(result)[:500]}"
    )
    artifacts = result.get("artifacts") or []
    assert artifacts, f"A2A failed task must publish an artifact carrying adcp_error; got: {json.dumps(result)[:500]}"
    parts = artifacts[0].get("parts") or []
    data_part = next((p for p in parts if p.get("kind") == "data"), None)
    assert data_part is not None, (
        f"A2A artifact must include a DataPart with adcp_error; got: {json.dumps(parts)[:500]}"
    )
    data = data_part.get("data") or {}
    assert isinstance(data, dict), f"A2A DataPart.data must be a dict; got: {data_part!r}"
    return data


def _extract_a2a_adcp_error(body: dict[str, Any]) -> dict[str, Any]:
    """Extract the A2A failed-task ``adcp_error`` artifact."""
    data = _extract_a2a_data(body, expected_state="failed")
    adcp_error = data.get("adcp_error")
    assert adcp_error is not None, f"DataPart.data.adcp_error missing; got: {json.dumps(data)[:500]}"
    return adcp_error


def _bad_update_media_buy_payload(principal_id: str) -> dict[str, Any]:
    """Wire patch that triggers ``pydantic.ValidationError`` inside the
    delegate's ``_coerce_to_request_model`` step.

    Threading the needle:

    * The framework validates the body against the **library**
      ``UpdateMediaBuyRequest`` / ``PackageUpdate`` types first. Both
      declare ``extra='allow'`` (forward-compat with future spec fields),
      so an unknown key on a package passes upstream validation.
    * Inside the delegate, ``_coerce_to_request_model`` re-validates the
      patch against our **stricter** :class:`AdCPPackageUpdate` subclass
      (``extra='forbid'`` in dev/test mode). The unknown key now raises
      pydantic ``ValidationError`` — that's the exception the decorator's
      ``except ValidationError`` branch must translate to
      ``INVALID_REQUEST`` with ``recovery='correctable'``.

    Without that translation, the framework's generic ``except Exception``
    wraps it as ``INTERNAL_ERROR: "Platform method 'update_media_buy'
    raised ValidationError"`` — on A2A this lands as "Task failed".
    """
    return {
        "media_buy_id": "mb_validation_target",
        "account": {"account_id": principal_id},
        "idempotency_key": f"wire-test-{uuid.uuid4().hex[:8]}",
        "packages": [
            {
                "package_id": "pkg-1",
                # Unknown key — library PackageUpdate allows extras; our
                # AdCPPackageUpdate forbids them in dev/test mode.
                "salesagent_unknown_field": "trigger ValidationError in delegate",
            }
        ],
    }


def _create_media_buy_payload(
    authenticated_principal: dict[str, str],
    *,
    idempotency_key: str,
    budget: float,
) -> dict[str, Any]:
    """Build a valid ``create_media_buy`` wire payload for the seeded product."""
    account_id = authenticated_principal["account_id"]
    product_id = authenticated_principal["product_id"]

    return {
        "account": {"account_id": account_id},
        "idempotency_key": idempotency_key,
        "brand": {"domain": "testbrand.example"},
        "start_time": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "end_time": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        "packages": [
            {
                "product_id": product_id,
                "pricing_option_id": "cpm_usd_fixed",
                "budget": budget,
            }
        ],
    }


def _conflicting_create_media_buy_payloads(
    authenticated_principal: dict[str, str],
    *,
    key_prefix: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build create payloads that differ only by package budget."""
    first_payload = _create_media_buy_payload(
        authenticated_principal,
        idempotency_key=f"{key_prefix}-{uuid.uuid4().hex}",
        budget=1000.0,
    )
    conflict_payload = deepcopy(first_payload)
    conflict_payload["packages"][0]["budget"] = 1200.0
    return first_payload, conflict_payload


def _assert_idempotency_conflict_adcp_error(adcp_error: dict[str, Any], *, transport: str) -> None:
    """Assert the AdCP idempotency conflict wire envelope."""
    assert adcp_error.get("code") == "IDEMPOTENCY_CONFLICT", (
        f"Expected code='IDEMPOTENCY_CONFLICT' on {transport} wire, got {adcp_error.get('code')!r}"
    )
    assert adcp_error.get("recovery") == "correctable", (
        f"Expected recovery='correctable' on {transport} wire, got {adcp_error.get('recovery')!r}"
    )
    assert "field" not in adcp_error, "IDEMPOTENCY_CONFLICT must not include a field pointer"


def _assert_mcp_create_success(result: Any) -> None:
    """Assert the first MCP create call reached the real sync success shape."""
    assert result.isError is False, f"Initial create_media_buy should succeed; got: {result!r}"
    structured = result.structuredContent or {}
    assert structured.get("media_buy_id"), (
        f"Expected a real create_media_buy success before conflict, got: {structured!r}"
    )


def _assert_a2a_create_success(body: dict[str, Any]) -> None:
    """Assert the first A2A create call reached the real sync success shape."""
    data = _extract_a2a_data(body, expected_state="completed")
    assert data.get("media_buy_id"), f"Expected a real create_media_buy success before conflict, got: {data!r}"


@pytest.fixture
def authenticated_principal(integration_db):
    """Create a Tenant + Principal + Product so the bearer middleware accepts requests.

    Returns the access_token the test passes through ``x-adcp-auth`` (MCP)
    and ``Authorization: Bearer`` (A2A). Both transports share the same
    BearerTokenAuth handler — one Principal serves both.
    """
    from sqlalchemy.orm import Session as SASession

    from src.core.database.database_session import get_engine
    from tests.factories import (
        ALL_FACTORIES,
        AccountFactory,
        AgentAccountAccessFactory,
        PricingOptionFactory,
        PrincipalFactory,
        ProductFactory,
        TenantAuthConfigFactory,
        TenantFactory,
    )
    from tests.helpers.publisher_authorization import seed_verified_publisher_authorization

    engine = get_engine()
    session = SASession(bind=engine)
    for f in ALL_FACTORIES:
        f._meta.sqlalchemy_session = session

    suffix = uuid.uuid4().hex[:8]
    tenant_id = f"wire_test_{suffix}"
    subdomain = f"wire-test-{suffix}"
    principal_id = f"wire_principal_{suffix}"
    account_id = f"wire_account_{suffix}"
    access_token = f"wire_token_{suffix}"

    try:
        tenant = TenantFactory(
            tenant_id=tenant_id,
            subdomain=subdomain,
            auth_setup_mode=False,
            human_review_required=False,
            approval_mode="auto-approve",
        )
        TenantAuthConfigFactory(tenant=tenant, oidc_enabled=True)
        seed_verified_publisher_authorization(
            tenant,
            property_id=f"wire-property-{suffix}",
            publisher_domain="testbrand.example",
        )
        product = ProductFactory(
            tenant=tenant,
            product_id=f"wire-product-{suffix}",
            delivery_type="non_guaranteed",
        )
        PricingOptionFactory(product=product, pricing_model="cpm", currency="USD", is_fixed=True)
        principal = PrincipalFactory(
            tenant=tenant,
            principal_id=principal_id,
            access_token=access_token,
        )
        account = AccountFactory(tenant=tenant, account_id=account_id)
        AgentAccountAccessFactory(tenant=tenant, principal=principal, account=account)
        session.commit()
        yield {
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "account_id": account.account_id,
            "access_token": access_token,
            "product_id": product.product_id,
        }
    finally:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = None
        session.close()


@pytest.mark.requires_db
def test_validation_error_wire_envelope_mcp(authenticated_principal) -> None:
    """End-to-end MCP wire test: pydantic ``ValidationError`` from delegate
    coercion surfaces as ``adcp_error.code == "INVALID_REQUEST"`` with
    ``recovery == "correctable"`` on the structuredContent envelope.

    Without the ``ValidationError`` branch in
    :func:`core.platforms._delegate.translate_adcp_errors`, the framework
    wraps it as ``INTERNAL_ERROR``. The MCP buyer agent then has no way to
    know which field to repair and treats it as a server failure.
    """
    bad_args = _bad_update_media_buy_payload(authenticated_principal["account_id"])

    # The framework projects typed AdcpError onto BOTH ``isError=True`` +
    # ``structuredContent.adcp_error`` (per transport-errors.mdx §MCP Binding).
    # FastMCP's Client raises ToolError on isError results, so use the raw
    # ``CallToolResult`` path to preserve ``recovery``.
    result = _call_mcp_raw("update_media_buy", bad_args, authenticated_principal)

    # CallToolResult: isError=True + structuredContent.adcp_error per
    # transport-errors.mdx §MCP Binding.
    assert result.isError is True, f"Expected isError=True for delegate ValidationError; got result: {result!r}"
    structured = result.structuredContent or {}
    adcp_error = structured.get("adcp_error")
    assert adcp_error is not None, f"Expected structuredContent.adcp_error envelope, got: {structured!r}"
    assert adcp_error.get("code") == "INVALID_REQUEST", (
        f"Expected code='INVALID_REQUEST' on MCP wire, got {adcp_error.get('code')!r}. "
        f"Without the ValidationError translation, the framework wraps it as "
        f"INTERNAL_ERROR ('Platform method raised ValidationError')."
    )
    assert adcp_error.get("code") != "INTERNAL_ERROR", (
        "ValidationError leaked through as INTERNAL_ERROR — translator regression"
    )
    assert adcp_error.get("recovery") == "correctable", (
        f"Expected recovery='correctable' (buyer-fixable), got {adcp_error.get('recovery')!r}"
    )
    field = adcp_error.get("field") or ""
    assert "packages" in field or "salesagent_unknown_field" in field, (
        f"Expected adcp_error.field to surface the offending field path; got {field!r}"
    )


@pytest.mark.requires_db
def test_idempotency_conflict_wire_envelope_mcp(authenticated_principal) -> None:
    """End-to-end MCP wire test: same ``idempotency_key`` plus a different
    canonical payload surfaces as ``IDEMPOTENCY_CONFLICT`` with
    ``recovery='correctable'``.

    Unit tests cover ``translate_idempotency_conflict`` directly. This pins the
    dispatcher path buyers hit, where a framework conflict must become an AdCP
    error envelope instead of leaking as ``INTERNAL_ERROR``.
    """
    first_payload, conflict_payload = _conflicting_create_media_buy_payloads(
        authenticated_principal,
        key_prefix="idem-conflict-mcp",
    )

    first = _call_mcp_raw(
        "create_media_buy",
        first_payload,
        authenticated_principal,
    )
    _assert_mcp_create_success(first)

    conflict = _call_mcp_raw(
        "create_media_buy",
        conflict_payload,
        authenticated_principal,
    )

    assert conflict.isError is True, f"Expected isError=True for idempotency conflict; got: {conflict!r}"
    structured = conflict.structuredContent or {}
    adcp_error = structured.get("adcp_error")
    assert adcp_error is not None, f"Expected structuredContent.adcp_error envelope, got: {structured!r}"
    _assert_idempotency_conflict_adcp_error(adcp_error, transport="MCP")


@pytest.mark.requires_db
def test_idempotency_conflict_wire_envelope_a2a(authenticated_principal) -> None:
    """End-to-end A2A wire test: idempotency conflicts publish the AdCP
    error envelope in the failed task artifact, not a generic task failure.
    """
    first_payload, conflict_payload = _conflicting_create_media_buy_payloads(
        authenticated_principal,
        key_prefix="idem-conflict-a2a",
    )

    first = _call_a2a_raw("create_media_buy", first_payload, authenticated_principal)
    _assert_a2a_create_success(first)

    conflict = _call_a2a_raw("create_media_buy", conflict_payload, authenticated_principal)
    assert "error" not in conflict, (
        f"A2A idempotency conflict should be a failed task, not JSON-RPC error: {conflict!r}"
    )
    adcp_error = _extract_a2a_adcp_error(conflict)

    _assert_idempotency_conflict_adcp_error(adcp_error, transport="A2A")


@pytest.mark.requires_db
def test_validation_error_wire_envelope_a2a(authenticated_principal) -> None:
    """End-to-end A2A wire test: pydantic ``ValidationError`` projects onto
    ``Task.artifacts[0].parts[0].data.adcp_error`` per AdCP transport-errors
    §A2A Binding.

    The A2A executor (``adcp.server.a2a_server.AdcpA2AExecutor.execute``)
    catches the framework :class:`AdcpError` re-raised by the delegate and
    publishes a failed task carrying the structured envelope. A regression
    that breaks delegate translation OR the A2A executor's catch path
    fails this test.
    """
    bad_params = _bad_update_media_buy_payload(authenticated_principal["account_id"])

    # A2A JSON-RPC ``message/send`` carrying explicit-skill DataPart per the
    # framework's ``_parse_request`` contract — ``{"skill": ..., "parameters":
    # ...}`` keyed in a data part.
    body = _call_a2a_raw("update_media_buy", bad_params, authenticated_principal)
    adcp_error = _extract_a2a_adcp_error(body)

    # Wire contract: INVALID_REQUEST + correctable + field path.
    assert adcp_error.get("code") == "INVALID_REQUEST", (
        f"Expected adcp_error.code='INVALID_REQUEST' for pydantic ValidationError "
        f"on A2A wire path, got {adcp_error.get('code')!r}. Without the decorator's "
        f"ValidationError translation, this would be INTERNAL_ERROR ('Task failed')."
    )
    assert adcp_error.get("recovery") == "correctable", (
        f"Expected adcp_error.recovery='correctable', got {adcp_error.get('recovery')!r}"
    )
    field = adcp_error.get("field") or ""
    assert "packages" in field or "salesagent_unknown_field" in field, (
        f"Expected adcp_error.field to surface the offending field path so buyers "
        f"know which field to repair; got {field!r}"
    )
