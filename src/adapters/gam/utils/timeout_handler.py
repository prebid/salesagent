"""Timeout handler for GAM operations.

Re-exports the shared timeout utilities for backwards compatibility.

A previous version re-exported a module-local ``TimeoutError`` that shadowed the
builtin. The shared decorator now raises ``AdCPServiceUnavailableError``, so
callers catch the AdCP error directly and there is no adapter-local timeout class
to keep in step.
"""

from src.adapters.utils.timeout import timeout
from src.core.exceptions import AdCPServiceUnavailableError

__all__ = ["AdCPServiceUnavailableError", "timeout"]
