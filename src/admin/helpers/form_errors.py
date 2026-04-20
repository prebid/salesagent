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

**Pattern (a) stub-first** — at L0 Red this function returns ``None`` so
semantic tests against a module-that-exists fail on content, not import.
L0 Green replaces the stub with the real ``TemplateResponse`` impl.
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
    """Stub — returns ``None`` at L0 Red. Replaced at L0 Green.

    Arguments are documented on the L0 Green rewrite.
    """
    _ = (templates, request, template_name, error, form, extra_context, status_code)
    return None
