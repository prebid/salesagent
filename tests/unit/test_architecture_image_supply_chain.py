"""Structural guards for PR 6 image supply-chain hardening (issue #1234)."""

from __future__ import annotations

import re

import pytest

from tests.unit._architecture_helpers import repo_root


@pytest.mark.arch_guard
def test_harden_runner_present_on_workflows() -> None:
    """Every hardened workflow must run harden-runner in audit mode."""
    repo = repo_root()
    count = 0
    for wf in (repo / ".github" / "workflows").glob("*.yml"):
        if wf.name == "harden-runner-emergency-revert.yml":
            continue
        text = wf.read_text()
        if "step-security/harden-runner@" in text:
            count += text.count("step-security/harden-runner@")
            assert "egress-policy: audit" in text, f"{wf.name} missing audit egress-policy"
            assert "disable-sudo-and-containers: true" in text, f"{wf.name} missing CVE-2025-32955 mitigation"
    assert count >= 5, f"expected >=5 harden-runner steps, found {count}"


@pytest.mark.arch_guard
def test_release_workflow_signs_and_attests() -> None:
    """release-please.yml must split publish/sign and include supply-chain fields."""
    text = (repo_root() / ".github/workflows/release-please.yml").read_text()
    assert "build-and-push:" in text
    assert "sign-and-attest:" in text
    assert re.search(r"sha:\s+\$\{\{\s*github\.sha\s*\}\}", text)
    assert "cosign sign --yes --bundle" in text
    assert "actions/attest-build-provenance" in text
    assert "provenance: mode=max" in text
    assert "sbom: true" in text
    assert "aquasecurity/trivy-action" in text


@pytest.mark.arch_guard
def test_dependency_review_configured() -> None:
    """security.yml runs dependency-review with extracted config."""
    repo = repo_root()
    security = (repo / ".github/workflows/security.yml").read_text()
    assert "actions/dependency-review-action" in security
    assert "Security / Dependency Review" in security
    config = repo / ".github/dependency-review-config.yml"
    assert config.exists()
    assert "fail-on-severity: moderate" in config.read_text()


@pytest.mark.arch_guard
def test_codeql_is_gating() -> None:
    """CodeQL analyze must block merges (no continue-on-error)."""
    text = (repo_root() / ".github/workflows/codeql.yml").read_text()
    assert "continue-on-error: true" not in text


@pytest.mark.arch_guard
def test_scorecard_workflow_present() -> None:
    """OpenSSF Scorecard self-host workflow must exist."""
    path = repo_root() / ".github/workflows/scorecard.yml"
    assert path.exists()
    text = path.read_text()
    assert "ossf/scorecard-action" in text
    assert "publish_results: true" in text
