"""
Google Ad Manager Client Manager

Handles GAM API client initialization, management, and service access.
Provides centralized access to GAM API services with health checking.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import zeep.exceptions
from google.auth.exceptions import GoogleAuthError
from googleads import ad_manager
from googleads.errors import GoogleAdsError

from src.core.exceptions import AdCPConfigurationError
from src.core.operator_errors import UpstreamFailure, safe_error_message

from .auth import GAMAuthManager
from .utils.error_handler import map_gam_exception
from .utils.health_check import GAMHealthChecker, HealthCheckResult, HealthStatus

logger = logging.getLogger(__name__)

#: What a googleads SOAP service call raises when GAM, or the way to it, fails: the
#: client turns a SOAP fault into ``GoogleAdsServerFault`` (a ``GoogleAdsError``), zeep
#: raises its own ``Error`` for a reply that is not SOAP, google-auth raises
#: ``GoogleAuthError`` when the credentials cannot be refreshed, and the transport's
#: ``requests.RequestException`` is an ``OSError`` (``requests`` itself is banned in
#: ``src/`` by ruff-egress.toml, so it is not named here). Private: a caller outside
#: this module catches :class:`GAMCallFailed`.
_GAM_CALL_ERRORS: tuple[type[Exception], ...] = (GoogleAdsError, zeep.exceptions.Error, GoogleAuthError, OSError)


class GAMCallFailed(UpstreamFailure):
    """A GAM call made for an operator failed: GAM refused it, or GAM could not be reached.

    Raised in place of the vendor's exception. Its one detail is the sentence the fault
    was logged under, which ends with the reference that finds it in the log.
    """

    MESSAGE = "The call to Google Ad Manager failed"


@contextmanager
def _operator_call(call: str) -> Iterator[None]:
    """Run the GAM *call* for an operator: a vendor failure is logged once and raised as :class:`GAMCallFailed`."""
    try:
        yield
    except _GAM_CALL_ERRORS as fault:
        context = f"GAM {call} failed"
        raise GAMCallFailed(context, details=(safe_error_message(fault, context),)) from fault


def safe_get(obj, key, default=None):
    """Get value from dict or zeep SOAP object.

    GAM API returns zeep objects which support bracket notation (obj["key"])
    but not .get() method. This helper handles both dict and zeep objects.
    """
    if hasattr(obj, "get"):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _network_summary(network) -> dict[str, Any]:
    """The fields of a GAM ``Network`` the connection test reports (GAM's camelCase names)."""
    return {
        "id": network["id"],
        "displayName": network["displayName"],
        "networkCode": network["networkCode"],
        "currencyCode": safe_get(network, "currencyCode", "USD"),
        "secondaryCurrencyCodes": safe_get(network, "secondaryCurrencyCodes") or [],
        "timeZone": safe_get(network, "timeZone"),
    }


def accessible_networks(client: ad_manager.AdManagerClient, auth_method: str) -> list[dict[str, Any]]:
    """The networks *client*'s credentials reach; raises :class:`GAMCallFailed` when GAM fails.

    A service account has its network code set already, so it asks for the current network.
    OAuth asks for all networks, and falls back to the current one when this API version
    has no ``getAllNetworks``.
    """
    with _operator_call("network lookup"):
        network_service = client.GetService("NetworkService")
        if auth_method != "service_account":
            try:
                all_networks = network_service.getAllNetworks()
            except AttributeError:
                logger.info("getAllNetworks not available, falling back to getCurrentNetwork")
            else:
                logger.info(f"getAllNetworks() returned {len(all_networks or [])} networks")
                return [_network_summary(network) for network in all_networks or []]
        current_network = network_service.getCurrentNetwork()
        logger.info(f"getCurrentNetwork() returned: {current_network}")
        return [_network_summary(current_network)]


def current_network_user(client: ad_manager.AdManagerClient) -> Any:
    """The GAM user *client*'s credentials act as, on its network; raises :class:`GAMCallFailed` when GAM fails."""
    with _operator_call("current user lookup"):
        return client.GetService("UserService").getCurrentUser()


def current_network_time_zone(client: ad_manager.AdManagerClient) -> str:
    """The network's own time zone, the zone a line item's flight is written in.

    ``Network.timeZone`` is read-only in GAM: the zone the network delivers orders and
    reports in. A refused lookup is classified like any other GAM fault.
    """
    try:
        return client.GetService("NetworkService").getCurrentNetwork().timeZone
    except Exception as fault:
        raise map_gam_exception(fault) from fault


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
            raise AdCPConfigurationError()

        try:
            # Get credentials from auth manager
            credentials = self.auth_manager.get_credentials()

            # Create AdManager client
            ad_manager_client = ad_manager.AdManagerClient(
                credentials, "Prebid Sales Agent", network_code=self.network_code
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
        return client.GetService(service_name)

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
            network_service = client.GetService("NetworkService")
            network_service.getCurrentNetwork()
            return True
        except Exception as e:
            logger.warning(f"GAM client connection test failed: {e}")
            return False

    def reset_client(self) -> None:
        """Reset the client connection (force re-initialization on next access)."""
        self._client = None
        logger.info("GAM client reset - will re-initialize on next access")

    def get_health_checker(self) -> GAMHealthChecker:
        """Get or create the health checker.

        Returns:
            GAMHealthChecker instance
        """
        if self._health_checker is None:
            # Merge network_code into config for health checker's own client init
            health_config = {**self.config, "network_code": self.network_code}
            self._health_checker = GAMHealthChecker(health_config)
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
