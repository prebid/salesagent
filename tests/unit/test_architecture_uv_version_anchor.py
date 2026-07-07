"""Guard: uv and Python version anchors are consistent across repo surfaces.

Per D24 + PR 5 of issue #1234. Prevents drift between Dockerfile, CI workflow,
and the setup-env composite action.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import (
    anchor_consistency_detects_drift,
    assert_adr008_target_version_pinned,
    assert_anchor_consistency,
    assert_setup_uv_single_pinned_source,
    extract_dockerfile_python_version,
    extract_python_version_anchors,
    iter_hardcoded_python_version_yaml,
    iter_hardcoded_uv_version_env,
    iter_python_version_anchors,
    iter_setup_uv_action_pins,
    iter_workflow_files,
    python_version_pattern_map,
    repo_root,
    uv_version_pattern_map,
)

_KNOWN_BAD_UV_VERSION_SOURCES = [
    (Path(".uv-version"), "0.11.15\n"),
    (Path("Dockerfile"), "ARG UV_VERSION=0.11.14\n"),
]

_KNOWN_BAD_PYTHON_VERSION_SOURCES = [
    (Path(".python-version"), "3.12\n"),
    (Path("Dockerfile"), "ARG PYTHON_VERSION=3.11\nFROM python:${PYTHON_VERSION}-slim\n"),
]

_KNOWN_BAD_DOCKERFILE_PYTHON = {
    "templated_from_only": "FROM python:${PYTHON_VERSION}-slim AS builder\n",
    "arg_anchor": "ARG PYTHON_VERSION=3.12\nFROM python:${PYTHON_VERSION}-slim AS builder\n",
}

_KNOWN_BAD_TARGET_VERSION_SOURCES = [
    (Path("pyproject.toml"), 'target-version = "py310"\n'),
]

_KNOWN_BAD_SETUP_UV_PINS = [
    (Path(".github/actions/_install-uv/action.yml"), "astral-sh/setup-uv@1111111111111111111111111111111111111111"),
    (Path(".github/workflows/ci.yml"), "astral-sh/setup-uv@2222222222222222222222222222222222222222"),
]

_BAD_PYTHON_VERSION_WORKFLOW = """\
jobs:
  test:
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
"""

_GOOD_PYTHON_VERSION_WORKFLOW = """\
jobs:
  test:
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version-file: .python-version
"""

_BAD_UV_VERSION_WORKFLOW = """\
env:
  UV_VERSION: "0.6.0"
