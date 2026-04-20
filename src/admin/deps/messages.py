"""FastAPI dependency for transient user-facing messages across redirects.

Native greenfield replacement for Flask's ``flash()`` / ``get_flashed_messages``.
Messages are POSTed by handlers, survive a Post/Redirect/Get round-trip in
``request.session["_messages"]``, and drained by the next GET handler that
renders a template.

Per .claude/notes/flask-to-fastapi/CLAUDE.md §D8 #4 SUPERSEDED: this module
lives under ``src/admin/deps/``, NOT as a top-level ``src/admin/flash.py``
wrapper. Structural guard ``tests/unit/test_architecture_no_admin_wrapper_modules.py``
asserts that ``src/admin/flash.py``, ``src/admin/sessions.py``, and
``src/admin/templating.py`` do NOT exist — the D8-native design places these
helpers under ``src/admin/deps/`` (messages) and inline in ``src/app.py``
(sessions, templates).

Canonical spec:
    flask-to-fastapi-foundation-modules.md §D8-native.1

Storage backend: session-backed via Starlette's signed-cookie
``SessionMiddleware``. Post/Redirect/Get is the canonical flash pattern and
messages must survive the 303 redirect — ``app.state``-backed queues would be
wiped between the POST response and the GET render.

Wire shape: ``list[FlashMessage]`` where ``FlashMessage`` is a Pydantic
``BaseModel`` with fields ``{level: MessageLevel, text: str}``. Stored as
serialized dicts because ``SessionMiddleware`` JSON-encodes the whole session
cookie.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from fastapi import Depends
from pydantic import BaseModel
from starlette.requests import Request


class MessageLevel(str, Enum):
    """Severity level for a flash message."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class FlashMessage(BaseModel):
    """A single flash message. Pydantic-typed so session storage validates."""

    level: MessageLevel
    text: str


_SESSION_KEY = "_messages"


class Messages:
    """Per-request flash-message accumulator backed by ``request.session``.

    Constructed by the ``get_messages()`` dep-factory and cached per request
    by FastAPI's dependency-resolution system (exactly one instance per
    request scope, so ``.drain()`` is called at most once per render).

    Writes reassign the top-level session key (not mutate-in-place) because
    Starlette's ``SessionMiddleware`` only flags the session dirty on
    top-level key reassignment.
    """

    __slots__ = ("_request",)

    def __init__(self, request: Request) -> None:
        self._request = request

    def add(self, level: MessageLevel, text: str) -> None:
        """Append a message at ``level``. Empty ``text`` is a no-op."""
        if not text:
            return
        raw = list(self._request.session.get(_SESSION_KEY, []))
        raw.append(FlashMessage(level=level, text=text).model_dump(mode="json"))
        # Reassignment dirties session — mutating raw in place would not.
        self._request.session[_SESSION_KEY] = raw

    def info(self, text: str) -> None:
        """Append an INFO-level message."""
        self.add(MessageLevel.INFO, text)

    def success(self, text: str) -> None:
        """Append a SUCCESS-level message."""
        self.add(MessageLevel.SUCCESS, text)

    def warning(self, text: str) -> None:
        """Append a WARNING-level message."""
        self.add(MessageLevel.WARNING, text)

    def error(self, text: str) -> None:
        """Append an ERROR-level message."""
        self.add(MessageLevel.ERROR, text)

    def drain(self) -> list[FlashMessage]:
        """Return all pending messages and clear the session bucket.

        Reassigns the bucket to ``[]`` (NOT ``del``) so double-drain is safe.
        Normalizes legacy string entries to ``FlashMessage(level=INFO, ...)``
        to survive mid-deploy cookie-shape drift.
        """
        raw = list(self._request.session.get(_SESSION_KEY, []))
        if not raw:
            return []
        self._request.session[_SESSION_KEY] = []
        out: list[FlashMessage] = []
        for entry in raw:
            if isinstance(entry, dict):
                try:
                    out.append(FlashMessage.model_validate(entry))
                except Exception:
                    # Malformed entries (cookie tampering, shape drift) are dropped.
                    continue
            elif isinstance(entry, str):
                out.append(FlashMessage(level=MessageLevel.INFO, text=entry))
        return out


def get_messages(request: Request) -> Messages:
    """FastAPI dep-factory: return a ``Messages`` bound to ``request.session``."""
    return Messages(request)


MessagesDep = Annotated[Messages, Depends(get_messages)]
