#!/usr/bin/env python3
"""Re-verify every numeric claim in the Flask→FastAPI v2.0.0 migration plan.

Run at each layer entry/exit to detect drift between plan text and live codebase.

Usage:
  python scripts/audit_migration_counts.py                  # print markdown table
  python scripts/audit_migration_counts.py > counts-L0.md   # save snapshot

Reference: Agent ε numeric re-count + file:line verification (2026-04-17 audit).
Canonical counts documented in .claude/notes/flask-to-fastapi/ docs; drift
between this script's output and plan text should trigger a doc-patch PR.
"""
from __future__ import annotations

import re
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def count_matches(pattern: str, paths: list[Path], regex_flags: int = 0) -> int:
    """Count total occurrences of `pattern` across files rooted at each path.

    Patterns match per-line (re.MULTILINE). Files that fail to read are skipped.
    """
    rx = re.compile(pattern, regex_flags | re.MULTILINE)
    total = 0
    for root in paths:
        if not root.exists():
            continue
        if root.is_file():
            files = [root]
        else:
            files = [
                p
                for p in root.rglob("*")
                if p.is_file() and p.suffix in {".py", ".html", ".js", ".ts", ".jinja", ".jinja2"}
            ]
        for p in files:
            try:
                text = p.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            total += len(rx.findall(text))
    return total


def count_files(pattern: str, paths: list[Path]) -> int:
    """Count files matching pattern at all."""
    rx = re.compile(pattern, re.MULTILINE)
    count = 0
    for root in paths:
        if not root.exists():
            continue
        files = [p for p in root.rglob("*") if p.is_file() and p.suffix == ".py"]
        for p in files:
            try:
                if rx.search(p.read_text()):
                    count += 1
            except (OSError, UnicodeDecodeError):
                continue
    return count


def count_files_globbed(glob: str, base: Path) -> int:
    return len(list(base.glob(glob)))


def count_template_files() -> int:
    tdir = REPO / "templates"
    if not tdir.exists():
        return 0
    return sum(1 for p in tdir.rglob("*") if p.is_file())


