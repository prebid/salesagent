"""Integration tests for verbatim SUCCESS replay through _create_media_buy_impl.

AdCP 3.0.1 idempotency: retrying with the same idempotency_key replays the
ORIGINAL success VERBATIM (top-level ``replayed: true``), never re-evaluating;
the same key carrying a *different* canonical payload raises
``IDEMPOTENCY_CONFLICT``; errors are NEVER cached, so a retry after an error
re-executes.

These pin the replay path through the production entrypoint — if the lookup were
deleted, the happy-path _impl tests would still pass green.
"""

import uuid
from datetime import UTC, datetime

import pytest

from tests.harness.media_buy_create import MediaBuyCreateEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _seed_success(tenant_id, principal_id, idempotency_key, *, media_buy_id="mb_seeded", payload_hash=None):
    """Write a cached success row directly via the repository (the verbatim cache).

    ``payload_hash=None`` makes the lookup always a replay; pass a non-matching
    hash to exercise the IDEMPOTENCY_CONFLICT path.
    """
    from adcp.server.helpers import valid_actions_for_status
    from adcp.types import MediaBuyStatus

    from src.core.database.repositories import MediaBuyUoW
    from src.core.schemas._base import CreateMediaBuySuccess

    success = CreateMediaBuySuccess(
        media_buy_id=media_buy_id,
        packages=[],
        status=MediaBuyStatus.active,
        valid_actions=valid_actions_for_status(MediaBuyStatus.active.value),
    )
    with MediaBuyUoW(tenant_id) as uow:
        assert uow.idempotency_attempts is not None
        uow.idempotency_attempts.record_success(
            principal_id=principal_id,
            account_id=None,
            tool_name="create_media_buy",
            idempotency_key=idempotency_key,
            response_model=success,
            protocol_status="completed",
            payload_hash=payload_hash,
        )


def _seed_principal(tenant_id, principal_id):
    """Commit a tenant + principal so the _impl auth/FK checks pass."""
    from tests.factories import PrincipalFactory, TenantFactory
    from tests.harness._base import IntegrationEnv

    class _Env(IntegrationEnv):
        EXTERNAL_PATCHES: dict[str, str] = {}

    with _Env() as env:
        tenant = TenantFactory(tenant_id=tenant_id)
        PrincipalFactory(tenant=tenant, principal_id=principal_id)
        env._commit_factory_data()


def _make_request(idempotency_key, *, po_number="REPLAY-1"):
    from src.core.schemas import CreateMediaBuyRequest

    return CreateMediaBuyRequest(
        brand={"domain": "replay-test.example.com"},
        packages=[],
        start_time=datetime(2026, 6, 1, tzinfo=UTC),
        end_time=datetime(2026, 6, 30, tzinfo=UTC),
        po_number=po_number,
        idempotency_key=idempotency_key,
    )


def _identity(tenant_id, principal_id):
    from src.core.testing_hooks import AdCPTestContext
    from tests.factories import PrincipalFactory

    return PrincipalFactory.make_identity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        testing_context=AdCPTestContext(test_session_id="replay_test"),
    )


class TestImplReplaysCachedSuccess:
    """_create_media_buy_impl replays the cached success verbatim on key match."""

    async def test_cached_success_replayed_verbatim(self, integration_db):
        from src.core.schemas._base import CreateMediaBuyResult, CreateMediaBuySuccess
        from src.core.tools.media_buy_create import _create_media_buy_impl

        idem_key = f"replay-{uuid.uuid4().hex[:8]}"
        tenant_id = f"replay_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"

        _seed_principal(tenant_id, principal_id)
        # No stored hash → always a replay (never a conflict).
        _seed_success(tenant_id, principal_id, idem_key, media_buy_id="mb_original_123")

        result = await _create_media_buy_impl(req=_make_request(idem_key), identity=_identity(tenant_id, principal_id))

        assert isinstance(result, CreateMediaBuyResult)
        assert isinstance(result.response, CreateMediaBuySuccess)
        assert result.response.media_buy_id == "mb_original_123"
        assert result.status == "completed"
        assert result.replayed is True  # top-level replay marker, injected at replay time

    async def test_different_payload_same_key_raises_conflict(self, integration_db):
        from src.core.exceptions import AdCPError
        from src.core.tools.media_buy_create import _create_media_buy_impl

        idem_key = f"conflict-{uuid.uuid4().hex[:8]}"
        tenant_id = f"conflict_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"

        _seed_principal(tenant_id, principal_id)
        # Stored hash will NOT match the request's canonical hash → conflict.
        _seed_success(tenant_id, principal_id, idem_key, media_buy_id="mb_first", payload_hash="non-matching-hash")

        with pytest.raises(AdCPError) as exc_info:
            await _create_media_buy_impl(req=_make_request(idem_key), identity=_identity(tenant_id, principal_id))

        exc = exc_info.value
        assert exc.error_code == "IDEMPOTENCY_CONFLICT"
        # Read-oracle defense: the conflict must not leak the cached payload/id.
        assert "mb_first" not in exc.message

    async def test_unrelated_key_does_not_replay(self, integration_db):
        """A different idempotency_key on the same principal never serves the seeded success."""
        from src.core.exceptions import AdCPError
        from src.core.tools.media_buy_create import _create_media_buy_impl

        seeded_key = f"seeded-{uuid.uuid4().hex[:8]}"
        other_key = f"other-{uuid.uuid4().hex[:8]}"
        tenant_id = f"miss_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"

        _seed_principal(tenant_id, principal_id)
        _seed_success(tenant_id, principal_id, seeded_key, media_buy_id="mb_seeded_other")

        # other_key has no cached success — the seeded buy must never be served. The
        # empty-packages request may still fail downstream, but never as a replay.
        try:
            result = await _create_media_buy_impl(
                req=_make_request(other_key), identity=_identity(tenant_id, principal_id)
            )
        except AdCPError:
            return  # failed downstream — definitively not a replay of the seeded success
        assert getattr(result, "replayed", False) is False
        if hasattr(result.response, "media_buy_id"):
            assert result.response.media_buy_id != "mb_seeded_other"


class TestErrorsAreNeverCached:
    """An error path writes no IdempotencyAttempt row — a retry re-executes (spec)."""

    def test_adapter_rejection_not_cached(self, integration_db):
        from datetime import timedelta

        from src.core.database.repositories import MediaBuyUoW
        from src.core.exceptions import AdCPError
        from src.core.schemas import CreateMediaBuyError, Error

        idem_key = f"err-{uuid.uuid4().hex[:8]}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            adapter = env.mock["adapter"].return_value
            adapter.create_media_buy.side_effect = None
            adapter.create_media_buy.return_value = CreateMediaBuyError(
                errors=[Error(code="ADAPTER_ERROR", message="adapter failure", recovery="terminal")],
                context=None,
            )
            now = datetime.now(UTC)
            # The adapter error surfaces as a failed result or a raised AdCPError —
            # either way the key must NOT be cached.
            try:
                result = env.call_impl(
                    brand={"domain": "err-test.example.com"},
                    packages=[
                        {"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}
                    ],
                    start_time=(now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    end_time=(now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    po_number="ERR-1",
                    idempotency_key=idem_key,
                )
                assert result.status == "failed"
            except AdCPError:
                pass
            tenant_id = env._tenant_id
            principal_id = env._principal_id

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            cached = uow.idempotency_attempts.find_by_key(
                principal_id=principal_id,
                tool_name="create_media_buy",
                idempotency_key=idem_key,
            )
            assert cached is None, "Errors must never be cached — a retry must re-execute"
