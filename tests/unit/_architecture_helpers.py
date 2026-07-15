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
import os
import re
import subprocess
import warnings
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Repo-root anchor
# ---------------------------------------------------------------------------


def repo_root() -> Path:
    """Project root, computed once from this module's path."""
    return Path(__file__).resolve().parents[2]


# Module-level repo anchor
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


def _base_expr_is_tenant(node: ast.expr) -> bool:
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
        if isinstance(node, ast.Attribute) and node.attr == "config" and _base_expr_is_tenant(node.value):
            lines.append(node.lineno)
        elif isinstance(node, ast.Subscript) and _base_expr_is_tenant(node.value):
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


def iter_call_expressions(tree: ast.AST, name: str | None = None) -> Iterator[ast.Call]:
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


def select_call_model_name(call: ast.Call) -> str | None:
    """Model name from a raw ``select(Model)`` call expression, else None.

    Matches only bare-name ``select(...)`` calls (not attribute access) and
    resolves the first argument whether written as ``Model`` or ``mod.Model``.

    NOTE: this resolves the leading attribute name, so a COLUMN-select
    ``select(Model.column)`` resolves to ``"column"`` (the attr), not the model —
    i.e. column-selects of ORM models are invisible to callers that match the
    result against ORM model names. Use ``select_target_model_name`` when
    column-selects of ORM models must also be detected.
    """
    if not (isinstance(call.func, ast.Name) and call.func.id == "select") or not call.args:
        return None
    model_arg = call.args[0]
    if isinstance(model_arg, ast.Name):
        return model_arg.id
    if isinstance(model_arg, ast.Attribute):
        return model_arg.attr
    return None


def select_target_model_name(call: ast.Call, model_names: set[str]) -> str | None:
    """ORM model targeted by ``select(...)`` — including ``select(Model.column)``.

    Closes the blind spot in ``select_call_model_name``: a column-select of an ORM
    model (``select(Model.tenant_id)``) is a raw model query just as much as
    ``select(Model)`` is, but the bare resolver returns the column name and misses
    it. This resolver disambiguates against ``model_names``:
      * ``select(Model)`` → ``Model`` (if a model);
      * ``select(pkg.Model)`` → ``Model`` (attr is the model);
      * ``select(Model.column)`` → ``Model`` (value is the model — a column-select).
    Returns the model name when the select targets an ORM model, else None.
    """
    if not (isinstance(call.func, ast.Name) and call.func.id == "select") or not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Name):
        return arg.id if arg.id in model_names else None
    if isinstance(arg, ast.Attribute):
        if arg.attr in model_names:  # select(pkg.Model)
            return arg.attr
        if isinstance(arg.value, ast.Name) and arg.value.id in model_names:  # select(Model.column)
            return arg.value.id
    return None


def extract_select_calls(
    source_path: Path,
    func_name: str,
    class_name: str | None = None,
    model_predicate: Callable[[str], bool] | None = None,
) -> list[dict[str, Any]]:
    """Extract ``select(Model)`` call info from a function or method using AST.

    Args:
        source_path: Absolute path to the Python source file.
        func_name: Function or method name to scan.
        class_name: If set, look for the method inside this class only.
        model_predicate: If set, only keep calls whose model name satisfies it.

    Returns:
        List of dicts with keys ``model``, ``has_tenant_filter``, ``lineno``.
        ``has_tenant_filter`` is True when "tenant_id" appears in the source
        lines from the select() call through the next ten lines (the full
        statement can span multiple lines).
    """
    source_text = source_path.read_text()
    tree = ast.parse(source_text)
    lines = source_text.splitlines()

    target_nodes: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if class_name is not None:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                target_nodes.extend(
                    child
                    for child in ast.walk(node)
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == func_name
                )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            target_nodes.append(node)

    results: list[dict[str, Any]] = []
    for func_node in target_nodes:
        for call in iter_call_expressions(func_node):
            model_name = select_call_model_name(call)
            if not model_name or (model_predicate is not None and not model_predicate(model_name)):
                continue
            stmt_text = "\n".join(lines[call.lineno - 1 : call.lineno + 10])
            results.append(
                {
                    "model": model_name,
                    "has_tenant_filter": "tenant_id" in stmt_text,
                    "lineno": call.lineno,
                }
            )
    return results