def main() -> int:
    datestamp = datetime.now(UTC).strftime("%Y-%m-%d")

    templates = [REPO / "templates"]
    static = [REPO / "static"]
    src = [REPO / "src"]
    src_admin_blueprints = [REPO / "src" / "admin" / "blueprints"]
    src_admin = [REPO / "src" / "admin"]
    src_core_tools = [REPO / "src" / "core" / "tools"]
    models_py = REPO / "src" / "core" / "database" / "models.py"

    print(f"# Migration count snapshot — {datestamp}")
    print()
    print("| # | Claim | Count | Pattern |")
    print("|---|---|---:|---|")

    rows = [
        (
            "A1",
            "script_root template refs",
            count_matches(r"request\.script_root|request\.script_name", templates),
            "`request\\.script_root|request\\.script_name` in templates/",
        ),
        (
            "A2",
            "url_for call sites in templates",
            count_matches(r"\{\{\s*url_for", templates),
            "`{{ url_for` in templates/",
        ),
        ("A3", "Template files", count_template_files(), "files under templates/"),
        (
            "A4",
            "flash() in src/admin/blueprints",
            count_matches(r"\bflash\s*\(", src_admin_blueprints),
            "`\\bflash\\s*\\(` in src/admin/blueprints/",
        ),
        (
            "A5",
            "redirect() in src/admin/blueprints",
            count_matches(r"\bredirect\s*\(", src_admin_blueprints),
            "`\\bredirect\\s*\\(` in src/admin/blueprints/",
        ),
        ("A6", "<form> tags in templates", count_matches(r"<form\b", templates), "`<form\\b` in templates/"),
        ("A7a", "fetch() in templates", count_matches(r"fetch\s*\(", templates), "`fetch\\s*\\(` in templates/"),
        ("A7b", "fetch() in static/", count_matches(r"fetch\s*\(", static), "`fetch\\s*\\(` in static/"),
        (
            "A8",
            "Blueprint .py files (incl __init__)",
            count_files_globbed("*.py", REPO / "src" / "admin" / "blueprints"),
            "src/admin/blueprints/*.py",
        ),
        (
            "A9",
            "Admin route decorators",
            count_matches(r"@\w+\.route\(", src_admin),
            "`@\\w+\\.route\\(` in src/admin/",
        ),
        (
            "A10",
            "Flask-importing files in src/",
            count_files(r"^(from|import)\s+flask\b", src),
            "files with `^(from|import) flask` in src/",
        ),
        (
            "A11",
            "session.query() sites (should be 0)",
            count_matches(r"session\.query\(", src),
            "`session\\.query\\(` in src/",
        ),
        (
            "A13",
            "request.form call sites",
            count_matches(r"request\.form", src_admin_blueprints),
            "`request\\.form` in src/admin/blueprints/",
        ),
        (
            "A14",
            "request.args call sites",
            count_matches(r"request\.args", src_admin_blueprints),
            "`request\\.args` in src/admin/blueprints/",
        ),
        (
            "A15",
            "request.files call sites",
            count_matches(r"request\.files", src_admin_blueprints),
            "`request\\.files` in src/admin/blueprints/",
        ),
        (
            "A16",
            "print() statements (line-start only)",
            count_matches(r"^(\s+)?print\(", src),
            "`^(\\s+)?print\\(` in src/",
        ),
        ("A17", "os.environ.get() sites", count_matches(r"os\.environ\.get", src), "`os\\.environ\\.get` in src/"),
        (
            "A18",
            "logging.getLogger() sites",
            count_matches(r"logging\.getLogger\(", src),
            "`logging\\.getLogger\\(` in src/",
        ),
    ]

    if models_py.exists():
        models_text = models_py.read_text()
        rows += [
            (
                "A19",
                "relationship() in models.py",
                len(re.findall(r"relationship\(", models_text)),
                "`relationship\\(` in models.py",
            ),
            (
                "A20a",
                "back_populates= in models.py",
                len(re.findall(r"back_populates=", models_text)),
                "`back_populates=` in models.py",
            ),
            (
                "A20b",
                "backref= in models.py",
                len(re.findall(r"backref\s*=", models_text)),
                "`backref\\s*=` in models.py",
            ),
            (
                "A21",
                "server_default= in models.py",
                len(re.findall(r"server_default=", models_text)),
                "`server_default=` in models.py",
            ),
        ]

    rows += [
        (
            "A22",
            "get_db_session() sites in src/",
            count_matches(r"get_db_session\(\)", src),
            "`get_db_session\\(\\)` in src/",
        ),
        (
            "A23",
            "scoped_session refs in src/ (should → 0 at L0)",
            count_matches(r"scoped_session", src),
            "`scoped_session` in src/",
        ),
        (
            "A25",
            ".log_operation() call sites",
            count_matches(r"\.log_operation\(", src),
            "`\\.log_operation\\(` in src/",
        ),
        (
            "A26",
            "threading.Thread sites in src/",
            count_matches(r"threading\.Thread", src),
            "`threading\\.Thread` in src/",
        ),
        (
            "A27",
            "adapter.* call sites in src/core/tools/",
            count_matches(r"adapter\.", src_core_tools),
            "`adapter\\.` in src/core/tools/",
        ),
    ]

    for label, claim, count, cmd in rows:
        print(f"| {label} | {claim} | {count} | {cmd} |")

    # Per-blueprint breakdown
    print()
    print("## Per-blueprint breakdown (routes × LOC × flash × redirect × request.form)")
    print()
    print("| Blueprint | Routes | LOC | flash() | redirect() | request.form |")
    print("|---|---:|---:|---:|---:|---:|")
    bp_dir = REPO / "src" / "admin" / "blueprints"
    if bp_dir.exists():
        for bp in sorted(bp_dir.glob("*.py")):
            if bp.name == "__init__.py":
                continue
            try:
                text = bp.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            routes = len(re.findall(r"@\w+\.route\(", text))
            loc = text.count("\n") + 1
            flashes = len(re.findall(r"\bflash\s*\(", text))
            redirects = len(re.findall(r"\bredirect\s*\(", text))
            forms = len(re.findall(r"request\.form", text))
            print(f"| {bp.name} | {routes} | {loc} | {flashes} | {redirects} | {forms} |")

    print()
    print("## Pool + concurrency config")
    print()
    print("- Direct-PG pool: pool_size=10, max_overflow=20 (src/core/database/database_session.py:124-125)")
    print("- PgBouncer pool: pool_size=2,  max_overflow=5  (src/core/database/database_session.py:108-109)")
    print("- anyio default threadpool: 40 tokens")
    print("- Proposed threadpool bump (Decision 1): 80 tokens (env: ADCP_THREADPOOL_TOKENS)")
    print()
    print("---")
    print()
    print(f"*Generated by `scripts/audit_migration_counts.py` on {datestamp}.*")
    print("*Commit output to `docs/migration/counts-<layer>.md` at each layer entry/exit for drift tracking.*")

    return 0


if __name__ == "__main__":
    sys.exit(main())
