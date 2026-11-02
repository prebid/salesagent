"""
Timeout handler for adapter operations.

Provides timeout decorator to prevent operations from hanging indefinitely.
Uses ThreadPoolExecutor for cross-platform compatibility (works on Windows, threads, etc.).
"""

import concurrent.futures
import logging
from collections.abc import Callable
from functools import wraps
from typing import TypeVar

from src.core.exceptions import AdCPServiceUnavailableError

logger = logging.getLogger(__name__)

T = TypeVar("T")


def timeout(seconds: int = 300):
    """
    Decorator to add timeout to a function using ThreadPoolExecutor.

    This implementation works everywhere (threads, Windows, Linux) unlike signal-based timeouts.

    Args:
        seconds: Timeout in seconds (default: 300 = 5 minutes)

    Returns:
        Decorated function with timeout

    Raises:
        AdCPServiceUnavailableError: If the function doesn't complete in time.
            AdCP 3.1.1 transport-errors.mdx Rule 1 names this translation: "A
            database connection timeout becomes SERVICE_UNAVAILABLE." A hung
            ad-server call gets the same code, and both are transient, so the
            buyer's instruction -- retry with backoff -- is correct for either.

    Example:
        @timeout(seconds=60)
        def slow_api_operation():
            # This will be killed if it takes more than 60 seconds
            response = api_service.getSomething()
            return response
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            # Execute function in separate thread with timeout
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(func, *args, **kwargs)

                try:
                    result = future.result(timeout=seconds)
                    return result

                except concurrent.futures.TimeoutError as e:
                    # Log timeout for debugging
                    logger.error(
                        f"Operation {func.__name__} timed out after {seconds}s. This usually means the API is hanging."
                    )
                    raise AdCPServiceUnavailableError(internal_detail=e) from e

        return wrapper

    return decorator
