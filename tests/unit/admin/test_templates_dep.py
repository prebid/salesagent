"""L0-05 — TemplatesDep + BaseCtxDep + tojson filter obligation tests.

Pattern (a) Red: module-level stubs exist but return sentinels / empty dicts —
the tests below assert the real behavior (10-key context, callable drain,
tojson with indent support). Red fails with AttributeError / AssertionError
on the stub — a SEMANTIC failure, not ImportError.

Per .claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-05 and
.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md §D8-native.3.
"""

from __future__ import annotations

import json
from typing import get_args, get_origin
from unittest.mock import MagicMock

from fastapi import Depends
from fastapi.templating import Jinja2Templates

from tests.unit.admin._contracts import ADMIN_BASE_CTX_KEYS


class _StubRequest:
    """Minimal Request duck-type — only exposes .session and .app.state.templates."""

    def __init__(
        self,
        session: dict | None = None,
        templates: Jinja2Templates | None = None,
    ) -> None:
        self.session: dict = session if session is not None else {}
        app = MagicMock()
        app.state.templates = templates
        self.app = app


# ---------------------------------------------------------------------------
# Public-API existence + shape
# ---------------------------------------------------------------------------


def test_templates_module_public_api_exports() -> None:
    """L0-05 obligation: src/admin/deps/templates.py exports the public API."""
    from src.admin.deps import templates as mod

    for name in (
        "TemplatesDep",
        "BaseCtxDep",
        "get_templates",
        "get_base_context",
        "tojson_filter",
    ):
        assert hasattr(mod, name), f"missing export: {name}"


def test_templates_dep_is_annotated_alias_for_jinja2_templates() -> None:
    """TemplatesDep is Annotated[Jinja2Templates, Depends(get_templates)]."""
    from src.admin.deps.templates import TemplatesDep, get_templates

    args = get_args(TemplatesDep)
    assert args, "TemplatesDep must be an Annotated[...] alias (sentinel = None fails here)"
    assert args[0] is Jinja2Templates
    depends_entries = [a for a in args[1:] if isinstance(a, type(Depends(lambda: None)))]
    assert len(depends_entries) == 1
    assert depends_entries[0].dependency is get_templates


def test_base_ctx_dep_is_annotated_alias_for_dict() -> None:
    """BaseCtxDep is Annotated[dict, Depends(get_base_context)]."""
    from src.admin.deps.templates import BaseCtxDep, get_base_context

    args = get_args(BaseCtxDep)
    assert args, "BaseCtxDep must be an Annotated[...] alias (sentinel = None fails here)"
    # args[0] is dict (or dict[str, Any]); accept both forms.
    origin = get_origin(args[0]) or args[0]
    assert origin is dict
    depends_entries = [a for a in args[1:] if isinstance(a, type(Depends(lambda: None)))]
    assert len(depends_entries) == 1
    assert depends_entries[0].dependency is get_base_context


# ---------------------------------------------------------------------------
# get_templates behavior — reads request.app.state.templates
# ---------------------------------------------------------------------------


def test_get_templates_returns_app_state_instance() -> None:
    """get_templates(request) returns request.app.state.templates."""
    from src.admin.deps.templates import get_templates

    jinja = Jinja2Templates(directory="src/admin/templates")
    request = _StubRequest(templates=jinja)

    assert get_templates(request) is jinja  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 10-key BaseCtxDep contract
# ---------------------------------------------------------------------------

EXPECTED_BASE_CTX_KEYS: frozenset[str] = ADMIN_BASE_CTX_KEYS


def _call_get_base_context(request: _StubRequest) -> dict:
    """Call get_base_context with a Messages instance (L0-04)."""
    from src.admin.deps.messages import Messages
    from src.admin.deps.templates import get_base_context

    messages = Messages(request)  # type: ignore[arg-type]
    return get_base_context(request, messages)  # type: ignore[arg-type]


def test_base_ctx_has_exactly_10_keys_no_more_no_less() -> None:
    """The 10-key contract is load-bearing for base.html across ~54 admin pages."""
    request = _StubRequest()
    ctx = _call_get_base_context(request)

    actual = set(ctx.keys())
    expected = EXPECTED_BASE_CTX_KEYS
    assert actual == expected, f"got {sorted(actual)}, want {sorted(expected)}"


def test_base_ctx_has_no_messages_key() -> None:
    """A pre-drained ``messages`` key is deliberately absent.

    Rationale: every base template (base.html, login.html, settings.html,
    signup_onboarding.html, users.html, tenant_users.html) uses
    ``{% with messages = get_flashed_messages(...) %}`` which shadows any
    outer ``messages`` key. Exposing a pre-drained list alongside the
    wrapper causes a double-drain: the pre-populated key consumes the
    bucket, and the wrapper then sees an empty bucket and returns ``[]``.
    See ``src/admin/deps/templates.py::get_base_context`` for the guard
    comment preventing re-addition.
    """
    from src.admin.deps.messages import Messages
    from src.admin.deps.templates import get_base_context

    request = _StubRequest()
    messages = Messages(request)  # type: ignore[arg-type]
    messages.info("seeded")  # seed to prove the wrapper is the only drain site

    ctx = get_base_context(request, messages)  # type: ignore[arg-type]

    assert "messages" not in ctx


