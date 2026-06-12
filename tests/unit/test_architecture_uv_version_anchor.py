"""Guard: uv and Python version anchors are consistent across repo surfaces.

Per D24 + PR 5 of issue #1234. Prevents drift between Dockerfile, CI workflow,
and the setup-env composite action.
"""

from __future__ import annotations

import re

import pytest

from tests.unit._architecture_helpers import (
    assert_anchor_consistency,
    iter_python_version_anchors,
    iter_workflow_files,
    repo_root,
)


def _read(path_suffix: str) -> str:
    return (repo_root() / path_suffix).read_text(encoding="utf-8")


@pytest.mark.arch_guard
def test_uv_version_consistent() -> None:
    """Dockerfile ARG UV_VERSION must match setup-env default and ci.yml env."""
    repo = repo_root()
    security_yml = _read(".github/workflows/security.yml")
    assert security_yml.count("version: ${{ env.UV_VERSION }}") >= 2, (
        "security.yml setup-uv steps must pin version via env.UV_VERSION"
    )

    assert_anchor_consistency(
        [
            (repo / "Dockerfile", _read("Dockerfile")),
            (repo / ".github/actions/_setup-env/action.yml", _read(".github/actions/_setup-env/action.yml")),
            (repo / ".github/workflows/ci.yml", _read(".github/workflows/ci.yml")),
            (repo / ".github/workflows/security.yml", security_yml),
        ],
        {
            "Dockerfile": r"ARG UV_VERSION=([\d.]+)",
            ".github/actions/_setup-env/action.yml": r'  default: "([\d.]+)"',
            ".github/workflows/ci.yml": r'UV_VERSION:\s*["\']([\d.]+)["\']',
            ".github/workflows/security.yml": r'UV_VERSION:\s*["\']([\d.]+)["\']',
        },
        label="uv version",
    )


@pytest.mark.arch_guard
def test_python_version_anchors_consistent() -> None:
    """`.python-version` is canonical; all scanned anchors must match major.minor."""
    repo = repo_root()
    canonical = (repo / ".python-version").read_text(encoding="utf-8").strip()
    major_minor = ".".join(canonical.split(".")[:2])

    anchors = list(iter_python_version_anchors(repo))
    assert anchors, "iter_python_version_anchors found no python version anchors"

    drift: list[str] = []
    for path, version in anchors:
        # ADR-008: ruff/black target-version stays py311 until a dedicated post-#1234 PR.
        if path.name == "pyproject.toml" and version == "3.11":
            continue
        if version != major_minor:
            drift.append(f"{path.relative_to(repo)}: {version}")

    assert not drift, f"python version drift — canonical {major_minor} from .python-version:\n" + "\n".join(drift)


@pytest.mark.arch_guard
def test_workflows_use_python_version_file() -> None:
    """Workflows must not hardcode python-version when setup-env reads .python-version."""
    violations: list[str] = []
    for wf in iter_workflow_files(repo_root()):
        for lineno, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if "python-version-file" in stripped:
                continue
            if re.search(r"python-version:\s*['\"]?[0-9]", stripped):
                rel = wf.relative_to(repo_root())
                violations.append(f"{rel}:{lineno}: {stripped}")
    assert not violations, (
        "Hardcoded python-version in workflows — use python-version-file: .python-version via _setup-env:\n"
        + "\n".join(violations)
    )
