"""Structural guards for PR 6 image supply-chain hardening (issue #1234)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.helpers.bash_stubs import run_bash_with_stubs
from tests.unit._architecture_helpers import assert_single_sha_pin, iter_action_pins, repo_root

_IMAGE_PUBLISHING_WORKFLOWS = frozenset({"release-please.yml", "publish-creative-agent.yml"})
_SIGN_ATTEST_JOB = "sign-and-attest"


def _load_workflow(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _job_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps", [])
    return [step for step in steps if isinstance(step, dict)]


def _step_uses(step: dict[str, Any]) -> str:
    return str(step.get("uses", ""))


def _steps_using(job: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    """Steps of *job* whose ``uses:`` starts with *prefix* (``owner/action@``)."""
    return [step for step in _job_steps(job) if _step_uses(step).startswith(prefix)]


def _step_inputs(job: dict[str, Any], prefix: str, key: str) -> list[Any]:
    """The ``with: <key>`` input of each step of *job* using *prefix*, as written; ``""`` where it is absent."""
    return [step.get("with", {}).get(key, "") for step in _steps_using(job, prefix)]


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
    return _steps_using(job, "step-security/harden-runner@")


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


_ATTEST = "actions/attest-build-provenance@"
_TRIVY = "aquasecurity/trivy-action@"


def _resolve_env(env: dict[str, Any], value: str) -> str:
    """Expand a whole-value ``${{ env.NAME }}`` reference from *env*."""
    match = re.fullmatch(r"\$\{\{\s*env\.(\w+)\s*\}\}", value)
    return str(env.get(match.group(1), value)) if match else value


def _assert_sign_and_attest_job(job: dict[str, Any], workflow_env: dict[str, Any] | None = None) -> None:
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
    env = {**(workflow_env or {}), **job.get("env", {})}
    subjects = [_resolve_env(env, subject) for subject in _step_inputs(job, _ATTEST, "subject-name")]
    assert len(subjects) >= 2, "sign-and-attest must attest ghcr.io and Docker Hub images"
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
    gate_steps = [step for step in _steps_using(job, _TRIVY) if "pre-push" in (step.get("name") or "").lower()]
    assert len(gate_steps) >= 2, "build-and-push missing per-arch pre-push Trivy steps"
    assert set(_step_inputs(job, _TRIVY, "exit-code")) == {1}, "every Trivy step in build-and-push must exit-code: 1"
    arm64_gate = [step for step in gate_steps if "arm64" in (step.get("name") or "").lower()]
    assert arm64_gate, "build-and-push missing arm64 pre-push Trivy gate"
    assert arm64_gate[0].get("env", {}).get("TRIVY_PLATFORM") == "linux/arm64", (
        "arm64 pre-push Trivy must set TRIVY_PLATFORM=linux/arm64"
    )
    pushes = _step_inputs(job, "docker/build-push-action@", "push")
    assert len(pushes) >= 3, "build-and-push must gate amd64, gate arm64, then push multi-arch"
    assert pushes[-1] is True, "final build step must push to registry"

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
    workflow = _release_workflow()
    jobs = workflow.get("jobs", {})
    assert "build-and-push" in jobs
    assert _SIGN_ATTEST_JOB in jobs
    _assert_build_and_push_job(jobs["build-and-push"])
    _assert_sign_and_attest_job(jobs[_SIGN_ATTEST_JOB], workflow.get("env"))

    # build-and-push's image_tags output contains the Docker Hub user (a secret), so GitHub drops it
    # between jobs; signing from it signed nothing in the 3.0.1 release.
    build_outputs = jobs["build-and-push"].get("outputs", {})
    assert "image_tags" not in build_outputs, "build-and-push must not export image_tags (dropped as secret-bearing)"
    assert "image_digest" in build_outputs

    text = _release_workflow_text()
    assert "ghcr_image:" not in text
    assert "concurrency:" in text
    assert "provenance: mode=max" in text
    assert "sbom: true" in text


_RELEASE_WORKFLOW = ".github/workflows/release-please.yml"


def _release_workflow_text() -> str:
    return (repo_root() / _RELEASE_WORKFLOW).read_text(encoding="utf-8")


def _release_workflow() -> dict[str, Any]:
    return _load_workflow(repo_root() / _RELEASE_WORKFLOW)


def _release_sign_job() -> dict[str, Any]:
    return _release_workflow()["jobs"][_SIGN_ATTEST_JOB]


def _release_step(step_id: str) -> dict[str, Any]:
    steps = [step for step in _job_steps(_release_sign_job()) if step.get("id") == step_id]
    assert len(steps) == 1, f"release sign-and-attest must have exactly one step with id {step_id!r}"
    return steps[0]


def _run_release_step(
    tmp_path: Path, step_id: str, stubs: dict[str, str], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run one sign-and-attest step's ``run:`` script under bash, with *stubs* first on ``PATH``."""
    script = tmp_path / f"{step_id}.sh"
    script.write_text(_release_step(step_id)["run"], encoding="utf-8", newline="\n")
    return run_bash_with_stubs([script.as_posix()], stub_dir=tmp_path / "bin", stubs=stubs, env=env)


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


