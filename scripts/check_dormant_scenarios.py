#!/usr/bin/env python3
"""Informational check: which BDD scenarios touched by your change never run?

The failure mode this surfaces (#1603): a branch edits step modules, feature
files, or the BDD conftest, CI stays green, and nothing anywhere says that the
scenarios involved are auto-xfailed — dormant steps look like coverage. This
is the local, informational companion to ``run_all_tests.sh`` suggested in
#1603: it never fails your build (unless you opt into ``--strict``), it just
tells you what is NOT running before you claim coverage.

How it works
------------
1. Diffs your branch against the merge-base with ``--base`` (default: the
   first of ``upstream/main`` / ``origin/main`` that exists) plus any
   uncommitted changes, and keeps the BDD-relevant paths.
2. Maps those paths to the BDD test modules that bind them
   (``steps/domain/uc003_*`` -> ``test_uc003*.py``; a feature file -> the
   module whose ``scenarios(...)`` references it).
3. Runs ONLY those modules with ``DATABASE_URL`` removed from the
   environment. That split is the whole trick:
     - wired scenarios SKIP at the ``integration_db`` gate ("requires
       PostgreSQL DATABASE_URL") — they would run in CI;
     - dormant scenarios XFAIL at the harness/step layer — they would NOT
       run anywhere.
   No Docker, no Postgres, seconds not minutes.
4. Prints the dormant scenarios grouped by reason, transport params
   collapsed. "Dormant" is decided against ``tests/bdd/xfail_taxonomy``, the
   same module ``tests/bdd/conftest.py`` builds those reasons from — this
   checker never hand-copies a reason string.

Usage
-----
    uv run python scripts/check_dormant_scenarios.py            # diff vs main
    uv run python scripts/check_dormant_scenarios.py --base origin/main
    uv run python scripts/check_dormant_scenarios.py --paths tests/bdd/steps/domain/uc026_package_media_buy.py
    uv run python scripts/check_dormant_scenarios.py --all      # every BDD module
    make check-dormant

Exit code is 0 unless ``--strict`` is passed AND harness-/step-dormant
scenarios were found.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BDD_DIR = Path("tests") / "bdd"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The reason vocabulary is OWNED by tests/bdd/xfail_taxonomy and emitted by
# tests/bdd/conftest.py. Importing it (rather than hand-copying the literals)
# is the point: a reworded conftest reason must not silently reclassify dormant
# scenarios as documented gaps. The module is a leaf — no pytest import — so
# this stays a cheap script.
from tests.bdd.xfail_taxonomy import DORMANT_REASON_MARKERS, scenario_name

_XFAIL_PREFIX = re.compile(r"^XFAIL\s+")
_XFAIL_REASON_SEP = re.compile(r"^\s+-\s+")
_UC_IN_NAME = re.compile(r"uc(\d+)", re.IGNORECASE)


def _norm(path: str) -> str:
    """Normalize a path for comparison: strip whitespace, backslashes -> slashes."""
    return path.strip().replace("\\", "/")


def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    return out.stdout.strip()


def resolve_base(base: str | None) -> str | None:
    """The ref to diff against: explicit --base, else upstream/main, else origin/main."""
    candidates = [base] if base else ["upstream/main", "origin/main"]
    for ref in candidates:
        if ref and _git("rev-parse", "--verify", "--quiet", ref):
            return ref
    return None


def _porcelain_paths(lines: list[str]) -> list[str]:
    """Working-tree paths from ``git status --porcelain`` lines.

    Rename/copy entries read ``R  old.py -> new.py``; taking ``line[3:]`` whole
    yields the pseudo-path "old.py -> new.py", which matches nothing and drops
    the change. An uncommitted ``git mv`` of a feature/step file is exactly when
    scenarios go dormant, so keep the DESTINATION path.
    """
    return [_norm(line[3:].split(" -> ")[-1]) for line in lines if len(line) > 3]


def changed_paths(base_ref: str) -> list[str]:
    """Committed changes vs merge-base plus uncommitted working-tree changes."""
    merge_base = _git("merge-base", "HEAD", base_ref)
    committed = _git("diff", "--name-only", f"{merge_base}..HEAD").splitlines()
    working = _porcelain_paths(_git("status", "--porcelain").splitlines())
    seen: list[str] = []
    for p in committed + working:
        p = _norm(p)
        if p and p not in seen:
            seen.append(p)
    return seen


def is_bdd_relevant(path: str) -> bool:
    p = _norm(path)
    return p.startswith(("tests/bdd/", "tests/harness/"))


def map_paths_to_modules(paths: list[str]) -> tuple[set[Path], list[str]]:
    """Map touched paths to the BDD test modules that bind them.

    Returns (modules, notes). A touched conftest/harness/generic-steps file
    affects every scenario, which the default run does not sweep — a note
    tells the caller to use --all for that.
    """
    modules: set[Path] = set()
    notes: list[str] = []
    all_test_modules = sorted((REPO_ROOT / BDD_DIR).glob("test_*.py"))

    for raw in paths:
        p = _norm(raw)
        name = p.rsplit("/", 1)[-1]

        if p.startswith("tests/bdd/test_") and p.endswith(".py"):
            modules.add(REPO_ROOT / p)
        elif p.startswith("tests/bdd/steps/domain/"):
            m = _UC_IN_NAME.search(name)
            if m:
                hits = [t for t in all_test_modules if f"uc{m.group(1)}" in t.name.lower()]
                if hits:
                    modules.update(hits)
                else:
                    notes.append(f"{p}: no test module matches uc{m.group(1)} — its steps bind nothing")
            else:
                notes.append(f"{p}: domain step module without a ucNNN name — run --all to sweep")
        elif p.startswith("tests/bdd/features/") and p.endswith(".feature"):
            binders = [t for t in all_test_modules if name in t.read_text(encoding="utf-8", errors="replace")]
            if binders:
                modules.update(binders)
            else:
                notes.append(f"{p}: NO test module binds this feature — every scenario in it is dormant")
        elif p.startswith(("tests/bdd/steps/generic/", "tests/harness/")) or p == "tests/bdd/conftest.py":
            notes.append(f"{p}: affects every scenario — use --all for the full sweep")
    return modules, notes


def run_without_db(modules: list[Path]) -> subprocess.CompletedProcess[str]:
    """Run the modules with DATABASE_URL removed; return the completed process.

    The caller must inspect ``returncode``: pytest exits >= 2 when it could not
    validly execute the modules (collection error, internal error, no tests),
    where an empty XFAIL list means "the run broke", not "no dormant scenarios".
    """
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PYTHONUTF8"] = "1"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *[str(m.relative_to(REPO_ROOT)) for m in modules],
        "-q",
        "-p",
        "no:randomly",
        "-p",
        "no:cacheprovider",
        "-rxX",
        "--no-header",
    ]
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, env=env, check=False)


def split_xfail_line(line: str) -> tuple[str, str] | None:
    """Split one ``-rxX`` XFAIL line into (nodeid, reason), or None if not one.

    A regex cannot do this: real BDD outline param ids contain spaces AND the
    literal reason separator, e.g.

        ...::test_idempotency_key_boundary_validation__boundary_point[a2a-length
        15 (min - 1)-<15 char string>-error "VALIDATION_ERROR" with suggestion]
        - UC-003 harness not yet wired ...

    ``\\S+`` drops the line outright; splitting on the first " - " cuts the
    nodeid mid-param and files the tail as the reason. So scan for the first
    whitespace at bracket depth 0 — parametrization brackets nest, and the
    separator only ever appears after them.
    """
    m = _XFAIL_PREFIX.match(line)
    if not m:
        return None
    rest = line[m.end() :]
    depth = 0
    end = len(rest)
    for i, ch in enumerate(rest):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        elif depth == 0 and ch.isspace():
            end = i
            break
    if depth != 0:
        # Unbalanced brackets: fall back to the first separator rather than
        # swallowing the reason into the nodeid.
        head, sep, tail = rest.partition(" - ")
        return (head, tail.strip()) if sep else (rest.strip(), "")
    sep_match = _XFAIL_REASON_SEP.match(rest[end:])
    reason = rest[end:][sep_match.end() :].strip() if sep_match else ""
    return rest[:end], reason


def classify(pytest_output: str) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Split XFAIL lines into (dormant, documented) — reason -> scenario names.

    Transport params are collapsed so one scenario counts once, and dormant
    means the xfail reason matches a wiring/step gap rather than a documented
    spec-production gap.
    """
    dormant: dict[str, set[str]] = defaultdict(set)
    documented: dict[str, set[str]] = defaultdict(set)
    for line in pytest_output.splitlines():
        parsed = split_xfail_line(line.strip())
        if parsed is None:
            continue
        nodeid, reason = parsed
        # Collapse parametrization (shared with scripts/enumerate_bdd_issues.py)
        scenario = scenario_name(nodeid)
        reason_key = reason.split(". ")[0][:120] if reason else "(no reason recorded)"
        lowered = reason.lower()
        bucket = dormant if any(k in lowered for k in DORMANT_REASON_MARKERS) else documented
        bucket[reason_key].add(scenario)
    return dormant, documented


