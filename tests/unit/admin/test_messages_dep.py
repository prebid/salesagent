"""L0-04 — MessagesDep obligation tests.

Pattern (b) Red: module-absence is itself the semantic obligation — Red fails
with ModuleNotFoundError until L0-04 Green lands src/admin/deps/messages.py.

Per .claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-04 and
.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md §D8-native.1.
"""

from __future__ import annotations

import pytest


def test_messages_module_exists() -> None:
    """L0-04 obligation: src/admin/deps/messages.py exists and exports the full API.

    Pattern (b) Red: module-absence is itself the semantic obligation — Red fails
    with ModuleNotFoundError until L0-04 Green lands.
    """
    try:
        from src.admin.deps.messages import (
            FlashMessage,
            MessageLevel,
            Messages,
            MessagesDep,
            get_messages,
        )
    except ModuleNotFoundError as e:
        pytest.fail(
            "src/admin/deps/messages.py must exist and export the L0-04 public API "
            "({FlashMessage, MessageLevel, Messages, MessagesDep, get_messages}). "
            f"ModuleNotFoundError: {e}"
        )
    # Smoke-check exports are real (not None)
    assert MessageLevel.INFO.value == "info"
    assert FlashMessage(level=MessageLevel.INFO, text="ok").text == "ok"
    assert Messages is not None
    assert MessagesDep is not None
    assert callable(get_messages)
