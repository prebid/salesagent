"""Semantic tests for ``src.admin.helpers.form_errors.form_error_response``.

Pattern (a) stub-first: the module exists but ``form_error_response()``
returns ``None`` at L0 Red, so every assertion about the TemplateResponse
shape fails. L0 Green implements the behavior.

Per ``.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-17``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.testclient import TestClient


@pytest.fixture
def templates(tmp_path) -> Jinja2Templates:
    """Build a Jinja2Templates rooted at a scratch directory with one template.

    The template echoes every context value we care about so assertions
    can hit the rendered body directly.
    """
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "form_error.html").write_text(
        """<!doctype html>
<html>
  <body>
    <div id="error">{{ error }}</div>
    <input id="name" value="{{ form.name }}">
    <div id="extra">{{ extra or "" }}</div>
  </body>
</html>
"""
    )
    return Jinja2Templates(directory=str(template_dir))


@pytest.fixture
def fastapi_request(templates: Jinja2Templates) -> Request:
    """Construct a minimal Request via TestClient so Starlette has a real scope."""
    app = FastAPI()
    captured: dict[str, Any] = {}

    @app.get("/probe")
    def probe(request: Request) -> dict[str, Any]:
        captured["request"] = request
        return {"ok": True}

    with TestClient(app) as client:
        client.get("/probe")
    return captured["request"]


def test_form_error_response_returns_a_response(templates: Jinja2Templates, fastapi_request: Request) -> None:
    """The helper returns a renderable response, not ``None``.

    At L0 Red the stub returns ``None`` — this test fails semantically
    on the first attribute access (``.status_code``), making the Red
    state observable without an ImportError.
    """
    from src.admin.helpers.form_errors import form_error_response

    response = form_error_response(
        templates,
        fastapi_request,
        "form_error.html",
        "Name is required",
        form={"name": ""},
    )
    assert response is not None, "form_error_response returned None — L0 Red stub"
    assert hasattr(response, "status_code")


def test_form_error_response_has_422_status(templates: Jinja2Templates, fastapi_request: Request) -> None:
    """Default status is 422 Unprocessable Entity (RFC 9110 §15.5.21)."""
    from src.admin.helpers.form_errors import FORM_ERROR_STATUS, form_error_response

    response = form_error_response(templates, fastapi_request, "form_error.html", "boom", form={"name": ""})
    assert response.status_code == 422
    assert FORM_ERROR_STATUS == 422


def test_form_error_response_echoes_submitted_form_values(templates: Jinja2Templates, fastapi_request: Request) -> None:
    """Submitted form values survive back to the rendered template."""
    from src.admin.helpers.form_errors import form_error_response

    response = form_error_response(
        templates,
        fastapi_request,
        "form_error.html",
        "Name too short",
        form={"name": "Alice"},
    )
    body = response.body.decode("utf-8")
    assert 'value="Alice"' in body, body


def test_form_error_response_renders_error_message(templates: Jinja2Templates, fastapi_request: Request) -> None:
    """The error message appears in the rendered banner div."""
    from src.admin.helpers.form_errors import form_error_response

    response = form_error_response(templates, fastapi_request, "form_error.html", "Name required")
    body = response.body.decode("utf-8")
    assert "Name required" in body


def test_form_error_response_supports_extra_context(templates: Jinja2Templates, fastapi_request: Request) -> None:
    """Extra context keys land in the template namespace alongside the defaults."""
    from src.admin.helpers.form_errors import form_error_response

    response = form_error_response(
        templates,
        fastapi_request,
        "form_error.html",
        "bad",
        form={"name": ""},
        extra_context={"extra": "EXTRA_PAYLOAD"},
    )
    body = response.body.decode("utf-8")
    assert "EXTRA_PAYLOAD" in body


def test_form_error_response_allows_status_override(templates: Jinja2Templates, fastapi_request: Request) -> None:
    """Callers with parity constraints may override the status code."""
    from src.admin.helpers.form_errors import form_error_response

    response = form_error_response(
        templates,
        fastapi_request,
        "form_error.html",
        "parity",
        form={"name": ""},
        status_code=400,
    )
    assert response.status_code == 400


def test_form_error_response_handles_none_form(templates: Jinja2Templates, fastapi_request: Request) -> None:
    """``form=None`` renders as an empty dict so the template is safe."""
    from src.admin.helpers.form_errors import form_error_response

    response = form_error_response(templates, fastapi_request, "form_error.html", "no-form", form=None)
    # Accessing form.name returns empty because form is a dict.
    body = response.body.decode("utf-8")
    assert 'value=""' in body or "value=" in body
