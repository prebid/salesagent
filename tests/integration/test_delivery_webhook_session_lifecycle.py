"""Scheduler session ownership: internal batch/manual callbacks, real DB and TLS.

These callbacks have no buyer transport. Multiple rows expose reuse of expired
ORM state after stored-account resolution; the receiver and saved outcomes grade
the complete callback, including duplicate suppression and forced sequences.
"""

import pytest

from src.services.delivery_webhook_scheduler import DeliveryWebhookScheduler
from tests.harness.sandbox_lifecycle import SchedulerLifecycleEnv
from tests.helpers.log_capture import capture_logs

pytestmark = [pytest.mark.requires_db, pytest.mark.asyncio]


@pytest.mark.parametrize("account_id", ["sandbox", "live"])
async def test_scheduled_batch_survives_account_resolution_and_suppresses_duplicates(integration_db, account_id):
    with SchedulerLifecycleEnv() as env:
        env.seed_lifecycle()
        for buy_id in ("first-report", "second-report"):
            env.seed_reporting_buy(account_id, buy_id)
        scheduler = DeliveryWebhookScheduler()

        with env.delivery_session_states() as sessions:
            await scheduler._send_reports()

        assert sessions == [False, False]
        assert env.delivery_attempts == 2
        reported = {
            request.json()["result"]["media_buy_deliveries"][0]["media_buy_id"] for request in env.delivered_requests
        }
        assert reported == {"first-report", "second-report"}
        for buy_id in reported:
            assert env.report_sequences(buy_id) == [(1, "success", "scheduled")]

        await scheduler._send_reports()

        assert env.delivery_attempts == 2
        for buy_id in reported:
            assert env.report_sequences(buy_id) == [(1, "success", "scheduled")]
        assert env.vendor_calls() == 0


@pytest.mark.parametrize("account_id", ["sandbox", "live"])
async def test_manual_report_forces_frequency_and_duplicate_checks_with_stored_registration(integration_db, account_id):
    with SchedulerLifecycleEnv() as env:
        env.seed_lifecycle()
        env.seed_reporting_buy(account_id, "manual-report", frequency="hourly")
        env.register_competing_targets()
        scheduler = DeliveryWebhookScheduler()

        await scheduler._send_reports()
        assert env.delivery_attempts == 0
        with env.delivery_session_states() as sessions:
            for _ in range(2):
                assert await scheduler.trigger_report_for_media_buy_by_id("manual-report", env.tenant_id) is True

        assert sessions == [False, False]

        assert env.delivery_attempts == 2
        assert env.report_sequences("manual-report") == [(1, "success", "scheduled"), (2, "success", "scheduled")]
        for request in env.delivered_requests:
            assert request.headers["authorization"] == "Bearer stored-registration-secret-at-least-32-characters"
            assert request.json()["operation_id"] == "stored-operation"
            assert request.json()["token"] == "stored-correlation-token"
            assert request.json()["result"]["media_buy_deliveries"][0]["media_buy_id"] == "manual-report"
        assert env.vendor_calls() == 0


async def test_failed_scheduled_send_remains_eligible_and_advances_sequence(integration_db):
    with SchedulerLifecycleEnv() as env:
        env.seed_lifecycle()
        env.seed_reporting_buy("sandbox", "retry-report")
        env.set_http_status(400)
        scheduler = DeliveryWebhookScheduler()

        await scheduler._send_reports()
        assert env.delivery_attempts == 1
        assert env.report_sequences("retry-report") == [(1, "failed", "scheduled")]

        env.set_http_status(200)
        await scheduler._send_reports()
        await scheduler._send_reports()

        assert env.delivery_attempts == 2
        assert env.report_sequences("retry-report") == [(1, "failed", "scheduled"), (2, "success", "scheduled")]


@pytest.mark.parametrize("separator", ["\r", "\n", "\r\n"], ids=["cr", "lf", "crlf"])
async def test_missing_report_buy_logs_one_line_without_changing_lookup(integration_db, separator):
    with SchedulerLifecycleEnv() as env:
        env.seed_lifecycle()
        env.seed_reporting_buy("sandbox", "missingbuy")
        scheduler = DeliveryWebhookScheduler()

        with capture_logs("src.services.delivery_webhook_scheduler") as logs:
            found = await scheduler.trigger_report_for_media_buy_by_id(f"missing{separator}buy", env.tenant_id)

        assert found is False
        assert env.delivery_attempts == 0
        assert logs.records == ["Cannot trigger report: Media buy missingbuy not found or has no reporting_webhook"]

        assert await scheduler.trigger_report_for_media_buy_by_id("missingbuy", env.tenant_id) is True
        assert env.delivery_attempts == 1
        assert env.delivered_requests[0].json()["result"]["media_buy_deliveries"][0]["media_buy_id"] == "missingbuy"
