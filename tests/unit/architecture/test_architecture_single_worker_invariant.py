"""Structural guard: single-worker invariant for v2.0.

Per ``.claude/notes/flask-to-fastapi/flask-to-fastapi-deep-audit.md`` §3.1 R12
and ``flask-to-fastapi-adcp-safety.md``:

> v2.0 MUST remain single-worker; multi-worker is a follow-up that requires
> scheduler leasing (Postgres advisory lock or separate scheduler container).
> Setting uvicorn ``workers>1`` → schedulers start N× per tick.

The scanner rejects ANY of the following patterns appearing in production
configuration surfaces:

1. ``uvicorn.run(..., workers=<int ≥ 2>, ...)`` in Python source.
2. ``workers = <int ≥ 2>`` as a module-level assignment in ``scripts/``.
3. ``WEB_CONCURRENCY``/``UVICORN_WORKERS`` shell-assignment of value ≥ 2 in
   ``Dockerfile``/``docker-compose*.yml``/``fly*.toml``.
4. ``--workers <N>`` CLI flag with N ≥ 2 anywhere in those same files.

The guard is deliberately path-limited (not ``tests/`` or ``.claude/notes/``
where multi-worker is discussed in prose) so docs talking about v2.1
multi-worker plans don't trip it.

Meta-fixture: a tiny text fixture containing ``workers=4`` that MUST be
flagged by the same regex the guard uses on real files.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    REPO_ROOT,
    SRC,
    call_func_name,
)

FIXTURE = FIXTURES_DIR / "test_single_worker_invariant_meta_fixture.txt"

# Python surfaces — parsed as AST.
PYTHON_SCAN_ROOTS: list[Path] = [
    REPO_ROOT / "scripts",
    SRC,
]

# Shell / infra surfaces — scanned with regex.
INFRA_PATTERNS: tuple[str, ...] = (
    "Dockerfile",
    "Dockerfile.*",
    "docker-compose*.yml",
    "docker-compose*.yaml",
    "fly*.toml",
)

# Matches WEB_CONCURRENCY=3, UVICORN_WORKERS=2, --workers 4, --workers=4.
_ENV_WORKERS_RE = re.compile(
    r"(?:WEB_CONCURRENCY|UVICORN_WORKERS)\s*[:=]\s*(\d+)",
    re.IGNORECASE,
)
_CLI_WORKERS_RE = re.compile(r"--workers[ =](\d+)")


def _uvicorn_run_workers(tree: ast.AST) -> list[tuple[int, int]]:
    """Return ``[(lineno, workers_value)]`` for every ``uvicorn.run(..., workers=N)``.

    Only integer literals are inspected — dynamic values (``workers=N`` where N
    is a variable) would need a different check and are out of scope here;
    a dynamic workers kwarg already raises a tighter review bar.
    """
    findings: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if call_func_name(node) != "run":
            continue
        # Accept both `uvicorn.run` and bare `run` (imported) — we already
        # narrow by the `workers` kwarg.
        for kw in node.keywords:
            if kw.arg != "workers":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, int):
                findings.append((node.lineno, kw.value.value))
    return findings


def _module_level_workers(tree: ast.AST) -> list[tuple[int, int]]:
    """Return module-level ``workers = <int>`` assignments."""
    findings: list[tuple[int, int]] = []
    if not isinstance(tree, ast.Module):
        return findings
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "workers"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, int)
                ):
                    findings.append((node.lineno, node.value.value))
    return findings


def _scan_python_file(path: Path) -> list[str]:
    """Return human-readable violation messages for a single Python file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    rel = path.relative_to(REPO_ROOT).as_posix()
    msgs: list[str] = []
    for lineno, value in _uvicorn_run_workers(tree):
        if value >= 2:
            msgs.append(f"{rel}:{lineno} — uvicorn.run(..., workers={value}) (must be 1)")
    for lineno, value in _module_level_workers(tree):
        if value >= 2:
            msgs.append(f"{rel}:{lineno} — module-level workers={value} (must be 1)")
    return msgs


