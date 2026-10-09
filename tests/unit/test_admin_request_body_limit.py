"""The admin app refuses a url-encoded body longer than ``MAX_FORM_MEMORY_SIZE`` with 413.

Werkzeug 3.1.9 stopped applying ``max_form_memory_size`` to url-encoded bodies and bounds
them only by ``max_content_length``, which Flask leaves unset. ``AdminRequest`` puts the form
limit back for that content type; JSON and multipart bodies keep the limits they had.

Each case POSTs to a probe view registered on the app ``create_app`` builds, so the request
class under test is the one the factory installs.
"""

from __future__ import annotations

import io

import pytest
from flask import request

from src.admin.app import create_app

#: Flask's default ``MAX_FORM_MEMORY_SIZE``.
FORM_LIMIT = 500_000

URLENCODED = "application/x-www-form-urlencoded"


def _read_body() -> dict[str, int]:
    """Read the body as a view would, and say how much of it arrived."""
    return {
        "form": sum(len(value) for value in request.form.values()),
        "files": sum(len(upload.read()) for upload in request.files.values()),
        "data": len(request.get_data()),
    }


def _post(body: dict, **config):
    app = create_app({"TESTING": True, **config})
    app.add_url_rule("/_body_probe", view_func=_read_body, methods=["POST"])
    return app.test_client().post("/_body_probe", **body)


def _urlencoded(size: int, content_type: str = URLENCODED) -> dict:
    """A url-encoded body of exactly *size* bytes: one field whose value fills the rest."""
    return {"data": "field=" + "x" * (size - len("field=")), "content_type": content_type}


def _json(size: int) -> dict:
    return {"data": '"' + "x" * (size - 2) + '"', "content_type": "application/json"}


def _multipart_file(size: int) -> dict:
    return {"data": {"upload": (io.BytesIO(b"x" * size), "upload.bin")}, "content_type": "multipart/form-data"}


def test_a_url_encoded_body_at_the_form_limit_is_read_whole():
    response = _post(_urlencoded(FORM_LIMIT))

    assert response.status_code == 200
    assert response.get_json()["form"] == FORM_LIMIT - len("field=")


@pytest.mark.parametrize(
    "content_type",
    [URLENCODED, f"{URLENCODED}; charset=utf-8"],
    ids=["bare", "with a charset parameter"],
)
def test_a_url_encoded_body_over_the_form_limit_is_refused(content_type):
    assert _post(_urlencoded(FORM_LIMIT + 1, content_type)).status_code == 413


@pytest.mark.parametrize(
    ("body", "reached"),
    [
        pytest.param(_json(FORM_LIMIT + 1), "data", id="json"),
        pytest.param(_multipart_file(FORM_LIMIT + 1), "files", id="multipart file"),
    ],
)
def test_other_bodies_over_the_form_limit_are_read(body, reached):
    response = _post(body)

    assert response.status_code == 200
    assert response.get_json()[reached] == FORM_LIMIT + 1


@pytest.mark.parametrize(("size", "status"), [(1_000, 200), (1_001, 413)])
def test_a_lower_max_content_length_still_bounds_a_url_encoded_body(size, status):
    assert _post(_urlencoded(size), MAX_CONTENT_LENGTH=1_000).status_code == status


@pytest.mark.parametrize(("size", "status"), [(FORM_LIMIT + 1, 200), (2 * FORM_LIMIT + 1, 413)])
def test_with_no_form_limit_a_url_encoded_body_is_bounded_by_max_content_length_alone(size, status):
    config = {"MAX_FORM_MEMORY_SIZE": None, "MAX_CONTENT_LENGTH": 2 * FORM_LIMIT}

    assert _post(_urlencoded(size), **config).status_code == status
