"""Idempotency tests for sync_accounts (AdCP 3.0.1 verbatim replay).

sync_accounts drives the shared verbatim-replay engine (``src.services.idempotency_replay``)
via ``_SYNC_REPLAY_POLICY``: a retry with the same client ``idempotency_key`` replays the
original success verbatim (marked ``replayed: true``), a different canonical payload under
the same key is ``IDEMPOTENCY_CONFLICT``, and dry-runs are never cached. Unlike
create_media_buy, sync has no separate resource backstop: the ``idempotency_attempts`` unique
index deduplicates the RESPONSE by request hash, so a concurrent same-key loser replays the
winner (or fails closed) via ``on_race``. It does NOT make the account upsert atomic — a
concurrent same-natural-key create can still duplicate account rows (tracked in #1535).

The parametrized replay test also pins the wire ``replayed`` marker on every transport: a
miss there would mean the ``@model_serializer`` marker did not survive that transport's
serialization path (e.g. MCP structured_content).

Business rules: BR-RULE-055..062 (sync_accounts); idempotency per AdCP 3.0.1.
"""

import pytest

from src.core.exceptions import (
    AdCPIdempotencyConflictError,
    AdCPIdempotencyExpiredError,
    AdCPIdempotencyInFlightError,
)
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
    """The cache unique index dedups the RESPONSE by request hash; these pin the degraded
    replay path failing closed (cache absent/expired) — never fabricating a body. Account-row
    upsert atomicity is out of scope here (tracked in #1535)."""

    def test_degraded_replay_fails_closed_when_cache_absent(self, integration_db):
        # When the cache row is not visible (a true in-flight race for a cache-only-backstop
        # tool), the degraded path rejects with a transient IDEMPOTENCY_IN_FLIGHT (rule 9
        # reject-and-redirect) rather than reconstructing a response — never a fabricated body.
        with AccountSyncEnv(tenant_id="sync_idem_race", principal_id="agent_idem") as env:
            env.setup_default_data()
            from src.core.tools.accounts import _SYNC_REPLAY_POLICY

            with pytest.raises(AdCPIdempotencyInFlightError) as exc_info:
                idempotency_replay.replay_after_race(
                    _SYNC_REPLAY_POLICY,
                    "sync_idem_race",
                    idempotency_key="never-written",
                    principal_id="agent_idem",
                    account_id=None,
                    request_hash="some-canonical-hash",
                )
            assert exc_info.value.error_code == "IDEMPOTENCY_IN_FLIGHT"
            assert exc_info.value.recovery == "transient"

    def test_degraded_path_rejects_expired_sync_cache_row(self, integration_db):
        """An expired sync cache row rejects IDEMPOTENCY_EXPIRED (rule 6), never a stale replay.

        The sync policy is anchor-less (find_backstop_anchor=None), so the expiry decision
        rests on the cache row's STORED expires_at. This pins the EXPIRED branch for the
        SECOND engine consumer specifically — the create-side tests prove the policy-agnostic
        engine logic; this proves sync (the pattern #1470 inherits) does not special-case away.
        """
        from datetime import UTC, datetime, timedelta

        from src.core.database.repositories.idempotency_attempt import DEFAULT_REPLAY_TTL
        from src.core.database.repositories.uow import AccountUoW
        from src.core.schemas.account import SyncAccountsResponse
        from src.core.tools.accounts import _SYNC_REPLAY_POLICY

        tenant_id, principal_id, key = "sync_idem_expired", "agent_idem", "k-expired"
        req_hash = "sync-canonical-hash-expired"

        with AccountSyncEnv(tenant_id=tenant_id, principal_id=principal_id) as env:
            env.setup_default_data()
            # Seed a cache row already past its replay window (record at a past `now` so
            # expires_at = past + TTL is still in the past).
            with AccountUoW(tenant_id) as uow:
                assert uow.idempotency_attempts is not None
                uow.idempotency_attempts.record_success(
                    principal_id=principal_id,
                    tool_name="sync_accounts",
                    idempotency_key=key,
                    response_model=SyncAccountsResponse(accounts=[]),
                    protocol_status="completed",
                    payload_hash=req_hash,
                    account_id=None,
                    now=datetime.now(UTC) - (DEFAULT_REPLAY_TTL + timedelta(seconds=60)),
                )

            # Same key + MATCHING hash: the rule-5 conflict check passes, so the rule-6
            # window-expired check is what fires → IDEMPOTENCY_EXPIRED (correctable), not a replay.
            with pytest.raises(AdCPIdempotencyExpiredError) as exc_info:
                idempotency_replay.replay_after_race(
                    _SYNC_REPLAY_POLICY,
                    tenant_id,
                    idempotency_key=key,
                    principal_id=principal_id,
                    account_id=None,
                    request_hash=req_hash,
                )
            assert exc_info.value.error_code == "IDEMPOTENCY_EXPIRED"
            assert exc_info.value.recovery == "correctable"