def _iter_infra_files() -> list[Path]:
    found: list[Path] = []
    for pattern in INFRA_PATTERNS:
        found.extend(REPO_ROOT.glob(pattern))
    return found


def _scan_infra_file(path: Path) -> list[str]:
    """Return human-readable violation messages for a single infra file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = path.relative_to(REPO_ROOT).as_posix()
    msgs: list[str] = []
    for match in _ENV_WORKERS_RE.finditer(text):
        n = int(match.group(1))
        if n >= 2:
            line = text.count("\n", 0, match.start()) + 1
            msgs.append(f"{rel}:{line} — {match.group(0)} (must be 1)")
    for match in _CLI_WORKERS_RE.finditer(text):
        n = int(match.group(1))
        if n >= 2:
            line = text.count("\n", 0, match.start()) + 1
            msgs.append(f"{rel}:{line} — {match.group(0)} (must be 1)")
    return msgs


# ── Production invariant ────────────────────────────────────────────


def test_no_multi_worker_config() -> None:
    """No production config declares workers ≥ 2.

    v2.0 is single-worker by design. The MCP scheduler (delivery webhooks,
    media-buy status polling) runs as a process-local singleton in the app
    lifespan. Any config that spawns multiple workers fires the scheduler
    N× per tick — loud but expensive failure.
    """
    violations: list[str] = []
    for root in PYTHON_SCAN_ROOTS:
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            violations.extend(_scan_python_file(py))
    for infra in _iter_infra_files():
        violations.extend(_scan_infra_file(infra))

    assert not violations, (
        "Multi-worker configuration detected (v2.0 must remain single-worker "
        "per deep-audit §3.1 R12):\n  " + "\n  ".join(violations)
    )


# ── Meta-guard ──────────────────────────────────────────────────────


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    env_hits = [m for m in _ENV_WORKERS_RE.finditer(text) if int(m.group(1)) >= 2]
    cli_hits = [m for m in _CLI_WORKERS_RE.finditer(text) if int(m.group(1)) >= 2]
    assert env_hits or cli_hits, (
        f"Detector FAILED to find a workers>=2 reference in {FIXTURE.name}. "
        "The guard is broken — planted violation was not caught."
    )


@pytest.mark.parametrize(
    "snippet,expected_violation",
    [
        ("uvicorn.run('app:app', workers=1)\n", False),
        ("uvicorn.run('app:app')\n", False),
        ("uvicorn.run('app:app', workers=2)\n", True),
        ("uvicorn.run('app:app', workers=4)\n", True),
        # Non-int workers kwarg is intentionally out of scope — the scanner
        # only flags literal ints so string interpolation/variables need a
        # separate review check.
        ("uvicorn.run('app:app', workers=n)\n", False),
    ],
)
def test_python_detector_behavior(snippet: str, expected_violation: bool) -> None:
    tree = ast.parse(snippet)
    findings = [(l, v) for l, v in _uvicorn_run_workers(tree) if v >= 2]
    assert bool(findings) is expected_violation


@pytest.mark.parametrize(
    "text,expected_violation",
    [
        ("WEB_CONCURRENCY=1\n", False),
        ("WEB_CONCURRENCY=2\n", True),
        ("UVICORN_WORKERS=4\n", True),
        ("--workers 3\n", True),
        ("--workers=1\n", False),
        ("# --workers 9 (comment)\n", True),  # regex is path-limited, not syntax-aware
    ],
)
def test_infra_detector_behavior(text: str, expected_violation: bool) -> None:
    env_hits = [m for m in _ENV_WORKERS_RE.finditer(text) if int(m.group(1)) >= 2]
    cli_hits = [m for m in _CLI_WORKERS_RE.finditer(text) if int(m.group(1)) >= 2]
    assert bool(env_hits or cli_hits) is expected_violation
