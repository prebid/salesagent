"""A run must say which tree it graded, and must not say it when it cannot.

``scripts/audit/run_identity.py`` exists because a run directory recorded no
commit, no branch and no installed pin, so a per-nodeid comparison could be
performed against a baseline that graded neither tree in the claim. That
happened and cost a wrong report: 36 regressions and 15 fixes, all of which
dissolved against a real base (salesagent-dxb3j).

The properties under test are the two that the false report needed:

COLLECTION cannot invent an identity it does not have, and cannot miss one it
does. The CI box's shape is the one that breaks a naive implementation -- an
rsync mirror with ``git init`` run in it has a work tree, no commits, and
thousands of staged paths -- so it is built here for real rather than mocked.

THE REFUSALS fire on the subject, not on the authority. Each is perturbed by
changing the run directories, never by changing the predicate.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

from scripts.audit.run_identity import (
    IDENTITY_FILE,
    Identity,
    ancestry,
    collect,
    describe,
    read,
    unidentified,
)


def _git(root: pathlib.Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(root),
        },
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A real repository with one commit on a named branch."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "work/lane")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-qm", "one")
    return root


# ── collection ───────────────────────────────────────────────────────────────


def test_a_real_checkout_is_identified_by_git(repo: pathlib.Path) -> None:
    identity = collect(repo, {})

    assert identity.source == "git"
    assert identity.commit == _git(repo, "rev-parse", "HEAD")
    assert identity.branch == "work/lane"
    assert identity.detached is False
    assert identity.tree == "clean"


def test_an_uncommitted_edit_makes_the_tree_dirty(repo: pathlib.Path) -> None:
    """A dirty tree is not identified by its commit, and the record says so."""
    (repo / "a.txt").write_text("two\n")

    assert collect(repo, {}).tree == "dirty"


def test_a_detached_checkout_is_marked_detached_not_named_HEAD(repo: pathlib.Path) -> None:
    """``rev-parse --abbrev-ref HEAD`` returns the literal "HEAD" when detached.

    Recording that as a branch name makes every detached run look like the same
    branch, which is a wrong answer rather than a missing one.
    """
    _git(repo, "checkout", "-q", "--detach")

    identity = collect(repo, {})

    assert identity.detached is True
    assert identity.branch is None
    assert identity.commit


def test_the_ci_box_shape_is_unidentified_rather_than_dirty(tmp_path: pathlib.Path) -> None:
    """THE case that breaks a naive implementation, built for real.

    ``cassini run`` executes the runner inside an rsync MIRROR, and cassini's
    supervisor runs ``git init -q`` + ``git add -A`` there so the structural
    guards that read ``git ls-files`` have something to read. The result has a
    work tree, NO commits, and every path staged.

    Two wrong answers are available and both are worse than refusing:
    ``rev-parse --is-inside-work-tree`` says ``true``, and ``status --porcelain``
    is thousands of lines long -- so a check built on either reports a known
    tree, or a dirty one, for a run that has no identity at all.
    """
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "run_all_tests.sh").write_text("#!/bin/sh\n")
    _git(mirror, "init", "-q")
    _git(mirror, "add", "-A")
    # The two signals a naive implementation would have read:
    assert _git(mirror, "rev-parse", "--is-inside-work-tree") == "true"
    assert _git(mirror, "status", "--porcelain") != ""

    identity = collect(mirror, {})

    assert identity.source == "absent", f"a commit-less mirror has no identity: {identity}"
    assert identity.commit is None
    assert identity.tree is None, "dirtiness must not be read off a repository with no commit"
    assert "SA_RUN_COMMIT" in (identity.unidentified_because or ""), identity.unidentified_because


def test_the_environment_identifies_a_run_git_cannot(tmp_path: pathlib.Path) -> None:
    """The box path: told, because it cannot find out."""
    sha = "0c5385bad9a8fb40d30c5f9fa5ff92948af66ce9"

    identity = collect(tmp_path, {"SA_RUN_COMMIT": sha, "SA_RUN_BRANCH": "land/one-tree", "SA_RUN_TREE": "clean"})

    assert identity == Identity(
        commit=sha,
        branch="land/one-tree",
        detached=False,
        tree="clean",
        source="env",
        unidentified_because=None,
        adcp=None,
    )


def test_git_wins_over_a_stale_environment_variable(repo: pathlib.Path) -> None:
    """The environment is a fallback for where git has no answer, not an override.

    Reversing this precedence would let a variable exported hours ago in some
    other worktree relabel a run that git could identify exactly -- the same
    class of false attribution this module exists to stop.
    """
    identity = collect(repo, {"SA_RUN_COMMIT": "f" * 40, "SA_RUN_BRANCH": "wrong"})

    assert identity.source == "git"
    assert identity.commit == _git(repo, "rev-parse", "HEAD")
    assert identity.branch == "work/lane"


def test_a_malformed_asserted_commit_is_refused_and_named(tmp_path: pathlib.Path) -> None:
    """Somebody tried to say which tree this was and got it wrong.

    That is a different sentence from nobody trying, and it must not be trusted:
    a short sha or a branch name in SA_RUN_COMMIT would make every ancestry
    question below unanswerable while looking like an identity.
    """
    identity = collect(tmp_path, {"SA_RUN_COMMIT": "0c5385b"})

    assert identity.commit is None
    assert identity.source == "env"
    assert "not a 40-character commit sha" in (identity.unidentified_because or "")


# ── reading ──────────────────────────────────────────────────────────────────


def test_a_run_directory_with_no_identity_file_reads_as_none(tmp_path: pathlib.Path) -> None:
    assert read(tmp_path) is None
    assert unidentified("baseline", tmp_path, None) is not None
    assert IDENTITY_FILE in (unidentified("baseline", tmp_path, None) or "")


def test_an_unreadable_identity_file_is_not_an_identity(tmp_path: pathlib.Path) -> None:
    (tmp_path / IDENTITY_FILE).write_text("{ truncated")

    assert read(tmp_path) is None


def test_describe_never_claims_a_tree_it_cannot_support(tmp_path: pathlib.Path) -> None:
    assert describe(None) == "IDENTITY UNKNOWN"
    (tmp_path / IDENTITY_FILE).write_text(json.dumps({"source": "absent", "commit": None}))
    assert describe(read(tmp_path)) == "IDENTITY UNKNOWN"


# ── ancestry ─────────────────────────────────────────────────────────────────


def _identity(commit: str, branch: str) -> Identity:
    return Identity(commit, branch, False, "clean", "git", None, None)


def test_a_descendant_is_a_comparable_subject(repo: pathlib.Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-qm", "two")
    head = _git(repo, "rev-parse", "HEAD")

    assert ancestry(_identity(base, "main"), _identity(head, "work/lane"), repo) is None


def test_the_same_commit_is_comparable(repo: pathlib.Path) -> None:
    sha = _git(repo, "rev-parse", "HEAD")

    assert ancestry(_identity(sha, "main"), _identity(sha, "main"), repo) is None


def test_two_divergent_branches_refuse_and_name_their_merge_base(repo: pathlib.Path) -> None:
    """The 36-regression false report, in miniature.

    Neither tree contains the other, so every test that differs between the two
    branches reads as a change the subject made. The refusal names the commit a
    comparable baseline would have been run on, so there is an action rather
    than a flag to reach for.
    """
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "lane/a")
    (repo / "a2.txt").write_text("a2\n")
    _git(repo, "add", "a2.txt")
    _git(repo, "commit", "-qm", "a2")
    left = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", base)
    _git(repo, "checkout", "-q", "-b", "lane/b")
    (repo / "b2.txt").write_text("b2\n")
    _git(repo, "add", "b2.txt")
    _git(repo, "commit", "-qm", "b2")
    right = _git(repo, "rev-parse", "HEAD")

    problem = ancestry(_identity(left, "lane/a"), _identity(right, "lane/b"), repo)

    assert problem is not None, "two divergent branches are not a baseline/subject pair"
    assert "NO ancestry relationship" in problem, problem
    assert base[:12] in problem, f"the merge-base must be named so there is an action:\n{problem}"


def test_a_commit_this_checkout_never_fetched_refuses_as_unknown_not_divergent(repo: pathlib.Path) -> None:
    """``merge-base --is-ancestor`` exits non-zero for both, and they differ.

    A run graded on a branch this checkout has never seen is not evidence of
    divergence, and reporting it as such would be a confident wrong answer about
    two trees one of which is simply absent.
    """
    here = _git(repo, "rev-parse", "HEAD")
    never = "1234567890abcdef1234567890abcdef12345678"

    problem = ancestry(_identity(never, "elsewhere"), _identity(here, "work/lane"), repo)

    assert problem is not None
    assert "not in this repository" in problem, problem
    assert never[:12] in problem, problem
    assert "NO ancestry relationship" not in problem, f"an absent commit is not divergence:\n{problem}"
