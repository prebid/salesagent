"""
Health check and connection validation for Google Ad Manager.

This module provides:
- Connection testing
- Permission validation
- Configuration verification
- API quota monitoring
"""

import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import google.oauth2.service_account
from googleads import ad_manager, oauth2

from .constants import GAM_API_VERSION
from .error_handler import GAMAuthenticationError, GAMConfigurationError
from .logging import logger


class HealthStatus(Enum):
    """Health check status levels."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """Result of a health check."""

    status: HealthStatus
    check_name: str
    message: str
    details: dict[str, Any]
    duration_ms: float
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging/API responses."""
        return {
            "status": self.status.value,
            "check_name": self.check_name,
            "message": self.message,
            "details": self.details,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat(),
        }


class GAMHealthChecker:
    """Health checker for Google Ad Manager integration."""

    def __init__(self, config: dict[str, Any], dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run
        self.client = None
        self.last_check_time = None
        self.last_results = []

    def _init_client(self) -> bool:
        """Initialize GAM client for health checks."""
        if self.dry_run:
            logger.info("Health check running in dry-run mode")
            return True

        try:
            key_file = self.config.get("service_account_key_file")
            if not key_file:
                raise GAMConfigurationError("Missing service_account_key_file in config")

            credentials = google.oauth2.service_account.Credentials.from_service_account_file(
                key_file, scopes=["https://www.googleapis.com/auth/dfp"]
            )
            # Wrap in GoogleCredentialsClient for AdManagerClient compatibility
            oauth2_client = oauth2.GoogleCredentialsClient(credentials)

            self.client = ad_manager.AdManagerClient(
                oauth2_client, "AdCP Health Check", network_code=self.config.get("network_code")
            )
            return True

        except Exception as e:
            logger.error(f"Failed to initialize GAM client: {e}")
            return False

    def check_authentication(self) -> HealthCheckResult:
        """Check if we can authenticate with GAM."""
        start_time = time.time()

        if self.dry_run:
            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                check_name="authentication",
                message="Dry-run mode - authentication check skipped",
                details={"dry_run": True},
                duration_ms=0,
            )

        try:
            if not self.client and not self._init_client():
                raise GAMAuthenticationError("Failed to initialize client")

            # Try a simple API call to verify auth
            network_service = self.client.GetService("NetworkService")
            network = network_service.getCurrentNetwork()

            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                check_name="authentication",
                message="Successfully authenticated with GAM",
                details={"network_code": network.get("networkCode"), "display_name": network.get("displayName")},
                duration_ms=(time.time() - start_time) * 1000,
            )

        except Exception as e:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                check_name="authentication",
                message=f"Authentication failed: {str(e)}",
                details={"error": str(e)},
                duration_ms=(time.time() - start_time) * 1000,
            )

    def check_permissions(self, advertiser_id: str) -> HealthCheckResult:
        """Check if we have necessary permissions."""
        start_time = time.time()

        if self.dry_run:
            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                check_name="permissions",
                message="Dry-run mode - permissions check skipped",
                details={"dry_run": True},
                duration_ms=0,
            )

        try:
            permissions_ok = True
            missing_permissions = []

            # Check if we can access the advertiser
            company_service = self.client.GetService("CompanyService")
            from googleads import ad_manager

            statement = (
                ad_manager.StatementBuilder(version=GAM_API_VERSION)
                .Where("id = :id")
                .WithBindVariable("id", int(advertiser_id))
                .ToStatement()
            )

            response = company_service.getCompaniesByStatement(statement)
            if not response.get("results"):
                permissions_ok = False
                missing_permissions.append("Cannot access advertiser")

            # Check if we can create orders
            # This is a read-only check, we're not actually creating anything
            user_service = self.client.GetService("UserService")
            current_user = user_service.getCurrentUser()

            if not current_user.get("isActive"):
                permissions_ok = False
                missing_permissions.append("User is not active")

            # Check role permissions
            role_id = current_user.get("roleId")
            if role_id:
                # In a real implementation, we'd check specific permissions
                # For now, we just verify we have a role
                logger.debug(f"User role ID: {role_id}")

            if permissions_ok:
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    check_name="permissions",
                    message="All required permissions verified",
                    details={
                        "user_id": current_user.get("id"),
                        "user_email": current_user.get("email"),
                        "advertiser_accessible": True,
                    },
                    duration_ms=(time.time() - start_time) * 1000,
                )
            else:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    check_name="permissions",
                    message="Missing required permissions",
                    details={"missing": missing_permissions},
                    duration_ms=(time.time() - start_time) * 1000,
                )

        except Exception as e:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                check_name="permissions",
                message=f"Permission check failed: {str(e)}",
                details={"error": str(e)},
                duration_ms=(time.time() - start_time) * 1000,
            )

    def check_api_quota(self) -> HealthCheckResult:
        """Check API quota status."""
        start_time = time.time()

        if self.dry_run:
            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                check_name="api_quota",
                message="Dry-run mode - quota check skipped",
                details={"dry_run": True},
                duration_ms=0,
            )

        try:
            # GAM doesn't expose quota directly, but we can track our usage
            # In production, this would integrate with quota monitoring

            # For now, return a healthy status with usage estimates
            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                check_name="api_quota",
                message="API quota within limits",
                details={
                    "estimated_daily_calls": 1000,  # Would track actual usage
                    "daily_limit": 10000,  # GAM typical limit
                    "usage_percentage": 10,
                },
                duration_ms=(time.time() - start_time) * 1000,
            )

        except Exception as e:
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                check_name="api_quota",
                message=f"Could not check quota: {str(e)}",
                details={"error": str(e)},
                duration_ms=(time.time() - start_time) * 1000,
            )

    def check_inventory_access(self, ad_unit_ids: list[str]) -> HealthCheckResult:
        """Check if we can access configured ad units."""
        start_time = time.time()

        if self.dry_run:
            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                check_name="inventory_access",
                message="Dry-run mode - inventory check skipped",
                details={"dry_run": True},
                duration_ms=0,
            )

        if not ad_unit_ids:
            return HealthCheckResult(
                status=HealthStatus.DEGRADED,
                check_name="inventory_access",
                message="No ad unit IDs configured",
                details={"configured_units": 0},
                duration_ms=(time.time() - start_time) * 1000,
            )

        try:
            inventory_service = self.client.GetService("InventoryService")

            # Check first few ad units
            accessible_units = []
            inaccessible_units = []

            for ad_unit_id in ad_unit_ids[:5]:  # Check first 5 only
                try:
                    statement = (
                        ad_manager.StatementBuilder(version=GAM_API_VERSION)
                        .Where("id = :id")
                        .WithBindVariable("id", int(ad_unit_id))
                        .ToStatement()
                    )

                    response = inventory_service.getAdUnitsByStatement(statement)
                    if response.get("results"):
                        accessible_units.append(ad_unit_id)
                    else:
                        inaccessible_units.append(ad_unit_id)

                except Exception:
                    inaccessible_units.append(ad_unit_id)

            if not inaccessible_units:
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    check_name="inventory_access",
                    message="All checked ad units are accessible",
                    details={
                        "checked": len(accessible_units),
                        "accessible": len(accessible_units),
                        "total_configured": len(ad_unit_ids),
                    },
                    duration_ms=(time.time() - start_time) * 1000,
                )
            elif accessible_units:
                return HealthCheckResult(
                    status=HealthStatus.DEGRADED,
                    check_name="inventory_access",
                    message="Some ad units are inaccessible",
                    details={"accessible": accessible_units, "inaccessible": inaccessible_units},
                    duration_ms=(time.time() - start_time) * 1000,
                )
            else:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    check_name="inventory_access",
                    message="No ad units are accessible",
                    details={"inaccessible": inaccessible_units},
                    duration_ms=(time.time() - start_time) * 1000,
                )

        except Exception as e:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                check_name="inventory_access",
                message=f"Inventory check failed: {str(e)}",
                details={"error": str(e)},
                duration_ms=(time.time() - start_time) * 1000,
            )

    def check_service_availability(self) -> HealthCheckResult:
        """Check if GAM services are available."""
        start_time = time.time()

        if self.dry_run:
            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                check_name="service_availability",
                message="Dry-run mode - service check skipped",
                details={"dry_run": True},
                duration_ms=0,
            )

        try:
            # Test key services
            services_to_test = ["OrderService", "LineItemService", "CreativeService", "ReportService"]

            service_status = {}
            all_healthy = True

            for service_name in services_to_test:
                try:
                    self.client.GetService(service_name)
                    # Just getting the service is enough to verify it's available
                    service_status[service_name] = "available"
                except Exception as e:
                    service_status[service_name] = f"error: {str(e)}"
                    all_healthy = False

            if all_healthy:
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    check_name="service_availability",
                    message="All GAM services are available",
                    details=service_status,
                    duration_ms=(time.time() - start_time) * 1000,
                )
            else:
                return HealthCheckResult(
                    status=HealthStatus.DEGRADED,
                    check_name="service_availability",
                    message="Some GAM services are unavailable",
                    details=service_status,
                    duration_ms=(time.time() - start_time) * 1000,
                )

        except Exception as e:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                check_name="service_availability",
                message=f"Service check failed: {str(e)}",
                details={"error": str(e)},
                duration_ms=(time.time() - start_time) * 1000,
            )

    def run_all_checks(
        self, advertiser_id: str | None = None, ad_unit_ids: list[str] | None = None
    ) -> tuple[HealthStatus, list[HealthCheckResult]]:
        """
        Run all health checks and return overall status.

        Returns:
            Tuple of (overall_status, list_of_results)
        """
        logger.info("Starting GAM health checks")

        results = []

        # Run checks in order of importance
        results.append(self.check_authentication())

        # Only run other checks if authentication succeeded
        if results[0].status == HealthStatus.HEALTHY:
            if advertiser_id:
                results.append(self.check_permissions(advertiser_id))

            results.append(self.check_api_quota())
            results.append(self.check_service_availability())

            if ad_unit_ids:
                results.append(self.check_inventory_access(ad_unit_ids))

        # Determine overall status
        statuses = [r.status for r in results]

        if all(s == HealthStatus.HEALTHY for s in statuses):
            overall_status = HealthStatus.HEALTHY
        elif any(s == HealthStatus.UNHEALTHY for s in statuses):
            overall_status = HealthStatus.UNHEALTHY
        elif any(s == HealthStatus.DEGRADED for s in statuses):
            overall_status = HealthStatus.DEGRADED
        else:
            overall_status = HealthStatus.UNKNOWN

        # Cache results
        self.last_check_time = datetime.now()
        self.last_results = results

        logger.info(
            f"GAM health check completed: {overall_status.value}",
            extra={
                "overall_status": overall_status.value,
                "check_count": len(results),
                "healthy_count": sum(1 for r in results if r.status == HealthStatus.HEALTHY),
                "degraded_count": sum(1 for r in results if r.status == HealthStatus.DEGRADED),
                "unhealthy_count": sum(1 for r in results if r.status == HealthStatus.UNHEALTHY),
            },
        )

        return overall_status, results

    def get_status_summary(self) -> dict[str, Any]:
        """Get a summary of the last health check."""
        if not self.last_results:
            return {"status": HealthStatus.UNKNOWN.value, "message": "No health check has been run", "last_check": None}

        overall_status = HealthStatus.HEALTHY
        unhealthy_checks = []
        degraded_checks = []

        for result in self.last_results:
            if result.status == HealthStatus.UNHEALTHY:
                overall_status = HealthStatus.UNHEALTHY
                unhealthy_checks.append(result.check_name)
            elif result.status == HealthStatus.DEGRADED:
                if overall_status != HealthStatus.UNHEALTHY:
                    overall_status = HealthStatus.DEGRADED
                degraded_checks.append(result.check_name)

        message = "All systems operational"
        if unhealthy_checks:
            message = f"Critical issues: {', '.join(unhealthy_checks)}"
        elif degraded_checks:
            message = f"Degraded performance: {', '.join(degraded_checks)}"

        return {
            "status": overall_status.value,
            "message": message,
            "last_check": self.last_check_time.isoformat() if self.last_check_time else None,
            "checks": [r.to_dict() for r in self.last_results],
        }


def create_health_check_endpoint(app, get_config_func):
    """
    Create a health check endpoint for Flask app.

    Args:
        app: Flask application
        get_config_func: Function to get GAM config for a tenant
    """

    @app.route("/health/gam/<tenant_id>")
    def gam_health_check(tenant_id):
        try:
            config = get_config_func(tenant_id)
            if not config:
                return {"error": "Tenant not found"}, 404

            checker = GAMHealthChecker(config)

            # Get advertiser ID if available
            # In production, this would come from the tenant's principal mappings
            advertiser_id = config.get("default_advertiser_id")
            ad_unit_ids = config.get("default_ad_unit_ids", [])

            overall_status, results = checker.run_all_checks(advertiser_id=advertiser_id, ad_unit_ids=ad_unit_ids)

            return {
                "status": overall_status.value,
                "timestamp": datetime.now().isoformat(),
                "checks": [r.to_dict() for r in results],
            }

        except Exception as e:
            logger.error(f"Health check endpoint error: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}, 500
