"""Unit regressions for delivery AccountReference propagation."""

from __future__ import annotations

from typing import Any

import pytest
from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference1

from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import GetMediaBuyDeliveryRequest


def _account_id(account: Any) -> str:
    return account.root.account_id


def test_delivery_raw_preserves_account_for_shared_impl(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.tools import media_buy_delivery

    captured: dict[str, Any] = {}
    identity = ResolvedIdentity(principal_id="buyer-001", tenant_id="tenant-001")

    def fake_impl(req: GetMediaBuyDeliveryRequest, got_identity: ResolvedIdentity | None) -> str:
        captured["account"] = req.account
        captured["identity"] = got_identity
        return "ok"

    monkeypatch.setattr(media_buy_delivery, "_get_media_buy_delivery_impl", fake_impl)

    result = media_buy_delivery.get_media_buy_delivery_raw(
        media_buy_ids=["mb-001"],
        account=AccountReference(AccountReference1(account_id="acc_001")),
        identity=identity,
    )

    assert result == "ok"
    assert _account_id(captured["account"]) == "acc_001"
    assert captured["identity"] is identity


def test_delivery_impl_resolves_account_before_principal_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.tools import media_buy_delivery

    calls: dict[str, Any] = {}
    account = AccountReference(AccountReference1(account_id="acc_001"))
    identity = ResolvedIdentity(
        principal_id="buyer-001",
        tenant_id="tenant-001",
        tenant={"tenant_id": "tenant-001"},
    )
    enriched = identity.model_copy(update={"account_id": "acc_001"})

    def fake_enrich_identity(got_identity: ResolvedIdentity | None, got_account: Any) -> ResolvedIdentity:
        calls["identity"] = got_identity
        calls["account"] = got_account
        return enriched

    def stop_after_account_resolution(principal_id: str, tenant_id: str | None = None) -> None:
        calls["principal_lookup"] = (principal_id, tenant_id)
        raise RuntimeError("stop before database access")

    monkeypatch.setattr("src.core.transport_helpers.enrich_identity_with_account", fake_enrich_identity)
    monkeypatch.setattr(media_buy_delivery, "get_principal_object", stop_after_account_resolution)

    req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb-001"], account=account)
    with pytest.raises(RuntimeError, match="stop before database access"):
        media_buy_delivery._get_media_buy_delivery_impl(req, identity)

    assert calls["identity"] is identity
    assert _account_id(calls["account"]) == "acc_001"
    assert calls["principal_lookup"] == ("buyer-001", "tenant-001")


@pytest.mark.asyncio
async def test_a2a_delivery_handler_forwards_validated_account_to_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.a2a_server import adcp_a2a_server

    captured: dict[str, Any] = {}

    def fake_raw(**kwargs: Any) -> dict[str, bool]:
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(adcp_a2a_server, "core_get_media_buy_delivery_tool", fake_raw)

    handler = adcp_a2a_server.AdCPRequestHandler()
    identity = ResolvedIdentity(
        principal_id="buyer-001",
        tenant_id="tenant-001",
        tenant={"tenant_id": "tenant-001"},
        protocol="a2a",
    )

    result = await handler._handle_get_media_buy_delivery_skill(
        {"media_buy_ids": ["mb-001"], "account": {"account_id": "acc_001"}},
        identity,
    )

    assert result == {"ok": True}
    assert _account_id(captured["account"]) == "acc_001"
    assert captured["identity"] is identity


@pytest.mark.asyncio
async def test_rest_delivery_route_passes_account_to_raw_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.routes import api_v1

    captured: dict[str, Any] = {}
    identity = ResolvedIdentity(
        principal_id="buyer-001",
        tenant_id="tenant-001",
        tenant={"tenant_id": "tenant-001"},
        protocol="rest",
    )

    class FakeResponse:
        def model_dump(self, mode: str = "python") -> dict[str, bool]:
            return {"ok": mode == "json"}

    def fake_raw(**kwargs: Any) -> FakeResponse:
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(api_v1.media_buy_delivery_module, "get_media_buy_delivery_raw", fake_raw)

    body = api_v1.GetMediaBuyDeliveryBody(
        media_buy_ids=["mb-001"],
        account={"account_id": "acc_001"},
    )
    result = await api_v1.get_media_buy_delivery(body, identity)

    assert result == {"ok": True}
    assert _account_id(captured["account"]) == "acc_001"
    assert captured["identity"] is identity
