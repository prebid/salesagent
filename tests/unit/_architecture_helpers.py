"""Shared helpers for AST-based structural guard tests.

Used by tests/unit/test_architecture_*.py. Centralizes:

- AST parsing with mtime-keyed cache (safe under pytest-xdist worker forks)
- Source-file iteration (src/, workflows, compose)
- Cross-file anchor-consistency assertions (D25)
- Stale-allowlist detection helper (D23)
- Standard failure-message formatter (D26)

PR 2 commit 8 introduces the baseline (parse_module, iter_call_expressions,
src_python_files, repo_root). PR 4 commit 1 extends with workflow iteration,
the assertion helpers, and the failure-message formatter.
"""

from __future__ import annotations

import ast
import functools
import re
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo-root anchor
# ---------------------------------------------------------------------------


def repo_root() -> Path:
    """Project root, computed once from this module's path."""
    return Path(__file__).resolve().parents[2]


# Module-level repo anchor (legacy ``_ast_helpers`` compat — import from here)
REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [REPO_ROOT / "src/core/tools", REPO_ROOT / "src/adapters"]


def rel(path: Path) -> str:
    """Return path relative to repo root for stable allowlist keys."""
    return str(path.relative_to(REPO_ROOT))


def safe_parse(filepath: Path) -> ast.Module | None:
    """Parse a Python file, returning None if missing or SyntaxError."""
    if not filepath.exists():
        return None
    try:
        return ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))
    except SyntaxError:
        return None


def iter_module_trees(scan_dirs: list[Path]) -> Iterator[tuple[ast.Module, str]]:
    """Yield ``(parsed_tree, repo_relative_path)`` for parseable ``.py`` under ``scan_dirs``."""
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for py_file in sorted(scan_dir.rglob("*.py")):
            if "__pycache__" in str(py_file):
                continue
            tree = safe_parse(py_file)
            if tree is not None:
                yield tree, rel(py_file)


def walk_with_enclosing_function(tree: ast.AST) -> Iterator[tuple[ast.AST, str]]:
    """Yield ``(node, enclosing_function_name)`` for every node in ``tree``."""

    def visit(node: ast.AST, func_name: str) -> Iterator[tuple[ast.AST, str]]:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            func_name = node.name
        yield node, func_name
        for child in ast.iter_child_nodes(node):
            yield from visit(child, func_name)

    yield from visit(tree, "<module>")


def collect_error_aliases(tree: ast.AST) -> set[str]:
    """Collect local names that alias the adcp ``Error`` class."""
    aliases: set[str] = {"Error"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if "error" not in module.split("."):
            continue
        for alias in node.names:
            if alias.name == "Error":
                aliases.add(alias.asname or alias.name)
    return aliases


# ---------------------------------------------------------------------------
# AST parsing — mtime-keyed cache
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=4096)
def _parse_cached(path_str: str, _mtime: float) -> ast.Module:
    return ast.parse(Path(path_str).read_text(encoding="utf-8"), filename=path_str)


def parse_module(path: Path) -> ast.Module:
    """Parse a Python file. Cache key is (path, mtime) so edits invalidate."""
    return _parse_cached(str(path), path.stat().st_mtime)


def base_expr_is_tenant(node: ast.expr) -> bool:
    """True when *node* is a tenant reference (``tenant``, ``self.tenant``, ``ctx.tenant``, …)."""
    if isinstance(node, ast.Name) and node.id == "tenant":
        return True
    return isinstance(node, ast.Attribute) and node.attr == "tenant"


def assert_detector_catches_ast_snippets(
    find_lineno_violations: Callable[[ast.Module], list[int]],
    *,
    snippets: dict[str, str],
) -> None:
    """Fail if an inline known-bad snippet is not flagged by the detector."""
    missed: list[str] = []
    for label, source in snippets.items():
        tree = ast.parse(source, filename=f"<known-bad:{label}>")
        if not find_lineno_violations(tree):
            missed.append(label)
    assert not missed, "Detector missed known-bad snippet(s):\n" + "\n".join(f"  {s}" for s in missed)


def find_tenant_config_violations(tree: ast.Module) -> list[int]:
    """Return line numbers of tenant.config / tenant['config'] access patterns."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "config" and base_expr_is_tenant(node.value):
            lines.append(node.lineno)
        elif isinstance(node, ast.Subscript) and base_expr_is_tenant(node.value):
            sl = node.slice
            if isinstance(sl, ast.Constant) and sl.value == "config":
                lines.append(node.lineno)
    return lines


def find_plain_json_column_violations(tree: ast.Module) -> list[int]:
    """Return line numbers of mapped_column/Column calls using plain JSON."""
    lines: list[int] = []
    for call in iter_call_expressions(tree):
        func_name: str | None = None
        if isinstance(call.func, ast.Name):
            func_name = call.func.id
        elif isinstance(call.func, ast.Attribute):
            func_name = call.func.attr
        if func_name not in {"Column", "mapped_column"} or not call.args:
            continue
        first_arg = call.args[0]
        uses_plain_json = (isinstance(first_arg, ast.Name) and first_arg.id == "JSON") or (
            isinstance(first_arg, ast.Attribute) and first_arg.attr == "JSON"
        )
        if uses_plain_json:
            lines.append(call.lineno)
    return lines


def iter_call_expressions(tree: ast.Module, name: str | None = None) -> Iterator[ast.Call]:
    """Yield Call nodes, optionally filtered by callable name."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if name is None:
            yield node
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id == name:
            yield node
        elif isinstance(f, ast.Attribute) and f.attr == name:
            yield node


