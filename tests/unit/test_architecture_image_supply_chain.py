"""Structural guards for PR 6 image supply-chain hardening (issue #1234)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.unit._architecture_helpers import repo_root

_IMAGE_PUBLISHING_WORKFLOWS = frozenset({"release-please.yml", "publish-creative-agent.yml"})
_SIGN_ATTEST_JOB = "sign-and-attest"


def _load_workflow(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _job_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps", [])
    return [step for step in steps if isinstance(step, dict)]


def _step_uses(step: dict[str, Any]) -> str:
    return str(step.get("uses", ""))


def _job_uses_docker(job: dict[str, Any]) -> bool:
    if job.get("container") or job.get("services"):
        return True
    for step in _job_steps(job):
        uses = _step_uses(step)
        if uses.startswith(
            (
                "docker/build-push-action@",
                "docker/setup-buildx-action@",
                "docker/setup-qemu-action@",
            )
        ):
            return True
        run_script = step.get("run", "")
        if isinstance(run_script, str) and (
            "docker build" in run_script
            or "docker run" in run_script
            or "docker push" in run_script
            or "./scripts/creative-agent-stack.sh" in run_script
        ):
            return True
    return False


def _harden_runner_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in _job_steps(job) if _step_uses(step).startswith("step-security/harden-runner@")]


def _assert_harden_runner_job(wf_name: str, job_name: str, job: dict[str, Any]) -> None:
    hr_steps = _harden_runner_steps(job)
    assert hr_steps, f"{wf_name} job {job_name} missing harden-runner step"
    uses_docker = _job_uses_docker(job)
    for step in hr_steps:
        with_block = step.get("with", {})
        assert isinstance(with_block, dict), f"{wf_name} job {job_name} harden-runner missing with:"
        assert with_block.get("egress-policy") == "audit", f"{wf_name} job {job_name} must use egress-policy: audit"
        sudo_disabled = with_block.get("disable-sudo-and-containers") is True
        if uses_docker:
            assert not sudo_disabled, f"{wf_name} job {job_name} must omit disable-sudo (docker/service job)"
        else:
            assert sudo_disabled, f"{wf_name} job {job_name} must set disable-sudo-and-containers: true"


def _assert_sign_and_attest_job(job: dict[str, Any]) -> None:
    perms = job.get("permissions", {})
    assert isinstance(perms, dict)
    for perm in ("id-token", "attestations", "packages", "security-events"):
        assert perms.get(perm) == "write", f"sign-and-attest missing permissions.{perm}: write"

    step_uses = [_step_uses(step) for step in _job_steps(job)]
    assert any(u.startswith("docker/login-action@") for u in step_uses), "sign-and-attest missing docker/login-action"
    assert sum(u.startswith("docker/login-action@") for u in step_uses) >= 2, (
        "sign-and-attest needs ghcr + Docker Hub login"
    )
    assert any("cosign sign" in (step.get("run") or "") for step in _job_steps(job)), (
        "sign-and-attest missing cosign sign"
    )
    assert any(u.startswith("actions/attest-build-provenance@") for u in step_uses), (
        "sign-and-attest missing provenance"
    )
    assert any(u.startswith("actions/upload-artifact@") for u in step_uses), (
        "sign-and-attest must upload cosign bundles"
    )
    assert any(u.startswith("github/codeql-action/upload-sarif@") for u in step_uses), (
        "sign-and-attest missing SARIF upload"
    )


def _assert_build_and_push_job(job: dict[str, Any]) -> None:
    names = [step.get("name", "") for step in _job_steps(job)]
    assert any("pre-push" in name.lower() for name in names), "build-and-push missing pre-push Trivy gate"
    push_steps = [step for step in _job_steps(job) if _step_uses(step).startswith("docker/build-push-action@")]
    assert len(push_steps) >= 2, "build-and-push must build scan artifact then push multi-arch image"
    assert push_steps[-1].get("with", {}).get("push") is True, "final build step must push to registry"


@pytest.mark.arch_guard
def test_image_publishing_workflows_harden_runner_per_job() -> None:
    """Image workflows must harden every job; docker jobs omit disable-sudo."""
    repo = repo_root()
    wf_dir = repo / ".github" / "workflows"
    hardened_jobs = 0
    for wf_name in sorted(_IMAGE_PUBLISHING_WORKFLOWS):
        wf_path = wf_dir / wf_name
        assert wf_path.exists(), f"missing workflow {wf_name}"
        jobs = _load_workflow(wf_path).get("jobs", {})
        assert isinstance(jobs, dict) and jobs, f"{wf_name} has no jobs"
        for job_name, job in jobs.items():
            assert isinstance(job, dict), f"{wf_name} job {job_name} malformed"
            _assert_harden_runner_job(wf_name, job_name, job)
            hardened_jobs += len(_harden_runner_steps(job))
    assert hardened_jobs >= 3, f"expected hardened steps across publishing workflows, found {hardened_jobs}"


@pytest.mark.arch_guard
def test_release_workflow_supply_chain_wiring() -> None:
    """release-please.yml must gate before push, then sign/attest with registry auth."""
    wf_path = repo_root() / ".github/workflows/release-please.yml"
    jobs = _load_workflow(wf_path).get("jobs", {})
    assert "build-and-push" in jobs
    assert _SIGN_ATTEST_JOB in jobs
    _assert_build_and_push_job(jobs["build-and-push"])
    _assert_sign_and_attest_job(jobs[_SIGN_ATTEST_JOB])

    text = wf_path.read_text(encoding="utf-8")
    assert "image_tags:" in text
    assert "image_digest:" in text
    assert "ghcr_image:" not in text
    assert "provenance: mode=max" in text
    assert "sbom: true" in text


@pytest.mark.arch_guard
def test_harden_runner_guard_detects_missing_disable_sudo() -> None:
    """Negative probe: non-docker job without disable-sudo must fail the guard."""
    job = {
        "runs-on": "ubuntu-latest",
        "steps": [
            {"uses": "step-security/harden-runner@abc", "with": {"egress-policy": "audit"}},
            {"run": "echo ok"},
        ],
    }
    with pytest.raises(AssertionError, match="disable-sudo"):
        _assert_harden_runner_job("probe.yml", "probe", job)


@pytest.mark.arch_guard
def test_harden_runner_guard_detects_docker_job_with_disable_sudo() -> None:
    """Negative probe: docker job with disable-sudo must fail the guard."""
    job = {
        "runs-on": "ubuntu-latest",
        "services": {"postgres": {"image": "postgres:17-alpine"}},
        "steps": [
            {
                "uses": "step-security/harden-runner@abc",
                "with": {"egress-policy": "audit", "disable-sudo-and-containers": True},
            },
        ],
    }
    with pytest.raises(AssertionError, match="omit disable-sudo"):
        _assert_harden_runner_job("probe.yml", "docker-probe", job)


@pytest.mark.arch_guard
def test_sign_and_attest_guard_detects_missing_registry_login() -> None:
    """Negative probe: sign-and-attest without docker login must fail."""
    job = {
        "permissions": {
            "id-token": "write",
            "attestations": "write",
            "packages": "write",
            "security-events": "write",
        },
        "steps": [
            {"uses": "sigstore/cosign-installer@abc"},
            {"run": "cosign sign --yes --bundle /tmp/b.json image@sha256:abc"},
            {"uses": "actions/attest-build-provenance@abc"},
            {"uses": "actions/upload-artifact@abc"},
            {"uses": "github/codeql-action/upload-sarif@abc"},
        ],
    }
    with pytest.raises(AssertionError, match="docker/login-action"):
        _assert_sign_and_attest_job(job)


@pytest.mark.arch_guard
def test_dependency_review_configured() -> None:
    """security.yml runs dependency-review with extracted config."""
    repo = repo_root()
    security = (repo / ".github/workflows/security.yml").read_text(encoding="utf-8")
    assert "actions/dependency-review-action" in security
    assert "Security / Dependency Review" in security
    config = repo / ".github/dependency-review-config.yml"
    assert config.exists()
    assert "fail-on-severity: moderate" in config.read_text(encoding="utf-8")


@pytest.mark.arch_guard
def test_codeql_is_gating() -> None:
    """CodeQL analyze must block merges (no continue-on-error)."""
    text = (repo_root() / ".github/workflows/codeql.yml").read_text(encoding="utf-8")
    assert "continue-on-error: true" not in text


@pytest.mark.arch_guard
def test_scorecard_workflow_present() -> None:
    """OpenSSF Scorecard self-host workflow must exist."""
    path = repo_root() / ".github/workflows/scorecard.yml"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "ossf/scorecard-action" in text
    assert "publish_results: true" in text


@pytest.mark.arch_guard
def test_checkout_pins_consistent_on_hardened_workflows() -> None:
    """Hardened workflows use a single actions/checkout SHA pin."""
    expected = "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"
    for wf_name in ("ci.yml", "security.yml", "codeql.yml", "release-please.yml", "publish-creative-agent.yml"):
        text = (repo_root() / ".github/workflows" / wf_name).read_text(encoding="utf-8")
        assert expected in text, f"{wf_name} must pin checkout to {expected}"
        assert "actions/checkout@11bd719" not in text, f"{wf_name} still uses stale checkout pin"
