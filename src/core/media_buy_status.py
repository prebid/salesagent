"""Public media-buy canonical wire-status resolution.

Cross-package callers (admin blueprints, etc.) import from here. Tool-internal
modules may continue to use ``src.core.tools._media_buy_status`` directly.
"""

from __future__ import annotations

from src.core.tools._media_buy_status import resolve_canonical_status

__all__ = ["resolve_canonical_status"]
