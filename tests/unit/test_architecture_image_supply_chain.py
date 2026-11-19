"""Structural guards for PR 6 image supply-chain hardening (issue #1234)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
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
                "ossf/scorecard-action@",
            )
        ):
            return True
        run_script = step.get("run", "")
        if isinstance(run_script, str) and (
            "docker build" in run_script
            or "docker run" in run_script
            or "docker push" in run_script
            or "docker compose" in run_script
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


def _resolve_job_env(job: dict[str, Any], value: str) -> str:
    """Expand a whole-value ``${{ env.NAME }}`` reference from the job's ``env``."""
    match = re.fullmatch(r"\$\{\{\s*env\.(\w+)\s*\}\}", value)
    return str(job.get("env", {}).get(match.group(1), value)) if match else value


def _assert_sign_and_attest_job(job: dict[str, Any]) -> None:
    perms = job.get("permissions", {})
    assert isinstance(perms, dict)
    for perm in ("id-token", "attestations", "packages", "security-events"):
        assert perms.get(perm) == "write", f"sign-and-attest missing permissions.{perm}: write"

    steps = _job_steps(job)
    step_uses = [_step_uses(step) for step in steps]
    assert any(u.startswith("sigstore/cosign-installer@") for u in step_uses), (
        "sign-and-attest missing cosign-installer"
    )
    login_runs = [step.get("run") or "" for step in steps]
    assert any("cosign login ghcr.io" in run for run in login_runs), (
        "sign-and-attest must daemon-free cosign login for ghcr.io"
    )
    assert any("cosign login docker.io" in run for run in login_runs), (
        "sign-and-attest must daemon-free cosign login for docker.io"
    )
    assert not any(u.startswith("docker/login-action@") for u in step_uses), (
        "sign-and-attest must not use docker/login-action (Docker uninstalled by harden-runner)"
    )
    assert any("cosign sign" in run for run in login_runs), "sign-and-attest missing cosign sign"
    assert any('[[ -n "$DIGEST" ]]' in run for run in login_runs), (
        "sign-and-attest must guard empty image_digest before cosign sign"
    )
    attest_steps = [step for step in steps if _step_uses(step).startswith("actions/attest-build-provenance@")]
    assert len(attest_steps) >= 2, "sign-and-attest must attest ghcr.io and Docker Hub images"
    subjects = [_resolve_job_env(job, str(s.get("with", {}).get("subject-name", ""))) for s in attest_steps]
    ghcr_attest = [subject for subject in subjects if "ghcr.io/" in subject]
    dockerhub_attest = [subject for subject in subjects if subject.startswith("docker.io/")]
    assert ghcr_attest, "sign-and-attest must attest ghcr.io image with registry host"
    assert dockerhub_attest, "sign-and-attest Docker Hub attest must use docker.io/ registry host in subject-name"
    assert any(u.startswith("actions/upload-artifact@") for u in step_uses), (
        "sign-and-attest must upload cosign bundles"
    )
    assert any(u.startswith("github/codeql-action/upload-sarif@") for u in step_uses), (
        "sign-and-attest missing SARIF upload"
    )


def _assert_build_and_push_job(job: dict[str, Any]) -> None:
    perms = job.get("permissions", {})
    assert isinstance(perms, dict)
    assert perms.get("actions") == "read", "build-and-push D47 gate needs permissions.actions: read"

    steps = _job_steps(job)
    names = [step.get("name", "") for step in steps]
    assert sum("pre-push" in name.lower() for name in names) >= 2, (
        "build-and-push must Trivy-gate amd64 and arm64 before push"
    )
    gate_steps = [
        step
        for step in steps
        if _step_uses(step).startswith("aquasecurity/trivy-action@") and "pre-push" in (step.get("name") or "").lower()
    ]
    assert len(gate_steps) >= 2, "build-and-push missing per-arch pre-push Trivy steps"
    for step in gate_steps:
        assert step.get("with", {}).get("exit-code") == 1, f"pre-push Trivy step {step.get('name')} must exit-code: 1"
    arm64_gate = [step for step in gate_steps if "arm64" in (step.get("name") or "").lower()]
    assert arm64_gate, "build-and-push missing arm64 pre-push Trivy gate"
    assert arm64_gate[0].get("env", {}).get("TRIVY_PLATFORM") == "linux/arm64", (
        "arm64 pre-push Trivy must set TRIVY_PLATFORM=linux/arm64"
    )
    push_steps = [step for step in steps if _step_uses(step).startswith("docker/build-push-action@")]
    assert len(push_steps) >= 3, "build-and-push must gate amd64, gate arm64, then push multi-arch"
    assert push_steps[-1].get("with", {}).get("push") is True, "final build step must push to registry"

    d47_runs = [step.get("run") or "" for step in steps if "D47 gate" in (step.get("name") or "")]
    assert d47_runs, "build-and-push missing D47 gate step"
    assert all("${{ github.repository }}" not in run for run in d47_runs), (
        "D47 gate must not interpolate github.repository in run: (use env:)"
    )


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

    # build-and-push's image_tags output contains the Docker Hub user (a secret), so GitHub drops it
    # between jobs; signing from it signed nothing in the 3.0.1 release.
    build_outputs = jobs["build-and-push"].get("outputs", {})
    assert "image_tags" not in build_outputs, "build-and-push must not export image_tags (dropped as secret-bearing)"
    assert "image_digest" in build_outputs

    text = wf_path.read_text(encoding="utf-8")
    assert "ghcr_image:" not in text
    assert "concurrency:" in text
    assert "provenance: mode=max" in text
    assert "sbom: true" in text