# ---------------------------------------------------------------------------
# File iteration
# ---------------------------------------------------------------------------


def src_python_files(repo: Path) -> Iterator[Path]:
    """Every .py file under src/, excluding migrations and the legacy GAM file."""
    excluded = {repo / "src" / "adapters" / "google_ad_manager_original.py"}
    for p in (repo / "src").rglob("*.py"):
        if p in excluded:
            continue
        yield p


def iter_workflow_files(repo: Path) -> Iterator[Path]:
    """.yml and .yaml files in .github/workflows/."""
    wf_dir = repo / ".github" / "workflows"
    if not wf_dir.exists():
        return
    yield from sorted([*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")])


def iter_compose_files(repo: Path) -> Iterator[Path]:
    """docker-compose*.yml and compose.yaml at repo root."""
    yield from sorted([*repo.glob("docker-compose*.yml"), *repo.glob("compose.yaml")])


# ---------------------------------------------------------------------------
# Cross-surface version-anchor extractors (D24 + PR 5)
# ---------------------------------------------------------------------------

# Patterns intentionally permissive: surfaces use varied formats. Each returns
# a normalized version string ("3.12", "17", "0.11.7") via the first capture group.

_PY_VERSION_PATTERNS: list[tuple[str, str]] = [
    # Dockerfile / Dockerfile.* — `FROM python:3.12-slim` or `FROM python:3.12.4`
    (r"^\s*FROM\s+python:([0-9]+(?:\.[0-9]+)*)", "Dockerfile"),
    # pyproject.toml — `target-version = "py312"` (black/ruff)
    (r'^\s*target-version\s*=\s*["\']py([0-9]{2,3})["\']', "pyproject.toml"),
    # pyproject.toml — `python_version = "3.12"` (mypy section)
    (r'^\s*python_version\s*=\s*["\']?([0-9]+\.[0-9]+)', "pyproject.toml"),
    # pyproject.toml — `requires-python = ">=3.12"`
    (r'^\s*requires-python\s*=\s*["\']>=\s*([0-9]+\.[0-9]+)', "pyproject.toml"),
    # mypy.ini — `python_version = 3.12`
    (r"^\s*python_version\s*=\s*([0-9]+\.[0-9]+)", "mypy.ini"),
    # .python-version — bare version string
    (r"^\s*([0-9]+\.[0-9]+(?:\.[0-9]+)?)\s*$", ".python-version"),
    # tox.ini — `basepython = python3.12`
    (r"^\s*basepython\s*=\s*python([0-9]+\.[0-9]+)", "tox.ini"),
    # .github/workflows/*.yml + actions/*/action.yml — `python-version: '3.12'` or
    # `python-version: ['3.12', '3.13']` first match
    (r"^\s*python-version:\s*['\"]?([0-9]+\.[0-9]+)", ".yml"),
    (r"^\s*python-version:\s*['\"]?([0-9]+\.[0-9]+)", ".yaml"),
]


def iter_python_version_anchors(repo: Path) -> Iterator[tuple[Path, str]]:
    """Yield (file_path, version_string) for every Python version anchor across repo surfaces.

    Surfaces scanned: Dockerfile / Dockerfile.*, pyproject.toml (target-version, python_version,
    requires-python), .python-version, mypy.ini, tox.ini, .github/workflows/*.yml,
    .github/actions/*/action.yml. Returns one tuple per match (a file may have multiple).

    Used by `test_architecture_uv_version_anchor.py` (and python-anchor-consistency) to assert
    cross-file consistency under D24 + PR 5.
    """
    candidates: list[Path] = []
    candidates.extend(repo.glob("Dockerfile*"))
    for fname in ("pyproject.toml", "mypy.ini", "tox.ini", ".python-version"):
        p = repo / fname
        if p.exists():
            candidates.append(p)
    candidates.extend(iter_workflow_files(repo))
    actions_dir = repo / ".github" / "actions"
    if actions_dir.exists():
        candidates.extend(actions_dir.rglob("action.yml"))
        candidates.extend(actions_dir.rglob("action.yaml"))

    for path in candidates:
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for pattern, suffix_hint in _PY_VERSION_PATTERNS:
            if not str(path).endswith(suffix_hint):
                continue
            for m in re.finditer(pattern, text, flags=re.MULTILINE):
                version = m.group(1)
                # Normalize "py312" → "3.12" for black/ruff target-version values
                if pattern.startswith(r"^\s*target-version"):
                    if len(version) == 3:  # "312"
                        version = f"{version[0]}.{version[1:]}"
                    elif len(version) == 2:  # "312" via 2-digit major
                        version = f"{version[0]}.{version[1]}"
                yield path, version


_PG_IMAGE_PATTERNS: tuple[str, ...] = (
    # Dockerfile / Dockerfile.* — `FROM postgres:17-alpine`
    r"^\s*FROM\s+postgres:([0-9]+(?:\.[0-9]+)?(?:-[a-z0-9.]+)?)",
    # docker-compose*.yml + .github/workflows/*.yml — `image: postgres:17-alpine`
    r"^\s+image:\s+postgres:([0-9]+(?:\.[0-9]+)?(?:-[a-z0-9.]+)?)",
    # tests/conftest*.py — `"postgres:17-alpine"` or 'postgres:17'
    r'["\']postgres:([0-9]+(?:\.[0-9]+)?(?:-[a-z0-9.]+)?)["\']',
)


def iter_postgres_image_refs(repo: Path) -> Iterator[tuple[Path, str]]:
    """Yield (file_path, image_tag) for every postgres image reference across repo surfaces.

    Surfaces scanned: Dockerfile / Dockerfile.*, docker-compose*.yml, .github/workflows/*.yml,
    tests/conftest*.py. The yielded `image_tag` is the part after `postgres:` (e.g.,
    "17-alpine", "17.9", "16").

    Used by structural guards to assert cross-file Postgres-version consistency under PR 5.
    """
    candidates: list[Path] = []
    candidates.extend(repo.glob("Dockerfile*"))
    candidates.extend(iter_compose_files(repo))
    candidates.extend(iter_workflow_files(repo))
    tests_dir = repo / "tests"
    if tests_dir.exists():
        candidates.extend(tests_dir.rglob("conftest*.py"))

    for path in candidates:
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in _PG_IMAGE_PATTERNS:
            for m in re.finditer(pattern, text, flags=re.MULTILINE):
                yield path, m.group(1)


# ---------------------------------------------------------------------------
# Allowlist + stale-detection helper (D23)
# ---------------------------------------------------------------------------


def assert_violations_match_allowlist(
    found: set[tuple],
    allowlist: set[tuple],
    *,
    fix_hint: str = "",
) -> None:
    """Assert that the actual-found set equals the allowlist.

    Raises with two distinct error modes:
    - "new violations" — entries in `found` not in `allowlist`
    - "stale entries" — entries in `allowlist` not in `found`

    Both modes can fire in one assertion if the allowlist is wrong AND new
    violations exist. The message lists each separately.
    """
    new = found - allowlist
    stale = allowlist - found
    if not new and not stale:
        return

    parts: list[str] = []
    if new:
        parts.append(f"new violations ({len(new)}) — fix or add to allowlist:")
        parts.extend(f"  {v}" for v in sorted(new))
    if stale:
        parts.append(f"stale entries ({len(stale)}) — violation fixed, remove from allowlist:")
        parts.extend(f"  {v}" for v in sorted(stale))
    if fix_hint:
        parts.append("")
        parts.append(fix_hint)
    raise AssertionError("\n".join(parts))


# ---------------------------------------------------------------------------
# Cross-file anchor consistency (D25)
# ---------------------------------------------------------------------------


def assert_anchor_consistency(
    sources: Iterable[tuple[Path, str]],
    pattern_map: dict[str, str],
    *,
    label: str,
) -> None:
    """Assert that every source extracts the same anchor value.

    `sources` is an iterable of (path, text) tuples. `pattern_map` maps a path
    string (or path-suffix that endswith-matches) to a regex with one capture
    group that extracts the anchor value (e.g., "3.12").

    Raises AssertionError("drift: ...") if any pair of extracted values differ.
    """
    values: dict[Path, str] = {}
    for path, text in sources:
        pattern = None
        for path_key, pat in pattern_map.items():
            if str(path).endswith(path_key) or str(path) == path_key:
                pattern = pat
                break
        if pattern is None:
            raise AssertionError(f"{label} anchor: no pattern for {path}")
        m = re.search(pattern, text, flags=re.MULTILINE)
        if not m:
            raise AssertionError(f"{label} anchor: pattern {pattern!r} did not match in {path}")
        values[path] = m.group(1)

    distinct = set(values.values())
    if len(distinct) > 1:
        rendered = "\n".join(f"  {p}: {v}" for p, v in sorted(values.items()))
        raise AssertionError(f"{label} drift across sources:\n{rendered}")


# ---------------------------------------------------------------------------
# Failure-message formatter (D26)
# ---------------------------------------------------------------------------


def format_failure(
    *,
    summary: str,
    violations: list[str],
    fix_hint: str | None = None,
    docs_link: str | None = None,
) -> str:
    """Standard structure for guard failure messages.

    summary       — one-line description of what's wrong
    violations    — list of "<file>:<line>: <detail>" lines
    fix_hint      — optional one-paragraph remediation
    docs_link     — optional path/URL to the relevant pattern doc
    """
    parts: list[str] = [summary, ""]
    parts.extend(f"  {v}" for v in violations)
    if fix_hint:
        parts.extend(["", fix_hint])
    if docs_link:
        parts.extend(["", f"See: {docs_link}"])
    return "\n".join(parts)
