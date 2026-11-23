"""``safe_url`` (Python) and ``checkedUrl`` (static/js/escape_html.js): a URL cannot be a script URL.

Both answer the same question for the two places a URL is written, so one case table grades
both, and a case added for one is a case for the other.
"""

from __future__ import annotations

import pytest

from src.admin.app import create_app
from src.core.http_utils import safe_url
from tests.helpers.escape_html_node import run_escape_html_helper as _run

CASES = [
    ("https://example.com/a?b=1", "https://example.com/a?b=1"),
    ("http://example.com", "http://example.com"),
    ("mailto:ops@example.com", "mailto:ops@example.com"),
    ("/tenant/t1/settings", "/tenant/t1/settings"),
    ("?page=2", "?page=2"),
    ("javascript:alert(1)", "#"),
    (" JaVaScRiPt:alert(1)", "#"),
    ("java\tscript:alert(1)", "#"),
    ("data:text/html,<script>alert(1)</script>", "#"),
    ("vbscript:x", "#"),
    ("//cdn.example/x.png", "//cdn.example/x.png"),
    ("HTTPS://EXAMPLE.COM", "HTTPS://EXAMPLE.COM"),
    ("\u0001javascript:alert(1)", "#"),
    ("  /padded  ", "/padded"),
    (None, ""),
]


@pytest.mark.parametrize(("value", "expected"), CASES)
def test_safe_url(value, expected):
    assert safe_url(value) == expected


def test_checked_url_answers_every_case_the_same_way():
    assert _run("checkedUrl", [value for value, _ in CASES]) == [expected for _, expected in CASES]


def test_the_filter_is_registered_and_autoescaped():
    env = create_app({"TESTING": True, "SECRET_KEY": "test-secret"}).jinja_env
    rendered = env.from_string('<a href="{{ u|safe_url }}">').render(u="javascript:alert(1)")
    assert rendered == '<a href="#">'
    rendered = env.from_string('<a href="{{ u|safe_url }}">').render(u='https://x.example/?a="b"')
    assert rendered == '<a href="https://x.example/?a=&#34;b&#34;">'
