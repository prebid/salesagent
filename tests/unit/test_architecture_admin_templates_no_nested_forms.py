"""Guard: no admin template opens a ``<form>`` inside another ``<form>``.

WHY THIS EXISTS. HTML does not allow nested forms, and browsers do not report them. The
parser drops the inner ``<form>`` start tag, then the inner ``</form>`` closes the OUTER form.
Everything after that point leaves the outer form without any error.

That is how the Account Settings page broke: the favicon upload, URL and remove forms sat
inside ``#account-settings-form``. The first inner ``</form>`` ended the account form, so
"Save Account Settings" was outside every form and clicking it sent nothing. The favicon
"Upload" button became the account form's submit button and posted a bare filename to
``/settings/general``. Nothing failed on the server, so no route test could see it.

This reads the template text. It ignores Jinja and HTML comments but not ``{% if %}``
branches. A form opened in both arms of a conditional and closed once after it would be
reported, and that pattern should be written differently anyway.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "templates"

_FORM_TAG = re.compile(r"<form\b|</form\s*>", re.IGNORECASE)
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _blank_out(pattern: re.Pattern[str], text: str) -> str:
    """Remove matches but keep their newlines, so reported line numbers stay true."""
    return pattern.sub(lambda m: "\n" * m.group().count("\n"), text)


def _nested_forms(text: str) -> list[tuple[int, int]]:
    """(inner line, enclosing line) for every ``<form>`` opened while another is open."""
    text = _blank_out(_HTML_COMMENT, _blank_out(_JINJA_COMMENT, text))
    open_lines: list[int] = []
    nested: list[tuple[int, int]] = []
    for match in _FORM_TAG.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        if match.group().lower().startswith("<form"):
            if open_lines:
                nested.append((line, open_lines[-1]))
            open_lines.append(line)
        elif open_lines:
            open_lines.pop()
    return nested


def _templates() -> list[Path]:
    return sorted(TEMPLATE_DIR.rglob("*.html"))


def test_no_template_nests_a_form_inside_another() -> None:
    """A nested form silently truncates its parent; the parent's later fields and buttons die."""
    offenders = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{inner} opens a <form> inside the one opened at line {outer}"
        for path in _templates()
        for inner, outer in _nested_forms(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "these templates nest one <form> inside another. The browser ends the outer form at "
        "the inner </form>, so the outer form's later inputs and submit button stop working:\n  "
        + "\n  ".join(offenders)
        + '\n\nMove the inner form outside the outer one, and use the form="<id>" attribute '
        "on its inputs and buttons if they must stay where they are on the page."
    )


def test_the_guard_detects_nesting() -> None:
    """The scan must catch the shape that shipped, or it passes for the wrong reason."""
    shipped = '<form id="outer">\n<form action="/inner">\n</form>\n<button>Save</button>\n</form>'
    assert _nested_forms(shipped) == [(2, 1)]
    assert _nested_forms('<form id="a"></form>\n<form id="b"></form>') == []
    assert _nested_forms('<form id="a">\n{# <form> #}\n<!-- <form> -->\n</form>') == []


def test_the_guard_reads_a_non_empty_set() -> None:
    """A scan that finds no templates would pass for the wrong reason."""
    templates = _templates()
    assert len(templates) > 20, f"only {len(templates)} templates found — the scan is looking in the wrong place"
    assert any("<form" in p.read_text(encoding="utf-8") for p in templates), "no template contains a <form>"
