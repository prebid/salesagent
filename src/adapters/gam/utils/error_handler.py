"""
Enhanced error handling for Google Ad Manager adapter.

This module provides production-ready error handling including:
- Structured exception hierarchy
- Retry logic with exponential backoff
- Comprehensive error mapping
- Recovery strategies
"""

import logging
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from functools import wraps
from typing import Any, TypeVar

# Configure logging
logger = logging.getLogger(__name__)

T = TypeVar("T")


class GAMErrorType(Enum):
    """Categorized error types for GAM operations."""

    AUTHENTICATION = "authentication_error"
    PERMISSION = "permission_error"
    VALIDATION = "validation_error"
    QUOTA_EXCEEDED = "quota_exceeded"
    NETWORK = "network_error"
    TIMEOUT = "timeout_error"
    RESOURCE_NOT_FOUND = "resource_not_found"
    DUPLICATE_RESOURCE = "duplicate_resource"
    INTERNAL_ERROR = "internal_error"
    CONFIGURATION = "configuration_error"
    UNKNOWN = "unknown_error"


class GAMError(Exception):
    """Base exception for all GAM adapter errors."""

    def __init__(
        self,
        message: str,
        error_type: GAMErrorType = GAMErrorType.UNKNOWN,
        details: dict[str, Any] | None = None,
        recoverable: bool = True,
    ):
        super().__init__(message)
        self.error_type = error_type
        self.details = details or {}
        self.recoverable = recoverable
        self.timestamp = datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        """Convert error to dictionary for logging/monitoring."""
        return {
            "error_type": self.error_type.value,
            "message": str(self),
            "details": self.details,
            "recoverable": self.recoverable,
            "timestamp": self.timestamp.isoformat(),
        }


class GAMAuthenticationError(GAMError):
    """Raised when authentication with GAM fails."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, GAMErrorType.AUTHENTICATION, details, recoverable=False)


class GAMPermissionError(GAMError):
    """Raised when operation is not permitted."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, GAMErrorType.PERMISSION, details, recoverable=False)


class GAMValidationError(GAMError):
    """Raised when request validation fails."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, GAMErrorType.VALIDATION, details, recoverable=False)


class GAMQuotaError(GAMError):
    """Raised when API quota is exceeded."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, GAMErrorType.QUOTA_EXCEEDED, details, recoverable=True)


class GAMNetworkError(GAMError):
    """Raised for network-related issues."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, GAMErrorType.NETWORK, details, recoverable=True)


class GAMTimeoutError(GAMError):
    """Raised when operation times out."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, GAMErrorType.TIMEOUT, details, recoverable=True)


class GAMResourceNotFoundError(GAMError):
    """Raised when requested resource doesn't exist."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, GAMErrorType.RESOURCE_NOT_FOUND, details, recoverable=False)


class GAMDuplicateResourceError(GAMError):
    """Raised when trying to create duplicate resource."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, GAMErrorType.DUPLICATE_RESOURCE, details, recoverable=False)


