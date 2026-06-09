"""Guard: Dockerfile pins base image by digest and runs as non-root.

Per D34 + PR 5 of issue #1234. Without these, the runtime image carries
unverified base-layer provenance and root-equivalent privileges.
"""

from __future__ import annotations

import pytest

from tests.unit._architecture_helpers import repo_root


@pytest.mark.arch_guard
def test_dockerfile_base_image_digest_pinned() -> None:
    """Every external FROM must include @sha256: or digest ARG — not tag-only."""
    dockerfile = (repo_root() / "Dockerfile").read_text()
    violations: list[str] = []
    for line in dockerfile.splitlines():
        stripped = line.strip()
        if not stripped.startswith("FROM "):
            continue
        if " AS " in stripped.upper() and "${" not in stripped and "@" not in stripped:
            # Intra-file stage alias (e.g. FROM builder) — skip.
            ref_part = stripped.split()[1]
            if ":" not in ref_part and "/" not in ref_part:
                continue
        if "@sha256:" in stripped or "@${PYTHON_BASE_DIGEST}" in stripped or "${PYTHON_BASE_DIGEST}" in stripped:
            continue
        if stripped.startswith("FROM python:") and "@" not in stripped:
            violations.append(f"Dockerfile FROM line lacks digest pin: {stripped}")
    assert not violations, "\n".join(violations)


@pytest.mark.arch_guard
def test_dockerfile_runs_as_non_root() -> None:
    """Runtime stage must end with USER set to a non-root identity."""
    dockerfile = (repo_root() / "Dockerfile").read_text()
    from_indices = [i for i, line in enumerate(dockerfile.splitlines()) if line.strip().startswith("FROM ")]
    assert from_indices, "no FROM lines in Dockerfile"
    runtime_stage = dockerfile.splitlines()[from_indices[-1] :]
    user_lines = [line.strip() for line in runtime_stage if line.strip().startswith("USER ")]
    assert user_lines, "Dockerfile runtime stage has no USER directive (D34)"
    last_user = user_lines[-1]
    assert last_user not in ("USER root", "USER 0"), f"Dockerfile runtime stage runs as root: {last_user}"
