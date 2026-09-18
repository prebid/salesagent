"""Guard: every template an admin route renders exists.

WHY THIS EXISTS. Two admin routes rendered templates that had never been written, and both
were invisible for as long as they shipped:

* ``authorized_properties.list_authorized_properties`` raised ``TemplateNotFound``, which the
  route's own broad ``except Exception`` turned into a flash and a 302 to the dashboard — so
  the page looked like a transient error rather than a missing file;
* ``creatives.add_ai`` was never linked from anywhere, so nobody arrived to see it fail.

Neither is catchable by a test of the route, because a route test has to know the page is
supposed to exist. This reads the tree instead: any ``render_template("x.html")`` under
``src/admin`` must name a file under ``templates/``. A name that does not resolve is a page
that is broken on every request, and the check costs one AST walk.

It grades NAMES, deliberately. Whether the page is reachable, correct or useful is not
something a scan can tell you — ``tests/ui`` drives a browser for that. What a scan CAN tell
you is that the file the route names is not there, which is the failure that shipped twice.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ADMIN_BLUEPRINTS = REPO_ROOT / "src" / "admin"
TEMPLATE_DIR = REPO_ROOT / "templates"


def _rendered_templates() -> set[tuple[str, str, str]]:
    """Every (module, function, template) a ``render_template`` literal names under src/admin."""
    found: set[tuple[str, str, str]] = set()
    for path in sorted(ADMIN_BLUEPRINTS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call) or not call.args:
                    continue
                name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
                if name != "render_template":
                    continue
                first = call.args[0]
                # A computed template name is outside this guard: it cannot be resolved
                # statically, and inventing a resolution would make the check lie.
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.add((path.relative_to(REPO_ROOT).as_posix(), node.name, first.value))
    return found


def _existing_templates() -> set[str]:
    return {p.relative_to(TEMPLATE_DIR).as_posix() for p in TEMPLATE_DIR.rglob("*.html")}


def test_every_rendered_admin_template_exists() -> None:
    """A route naming a template that is not in the tree is broken on every request."""
    existing = _existing_templates()
    missing = sorted(
        f"{module}::{function} renders {template!r}"
        for module, function, template in _rendered_templates()
        if template not in existing
    )
    assert not missing, (
        "these admin routes render a template that does not exist, so every request to them "
        "raises TemplateNotFound:\n  " + "\n  ".join(missing) + "\n\n"
        "Write the template, or delete the route. Do not rely on the caller's except clause to "
        "hide it — that is what kept the last two invisible."
    )


def test_the_guard_reads_a_non_empty_set() -> None:
    """A scan that finds nothing to check would pass for the wrong reason."""
    rendered = _rendered_templates()
    assert len(rendered) > 20, f"only {len(rendered)} render_template calls found under src/admin — the scan is broken"
    assert len(_existing_templates()) > 20, "no templates found — the scan is looking in the wrong place"
