#!/usr/bin/env python3
"""Pre-commit hook to detect deprecated URL patterns in JavaScript.

Rewritten at L0-28 for the v2.0 FastAPI migration. Under FastAPI the
``scriptRoot`` / ``request.script_root`` pattern is deprecated — the
v2.0 canonical pattern is:

  - Templates emit URLs via ``{{ url_for('admin_<route_name>', ...) }}``
    into ``data-*`` attributes on DOM elements.
  - JavaScript reads the URL from ``document.body.dataset.*`` (or a
    specific element's ``.dataset.*``).
  - Static assets are referenced via ``{{ url_for('static', path=...) }}``.

See ``docs/deployment/static-js-urls.md`` for the full target pattern
and the L1c/L1d migration boundary.

This hook rejects:

  * Declaration of a ``scriptRoot`` variable.
  * Inline ``request.script_root`` template references.
  * Hardcoded ``fetch('/api/...')`` / ``/auth/...`` / ``/tenant/...``
    paths that do NOT read from a ``dataset.*`` source.

It accepts:

  * URLs sourced from ``document.dataset.*`` / ``element.dataset.*``.
  * JS files with no URL references at all.
  * ``url_for(...)`` calls in embedded JSX/template literals (rare).

Pre-commit runs this hook only on CHANGED files. Pre-existing JS files
with ``scriptRoot`` are NOT swept during L0 (per L0-implementation-plan
§7.5 RATIFIED — L0 is doc-only); L1c/L1d router PRs each clean up the
``scriptRoot`` in the JS files they own.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Patterns that constitute v1 scriptRoot usage or hardcoded URLs.
# Each entry: (compiled regex, human-readable reason)
DEPRECATED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\b(?:const|let|var)\s+scriptRoot\b"),
        "`scriptRoot` variable is deprecated in v2.0 — read URLs from `data-*` attributes instead.",
    ),
    (
        re.compile(r"scriptRoot\s*[\+|]"),
        "`scriptRoot` string concatenation is deprecated in v2.0 — read URLs from `data-*` attributes instead.",
    ),
    (
        re.compile(r"\{\{\s*request\.script_root\s*\}\}"),
        "`request.script_root` template variable is deprecated in v2.0 — emit URLs via `url_for(...)` into `data-*` attributes.",
    ),
    (
        re.compile(r"\brequest\.script_root\b"),
        "`request.script_root` is deprecated in v2.0 — emit URLs via `url_for(...)` into `data-*` attributes.",
    ),
    (
        re.compile(r"""fetch\s*\(\s*['"`]/(api|auth|tenant|admin)/"""),
        "Hardcoded URL in `fetch(...)` — read the URL from a `data-*` attribute emitted by `url_for(...)`.",
    ),
    (
        re.compile(r"""window\.location\.href\s*=\s*['"`]/(api|auth|tenant|admin)/"""),
        "Hardcoded URL in `window.location.href = ...` — read the URL from a `data-*` attribute emitted by `url_for(...)`.",
    ),
]

# Patterns that are explicitly ACCEPTED — line-level overrides for
# DEPRECATED_PATTERNS matches that appear alongside an accepted source.
ACCEPTED_PATTERNS: list[re.Pattern[str]] = [
    # Reading from dataset.* / data-* is the v2.0 canonical pattern.
    re.compile(r"\.dataset\."),
    re.compile(r"\bgetAttribute\s*\(\s*['\"`]data-"),
    # url_for() references (inside template-literal JS).
    re.compile(r"\burl_for\s*\("),
]

# Comment-only lines (JS ``//`` or ``/* ... */``) — never a violation.
COMMENT_LINE = re.compile(r"^\s*(//|/\*|\*)")


def _line_is_accepted(line: str) -> bool:
    if COMMENT_LINE.match(line):
        return True
    return any(p.search(line) for p in ACCEPTED_PATTERNS)


def check_file(filepath: Path) -> list[tuple[int, str, str]]:
    """Return ``[(lineno, line, reason), ...]`` for every deprecated hit in the file."""
    violations: list[tuple[int, str, str]] = []
    try:
        content = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Error reading {filepath}: {exc}", file=sys.stderr)
        return violations

    for lineno, line in enumerate(content.splitlines(), start=1):
        if _line_is_accepted(line):
            continue
        for pattern, reason in DEPRECATED_PATTERNS:
            if pattern.search(line):
                violations.append((lineno, line.strip(), reason))
                break
    return violations


def main(filenames: list[str]) -> int:
    """Return 0 if clean, 1 if any file has a deprecated pattern."""
    all_violations: list[tuple[Path, list[tuple[int, str, str]]]] = []
    for filename in filenames:
        path = Path(filename)
        if not path.exists() or path.is_dir():
            continue
        violations = check_file(path)
        if violations:
            all_violations.append((path, violations))

    if not all_violations:
        return 0

    print("Deprecated URL pattern(s) in JavaScript/template files.")
    print("v2.0 canonical pattern: `url_for(...)` → `data-*` attribute → `element.dataset.*` in JS.")
    print("See docs/deployment/static-js-urls.md for the full migration guide.\n")

    for path, violations in all_violations:
        print(f"{path}:")
        for lineno, line, reason in violations:
            print(f"  Line {lineno}: {reason}")
            print(f"    {line}")
        print()

    print("Target pattern:")
    print("  <!-- template -->")
    print('  <body data-api-url="{{ url_for(\'admin_api_list\', tenant_id=tenant.id) }}">')
    print()
    print("  // JS")
    print("  const apiUrl = document.body.dataset.apiUrl;")
    print("  fetch(apiUrl);")

    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
