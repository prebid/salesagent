"""FastAPI dependencies for admin Jinja2 template rendering (D8-native).

Replaces Flask's ``Jinja2Templates`` wrapper + ``inject_context()`` processor.
Canonical reference: ``flask-to-fastapi-foundation-modules.md §D8-native.3``.

**Instance location** — ``Jinja2Templates(directory="src/admin/templates")`` is
attached to ``app.state.templates`` in ``src/app.py::lifespan``. ``get_templates``
reads it off the request; the wrapper module ``src/admin/templating.py`` is
deliberately NOT created (structural guard
``tests/unit/test_architecture_no_admin_wrapper_modules.py`` enforces absence).

**10-key ``BaseCtxDep`` contract** — v1's 6 keys (support_email,
sales_agent_domain, user_email, user_authenticated, user_role, test_mode) plus
v2's 4 additions:

- ``session``            — ``request.session`` proxy (templates read cookies)
- ``g_test_mode``        — bridges Flask's ``g.test_mode`` (bool; False unless set)
- ``csrf_token``         — NULL-OP callable returning ``""`` (CSRFOriginMiddleware
                           uses Origin-header validation, not form tokens —
                           templates coded against Flask reference
                           ``{{ csrf_token() }}``, so a callable must exist)
- ``get_flashed_messages`` — SOLE flash surface: drain-wrapper over the ``Messages``
                           accumulator so templates call
                           ``{% with messages = get_flashed_messages() %}``
                           (Flask compat). A pre-drained ``messages`` key is
                           deliberately absent — see ``get_base_context`` for
                           the dual-drain defect it prevents.

The 10-key contract is load-bearing for ``base.html`` across ~54 admin pages;
drop one key and every page breaks at render time. ``test_template_context_completeness.py``
(L0-05 sibling guard) pins the set.

**``tojson`` filter** — Starlette's ``Jinja2Templates`` does NOT ship Flask's
``tojson`` filter. 30+ expressions across 12 templates use it, 5 with
``|tojson(indent=2)``. Per ``frontend-deep-audit.md §F5``, this module exposes
``tojson_filter(value, indent=None)``; the lifespan startup wires it via
``app.state.templates.env.filters["tojson"] = tojson_filter`` before any
``TemplateResponse`` call (L1a foundation work).

Per .claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-05.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from src.admin.deps.messages import Messages, MessagesDep
from src.core.domain_config import get_sales_agent_domain, get_support_email


def get_templates(request: Request) -> Jinja2Templates:
    """Return the app-state-bound ``Jinja2Templates`` instance.

    The instance is constructed in ``src/app.py::lifespan`` and attached to
    ``app.state.templates`` so every request sees the same filter/globals
    registration (``url_for`` override, ``tojson``, ``from_json``, ``markdown``,
    ``tojson_safe``).
    """
    return request.app.state.templates


TemplatesDep = Annotated[Jinja2Templates, Depends(get_templates)]


def _null_csrf_token() -> str:
    """NULL-OP ``csrf_token()`` for templates.

    Kept as a module-level function (not a lambda) so it is serializable /
    picklable / identity-comparable in tests. The middleware strategy
    (Option A — SameSite=Lax + Origin validation) does NOT use form tokens,
    so emitting ``""`` is correct — any template rendering ``{{ csrf_token() }}``
    produces an empty string attribute that Jinja/HTML tolerate.
    """
    return ""


def _build_flashed_messages_wrapper(messages: Messages) -> Callable[..., list[dict[str, Any]]]:
    """Return a zero-arg callable that drains ``messages`` on invocation.

    Flask templates call ``get_flashed_messages()`` (optionally with
    ``with_categories=True``, ``category_filter=[...]`` — both ignored here
    because ``Messages.drain()`` already returns typed ``FlashMessage`` objects
    with a ``.level.value`` attribute that template authors can read directly).

    The wrapper captures a reference to ``messages`` so repeated invocations
    see the current bucket state. Draining is idempotent (reassigns to ``[]``).
    """

    def _drain(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [{"level": m.level.value, "text": m.text} for m in messages.drain()]

    return _drain


def get_base_context(
    request: Request,
    messages: MessagesDep,
) -> dict[str, Any]:
    """Auto-merged template context — replaces Flask's ``inject_context()`` processor.

    Returns the 10-key contract (see module docstring). ``get_flashed_messages``
    is the SOLE drain site — invoked lazily from templates via
    ``{% with messages = get_flashed_messages() %}``.

    NO ``csrf_token`` string (callable-NULL-OP instead). NO tenant (N+1 risk —
    handlers load on-demand via ``CurrentTenantDep``).
    """
    session = request.session
    # NO "messages" key — templates shadow it via {% with messages = get_flashed_messages(...) %}.
    # Adding a pre-drained "messages" key here would double-drain the session bucket
    # (wrapper sees an empty bucket and returns []); see
    # tests/unit/admin/test_templates_dep.py::test_base_ctx_has_no_messages_key.
    return {
        "support_email": get_support_email(),
        "sales_agent_domain": get_sales_agent_domain() or "example.com",
        "user_email": session.get("user"),
        "user_authenticated": bool(session.get("user")),
        "user_role": session.get("role"),
        "test_mode": False,
        # v2 additions (F2 / H4-H5 / frontend-deep-audit)
        "session": session,
        "g_test_mode": False,
        "csrf_token": _null_csrf_token,
        "get_flashed_messages": _build_flashed_messages_wrapper(messages),
    }


BaseCtxDep = Annotated[dict[str, Any], Depends(get_base_context)]


def tojson_filter(value: Any, indent: int | None = None) -> str:
    """Jinja ``|tojson`` filter — Flask-compatible JSON serializer.

    Starlette's ``Jinja2Templates`` does not register this filter by default.
    Wired into the Jinja env at lifespan startup; exposed here so the
    registration site and tests share one implementation.

    Parameters
    ----------
    value
        Any JSON-serializable object.
    indent
        If non-None, produces multi-line pretty-printed JSON with the given
        indent. 5 template sites use ``|tojson(indent=2)``; the rest omit the
        kwarg and get compact output.

    Non-ASCII characters are preserved losslessly (``ensure_ascii=False``) —
    matches Flask's ``tojson`` behavior for unicode template data.
    """
    return json.dumps(value, indent=indent, ensure_ascii=False)
