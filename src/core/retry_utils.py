"""Retry utilities for resilient operations."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TypeVar

import aiohttp

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_on_exception(
    max_attempts: int = 3, delay: float = 1.0, backoff_factor: float = 2.0, exceptions: tuple = (Exception,)
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Retry decorator for synchronous functions.

    Args:
        max_attempts: Maximum number of attempts
        delay: Initial delay between attempts in seconds
        backoff_factor: Multiplier for delay after each failure
        exceptions: Tuple of exceptions to retry on

    Returns:
        Decorated function with retry logic
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None
            current_delay = delay

            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        logger.warning(
                            f"Attempt {attempt + 1}/{max_attempts} failed for {func.__name__}: {e}. "
                            f"Retrying in {current_delay}s..."
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff_factor
                    else:
                        logger.error(f"All {max_attempts} attempts failed for {func.__name__}")

            # Re-raise the last exception if all attempts failed
            assert last_exception is not None, "last_exception should be set if all attempts failed"
            raise last_exception

        return wrapper

    return decorator


def async_retry_on_exception(
    max_attempts: int = 3, delay: float = 1.0, backoff_factor: float = 2.0, exceptions: tuple = (Exception,)
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """
    Async retry decorator for asynchronous functions.

    Args:
        max_attempts: Maximum number of attempts
        delay: Initial delay between attempts in seconds
        backoff_factor: Multiplier for delay after each failure
        exceptions: Tuple of exceptions to retry on

    Returns:
        Decorated async function with retry logic
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception = None
            current_delay = delay

            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        logger.warning(
                            f"Attempt {attempt + 1}/{max_attempts} failed for {func.__name__}: {e}. "
                            f"Retrying in {current_delay}s..."
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff_factor
                    else:
                        logger.error(f"All {max_attempts} attempts failed for {func.__name__}")

            # Re-raise the last exception if all attempts failed
            assert last_exception is not None, "last_exception should be set if all attempts failed"
            raise last_exception

        return wrapper

    return decorator


# Common retry configurations
http_retry = async_retry_on_exception(
    max_attempts=3,
    delay=1.0,
    backoff_factor=2.0,
    exceptions=(aiohttp.ClientError, asyncio.TimeoutError, ConnectionError),
)

api_retry = retry_on_exception(
    max_attempts=3, delay=0.5, backoff_factor=1.5, exceptions=(ConnectionError, TimeoutError, Exception)
)