_DIGEST = "sha256:" + "0" * 64
_OTHER_DIGEST = "sha256:" + "1" * 64
_TWO_IMAGES = "ghcr.io/org/repo docker.io/user/salesagent"

# Logs every call; fails any call whose arguments contain $COSIGN_FAIL_FOR (when set).
_STUB_COSIGN = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$COSIGN_LOG"
if [[ -n "${COSIGN_FAIL_FOR:-}" && "$*" == *"$COSIGN_FAIL_FOR"* ]]; then exit 1; fi
exit 0
"""
# The sign step sleeps 30s between attempts.
_STUB_SLEEP = "#!/usr/bin/env bash\nexit 0\n"


def _run_sign_script(tmp_path: Path, images: str, *, fail_for: str = "") -> tuple[int, list[str]]:
    """Run the sign step with a stub cosign; return its exit code and the ``<image>@<digest>`` of each sign call."""
    log = tmp_path / "cosign.log"
    result = _run_release_step(
        tmp_path,
        "sign",
        {"cosign": _STUB_COSIGN, "sleep": _STUB_SLEEP},
        {
            "COSIGN_LOG": log.as_posix(),
            "COSIGN_FAIL_FOR": fail_for,
            "DIGEST": _DIGEST,
            "IMAGES": images,
            "BUNDLE_DIR": (tmp_path / "bundles").as_posix(),
        },
    )
    return result.returncode, [call.split()[-1] for call in _read_lines(log) if call.startswith("sign ")]


@pytest.mark.arch_guard
def test_release_sign_script_signs_each_image_once_at_the_digest(tmp_path: Path) -> None:
    """Two image names give one cosign sign call per <image>@<digest>, and the step passes."""
    returncode, signed = _run_sign_script(tmp_path, _TWO_IMAGES)
    assert returncode == 0
    assert signed == [f"ghcr.io/org/repo@{_DIGEST}", f"docker.io/user/salesagent@{_DIGEST}"]


@pytest.mark.arch_guard
@pytest.mark.parametrize("images", ["", "   "])
def test_release_sign_script_fails_when_there_is_nothing_to_sign(tmp_path: Path, images: str) -> None:
    """An empty image list fails the step; 3.0.1 signed nothing and passed."""
    returncode, signed = _run_sign_script(tmp_path, images)
    assert returncode != 0
    assert signed == []


@pytest.mark.arch_guard
def test_release_sign_script_fails_when_one_registry_fails(tmp_path: Path) -> None:
    """A registry that refuses all three attempts fails the step, after the other one was signed."""
    returncode, signed = _run_sign_script(tmp_path, _TWO_IMAGES, fail_for="docker.io/")
    assert returncode != 0
    assert signed == [f"ghcr.io/org/repo@{_DIGEST}"] + [f"docker.io/user/salesagent@{_DIGEST}"] * 3


# Answers token requests, and manifest HEADs with the Docker-Content-Digest set per registry.
_STUB_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$CURL_LOG"
case "$*" in
  *ghcr.io/token*|*auth.docker.io/token*) echo '{"token":"stub-token"}' ;;
  *https://ghcr.io/v2/*) printf 'HTTP/2 200\\r\\ndocker-content-digest: %s\\r\\n\\r\\n' "$STUB_GHCR_DIGEST" ;;
  *https://registry-1.docker.io/v2/*) printf 'HTTP/1.1 200 OK\\r\\nDocker-Content-Digest: %s\\r\\n\\r\\n' "$STUB_DOCKERHUB_DIGEST" ;;
  *) exit 22 ;;
esac
"""
# `gh release view v<version> ...` succeeds only for $STUB_RELEASE.
_STUB_GH = """#!/usr/bin/env bash
case "$*" in "release view v${STUB_RELEASE:-none} "*) exit 0 ;; esac
exit 1
"""


