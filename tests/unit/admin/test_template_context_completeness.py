"""L0-24 atomic guard: ``BaseCtxDep`` returns the full 11-key contract.

Rationale — ``src/admin/deps/templates.py::get_base_context`` is the
function that replaces Flask's ``inject_context()`` processor. Every
admin template inherits ``base.html`` which references 11 context
keys; omitting one breaks page rendering at runtime. The structural
invariant is that the returned dict keys == exactly the 11 keys
documented in the module docstring.

The contract:

v1 (Flask parity, 7 keys):

- ``messages``
- ``support_email``
- ``sales_agent_domain``
- ``user_email``
- ``user_authenticated``
- ``user_role``
- ``test_mode``

v2 additions (4 keys) per ``frontend-deep-audit.md §F2/H4-H5``:

- ``session``
- ``g_test_mode``
- ``csrf_token`` (callable, NOT string)
- ``get_flashed_messages`` (callable drain wrapper)

Any drift — a renamed key, a silently-dropped key, a key type change —
breaks base.html rendering across ~54 admin pages. This guard pins
the key set exactly.

Per ``.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-24``
(row 22 of the §5.5 Structural Guards Inventory).

discipline: N/A - guard pins the L0-05 contract; no paired Red commit.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient

from tests.unit.admin._contracts import ADMIN_BASE_CTX_KEYS

EXPECTED_KEYS: frozenset[str] = ADMIN_BASE_CTX_KEYS


def _build_context() -> dict[str, object]:
    """Exercise ``get_base_context`` via a minimal FastAPI app+TestClient.

    SessionMiddleware is required because ``get_base_context`` reads
    ``request.session``; ``MessagesDep`` needs the session to stash
    flash messages.
    """
    from src.admin.deps.messages import Messages
    from src.admin.deps.templates import get_base_context

    captured: dict[str, object] = {}

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    @app.get("/probe")
    def probe(request: Request) -> dict[str, str]:
        # Build a fresh Messages instance bound to this request.
        messages = Messages(request)
        ctx = get_base_context(request, messages)
        captured.update(ctx)
        return {"ok": "yes"}

    with TestClient(app) as client:
        client.get("/probe")
    return captured


def test_base_context_returns_exactly_eleven_keys() -> None:
    """get_base_context returns exactly the 11 expected keys — no more, no less."""
    ctx = _build_context()
    keys = set(ctx.keys())
    missing = EXPECTED_KEYS - keys
    extra = keys - EXPECTED_KEYS
    assert not missing, f"BaseCtxDep missing required keys: {sorted(missing)}"
    assert not extra, (
        f"BaseCtxDep grew unexpected keys: {sorted(extra)}. Expanding the "
        "contract requires a spec update — frontend-deep-audit.md §F2/H4-H5."
    )


def test_base_context_key_count_is_eleven() -> None:
    """Key-count pin: the contract is exactly 11 keys."""
    ctx = _build_context()
    assert len(ctx) == 11, f"Expected 11 keys, got {len(ctx)}: {sorted(ctx.keys())}"


def test_csrf_token_is_callable_not_string() -> None:
    """csrf_token is a NULL-OP callable, not a string (per L0-05 docstring).

    Templates call ``{{ csrf_token() }}`` (Flask compat). If the key
    were a bare string, Jinja would render the repr, not an empty
    token.
    """
    ctx = _build_context()
    assert callable(ctx["csrf_token"]), f"csrf_token must be callable (NULL-OP); got {type(ctx['csrf_token']).__name__}"
    # Invocation returns empty string — tokens are not required (Option A CSRF).
    result = ctx["csrf_token"]()  # type: ignore[operator]
    assert result == "", f"csrf_token() must return '' (NULL-OP); got {result!r}"


def test_get_flashed_messages_is_callable() -> None:
    """get_flashed_messages is a drain-wrapper callable, not a list.

    Flask templates call ``get_flashed_messages(with_categories=True)``.
    The wrapper ignores the kwargs and drains the Messages bucket.
    """
    ctx = _build_context()
    fn = ctx["get_flashed_messages"]
    assert callable(fn), f"get_flashed_messages must be callable; got {type(fn).__name__}"
    # Call with Flask-style kwargs that the wrapper must tolerate.
    assert isinstance(fn(with_categories=True), list)  # type: ignore[operator]


def test_user_authenticated_is_a_bool() -> None:
    """user_authenticated is a bool, not the truthy user-session dict."""
    ctx = _build_context()
    assert isinstance(
        ctx["user_authenticated"], bool
    ), f"user_authenticated must be bool; got {type(ctx['user_authenticated']).__name__}"


def test_test_mode_defaults_to_false_unlessset() -> None:
    """test_mode and g_test_mode both default to False."""
    ctx = _build_context()
    assert ctx["test_mode"] is False
    assert ctx["g_test_mode"] is False


def test_session_is_the_request_session_proxy() -> None:
    """``session`` is the mutable ``request.session`` mapping.

    Templates read cookies / user identity out of ``session`` directly.
    """
    ctx = _build_context()
    # The Starlette SessionMiddleware yields a mapping object.
    session = ctx["session"]
    assert hasattr(session, "get"), f"session must be mapping-like; got {type(session).__name__}"


def test_csrf_token_is_the_module_level_null_op() -> None:
    """csrf_token is the SAME module-level function across requests.

    Identity comparison defends against a lambda-per-request that would
    be harder to test and picklability-sensitive.
    """
    from src.admin.deps.templates import _null_csrf_token

    ctx = _build_context()
    token: Callable[[], str] = ctx["csrf_token"]  # type: ignore[assignment]
    assert token is _null_csrf_token, (
        "csrf_token in BaseCtxDep must be the module-level _null_csrf_token "
        "function (not a per-request lambda). See L0-05 docstring."
    )
