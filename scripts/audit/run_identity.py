#!/usr/bin/env python3
"""The identity of the tree a run graded, written beside that run's reports.

A per-nodeid delta is only as good as the identity of its two endpoints, and a
run directory used to carry none. ``test-results/<run>/`` held the per-suite
reports, the ``.suites`` manifest and nothing that said WHICH TREE produced
them, so every regression claim rested on somebody remembering -- and the
memory was not in the artifact.

That is not a hypothetical. On 2026-10-14 ``innet_141026_0335`` was compared
against ``innet_121026_1128`` with ``compare_runs.py`` and reported 36
REGRESSED / 15 FIXED. The differencer did exactly what it was asked. The
baseline lived in a worktree on a DIFFERENT BRANCH from the one under test, so
it graded neither tree in the comparison; against a real base the same change
was 12 fixed, 0 new failures and 2 genuine regressions. One of the 36 was run
directly on a worktree at the true base with that tree's own wheel and FAILED
there identically -- 17 pydantic errors, same fields, same order. All 36
dissolved (salesagent-dxb3j).

WHAT IS RECORDED, and why each field rather than a general "provenance" blob:

``commit`` / ``branch`` / ``detached``
    Which tree, and where it sat. A detached checkout is marked as such instead
    of being recorded under the branch name ``HEAD``, which would make every
    detached run look like the same branch.
``tree``
    ``clean`` or ``dirty``. A dirty tree is not identified by its commit, and a
    comparison between two dirty trees at the same sha is not a comparison
    between two known trees. The field says so rather than implying otherwise.
``adcp``
    The pin AS INSTALLED, which is NOT what ``pyproject.toml`` declares.
    ``pyproject.toml`` declares the release; a locally built SDK wheel carries a
    ``+<local>`` version segment, and that segment is the only thing that
    distinguishes two builds of the same release. The runner's own history
    contains the case that needs it: three consecutive runs published a stale
    ``storyboard.json`` and the numbers were read as current until an SDK
    version inside the report contradicted the SDK that was actually installed
    (``run_all_tests.sh``, the extraction comment). So this is read from the
    INTERPRETER THAT RUNS THE SUITES -- ``--adcp`` is invoked inside the
    ``tests`` container -- and never from a declaration on the host.
``source``
    WHERE the identity came from: ``git``, ``env``, or ``absent``. A run with no
    identity must be distinguishable from a run whose identity was asserted by
    its invoker, because the two support different claims.

GIT IS NOT ALWAYS THERE, AND WHEN IT IS THERE IT IS NOT ALWAYS ABOUT THIS TREE.
``cassini run`` executes ``./run_all_tests.sh`` on the CI box inside an rsync
MIRROR, not a clone, and cassini's supervisor then runs ``git init -q`` plus
``git add -A`` in it so the structural guards that read ``git ls-files`` have
something to read (``cassini/supervisor.py``, "the run directory is an rsync
mirror, not a clone, so there may be no repository at all"). The result is a
repository with NO COMMITS whose working tree reads as 2400-odd ADDED paths. So:

- ``git rev-parse --is-inside-work-tree`` answers ``true`` there and must not be
  used as the test. Having a commit is the test, and ``git rev-parse HEAD`` is
  how it is asked.
- ``git status --porcelain`` is non-empty there for a reason that has nothing to
  do with the tree being dirty, which is why dirtiness is only ever read AFTER a
  commit has been established.

Because of that, a box run cannot derive its own sha and must be TOLD: with no
commit from git, ``SA_RUN_COMMIT`` / ``SA_RUN_BRANCH`` / ``SA_RUN_TREE`` are
read instead. Git is consulted FIRST and wins wherever it can answer, so a
stale exported variable can never overwrite a real repository's answer -- the
environment is a fallback for the one shape where git has no answer to give,
not an override. Forwarding those three to the box is one line in cassini's
``profiles/salesagent.toml`` ``forward_env`` (salesagent-bz8zy); until it lands,
box runs record ``source: absent`` with the reason, and the consumers below
refuse them, which is the honest verdict for a run nothing can attribute.

Two modes::

    python -m scripts.audit.run_identity --adcp          # inside the tests container
    python3 -m scripts.audit.run_identity --write <run-dir> [--adcp-json '<json>']
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import socket
import subprocess
import sys
from typing import NamedTuple

#: Written beside the per-suite reports. NOT a dotfile, and not left to glob
#: semantics to hide: ``pathlib.Path.glob("*.json")`` matches ``.meta.json``
#: while ``glob.glob("*.json")`` does not, and this run directory is enumerated
#: through both. Every enumerator therefore excludes this name EXPLICITLY, which
#: is auditable; relying on a leading dot would have been a silent trap in
#: exactly half of them.
IDENTITY_FILE = "meta.json"

#: Bumped when a field changes meaning. A reader that does not know the schema it
#: is holding cannot refuse intelligently, so the number is written, not implied.
SCHEMA = 1

_ENV_COMMIT = "SA_RUN_COMMIT"
_ENV_BRANCH = "SA_RUN_BRANCH"
_ENV_TREE = "SA_RUN_TREE"


class Identity(NamedTuple):
    """What a run directory says about the tree it graded."""

    commit: str | None
    branch: str | None
    detached: bool | None
    tree: str | None  # "clean" | "dirty" | None
    source: str  # "git" | "env" | "absent"
    unidentified_because: str | None
    adcp: dict | None

    @property
    def identified(self) -> bool:
        """True when this names a commit. ``source`` alone is not the test.

        An ``env`` identity whose variable was set to something that is not a
        sha is recorded with the reason and no commit, so the commit -- the only
        field a comparison can actually use -- is what is asked about.
        """
        return bool(self.commit)


# ── collection ───────────────────────────────────────────────────────────────


def _git(root: pathlib.Path, *args: str) -> str | None:
    """``git -C root <args>`` stdout stripped, or ``None`` if git said no.

    Every caller wants the same three things collapsed into one answer -- git
    missing, not a repository, or the command failing -- because all three mean
    "git cannot tell you this".
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _looks_like_a_sha(value: str) -> bool:
    return len(value) == 40 and all(c in "0123456789abcdef" for c in value.lower())


