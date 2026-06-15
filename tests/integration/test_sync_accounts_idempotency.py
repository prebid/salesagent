"""Idempotency tests for sync_accounts (AdCP 3.0.1 verbatim replay).

sync_accounts drives the shared verbatim-replay engine (``src.services.idempotency_replay``)
via ``_SYNC_REPLAY_POLICY``: a retry with the same client ``idempotency_key`` replays the
original success verbatim (marked ``replayed: true``), a different canonical payload under
the same key is ``IDEMPOTENCY_CONFLICT``, and dry-runs are never cached. Unlike
create_media_buy, sync has no separate resource backstop — its upsert is state-idempotent,
so the ``idempotency_attempts`` unique index is the race backstop and a concurrent same-key
loser replays the winner (or fails closed) via ``on_race``.

The parametrized replay test also pins the wire ``replayed`` marker on every transport: a
miss there would mean the ``@model_serializer`` marker did not survive that transport's
serialization path (e.g. MCP structured_content).

Business rules: BR-RULE-055..062 (sync_accounts); idempotency per AdCP 3.0.1.
"""

import pytest

from src.core.exceptions import AdCPIdempotencyConflictError, AdCPServiceUnavailableError
from src.core.schemas.account import SyncAccountsRequest
from src.services import idempotency_replay
from tests.harness import Transport
from tests.harness.account_sync import AccountSyncEnv
from tests.helpers import assert_envelope_shape

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.REST, Transport.MCP]
WIRE_TRANSPORTS = [Transport.A2A, Transport.REST, Transport.MCP]

_ACME = {"brand": {"domain": "acme.com"}, "operator": "example.com", "billing": "operator"}
_BETA = {"brand": {"domain": "beta.com"}, "operator": "example.com", "billing": "agent"}


def _action(action):
    return action.value if hasattr(action, "value") else str(action)


