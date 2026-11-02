"""
Enhanced error handling for Google Ad Manager adapter.

This module classifies GAM API faults and retries the ones worth retrying.

It defines NO exception classes. A GAM fault is an AdCP error like any other:
``map_gam_exception`` sorts an upstream fault into the ``AdCP*Error`` the buyer
should receive, which is what AdCP 3.1.1 mandates --
``building/operating/transport-errors.mdx`` Rule 1: "Translate upstream errors
into AdCP error codes. Do not pass through raw upstream errors ... The buyer
should never see error formats from systems it has no relationship with."

A previous version defined ``GAMError`` plus nine subclasses that mirrored the
AdCP hierarchy. Nothing converted them, no ``except GAMError`` existed outside
this module, and every GAM fault therefore reached the buyer as one collapsed
code with the diagnosis discarded.
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any, TypeVar

from src.core.exceptions import (
    AdCPAdapterResourceNotFoundError,
    AdCPAuthorizationError,
    AdCPConfigurationError,
    AdCPConflictError,
    AdCPInternalError,
    AdCPRateLimitError,
    AdCPSalesAgentError,
    AdCPServiceUnavailableError,
    AdCPValidationError,
)

# Configure logging
logger = logging.getLogger(__name__)

T = TypeVar("T")


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


def map_gam_exception(exception: Exception) -> AdCPSalesAgentError:
    """Classify an upstream GAM fault into the AdCP error the buyer should read.

    Sorting the fault is the adapter's job and this is the only place that does
    it. The returned error is raised as-is: the tool layer re-raises a typed
    ``AdCPSalesAgentError`` untouched, so this decision is the one the buyer receives.

    Carries NO upstream text into the error. A SOAP fault's string is a third
    party's, and AdCP 3.1.1 transport-errors.mdx forbids putting it on the wire
    (Security Considerations; restated for CONFIGURATION_ERROR as "MUST NOT
    include credentials, connection strings, full file paths, or stack traces").
    The fault travels in ``internal_detail``, which is non-wire by construction,
    and the buyer reads CODE_TABLE's sentence for the code.
    """
    name = type(exception).__name__
    message = str(exception).lower()

    def matches(*needles: str) -> bool:
        return any(n in name for n in needles) or any(n in message for n in needles)

    # Order matters: the first match wins, and "invalid" appears inside many
    # unrelated fault strings, so the specific families are tested ahead of it.
    if matches(
        "AuthError",
        "RefreshError",
        "authentication",
        "invalid_grant",
        "invalid_client",
        "credential",
    ):
        # The SELLER's OAuth to Google failed, not the buyer's token. AUTH_INVALID
        # is defined by the pin as the CALLER's credentials being rejected
        # ("Caller's signed envelope did not verify"), and it is terminal -- so
        # reporting it here would send the buyer to rotate credentials they do not
        # hold and stop them retrying. CONFIGURATION_ERROR is the pin's code for
        # "seller-side deployment ... an operator at the seller has to" fix it.
        #
        # This row MUST stay ahead of the validation row below, and must match the
        # OAuth vocabulary as well as the SOAP one. Google's canonical expired- or
        # revoked-refresh-token failure is `RefreshError: invalid_grant`, which
        # names neither "AuthError" nor "authentication" -- so on keyword order
        # alone it used to fall through to the validation row and told the buyer
        # their request was malformed while the seller's token sat expired. That is
        # the most common credential failure this adapter sees.
        #
        # No details object: the pin says CONFIGURATION_ERROR "carries no
        # error.details shape".
        return AdCPConfigurationError(internal_detail=exception)
    if matches("PermissionError", "permission"):
        return AdCPAuthorizationError(internal_detail=exception)
    if matches("QuotaError", "quota"):
        return AdCPRateLimitError(internal_detail=exception)
    if matches("NotFoundError", "not found"):
        return AdCPAdapterResourceNotFoundError(internal_detail=exception)
    if matches("DuplicateError", "already exists"):
        return AdCPConflictError(internal_detail=exception)
    if matches("NetworkError", "network", "TimeoutError", "timeout"):
        # Network and timeout collapse to one code. Rule 1 in the pin: "A database
        # connection timeout becomes SERVICE_UNAVAILABLE".
        return AdCPServiceUnavailableError(internal_detail=exception)
    if matches("ValidationError", "invalid"):
        return AdCPValidationError(internal_detail=exception)
    # Unclassifiable. NOT AdCPAdapterError: that is SERVICE_UNAVAILABLE, which is
    # both the network row above AND a member of with_retry's default retry set,
    # so routing an unknown fault there would retry it three times with backoff
    # for no reason. The pin: "Opaque crashes that don't fit that profile remain
    # catalog-uncoded".
    return AdCPInternalError(internal_detail=exception)


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
        # Retryability is THIS list, never the buyer-facing ``recovery`` hint from
        # CODE_TABLE: RATE_LIMITED carries recovery=None there, yet a quota fault
        # is exactly what should be retried. They answer different questions.
        #
        # Network and timeout collapse into AdCPServiceUnavailableError, so this
        # list is one shorter than the three GAM classes it replaces while
        # covering the same faults.
        retry_on = [AdCPServiceUnavailableError, AdCPRateLimitError]

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception: AdCPSalesAgentError | None = None
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
                    # Classify once. An already-typed AdCPSalesAgentError is the raise site's
                    # own decision and is never reclassified.
                    adcp_error = e if isinstance(e, AdCPSalesAgentError) else map_gam_exception(e)
                    last_exception = adcp_error

                    should_retry = (
                        any(isinstance(adcp_error, exc_type) for exc_type in retry_on)
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
                            f"{op_name} failed with {adcp_error.error_code}. Retrying in {delay:.1f} seconds..."
                        )

                        time.sleep(delay)
                    else:
                        # The error's own code identifies the fault. A mapped error is
                        # raised ``from`` the upstream fault so the boundary's one record
                        # carries its traceback; an already-typed error is re-raised as
                        # itself (``raise e from e`` would chain it to itself).
                        logger.error(f"{op_name} failed with {adcp_error.error_code}")
                        if adcp_error is e:
                            raise
                        raise adcp_error from e

            # All retries exhausted
            if last_exception is None:
                # This should never happen, but handle it gracefully
                raise AdCPInternalError()

            logger.error(
                f"{op_name} failed after {retry_config.max_attempts} attempts with {last_exception.error_code}"
            )
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