def _from_git(root: pathlib.Path) -> Identity | None:
    """The tree's identity per git, or ``None`` when git cannot name a commit.

    ``rev-parse HEAD`` is the test, deliberately: in the CI box's mirror git
    reports a work tree and no commits (see the module docstring), and any
    check that asks "is there a repository" answers yes there.
    """
    commit = _git(root, "rev-parse", "HEAD")
    if not commit or not _looks_like_a_sha(commit):
        return None
    branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    status = _git(root, "status", "--porcelain")
    return Identity(
        commit=commit,
        branch=branch or None,
        detached=branch is None,
        # ``status`` is only consulted once a commit exists, so an empty
        # repository's "everything is added" can never be read as dirty.
        tree=("clean" if status == "" else "dirty") if status is not None else None,
        source="git",
        unidentified_because=None,
        adcp=None,
    )


def _from_env(env: dict[str, str]) -> Identity | None:
    """The identity its invoker asserted, or ``None`` when none was asserted.

    A set-but-malformed ``SA_RUN_COMMIT`` returns an identity with NO commit and
    the reason attached, rather than either trusting the value or silently
    falling through to "absent". Somebody tried to say which tree this was and
    got it wrong, and that is worth a different sentence from nobody trying.
    """
    commit = (env.get(_ENV_COMMIT) or "").strip()
    if not commit:
        return None
    if not _looks_like_a_sha(commit):
        return Identity(
            commit=None,
            branch=None,
            detached=None,
            tree=None,
            source="env",
            unidentified_because=f"{_ENV_COMMIT}={commit!r} is not a 40-character commit sha",
            adcp=None,
        )
    branch = (env.get(_ENV_BRANCH) or "").strip()
    tree = (env.get(_ENV_TREE) or "").strip().lower()
    return Identity(
        commit=commit.lower(),
        branch=branch or None,
        detached=not branch,
        tree=tree if tree in ("clean", "dirty") else None,
        source="env",
        unidentified_because=None,
        adcp=None,
    )