class TestCrossToolIdempotencyIsolation:
    """A key cached by create_media_buy, probed by sync_accounts at the SAME (agent, account, key)
    scope, never replays the create body as a sync response.

    The cache tuple is tool-agnostic — one shared row, not a per-tool cache (see
    ``IdempotencyAttemptRepository.find_by_key``) — so the engine's docstring claims cross-tool
    isolation rests on two invariants. This pins BOTH (neither has a within-tool oracle):
      #1 the two requests have disjoint required fields → different canonical hashes → the shared
         row conflicts (IDEMPOTENCY_CONFLICT) instead of replaying;
      #2 even on a FORCED hash collision, the stored create envelope fails ``SyncAccountsResponse``
         validation → cache miss (None), never the create body mistyped as a sync response.
    """

    def test_create_cached_key_probed_by_sync_conflicts_or_misses(self, integration_db):
        from src.core.database.repositories.uow import AccountUoW
        from src.core.tools.accounts import _SYNC_REPLAY_POLICY
        from src.core.tools.media_buy_create import CreateMediaBuySuccess

        tenant_id, principal_id, key = "xtool_idem", "agent_xtool", "k-cross-tool"
        create_hash = "create-canonical-hash-0001"

        with AccountSyncEnv(tenant_id=tenant_id, principal_id=principal_id) as env:
            env.setup_default_data()

            # create_media_buy caches a success under the shared scope tuple (UoW commits on exit).
            with AccountUoW(tenant_id) as uow:
                assert uow.idempotency_attempts is not None
                uow.idempotency_attempts.record_success(
                    principal_id=principal_id,
                    tool_name="create_media_buy",
                    idempotency_key=key,
                    response_model=CreateMediaBuySuccess(media_buy_id="mb-xtool", packages=[]),
                    protocol_status="completed",
                    payload_hash=create_hash,
                    account_id=None,
                )

            # #1: a sync request hashes differently from the cached create → the shared row conflicts.
            with pytest.raises(AdCPIdempotencyConflictError) as exc_info:
                idempotency_replay.lookup_cached_replay(
                    _SYNC_REPLAY_POLICY,
                    tenant_id,
                    principal_id=principal_id,
                    account_id=None,
                    idempotency_key=key,
                    request_hash="sync-canonical-hash-differs",
                )
            assert exc_info.value.error_code == "IDEMPOTENCY_CONFLICT"

            # #2: forced hash collision — the create envelope cannot validate as SyncAccountsResponse,
            # so the probe MISSES (re-executes) rather than replaying a create body typed as sync.
            assert (
                idempotency_replay.lookup_cached_replay(
                    _SYNC_REPLAY_POLICY,
                    tenant_id,
                    principal_id=principal_id,
                    account_id=None,
                    idempotency_key=key,
                    request_hash=create_hash,
                )
                is None
            )
