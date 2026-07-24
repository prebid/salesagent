"""Guard: IPR Agreement workflow contract (durability / ADR-003 trust boundary).

Parses the real ``.github/workflows/ipr-agreement.yml`` (and zizmor allowlist)
so tip drift cannot reintroduce ``pull_request_target``, fork the bot allowlist,
or drop post-sign re-verify / re-run without failing Quality Gate.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from tests.unit._architecture_helpers import (
    ipr_agreement_workflow_path,
    load_yaml_mapping,
    repo_root,
)

_IPR_WORKFLOW = ipr_agreement_workflow_path()
_ZIZMOR = repo_root() / ".github" / "zizmor.yml"
_RETRY_SH = repo_root() / "scripts" / "ci" / "ipr_gh_retry.sh"
_VERIFY_SCRIPT = "scripts/ci/ipr_verify.py"
_RETRY_SCRIPT = "scripts/ci/ipr_gh_retry.sh"

# Keep in lockstep with ``gh_retry_to`` in ipr_gh_retry.sh (and the Path B health probe).
_IPR_RETRY_MAX = 5
_IPR_RETRY_SLEEP_FACTOR = 15


def _job_run_text(job: dict[str, Any]) -> str:
    chunks: list[str] = []
    for step in job.get("steps") or []:
        if isinstance(step, dict) and isinstance(step.get("run"), str):
            chunks.append(step["run"])
    return "\n".join(chunks)


def _soft_swallow_suffix(line: str) -> bool:
    """True if a shell line soft-swallows failure (``|| true`` / ``|| :`` / trailing ``set +e``)."""
    stripped = line.rstrip()
    if stripped.endswith("|| true") or stripped.endswith("||:"):
        return True
    if stripped.endswith("|| :"):
        return True
    return False


class TestIPRAgreementContract:
    pytestmark = pytest.mark.arch_guard

    def test_ipr_workflow_has_no_pull_request_target(self):
        wf = load_yaml_mapping(_IPR_WORKFLOW)
        on = wf.get("on") or wf.get(True)  # YAML may parse `on` as True
        assert on is not None, "ipr-agreement.yml must declare an `on:` trigger block"
        if isinstance(on, dict):
            assert "pull_request_target" not in on, (
                "ipr-agreement.yml must not use pull_request_target (ADR-003); "
                "verify uses pull_request, sign uses issue_comment."
            )
            assert "pull_request" in on, "Path A verify must trigger on pull_request"
            assert "issue_comment" in on, "Path B sign must trigger on issue_comment"
        else:
            raise AssertionError(f"unexpected on: shape {type(on).__name__}")

    def test_ipr_bot_allowlist_is_ssot(self):
        wf = load_yaml_mapping(_IPR_WORKFLOW)
        env = wf.get("env") or {}
        assert "IPR_BOT_ALLOWLIST" in env, "workflow env must define IPR_BOT_ALLOWLIST SSOT"
        allowlist = str(env["IPR_BOT_ALLOWLIST"])
        assert allowlist.strip(), "IPR_BOT_ALLOWLIST must be non-empty"
        assert "bot*" in allowlist, "IPR_BOT_ALLOWLIST must include bot* glob"

        jobs = wf.get("jobs") or {}
        assert "ipr-check" in jobs and "ipr-sign" in jobs, "both ipr-check and ipr-sign jobs required"
        check_run = _job_run_text(jobs["ipr-check"])
        sign_run = _job_run_text(jobs["ipr-sign"])
        assert _RETRY_SCRIPT in check_run and _RETRY_SCRIPT in sign_run, (
            f"both jobs must source {_RETRY_SCRIPT} (shared ipr_verify_pr / gh_retry_to)"
        )
        assert "ipr_verify_pr" in check_run and "ipr_verify_pr" in sign_run, (
            "both jobs must call ipr_verify_pr (shared verify orchestration)"
        )
        # Leaf helpers live only in the shared shell — workflows must not re-copy glue.
        retry_src = _RETRY_SH.read_text(encoding="utf-8")
        assert "ipr_verify_pr()" in retry_src and "ipr_fetch_sigs_and_commits()" in retry_src, (
            "ipr_gh_retry.sh must define ipr_verify_pr and ipr_fetch_sigs_and_commits"
        )
        assert _VERIFY_SCRIPT in retry_src, f"ipr_verify_pr must invoke {_VERIFY_SCRIPT}"
        assert "ipr_fetch_sigs_and_commits" not in check_run and "ipr_fetch_sigs_and_commits" not in sign_run, (
            "jobs must not inline ipr_fetch_sigs_and_commits (route via ipr_verify_pr)"
        )
        assert f"python3 {_VERIFY_SCRIPT} verify" not in check_run and (
            f"python3 {_VERIFY_SCRIPT} verify" not in sign_run
        ), "jobs must not inline ipr_verify.py verify (route via ipr_verify_pr)"

        # CLA Assistant allowlist must reference the SSOT env, not a forked literal.
        sign_steps = jobs["ipr-sign"].get("steps") or []
        cla = next(
            (
                s
                for s in sign_steps
                if isinstance(s, dict) and "contributor-assistant/github-action" in str(s.get("uses", ""))
            ),
            None,
        )
        assert cla is not None, "ipr-sign must run contributor-assistant/github-action"
        with_block = cla.get("with") or {}
        assert str(with_block.get("allowlist", "")) == "${{ env.IPR_BOT_ALLOWLIST }}", (
            "CLA Assistant allowlist must reference env.IPR_BOT_ALLOWLIST SSOT"
        )

    def test_path_b_post_sign_reverify_and_rerun(self):
        wf = load_yaml_mapping(_IPR_WORKFLOW)
        sign = (wf.get("jobs") or {}).get("ipr-sign") or {}
        run_text = _job_run_text(sign)
        assert "ipr_verify_pr" in run_text, "Path B must re-verify after CLA Assistant sign via ipr_verify_pr"
        assert "rerun-failed-jobs" in run_text, "Path B must re-run failed ipr-check via rerun-failed-jobs"
        # Soft-swallow of re-run failure must not return (sign green / check red).
        for line in run_text.splitlines():
            if "rerun-failed-jobs" in line or ("actions/runs" in line and "/rerun" in line):
                assert not _soft_swallow_suffix(line), f"re-run API line must not soft-swallow failures: {line!r}"

    def test_ipr_sign_checkouts_default_branch_not_pr_head(self):
        """Path B must checkout the repository default branch (ADR-003).

        A regression to PR-head checkout on the privileged ``ipr-sign`` job would
        execute tip workflow/scripts with a write token — pin ``ref`` explicitly.
        ``ipr-check`` tip checkout under ``pull_request`` remains allowed.
        """
        wf = load_yaml_mapping(_IPR_WORKFLOW)
        sign = (wf.get("jobs") or {}).get("ipr-sign") or {}
        steps = sign.get("steps") or []
        checkouts = [s for s in steps if isinstance(s, dict) and str(s.get("uses", "")).startswith("actions/checkout")]
        assert checkouts, "ipr-sign must include an actions/checkout step"
        refs = [str((s.get("with") or {}).get("ref", "")) for s in checkouts]
        assert any("default_branch" in ref for ref in refs), (
            f"ipr-sign checkout must set ref to github.event.repository.default_branch (got refs={refs!r})"
        )
        # No bare tip checkout (missing ref) on the privileged job.
        assert all((s.get("with") or {}).get("ref") for s in checkouts), (
            "ipr-sign checkout must not omit ref (would default to the triggering ref)"
        )
        # Reject a second checkout on PR-head / issue-event tip refs beside default_branch.
        assert not any(tok in ref for ref in refs for tok in ("pull_request", ".head", "refs/pull", "event.issue")), (
            f"ipr-sign must not checkout PR-head-derived refs (got refs={refs!r})"
        )
        # Privileged job: no repository override, credentials never persisted.
        assert all(not (s.get("with") or {}).get("repository") for s in checkouts), (
            "ipr-sign checkout must not override repository (would run fork code with the write token)"
        )
        assert all((s.get("with") or {}).get("persist-credentials") is False for s in checkouts), (
            "ipr-sign checkout must set persist-credentials: false"
        )
        # The gate consumes ipr_verify_pr's exit code; keep it fail-closed.
        verify_steps = 0
        for step in steps:
            if not isinstance(step, dict):
                continue
            run = step.get("run") or ""
            if "ipr_verify_pr" in run:
                verify_steps += 1
                assert "set -euo pipefail" in run, "verify step must keep set -euo pipefail"
                assert step.get("continue-on-error") is not True, "verify step must not continue-on-error"
                # Soft-swallow / set +e would mask a failed verify exit.
                assert "set +e" not in run, "verify step must not disable errexit with set +e"
                for line in run.splitlines():
                    if "ipr_verify_pr" in line:
                        assert not _soft_swallow_suffix(line), f"verify invocation must not soft-swallow: {line!r}"
        assert verify_steps >= 1, "ipr-sign must include a run step that invokes ipr_verify_pr"

        # Invisible PR-head checkout via shell (not actions/checkout).
        for step in steps:
            if not isinstance(step, dict):
                continue
            run = step.get("run") or ""
            assert "gh pr checkout" not in run, "ipr-sign must not gh pr checkout (would run PR-head code)"
            assert not re.search(r"git\s+fetch\s+.*pull/\d+/head", run), (
                "ipr-sign must not git fetch pull/N/head (would run PR-head code)"
            )

    def test_ipr_retry_constants_match_health_probe(self):
        """Path B health probe backoff must match ``gh_retry_to`` (same max / sleep factor)."""
        retry_src = _RETRY_SH.read_text(encoding="utf-8")
        assert f"local max={_IPR_RETRY_MAX}" in retry_src, f"ipr_gh_retry.sh must keep max={_IPR_RETRY_MAX}"
        assert f"attempt * {_IPR_RETRY_SLEEP_FACTOR}" in retry_src or (
            f"attempt*{_IPR_RETRY_SLEEP_FACTOR}" in retry_src
        ), f"ipr_gh_retry.sh must keep attempt*{_IPR_RETRY_SLEEP_FACTOR} sleep"

        wf = load_yaml_mapping(_IPR_WORKFLOW)
        sign = (wf.get("jobs") or {}).get("ipr-sign") or {}
        probe = next(
            (
                s
                for s in (sign.get("steps") or [])
                if isinstance(s, dict) and "Wait for GitHub API" in str(s.get("name", ""))
            ),
            None,
        )
        assert probe is not None, "ipr-sign must include Wait for GitHub API step"
        run = probe.get("run") or ""
        assert f"max={_IPR_RETRY_MAX}" in run, f"health probe must use max={_IPR_RETRY_MAX} (same as gh_retry_to)"
        assert f"attempt * {_IPR_RETRY_SLEEP_FACTOR}" in run or (f"attempt*{_IPR_RETRY_SLEEP_FACTOR}" in run), (
            f"health probe must use attempt*{_IPR_RETRY_SLEEP_FACTOR} (same as gh_retry_to)"
        )

    def test_path_a_uses_module_default_missing_message(self):
        """Path A must not fork ``--missing-message`` (module argparse default is SSOT)."""
        wf = load_yaml_mapping(_IPR_WORKFLOW)
        check_run = _job_run_text((wf.get("jobs") or {}).get("ipr-check") or {})
        assert "--missing-message" not in check_run, (
            "Path A must omit --missing-message so ipr_verify.py argparse default cannot drift"
        )

    def test_ipr_sign_harden_runner_disables_sudo_and_containers(self):
        wf = load_yaml_mapping(_IPR_WORKFLOW)
        sign = (wf.get("jobs") or {}).get("ipr-sign") or {}
        harden = next(
            (s for s in (sign.get("steps") or []) if isinstance(s, dict) and "harden-runner" in str(s.get("uses", ""))),
            None,
        )
        assert harden is not None, "ipr-sign must use harden-runner"
        with_block = harden.get("with") or {}
        assert with_block.get("disable-sudo-and-containers") is True, (
            "ipr-sign harden-runner must set disable-sudo-and-containers: true (write-token job)"
        )

    def test_ci_ipr_gate_reads_allowlist_ssot(self):
        """CI ipr-gate must extract IPR_BOT_ALLOWLIST from ipr-agreement.yml (no forked literal)."""
        from scripts.ci.workflow_helpers import load_ci_workflow

        job = (load_ci_workflow().get("jobs") or {}).get("ipr-gate") or {}
        assert job, "ci.yml must define ipr-gate (Summary merge-gate for unsigned PRs)"
        run = _job_run_text(job)
        assert "ipr-agreement.yml" in run and "IPR_BOT_ALLOWLIST" in run, (
            "ipr-gate must derive IPR_BOT_ALLOWLIST from ipr-agreement.yml SSOT"
        )
        assert 'IPR_BOT_ALLOWLIST: "bot*' not in str(job), (
            "ipr-gate must not hardcode a forked IPR_BOT_ALLOWLIST env literal"
        )
        assert _RETRY_SCRIPT in run, f"ipr-gate must source {_RETRY_SCRIPT}"
        assert "ipr_verify_pr" in run, "ipr-gate must call ipr_verify_pr (shared verify orchestration)"
        assert "ipr_fetch_sigs_and_commits" not in run, (
            "ipr-gate must not inline ipr_fetch_sigs_and_commits (route via ipr_verify_pr)"
        )
        assert f"python3 {_VERIFY_SCRIPT} verify" not in run, (
            "ipr-gate must not inline ipr_verify.py verify (route via ipr_verify_pr)"
        )

    def test_zizmor_does_not_relist_ipr_for_dangerous_triggers(self):
        ziz = load_yaml_mapping(_ZIZMOR)
        rules = ziz.get("rules") or {}
        dangerous = (rules.get("dangerous-triggers") or {}).get("ignore") or []
        assert isinstance(dangerous, list) and dangerous, (
            "zizmor dangerous-triggers.ignore must be a non-empty list (non-vacuous)"
        )
        assert "ipr-agreement.yml" not in dangerous, (
            "ipr-agreement.yml must not be re-listed under dangerous-triggers.ignore "
            "(it no longer uses pull_request_target)"
        )
        assert "pr-title-check.yml" in dangerous, "pr-title-check.yml remains the ADR-003 PRT allowlist entry (anchor)"
