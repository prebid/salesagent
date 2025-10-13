"""Delivery simulation service for mock adapter.

Provides time-accelerated campaign delivery simulation with webhook notifications.
Fires webhooks at configurable intervals to simulate real-world campaign delivery.

Example: 1 second = 1 hour of campaign time (configurable)

NOTE: Time acceleration is mock-adapter specific for testing. Webhook delivery
itself is a core feature (webhook_delivery_service) shared by all adapters.
"""

import atexit
import logging
import threading
from datetime import datetime, timedelta

from src.services.webhook_delivery_service import webhook_delivery_service

logger = logging.getLogger(__name__)


class DeliverySimulator:
    """Simulates accelerated campaign delivery with webhook notifications.

    Thread-safe simulator that uses shared webhook_delivery_service for webhook sending.
    This class handles time acceleration logic (mock-specific), while webhook delivery
    is a core feature shared by all adapters.
    """

    def __init__(self):
        """Initialize the delivery simulator."""
        self._active_simulations: dict[str, threading.Thread] = {}
        self._stop_signals: dict[str, threading.Event] = {}
        self._lock = threading.Lock()  # Protect shared state

        # Register graceful shutdown
        atexit.register(self._shutdown)

    def restart_active_simulations(self):
        """Restart delivery simulations for active media buys.

        Called on server startup to resume simulations that were running
        before a server restart. Daemon threads don't survive restarts.
        """
        try:

            from sqlalchemy import select

            from src.core.database.database_session import get_db_session
            from src.core.database.models import MediaBuy, Product, PushNotificationConfig

            logger.info("🔄 Checking for active media buys to restart simulations...")

            with get_db_session() as session:
                # Find active media buys with webhook configs
                # Include pending status because simulators start immediately on creation
                stmt = (
                    select(MediaBuy, PushNotificationConfig)
                    .join(PushNotificationConfig, MediaBuy.media_buy_id == PushNotificationConfig.media_buy_id)
                    .where(MediaBuy.status.in_(["pending", "active", "working"]))
                    .where(PushNotificationConfig.enabled == True)  # noqa: E712
                )

                results = session.execute(stmt).all()

                restarted_count = 0
                for media_buy, _webhook_config in results:
                    # Check if simulation is already running
                    if media_buy.media_buy_id in self._active_simulations:
                        continue

                    # Extract product IDs from the media buy packages
                    raw_request = media_buy.raw_request or {}
                    packages = raw_request.get("packages", [])
                    if not packages:
                        continue

                    # Get the first product (for simulation config)
                    first_package = packages[0]
                    product_id = first_package.get("product_id") or (first_package.get("products") or [None])[0]
                    if not product_id:
                        continue

                    # Look up the product
                    product_stmt = select(Product).where(
                        Product.tenant_id == media_buy.tenant_id, Product.product_id == product_id
                    )
                    product = session.scalars(product_stmt).first()
                    if not product:
                        continue

                    # Only restart for mock adapter products
                    if product.adapter_type != "mock":
                        continue

                    # Get simulation config from product
                    impl_config = product.implementation_config or {}
                    delivery_sim_config = impl_config.get("delivery_simulation", {})

                    # Skip if simulation is disabled
                    if not delivery_sim_config.get("enabled", True):
                        continue

                    logger.info(
                        f"🚀 Restarting simulation for {media_buy.media_buy_id} " f"(product: {product.product_id})"
                    )

                    try:
                        self.start_simulation(
                            media_buy_id=media_buy.media_buy_id,
                            tenant_id=media_buy.tenant_id,
                            principal_id=media_buy.principal_id,
                            start_time=media_buy.start_time,
                            end_time=media_buy.end_time,
                            total_budget=float(media_buy.total_budget),
                            time_acceleration=delivery_sim_config.get("time_acceleration", 3600),
                            update_interval_seconds=delivery_sim_config.get("update_interval_seconds", 1.0),
                        )
                        restarted_count += 1
                    except Exception as e:
                        logger.error(f"Failed to restart simulation for {media_buy.media_buy_id}: {e}")

                if restarted_count > 0:
                    logger.info(f"✅ Restarted {restarted_count} delivery simulation(s)")
                else:
                    logger.info("✅ No active simulations to restart")

        except Exception as e:
            logger.error(f"⚠️ Failed to restart delivery simulations: {e}")
            # Don't crash the server if restart fails
            import traceback

            logger.error(f"Traceback: {traceback.format_exc()}")

    def start_simulation(
        self,
        media_buy_id: str,
        tenant_id: str,
        principal_id: str,
        start_time: datetime,
        end_time: datetime,
        total_budget: float,
        time_acceleration: int = 3600,  # 1 second = 1 hour (3600 seconds)
        update_interval_seconds: float = 1.0,  # Fire webhook every 1 second
    ):
        """Start delivery simulation for a media buy.

        Thread-safe operation.

        Args:
            media_buy_id: Media buy identifier
            tenant_id: Tenant identifier
            principal_id: Principal identifier
            start_time: Campaign start datetime
            end_time: Campaign end datetime
            total_budget: Total campaign budget
            time_acceleration: How many real seconds = 1 simulated second (default: 3600 = 1 sec = 1 hour)
            update_interval_seconds: How often to fire webhooks in real time (default: 1 second)
        """
        with self._lock:
            # Don't start if already running
            if media_buy_id in self._active_simulations:
                logger.warning(f"Delivery simulation already running for {media_buy_id}")
                return

            # Create stop signal
            stop_signal = threading.Event()
            self._stop_signals[media_buy_id] = stop_signal

            # Start simulation thread
            thread = threading.Thread(
                target=self._run_simulation,
                args=(
                    media_buy_id,
                    tenant_id,
                    principal_id,
                    start_time,
                    end_time,
                    total_budget,
                    time_acceleration,
                    update_interval_seconds,
                    stop_signal,
                ),
                daemon=True,
            )
            self._active_simulations[media_buy_id] = thread
            thread.start()

            logger.info(
                f"✅ Started delivery simulation for {media_buy_id} "
                f"(acceleration: {time_acceleration}x, interval: {update_interval_seconds}s)"
            )

    def stop_simulation(self, media_buy_id: str):
        """Stop delivery simulation for a media buy.

        Thread-safe operation.

        Args:
            media_buy_id: Media buy identifier
        """
        with self._lock:
            if media_buy_id in self._stop_signals:
                self._stop_signals[media_buy_id].set()
                logger.info(f"🛑 Stopping delivery simulation for {media_buy_id}")

    def _run_simulation(
        self,
        media_buy_id: str,
        tenant_id: str,
        principal_id: str,
        start_time: datetime,
        end_time: datetime,
        total_budget: float,
        time_acceleration: int,
        update_interval_seconds: float,
        stop_signal: threading.Event,
    ):
        """Run the delivery simulation (thread worker).

        Args:
            media_buy_id: Media buy identifier
            tenant_id: Tenant identifier
            principal_id: Principal identifier
            start_time: Campaign start datetime
            end_time: Campaign end datetime
            total_budget: Total campaign budget
            time_acceleration: Seconds of real time = 1 second of simulated time
            update_interval_seconds: How often to fire webhooks
            stop_signal: Event to signal simulation stop
        """
        try:
            # Calculate campaign duration
            campaign_duration = (end_time - start_time).total_seconds()  # seconds
            simulation_duration = campaign_duration / time_acceleration  # real seconds

            # Calculate update interval in simulated time
            simulated_interval = update_interval_seconds * time_acceleration  # simulated seconds per update

            logger.info(
                f"📊 Simulation parameters for {media_buy_id}:\n"
                f"   Campaign duration: {campaign_duration / 3600:.1f} hours\n"
                f"   Simulation duration: {simulation_duration:.1f} seconds\n"
                f"   Update interval: {update_interval_seconds}s real time = {simulated_interval / 3600:.1f}h simulated time"
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
                next_expected_interval_seconds=update_interval_seconds,
            )

            elapsed_real_seconds = 0.0

            while elapsed_real_seconds < simulation_duration and not stop_signal.is_set():
                # Wait for update interval
                stop_signal.wait(update_interval_seconds)

                if stop_signal.is_set():
                    break

                elapsed_real_seconds += update_interval_seconds

                # Calculate simulated progress
                elapsed_simulated_seconds = elapsed_real_seconds * time_acceleration
                progress_ratio = min(elapsed_simulated_seconds / campaign_duration, 1.0)

                # Calculate simulated time
                simulated_time = start_time + timedelta(seconds=elapsed_simulated_seconds)

                # Calculate delivery metrics (realistic simulation)
                # Use even pacing with some variance
                base_spend = total_budget * progress_ratio
                variance = 0.05  # 5% variance
                import random

                spend = base_spend * (1 + random.uniform(-variance, variance))
                spend = min(spend, total_budget)  # Cap at total budget

                # Calculate impressions (assume $10 CPM)
                impressions = int(spend / 0.01)

                # Calculate elapsed hours for webhook
                elapsed_hours = elapsed_simulated_seconds / 3600
                total_hours = campaign_duration / 3600

                # Determine status
                if progress_ratio >= 1.0:
                    status = "completed"
                else:
                    status = "delivering"

                # Send delivery update webhook
                webhook_delivery_service.send_delivery_webhook(
                    media_buy_id=media_buy_id,
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    reporting_period_start=start_time,
                    reporting_period_end=simulated_time,
                    impressions=impressions,
                    spend=spend,
                    status=status,
                    clicks=int(impressions * 0.01),
                    ctr=0.01,
                    is_final=(progress_ratio >= 1.0),
                    next_expected_interval_seconds=update_interval_seconds if progress_ratio < 1.0 else None,
                )

                # Stop if campaign completed
                if progress_ratio >= 1.0:
                    logger.info(f"🎉 Campaign {media_buy_id} simulation completed")
                    break

        except Exception as e:
            logger.error(f"❌ Error in delivery simulation for {media_buy_id}: {e}", exc_info=True)
        finally:
            # Thread-safe cleanup
            with self._lock:
                if media_buy_id in self._active_simulations:
                    del self._active_simulations[media_buy_id]
                if media_buy_id in self._stop_signals:
                    del self._stop_signals[media_buy_id]

            # Reset webhook sequence number
            webhook_delivery_service.reset_sequence(media_buy_id)

    def _shutdown(self):
        """Graceful shutdown handler."""
        logger.info("🛑 DeliverySimulator shutting down")
        with self._lock:
            # Signal all active simulations to stop
            for media_buy_id, stop_signal in self._stop_signals.items():
                stop_signal.set()
                logger.info(f"   Stopping simulation for {media_buy_id}")

            # Wait briefly for threads to finish
            import time

            time.sleep(0.5)


# Global singleton instance
delivery_simulator = DeliverySimulator()
