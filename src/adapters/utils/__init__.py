"""Shared utilities for ad server adapters."""

from src.adapters.utils.http_errors import wrap_request_errors
from src.adapters.utils.timeout import TimeoutError, timeout

__all__ = ["timeout", "TimeoutError", "wrap_request_errors"]