def find_raw_select_violations(
    *,
    skip: Callable[[str], bool],
    model_names: set[str],
) -> list[tuple[str, str, str, int]]:
    """Find raw ``select(Model)`` calls in src/ for models in *model_names*.

    Returns ``(rel_path, function_name, model_name, lineno)`` tuples — at most
    one per function (one violation per function is enough for a guard).
    Files where ``skip(rel_path)`` is True are excluded from the scan.
    """
    repo = repo_root()
    violations: list[tuple[str, str, str, int]] = []
    for py_file in (repo / "src").rglob("*.py"):
        rel_path = str(py_file.relative_to(repo))
        if skip(rel_path):
            continue
        tree = safe_parse(py_file)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in iter_call_expressions(node):
                model_name = select_call_model_name(call)
                if model_name and model_name in model_names:
                    violations.append((rel_path, node.name, model_name, call.lineno))
                    break  # One violation per function is enough
    return violations


def iter_architecture_guard_trees(
    *,
    exempt: Iterable[Path] | None = None,
) -> Iterator[tuple[ast.Module, Path]]:
    """Yield ``(parsed_tree, repo_relative_path)`` for each ``test_architecture_*.py`` module."""
    repo = repo_root()
    skip = set(exempt or ())
    for path in sorted((repo / "tests" / "unit").glob("test_architecture_*.py")):
        rel = path.relative_to(repo)
        if rel in skip:
            continue
        tree = safe_parse(path)
        if tree is None:
            continue
        yield tree, rel


# ---------------------------------------------------------------------------
# File iteration
# ---------------------------------------------------------------------------


def src_python_files(repo: Path) -> Iterator[Path]:
    """Every .py file under src/."""
    yield from (repo / "src").rglob("*.py")


