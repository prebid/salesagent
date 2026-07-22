"""IPR signature verification used by ``.github/workflows/ipr-agreement.yml``.

Both Path A (``pull_request`` check) and Path B (post-sign re-verify) invoke this
module after fetching ``signatures/ipr-signatures.json`` and the PR commits
payload via ``gh``. Keep allowlist expansion and signed-contributor matching
here so the two workflow jobs cannot drift.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any


def parse_allowlist_globs(raw: str | None) -> list[str]:
    """Split comma-separated allowlist globs; empty tokens are dropped."""
    return [g.strip() for g in (raw or "").split(",") if g.strip()]


def compile_allowlist(globs: Sequence[str]) -> list[re.Pattern[str]]:
    """Compile CLA-style globs (``*`` → ``.*``) as case-insensitive full-match regexes.

    Patterns are anchored with ``^…$`` so a non-star entry like ``bot`` matches only
    ``bot`` (not ``bot-attacker``). ``re.match`` alone would also reject a leading
    mismatch (``robot`` vs ``bot*``), but the trailing ``$`` is what blocks suffix
    over-match on exact globs.
    """
    return [re.compile("^" + re.escape(g).replace(r"\*", ".*") + "$", re.I) for g in globs]


def is_allowed(login: str, allow_res: Sequence[re.Pattern[str]]) -> bool:
    return any(r.match(login) for r in allow_res)


def signed_names(sigs_doc: dict[str, Any]) -> set[str]:
    """Lower-cased contributor names from the CLA Assistant signatures document.

    Assumes CLA Assistant writes the GitHub *login* into
    ``signedContributors[].name`` (not display name). Matching is case-insensitive
    against author logins collected from the PR commits API.
    """
    return {(c.get("name") or "").lower() for c in sigs_doc.get("signedContributors") or [] if c.get("name")}


def collect_authors(commits: Sequence[dict[str, Any]], pr_author: str | None) -> set[str]:
    """Union of PR author login and each commit's author/committer logins."""
    authors: set[str] = set()
    author = (pr_author or "").strip()
    if author:
        authors.add(author)
    for commit in commits:
        for key in ("author", "committer"):
            login = (commit.get(key) or {}).get("login")
            if login:
                authors.add(login)
    return authors


def missing_signers(
    authors: Iterable[str],
    *,
    signed: set[str],
    allow_res: Sequence[re.Pattern[str]],
) -> list[str]:
    """Authors who are neither allowlisted nor present in signedContributors."""
    return sorted(a for a in authors if not is_allowed(a, allow_res) and a.lower() not in signed)


def verify_ipr(
    *,
    sigs_doc: dict[str, Any],
    commits: list[dict[str, Any]],
    pr_author: str | None,
    allowlist_raw: str | None,
) -> tuple[set[str], list[str], set[str]]:
    """Return ``(authors, missing, signed)``. Raises ``SystemExit`` with code 2 on bad input."""
    allow_globs = parse_allowlist_globs(allowlist_raw)
    if not allow_globs:
        print("IPR_BOT_ALLOWLIST is empty — refusing to verify", file=sys.stderr)
        raise SystemExit(2)

    authors = collect_authors(commits, pr_author)
    if not authors:
        print("no PR authors/committers found — cannot verify IPR", file=sys.stderr)
        raise SystemExit(2)

    allow_res = compile_allowlist(allow_globs)
    signed = signed_names(sigs_doc)
    missing = missing_signers(authors, signed=signed, allow_res=allow_res)
    return authors, missing, signed


def select_failed_workflow_run_ids(runs_payload: dict[str, Any], head_sha: str) -> list[str]:
    """Completed workflow runs for ``head_sha`` that concluded failure/cancelled/timed_out."""
    ids: list[str] = []
    for run in runs_payload.get("workflow_runs") or []:
        if run.get("head_sha") != head_sha:
            continue
        if run.get("status") != "completed":
            continue
        if run.get("conclusion") in ("failure", "cancelled", "timed_out"):
            ids.append(str(run["id"]))
    return ids


def format_run_ids_lines(ids: Sequence[str]) -> str:
    """Newline-joined run ids for ``mapfile``; empty input yields empty string (no blank line)."""
    return "\n".join(ids) if ids else ""


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify IPR signatures for a pull request.")
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify", help="Check all PR authors/committers have signed.")
    verify.add_argument("--sigs", type=Path, required=True, help="Path to decoded ipr-signatures.json")
    verify.add_argument("--commits", type=Path, required=True, help="Path to PR commits JSON array")
    verify.add_argument(
        "--pr-author",
        default="",
        help="PR author login (falls back to PR_AUTHOR env)",
    )
    verify.add_argument(
        "--allowlist",
        default="",
        help="Comma-separated bot allowlist (falls back to IPR_BOT_ALLOWLIST env)",
    )
    verify.add_argument(
        "--missing-message",
        default="IPR Policy not signed by: {missing}. Comment on the PR with exactly: I have read the IPR Policy",
        help="stderr template when unsigned authors remain; ``{missing}`` is replaced",
    )
    verify.add_argument(
        "--ok-message",
        default="All contributors have signed the IPR Policy.",
        help="stdout message on success",
    )

    runs = sub.add_parser("failed-run-ids", help="Print failed IPR workflow run ids for a head SHA.")
    runs.add_argument("--runs", type=Path, required=True, help="Path to workflow runs list JSON")
    runs.add_argument("--head-sha", required=True)

    args = parser.parse_args(argv)

    if args.command == "failed-run-ids":
        payload = _load_json(args.runs)
        if not isinstance(payload, dict):
            print("expected workflow runs object from GitHub API", file=sys.stderr)
            return 2
        text = format_run_ids_lines(select_failed_workflow_run_ids(payload, args.head_sha))
        if text:
            print(text)
        return 0

    sigs_doc = _load_json(args.sigs)
    commits = _load_json(args.commits)
    if not isinstance(sigs_doc, dict):
        print("expected signatures object from GitHub API", file=sys.stderr)
        return 2
    if not isinstance(commits, list):
        print("expected commits list from GitHub API", file=sys.stderr)
        return 2

    pr_author = args.pr_author or os.environ.get("PR_AUTHOR") or ""
    allowlist = args.allowlist or os.environ.get("IPR_BOT_ALLOWLIST") or ""
    try:
        authors, missing, signed = verify_ipr(
            sigs_doc=sigs_doc,
            commits=commits,
            pr_author=pr_author,
            allowlist_raw=allowlist,
        )
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1

    print(f"authors={sorted(authors)}")
    print(f"signed_count={len(signed)} missing={missing}")
    if missing:
        print(args.missing_message.format(missing=", ".join(missing)), file=sys.stderr)
        return 1
    print(args.ok_message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
