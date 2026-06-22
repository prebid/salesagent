"""Guard: Dockerfile pins base image by digest and runs as non-root.

Per D34 + PR 5 of issue #1234. Without these, the runtime image carries
unverified base-layer provenance and root-equivalent privileges.
"""

from __future__ import annotations

import pytest

from tests.unit._architecture_helpers import (
    _ROOT_USER_DIRECTIVES,
    assert_dockerfile_digest_args_present,
    find_unpinned_dockerfile_from_lines,
    format_failure,
    repo_root,
    runtime_user_directives,
)

_KNOWN_BAD_FROM = [
    "FROM ghcr.io/astral-sh/uv:0.11.15 AS uv",
    "FROM node:20-slim AS build",
]


@pytest.mark.arch_guard
def test_dockerfile_digest_args_present() -> None:
    """Dockerfile must declare UV_IMAGE_DIGEST and PYTHON_BASE_DIGEST with sha256:<64-hex> shape."""
    assert_dockerfile_digest_args_present((repo_root() / "Dockerfile").read_text(encoding="utf-8"))


@pytest.mark.arch_guard
def test_dockerfile_digest_args_detector_catches_missing_pins() -> None:
    """Mutation self-test: Dockerfile without digest ARGs must fail the guard."""
    with pytest.raises(AssertionError, match="Dockerfile missing digest ARG pins"):
        assert_dockerfile_digest_args_present("ARG PYTHON_VERSION=3.12\nFROM python:3.12-slim\n")


@pytest.mark.arch_guard
def test_dockerfile_base_image_digest_pinned() -> None:
    """Every external FROM must include @sha256: or digest ARG — not tag-only."""
    lines = (repo_root() / "Dockerfile").read_text(encoding="utf-8").splitlines()
    violations = find_unpinned_dockerfile_from_lines(lines)
    assert not violations, format_failure(
        summary="Dockerfile has tag-only external FROM lines",
        violations=violations,
    )


@pytest.mark.arch_guard
def test_dockerfile_digest_detector_catches_known_bad_from() -> None:
    assert find_unpinned_dockerfile_from_lines(_KNOWN_BAD_FROM), "Detector must flag tag-only external FROM lines"


@pytest.mark.arch_guard
def test_dockerfile_runs_as_non_root() -> None:
    """Runtime stage must end with USER set to a non-root identity."""
    lines = (repo_root() / "Dockerfile").read_text(encoding="utf-8").splitlines()
    user_lines = runtime_user_directives(lines)
    assert user_lines, "Dockerfile runtime stage has no USER directive (D34)"
    last_user = user_lines[-1]
    assert last_user not in _ROOT_USER_DIRECTIVES, format_failure(
        summary="Dockerfile runtime stage runs as root",
        violations=[last_user],
    )


@pytest.mark.arch_guard
def test_dockerfile_non_root_detector_catches_root_group_form() -> None:
    assert "USER root:root" in _ROOT_USER_DIRECTIVES
    assert "USER 0:0" in _ROOT_USER_DIRECTIVES