def test_base_ctx_session_bridges_request_session() -> None:
    """session key IS the request.session object (so templates can read cookies)."""
    request = _StubRequest(session={"foo": "bar"})
    ctx = _call_get_base_context(request)

    assert ctx["session"] == {"foo": "bar"}


def test_base_ctx_g_test_mode_false_by_default() -> None:
    """g_test_mode bridges Flask's g.test_mode; False unless explicitly set."""
    request = _StubRequest()
    ctx = _call_get_base_context(request)

    # g_test_mode is a bool (Flask semantics)
    assert ctx["g_test_mode"] is False


def test_base_ctx_csrf_token_is_callable_returning_empty_string() -> None:
    """csrf_token is a NULL-OP callable — CSRFOriginMiddleware uses Origin validation."""
    request = _StubRequest()
    ctx = _call_get_base_context(request)

    csrf_token = ctx["csrf_token"]
    assert callable(csrf_token), "csrf_token must be a callable for {{ csrf_token() }} compat"
    assert csrf_token() == ""


def test_base_ctx_get_flashed_messages_is_callable_drain_wrapper() -> None:
    """get_flashed_messages is a callable that drains messages.

    Regression guard for the dual-drain defect: invoking the wrapper
    MUST return the seeded message. A prior implementation populated a
    ``messages`` dict key by calling ``messages.drain()`` inside
    ``get_base_context``, which emptied the bucket before templates
    could invoke the wrapper. The wrapper then returned ``[]`` silently.
    See ``test_base_ctx_has_no_messages_key`` for the absence-guard.
    """
    from src.admin.deps.messages import Messages

    request = _StubRequest()
    messages = Messages(request)  # type: ignore[arg-type]
    messages.info("hello")

    # Build ctx AFTER seeding so the drain-wrapper can see seeded state.
    from src.admin.deps.templates import get_base_context

    ctx = get_base_context(request, messages)  # type: ignore[arg-type]

    gfm = ctx["get_flashed_messages"]
    assert callable(gfm), "get_flashed_messages must be callable for template compat"

    # First invocation: must surface the seeded message (not an empty list —
    # which is what happened under the dual-drain defect).
    first = gfm()
    assert isinstance(first, list)
    assert len(first) == 1, f"expected seeded message, got {first!r}"
    assert first[0]["level"] == "info"
    assert first[0]["text"] == "hello"

    # Second invocation: idempotent drain — bucket is now empty.
    second = gfm()
    assert second == []


def test_base_ctx_user_authenticated_derived_from_session_user() -> None:
    """user_authenticated = bool(session['user'])."""
    # Unauthenticated
    r1 = _StubRequest(session={})
    ctx1 = _call_get_base_context(r1)
    assert ctx1["user_authenticated"] is False

    # Authenticated
    r2 = _StubRequest(session={"user": "alice@example.com", "role": "admin"})
    ctx2 = _call_get_base_context(r2)
    assert ctx2["user_authenticated"] is True
    assert ctx2["user_email"] == "alice@example.com"
    assert ctx2["user_role"] == "admin"


def test_base_ctx_support_email_is_string() -> None:
    """support_email is a non-empty string (from get_support_email())."""
    request = _StubRequest()
    ctx = _call_get_base_context(request)
    assert isinstance(ctx["support_email"], str)
    assert ctx["support_email"]  # non-empty


def test_base_ctx_sales_agent_domain_is_string() -> None:
    """sales_agent_domain is a string (never None — fallback to 'example.com')."""
    request = _StubRequest()
    ctx = _call_get_base_context(request)
    assert isinstance(ctx["sales_agent_domain"], str)
    assert ctx["sales_agent_domain"]


def test_base_ctx_test_mode_is_bool() -> None:
    """test_mode key is a bool."""
    request = _StubRequest()
    ctx = _call_get_base_context(request)
    assert isinstance(ctx["test_mode"], bool)


# ---------------------------------------------------------------------------
# Jinja `tojson` filter — basic, indent=2, nested, null, unicode (5 tests)
# ---------------------------------------------------------------------------


def test_tojson_filter_basic_dict() -> None:
    """tojson({'a': 1}) → '{"a": 1}' (valid JSON)."""
    from src.admin.deps.templates import tojson_filter

    out = tojson_filter({"a": 1})
    assert json.loads(out) == {"a": 1}


def test_tojson_filter_indent_kwarg() -> None:
    """tojson(x, indent=2) produces multi-line indented output."""
    from src.admin.deps.templates import tojson_filter

    out = tojson_filter({"a": 1, "b": 2}, indent=2)
    # 12+ template sites rely on indent= — verify multi-line shape.
    assert "\n" in out
    assert json.loads(out) == {"a": 1, "b": 2}


def test_tojson_filter_nested() -> None:
    """Nested structures serialize correctly."""
    from src.admin.deps.templates import tojson_filter

    payload = {"outer": {"inner": [1, 2, 3]}}
    out = tojson_filter(payload)
    assert json.loads(out) == payload


def test_tojson_filter_nullable() -> None:
    """None serializes to 'null' (valid JSON)."""
    from src.admin.deps.templates import tojson_filter

    assert json.loads(tojson_filter(None)) is None


def test_tojson_filter_unicode() -> None:
    """Unicode strings round-trip losslessly."""
    from src.admin.deps.templates import tojson_filter

    out = tojson_filter({"greeting": "héllo wörld ✓"})
    assert json.loads(out) == {"greeting": "héllo wörld ✓"}
