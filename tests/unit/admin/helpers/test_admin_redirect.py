"""Unit tests for L0-32 ``admin_redirect()`` 302-default helper.

Canonical spec: ``L0-implementation-plan-v2.md`` §L0-32.

Obligations:
  1. Default status is 302 (Flask's ``redirect()`` default) — NOT 307
     (FastAPI's ``RedirectResponse`` default). This is the whole reason
     the helper exists.
  2. ``status_code`` is overrideable to 307 for POST-body preservation.
  3. Query strings survive the redirect verbatim in the ``Location``
     header — the helper does not rewrite or re-escape.
  4. Absolute URLs (``https://...``) pass through unchanged.
  5. Relative ``url_for(...)``-style URLs pass through unchanged.
"""

from __future__ import annotations

from starlette.responses import RedirectResponse

from src.admin.helpers.redirects import DEFAULT_REDIRECT_STATUS, admin_redirect


def test_default_status_is_302() -> None:
    """The helper must default to 302 — NOT FastAPI's 307."""

    response = admin_redirect("/admin/")

    assert response.status_code == 302
    assert DEFAULT_REDIRECT_STATUS == 302


def test_status_override_to_307_preserves_post_body() -> None:
    """``status_code=307`` overrideable (POST-body preservation path)."""

    response = admin_redirect("/admin/submit", status_code=307)

    assert response.status_code == 307


def test_returns_redirect_response_instance() -> None:
    """The helper wraps ``RedirectResponse`` — callers can rely on the
    response class contract (``Location`` header, redirect status
    handling, etc.)."""

    response = admin_redirect("/admin/")

    assert isinstance(response, RedirectResponse)


def test_query_string_preserved_verbatim() -> None:
    """Query strings on the target URL are not rewritten — the helper
    hands the string to ``RedirectResponse`` unchanged."""

    target = "/admin/products?page=2&sort=created_at"
    response = admin_redirect(target)

    assert response.headers["location"] == target


def test_absolute_url_passthrough() -> None:
    """Absolute URLs survive unchanged in the ``Location`` header."""

    target = "https://example.test/admin/callback?code=xyz"
    response = admin_redirect(target)

    assert response.headers["location"] == target


def test_relative_url_for_target_passthrough() -> None:
    """The shape ``url_for(...)`` produces for a named admin route
    (relative path, possibly with query string) passes through
    unchanged."""

    target = "/tenant/tenant_123/products"
    response = admin_redirect(target)

    assert response.headers["location"] == target