def _run_resolve_step(tmp_path: Path, **env: str) -> tuple[int, list[str], list[str], list[str]]:
    """Run the digest step with stub gh and curl; return its exit code, its outputs, the curl calls, and its ERROR lines."""
    output, curl_log = tmp_path / "github_output", tmp_path / "curl.log"
    result = _run_release_step(
        tmp_path,
        "digest",
        {"curl": _STUB_CURL, "gh": _STUB_GH},
        {
            "GITHUB_OUTPUT": output.as_posix(),
            "GITHUB_REPOSITORY": "org/repo",
            "GHCR_IMAGE": "ghcr.io/org/repo",
            "DOCKERHUB_IMAGE": "docker.io/user/salesagent",
            "GH_TOKEN": "t",
            "GHCR_USER": "u",
            "DH_USER": "user",
            "DH_PASS": "p",
            "CURL_LOG": curl_log.as_posix(),
            "BUILD_DIGEST": "",
            "VERSION": "",
            "STUB_RELEASE": "3.0.1",
            "STUB_GHCR_DIGEST": _DIGEST,
            "STUB_DOCKERHUB_DIGEST": _DIGEST,
            **env,
        },
    )
    errors = [line for line in (result.stdout + result.stderr).splitlines() if line.startswith("ERROR:")]
    return result.returncode, _read_lines(output), _read_lines(curl_log), errors


@pytest.mark.arch_guard
def test_release_digest_on_push_is_the_built_digest(tmp_path: Path) -> None:
    """After a release build, the job signs the digest build-and-push pushed, without asking a registry."""
    returncode, outputs, curl_calls, _ = _run_resolve_step(tmp_path, EVENT_NAME="push", BUILD_DIGEST=_DIGEST)
    assert returncode == 0
    assert outputs == [f"digest={_DIGEST}"]
    assert curl_calls == []


@pytest.mark.arch_guard
def test_release_digest_on_dispatch_is_the_released_tag_in_both_registries(tmp_path: Path) -> None:
    """A recovery run signs the digest that <image>:<version> names in both registries."""
    returncode, outputs, curl_calls, _ = _run_resolve_step(tmp_path, EVENT_NAME="workflow_dispatch", VERSION="3.0.1")
    assert returncode == 0
    assert outputs == [f"digest={_DIGEST}"]
    manifests = [call.split()[-1] for call in curl_calls if "/manifests/" in call]
    assert manifests == [
        "https://ghcr.io/v2/org/repo/manifests/3.0.1",
        "https://registry-1.docker.io/v2/user/salesagent/manifests/3.0.1",
    ]


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    ("env", "error"),
    [
        pytest.param(
            {"EVENT_NAME": "push", "BUILD_DIGEST": ""}, "ERROR: not an image digest: ''", id="push-without-digest"
        ),
        pytest.param(
            {"EVENT_NAME": "workflow_dispatch", "VERSION": "3.0.2"},
            "ERROR: no v3.0.2 release in org/repo",
            id="no-such-release",
        ),
        pytest.param(
            {"EVENT_NAME": "workflow_dispatch", "VERSION": "3.0.1", "STUB_DOCKERHUB_DIGEST": _OTHER_DIGEST},
            f"ERROR: 3.0.1 is '{_DIGEST}' on ghcr.io and '{_OTHER_DIGEST}' on Docker Hub",
            id="registries-disagree",
        ),
        pytest.param(
            {
                "EVENT_NAME": "workflow_dispatch",
                "VERSION": "3.0.1",
                "STUB_GHCR_DIGEST": "",
                "STUB_DOCKERHUB_DIGEST": "",
            },
            "ERROR: 3.0.1 is '' on ghcr.io and '' on Docker Hub",
            id="tag-not-found",
        ),
        # A release named `vlatest` exists, so only the format check refuses this input.
        pytest.param(
            {"EVENT_NAME": "workflow_dispatch", "VERSION": "latest", "STUB_RELEASE": "latest"},
            "ERROR: not a release version: latest",
            id="not-a-version",
        ),
    ],
)
def test_release_digest_refuses_anything_but_a_release(tmp_path: Path, env: dict[str, str], error: str) -> None:
    """No digest is produced for a missing release, a tag the registries disagree on, or a malformed input."""
    returncode, outputs, _, errors = _run_resolve_step(tmp_path, **env)
    assert returncode != 0
    assert outputs == []
    assert errors == [error]


