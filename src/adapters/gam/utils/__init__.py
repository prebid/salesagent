"""
GAM Utilities Module

This module provides utilities for Google Ad Manager operations including:
- Validation utilities for creative and targeting validation
- Error handling for GAM-specific exceptions and retry logic
- Logging utilities for structured logging and audit trails
- Health checking for connection and permission validation
- Formatters for currency, date, and data formatting
- Constants for GAM enums and configuration values
"""

# Import all utility classes and functions for easy access
from .constants import (
    GAM_ALLOWED_EXTENSIONS,
    GAM_CREATIVE_SIZE_LIMITS,
    GAM_MAX_DIMENSIONS,
    GAM_SCOPES,
    GAMCreativeType,
    GAMLineItemStatus,
    GAMOrderStatus,
    GAMTargetingType,
)
from .error_handler import (
    GAMAuthenticationError,
    GAMConfigurationError,
    GAMDuplicateResourceError,
    GAMError,
    GAMErrorType,
    GAMNetworkError,
    GAMOperationTracker,
    GAMPermissionError,
    GAMQuotaError,
    GAMResourceNotFoundError,
    GAMTimeoutError,
    RetryConfig,
    map_gam_exception,
    validate_gam_response,
    with_retry,
)
from .error_handler import (
    GAMValidationError as GAMErrorValidationError,
)
from .formatters import (
    format_currency,
    format_date_for_gam,
    format_file_size,
    format_targeting_for_display,
    sanitize_for_logging,
)
from .health_check import (
    GAMHealthChecker,
    HealthCheckResult,
    HealthStatus,
    create_health_check_endpoint,
)
from .logging import (
    GAMLogContext,
    GAMMetrics,
    GAMOperation,
    log_api_call,
    log_configuration,
    log_dry_run,
    log_gam_operation,
    log_validation_error,
)
from .macros import (
    ADCP_TO_GAM_MACRO_MAP,
    substitute_macros,
    substitute_tracking_urls,
)
from .validation import GAMValidationError, GAMValidator, validate_gam_creative

__all__ = [
    # Validation
    "GAMValidator",
    "GAMValidationError",
    "validate_gam_creative",
    # Error handling
    "GAMError",
    "GAMErrorType",
    "GAMAuthenticationError",
    "GAMPermissionError",
    "GAMErrorValidationError",
    "GAMQuotaError",
    "GAMNetworkError",
    "GAMTimeoutError",
    "GAMResourceNotFoundError",
    "GAMDuplicateResourceError",
    "GAMConfigurationError",
    "RetryConfig",
    "GAMOperationTracker",
    "map_gam_exception",
    "with_retry",
    "validate_gam_response",
    # Logging
    "GAMOperation",
    "GAMLogContext",
    "GAMMetrics",
    "log_gam_operation",
    "log_api_call",
    "log_dry_run",
    "log_validation_error",
    "log_configuration",
    # Health checking
    "HealthStatus",
    "HealthCheckResult",
    "GAMHealthChecker",
    "create_health_check_endpoint",
    # Formatters
    "format_currency",
    "format_date_for_gam",
    "format_targeting_for_display",
    "format_file_size",
    "sanitize_for_logging",
    # Constants
    "GAMCreativeType",
    "GAMOrderStatus",
    "GAMLineItemStatus",
    "GAMTargetingType",
    "GAM_SCOPES",
    "GAM_CREATIVE_SIZE_LIMITS",
    "GAM_MAX_DIMENSIONS",
    "GAM_ALLOWED_EXTENSIONS",
    # Macros
    "ADCP_TO_GAM_MACRO_MAP",
    "substitute_macros",
    "substitute_tracking_urls",
]
