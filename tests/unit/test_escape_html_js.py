"""static/js/escape_html.js: the admin UI's helpers for putting a value into HTML it builds.

Run in Node against the shipped file, so the test grades the code the browser loads.
GitHub's runners carry Node; a machine without it fails here rather than skipping.
"""

from __future__ import annotations

import json

import pytest

from tests.helpers.escape_html_node import HELPER, run_node
from tests.helpers.escape_html_node import run_escape_html_helper as _run
from tests.unit._architecture_helpers import repo_root

PAYLOAD = '<img src=x onerror="alert(document.cookie)">'


def test_escape_html_escapes_every_markup_character():
    assert _run("escapeHtml", [PAYLOAD, "a&b", "it's `x`", "plain", 42, None]) == [
        "&lt;img src=x onerror=&quot;alert(document.cookie)&quot;&gt;",
        "a&amp;b",
        "it&#39;s &#96;x&#96;",
        "plain",
        "42",
        "",
    ]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/a?b=1&c=2", "https://example.com/a?b=1&amp;c=2"),
        ("/tenant/t1/products", "/tenant/t1/products"),
        ("mailto:ops@example.com", "mailto:ops@example.com"),
        ("javascript:alert(1)", "#"),
        (" JavaScript:alert(1)", "#"),
        ("java\tscript:alert(1)", "#"),
        ("data:text/html,<script>alert(1)</script>", "#"),
        ("vbscript:msgbox(1)", "#"),
        ('https://example.com/"onmouseover="x', "https://example.com/&quot;onmouseover=&quot;x"),
        (None, ""),
    ],
)
def test_safe_url_refuses_script_schemes_and_escapes_the_rest(url, expected):
    assert _run("safeUrl", [url]) == [expected]


def test_js_arg_is_a_json_literal_escaped_for_an_attribute():
    """In onclick="f(${jsArg(v)})" the browser decodes the entities, and the handler sees JSON."""
    assert _run("jsArg", ["x'); alert(1); ('", 'a"b', "</script><b>", 7, None]) == [
        "&quot;x\\u0027); alert(1); (\\u0027&quot;",
        "&quot;a\\&quot;b&quot;",
        "&quot;\\u003c/script\\u003e\\u003cb\\u003e&quot;",
        "7",
        "null",
    ]


def test_js_arg_stays_a_string_even_inside_a_stray_quote():
    """onclick="f('${jsArg(v)}')" is a mistake, but the value still cannot close the quote."""
    (encoded,) = _run("jsArg", ["'); alert(1); //"])
    decoded = encoded.replace("&quot;", '"').replace("&#39;", "'").replace("&amp;", "&")
    assert "'" not in decoded


def test_a_publisher_display_name_renders_as_text_in_the_publisher_list():
    """renderPublishers (static/js/publisher_partners.js) puts a stored display name in as text.

    The inventory page and the settings page both list publishers with it. The shipped file
    is run in Node against a stub container, so the assertion reads the exact markup the
    browser would parse.
    """
    client = (repo_root() / "static" / "js" / "publisher_partners.js").read_text(encoding="utf-8")
    publisher = {
        "publisher_domain": "attacker.example",
        "display_name": PAYLOAD,
        "authorizes": True,
        "fetch_status": "ok",
        "fetch_error": None,
        "fetched_at": None,
        "property_count": 1,
        "format_option_count": 0,
    }
    script = (
        f"Object.assign(globalThis, require({json.dumps(str(HELPER))}));"
        "const container = {innerHTML: '', dataset: {}};"
        "globalThis.document = {getElementById: () => container};"
        f"{client}\n"
        f"renderPublishers([{json.dumps(publisher)}]);"
        "process.stdout.write(container.innerHTML);"
    )
    markup = run_node(script)
    assert "&lt;img src=x onerror=&quot;alert(document.cookie)&quot;&gt;" in markup
    assert "<img" not in markup
    assert 'onclick="viewPublisherProperties(&quot;attacker.example&quot;)"' in markup


def test_js_arg_refuses_a_value_with_no_json_form():
    script = (
        f"const h = require({json.dumps(str(HELPER))});"
        "const out = [() => 1, Symbol('s'), 10n].map(v => { try { h.jsArg(v); return 'returned'; }"
        " catch (e) { return e.name; } });"
        "process.stdout.write(JSON.stringify(out));"
    )
    assert json.loads(run_node(script)) == ["TypeError", "TypeError", "TypeError"]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/a?b=1&c=2", "https://example.com/a?b=1&c=2"),
        ("/tenant/t1", "/tenant/t1"),
        ("java\tscript:alert(1)", "#"),
        ("data:text/html,x", "#"),
    ],
)
def test_checked_url_refuses_script_schemes_and_leaves_the_rest_unescaped(url, expected):
    assert _run("checkedUrl", [url]) == [expected]