"""


def _github_yaml_repo(tmp_path: Path, rel: str, content: str) -> Path:
    """Throwaway git repo with one tracked workflow file (detector scans via ``git ls-files``)."""
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def _read(path_suffix: str) -> str:
    return (repo_root() / path_suffix).read_text(encoding="utf-8")


@pytest.mark.arch_guard
def test_uv_version_consistent() -> None:
    """``.uv-version`` is canonical; Dockerfile ARG is the only guarded duplicate."""
    repo = repo_root()
    assert_anchor_consistency(
        [
            (repo / ".uv-version", _read(".uv-version")),
            (repo / "Dockerfile", _read("Dockerfile")),
        ],
        uv_version_pattern_map(),
        label="uv version",
    )


@pytest.mark.arch_guard
def test_workflows_do_not_hardcode_uv_version_env() -> None:
    """Workflow/action env blocks must not copy uv version literals — use _install-uv instead."""
    violations = [
        f"{path.relative_to(repo_root())}:{lineno}: {line}"
        for path, lineno, line in iter_hardcoded_uv_version_env(repo_root())
    ]
    assert not violations, (
        "Hardcoded UV_VERSION env — CI must read .uv-version via ./.github/actions/_install-uv:\n"
        + "\n".join(violations)
    )


@pytest.mark.arch_guard
def test_setup_uv_action_pinned_in_single_source() -> None:
    """``astral-sh/setup-uv`` SHA pin lives only in _install-uv (guard widened per review)."""
    repo = repo_root()
    assert_setup_uv_single_pinned_source(iter_setup_uv_action_pins(repo), repo)

    for wf in iter_workflow_files(repo):
        assert "astral-sh/setup-uv@" not in wf.read_text(encoding="utf-8"), (
            f"{wf.relative_to(repo)} must use ./_install-uv, not astral-sh/setup-uv directly"
        )


@pytest.mark.arch_guard
def test_uv_version_anchor_detector_catches_known_bad_drift() -> None:
    """Mutation self-test: mismatched .uv-version vs Dockerfile ARG must fail the guard."""
    assert anchor_consistency_detects_drift(
        _KNOWN_BAD_UV_VERSION_SOURCES,
        uv_version_pattern_map(),
        label="uv version",
    ), "Detector must flag drift between .uv-version and Dockerfile ARG UV_VERSION"


@pytest.mark.arch_guard
def test_python_version_anchor_detector_catches_known_bad_drift() -> None:
    """Mutation self-test: mismatched .python-version vs Dockerfile ARG must fail the guard."""
    assert anchor_consistency_detects_drift(
        _KNOWN_BAD_PYTHON_VERSION_SOURCES,
        python_version_pattern_map(),
        label="python version",
    ), "Detector must flag drift between .python-version and Dockerfile ARG PYTHON_VERSION"


@pytest.mark.arch_guard
def test_python_dockerfile_anchor_detector_catches_templated_from_only() -> None:
    """Mutation self-test: templated FROM without ARG PYTHON_VERSION must not satisfy the guard."""
    assert extract_dockerfile_python_version(_KNOWN_BAD_DOCKERFILE_PYTHON["templated_from_only"]) is None
    assert extract_dockerfile_python_version(_KNOWN_BAD_DOCKERFILE_PYTHON["arg_anchor"]) == "3.12"


@pytest.mark.arch_guard
def test_setup_uv_pin_detector_catches_known_bad_multi_source() -> None:
    """Mutation self-test: setup-uv referenced outside _install-uv must fail the guard."""
    repo = repo_root()
    bad_pins = [(repo / rel, pin) for rel, pin in _KNOWN_BAD_SETUP_UV_PINS]
    with pytest.raises(AssertionError, match="astral-sh/setup-uv must be referenced only"):
        assert_setup_uv_single_pinned_source(bad_pins, repo)


@pytest.mark.arch_guard
def test_python_version_anchors_consistent() -> None:
    """``.python-version`` is canonical; Dockerfile ARG and other anchors must match major.minor."""
    repo = repo_root()
    canonical = (repo / ".python-version").read_text(encoding="utf-8").strip()
    major_minor = ".".join(canonical.split(".")[:2])

    assert_anchor_consistency(
        [
            (repo / ".python-version", _read(".python-version")),
            (repo / "Dockerfile", _read("Dockerfile")),
        ],
        python_version_pattern_map(),
        label="python version",
    )

    anchors = list(iter_python_version_anchors(repo))
    assert anchors, "non-vacuity: iter_python_version_anchors found no python version anchors"
    assert any(path.name == "Dockerfile" for path, _, _ in anchors), (
        "non-vacuity: Dockerfile must contribute a PYTHON_VERSION anchor (expected ARG PYTHON_VERSION=...)"
    )
    assert any(kind == "requires-python" for _, _, kind in anchors), (
        "non-vacuity: pyproject.toml requires-python anchor must be scanned"
    )

    drift: list[str] = []
    for path, version, anchor_kind in anchors:
        if path.name in {".python-version", "Dockerfile"}:
            continue
        # ADR-008: ruff target-version is py312 (aligned with runtime .python-version).
        if anchor_kind == "target-version":
            continue
        if version != major_minor:
            drift.append(f"{path.relative_to(repo)} ({anchor_kind}): {version}")

    assert not drift, f"python version drift — canonical {major_minor} from .python-version:\n" + "\n".join(drift)

    assert_adr008_target_version_pinned(anchors, repo)


@pytest.mark.arch_guard
def test_python_anchor_scan_includes_github_yaml_surfaces() -> None:
    """Workflow and composite-action YAML must be in the python anchor scan surface."""
    from tests.unit._architecture_helpers import _python_anchor_candidate, iter_git_tracked_files

    repo = repo_root()
    candidates = {
        str(path.relative_to(repo))
        for path in iter_git_tracked_files(repo)
        if _python_anchor_candidate(path, repo) and path.suffix in {".yml", ".yaml"}
    }
    assert candidates, "non-vacuity: expected git-tracked github yaml anchor candidates"
    assert any(path.startswith(".github/workflows/") for path in candidates)
    assert any(path.startswith(".github/actions/") for path in candidates)


@pytest.mark.arch_guard
def test_target_version_anchor_must_stay_py312() -> None:
    """Mutation self-test: ADR-008 target-version downgrade must fail the guard."""
    repo = repo_root()
    anchors = list(iter_python_version_anchors(repo))
    assert_adr008_target_version_pinned(anchors, repo)

    bad_path, bad_text = _KNOWN_BAD_TARGET_VERSION_SOURCES[0]
    bad_anchors = [
        (repo / bad_path, version, anchor_kind)
        for version, anchor_kind in extract_python_version_anchors(bad_path, bad_text)
    ]
    assert bad_anchors, "known-bad fixture must yield a target-version anchor"
    with pytest.raises(AssertionError, match="ADR-008 target-version must stay py312"):
        assert_adr008_target_version_pinned(bad_anchors, repo)


@pytest.mark.arch_guard
def test_hardcoded_python_version_yaml_detector_catches_known_bad(tmp_path: Path) -> None:
    """Full-chain self-test: a hardcoded python-version workflow must be flagged (#1497)."""
    repo = _github_yaml_repo(tmp_path, ".github/workflows/ci.yml", _BAD_PYTHON_VERSION_WORKFLOW)
    assert list(iter_hardcoded_python_version_yaml(repo)), "detector must flag hardcoded python-version"


@pytest.mark.arch_guard
def test_hardcoded_python_version_yaml_detector_skips_version_file(tmp_path: Path) -> None:
    """python-version-file must NOT be flagged — exercises skip path and guards over-broad matching."""
    repo = _github_yaml_repo(tmp_path, ".github/workflows/ci.yml", _GOOD_PYTHON_VERSION_WORKFLOW)
    assert list(iter_hardcoded_python_version_yaml(repo)) == []


@pytest.mark.arch_guard
def test_hardcoded_uv_version_env_detector_catches_known_bad(tmp_path: Path) -> None:
    """Full-chain self-test: a hardcoded UV_VERSION env block must be flagged (#1497)."""
    repo = _github_yaml_repo(tmp_path, ".github/workflows/ci.yml", _BAD_UV_VERSION_WORKFLOW)
    assert list(iter_hardcoded_uv_version_env(repo)), "detector must flag hardcoded UV_VERSION env"


@pytest.mark.arch_guard
def test_workflows_use_python_version_file() -> None:
    """Workflows/actions must not hardcode python-version when setup-env reads .python-version."""
    violations = [
        f"{path.relative_to(repo_root())}:{lineno}: {line}"
        for path, lineno, line in iter_hardcoded_python_version_yaml(repo_root())
    ]
    assert not violations, (
        "Hardcoded python-version in workflows/actions — use python-version-file: .python-version via _setup-env:\n"
        + "\n".join(violations)
    )
