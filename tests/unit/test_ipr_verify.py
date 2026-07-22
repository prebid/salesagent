"""Unit tests for scripts/ci/ipr_verify.py (IPR Agreement verify gate)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.ci.ipr_verify import (
    collect_authors,
    compile_allowlist,
    format_run_ids_lines,
    is_allowed,
    main,
    missing_signers,
    parse_allowlist_globs,
    select_failed_workflow_run_ids,
    signed_names,
    verify_ipr,
)
from tests.unit._architecture_helpers import repo_root

_IPR_WORKFLOW = repo_root() / ".github" / "workflows" / "ipr-agreement.yml"

# Canonical bot logins that must match the workflow IPR_BOT_ALLOWLIST globs.
_CANONICAL_BOT_LOGINS = (
    "bot",
    "dependabot[bot]",
    "renovate[bot]",
    "github-actions[bot]",
)


def _write_verify_inputs(
    tmp_path: Path,
    *,
    sigs: dict,
    commits: list,
) -> tuple[Path, Path]:
    """Shared sigs/commits file setup for ``main(["verify", …])`` cases."""
    sigs_path = tmp_path / "sigs.json"
    commits_path = tmp_path / "commits.json"
    sigs_path.write_text(json.dumps(sigs), encoding="utf-8")
    commits_path.write_text(json.dumps(commits), encoding="utf-8")
    return sigs_path, commits_path


def _run_verify(
    tmp_path: Path,
    *,
    sigs: dict,
    commits: list,
    pr_author: str = "alice",
    allowlist: str = "bot*,dependabot*",
) -> int:
    sigs_path, commits_path = _write_verify_inputs(tmp_path, sigs=sigs, commits=commits)
    return main(
        [
            "verify",
            "--sigs",
            str(sigs_path),
            "--commits",
            str(commits_path),
            "--pr-author",
            pr_author,
            "--allowlist",
            allowlist,
        ]
    )


def test_bot_glob_matches_bot_prefix_not_robot():
    allow_res = compile_allowlist(["bot*"])
    assert is_allowed("bot-foo", allow_res)
    assert is_allowed("BotDependabot", allow_res)
    assert not is_allowed("robot", allow_res)
    assert not is_allowed("mybot", allow_res)


def test_exact_glob_requires_full_match_not_prefix():
    """Non-star globs must not suffix-overmatch (``$`` anchor contract)."""
    allow_res = compile_allowlist(["bot"])
    assert is_allowed("bot", allow_res)
    assert is_allowed("BOT", allow_res)
    assert not is_allowed("bot-attacker", allow_res)
    assert not is_allowed("robot", allow_res)


def test_signed_matching_is_case_insensitive():
    signed = signed_names({"signedContributors": [{"name": "Alice"}, {"name": ""}]})
    assert signed == {"alice"}
    missing = missing_signers(["Alice", "Bob"], signed=signed, allow_res=[])
    assert missing == ["Bob"]


def test_empty_allowlist_exits_2(capsys):
    with pytest.raises(SystemExit) as exc:
        verify_ipr(
            sigs_doc={"signedContributors": []},
            commits=[{"author": {"login": "alice"}}],
            pr_author="alice",
            allowlist_raw="  ,  ",
        )
    assert exc.value.code == 2
    assert "IPR_BOT_ALLOWLIST is empty" in capsys.readouterr().err


def test_no_authors_exits_2(capsys):
    with pytest.raises(SystemExit) as exc:
        verify_ipr(
            sigs_doc={"signedContributors": []},
            commits=[{"author": None, "committer": None}],
            pr_author="",
            allowlist_raw="bot*",
        )
    assert exc.value.code == 2
    assert "no PR authors/committers found" in capsys.readouterr().err


def test_missing_signer_exits_1(tmp_path: Path, capsys):
    rc = _run_verify(
        tmp_path,
        sigs={"signedContributors": [{"name": "alice"}]},
        commits=[
            {"author": {"login": "alice"}, "committer": {"login": "alice"}},
            {"author": {"login": "bob"}, "committer": {"login": "bob"}},
        ],
    )
    assert rc == 1
    captured = capsys.readouterr()
    err = captured.err.lower()
    assert "bob" in err
    # Signed author must not leak into the missing stderr message.
    assert "alice" not in err
    assert "missing=['bob']" in captured.out.replace('"', "'")


def test_all_signed_exits_0(tmp_path: Path, capsys):
    rc = _run_verify(
        tmp_path,
        sigs={"signedContributors": [{"name": "Alice"}]},
        commits=[{"author": {"login": "alice"}}],
        allowlist="bot*",
    )
    assert rc == 0
    assert "All contributors have signed" in capsys.readouterr().out


def test_allowlisted_bot_skips_signature(tmp_path: Path):
    # dependabot* matches dependabot[bot]
    rc = _run_verify(
        tmp_path,
        sigs={"signedContributors": []},
        commits=[{"author": {"login": "dependabot[bot]"}}],
        pr_author="",
        allowlist="dependabot*",
    )
    assert rc == 0


def test_main_rejects_non_object_sigs(tmp_path: Path, capsys):
    sigs_path, commits_path = _write_verify_inputs(
        tmp_path,
        sigs=[],  # type: ignore[arg-type]  # intentional bad shape
        commits=[{"author": {"login": "alice"}}],
    )
    # Overwrite sigs with a JSON array (not object).
    sigs_path.write_text("[]", encoding="utf-8")
    rc = main(
        [
            "verify",
            "--sigs",
            str(sigs_path),
            "--commits",
            str(commits_path),
            "--allowlist",
            "bot*",
        ]
    )
    assert rc == 2
    assert "expected signatures object" in capsys.readouterr().err


def test_main_rejects_non_list_commits(tmp_path: Path, capsys):
    sigs_path = tmp_path / "sigs.json"
    commits_path = tmp_path / "commits.json"
    sigs_path.write_text(json.dumps({"signedContributors": []}), encoding="utf-8")
    commits_path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    rc = main(
        [
            "verify",
            "--sigs",
            str(sigs_path),
            "--commits",
            str(commits_path),
            "--allowlist",
            "bot*",
        ]
    )
    assert rc == 2
    assert "expected commits list" in capsys.readouterr().err


def test_failed_run_ids_rejects_non_object_payload(tmp_path: Path, capsys):
    runs = tmp_path / "runs.json"
    runs.write_text("[]", encoding="utf-8")
    rc = main(["failed-run-ids", "--runs", str(runs), "--head-sha", "abc"])
    assert rc == 2
    assert "expected workflow runs object" in capsys.readouterr().err


def test_canonical_bot_logins_match_workflow_allowlist():
    """Membership pin: workflow IPR_BOT_ALLOWLIST globs cover known bot logins."""
    data = yaml.safe_load(_IPR_WORKFLOW.read_text(encoding="utf-8"))
    raw = str((data.get("env") or {})["IPR_BOT_ALLOWLIST"])
    allow_res = compile_allowlist(parse_allowlist_globs(raw))
    for login in _CANONICAL_BOT_LOGINS:
        assert is_allowed(login, allow_res), f"{login!r} must match IPR_BOT_ALLOWLIST={raw!r}"


def test_collect_authors_unions_pr_author_and_commit_logins():
    authors = collect_authors(
        [
            {"author": {"login": "a"}, "committer": {"login": "c"}},
            {"author": None, "committer": {"login": "c"}},
        ],
        "pr-owner",
    )
    assert authors == {"pr-owner", "a", "c"}


def test_parse_allowlist_globs_drops_empty_tokens():
    assert parse_allowlist_globs(" bot* , ,dependabot* ") == ["bot*", "dependabot*"]
    assert parse_allowlist_globs("") == []
    assert parse_allowlist_globs(None) == []


def test_failed_run_ids_and_format_omit_blank_line_when_empty():
    payload = {
        "workflow_runs": [
            {"id": 1, "head_sha": "abc", "status": "completed", "conclusion": "failure"},
            {"id": 2, "head_sha": "abc", "status": "in_progress", "conclusion": None},
            {"id": 3, "head_sha": "def", "status": "completed", "conclusion": "failure"},
            {"id": 4, "head_sha": "abc", "status": "completed", "conclusion": "success"},
        ]
    }
    ids = select_failed_workflow_run_ids(payload, "abc")
    assert ids == ["1"]
    assert format_run_ids_lines(ids) == "1"
    assert format_run_ids_lines([]) == ""
    assert "\n" not in format_run_ids_lines([])


def test_failed_run_ids_cli_prints_nothing_when_empty(tmp_path: Path, capsys):
    runs = tmp_path / "runs.json"
    runs.write_text(json.dumps({"workflow_runs": []}), encoding="utf-8")
    rc = main(["failed-run-ids", "--runs", str(runs), "--head-sha", "abc"])
    assert rc == 0
    assert capsys.readouterr().out == ""
