"""Real-concurrency proof of the sync_accounts idempotency RESERVATION (AdCP 3.1.1).

Unlike ``test_sync_accounts_idempotency_replay.py`` (sequential wire matrix), these
tests spin up TWO real threads on TWO real DB connections against a shared Postgres
to exercise the first-insert-wins reservation under genuine contention:

- ``test_concurrent_same_key_reserves_exactly_once``: a barrier freezes thread A
  INSIDE its work transaction — AFTER its ``reserve()`` in_flight row has COMMITTED
  and AFTER it has observed "no existing account", but BEFORE it writes the account.
  A second same-key request (thread B / main) then collides on the unique
  reservation index and is rejected ``IDEMPOTENCY_IN_FLIGHT`` (recovery=transient)
  WITHOUT doing any work — so EXACTLY ONE account row exists when A finishes. This is
  the invariant that fails if ``reserve()`` stops enforcing first-insert-wins: both
  racers would then execute and create two rows (see the mutation note in the module
  docstring of the plan / the agent report).
- ``test_same_key_different_payload_conflicts``: a completed reservation + a same-key
  retry carrying a DIFFERENT canonical payload is ``IDEMPOTENCY_CONFLICT``.
- ``test_a2a_changed_callback_url_conflicts``: driving the REAL A2A explicit-skill
  boundary, a same-key retry that changes ONLY the ``push_notification_config``
  callback URL is ``IDEMPOTENCY_CONFLICT`` — pinning that the A2A wire snapshot is
  taken AFTER the callback is injected (so the callback is business-hashable), the
  #1546 B1d fix.
- ``test_completion_failure_is_not_a_permanent_poison``: a handler failure releases
  the reservation (errors are NEVER cached), so a later retry with the SAME key
  re-executes from scratch and succeeds.

Requires a shared Postgres (``eval $(.claude/skills/agent-db/agent-db.sh up)``);
the two threads must see each other's committed rows.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from typing import Any

import pytest

from src.core.exceptions import (
    AdCPIdempotencyConflictError,
    AdCPIdempotencyInFlightError,
    build_two_layer_error_envelope,
)
from src.core.schemas.account import SyncAccountsRequest
from tests.harness.account_sync import AccountSyncEnv
from tests.helpers import assert_envelope_shape

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_OPERATOR = "example.com"


def _one_account(domain: str = "concurrency.example.com") -> list[dict]:
    """A single spec-valid sync entry (``.com`` domain — not a reserved TLD)."""
    return [{"brand": {"domain": domain}, "operator": _OPERATOR, "billing": "operator"}]


def _action(a: Any) -> str:
    return a.value if hasattr(a, "value") else str(a)


def _run_impl(req: SyncAccountsRequest, identity: Any) -> Any:
    """Invoke ``_sync_accounts_impl`` on a fresh event loop (thread-safe)."""
    from src.core.tools.accounts import _sync_accounts_impl

    return asyncio.run(_sync_accounts_impl(req=req, identity=identity))


def _count_accounts(tenant_id: str, principal_id: str) -> int:
    """Committed count of the principal's accounts, via the repository (own txn)."""
    from src.core.database.repositories.uow import AccountUoW

    with AccountUoW(tenant_id) as uow:
        assert uow.accounts is not None
        return len(uow.accounts.list_by_principal(principal_id))


