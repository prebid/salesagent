"""Wire matrix for sync_accounts idempotency: replay / conflict / ceiling (AdCP 3.1.1).

Pinned at the real wire (not reconstructed exceptions) across transports:

- ``identical_retry_replays_verbatim``: an IDENTICAL retry (same key + payload)
  returns the ORIGINAL response with top-level ``replayed: true`` — the account is
  NOT upserted a second time (the verbatim "created" is replayed, not re-derived to
  "unchanged").
- ``key_reuse_conflict``: the same key with a DIFFERENT accounts payload rejects
  with ``IDEMPOTENCY_CONFLICT`` on every transport.
- ``insert_ceiling``: a cache MISS that would exceed the per-scope insert rate is
  rejected as ``RATE_LIMITED`` before executing.

Unlike create_media_buy there is no dup-booking backstop; sync upserts are
naturally idempotent, so the verbatim cache is the sole replay authority.
"""

from __future__ import annotations

import uuid

import pytest

from src.core.schemas.account import SyncAccountsRequest
from tests.harness import Transport
from tests.harness.account_sync import AccountSyncEnv
from tests.helpers import assert_envelope_shape

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

WIRE_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST]


def _one_account(domain: str = "replay.example.com") -> list[dict]:
    return [{"brand": {"domain": domain}, "operator": "example.com", "billing": "operator"}]


def _action(a) -> str:
    return a.value if hasattr(a, "value") else str(a)


@pytest.mark.parametrize("transport", WIRE_TRANSPORTS, ids=lambda t: t.value)
class TestSyncAccountsIdempotencyWireMatrix:
    """Replay, conflict, and ceiling observed through each real transport."""

    def test_identical_retry_replays_verbatim(self, integration_db, transport):
        key = f"replay-{uuid.uuid4().hex}"
        with AccountSyncEnv(tenant_id=f"repl_{transport.value}", principal_id=f"agent_repl_{transport.value}") as env:
            env.setup_default_data()
            req = SyncAccountsRequest(accounts=_one_account(), idempotency_key=key)

            first = env.call_via(transport, req=req)
            assert first.is_success, f"fresh sync failed on {transport.value}: {first.error}"
            assert first.payload.replayed is False
            assert _action(first.payload.accounts[0].action) == "created"

            second = env.call_via(transport, req=req)
            assert second.is_success, f"replay failed on {transport.value}: {second.error}"

        # The spec's top-level marker: present only on the replay.
        assert second.payload.replayed is True
        # Verbatim: the cached "created" is replayed, NOT re-executed to "unchanged".
        assert _action(second.payload.accounts[0].action) == "created"
        assert second.payload.accounts[0].account_id == first.payload.accounts[0].account_id

    def test_key_reuse_with_different_payload_conflicts(self, integration_db, transport):
        key = f"conflict-{uuid.uuid4().hex}"
        with AccountSyncEnv(tenant_id=f"conf_{transport.value}", principal_id=f"agent_conf_{transport.value}") as env:
            env.setup_default_data()

            first = env.call_via(
                transport, req=SyncAccountsRequest(accounts=_one_account("a.example.com"), idempotency_key=key)
            )
            assert first.is_success, f"fresh sync failed on {transport.value}: {first.error}"

            second = env.call_via(
                transport, req=SyncAccountsRequest(accounts=_one_account("b.example.com"), idempotency_key=key)
            )

        assert second.is_error, f"expected conflict on {transport.value}, got {second.payload}"
        envelope = second.wire_error_envelope if transport is not Transport.IMPL else second.synthesized_error_envelope
        assert_envelope_shape(envelope, "IDEMPOTENCY_CONFLICT", recovery="correctable")

    def test_fresh_key_is_not_a_replay(self, integration_db, transport):
        """A DIFFERENT key with an identical payload executes fresh (no cross-key replay)."""
        with AccountSyncEnv(tenant_id=f"fresh_{transport.value}", principal_id=f"agent_fresh_{transport.value}") as env:
            env.setup_default_data()

            first = env.call_via(
                transport, req=SyncAccountsRequest(accounts=_one_account(), idempotency_key=f"k1-{uuid.uuid4().hex}")
            )
            assert first.is_success
            second = env.call_via(
                transport, req=SyncAccountsRequest(accounts=_one_account(), idempotency_key=f"k2-{uuid.uuid4().hex}")
            )
            assert second.is_success

        # Different key → executed fresh: no replay marker; the SAME natural-key
        # account is now "unchanged" (proving a real re-execution, not a replay).
        assert second.payload.replayed is False
        assert _action(second.payload.accounts[0].action) == "unchanged"


class TestSyncAccountsInsertCeiling:
    """A cache MISS that would exceed the per-scope insert rate is rate-limited."""

    @pytest.mark.asyncio
    async def test_insert_rate_ceiling_rejects_excess(self, integration_db, monkeypatch):
        # Tighten the insert-rate ceiling so the third fresh key trips it.
        monkeypatch.setattr("src.services.idempotency_policy.MAX_INSERTS_PER_WINDOW", 2)

        with AccountSyncEnv(tenant_id="ceil_t", principal_id="agent_ceil") as env:
            env.setup_default_data()
            for _ in range(2):
                resp = await env.call_impl_async(
                    req=SyncAccountsRequest(accounts=_one_account(), idempotency_key=f"ceil-{uuid.uuid4().hex}")
                )
                assert resp.accounts

            from src.core.exceptions import AdCPRateLimitError

            with pytest.raises(AdCPRateLimitError):
                await env.call_impl_async(
                    req=SyncAccountsRequest(accounts=_one_account(), idempotency_key=f"ceil-{uuid.uuid4().hex}")
                )