def collect(root: pathlib.Path, env: dict[str, str]) -> Identity:
    """This tree's identity: git if it can answer, else the environment, else nothing."""
    found = _from_git(root) or _from_env(env)
    if found is not None:
        return found
    return Identity(
        commit=None,
        branch=None,
        detached=None,
        tree=None,
        source="absent",
        unidentified_because=(
            f"git names no commit at {root} and {_ENV_COMMIT} is unset — "
            "an rsync mirror with a freshly init'd repository (the CI box) is the known case; "
            f"forward {_ENV_COMMIT}/{_ENV_BRANCH}/{_ENV_TREE} to attribute such a run"
        ),
        adcp=None,
    )


def adcp_pin() -> dict:
    """The INSTALLED adcp pin, read from the interpreter this runs in.

    Both halves are needed and neither substitutes for the other: the spec
    version is what the seller is graded against, and the distribution version's
    local segment (``+<local>``, on a locally built wheel) is the only thing
    separating two builds of the same release. ``pyproject.toml`` declares the release and cannot tell
    them apart.
    """
    import importlib.metadata

    import adcp

    return {
        "spec_version": adcp.get_adcp_spec_version(),
        "distribution": importlib.metadata.version("adcp"),
    }


def document(identity: Identity, run: pathlib.Path, adcp: dict | None, host: str, now: str) -> dict:
    """The ``meta.json`` payload."""
    return {
        "schema": SCHEMA,
        "run": run.name,
        "started_utc": now,
        "host": host,
        "commit": identity.commit,
        "branch": identity.branch,
        "detached": identity.detached,
        "tree": identity.tree,
        "source": identity.source,
        "unidentified_because": identity.unidentified_because,
        "adcp": adcp,
    }


# ── reading, and the refusals both consumers share ───────────────────────────


def read(run: pathlib.Path) -> Identity | None:
    """A run's recorded identity, or ``None`` when it has none to read.

    ``None`` covers the absent file and the unreadable one alike: a reader that
    cannot establish which tree a run graded is in the same position either way,
    and the caller's refusal says which path it tried.
    """
    path = run / IDENTITY_FILE
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return Identity(
        commit=raw.get("commit"),
        branch=raw.get("branch"),
        detached=raw.get("detached"),
        tree=raw.get("tree"),
        source=raw.get("source") or "absent",
        unidentified_because=raw.get("unidentified_because"),
        adcp=raw.get("adcp"),
    )


def describe(identity: Identity | None) -> str:
    """One line naming the tree, for a header. Never a claim the record cannot support."""
    if identity is None or not identity.identified:
        return "IDENTITY UNKNOWN"
    where = identity.branch or ("detached" if identity.detached else "branch unrecorded")
    pin = (identity.adcp or {}).get("distribution") or "adcp unrecorded"
    return f"{identity.commit[:12]} ({where}, {identity.tree or 'tree unrecorded'}, {pin})"


def unidentified(label: str, run: pathlib.Path, identity: Identity | None) -> str | None:
    """Why *label* cannot be attributed to a tree, or ``None`` when it can.

    Names the side and what it lacked, because "identity missing" sends a reader
    to look at both run directories and one of them is fine.
    """
    if identity is None:
        return f"{label} ({run}) carries no {IDENTITY_FILE}: nothing records which tree it graded"
    if not identity.identified:
        return f"{label} ({run}) records no commit: {identity.unidentified_because or 'reason not recorded'}"
    return None


def dirty(label: str, identity: Identity) -> str | None:
    """Why *label*'s tree is not identified by its commit, or ``None``."""
    if identity.tree == "dirty":
        return f"{label} ran on a DIRTY tree at {identity.commit[:12]}: its commit does not identify what was graded"
    return None


