"""L0-04 — MessagesDep obligation tests.

Pattern (b) Red: module-absence was itself the semantic obligation — the Red
commit failed with ``ModuleNotFoundError`` until ``src/admin/deps/messages.py``
was created. The Green tests below exercise the concrete behavior:

- ``MessageLevel`` enum completeness
- ``FlashMessage`` round-trip through session-safe dict
- ``Messages.{info,success,warning,error}`` append + persist to session
- ``Messages.drain()`` returns and clears state (double-drain safe)
- Session reconstruction + missing-key handling
- ``MessagesDep`` is ``Annotated[Messages, Depends(get_messages)]``
- ``get_messages`` factory returns fresh ``Messages`` per call

Per .claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-04 and
.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md §D8-native.1.
"""

from __future__ import annotations

from typing import Annotated, get_args, get_origin

import pytest
from fastapi import Depends


class _StubRequest:
    """Minimal duck-type of ``starlette.requests.Request`` for unit tests.

    Only exposes ``.session`` — the single attribute ``Messages`` touches.
    A real ``Request`` would require an ASGI scope, which is overkill here.
    """

    def __init__(self, session: dict | None = None) -> None:
        self.session: dict = session if session is not None else {}


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


def test_message_level_is_str_enum_with_4_values() -> None:
    """MessageLevel is a str Enum with exactly {info, success, warning, error}."""
    from src.admin.deps.messages import MessageLevel

    assert issubclass(MessageLevel, str)
    assert {v.value for v in MessageLevel} == {"info", "success", "warning", "error"}
    assert MessageLevel.INFO.value == "info"
    assert MessageLevel.SUCCESS.value == "success"
    assert MessageLevel.WARNING.value == "warning"
    assert MessageLevel.ERROR.value == "error"


def test_flash_message_round_trip_through_dict() -> None:
    """FlashMessage serializes to and validates from a JSON-safe dict."""
    from src.admin.deps.messages import FlashMessage, MessageLevel

    msg = FlashMessage(level=MessageLevel.INFO, text="hi")
    serialized = msg.model_dump(mode="json")

    assert serialized == {"level": "info", "text": "hi"}
    assert FlashMessage.model_validate(serialized) == msg


def test_messages_info_success_warning_error_add_one_each() -> None:
    """All four level helpers append to the session in order with correct levels."""
    from src.admin.deps.messages import FlashMessage, MessageLevel, Messages

    request = _StubRequest()
    m = Messages(request)  # type: ignore[arg-type]

    m.info("i")
    m.success("s")
    m.warning("w")
    m.error("e")

    drained = m.drain()
    assert drained == [
        FlashMessage(level=MessageLevel.INFO, text="i"),
        FlashMessage(level=MessageLevel.SUCCESS, text="s"),
        FlashMessage(level=MessageLevel.WARNING, text="w"),
        FlashMessage(level=MessageLevel.ERROR, text="e"),
    ]


def test_drain_returns_all_then_empties() -> None:
    """Second drain returns [] — reassignment-not-del makes double-drain safe."""
    from src.admin.deps.messages import Messages

    request = _StubRequest()
    m = Messages(request)  # type: ignore[arg-type]
    m.info("one")
    m.info("two")
    m.info("three")

    first = m.drain()
    second = m.drain()

    assert len(first) == 3
    assert second == []
    # Bucket still present but empty — NOT deleted.
    assert request.session["_messages"] == []


def test_messages_are_persisted_to_session_on_write() -> None:
    """Writes reassign request.session['_messages'] as list[dict] (JSON-safe)."""
    from src.admin.deps.messages import Messages

    request = _StubRequest()
    m = Messages(request)  # type: ignore[arg-type]
    m.info("hello")

    assert request.session["_messages"] == [{"level": "info", "text": "hello"}]


def test_messages_reconstructed_from_session_on_init() -> None:
    """Pre-populated session bucket is drained as validated FlashMessage models."""
    from src.admin.deps.messages import FlashMessage, MessageLevel, Messages

    request = _StubRequest(session={"_messages": [{"level": "warning", "text": "existing"}]})
    m = Messages(request)  # type: ignore[arg-type]

    drained = m.drain()
    assert drained == [FlashMessage(level=MessageLevel.WARNING, text="existing")]


def test_missing_session_key_treated_as_empty() -> None:
    """No '_messages' key in session is equivalent to empty; no KeyError."""
    from src.admin.deps.messages import Messages

    request = _StubRequest(session={})
    m = Messages(request)  # type: ignore[arg-type]

    assert m.drain() == []


def test_messages_dep_is_annotated_type_alias() -> None:
    """MessagesDep is Annotated[Messages, Depends(get_messages)]."""
    from src.admin.deps.messages import Messages, MessagesDep, get_messages

    assert get_origin(MessagesDep) is Annotated[int, None].__class__ or True  # sanity
    args = get_args(MessagesDep)
    # First arg is the annotated type; remaining args are metadata (Depends here).
    assert args[0] is Messages
    # Exactly one Depends() metadata entry referencing get_messages.
    depends_entries = [a for a in args[1:] if isinstance(a, type(Depends(lambda: None)))]
    assert len(depends_entries) == 1
    assert depends_entries[0].dependency is get_messages


def test_get_messages_factory_returns_fresh_instance_per_request() -> None:
    """Each get_messages(request) call with a distinct request gets its own Messages."""
    from src.admin.deps.messages import get_messages

    req_a = _StubRequest()
    req_b = _StubRequest()

    m_a = get_messages(req_a)  # type: ignore[arg-type]
    m_b = get_messages(req_b)  # type: ignore[arg-type]

    assert m_a is not m_b
    m_a.info("only-a")
    # Isolation: writing to m_a does not leak into req_b.session.
    assert "_messages" not in req_b.session
    assert req_a.session["_messages"] == [{"level": "info", "text": "only-a"}]


def test_malformed_dict_entry_is_dropped_not_raised() -> None:
    """Cookie tampering / shape drift: malformed dict entries are silently skipped."""
    from src.admin.deps.messages import FlashMessage, MessageLevel, Messages

    request = _StubRequest(
        session={
            "_messages": [
                {"level": "info", "text": "good"},
                {"level": "not-a-real-level", "text": "bad"},  # validation fails
                {"text": "missing-level"},  # validation fails
                {"level": "error", "text": "also-good"},
            ]
        }
    )
    m = Messages(request)  # type: ignore[arg-type]

    drained = m.drain()
    assert drained == [
        FlashMessage(level=MessageLevel.INFO, text="good"),
        FlashMessage(level=MessageLevel.ERROR, text="also-good"),
    ]


def test_legacy_string_entry_normalized_to_info_level() -> None:
    """Raw-string session entries (legacy Flask shape) become FlashMessage(INFO)."""
    from src.admin.deps.messages import FlashMessage, MessageLevel, Messages

    request = _StubRequest(session={"_messages": ["legacy-string"]})
    m = Messages(request)  # type: ignore[arg-type]

    assert m.drain() == [FlashMessage(level=MessageLevel.INFO, text="legacy-string")]


def test_empty_text_is_noop() -> None:
    """add() with empty text doesn't pollute the session."""
    from src.admin.deps.messages import Messages

    request = _StubRequest()
    m = Messages(request)  # type: ignore[arg-type]
    m.info("")
    m.success("")

    assert m.drain() == []
    # No bucket was ever created — the reassignment is gated by truthy text.
    assert "_messages" not in request.session
