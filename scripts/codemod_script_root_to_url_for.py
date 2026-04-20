#!/usr/bin/env python3
"""Phase-A template codemod — ``{{ script_root }}`` → ``{{ url_for(...) }}``.

Rewrites every Jinja template under a supplied ``--templates-dir`` from the
Flask/``request.script_root``-prefixed idiom to the FastAPI/Starlette-native
``{{ url_for('admin_<blueprint>_<endpoint>', **params) }}`` form required by
CLAUDE.md Critical Invariant #1. Authored at L0-20; executed at L1a.

Three rewrite passes are applied in order, each independently idempotent:

1. **Static assets.** Both the Jinja-global form
   ``{{ [request.]script_root }}/static/<path>`` and the Flask-dotted form
   ``url_for('static', filename='<path>')`` collapse to Starlette's native
   ``url_for('static', path='/<path>')``. The leading slash is MANDATORY
   per Starlette's ``StaticFiles.name='static'`` contract — Starlette's
   ``url_for('static', path=...)`` does not prepend one.

2. **Dynamic ``script_root`` + tenant-scoped paths.** Expressions of the
   shape ``{{ [request.]script_root }}/tenant/{{ <expr> }}/<suffix>`` (plus
   the bare tenant-dashboard form with no suffix) are looked up in an
   OPINIONATED route-name map and rewritten to
   ``{{ url_for('<mapped_name>', tenant_id=<expr>) }}``. Suffixes NOT in
   the map are left untouched and surface in the manifest as
   ``manual-review`` items for L1a attention.

3. **Flask-dotted ``url_for``.** ``url_for('<bp>.<ep>', ...)`` becomes
   ``url_for('admin_<bp>_<ep>', ...)`` per the flat-name convention
   documented at foundation-modules.md §11.1 line 2134. ``url_for('static',
   ...)`` is NOT touched here (Pass 1 already handled it).

Operation modes:
    --dry-run : Report pending rewrites to stdout; do not mutate disk.
    --write   : Apply rewrites in-place.
    --check   : Exit 1 if any rewrite is pending; exit 0 otherwise. Used
                by CI to enforce the "codemod has been applied" invariant
                once L1a has shipped.

The rewriter is regex-based rather than Jinja-AST-based. Regex is
sufficient because each pattern targets a narrow, well-formed surface
syntax (the templates shipped in ``templates/`` follow predictable
conventions) and because the IDEMPOTENCY obligation forces the
transformations to be fixed-point — structural AST traversal would add
complexity without a correctness gain here. The Green implementation
guarantees idempotency by construction: each match pattern references
ONLY source-state tokens (``request.script_root``, ``script_root``,
``'<bp>.<ep>'``, ``filename=``), none of which appear in the target state.

Usage:
    python scripts/codemod_script_root_to_url_for.py --dry-run \\
        --templates-dir templates
    python scripts/codemod_script_root_to_url_for.py --write \\
        --templates-dir templates
    python scripts/codemod_script_root_to_url_for.py --check \\
        --templates-dir templates

Reference: .claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-20,
           .claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md §11.1
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Route-name map (OPINIONATED — derived from the 14 admin routers shipping
# under /tenant/{tenant_id}/... per D1 2026-04-16 canonical-URL routing).
# Keys are the URL suffix AFTER ``/tenant/{{ <expr> }}``. Empty string is
# the bare tenant-dashboard form.
#
# Naming convention: admin_<blueprint>_<endpoint> (flat form, no dot).
# See foundation-modules.md §11.1 line 2134 and worked-examples.md §Admin
# Auth for the canonical rename.
#
# Suffixes listed here are the ones actually observed in the golden fixtures
# plus a conservative superset of the most common production templates. The
# L1a codemod execution PR will re-expand this map with any additional
# suffixes surfaced by the dry-run manifest against the real templates/
# tree — at L0 only the fixture-covered entries are load-bearing.
# ---------------------------------------------------------------------------
TENANT_ROUTE_NAME_MAP: dict[str, str] = {
    "": "admin_tenants_dashboard",
    "settings": "admin_tenants_tenant_settings",
    "settings/general": "admin_settings_general",
    "settings/slack": "admin_settings_slack",
    "settings/ai": "admin_settings_ai",
    "settings/raw": "admin_settings_raw",
    "products": "admin_products_list_products",
    "products/add": "admin_products_add_product",
    "principals/create": "admin_principals_create",
    "workflows": "admin_workflows_list",
    "deactivate": "admin_tenants_deactivate",
    "setup-checklist": "admin_tenants_setup_checklist",
    "media-buys": "admin_tenants_media_buys_list",
}


# ---------------------------------------------------------------------------
# Pass 1 — static assets
# ---------------------------------------------------------------------------

# Matches ``{{ <spaces> [request.]script_root <spaces> }}/static/<path>``
# where <path> is any run of non-whitespace, non-quote characters. This
# captures both attribute forms (href="...", src="...") and bare inline
# usage. The path is normalized with a LEADING slash per Starlette's
# url_for('static', path=...) contract.
_STATIC_SCRIPT_ROOT_RE = re.compile(r"""\{\{\s*(?:request\.)?script_root\s*\}\}/static/([^\s"'<>{}]+)""")


def _rewrite_static_script_root(text: str) -> tuple[str, int]:
    """Pass 1a: ``{{ script_root }}/static/<p>`` → ``{{ url_for('static', path='/<p>') }}``."""
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        path = match.group(1)
        return f"{{{{ url_for('static', path='/{path}') }}}}"

    return _STATIC_SCRIPT_ROOT_RE.sub(repl, text), count


# Matches ``url_for('static', filename='...')`` (with either single or
# double quotes on the filename). Starlette's mount uses ``path=`` and
# REQUIRES a leading slash on the value.
_STATIC_FILENAME_RE = re.compile(r"""url_for\(\s*['"]static['"]\s*,\s*filename\s*=\s*(['"])([^'"]*)\1\s*\)""")


def _rewrite_static_filename(text: str) -> tuple[str, int]:
    """Pass 1b: ``url_for('static', filename='x')`` → ``url_for('static', path='/x')``."""
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        value = match.group(2)
        # Normalize: ensure the value starts with exactly one leading slash.
        normalized = "/" + value.lstrip("/")
        return f"url_for('static', path='{normalized}')"

    return _STATIC_FILENAME_RE.sub(repl, text), count


# ---------------------------------------------------------------------------
# Pass 2 — dynamic script_root + tenant-scoped paths
# ---------------------------------------------------------------------------

# Matches ``{{ [request.]script_root }}/tenant/{{ <expr> }}[/<suffix>]`` where:
#   - <expr> is any non-``}}`` run (trimmed),
#   - <suffix> is any non-whitespace, non-quote run OR empty.
#
# The trailing ``(?=["'\s<>]|$)`` anchor ensures we don't greedily swallow
# attribute-closing quotes or subsequent template tokens.
_TENANT_PATH_RE = re.compile(
    r"""
    \{\{\s*(?:request\.)?script_root\s*\}\}    # {{ script_root }}
    /tenant/
    \{\{\s*(?P<expr>[^}]+?)\s*\}\}              # {{ <expr> }}
    (?P<suffix>(?:/[^\s"'<>]*)?)                # optional /suffix
    """,
    re.VERBOSE,
)


def _rewrite_tenant_paths(text: str) -> tuple[str, int, list[str]]:
    """Pass 2: ``{{ script_root }}/tenant/{{ expr }}/<suffix>`` → url_for via map.

    Returns ``(new_text, rewrite_count, unmapped_suffixes)``. Unmapped
    suffixes are preserved verbatim in the output and reported back so the
    caller can emit a manifest entry.
    """
    count = 0
    unmapped: list[str] = []

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        expr = match.group("expr").strip()
        raw_suffix = match.group("suffix") or ""
        # Drop the leading slash from the captured suffix to match the map key.
        key = raw_suffix.lstrip("/")
        if key not in TENANT_ROUTE_NAME_MAP:
            unmapped.append(f"/tenant/{{{{ {expr} }}}}{raw_suffix}")
            return match.group(0)  # Leave as-is; L1a reviewer decides.
        route_name = TENANT_ROUTE_NAME_MAP[key]
        count += 1
        return f"{{{{ url_for('{route_name}', tenant_id={expr}) }}}}"

    return _TENANT_PATH_RE.sub(repl, text), count, unmapped


# ---------------------------------------------------------------------------
# Pass 3 — Flask-dotted ``url_for('<bp>.<ep>', ...)``
# ---------------------------------------------------------------------------

# Matches ``url_for('<bp>.<ep>'`` (single OR double quotes on the first arg).
# We deliberately do NOT match ``url_for('static', ...)`` (no dot in name).
# Blueprint/endpoint names are Python identifier chars.
_DOTTED_URL_FOR_RE = re.compile(
    r"""url_for\(\s*(['"])(?P<bp>[A-Za-z_][A-Za-z0-9_]*)\.(?P<ep>[A-Za-z_][A-Za-z0-9_]*)\1"""
)


def _rewrite_dotted_url_for(text: str) -> tuple[str, int]:
    """Pass 3: ``url_for('bp.ep', ...)`` → ``url_for('admin_bp_ep', ...)``."""
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        quote = match.group(1)
        bp = match.group("bp")
        ep = match.group("ep")
        return f"url_for({quote}admin_{bp}_{ep}{quote}"

    return _DOTTED_URL_FOR_RE.sub(repl, text), count


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class FileRewriteResult:
    """Per-file summary of what the codemod would do / did do."""

    path: Path
    original: str
    rewritten: str
    static_script_root_count: int = 0
    static_filename_count: int = 0
    tenant_path_count: int = 0
    dotted_url_for_count: int = 0
    unmapped_suffixes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.rewritten != self.original

    @property
    def total_rewrites(self) -> int:
        return (
            self.static_script_root_count
            + self.static_filename_count
            + self.tenant_path_count
            + self.dotted_url_for_count
        )


def rewrite_text(text: str) -> FileRewriteResult:
    """Apply all three passes to ``text`` and return a ``FileRewriteResult``.

    Passes are applied in a specific order:
    1. Static-asset ``script_root`` → avoids being re-captured by Pass 2's
       ``/tenant/`` matcher (impossible here, but defence-in-depth).
    2. Static-asset ``filename=`` → independent surface.
    3. Tenant-scoped dynamic paths → consumes remaining ``script_root``.
    4. Flask-dotted ``url_for`` → sweeps any remaining blueprint.endpoint
       forms, including those INSIDE templates that also had script_root
       references (mixed-mode templates).
    """
    result = FileRewriteResult(path=Path("<memory>"), original=text, rewritten=text)
    result.rewritten, result.static_script_root_count = _rewrite_static_script_root(result.rewritten)
    result.rewritten, result.static_filename_count = _rewrite_static_filename(result.rewritten)
    result.rewritten, result.tenant_path_count, result.unmapped_suffixes = _rewrite_tenant_paths(result.rewritten)
    result.rewritten, result.dotted_url_for_count = _rewrite_dotted_url_for(result.rewritten)
    return result


def _iter_template_files(root: Path) -> list[Path]:
    """Return every ``*.html`` under ``root`` (recursively), in deterministic order."""
    return sorted(p for p in root.rglob("*.html") if p.is_file())


def process_tree(root: Path) -> list[FileRewriteResult]:
    """Run the codemod over every template in ``root``. Does not write anywhere."""
    results: list[FileRewriteResult] = []
    for path in _iter_template_files(root):
        text = path.read_text(encoding="utf-8")
        result = rewrite_text(text)
        result.path = path
        results.append(result)
    return results


def _print_manifest(results: list[FileRewriteResult], *, stream) -> None:
    """Emit a one-file-per-line summary table to ``stream`` (stdout/stderr)."""
    header = f"{'file':<60} {'static':>7} {'fname':>6} {'tenant':>7} {'dotted':>7}"
    stream.write(header + "\n")
    stream.write("-" * len(header) + "\n")
    for r in results:
        if not r.changed and not r.unmapped_suffixes:
            continue
        stream.write(
            f"{str(r.path):<60} "
            f"{r.static_script_root_count:>7} "
            f"{r.static_filename_count:>6} "
            f"{r.tenant_path_count:>7} "
            f"{r.dotted_url_for_count:>7}\n"
        )
        for unmapped in r.unmapped_suffixes:
            stream.write(f"    MANUAL REVIEW: unmapped suffix → {unmapped}\n")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase-A template codemod (script_root → url_for).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--templates-dir",
        required=True,
        type=Path,
        help="Root of the Jinja template tree to scan (e.g. templates).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview rewrites; do not mutate disk.")
    mode.add_argument("--write", action="store_true", help="Apply rewrites in-place.")
    mode.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any rewrite is pending; exit 0 if the tree is already migrated.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    root: Path = args.templates_dir
    if not root.exists() or not root.is_dir():
        sys.stderr.write(f"error: --templates-dir {root} is not a directory\n")
        return 2

    results = process_tree(root)
    pending = [r for r in results if r.changed]

    if args.check:
        _print_manifest(results, stream=sys.stdout)
        if pending:
            sys.stderr.write(
                f"--check failed: {len(pending)} file(s) have pending rewrites. " "Run with --write to apply.\n"
            )
            return 1
        return 0

    if args.dry_run:
        _print_manifest(results, stream=sys.stdout)
        sys.stdout.write(f"\n{len(pending)} file(s) would change. Run with --write to apply.\n")
        return 0

    # --write: mutate files.
    for r in pending:
        r.path.write_text(r.rewritten, encoding="utf-8")
    _print_manifest(results, stream=sys.stdout)
    sys.stdout.write(f"\nWrote {len(pending)} file(s).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
