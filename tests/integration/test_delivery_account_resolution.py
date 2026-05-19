"""Regression coverage for delivery account resolution across transports."""

from __future__ import annotations

from typing import Any

import pytest

from tests.factories import MediaBuyFactory
from tests.harness.delivery_poll import DeliveryPollEnv
from tests.harness.transport import Transport
from tests.helpers.account_resolution import create_accessible_delivery_account

pytestmark = [pytest.mark.integration, pytest.mark.requires_db, pytest.mark.delivery]

ALL_TRANSPORTS = (Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST)


def _seed_delivery_account(env: DeliveryPollEnv) -> None:
    tenant, principal = env.setup_default_data()
    account = create_accessible_delivery_account(tenant=tenant, principal=principal)
    MediaBuyFactory(
        tenant=tenant,
        principal=principal,
        media_buy_id="mb-account-001",
        status="active",
        account_id=account.account_id,
    )
    env.set_adapter_response(media_buy_id="mb-account-001")


def _assert_delivery_success(result: Any) -> None:
    assert result.is_success, result.error
    assert result.payload is not None
    deliveries = result.payload.media_buy_deliveries
    assert [delivery.media_buy_id for delivery in deliveries] == ["mb-account-001"]


@pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=[t.value for t in ALL_TRANSPORTS])
def test_delivery_resolves_explicit_account_id_across_transports(integration_db, transport: Transport) -> None:
    with DeliveryPollEnv(
        tenant_id=f"delivery_acct_explicit_{transport.value}",
        principal_id="buyer-001",
    ) as env:
        _seed_delivery_account(env)

        result = env.call_via(
            transport,
            media_buy_ids=["mb-account-001"],
            account={"account_id": "acc_acme_001"},
        )

    _assert_delivery_success(result)


@pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=[t.value for t in ALL_TRANSPORTS])
def test_delivery_resolves_natural_key_account_across_transports(integration_db, transport: Transport) -> None:
    with DeliveryPollEnv(
        tenant_id=f"delivery_acct_natural_{transport.value}",
        principal_id="buyer-001",
    ) as env:
        _seed_delivery_account(env)

        result = env.call_via(
            transport,
            media_buy_ids=["mb-account-001"],
            account={"brand": {"domain": "acme-corp.com"}, "operator": "acme-corp.com"},
        )

    _assert_delivery_success(result)


@pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=[t.value for t in ALL_TRANSPORTS])
def test_delivery_account_not_found_across_transports(integration_db, transport: Transport) -> None:
    with DeliveryPollEnv(
        tenant_id=f"delivery_acct_missing_{transport.value}",
        principal_id="buyer-001",
    ) as env:
        _seed_delivery_account(env)

        result = env.call_via(
            transport,
            media_buy_ids=["mb-account-001"],
            account={"account_id": "acc_nonexistent"},
        )

    assert result.is_error
    assert getattr(result.error, "error_code", None) == "ACCOUNT_NOT_FOUND"


def test_a2a_forwards_account_to_delivery_raw(integration_db) -> None:
    with DeliveryPollEnv(tenant_id="delivery_acct_a2a_forward", principal_id="buyer-001") as env:
        _seed_delivery_account(env)

        result = env.call_via(
            Transport.A2A,
            media_buy_ids=["mb-account-001"],
            account={"account_id": "acc_nonexistent"},
        )

    assert result.is_error
    assert getattr(result.error, "error_code", None) == "ACCOUNT_NOT_FOUND"
