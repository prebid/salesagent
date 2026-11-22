"""Run a block against an emptied environment that still lets the platform work.

``patch.dict(os.environ, values, clear=True)`` is how a test proves a value reaches the
code under test only through the variables it names. On Windows an empty environment
also removes ``SYSTEMROOT``, without which OpenSSL cannot build an ``SSLContext``
(``ssl.SSLError: [SSL] unknown error``), and ``USERPROFILE``, without which
``Path.home()`` raises. Neither carries configuration, so both are kept. On every other
platform nothing is kept and this is exactly ``patch.dict(..., clear=True)``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Any
from unittest.mock import patch

_PLATFORM_VARIABLES = ("SYSTEMROOT", "USERPROFILE") if os.name == "nt" else ()


def isolated_environ(values: Mapping[str, str] | None = None) -> AbstractContextManager[Any]:
    """``patch.dict(os.environ, values, clear=True)``, keeping only the variables the OS needs."""
    kept = {name: os.environ[name] for name in _PLATFORM_VARIABLES if name in os.environ}
    return patch.dict(os.environ, {**kept, **(values or {})}, clear=True)