def _known_commit(root: pathlib.Path, commit: str) -> bool:
    """Whether this repository holds *commit* as a commit object.

    Asked before ancestry, because ``merge-base --is-ancestor`` exits non-zero
    both for "not an ancestor" and for "never heard of it", and those want
    different sentences. A run graded on a branch this checkout never fetched is
    the second, and reporting it as divergence would be a confident wrong answer.
    """
    return _git(root, "cat-file", "-e", f"{commit}^{{commit}}") is not None


def ancestry(
    baseline: Identity,
    new: Identity,
    root: pathlib.Path,
    labels: tuple[str, str] = ("baseline", "new"),
) -> str | None:
    """Why these two runs are not a baseline/subject pair, or ``None``.

    THE CASE THIS EXISTS FOR is two DIVERGENT branches compared as though one
    were the other's baseline, which is what produced the 36-regression false
    report. Neither sha reaches the other, so every test that differs between
    the branches reads as a change the subject made, and the real base is a
    third commit nobody looked at.

    An ancestor relationship in EITHER direction is accepted. Baseline-then-
    descendant is the ordinary gate; the reverse is a caller reading the delta
    backwards, which is their business and is a real pair either way.
    """
    base_label, new_label = labels
    if baseline.commit == new.commit:
        return None
    unknown = [
        f"{label} commit {identity.commit[:12]} is not in this repository (fetch it, or run from a checkout that has it)"
        for label, identity in ((base_label, baseline), (new_label, new))
        if not _known_commit(root, identity.commit or "")
    ]
    if unknown:
        return "; ".join(unknown)
    for first, second in ((baseline, new), (new, baseline)):
        if _git(root, "merge-base", "--is-ancestor", first.commit or "", second.commit or "") is not None:
            return None
    base = _git(root, "merge-base", baseline.commit or "", new.commit or "")
    where = f" Their merge-base is {base[:12]}; a baseline run on that tree is the comparable one." if base else ""
    return (
        f"{base_label} {baseline.commit[:12]} ({baseline.branch or 'detached'}) and "
        f"{new_label} {new.commit[:12]} ({new.branch or 'detached'}) have NO ancestry relationship — "
        f"neither tree contains the other, so a per-nodeid delta between them attributes "
        f"every difference between two divergent branches to the subject.{where}"
    )


def refusals(
    baseline_run: pathlib.Path,
    new_run: pathlib.Path,
    root: pathlib.Path,
    labels: tuple[str, str] = ("baseline", "new"),
) -> tuple[list[str], list[str], dict[str, Identity | None]]:
    """``(hard, waivable, identities)`` for a pair of run directories.

    WAIVABLE is exactly the missing-identity case, and only because every run
    retained today predates this file: refusing all of them would leave the tool
    useless on the only data there is, while accepting them silently is the
    defect being fixed. So a caller may proceed past it by name, and the output
    says it did.

    HARD is divergence and dirtiness, and neither is waivable, because both are
    cases where identity IS recorded and says the comparison is not one. A
    divergent pair has a correct baseline -- the merge-base -- and the refusal
    names it, so there is a real action rather than a flag to reach for.
    """
    identities = {labels[0]: read(baseline_run), labels[1]: read(new_run)}
    waivable = [
        problem
        for label, run in ((labels[0], baseline_run), (labels[1], new_run))
        if (problem := unidentified(label, run, identities[label])) is not None
    ]
    hard: list[str] = []
    if not waivable:
        base, new = identities[labels[0]], identities[labels[1]]
        assert base is not None and new is not None  # implied by an empty `waivable`
        hard = [
            problem
            for problem in (
                dirty(labels[0], base),
                dirty(labels[1], new),
                ancestry(base, new, root, labels),
            )
            if problem is not None
        ]
    return hard, waivable, identities


