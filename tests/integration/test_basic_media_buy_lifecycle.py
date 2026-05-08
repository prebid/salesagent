"""Stage 1 of end-to-end media buy validation: in-process basic-mode lifecycle.

Drives the four ``_impl`` functions directly against a real Postgres DB with the
mock adapter:

  1. ``_get_products_impl``         — discover the seeded product
  2. ``_create_media_buy_impl``     — create a media buy (auto-approved)
  3. ``_sync_creatives_impl``       — upload + assign a creative
  4. ``_get_media_buy_delivery_impl`` — fetch delivery shape

The mock adapter exercises the same business-logic path the GAM adapter takes
without network calls or credentials. Once this is green, swapping the tenant's
``adapter_type`` to ``google_ad_manager`` is a one-line change for the Stage 2
real-network variant.

Reuses ``sample_tenant`` / ``sample_principal`` / ``sample_products`` fixtures
from ``tests/integration/conftest.py`` (they already seed CurrencyLimit, PropertyTag,
AuthorizedProperty, GAMInventory, TenantAuthConfig — everything ``validate_setup_complete``
checks).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from src.core.database.models import MediaBuy as DBMediaBuy
from src.core.database.models import MediaPackage as DBMediaPackage
from tests.integration.media_buy_helpers import (
    _get_tenant_dict as _tenant_dict,
)
from tests.integration.media_buy_helpers import (
    admin_mark_creative_approved,
    force_media_buy_status,
    make_lifecycle_identity,
    set_tenant_approval_mode,
    set_tenant_human_review_required,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db, pytest.mark.asyncio]


def _future(days: int) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


class TestBasicMediaBuyLifecycle:
    """Happy-path 4-phase lifecycle against the mock adapter, in-process."""

    async def test_basic_lifecycle_end_to_end(self, sample_tenant, sample_principal, sample_products):
        """get_products → create_media_buy → sync_creatives → get_media_buy_delivery."""
        from src.core.schemas import (
            CreateMediaBuyRequest,
            GetMediaBuyDeliveryRequest,
            GetProductsRequest,
        )
        from src.core.tools.creatives._sync import _sync_creatives_impl
        from src.core.tools.media_buy_create import _create_media_buy_impl
        from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl
        from src.core.tools.products import _get_products_impl

        tenant_dict = _tenant_dict(sample_tenant["tenant_id"])
        identity = make_lifecycle_identity(tenant_dict, sample_principal["principal_id"])

        # ───────── Phase 1: get_products ─────────
        products_req = GetProductsRequest(
            brand={"domain": "testbrand.com"},
            brief="display advertising",
        )
        products_resp = await _get_products_impl(products_req, identity)

        assert products_resp.products, f"Expected products, got: {products_resp}"
        # Pick the guaranteed CPM/USD product (matches what _make_create_request defaults to)
        chosen = next(
            (p for p in products_resp.products if p.product_id == "guaranteed_display"),
            None,
        )
        assert (
            chosen is not None
        ), f"Expected 'guaranteed_display' in product list, got {[p.product_id for p in products_resp.products]}"
        # Synthesized pricing_option_id format: "{model}_{currency}_{fixed|auction}"
        # See src/core/tools/media_buy_create.py:1654-1661
        pricing_option_id = "cpm_usd_fixed"

        # ───────── Phase 2: create_media_buy ─────────
        create_req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            start_time=_future(1),
            end_time=_future(8),
            packages=[
                {
                    "product_id": chosen.product_id,
                    "budget": 5000.0,
                    "pricing_option_id": pricing_option_id,
                }
            ],
        )

        create_result = await _create_media_buy_impl(req=create_req, identity=identity)

        assert create_result.status not in ("failed",), (
            f"create_media_buy failed: status={create_result.status}, "
            f"errors={getattr(create_result.response, 'errors', None)}"
        )
        media_buy_id = create_result.response.media_buy_id
        assert media_buy_id, f"media_buy_id missing: {create_result.response}"

        with get_db_session() as session:
            mb = session.scalars(select(DBMediaBuy).where(DBMediaBuy.media_buy_id == media_buy_id)).first()
            assert mb is not None, f"MediaBuy {media_buy_id} not persisted"
            packages = session.scalars(select(DBMediaPackage).where(DBMediaPackage.media_buy_id == media_buy_id)).all()
            assert packages, f"No MediaPackage rows for {media_buy_id}"

        # ───────── Phase 3: sync_creatives ─────────
        creative_id = f"cr_{uuid.uuid4().hex[:8]}"
        creative = {
            "creative_id": creative_id,
            "name": "Lifecycle Test Creative",
            "format": "display_300x250",
            "format_id": {
                "agent_url": "https://creative.adcontextprotocol.org",
                "id": "display_300x250",
            },
            "media_url": "https://example.com/creative.png",
            "click_through_url": "https://example.com/landing",
        }

        sync_resp = _sync_creatives_impl(
            creatives=[creative],
            assignments={creative_id: [packages[0].package_id]},
            identity=identity,
        )

        assert sync_resp.creatives, f"sync_creatives returned no creatives: {sync_resp}"
        assert any(
            c.creative_id == creative_id for c in sync_resp.creatives
        ), f"Synced creative {creative_id} missing from response: {[c.creative_id for c in sync_resp.creatives]}"

        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).where(
                    DBCreative.creative_id == creative_id,
                    DBCreative.tenant_id == tenant_dict["tenant_id"],
                )
            ).first()
            assert db_creative is not None, f"Creative {creative_id} not persisted to DB"

        # ───────── Phase 4: get_media_buy_delivery ─────────
        delivery_req = GetMediaBuyDeliveryRequest(
            media_buy_ids=[media_buy_id],
            start_date=datetime.now(UTC).date().isoformat(),
            end_date=(datetime.now(UTC) + timedelta(days=8)).date().isoformat(),
        )
        delivery_resp = _get_media_buy_delivery_impl(delivery_req, identity)

        assert (
            delivery_resp.errors is None or len(delivery_resp.errors) == 0
        ), f"get_media_buy_delivery returned errors: {delivery_resp.errors}"
        # Mock adapter may return zero metrics for a fresh future-dated buy.
        # Validate shape, not values: the response must include our media buy.
        deliveries = delivery_resp.media_buy_deliveries or []
        delivery_ids = [d.media_buy_id for d in deliveries]
        assert (
            media_buy_id in delivery_ids
        ), f"Expected {media_buy_id} in delivery response, got {delivery_ids}. Full response: {delivery_resp}"


class TestCreativeApprovalAsync:
    """Stage 3a: creative goes through human-approval workflow."""

    async def test_creative_pending_review_then_approved(self, sample_tenant, sample_principal, sample_products):
        """Default approval_mode='require-human' → creative status=pending_review on
        sync; admin approval transitions to status=approved."""
        from src.core.database.models import Creative as DBCreative
        from src.core.tools.creatives._sync import _sync_creatives_impl

        # Force the tenant to require human approval (default in DB but
        # be explicit so this test is independent of fixture defaults).
        set_tenant_approval_mode(sample_tenant["tenant_id"], "require-human")

        tenant_dict = _tenant_dict(sample_tenant["tenant_id"])
        # _sync_creatives_impl reads approval_mode straight from the dict.
        tenant_dict["approval_mode"] = "require-human"
        identity = make_lifecycle_identity(tenant_dict, sample_principal["principal_id"])

        creative_id = f"cr_{uuid.uuid4().hex[:8]}"
        sync_resp = _sync_creatives_impl(
            creatives=[
                {
                    "creative_id": creative_id,
                    "name": "Pending Approval Creative",
                    "format": "display_300x250",
                    "format_id": {
                        "agent_url": "https://creative.adcontextprotocol.org",
                        "id": "display_300x250",
                    },
                    "media_url": "https://example.com/creative.png",
                    "click_through_url": "https://example.com/landing",
                }
            ],
            identity=identity,
        )

        # Sync result lists the creative; status reflects approval mode.
        synced = next((c for c in (sync_resp.creatives or []) if c.creative_id == creative_id), None)
        assert synced is not None, f"creative {creative_id} missing from sync response"

        with get_db_session() as session:
            row = session.scalars(
                select(DBCreative).where(
                    DBCreative.creative_id == creative_id,
                    DBCreative.tenant_id == tenant_dict["tenant_id"],
                )
            ).first()
            assert row is not None, f"creative {creative_id} not persisted"
            assert (
                row.status == "pending_review"
            ), f"Expected pending_review, got {row.status}. sync response status={synced.status}"

        # Simulate admin approval through the same repository method the
        # admin Flask route uses (CreativeRepository.admin_mark_approved).
        admin_mark_creative_approved(tenant_dict["tenant_id"], creative_id, approved_by="test_admin")

        with get_db_session() as session:
            row = session.scalars(
                select(DBCreative).where(
                    DBCreative.creative_id == creative_id,
                    DBCreative.tenant_id == tenant_dict["tenant_id"],
                )
            ).first()
            assert row is not None
            assert row.status == "approved"
            assert row.approved_by == "test_admin"


class TestMediaBuyApprovalAsync:
    """Stage 3b: media buy goes through human-approval workflow."""

    async def test_human_review_required_creates_workflow_step_then_executes(
        self, sample_tenant, sample_principal, sample_products
    ):
        """tenant.human_review_required=True → create_media_buy returns the
        sync-success envelope (variant-1) carrying ``media_buy_id`` and
        ``MediaBuyStatus.pending_creatives`` (no creatives in this request);
        a ``requires_approval`` workflow_step is parked for the human;
        execute_approved_media_buy then transitions the buy to active."""
        from adcp.types import MediaBuyStatus

        from src.core.database.models import MediaBuy as DBMediaBuy
        from src.core.database.models import WorkflowStep
        from src.core.schemas import CreateMediaBuyRequest
        from src.core.tools.media_buy_create import (
            _create_media_buy_impl,
            execute_approved_media_buy,
        )

        set_tenant_human_review_required(sample_tenant["tenant_id"], True)

        tenant_dict = _tenant_dict(sample_tenant["tenant_id"])
        identity = make_lifecycle_identity(tenant_dict, sample_principal["principal_id"])

        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            start_time=_future(1),
            end_time=_future(8),
            packages=[
                {
                    "product_id": "guaranteed_display",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                }
            ],
        )
        result = await _create_media_buy_impl(req=req, identity=identity)

        # Variant-1 (sync-success): the buy is minted synchronously with a
        # ``MediaBuyStatus`` describing what's blocking activation. Without
        # creatives in the request that's ``pending_creatives``. The wrapper
        # ``status`` reports the seller's task as ``completed`` (sync work done).
        assert (
            result.status == "completed"
        ), f"Expected completed, got status={result.status}, errors={getattr(result.response, 'errors', None)}"
        media_buy_id = result.response.media_buy_id
        assert media_buy_id
        assert result.response.status == MediaBuyStatus.pending_creatives

        with get_db_session() as session:
            steps = session.scalars(select(WorkflowStep).where(WorkflowStep.step_type == "media_buy_creation")).all()
            approval_steps = [s for s in steps if s.status == "requires_approval"]
            assert (
                approval_steps
            ), f"Expected requires_approval workflow_step, got {[(s.step_id, s.status) for s in steps]}"

        # Execute approval.
        success, error = execute_approved_media_buy(
            media_buy_id=media_buy_id,
            tenant_id=tenant_dict["tenant_id"],
        )
        assert success, f"execute_approved_media_buy failed: {error}"

        with get_db_session() as session:
            mb = session.scalars(select(DBMediaBuy).where(DBMediaBuy.media_buy_id == media_buy_id)).first()
            assert mb is not None
            assert mb.status == "active", f"Expected active after approval, got {mb.status}"


class TestDeliveryStatusExcludedError:
    """Buyers polling delivery on a future-dated buy used to get a misleading
    'media_buy_not_found' error. The fix returns 'media_buy_status_excluded'
    with the actual current status instead."""

    async def test_future_dated_buy_returns_status_excluded_error(
        self, sample_tenant, sample_principal, sample_products
    ):
        from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus

        from src.core.schemas import (
            CreateMediaBuyRequest,
            GetMediaBuyDeliveryRequest,
        )
        from src.core.tools.media_buy_create import _create_media_buy_impl
        from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl

        tenant_dict = _tenant_dict(sample_tenant["tenant_id"])
        identity = make_lifecycle_identity(tenant_dict, sample_principal["principal_id"])

        # Buy starts in the future → date-derived dynamic status = "ready".
        create_result = await _create_media_buy_impl(
            req=CreateMediaBuyRequest(
                brand={"domain": "testbrand.com"},
                start_time=_future(2),
                end_time=_future(8),
                packages=[
                    {
                        "product_id": "guaranteed_display",
                        "budget": 1000.0,
                        "pricing_option_id": "cpm_usd_fixed",
                    }
                ],
            ),
            identity=identity,
        )
        media_buy_id = create_result.response.media_buy_id
        assert media_buy_id

        # Query delivery with status_filter=[active] and a window ending today —
        # reference_date = end_date = today, buy starts 2 days from now, so
        # the dynamic status is "ready". The impl should report it as
        # status_excluded, NOT as not_found.
        today = datetime.now(UTC).date()
        delivery_resp = _get_media_buy_delivery_impl(
            GetMediaBuyDeliveryRequest(
                media_buy_ids=[media_buy_id],
                status_filter=[MediaBuyStatus.active],
                start_date=(today - timedelta(days=1)).isoformat(),
                end_date=today.isoformat(),
            ),
            identity,
        )

        assert delivery_resp.errors, f"expected an error, got: {delivery_resp}"
        codes = [e.code for e in delivery_resp.errors]
        assert "media_buy_status_excluded" in codes, f"got codes: {codes}"
        assert "media_buy_not_found" not in codes, f"buyer would have seen misleading 'not_found': {codes}"
        # Status should mention "ready" (the dynamic status of a future buy).
        msgs = [e.message for e in delivery_resp.errors]
        assert any("ready" in m for m in msgs), f"expected 'ready' in messages: {msgs}"


class TestDeliveryWebhookFires:
    """Stage D: scheduler delivers webhook payload to reporting_webhook URL."""

    async def test_scheduler_posts_delivery_report_to_webhook(self, sample_tenant, sample_principal, sample_products):
        """Lifecycle with reporting_webhook → manually-triggered scheduler → local
        HTTPServer receives the AdCP MCP webhook envelope."""
        import json
        import socket
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from threading import Thread
        from time import sleep

        from src.core.schemas import CreateMediaBuyRequest
        from src.core.tools.media_buy_create import _create_media_buy_impl
        from src.services.delivery_webhook_scheduler import DeliveryWebhookScheduler

        # ── Local HTTP receiver ──
        received: list[dict] = []

        class _Receiver(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                received.append(json.loads(self.rfile.read(length).decode()))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *_a, **_k):
                pass

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        server = HTTPServer(("127.0.0.1", port), _Receiver)
        Thread(target=server.serve_forever, daemon=True).start()
        try:
            tenant_dict = _tenant_dict(sample_tenant["tenant_id"])
            identity = make_lifecycle_identity(tenant_dict, sample_principal["principal_id"])

            create_req = CreateMediaBuyRequest(
                brand={"domain": "testbrand.com"},
                # 'asap' so the date-based dynamic status is "active" — the
                # scheduler's status_filter=[active, completed] is evaluated
                # against dates, not the DB column. See _get_target_media_buys.
                start_time="asap",
                end_time=_future(8),
                packages=[
                    {
                        "product_id": "guaranteed_display",
                        "budget": 5000.0,
                        "pricing_option_id": "cpm_usd_fixed",
                    }
                ],
                reporting_webhook={
                    "url": f"http://127.0.0.1:{port}/webhook",
                    "reporting_frequency": "daily",
                    "authentication": {
                        # schemes/credentials per ReportingWebhook spec; the
                        # scheduler currently doesn't enforce these but the
                        # schema requires the fields to be present.
                        "schemes": ["Bearer"],
                        "credentials": "test_credential_minimum_32_chars_xxx",
                    },
                },
            )
            create_result = await _create_media_buy_impl(req=create_req, identity=identity)
            assert create_result.status not in (
                "failed",
            ), f"create failed: errors={getattr(create_result.response, 'errors', None)}"
            media_buy_id = create_result.response.media_buy_id
            assert media_buy_id

            # The scheduler's delivery query filters status_filter=[active, completed].
            # A freshly-created mock buy lands in pending_activation/pending_creatives;
            # force-promote so the delivery lookup includes it.
            force_media_buy_status(tenant_dict["tenant_id"], media_buy_id, "active")

            # Force-trigger the scheduler (bypasses 24h dedupe + frequency check).
            scheduler = DeliveryWebhookScheduler()
            triggered = await scheduler.trigger_report_for_media_buy_by_id(
                media_buy_id=media_buy_id,
                tenant_id=tenant_dict["tenant_id"],
            )
            assert triggered, "scheduler.trigger_report_for_media_buy_by_id returned False"

            # Wait briefly for the synchronous POST to land.
            deadline = 10.0
            elapsed = 0.0
            while not received and elapsed < deadline:
                sleep(0.5)
                elapsed += 0.5
            assert received, f"No webhook received within {deadline}s"

            payload = received[0]
            assert payload.get("status") == "completed", payload
            assert payload.get("task_id") == media_buy_id, payload
            assert "timestamp" in payload, payload

            result = payload.get("result") or {}
            deliveries = result.get("media_buy_deliveries")
            assert deliveries, f"missing media_buy_deliveries: {result}"
            assert deliveries[0]["media_buy_id"] == media_buy_id
            assert result.get("notification_type") == "scheduled", result
        finally:
            server.shutdown()
            server.server_close()


class TestDeliveryWebhookHeartbeatForPendingStart:
    """Issue #48: future-dated buys (pending_start) should get heartbeat reports.

    Default ``tenant.report_pre_start_buys=True`` means a buy with
    ``start_time`` in the future and a configured reporting_webhook still
    receives a daily heartbeat — flagged ``partial_data=true`` and carrying
    impressions=0. Lets buyers stop polling for "did my flight land?" once
    they configure the webhook.
    """

    async def test_pending_start_buy_gets_heartbeat_with_partial_data(
        self, sample_tenant, sample_principal, sample_products
    ):
        import json
        import socket
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from threading import Thread
        from time import sleep

        from src.core.schemas import CreateMediaBuyRequest
        from src.core.tools.media_buy_create import _create_media_buy_impl
        from src.services.delivery_webhook_scheduler import DeliveryWebhookScheduler

        received: list[dict] = []

        class _Receiver(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                received.append(json.loads(self.rfile.read(length).decode()))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *_a, **_k):
                pass

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        server = HTTPServer(("127.0.0.1", port), _Receiver)
        Thread(target=server.serve_forever, daemon=True).start()

        try:
            tenant_dict = _tenant_dict(sample_tenant["tenant_id"])
            identity = make_lifecycle_identity(tenant_dict, sample_principal["principal_id"])

            create_result = await _create_media_buy_impl(
                req=CreateMediaBuyRequest(
                    brand={"domain": "testbrand.com"},
                    # Future start so the date-derived dynamic status is
                    # 'pending_start'. Pre-#48 the scheduler skipped these.
                    start_time=_future(2),
                    end_time=_future(8),
                    packages=[
                        {
                            "product_id": "guaranteed_display",
                            "budget": 5000.0,
                            "pricing_option_id": "cpm_usd_fixed",
                        }
                    ],
                    reporting_webhook={
                        "url": f"http://127.0.0.1:{port}/webhook",
                        "reporting_frequency": "daily",
                        "authentication": {
                            "schemes": ["Bearer"],
                            "credentials": "test_credential_minimum_32_chars_xxx",
                        },
                    },
                ),
                identity=identity,
            )
            assert create_result.status not in (
                "failed",
            ), f"create failed: {getattr(create_result.response, 'errors', None)}"
            media_buy_id = create_result.response.media_buy_id
            assert media_buy_id

            scheduler = DeliveryWebhookScheduler()
            triggered = await scheduler.trigger_report_for_media_buy_by_id(
                media_buy_id=media_buy_id,
                tenant_id=tenant_dict["tenant_id"],
            )
            assert triggered

            deadline = 10.0
            elapsed = 0.0
            while not received and elapsed < deadline:
                sleep(0.5)
                elapsed += 0.5
            assert received, (
                f"No webhook received within {deadline}s — pre-#48 the "
                f"scheduler silently skipped pending_start buys; this test "
                f"guards the new heartbeat behaviour."
            )

            payload = received[0]
            result = payload.get("result") or {}
            assert (
                result.get("partial_data") is True
            ), f"Heartbeat for pending_start should set partial_data=True, got result={result}"
            deliveries = result.get("media_buy_deliveries") or []
            assert deliveries, f"missing media_buy_deliveries: {result}"
            assert deliveries[0]["media_buy_id"] == media_buy_id
            # Buy is pre-start so no impressions yet.
            assert (deliveries[0].get("totals") or {}).get("impressions", 0) == 0
        finally:
            server.shutdown()
            server.server_close()


class TestDeliveryWebhookOptOutPreStart:
    """Issue #48: tenant can opt out of pre-start heartbeats.

    With ``tenant.report_pre_start_buys=False`` the scheduler reverts to
    the legacy ``[active, completed]`` filter — pending_start buys get
    silently skipped (status_excluded INFO log, no webhook).
    """

    async def test_opt_out_silences_pending_start_heartbeat(self, sample_tenant, sample_principal, sample_products):
        import json
        import socket
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from threading import Thread
        from time import sleep

        from src.core.database.repositories.uow import TenantConfigUoW
        from src.core.schemas import CreateMediaBuyRequest
        from src.core.tools.media_buy_create import _create_media_buy_impl
        from src.services.delivery_webhook_scheduler import DeliveryWebhookScheduler

        # Opt the tenant out of pre-start heartbeats.
        with TenantConfigUoW(sample_tenant["tenant_id"]) as uow:
            assert uow.tenant_config is not None
            t = uow.tenant_config.get_tenant()
            assert t is not None
            t.report_pre_start_buys = False

        received: list[dict] = []

        class _Receiver(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                received.append(json.loads(self.rfile.read(length).decode()))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *_a, **_k):
                pass

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        server = HTTPServer(("127.0.0.1", port), _Receiver)
        Thread(target=server.serve_forever, daemon=True).start()

        try:
            tenant_dict = _tenant_dict(sample_tenant["tenant_id"])
            identity = make_lifecycle_identity(tenant_dict, sample_principal["principal_id"])

            create_result = await _create_media_buy_impl(
                req=CreateMediaBuyRequest(
                    brand={"domain": "testbrand.com"},
                    start_time=_future(2),
                    end_time=_future(8),
                    packages=[
                        {
                            "product_id": "guaranteed_display",
                            "budget": 5000.0,
                            "pricing_option_id": "cpm_usd_fixed",
                        }
                    ],
                    reporting_webhook={
                        "url": f"http://127.0.0.1:{port}/webhook",
                        "reporting_frequency": "daily",
                        "authentication": {
                            "schemes": ["Bearer"],
                            "credentials": "test_credential_minimum_32_chars_xxx",
                        },
                    },
                ),
                identity=identity,
            )
            assert create_result.status not in ("failed",)
            media_buy_id = create_result.response.media_buy_id

            scheduler = DeliveryWebhookScheduler()
            await scheduler.trigger_report_for_media_buy_by_id(
                media_buy_id=media_buy_id,
                tenant_id=tenant_dict["tenant_id"],
            )

            # Give the receiver thread a chance to pick up any inbound POST.
            sleep(2.0)
            assert (
                not received
            ), f"Expected no webhook (tenant opted out of pre-start heartbeats) but receiver got: {received}"
        finally:
            server.shutdown()
            server.server_close()
