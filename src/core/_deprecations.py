"""Deprecation-warning helpers and shared sunset version constants.

Centralizes the legacy-shape sunset version (so a single edit moves the date
when removal lands) and the skip-file-prefix computation needed for warnings
emitted from inside Pydantic ``model_validator`` callbacks.

Without ``skip_file_prefixes``, ``DeprecationWarning`` from inside a validator
points at our own code (the validator function) or Pydantic internals — neither
of which buyers can act on. Skipping both salesagent and pydantic frames
attributes the warning to the buyer's own call site.
"""

import os
import warnings

import pydantic

# Sunset target for the legacy creative wire shapes (string format_id, legacy
# 'format' key). Issue #289. Two release cycles after the warning lands.
LEGACY_FORMAT_ID_SUNSET = "v1.10.0"

_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + os.sep
_PYDANTIC_ROOT = os.path.dirname(pydantic.__file__) + os.sep
_CALLER_SKIP_PREFIXES = (_SRC_ROOT, _PYDANTIC_ROOT)


def warn_deprecated(message: str) -> None:
    """Emit ``DeprecationWarning`` attributed to the caller, not us or Pydantic."""
    warnings.warn(message, DeprecationWarning, skip_file_prefixes=_CALLER_SKIP_PREFIXES)