class TestSyncAccountsReplay:
    """A same-key retry replays the original success verbatim (AdCP 3.0.1)."""

    @pytest.mark.parametrize("transport", ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_same_key_retry_replays_verbatim(self, integration_db, transport):
        with AccountSyncEnv(tenant_id=f"sync_idem_{transport.value}", principal_id="agent_idem") as env:
            env.setup_default_data()
            first = env.call_via(transport, req=SyncAccountsRequest(accounts=[_ACME], idempotency_key="k-replay"))
            # The account now exists, so a FRESH execution would report "updated"; a verbatim
            # replay returns the ORIGINAL "created" response, marked replayed on the wire.
            second = env.call_via(transport, req=SyncAccountsRequest(accounts=[_ACME], idempotency_key="k-replay"))

        assert first.is_success and second.is_success, f"{transport}: {first.error or second.error}"
        assert first.payload.replayed is False
        assert second.payload.replayed is True, f"{transport} dropped the replayed wire marker"
        assert _action(first.payload.accounts[0].action) == "created"
        assert _action(second.payload.accounts[0].action) == "created"
        assert second.payload.model_dump(mode="json")["accounts"] == first.payload.model_dump(mode="json")["accounts"]
        if transport is Transport.REST:
            # Byte-level: the replay is the ORIGINAL success body verbatim plus the
            # replayed marker — proves no field diverged on the wire, not just .accounts.
            assert second.raw_response.json() == {**first.raw_response.json(), "replayed": True}

    @pytest.mark.asyncio
    async def test_no_idempotency_key_re_executes(self, integration_db):
        """A missing key runs non-idempotent — a DELIBERATE deviation from spec 3.0.1.

        Spec 3.0.1 requires REJECTING a mutating request that omits idempotency_key
        (INVALID_REQUEST). salesagent deliberately keeps the key OPTIONAL for the sync
        tools (a pre-existing repo-wide override), so this pins the intentional
        "omit => re-execute" behavior, NOT spec conformance. The missing-key rejection
        is tracked for a coordinated fix across the sync tools; create_media_buy already
        conforms (its key stays required).
        """
        with AccountSyncEnv(tenant_id="sync_idem_nokey", principal_id="agent_idem") as env:
            env.setup_default_data()
            r1 = await env.call_impl_async(req=SyncAccountsRequest(accounts=[_ACME]))
            r2 = await env.call_impl_async(req=SyncAccountsRequest(accounts=[_ACME]))

        # No key → nothing cached → the second call RE-EXECUTES (replayed stays False) and
        # re-evaluates current state: identical data upserts to "unchanged". Contrast the
        # replay test, where the second call returns the original "created" marked replayed.
        assert r1.replayed is False
        assert r2.replayed is False
        assert _action(r1.accounts[0].action) == "created"
        assert _action(r2.accounts[0].action) == "unchanged"

    @pytest.mark.asyncio
    async def test_dry_run_not_cached(self, integration_db):
        with AccountSyncEnv(tenant_id="sync_idem_dry", principal_id="agent_idem") as env:
            env.setup_default_data()
            preview = await env.call_impl_async(
                req=SyncAccountsRequest(accounts=[_ACME], dry_run=True, idempotency_key="k-dry")
            )
            # The dry_run preview is never cached, so a real call with the SAME key executes
            # for real (creates the account) rather than replaying the preview.
            real = await env.call_impl_async(req=SyncAccountsRequest(accounts=[_ACME], idempotency_key="k-dry"))

        assert preview.replayed is False
        assert real.replayed is False
        assert _action(real.accounts[0].action) == "created"


class TestSyncAccountsConflict:
    """A reused key with a different canonical payload is IDEMPOTENCY_CONFLICT (rule 5)."""

    @pytest.mark.asyncio
    async def test_same_key_different_payload_conflicts_impl(self, integration_db):
        with AccountSyncEnv(tenant_id="sync_idem_conf", principal_id="agent_idem") as env:
            env.setup_default_data()
            await env.call_impl_async(req=SyncAccountsRequest(accounts=[_ACME], idempotency_key="k-conf"))
            with pytest.raises(AdCPIdempotencyConflictError) as exc_info:
                await env.call_impl_async(req=SyncAccountsRequest(accounts=[_BETA], idempotency_key="k-conf"))
            assert exc_info.value.error_code == "IDEMPOTENCY_CONFLICT"

    @pytest.mark.parametrize("transport", WIRE_TRANSPORTS, ids=lambda t: t.value)
    def test_same_key_different_payload_conflicts_on_wire(self, integration_db, transport):
        with AccountSyncEnv(tenant_id=f"sync_idem_confw_{transport.value}", principal_id="agent_idem") as env:
            env.setup_default_data()
            first = env.call_via(transport, req=SyncAccountsRequest(accounts=[_ACME], idempotency_key="k-confw"))
            assert first.is_success
            conflict = env.call_via(transport, req=SyncAccountsRequest(accounts=[_BETA], idempotency_key="k-confw"))

        assert conflict.is_error, f"{transport}: expected conflict, got {conflict.payload}"
        assert_envelope_shape(conflict.wire_error_envelope, "IDEMPOTENCY_CONFLICT", recovery="correctable")


class TestSyncAccountsRace:
    """sync's cache unique index is the race backstop; the degraded path never fabricates."""

    def test_degraded_replay_fails_closed_when_cache_absent(self, integration_db):
        # When the cache row is not visible (a true in-flight race for a cache-only-backstop
        # tool), the degraded path fails closed with a transient SERVICE_UNAVAILABLE rather
        # than reconstructing a response — never a fabricated body.
        with AccountSyncEnv(tenant_id="sync_idem_race", principal_id="agent_idem") as env:
            env.setup_default_data()
            from src.core.tools.accounts import _SYNC_REPLAY_POLICY

            with pytest.raises(AdCPServiceUnavailableError):
                idempotency_replay.replay_after_race(
                    _SYNC_REPLAY_POLICY,
                    "sync_idem_race",
                    idempotency_key="never-written",
                    principal_id="agent_idem",
                    account_id=None,
                    request_hash="some-canonical-hash",
                )