def iter_workflow_files(repo: Path) -> Iterator[Path]:
    """.yml and .yaml files in .github/workflows/."""
    wf_dir = repo / ".github" / "workflows"
    if not wf_dir.exists():
        return
    yield from sorted([*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")])


# Dirs the filesystem fallback prunes — VCS internals plus build/cache artifacts
# that ``git ls-files`` never reports. In a clean checkout this mirrors the ignored
# set closely enough that the fallback matches the tracked set. ``.github`` is
# intentionally NOT excluded: the version-anchor guards scan ``.github/workflows/``.
_FALLBACK_PRUNE_DIRS = frozenset(
    {
        ".claude",
        ".git",
        ".venv",
        "venv",
        ".tox",
        "__pycache__",
        "node_modules",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "htmlcov",
        "test-results",
        ".agent-index",
        ".beads",
        "audit_logs",
        "logs",
        ".cache",
        ".eggs",
        "build",
        "dist",
        ".idea",
        ".vscode",
    }
)


def iter_git_tracked_files(repo: Path) -> Iterator[Path]:
    """Yield git-tracked files that exist on disk (hermetic; ignores untracked local files).

    Falls back to a filesystem walk when ``git ls-files`` cannot run — notably when a
    git worktree is bind-mounted into a container at a path that breaks the worktree's
    absolute ``.git`` back-references (``run_all_tests.sh`` mounts ``.:/app``; in a
    worktree ``/app/.git`` is a pointer FILE to a gitdir outside the mount, so git
    errors with "not a git repository"). The source files are all present in the bind
    mount; only git's metadata is unreachable. Without the fallback every git-dependent
    architecture guard hard-errors in that runner (and is silently never exercised
    there). The fallback prunes the usual VCS/build/cache dirs so, in a clean checkout,
    it matches the tracked set. On any host with a working git the fallback never
    triggers — hermetic behavior there is unchanged.
    """
    try:
        output = subprocess.check_output(["git", "ls-files"], cwd=repo, text=True, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        # Loud fallback: guard verdicts computed from a filesystem walk are only
        # as hermetic as the prune set, so surface the degradation in test output.
        warnings.warn(
            f"iter_git_tracked_files: 'git ls-files' failed in {repo} "
            f"({exc.__class__.__name__}: {exc}); falling back to a pruned filesystem "
            "walk — untracked files outside _FALLBACK_PRUNE_DIRS can affect guard scans",
            RuntimeWarning,
            stacklevel=2,
        )
        yield from _iter_files_fallback(repo)
        return
    for line in output.splitlines():
        if not line.strip():
            continue
        path = repo / line
        if path.is_file():
            yield path


def _iter_files_fallback(repo: Path) -> Iterator[Path]:
    """Deterministic filesystem walk mirroring git-tracked enumeration when git is unavailable."""
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(d for d in dirnames if d not in _FALLBACK_PRUNE_DIRS)
        base = Path(dirpath)
        for name in sorted(filenames):
            # In a worktree ``.git`` is a pointer FILE (not a dir), so the dir
            # filter above misses it; skip it (and any prune-named file) here.
            if name in _FALLBACK_PRUNE_DIRS:
                continue
            path = base / name
            if path.is_file():
                yield path


# ---------------------------------------------------------------------------
# Cross-surface version-anchor extractors (D24 + PR 5)
# ---------------------------------------------------------------------------

# Patterns intentionally permissive: surfaces use varied formats. Each returns
# a normalized version string ("3.12", "17", "0.11.7") via the first capture group.
# ``anchor_kind`` labels the surface for ADR-008 exemptions. Each string mirrors
# the config key on that surface (hyphens in TOML/YAML, underscores in ini —
# e.g. ``requires-python`` vs ``python_version``).

_DOCKERFILE_PYTHON_VERSION_PATTERN = r"^\s*ARG\s+PYTHON_VERSION=([0-9]+(?:\.[0-9]+)*)"

_PY_VERSION_PATTERNS: list[tuple[str, str, str]] = [
    # Dockerfile — templated builds: `ARG PYTHON_VERSION=3.12`
    (_DOCKERFILE_PYTHON_VERSION_PATTERN, "Dockerfile", "dockerfile-arg"),
    # Dockerfile / Dockerfile.* — literal `FROM python:3.12-slim` (legacy)
    (r"^\s*FROM\s+python:([0-9]+(?:\.[0-9]+)*)", "Dockerfile", "dockerfile-from"),
    # pyproject.toml — `target-version = "py312"` (black/ruff)
    (
        r'^\s*target-version\s*=\s*["\']py([0-9]{2,3})["\']',
        "pyproject.toml",
        "target-version",
    ),
    # pyproject.toml — `python_version = "3.12"` (mypy section)
    (
        r'^\s*python_version\s*=\s*["\']?([0-9]+\.[0-9]+)',
        "pyproject.toml",
        "python_version",
    ),
    # pyproject.toml — `requires-python = ">=3.12"`
    (
        r'^\s*requires-python\s*=\s*["\']>=\s*([0-9]+\.[0-9]+)',
        "pyproject.toml",
        "requires-python",
    ),
    # mypy.ini — `python_version = 3.12`
    (r"^\s*python_version\s*=\s*([0-9]+\.[0-9]+)", "mypy.ini", "python_version"),
    # .python-version — bare version string
    (
        r"^\s*([0-9]+\.[0-9]+(?:\.[0-9]+)?)\s*$",
        ".python-version",
        "python-version-file",
    ),
    # tox.ini — `basepython = python3.12`
    (r"^\s*basepython\s*=\s*python([0-9]+\.[0-9]+)", "tox.ini", "basepython"),
    # .github/workflows/*.yml + actions/*/action.yml — `python-version: '3.12'`
    (
        r"^\s*python-version:\s*['\"]?([0-9]+\.[0-9]+)",
        ".yml",
        "workflow-python-version",
    ),
    (
        r"^\s*python-version:\s*['\"]?([0-9]+\.[0-9]+)",
        ".yaml",
        "workflow-python-version",
    ),
]

_UV_VERSION_FILE_RE = re.compile(r"^\s*([\d.]+)\s*$", re.MULTILINE)
_DOCKERFILE_UV_VERSION_RE = re.compile(r"ARG UV_VERSION=([\d.]+)")
_PYTHON_VERSION_FILE_RE = re.compile(r"^\s*([0-9]+\.[0-9]+)", re.MULTILINE)
_DOCKERFILE_PYTHON_VERSION_RE = re.compile(_DOCKERFILE_PYTHON_VERSION_PATTERN, re.MULTILINE)
_DOCKERFILE_UV_IMAGE_DIGEST_RE = re.compile(
    r"^\s*ARG\s+UV_IMAGE_DIGEST=sha256:[a-f0-9]{64}\s*$",
    re.MULTILINE,
)
_DOCKERFILE_PYTHON_BASE_DIGEST_RE = re.compile(
    r"^\s*ARG\s+PYTHON_BASE_DIGEST=sha256:[a-f0-9]{64}\s*$",
    re.MULTILINE,
)

_PYTHON_ANCHOR_NAMES = frozenset({"pyproject.toml", "mypy.ini", "tox.ini", ".python-version"})


def uv_version_pattern_map() -> dict[str, str]:
    """Path-suffix → regex map for ``assert_anchor_consistency`` on uv version anchors."""
    return {
        ".uv-version": _UV_VERSION_FILE_RE.pattern,
        "Dockerfile": _DOCKERFILE_UV_VERSION_RE.pattern,
    }


def python_version_pattern_map() -> dict[str, str]:
    """Path-suffix → regex map for ``assert_anchor_consistency`` on Python version anchors."""
    return {
        ".python-version": _PYTHON_VERSION_FILE_RE.pattern,
        "Dockerfile": _DOCKERFILE_PYTHON_VERSION_RE.pattern,
    }


def _repo_relative(path: Path, repo: Path) -> str:
    return str(path.relative_to(repo))


def _github_yaml_candidate(path: Path, repo: Path) -> bool:
    rel_path = _repo_relative(path, repo)
    return path.suffix in {".yml", ".yaml"} and (
        rel_path.startswith(".github/workflows/") or rel_path.startswith(".github/actions/")
    )


def _python_anchor_candidate(path: Path, repo: Path) -> bool:
    rel_path = _repo_relative(path, repo)
    if path.name.startswith("Dockerfile"):
        return True
    if path.name in _PYTHON_ANCHOR_NAMES:
        return True
    if path.suffix in {".yml", ".yaml"} and rel_path.startswith(".github/workflows/"):
        return True
    return path.name in {"action.yml", "action.yaml"} and rel_path.startswith(".github/actions/")


def _normalize_py_target_version(version: str) -> str:
    """Normalize ruff/black ``py312`` capture groups to ``3.12``."""
    if len(version) == 3:
        return f"{version[0]}.{version[1:]}"
    if len(version) == 2:
        return f"{version[0]}.{version[1]}"
    return version


def extract_python_version_anchors(path: Path, text: str) -> list[tuple[str, str]]:
    """Extract (version, anchor_kind) tuples from *text* using the live python anchor patterns."""
    anchors: list[tuple[str, str]] = []
    for pattern, suffix_hint, anchor_kind in _PY_VERSION_PATTERNS:
        if not str(path).endswith(suffix_hint):
            continue
        for match in re.finditer(pattern, text, flags=re.MULTILINE):
            version = match.group(1)
            if anchor_kind == "target-version":
                version = _normalize_py_target_version(version)
            anchors.append((version, anchor_kind))
    return anchors


def iter_python_version_anchors(repo: Path) -> Iterator[tuple[Path, str, str]]:
    """Yield (file_path, version_string) for every Python version anchor across repo surfaces.

    Surfaces scanned: Dockerfile / Dockerfile.*, pyproject.toml (target-version, python_version,
    requires-python), .python-version, mypy.ini, tox.ini, .github/workflows/*.yml,
    .github/actions/*/action.yml. Returns one tuple per match (a file may have multiple).

    Uses ``git ls-files`` so untracked local files cannot affect the guard verdict.
    """
    for path in iter_git_tracked_files(repo):
        if not _python_anchor_candidate(path, repo):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for version, anchor_kind in extract_python_version_anchors(path, text):
            yield path, version, anchor_kind


# Anchored postgres image refs only — must not match bare prose image refs in sentences.
_PG_IMAGE_REF_PREFIXES: tuple[str, ...] = (
    r"^\s*FROM\s+postgres:",
    r"^\s+image:\s+postgres:",
    r"^\s+postgres:",  # docker run image argument (e.g. creative-agent-stack.sh)
    r'PG_IMAGE=["\']postgres:',
    r"`postgres:",
    r'["\']postgres:',
)
_PG_IMAGE_REF_PATTERN = (
    r"(?:" + "|".join(_PG_IMAGE_REF_PREFIXES) + r")([0-9]{1,2}(?:\.[0-9]+)?(?:-[a-z0-9.]+)?)(?![0-9])"
)
_PG_IMAGE_LITERAL = re.compile(_PG_IMAGE_REF_PATTERN, re.MULTILINE)
_PG_TAG_PATTERN = _PG_IMAGE_REF_PATTERN

# ADR-008: ruff target-version pinned to py312 (runtime .python-version is 3.12).
_ADR_008_TARGET_VERSION = "3.12"


def postgres_image_ref(tag: str) -> str:
    """Build a postgres image ref without a literal ``postgres:`` in source (avoids guard self-scan)."""
    return f"{'postgres'}:{tag}"


_PG_SCAN_SUFFIXES = frozenset({".py", ".yml", ".yaml", ".md", ".sh", ".toml", ".ini", ".txt", ".mdc"})


def postgres_tag_pattern_map() -> dict[str, str]:
    """Path-suffix → regex map for ``assert_anchor_consistency`` on postgres image tags."""
    mapping = dict.fromkeys(_PG_SCAN_SUFFIXES, _PG_TAG_PATTERN)
    mapping["Dockerfile"] = _PG_TAG_PATTERN
    mapping["compose.yaml"] = _PG_TAG_PATTERN
    return mapping


def _postgres_scan_candidate(path: Path) -> bool:
    return (
        path.name.startswith("Dockerfile")
        or path.suffix in _PG_SCAN_SUFFIXES
        or path.name in {"compose.yaml", "Dockerfile"}
    )


_SETUP_UV_ACTION = re.compile(r"astral-sh/setup-uv@([0-9a-f]{40})")
_INSTALL_UV_ACTION = ".github/actions/_install-uv/action.yml"
_HARDCODED_UV_VERSION_ENV_RE = re.compile(r'^\s*UV_VERSION:\s*["\']')
_HARDCODED_PYTHON_VERSION_RE = re.compile(r"python-version:\s*['\"]?[0-9]")


def extract_dockerfile_python_version(text: str) -> str | None:
    """Extract Python version from templated Dockerfile ``ARG PYTHON_VERSION=…`` line."""
    match = _DOCKERFILE_PYTHON_VERSION_RE.search(text)
    return match.group(1) if match else None


def assert_setup_uv_single_pinned_source(pins: Iterable[tuple[Path, str]], repo: Path) -> None:
    """Assert ``astral-sh/setup-uv`` is pinned in exactly one composite action file."""
    pin_list = list(pins)
    if not pin_list:
        raise AssertionError("non-vacuity: no astral-sh/setup-uv references found")

    by_file: dict[str, set[str]] = {}
    for path, pin in pin_list:
        rel = str(path.relative_to(repo))
        by_file.setdefault(rel, set()).add(pin)

    if set(by_file) != {_INSTALL_UV_ACTION}:
        raise AssertionError(
            "astral-sh/setup-uv must be referenced only from "
            f"{_INSTALL_UV_ACTION} — found pins in:\n"
            + "\n".join(f"  {path}: {sorted(values)}" for path, values in sorted(by_file.items()))
        )
    if len(by_file[_INSTALL_UV_ACTION]) != 1:
        raise AssertionError(
            f"expected exactly one setup-uv pin in {_INSTALL_UV_ACTION}, got {sorted(by_file[_INSTALL_UV_ACTION])}"
        )


def iter_setup_uv_action_pins(repo: Path) -> Iterator[tuple[Path, str]]:
    """Yield (file_path, full_pin) for every ``astral-sh/setup-uv@<sha>`` reference."""
    install_uv = repo / _INSTALL_UV_ACTION
    if install_uv.is_file():
        for m in _SETUP_UV_ACTION.finditer(install_uv.read_text(encoding="utf-8")):
            yield install_uv, m.group(0)

    for path in iter_git_tracked_files(repo):
        if path == install_uv or path.suffix not in {".yml", ".yaml"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in _SETUP_UV_ACTION.finditer(text):
            yield path, m.group(0)


def _iter_hardcoded_yaml_anchor(
    repo: Path,
    line_regex: re.Pattern[str],
    *,
    skip_substr: str | None = None,
) -> Iterator[tuple[Path, int, str]]:
    """Yield workflow/action lines matching *line_regex* under ``.github/workflows`` and ``actions``."""
    for path in iter_git_tracked_files(repo):
        if not _github_yaml_candidate(path, repo):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            if skip_substr and skip_substr in stripped:
                continue
            if line_regex.search(stripped):
                yield path, lineno, stripped


def iter_hardcoded_uv_version_env(repo: Path) -> Iterator[tuple[Path, int, str]]:
    """Yield workflow/action lines that hardcode ``UV_VERSION: "..."`` instead of reading ``.uv-version``."""
    yield from _iter_hardcoded_yaml_anchor(repo, _HARDCODED_UV_VERSION_ENV_RE)


def iter_hardcoded_python_version_yaml(repo: Path) -> Iterator[tuple[Path, int, str]]:
    """Yield workflow/action lines that hardcode ``python-version:`` instead of ``python-version-file``."""
    yield from _iter_hardcoded_yaml_anchor(
        repo,
        _HARDCODED_PYTHON_VERSION_RE,
        skip_substr="python-version-file",
    )


def assert_adr008_target_version_pinned(anchors: Iterable[tuple[Path, str, str]], repo: Path) -> None:
    """Assert ADR-008 ruff ``target-version`` stays exactly py312 (normalized ``3.12``)."""
    target_versions = [version for _, version, anchor_kind in anchors if anchor_kind == "target-version"]
    if not target_versions:
        raise AssertionError("non-vacuity: pyproject.toml target-version anchor must be scanned")
    drift = [
        f"{path.relative_to(repo)}: {version}"
        for path, version, anchor_kind in anchors
        if anchor_kind == "target-version" and version != _ADR_008_TARGET_VERSION
    ]
    if drift:
        raise AssertionError(
            f"ADR-008 target-version must stay py312 ({_ADR_008_TARGET_VERSION!r}):\n"
            + "\n".join(f"  {item}" for item in drift)
        )


def assert_dockerfile_digest_args_present(text: str) -> None:
    """Assert Dockerfile declares digest ARGs with ``sha256:<64-hex>`` shape.

    Verifies presence and format only — not that the digest matches the pinned
    ``PYTHON_VERSION`` / ``UV_VERSION`` tags.
    """
    missing: list[str] = []
    if not _DOCKERFILE_UV_IMAGE_DIGEST_RE.search(text):
        missing.append("ARG UV_IMAGE_DIGEST=sha256:<64-hex>")
    if not _DOCKERFILE_PYTHON_BASE_DIGEST_RE.search(text):
        missing.append("ARG PYTHON_BASE_DIGEST=sha256:<64-hex>")
    if missing:
        raise AssertionError("Dockerfile missing digest ARG pins:\n" + "\n".join(f"  {item}" for item in missing))


def extract_postgres_image_refs(path: Path, text: str) -> list[str]:
    """Extract postgres image tags from *text* using the live anchored detector."""
    return [m.group(1) for m in _PG_IMAGE_LITERAL.finditer(text)]


def iter_postgres_image_refs(repo: Path) -> Iterator[tuple[Path, str]]:
    """Yield (file_path, image_tag) for every ``postgres:`` literal in git-tracked text surfaces.

    Uses ``git ls-files`` so untracked local files (e.g. ``.claude/`` notes) cannot
    affect the guard verdict.
    """
    for path in iter_git_tracked_files(repo):
        if not _postgres_scan_candidate(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for tag in extract_postgres_image_refs(path, text):
            yield path, tag


# ---------------------------------------------------------------------------
# Dockerfile supply-chain helpers (PR 5)
# ---------------------------------------------------------------------------

_ROOT_USER_DIRECTIVES = frozenset({"USER root", "USER 0", "USER root:root", "USER 0:0"})


def find_unpinned_dockerfile_from_lines(lines: Iterable[str]) -> list[str]:
    """Return external FROM lines that lack a digest pin (@sha256 or @${…} ARG)."""
    stage_names: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped.upper().startswith("FROM "):
            continue
        parts = stripped.split()
        if len(parts) >= 4 and parts[2].upper() == "AS":
            stage_names.add(parts[3])
    violations: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.upper().startswith("FROM "):
            continue
        ref = stripped[5:].split(" AS ")[0].split(" as ")[0].strip()
        if ref in stage_names:
            continue
        if "/" not in ref and ":" not in ref:
            continue
        if "@sha256:" in stripped:
            continue
        if re.search(r"@\$\{[^}]+\}", stripped):
            continue
        violations.append(stripped)
    return violations


def runtime_user_directives(lines: Iterable[str]) -> list[str]:
    """USER directives in the final Dockerfile stage."""
    line_list = list(lines)
    from_indices = [i for i, line in enumerate(line_list) if line.strip().upper().startswith("FROM ")]
    if not from_indices:
        return []
    runtime_stage = line_list[from_indices[-1] :]
    return [line.strip() for line in runtime_stage if line.strip().upper().startswith("USER ")]


# ---------------------------------------------------------------------------
# Pre-commit config helpers
# ---------------------------------------------------------------------------

_PRE_COMMIT_CONFIG_PATH = Path(".pre-commit-config.yaml")


def load_pre_commit_config(path: Path | None = None, repo: Path | None = None) -> dict[str, Any]:
    """Load ``.pre-commit-config.yaml`` anchored to *repo* (default: repo root)."""
    root = repo or repo_root()
    cfg_path = path or (root / _PRE_COMMIT_CONFIG_PATH)
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8"))


def iter_pre_commit_hooks(
    cfg: dict[str, Any] | None = None,
    *,
    path: Path | None = None,
    repo: Path | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield hook records with ``id``, ``entry``, and ``stages`` from pre-commit config."""
    config = cfg if cfg is not None else load_pre_commit_config(path=path, repo=repo)
    default_stages = config.get("default_stages", ["pre-commit", "commit"])
    for repo_entry in config["repos"]:
        for hook in repo_entry["hooks"]:
            yield {
                "id": hook["id"],
                "entry": hook.get("entry", ""),
                "stages": hook.get("stages", default_stages),
            }


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


def _pattern_for_path(path: Path, pattern_map: dict[str, str]) -> str | None:
    for path_key, pat in pattern_map.items():
        if str(path).endswith(path_key) or str(path) == path_key:
            return pat
    return None


def assert_anchor_consistency(
    sources: Iterable[tuple[Path, str]],
    pattern_map: dict[str, str],
    *,
    label: str,
) -> None:
    """Assert that every regex match in every source extracts the same anchor value.

    Uses ``finditer`` per file so multiple anchors in one file (e.g. four postgres
    services in ``ci.yml``) cannot drift undetected.
    """
    matches: list[tuple[Path, str]] = []
    for path, text in sources:
        pattern = _pattern_for_path(path, pattern_map)
        if pattern is None:
            raise AssertionError(f"{label} anchor: no pattern for {path}")
        found = list(re.finditer(pattern, text, flags=re.MULTILINE))
        if not found:
            raise AssertionError(f"{label} anchor: pattern {pattern!r} did not match in {path}")
        matches.extend((path, m.group(1)) for m in found)

    assert matches, f"non-vacuity: no {label} anchors matched"

    distinct = {value for _, value in matches}
    if len(distinct) > 1:
        by_path: dict[Path, set[str]] = {}
        for path, value in matches:
            by_path.setdefault(path, set()).add(value)
        rendered = "\n".join(
            f"  {path}: {sorted(values)}" for path, values in sorted(by_path.items(), key=lambda item: str(item[0]))
        )
        raise AssertionError(f"{label} drift across sources:\n{rendered}")


def anchor_consistency_detects_drift(
    sources: Iterable[tuple[Path, str]],
    pattern_map: dict[str, str],
    *,
    label: str,
) -> bool:
    """Return True when *sources* contain conflicting anchors (mutation self-test probe)."""
    try:
        assert_anchor_consistency(sources, pattern_map, label=label)
    except AssertionError as exc:
        return f"{label} drift" in str(exc)
    return False


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