class TestSyncAccountsReservationConcurrency:
    """Two real threads contend for one idempotency key on a shared DB."""

    def test_concurrent_same_key_reserves_exactly_once(self, integration_db):
        """A same-key request DURING the in-flight window is IN_FLIGHT; only ONE account lands.

        Thread A reserves (committing the in_flight row), then blocks at account-id
        generation — past its "no existing account" probe but before the write. While
        A is frozen, the main thread issues the SAME key and must be rejected
        IDEMPOTENCY_IN_FLIGHT without creating anything.
        """
        from src.core.tools import accounts as accounts_mod

        with AccountSyncEnv(tenant_id="conc_reserve", principal_id="agent_conc_reserve") as env:
            tenant, principal = env.setup_default_data()
            env._commit_factory_data()
            identity = env.identity
            tenant_id = tenant.tenant_id
            principal_id = principal.principal_id

            key = f"conc-{uuid.uuid4().hex}"
            req = SyncAccountsRequest(accounts=_one_account(), idempotency_key=key)

            real_gen = accounts_mod._generate_account_id
            at_create = threading.Event()
            release = threading.Event()
            state = {"calls": 0}
            lock = threading.Lock()

            def gated_generate_account_id() -> str:
                # Freeze ONLY the first racer (thread A) at the create point — after
                # reserve() has committed the in_flight row and after get_by_natural_key
                # returned None, but before the DBAccount write. A second racer that
                # (in a mutated build) reaches here does not block, so two writes race.
                with lock:
                    state["calls"] += 1
                    first = state["calls"] == 1
                if first:
                    at_create.set()
                    assert release.wait(timeout=30), "release was never signaled"
                return real_gen()

            a_result: dict[str, Any] = {}

            def run_a() -> None:
                try:
                    a_result["resp"] = _run_impl(req, identity)
                except Exception as exc:  # noqa: BLE001 — recorded for the assertion below
                    a_result["exc"] = exc

            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(accounts_mod, "_generate_account_id", gated_generate_account_id)
                thread_a = threading.Thread(target=run_a, name="reserve-winner")
                thread_a.start()
                try:
                    assert at_create.wait(timeout=30), "thread A never reached the create barrier"

                    # A holds a durable in_flight reservation. The same key now collides.
                    b_exc: Exception | None = None
                    try:
                        _run_impl(req, identity)
                    except Exception as exc:  # noqa: BLE001 — asserted below
                        b_exc = exc
                finally:
                    release.set()
                    thread_a.join(timeout=30)
                    assert not thread_a.is_alive(), "thread A did not finish"

            # Thread A won the reservation and created the account.
            assert "exc" not in a_result, f"reservation winner failed: {a_result.get('exc')!r}"
            assert a_result["resp"].replayed is False
            assert _action(a_result["resp"].accounts[0].action) == "created"

            # The second same-key request was rejected IN_FLIGHT (recovery=transient),
            # with a retry_after wait hint — and did NO work.
            assert isinstance(b_exc, AdCPIdempotencyInFlightError), f"expected IN_FLIGHT, got {b_exc!r}"
            assert b_exc.details.get("retry_after"), "IN_FLIGHT must carry details.retry_after"
            assert_envelope_shape(
                build_two_layer_error_envelope(b_exc),
                "IDEMPOTENCY_IN_FLIGHT",
                recovery="transient",
            )

            # The load-bearing invariant: first-insert-wins created EXACTLY ONE row.
            assert _count_accounts(tenant_id, principal_id) == 1

    def test_same_key_different_payload_conflicts(self, integration_db):
        """A same-key retry with a DIFFERENT canonical payload is IDEMPOTENCY_CONFLICT."""
        with AccountSyncEnv(tenant_id="conc_conflict", principal_id="agent_conc_conflict") as env:
            env.setup_default_data()
            env._commit_factory_data()
            identity = env.identity
            key = f"conf-{uuid.uuid4().hex}"

            first = _run_impl(
                SyncAccountsRequest(accounts=_one_account("a.example.com"), idempotency_key=key), identity
            )
            assert first.replayed is False

            with pytest.raises(AdCPIdempotencyConflictError) as excinfo:
                _run_impl(SyncAccountsRequest(accounts=_one_account("b.example.com"), idempotency_key=key), identity)

            assert_envelope_shape(
                build_two_layer_error_envelope(excinfo.value),
                "IDEMPOTENCY_CONFLICT",
                recovery="correctable",
            )

    def test_completion_failure_is_not_a_permanent_poison(self, integration_db):
        """A handler failure releases the reservation, so a same-key retry re-executes.

        Errors are NEVER cached: the in_flight row is released on failure, so the key
        is not poisoned — a later retry runs from scratch and succeeds, creating the
        account exactly once.
        """
        from src.core.tools import accounts as accounts_mod

        with AccountSyncEnv(tenant_id="conc_poison", principal_id="agent_conc_poison") as env:
            tenant, principal = env.setup_default_data()
            env._commit_factory_data()
            identity = env.identity
            tenant_id = tenant.tenant_id
            principal_id = principal.principal_id

            key = f"poison-{uuid.uuid4().hex}"
            req = SyncAccountsRequest(accounts=_one_account(), idempotency_key=key)

            real_process = accounts_mod._process_sync_entries
            state = {"calls": 0}

            def failing_once(*args: Any, **kwargs: Any) -> Any:
                state["calls"] += 1
                if state["calls"] == 1:
                    raise RuntimeError("injected handler failure after reservation")
                return real_process(*args, **kwargs)

            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(accounts_mod, "_process_sync_entries", failing_once)
                with pytest.raises(RuntimeError, match="injected handler failure"):
                    _run_impl(req, identity)

                # No account was written and the error was NOT cached.
                assert _count_accounts(tenant_id, principal_id) == 0

                # The SAME key retries and re-executes (the reservation was released).
                retry = _run_impl(req, identity)

            assert retry.replayed is False
            assert _action(retry.accounts[0].action) == "created"
            assert _count_accounts(tenant_id, principal_id) == 1