def _release_sign_job() -> dict[str, Any]:
    return _load_workflow(repo_root() / ".github/workflows/release-please.yml")["jobs"][_SIGN_ATTEST_JOB]


def _release_sign_step() -> dict[str, Any]:
    steps = [step for step in _job_steps(_release_sign_job()) if "cosign sign" in (step.get("run") or "")]
    assert len(steps) == 1, "release sign-and-attest must have exactly one cosign sign step"
    return steps[0]


_STUB_COSIGN = '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$COSIGN_LOG"\n'


def _run_sign_script(tmp_path: Path, images: str, digest: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the release sign step's script under bash with a stub cosign that records its arguments."""
    bash = shutil.which("bash")
    assert bash, "bash is required to run the release sign step's script"
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    cosign = stub_dir / "cosign"
    cosign.write_text(_STUB_COSIGN, encoding="utf-8", newline="\n")
    cosign.chmod(0o755)
    log = tmp_path / "cosign.log"
    script = tmp_path / "sign.sh"
    script.write_text(_release_sign_step()["run"], encoding="utf-8", newline="\n")
    env = {
        **os.environ,
        "PATH": f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "COSIGN_LOG": log.as_posix(),
        "DIGEST": digest,
        "IMAGES": images,
        "BUNDLE_DIR": (tmp_path / "bundles").as_posix(),
    }
    result = subprocess.run([bash, script.as_posix()], env=env, capture_output=True, text=True, timeout=60, check=False)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, calls


_DIGEST = "sha256:" + "0" * 64


@pytest.mark.arch_guard
def test_release_sign_script_signs_each_image_once_at_the_digest(tmp_path: Path) -> None:
    """Two image names give one cosign sign call per <image>@<digest>, and the step passes."""
    result, calls = _run_sign_script(tmp_path, "ghcr.io/org/repo docker.io/user/salesagent", _DIGEST)
    assert result.returncode == 0, result.stdout + result.stderr
    signed = [call.split()[-1] for call in calls if call.startswith("sign ")]
    assert signed == [f"ghcr.io/org/repo@{_DIGEST}", f"docker.io/user/salesagent@{_DIGEST}"]


@pytest.mark.arch_guard
@pytest.mark.parametrize("images", ["", "   "])
def test_release_sign_script_fails_when_there_is_nothing_to_sign(tmp_path: Path, images: str) -> None:
    """An empty image list fails the step; 3.0.1 signed nothing and passed."""
    result, calls = _run_sign_script(tmp_path, images, _DIGEST)
    assert result.returncode != 0
    assert not [call for call in calls if call.startswith("sign ")]


@pytest.mark.arch_guard
def test_release_sign_and_attest_reads_image_names_from_job_env() -> None:
    """Signing, both attestations and the scan read the image names from one job-level env."""
    job = _release_sign_job()
    job_env = job.get("env", {})
    assert job_env.get("GHCR_IMAGE") == "ghcr.io/${{ github.repository }}"
    assert job_env.get("DOCKERHUB_IMAGE") == "docker.io/${{ secrets.DOCKERHUB_USER }}/salesagent"
    assert _release_sign_step().get("env", {}).get("IMAGES") == "${{ env.GHCR_IMAGE }} ${{ env.DOCKERHUB_IMAGE }}"
    steps = _job_steps(job)
    subjects = sorted(
        str(step.get("with", {}).get("subject-name"))
        for step in steps
        if _step_uses(step).startswith("actions/attest-build-provenance@")
    )
    assert subjects == ["${{ env.DOCKERHUB_IMAGE }}", "${{ env.GHCR_IMAGE }}"]
    scan_refs = [
        str(step.get("with", {}).get("image-ref"))
        for step in steps
        if _step_uses(step).startswith("aquasecurity/trivy-action@")
    ]
    assert scan_refs == ["${{ env.GHCR_IMAGE }}@${{ env.IMAGE_DIGEST }}"]


@pytest.mark.arch_guard
def test_release_sign_and_attest_dispatch_is_main_only_and_does_not_attest() -> None:
    """A dispatch run signs from main only, and leaves build provenance to the run that built the image."""
    job = _release_sign_job()
    condition = str(job.get("if", ""))
    assert "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in condition, (
        "dispatch from a branch signs with an identity ADR-007's cosign verify rejects"
    )
    attest_steps = [step for step in _job_steps(job) if _step_uses(step).startswith("actions/attest-build-provenance@")]
    assert attest_steps
    for step in attest_steps:
        assert step.get("if") == "github.event_name == 'push'", (
            "build provenance must come from the run that built the image, not a dispatch run"
        )


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
def test_sign_and_attest_guard_detects_bare_dockerhub_subject_name() -> None:
    """Negative probe: Docker Hub attest without docker.io/ host must fail."""
    job = {
        "permissions": {
            "id-token": "write",
            "attestations": "write",
            "packages": "write",
            "security-events": "write",
        },
        "steps": [
            {"uses": "sigstore/cosign-installer@abc"},
            {"run": "cosign login ghcr.io\ncosign login docker.io"},
            {"run": '[[ -n "$DIGEST" ]]\ncosign sign --yes image@sha256:abc'},
            {"uses": "actions/attest-build-provenance@abc", "with": {"subject-name": "ghcr.io/org/repo"}},
            {
                "uses": "actions/attest-build-provenance@abc",
                "with": {"subject-name": "${{ secrets.DOCKERHUB_USER }}/salesagent"},
            },
            {"uses": "actions/upload-artifact@abc"},
            {"uses": "github/codeql-action/upload-sarif@abc"},
        ],
    }
    with pytest.raises(AssertionError, match="docker.io/"):
        _assert_sign_and_attest_job(job)