@pytest.mark.arch_guard
def test_release_image_names_have_one_source() -> None:
    """The build, signing, both attestations and the scan read the image names from the workflow env."""
    workflow = _release_workflow()
    assert workflow.get("env", {}).get("GHCR_IMAGE") == "ghcr.io/${{ github.repository }}"
    assert workflow.get("env", {}).get("DOCKERHUB_IMAGE") == "docker.io/${{ secrets.DOCKERHUB_USER }}/salesagent"
    names = ["${{ env.GHCR_IMAGE }}", "${{ env.DOCKERHUB_IMAGE }}"]

    meta_images = _step_inputs(workflow["jobs"]["build-and-push"], "docker/metadata-action@", "images")
    assert len(meta_images) == 1
    assert str(meta_images[0]).strip().splitlines() == names
    job = _release_sign_job()
    assert _release_step("sign").get("env", {}).get("IMAGES") == " ".join(names)
    assert sorted(_step_inputs(job, _ATTEST, "subject-name")) == sorted(names)
    assert _step_inputs(job, _TRIVY, "image-ref") == ["${{ env.GHCR_IMAGE }}@${{ steps.digest.outputs.digest }}"]

    text = _release_workflow_text()
    assert text.count("ghcr.io/${{ github.repository }}") == 1
    assert text.count("${{ secrets.DOCKERHUB_USER }}/salesagent") == 1


@pytest.mark.arch_guard
def test_release_sign_and_attest_signs_the_resolved_digest() -> None:
    """Signing and both attestations read the digest step's output, so a dispatch signs only what it resolved."""
    job = _release_sign_job()
    resolved = "${{ steps.digest.outputs.digest }}"
    assert _release_step("sign").get("env", {}).get("DIGEST") == resolved
    assert _step_inputs(job, _ATTEST, "subject-digest") == [resolved, resolved]


@pytest.mark.arch_guard
def test_release_dispatch_takes_a_version_runs_from_main_and_attests() -> None:
    """A dispatch names a release version, runs from main only, and attests like a release run."""
    workflow = _release_workflow()
    triggers = workflow.get("on", workflow.get(True, {}))  # PyYAML reads the bare key `on` as True
    assert set(triggers["workflow_dispatch"]["inputs"]) == {"version"}, (
        "a dispatch must name a release, not a digest: any digest would get the main-branch signature"
    )
    job = _release_sign_job()
    assert "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in str(job.get("if", "")), (
        "dispatch from a branch signs with an identity ADR-007's cosign verify rejects"
    )
    attest_steps = _steps_using(job, _ATTEST)
    assert attest_steps
    for step in attest_steps:
        assert "if" not in step, (
            "a recovery run attests too, so `gh attestation verify` works for the release it recovers"
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
    image_refs = _step_inputs(publish_job, _TRIVY, "image-ref")
    assert image_refs, "publish-creative-agent missing Trivy scan"
    assert image_refs[0] == "${{ steps.publish.outputs.ref }}", "Trivy must reference steps.publish.outputs.ref"
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
@pytest.mark.parametrize("action", ["actions/checkout", "actions/cache", "astral-sh/setup-uv"])
def test_action_pinned_to_one_full_sha(action: str) -> None:
    """Every git-tracked workflow and action pins *action* to the same full commit SHA.

    The SHA itself is not named here: Dependabot bumps it, and pinact checks it
    matches a tag. This guard only requires every reference to be a full SHA and
    all of them to agree, so a partial bump fails.
    """
    assert_single_sha_pin(iter_action_pins(repo_root(), action), action)


_KNOWN_BAD_CHECKOUT_PINS = {
    "partial bump": (
        [
            (Path(".github/workflows/ci.yml"), "1" * 40),
            (Path(".github/workflows/scorecard.yml"), "2" * 40),
        ],
        "pinned to different SHAs",
    ),
    "tag ref": ([(Path(".github/workflows/ci.yml"), "v7")], "full commit SHA"),
}


@pytest.mark.arch_guard
@pytest.mark.parametrize("case", sorted(_KNOWN_BAD_CHECKOUT_PINS))
def test_single_sha_pin_detector_catches_known_bad_pins(case: str) -> None:
    """Mutation self-test: a partial bump and a tag ref must both fail the guard."""
    pins, message = _KNOWN_BAD_CHECKOUT_PINS[case]
    with pytest.raises(AssertionError, match=message):
        assert_single_sha_pin(pins, "actions/checkout")


@pytest.mark.arch_guard
def test_single_sha_pin_detector_refuses_no_pins() -> None:
    """Non-vacuity: an action with no references at all fails rather than passing."""
    with pytest.raises(AssertionError, match="non-vacuity"):
        assert_single_sha_pin([], "actions/checkout")
