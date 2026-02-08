"""
Timeout handler for GAM operations.

Re-exports shared timeout utilities for backwards compatibility.
"""

# Re-export from shared utilities
from src.adapters.utils.timeout import TimeoutError, timeout

__all__ = ["timeout", "TimeoutError"]