class TestSyncAccountsA2ACallbackHashing:
    """The A2A callback URL participates in the idempotency payload hash (#1546 B1d)."""

    @pytest.mark.asyncio
    async def test_a2a_changed_callback_url_conflicts(self, integration_db, monkeypatch):
        """Same key + CHANGED push_notification_config URL over A2A → IDEMPOTENCY_CONFLICT.

        Drives the real ``_handle_explicit_skill`` boundary so the callback is injected
        into ``parameters`` and the raw-wire snapshot is taken AFTER injection. Because
        the top-level ``push_notification_config`` is NOT excluded by the SDK
        canonicalizer, a changed callback URL is a different canonical request and must
        conflict — the same behaviour MCP/REST get with an in-body callback.
        """
        from google.protobuf import json_format

        # Allow the loopback callback URL past SSRF so the FIRST call can register +
        # complete; the conflicting SECOND call is rejected at reserve(), before SSRF.
        monkeypatch.setenv("ADCP_ALLOW_PRIVATE_WEBHOOKS", "1")

        from a2a.types import TaskPushNotificationConfig

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from tests.harness.transport import Transport

        def _pnc(url: str) -> TaskPushNotificationConfig:
            return json_format.ParseDict(
                {"url": url, "authentication": {"scheme": "Bearer", "credentials": "x" * 40}},
                TaskPushNotificationConfig(),
            )

        with AccountSyncEnv(tenant_id="conc_a2a_cb", principal_id="agent_conc_a2a_cb") as env:
            env.setup_default_data()
            env._commit_factory_data()
            identity = env.identity_for(Transport.A2A)

            handler = AdCPRequestHandler()
            key = f"a2a-cb-{uuid.uuid4().hex}"
            params = {"accounts": _one_account(), "idempotency_key": key}

            # First: registers the callback + completes the reservation.
            first = await handler._handle_explicit_skill(
                "sync_accounts",
                {**params},
                identity,
                push_notification_config=_pnc("http://127.0.0.1:9099/callback-a"),
            )
            assert first.get("accounts"), f"first A2A sync did not create: {first}"

            # Second: identical body + key, DIFFERENT callback URL → conflict at reserve.
            with pytest.raises(AdCPIdempotencyConflictError) as excinfo:
                await handler._handle_explicit_skill(
                    "sync_accounts",
                    {**params},
                    identity,
                    push_notification_config=_pnc("http://127.0.0.1:9099/callback-B-CHANGED"),
                )

        assert_envelope_shape(
            build_two_layer_error_envelope(excinfo.value),
            "IDEMPOTENCY_CONFLICT",
            recovery="correctable",
        )
