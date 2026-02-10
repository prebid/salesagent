"""Shared utilities for ad server adapters."""

from src.adapters.utils.timeout import TimeoutError, timeout

__all__ = ["timeout", "TimeoutError"]
