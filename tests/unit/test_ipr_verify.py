"""Unit tests for scripts/ci/ipr_verify.py (IPR Agreement verify gate)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def test_bot_glob_matches_bot_prefix_not_robot():
    allow_res = compile_allowlist(["bot*"])
    assert is_allowed("bot-foo", allow_res)
    assert is_allowed("BotDependabot", allow_res)
    assert not is_allowed("robot", allow_res)
    assert not is_allowed("mybot", allow_res)


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


def test_missing_signer_exits_1(tmp_path: Path, capsys):
    sigs = tmp_path / "sigs.json"
    commits = tmp_path / "commits.json"
    sigs.write_text(json.dumps({"signedContributors": [{"name": "alice"}]}), encoding="utf-8")
    commits.write_text(
        json.dumps(
            [
                {"author": {"login": "alice"}, "committer": {"login": "alice"}},
                {"author": {"login": "bob"}, "committer": {"login": "bob"}},
            ]
        ),
        encoding="utf-8",
    )
    rc = main(
        [
            "verify",
            "--sigs",
            str(sigs),
            "--commits",
            str(commits),
            "--pr-author",
            "alice",
            "--allowlist",
            "bot*,dependabot*",
        ]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "bob" in err.lower()
    assert "alice" not in err.lower() or "not signed" in err.lower()


def test_all_signed_exits_0(tmp_path: Path, capsys):
    sigs = tmp_path / "sigs.json"
    commits = tmp_path / "commits.json"
    sigs.write_text(json.dumps({"signedContributors": [{"name": "Alice"}]}), encoding="utf-8")
    commits.write_text(json.dumps([{"author": {"login": "alice"}}]), encoding="utf-8")
    rc = main(
        [
            "verify",
            "--sigs",
            str(sigs),
            "--commits",
            str(commits),
            "--pr-author",
            "alice",
            "--allowlist",
            "bot*",
        ]
    )
    assert rc == 0
    assert "All contributors have signed" in capsys.readouterr().out


def test_allowlisted_bot_skips_signature(tmp_path: Path):
    sigs = tmp_path / "sigs.json"
    commits = tmp_path / "commits.json"
    sigs.write_text(json.dumps({"signedContributors": []}), encoding="utf-8")
    commits.write_text(json.dumps([{"author": {"login": "dependabot[bot]"}}]), encoding="utf-8")
    # dependabot* matches dependabot[bot]
    rc = main(
        [
            "verify",
            "--sigs",
            str(sigs),
            "--commits",
            str(commits),
            "--allowlist",
            "dependabot*",
        ]
    )
    assert rc == 0


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
