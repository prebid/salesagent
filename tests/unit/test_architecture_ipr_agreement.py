"""Guard: IPR Agreement workflow contract (durability / ADR-003 trust boundary).

Parses the real ``.github/workflows/ipr-agreement.yml`` (and zizmor allowlist)
so tip drift cannot reintroduce ``pull_request_target``, fork the bot allowlist,
or drop post-sign re-verify / re-run without failing Quality Gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.unit._architecture_helpers import repo_root

_IPR_WORKFLOW = repo_root() / ".github" / "workflows" / "ipr-agreement.yml"
_ZIZMOR = repo_root() / ".github" / "zizmor.yml"
_VERIFY_SCRIPT = "scripts/ci/ipr_verify.py"
_RETRY_SCRIPT = "scripts/ci/ipr_gh_retry.sh"


def _load_yaml(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"missing {path}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path} must parse to a mapping"
    return data


def _job_run_text(job: dict[str, Any]) -> str:
    chunks: list[str] = []
    for step in job.get("steps") or []:
        if isinstance(step, dict) and isinstance(step.get("run"), str):
            chunks.append(step["run"])
    return "\n".join(chunks)


class TestIPRAgreementContract:
    @pytest.mark.arch_guard
    def test_ipr_workflow_has_no_pull_request_target(self):
        wf = _load_yaml(_IPR_WORKFLOW)
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

    @pytest.mark.arch_guard
    def test_ipr_bot_allowlist_is_ssot(self):
        wf = _load_yaml(_IPR_WORKFLOW)
        env = wf.get("env") or {}
        assert "IPR_BOT_ALLOWLIST" in env, "workflow env must define IPR_BOT_ALLOWLIST SSOT"
        allowlist = str(env["IPR_BOT_ALLOWLIST"])
        assert allowlist.strip(), "IPR_BOT_ALLOWLIST must be non-empty"
        assert "bot*" in allowlist, "IPR_BOT_ALLOWLIST must include bot* glob"

        jobs = wf.get("jobs") or {}
        assert "ipr-check" in jobs and "ipr-sign" in jobs, "both ipr-check and ipr-sign jobs required"
        check_run = _job_run_text(jobs["ipr-check"])
        sign_run = _job_run_text(jobs["ipr-sign"])
        assert _VERIFY_SCRIPT in check_run, f"ipr-check must invoke {_VERIFY_SCRIPT}"
        assert _VERIFY_SCRIPT in sign_run, f"ipr-sign post-sign verify must invoke {_VERIFY_SCRIPT}"
        assert _RETRY_SCRIPT in check_run and _RETRY_SCRIPT in sign_run, (
            f"both jobs must source {_RETRY_SCRIPT} (single gh_retry_to)"
        )
        assert "ipr_fetch_sigs_and_commits" in check_run and "ipr_fetch_sigs_and_commits" in sign_run, (
            "both jobs must call ipr_fetch_sigs_and_commits (shared sigs/commits fetch)"
        )

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

    @pytest.mark.arch_guard
    def test_path_b_post_sign_reverify_and_rerun(self):
        wf = _load_yaml(_IPR_WORKFLOW)
        sign = (wf.get("jobs") or {}).get("ipr-sign") or {}
        run_text = _job_run_text(sign)
        assert "ipr_verify.py verify" in run_text, "Path B must re-verify after CLA Assistant sign"
        assert "rerun-failed-jobs" in run_text, "Path B must re-run failed ipr-check via rerun-failed-jobs"
        # Soft-swallow of re-run failure must not return (sign green / check red).
        for line in run_text.splitlines():
            if "rerun-failed-jobs" in line or ("actions/runs" in line and "/rerun" in line):
                assert not line.rstrip().endswith("|| true"), (
                    f"re-run API line must not soft-swallow failures: {line!r}"
                )

    @pytest.mark.arch_guard
    def test_ipr_sign_checkouts_default_branch_not_pr_head(self):
        """Path B must checkout the repository default branch (ADR-003).

        A regression to PR-head checkout on the privileged ``ipr-sign`` job would
        execute tip workflow/scripts with a write token — pin ``ref`` explicitly.
        ``ipr-check`` tip checkout under ``pull_request`` remains allowed.
        """
        wf = _load_yaml(_IPR_WORKFLOW)
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

    @pytest.mark.arch_guard
    def test_zizmor_does_not_relist_ipr_for_dangerous_triggers(self):
        ziz = _load_yaml(_ZIZMOR)
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