_SUMMARY_FALLBACK = "Informational check: which BDD scenarios touched by your change never run?"


def _summary(doc: str | None) -> str:
    """First line of the module docstring, or a literal fallback.

    Under ``python -OO`` docstrings are stripped and ``__doc__`` is None, so
    ``__doc__.splitlines()[0]`` raises AttributeError — and ``(__doc__ or "")``
    alone still raises IndexError on the empty list.
    """
    lines = (doc or "").splitlines()
    return lines[0] if lines and lines[0].strip() else _SUMMARY_FALLBACK


def main() -> int:
    parser = argparse.ArgumentParser(description=_summary(__doc__))
    parser.add_argument("--base", help="Ref to diff against (default: upstream/main, then origin/main)")
    parser.add_argument("--paths", nargs="*", help="Check these paths instead of the git diff")
    parser.add_argument("--all", action="store_true", help="Sweep every BDD test module")
    parser.add_argument(
        "--strict", action="store_true", help="Exit 1 when dormant scenarios are found (default: informational)"
    )
    args = parser.parse_args()

    if args.all:
        modules = sorted((REPO_ROOT / BDD_DIR).glob("test_*.py"))
        notes: list[str] = []
    elif args.paths:
        modules_set, notes = map_paths_to_modules(list(args.paths))
        modules = sorted(modules_set)
    else:
        base_ref = resolve_base(args.base)
        if base_ref is None:
            print("check-dormant: no usable base ref (tried upstream/main, origin/main); use --base or --all")
            return 0
        touched = [p for p in changed_paths(base_ref) if is_bdd_relevant(p)]
        if not touched:
            print(f"check-dormant: no BDD-relevant changes vs {base_ref} — nothing to check")
            return 0
        modules_set, notes = map_paths_to_modules(touched)
        modules = sorted(modules_set)
        print(f"check-dormant: {len(touched)} BDD-relevant path(s) changed vs {base_ref}")

    for note in notes:
        print(f"  note: {note}")
    if not modules:
        # Parity with the git-diff path, which says so explicitly rather than
        # exiting 0 in silence (a silent exit reads as "checked, all clear").
        print("check-dormant: the given path(s) map to no BDD test module — nothing to check")
        return 0

    print(
        f"check-dormant: running {len(modules)} module(s) without a database "
        "(wired scenarios skip, dormant ones xfail)..."
    )
    result = run_without_db(modules)
    # The clean no-DB run is exit 0: wired scenarios SKIP and dormant ones XFAIL,
    # neither of which produces a nonzero exit. ANY nonzero code means the run did
    # not complete as that clean skip/xfail pass -- a collection/import error (this
    # repo surfaces those as exit 1 via the step-plugin import), a failed test, or
    # an internal error -- so an empty XFAIL list is a broken run, not "no dormant
    # scenarios". Surface it and fail rather than reporting a false all-clear.
    if result.returncode != 0:
        print(
            f"\ncheck-dormant: pytest did not run cleanly (exit {result.returncode}); "
            "cannot assess dormant scenarios -- the 'no dormant' result would be false. "
            "Last stderr/stdout lines:"
        )
        for line in ((result.stderr or "") + (result.stdout or "")).splitlines()[-25:]:
            print(f"    {line}")
        return 1
    dormant, documented = classify(result.stdout)

    if documented:
        n = sum(len(v) for v in documented.values())
        print(f"\n  {n} scenario(s) xfailed as DOCUMENTED spec-production gaps (fine, listed for awareness)")

    if not dormant:
        print("  no dormant scenarios in the touched area — everything you changed either runs or is a documented gap")
        return 0

    total = sum(len(v) for v in dormant.values())
    print(f"\n  {total} DORMANT scenario(s) in the touched area — these never execute anywhere:")
    for reason, scenarios in sorted(dormant.items(), key=lambda kv: -len(kv[1])):
        print(f"\n  [{len(scenarios)}] {reason}")
        for s in sorted(scenarios)[:15]:
            print(f"      - {s}")
        if len(scenarios) > 15:
            print(f"      ... and {len(scenarios) - 15} more")
    print(
        "\n  Dormant scenarios are not coverage. Wire the harness/steps, or say so explicitly in the PR.\n"
        "  (Informational check — see #1603. Use --strict to make it fail.)"
    )
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