@pytest.mark.arch_guard
def test_sign_and_attest_guard_detects_missing_registry_login() -> None:
    """Negative probe: sign-and-attest without daemon-free cosign login must fail."""
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
            {"uses": "actions/attest-build-provenance@abc"},
            {"uses": "actions/upload-artifact@abc"},
            {"uses": "github/codeql-action/upload-sarif@abc"},
        ],
    }
    with pytest.raises(AssertionError, match="cosign login"):
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
    """CodeQL analyze job and steps must block merges (no continue-on-error)."""
    jobs = _load_workflow(repo_root() / ".github/workflows/codeql.yml").get("jobs", {})
    assert isinstance(jobs, dict)
    analyze_jobs = [job for name, job in jobs.items() if "analyze" in name.lower()]
    assert analyze_jobs, "codeql.yml must define an analyze job"
    for job in analyze_jobs:
        assert job.get("continue-on-error") is not True, "CodeQL analyze job must not continue-on-error"
        for step in _job_steps(job):
            assert step.get("continue-on-error") is not True, (
                f"CodeQL analyze step {_step_uses(step) or step.get('name')} must not continue-on-error"
            )


@pytest.mark.arch_guard
def test_codeql_guard_detects_step_level_continue_on_error() -> None:
    """Negative probe: step-level continue-on-error on analyze must fail the guard."""
    job = {
        "steps": [
            {"uses": "github/codeql-action/analyze@abc", "continue-on-error": True},
        ],
    }
    with pytest.raises(AssertionError, match="continue-on-error"):
        for step in _job_steps(job):
            assert step.get("continue-on-error") is not True


@pytest.mark.arch_guard
def test_emergency_revert_detect_excludes_self() -> None:
    """Emergency revert must not match its own grep instruction lines."""
    text = (repo_root() / ".github/workflows/harden-runner-emergency-revert.yml").read_text(encoding="utf-8")
    assert "--exclude='harden-runner-emergency-revert.yml'" in text


@pytest.mark.arch_guard
def test_creative_agent_scan_uses_publish_output_ref() -> None:
    """publish-creative-agent Trivy must scan the ref emitted by publish, not a hardcoded pin."""
    wf_path = repo_root() / ".github/workflows/publish-creative-agent.yml"
    jobs = _load_workflow(wf_path).get("jobs", {})
    publish_job = jobs.get("publish", {})
    trivy_steps = [
        step for step in _job_steps(publish_job) if _step_uses(step).startswith("aquasecurity/trivy-action@")
    ]
    assert trivy_steps, "publish-creative-agent missing Trivy scan"
    image_ref = trivy_steps[0].get("with", {}).get("image-ref", "")
    assert image_ref == "${{ steps.publish.outputs.ref }}", "Trivy must reference steps.publish.outputs.ref"
    assert "ca70dd1e2a6c" not in wf_path.read_text(encoding="utf-8"), (
        "publish-creative-agent must not hardcode ADCP pin in workflow"
    )


@pytest.mark.arch_guard
def test_scorecard_workflow_present() -> None:
    """OpenSSF Scorecard self-host workflow must exist and omit disable-sudo (needs Docker)."""
    path = repo_root() / ".github/workflows/scorecard.yml"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "ossf/scorecard-action" in text
    assert "publish_results: true" in text
    jobs = _load_workflow(path).get("jobs", {})
    analysis = jobs.get("analysis", {})
    _assert_harden_runner_job("scorecard.yml", "analysis", analysis)


@pytest.mark.arch_guard
def test_checkout_pins_consistent_on_hardened_workflows() -> None:
    """Hardened workflows use a single actions/checkout SHA pin."""
    expected = "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"
    for wf_name in ("ci.yml", "security.yml", "codeql.yml", "release-please.yml", "publish-creative-agent.yml"):
        text = (repo_root() / ".github/workflows" / wf_name).read_text(encoding="utf-8")
        assert expected in text, f"{wf_name} must pin checkout to {expected}"
        assert "actions/checkout@11bd719" not in text, f"{wf_name} still uses stale checkout pin"
