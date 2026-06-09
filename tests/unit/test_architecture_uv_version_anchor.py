"""Guard: uv and Python version anchors are consistent across repo surfaces.

Per D24 + PR 5 of issue #1234. Prevents drift between Dockerfile, CI workflow,
and the setup-env composite action.
"""

from __future__ import annotations

import re

import pytest

from tests.unit._architecture_helpers import iter_workflow_files, repo_root


def _read(path_suffix: str) -> str:
    return (repo_root() / path_suffix).read_text()


@pytest.mark.arch_guard
def test_uv_version_consistent() -> None:
    """Dockerfile ARG UV_VERSION must match setup-env default and ci.yml env."""
    dockerfile = _read("Dockerfile")
    setup_env = _read(".github/actions/_setup-env/action.yml")
    ci_yml = _read(".github/workflows/ci.yml")

    docker_match = re.search(r"ARG UV_VERSION=([\d.]+)", dockerfile)
    assert docker_match, "Dockerfile missing ARG UV_VERSION"
    docker_ver = docker_match.group(1)

    setup_match = re.search(
        r"uv-version:.*?default:\s*[\"']([\d.]+)[\"']",
        setup_env,
        flags=re.DOTALL,
    )
    assert setup_match, "_setup-env missing uv-version default"
    setup_ver = setup_match.group(1)

    ci_match = re.search(r'UV_VERSION:\s*["\']([\d.]+)["\']', ci_yml)
    assert ci_match, "ci.yml missing UV_VERSION env anchor"
    ci_ver = ci_match.group(1)

    assert docker_ver == setup_ver == ci_ver, (
        f"uv version drift: Dockerfile={docker_ver}, setup-env={setup_ver}, ci.yml={ci_ver}"
    )


@pytest.mark.arch_guard
def test_python_version_anchors_consistent() -> None:
    """`.python-version` is canonical; Dockerfile ARG and mypy.ini must match."""
    repo = repo_root()
    canonical = (repo / ".python-version").read_text().strip()
    major_minor = ".".join(canonical.split(".")[:2])

    dockerfile = _read("Dockerfile")
    assert re.search(rf"ARG PYTHON_VERSION={re.escape(major_minor)}\b", dockerfile), (
        f"Dockerfile ARG PYTHON_VERSION must match .python-version ({major_minor})"
    )

    mypy = _read("mypy.ini")
    assert re.search(rf"python_version\s*=\s*{re.escape(major_minor)}\b", mypy), (
        f"mypy.ini python_version must match .python-version ({major_minor})"
    )


@pytest.mark.arch_guard
def test_workflows_use_python_version_file() -> None:
    """Workflows must not hardcode python-version when setup-env reads .python-version."""
    violations: list[str] = []
    for wf in iter_workflow_files(repo_root()):
        for lineno, line in enumerate(wf.read_text().splitlines(), 1):
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