def report_refusals(
    hard: list[str],
    waivable: list[str],
    identities: dict[str, Identity | None],
    allow_unidentified: bool,
    out=None,
) -> bool:
    """Print the identity block and say whether the comparison may proceed.

    Always prints what each side was, whatever the verdict: a reader who is
    about to be refused needs to see the two trees, and a reader who is not
    needs the attribution printed beside the numbers rather than remembered.

    ``out=None`` rather than ``out=sys.stdout``: a default argument binds ONCE,
    at definition time, so the latter captured the real stdout before any test
    harness could replace it -- every refusal below printed to the terminal and
    none of it reached the assertion that was checking for it.
    """
    out = sys.stdout if out is None else out
    print("TREE IDENTITY", file=out)
    for label, identity in identities.items():
        print(f"  {label:8} {describe(identity)}", file=out)
    for problem in hard:
        print(f"\nREFUSED: {problem}", file=out)
    if hard:
        return False
    if waivable and not allow_unidentified:
        for problem in waivable:
            print(f"\nREFUSED: {problem}", file=out)
        print(
            "\n  Nothing attributes this comparison to a tree, so no claim is made about one. "
            "Runs retained from before this field existed carry no identity at all; pass "
            "--allow-unidentified to compare them anyway, and every verdict below is then "
            "labelled UNATTRIBUTED.",
            file=out,
        )
        return False
    if waivable:
        print("\nUNATTRIBUTED (--allow-unidentified): the verdict below is not tied to any tree.", file=out)
        for problem in waivable:
            print(f"    {problem}", file=out)
    return True


# ── cli ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--adcp", action="store_true", help="print the installed adcp pin as JSON and exit")
    mode.add_argument("--write", type=pathlib.Path, metavar="RUN_DIR", help=f"write RUN_DIR/{IDENTITY_FILE}")
    parser.add_argument(
        "--adcp-json",
        default="",
        help="the output of a --adcp invocation inside the suites' container; omitted or unparseable records null",
    )
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd(), help="the tree being graded")
    # The asserted identity arrives as ARGUMENTS, not as this process's environment, and
    # both halves of that are deliberate.
    #
    # This module is pure stdlib plus `git`, reaching only `--root`. It runs before any
    # suite and in places where the application package is not importable at all --
    # `tests/unit/test_run_all_tests_parallel_tox.py` provisions a stub workdir holding
    # this file and five siblings and NO `src/`, and it executes the real runner against
    # it. So `from src.core.config import ToolingSettings` here is a ModuleNotFoundError
    # that ends the run before tox is reached; that is measured, not hypothetical.
    #
    # And an `os.environ` read here is banned by ruff-environment.toml, correctly: the
    # environment is read once, by the settings loader. Arguments satisfy both at once --
    # `run_all_tests.sh` reads `$SA_RUN_COMMIT` in SHELL, where no such ban applies and no
    # import is needed, and hands the values over the same way it already hands
    # `--adcp-json` a value derived outside this process. An identity asserted by the
    # invoker is an INPUT to the audit, so taking it as one is also the honester shape.
    parser.add_argument("--commit", default="", help="the sha the invoker asserts this run graded")
    parser.add_argument("--branch", default="", help="the branch the invoker asserts")
    parser.add_argument("--tree", default="", help="'clean' or 'dirty', as the invoker found it")
    args = parser.parse_args(argv)

    if args.adcp:
        print(json.dumps(adcp_pin()))
        return 0

    try:
        pin = json.loads(args.adcp_json) if args.adcp_json.strip() else None
    except ValueError:
        pin = None
    identity = collect(
        args.root,
        {_ENV_COMMIT: args.commit, _ENV_BRANCH: args.branch, _ENV_TREE: args.tree},
    )
    payload = document(
        identity,
        args.write,
        pin if isinstance(pin, dict) else None,
        socket.gethostname(),
        datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    )
    args.write.mkdir(parents=True, exist_ok=True)
    (args.write / IDENTITY_FILE).write_text(json.dumps(payload, indent=1) + "\n")
    print(f"{args.write / IDENTITY_FILE}: {describe(read(args.write))}")
    if identity.source != "git":
        # Loud, not fatal. A run that cannot name its tree should still run; what
        # must not happen is the gap going unnoticed until somebody compares it.
        print(
            f"  WARNING: identity source is {identity.source!r} — {identity.unidentified_because or 'asserted by its invoker'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