class GAMConfigurationError(GAMError):
    """Raised for configuration issues."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, GAMErrorType.CONFIGURATION, details, recoverable=False)


class RetryConfig:
    """Configuration for retry behavior."""

    def __init__(
        self,
        max_attempts: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
    ):
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter


def map_gam_exception(exception: Exception) -> GAMError:
    """
    Map GAM API exceptions to our structured error types.

    Args:
        exception: The original exception from GAM API

    Returns:
        Appropriate GAMError subclass
    """
    error_message = str(exception)
    error_details = {"original_type": type(exception).__name__, "traceback": traceback.format_exc()}

    # Map based on exception type and message patterns
    if "AuthError" in type(exception).__name__ or "authentication" in error_message.lower():
        return GAMAuthenticationError(f"GAM authentication failed: {error_message}", error_details)

    elif "PermissionError" in type(exception).__name__ or "permission" in error_message.lower():
        return GAMPermissionError(f"GAM permission denied: {error_message}", error_details)

    elif "ValidationError" in type(exception).__name__ or "invalid" in error_message.lower():
        return GAMValidationError(f"GAM validation failed: {error_message}", error_details)

    elif "QuotaError" in type(exception).__name__ or "quota" in error_message.lower():
        return GAMQuotaError(f"GAM quota exceeded: {error_message}", error_details)

    elif "NetworkError" in type(exception).__name__ or "network" in error_message.lower():
        return GAMNetworkError(f"GAM network error: {error_message}", error_details)

    elif "TimeoutError" in type(exception).__name__ or "timeout" in error_message.lower():
        return GAMTimeoutError(f"GAM operation timed out: {error_message}", error_details)

    elif "NotFoundError" in type(exception).__name__ or "not found" in error_message.lower():
        return GAMResourceNotFoundError(f"GAM resource not found: {error_message}", error_details)

    elif "DuplicateError" in type(exception).__name__ or "already exists" in error_message.lower():
        return GAMDuplicateResourceError(f"GAM resource already exists: {error_message}", error_details)

    else:
        # Default to unknown error
        return GAMError(f"GAM error: {error_message}", GAMErrorType.UNKNOWN, error_details)


def with_retry(
    retry_config: RetryConfig | None = None,
    retry_on: list[type] | None = None,
    operation_name: str | None = None,
) -> Callable:
    """
    Decorator for adding retry logic to GAM operations.

    Args:
        retry_config: Configuration for retry behavior
        retry_on: List of exception types to retry on
        operation_name: Name of operation for logging

    Returns:
        Decorated function with retry logic
    """
    if retry_config is None:
        retry_config = RetryConfig()

    if retry_on is None:
        retry_on = [GAMNetworkError, GAMTimeoutError, GAMQuotaError]

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception: GAMError | None = None
            op_name = operation_name or func.__name__

            for attempt in range(retry_config.max_attempts):
                try:
                    # Log attempt
                    if attempt > 0:
                        logger.info(f"Retrying {op_name} (attempt {attempt + 1}/{retry_config.max_attempts})")

                    # Execute function
                    result = func(*args, **kwargs)

                    # Success - log if it was a retry
                    if attempt > 0:
                        logger.info(f"{op_name} succeeded after {attempt + 1} attempts")

                    return result

                except Exception as e:
                    # Map to GAM error
                    gam_error = map_gam_exception(e) if not isinstance(e, GAMError) else e
                    last_exception = gam_error

                    # Check if we should retry
                    should_retry = (
                        gam_error.recoverable
                        and any(isinstance(gam_error, exc_type) for exc_type in retry_on)
                        and attempt < retry_config.max_attempts - 1
                    )

                    if should_retry:
                        # Calculate delay with exponential backoff
                        delay = min(
                            retry_config.initial_delay * (retry_config.exponential_base**attempt),
                            retry_config.max_delay,
                        )

                        # Add jitter if configured
                        if retry_config.jitter:
                            import random

                            delay = delay * (0.5 + random.random())

                        logger.warning(
                            f"{op_name} failed with {gam_error.error_type.value}: {str(gam_error)}. "
                            f"Retrying in {delay:.1f} seconds..."
                        )

                        time.sleep(delay)
                    else:
                        # Don't retry - log and raise
                        error_dict = gam_error.to_dict()
                        # Remove 'message' key to avoid conflict with logging system
                        error_dict.pop("message", None)
                        logger.error(
                            f"{op_name} failed with {gam_error.error_type.value}: {str(gam_error)}", extra=error_dict
                        )
                        raise gam_error

            # All retries exhausted
            if last_exception is None:
                # This should never happen, but handle it gracefully
                raise RuntimeError(
                    f"{op_name} failed after {retry_config.max_attempts} attempts with no exception recorded"
                )

            error_dict = last_exception.to_dict()
            # Remove 'message' key to avoid conflict with logging system
            error_dict.pop("message", None)
            logger.error(f"{op_name} failed after {retry_config.max_attempts} attempts", extra=error_dict)
            raise last_exception

        return wrapper

    return decorator


class GAMOperationTracker:
    """
    Track multi-step GAM operations for rollback support.
    """

    def __init__(self, operation_id: str):
        self.operation_id = operation_id
        self.steps: list[dict[str, Any]] = []
        self.start_time = datetime.now(UTC)

    def add_step(
        self,
        step_name: str,
        resource_type: str,
        resource_id: str,
        rollback_action: Callable | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Add a completed step to the operation."""
        self.steps.append(
            {
                "step_name": step_name,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "rollback_action": rollback_action,
                "metadata": metadata or {},
                "timestamp": datetime.now(UTC),
            }
        )

    def rollback(self) -> list[dict[str, Any]]:
        """
        Execute rollback actions in reverse order.

        Returns:
            List of rollback results
        """
        rollback_results = []

        for step in reversed(self.steps):
            if step["rollback_action"]:
                try:
                    logger.info(f"Rolling back {step['step_name']} for {step['resource_type']} {step['resource_id']}")

                    result = step["rollback_action"]()
                    rollback_results.append({"step": step["step_name"], "success": True, "result": result})

                except Exception as e:
                    logger.error(f"Rollback failed for {step['step_name']}: {str(e)}")
                    rollback_results.append({"step": step["step_name"], "success": False, "error": str(e)})

        return rollback_results

    def to_dict(self) -> dict[str, Any]:
        """Convert operation to dictionary for logging."""
        return {
            "operation_id": self.operation_id,
            "start_time": self.start_time.isoformat(),
            "duration": (datetime.now(UTC) - self.start_time).total_seconds(),
            "steps": [
                {
                    "name": step["step_name"],
                    "resource": f"{step['resource_type']}:{step['resource_id']}",
                    "timestamp": step["timestamp"].isoformat(),
                }
                for step in self.steps
            ],
        }


def validate_gam_response(response: Any, expected_fields: list[str]) -> None:
    """
    Validate GAM API response has expected structure.

    Args:
        response: The API response
        expected_fields: List of field names that should be present

    Raises:
        GAMValidationError: If response is invalid
    """
    if not response:
        raise GAMValidationError("Empty response from GAM API")

    missing_fields = []
    for field in expected_fields:
        if field not in response:
            missing_fields.append(field)

    if missing_fields:
        raise GAMValidationError(
            f"GAM response missing required fields: {', '.join(missing_fields)}",
            {"response": str(response)[:500]},  # Truncate for logging
        )
