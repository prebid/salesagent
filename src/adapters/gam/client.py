"""
Google Ad Manager Client Manager

Handles GAM API client initialization, management, and service access.
Provides centralized access to GAM API services with health checking.
"""

import logging
from typing import Any

from googleads import ad_manager

from .auth import GAMAuthManager
from .utils.health_check import GAMHealthChecker, HealthCheckResult, HealthStatus

logger = logging.getLogger(__name__)


class GAMClientManager:
    """Manages GAM API client and service access."""

    def __init__(self, config: dict[str, Any], network_code: str):
        """Initialize client manager.

        Args:
            config: Authentication and client configuration
            network_code: GAM network code
        """
        self.config = config
        self.network_code = network_code
        self.auth_manager = GAMAuthManager(config)
        self._client: ad_manager.AdManagerClient | None = None
        self._health_checker: GAMHealthChecker | None = None

    def get_client(self) -> ad_manager.AdManagerClient:
        """Get or create the GAM API client.

        Returns:
            Initialized AdManagerClient instance

        Raises:
            ValueError: If network code is missing
            Exception: If client initialization fails
        """
        if self._client is None:
            self._client = self._init_client()
        return self._client

    def _init_client(self) -> ad_manager.AdManagerClient:
        """Initialize the Ad Manager client.

        Returns:
            Initialized AdManagerClient

        Raises:
            ValueError: If configuration is invalid
            Exception: If client creation fails
        """
        if not self.network_code:
            raise ValueError("Network code is required for GAM client initialization")

        try:
            # Get credentials from auth manager
            credentials = self.auth_manager.get_credentials()

            # Create AdManager client
            ad_manager_client = ad_manager.AdManagerClient(
                credentials, "AdCP Sales Agent", network_code=self.network_code
            )

            logger.info(
                f"GAM client initialized for network {self.network_code} using {self.auth_manager.get_auth_method()}"
            )
            return ad_manager_client

        except Exception as e:
            logger.error(f"Error initializing GAM client: {e}")
            raise

    def get_service(self, service_name: str):
        """Get a specific GAM API service.

        Args:
            service_name: Name of the service (e.g., 'OrderService', 'LineItemService')

        Returns:
            GAM service instance
        """
        client = self.get_client()
        return client.GetService(service_name, version="v202411")

    def get_statement_builder(self):
        """Get a StatementBuilder for GAM API queries.

        Returns:
            StatementBuilder instance
        """
        return ad_manager.StatementBuilder()

    def is_connected(self) -> bool:
        """Check if client is connected and working.

        Returns:
            True if client is connected, False otherwise
        """
        try:
            client = self.get_client()
            # Simple test call - get network info
            network_service = client.GetService("NetworkService", version="v202411")
            network_service.getCurrentNetwork()
            return True
        except Exception as e:
            logger.warning(f"GAM client connection test failed: {e}")
            return False

    def reset_client(self) -> None:
        """Reset the client connection (force re-initialization on next access)."""
        self._client = None
        logger.info("GAM client reset - will re-initialize on next access")

    def get_health_checker(self, dry_run: bool = False) -> GAMHealthChecker:
        """Get or create the health checker.

        Args:
            dry_run: Whether to run in dry-run mode

        Returns:
            GAMHealthChecker instance
        """
        if self._health_checker is None:
            self._health_checker = GAMHealthChecker(self.config, dry_run=dry_run)
        return self._health_checker

    def check_health(
        self, advertiser_id: str | None = None, ad_unit_ids: list[str] | None = None
    ) -> tuple[HealthStatus, list[HealthCheckResult]]:
        """Run health checks for this GAM connection.

        Args:
            advertiser_id: Optional advertiser ID to check permissions for
            ad_unit_ids: Optional ad unit IDs to check access for

        Returns:
            Tuple of (overall_status, list_of_results)
        """
        health_checker = self.get_health_checker()
        return health_checker.run_all_checks(advertiser_id=advertiser_id, ad_unit_ids=ad_unit_ids)

    def get_health_status(self) -> dict[str, Any]:
        """Get a summary of the last health check.

        Returns:
            Health status summary dictionary
        """
        health_checker = self.get_health_checker()
        return health_checker.get_status_summary()

    def test_connection(self) -> HealthCheckResult:
        """Test basic connection and authentication.

        Returns:
            HealthCheckResult for the connection test
        """
        health_checker = self.get_health_checker()
        return health_checker.check_authentication()

    def test_permissions(self, advertiser_id: str) -> HealthCheckResult:
        """Test permissions for a specific advertiser.

        Args:
            advertiser_id: Advertiser ID to test permissions for

        Returns:
            HealthCheckResult for the permissions test
        """
        health_checker = self.get_health_checker()
        return health_checker.check_permissions(advertiser_id)

    @classmethod
    def from_existing_client(cls, client: ad_manager.AdManagerClient) -> "GAMClientManager":
        """Create a GAMClientManager from an existing client instance.

        This is useful when integrating with existing code that already has
        an initialized GAM client.

        Args:
            client: Existing AdManagerClient instance

        Returns:
            GAMClientManager instance wrapping the existing client
        """
        # Create a minimal config since we have the client already
        config = {"existing_client": True}
        network_code = getattr(client, "network_code", "unknown")

        # Create instance
        manager = cls.__new__(cls)
        manager.config = config
        manager.network_code = network_code
        manager.auth_manager = None  # Not needed since client exists
        manager._client = client
        manager._health_checker = None

        logger.info(f"Created GAMClientManager from existing client (network: {network_code})")
        return manager
