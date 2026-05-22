"""Google Ad Manager Reporting Manager.

Handles delivery reporting and webhook notifications for active campaigns.
"""

import logging
import threading
from datetime import UTC, datetime

from src.core.thread_registry import ThreadRegistry
from src.services.webhook_delivery_service import webhook_delivery_service

logger = logging.getLogger(__name__)


class GAMReportingManager:
    """Manages delivery reporting and webhooks for GAM campaigns."""

    def __init__(self, gam_client, config: dict):
        """Initialize the reporting manager.

        Args:
            gam_client: GAM client instance
            config: Adapter configuration
        """
        self.gam_client = gam_client
        self.config = config
        # ThreadRegistry reaps dead reporting threads on every read. The reap
        # callback drops the parallel _stop_signals entry in lockstep
        # (lock-free dict.pop — ABBA-safe vs self._lock).
        self._active_reports = ThreadRegistry()
        self._stop_signals: dict[str, threading.Event] = {}
        self._lock = threading.Lock()  # Protect _stop_signals
        self._active_reports.add_reap_callback(self._on_report_reaped)

        logger.info("✅ GAM Reporting Manager initialized")

    def _on_report_reaped(self, media_buy_id: str) -> None:
        """Drop the parallel _stop_signals entry when a dead report is reaped.

        Lock-free (``dict.pop`` is atomic under the GIL). MUST NOT acquire
        ``self._lock`` — see ThreadRegistry.add_reap_callback docstring.
        """
        self._stop_signals.pop(media_buy_id, None)

    def start_delivery_reporting(
        self,
        media_buy_id: str,
        tenant_id: str,
        principal_id: str,
        order_id: str,
        start_time: datetime,
        end_time: datetime,
        total_budget: float,
        reporting_interval_hours: int = 24,
    ):
        """Start periodic delivery reporting with webhook notifications.

        Thread-safe operation.

        Args:
            media_buy_id: Media buy identifier
            tenant_id: Tenant identifier
            principal_id: Principal identifier
            order_id: GAM order ID
            start_time: Campaign start datetime
            end_time: Campaign end datetime
            total_budget: Total campaign budget
            reporting_interval_hours: Hours between reports (default: 24)
        """
        # Atomically reserve the slot via _stop_signals under self._lock.
        # Concurrent callers serialize here. Never call into the registry
        # while holding self._lock (registry has its own lock + a reap
        # callback that re-enters _stop_signals — ABBA avoidance).
        with self._lock:
            if media_buy_id in self._stop_signals:
                logger.warning(f"Delivery reporting already running for {media_buy_id}")
                return
            stop_signal = threading.Event()
            self._stop_signals[media_buy_id] = stop_signal

        # Start reporting thread (registry has its own lock)
        thread = threading.Thread(
            target=self._run_reporting,
            args=(
                media_buy_id,
                tenant_id,
                principal_id,
                order_id,
                start_time,
                end_time,
                total_budget,
                reporting_interval_hours,
                stop_signal,
            ),
            daemon=True,
        )
        self._active_reports.add(media_buy_id, thread)
        thread.start()

        logger.info(f"✅ Started delivery reporting for {media_buy_id} (interval: {reporting_interval_hours}h)")

    def stop_delivery_reporting(self, media_buy_id: str):
        """Stop delivery reporting for a media buy.

        Thread-safe operation.

        Args:
            media_buy_id: Media buy identifier
        """
        with self._lock:
            if media_buy_id in self._stop_signals:
                self._stop_signals[media_buy_id].set()
                logger.info(f"🛑 Stopping delivery reporting for {media_buy_id}")

    def _run_reporting(
        self,
        media_buy_id: str,
        tenant_id: str,
        principal_id: str,
        order_id: str,
        start_time: datetime,
        end_time: datetime,
        total_budget: float,
        reporting_interval_hours: int,
        stop_signal: threading.Event,
    ):
        """Run the delivery reporting (thread worker).

        Args:
            media_buy_id: Media buy identifier
            tenant_id: Tenant identifier
            principal_id: Principal identifier
            order_id: GAM order ID
            start_time: Campaign start datetime
            end_time: Campaign end datetime
            total_budget: Total campaign budget
            reporting_interval_hours: Hours between reports
            stop_signal: Event to signal reporting stop
        """
        try:
            reporting_interval_seconds = reporting_interval_hours * 3600

            logger.info(
                f"📊 Reporting parameters for {media_buy_id}:\n"
                f"   GAM Order ID: {order_id}\n"
                f"   Campaign: {start_time.date()} to {end_time.date()}\n"
                f"   Reporting interval: {reporting_interval_hours} hours"
            )

            # Send initial webhook - campaign started
            webhook_delivery_service.send_delivery_webhook(
                media_buy_id=media_buy_id,
                tenant_id=tenant_id,
                principal_id=principal_id,
                reporting_period_start=start_time,
                reporting_period_end=start_time,
                impressions=0,
                spend=0.0,
                status="pending",
                clicks=0,
                ctr=0.0,
                is_final=False,
                next_expected_interval_seconds=reporting_interval_seconds,
            )

            # Loop until campaign ends or stop signal
            while datetime.now(UTC) < end_time and not stop_signal.is_set():
                # Wait for reporting interval
                if stop_signal.wait(reporting_interval_seconds):
                    break  # Stop signal received

                # Fetch delivery metrics from GAM
                try:
                    metrics = self._fetch_gam_delivery_metrics(order_id, start_time, datetime.now(UTC))

                    # Determine if this is the final report
                    now = datetime.now(UTC)
                    is_final = now >= end_time

                    # Send delivery webhook
                    webhook_delivery_service.send_delivery_webhook(
                        media_buy_id=media_buy_id,
                        tenant_id=tenant_id,
                        principal_id=principal_id,
                        reporting_period_start=start_time,
                        reporting_period_end=now,
                        impressions=metrics.get("impressions", 0),
                        spend=metrics.get("spend", 0.0),
                        status="completed" if is_final else "active",
                        clicks=metrics.get("clicks", 0),
                        ctr=metrics.get("ctr", 0.0),
                        is_final=is_final,
                        next_expected_interval_seconds=reporting_interval_seconds if not is_final else None,
                    )

                    if is_final:
                        logger.info(f"🎉 Campaign {media_buy_id} reporting completed")
                        break

                except Exception as e:
                    logger.error(f"❌ Error fetching GAM metrics for {media_buy_id}: {e}", exc_info=True)
                    # Continue reporting despite errors

        except Exception as e:
            logger.error(f"❌ Error in delivery reporting for {media_buy_id}: {e}", exc_info=True)
        finally:
            # Thread-safe cleanup. Drop the registry entry first (its own
            # lock); then drop the parallel _stop_signals entry under
            # self._lock. Never nest the two locks.
            self._active_reports.remove(media_buy_id)
            with self._lock:
                self._stop_signals.pop(media_buy_id, None)

            # Reset webhook sequence number
            webhook_delivery_service.reset_sequence(media_buy_id)

    def _fetch_gam_delivery_metrics(self, order_id: str, start_date: datetime, end_date: datetime) -> dict:
        """Fetch delivery metrics from GAM API.

        Args:
            order_id: GAM order ID
            start_date: Report start date
            end_date: Report end date

        Returns:
            Dictionary with impressions, spend, clicks, ctr
        """
        try:
            # TODO: Implement actual GAM API call using gam_client
            # This would use the GAM Reporting API to fetch:
            # - TOTAL_LINE_ITEM_LEVEL_IMPRESSIONS
            # - TOTAL_LINE_ITEM_LEVEL_CLICKS
            # - TOTAL_LINE_ITEM_LEVEL_CPM_AND_CPC_REVENUE
            #
            # For now, return placeholder data
            logger.debug(f"Fetching GAM metrics for order {order_id} from {start_date.date()} to {end_date.date()}")

            # Placeholder - would be replaced with actual GAM API call
            return {
                "impressions": 0,
                "clicks": 0,
                "spend": 0.0,
                "ctr": 0.0,
            }

        except Exception as e:
            logger.error(f"Error fetching GAM delivery metrics: {e}", exc_info=True)
            return {
                "impressions": 0,
                "clicks": 0,
                "spend": 0.0,
                "ctr": 0.0,
            }
