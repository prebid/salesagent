"""Shared ``form_error_response()`` helper for form-validation errors.

The 25 admin router modules ported across L1a-L1d all hit the same
re-render-on-form-error flow: a POST handler validates input, finds a
problem, and re-renders the original template with the submitted form
values + an error banner. Without a shared helper, each site duplicates
the plumbing and drifts on status-code conventions.

This helper pins:

- HTTP status ``422 Unprocessable Content`` (RFC 9110 §15.5.21).
- Preserved form data echoed back to the template under ``form``.
- Single error message under ``error``.

Per ``.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-17``
and ``implementation-checklist.md §EP-3``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates
    from starlette.requests import Request

# RFC 9110 §15.5.21 — 422 Unprocessable Content. Hard-coded rather than
# referenced via ``fastapi.status`` so the constant is stable across the
# Starlette/FastAPI rename from ``UNPROCESSABLE_ENTITY`` to
# ``UNPROCESSABLE_CONTENT`` in upstream 0.40+.
FORM_ERROR_STATUS: int = 422


def form_error_response(
    templates: Jinja2Templates,
    request: Request,
    template_name: str,
    error: str,
    form: dict[str, Any] | None = None,
    *,
    extra_context: dict[str, Any] | None = None,
    status_code: int = FORM_ERROR_STATUS,
) -> Any:
    """Render a form-error template response with a consistent contract.

    Parameters
    ----------
    templates
        The request-scoped ``Jinja2Templates`` instance (usually injected
        via ``TemplatesDep``).
    request
        The current ``Request`` — Starlette needs this in the context so
        ``url_for`` works inside the template.
    template_name
        Template path relative to the ``Jinja2Templates`` root, e.g.
        ``"admin/accounts/edit.html"``.
    error
        Human-readable error message to render in the banner.
    form
        Dict of submitted form fields to echo back so the user does not
        retype. Pass ``None`` for GET-style flows with no form input —
        the template sees an empty dict under ``form``.
    extra_context
        Extra template variables (e.g. the ``tenant`` the form belongs
        to). Merged into the base context so callers can keep one call
        instead of constructing a full context dict.
    status_code
        HTTP status code — defaults to 422 per RFC 9110 §15.5.21. Callers
        should NOT override unless a router has a documented parity
        exception matched against Flask route fingerprints.

    Returns
    -------
    TemplateResponse
        A Starlette ``_TemplateResponse`` with the requested template,
        status code, and the standard ``{request, error, form, ...}``
        context. Callers return it directly from the route handler.
    """
    context: dict[str, Any] = {
        "error": error,
        "form": form or {},
    }
    if extra_context:
        context.update(extra_context)
    # New-style signature (request first, context second) — the old
    # kwargs form (name=, context=) is deprecated upstream since
    # Starlette 0.35; using the modern form means no DeprecationWarning
    # spam in the suite.
    return templates.TemplateResponse(
        request,
        template_name,
        context,
        status_code=status_code,
    )
