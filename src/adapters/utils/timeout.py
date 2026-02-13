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

logger = logging.getLogger(__name__)

T = TypeVar("T")


class TimeoutError(Exception):
    """Raised when operation times out."""

    pass


def timeout(seconds: int = 300):
    """
    Decorator to add timeout to a function using ThreadPoolExecutor.

    This implementation works everywhere (threads, Windows, Linux) unlike signal-based timeouts.

    Args:
        seconds: Timeout in seconds (default: 300 = 5 minutes)

    Returns:
        Decorated function with timeout

    Raises:
        TimeoutError: If function doesn't complete within timeout

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

                except concurrent.futures.TimeoutError:
                    # Log timeout for debugging
                    logger.error(
                        f"Operation {func.__name__} timed out after {seconds}s. This usually means the API is hanging."
                    )
                    raise TimeoutError(f"{func.__name__} timed out after {seconds} seconds")

        return wrapper

    return decorator
