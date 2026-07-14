"""Informational check: which BDD scenarios touched by your change never run?

The failure mode this surfaces (#1603): a branch edits step modules, feature
files, or the BDD conftest, CI stays green, and nothing anywhere says that the
scenarios involved are auto-xfailed — dormant steps look like coverage. This
is the local, informational companion to ``run_all_tests.sh`` Konstantin
suggested in #1603: it never fails your build (unless you opt into
``--strict``), it just tells you what is NOT running before you claim
coverage.

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
   collapsed.

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

# Reasons that mean "this scenario is dormant because nothing wires it", as
# opposed to documented spec-production gaps that are xfailed on purpose.
_DORMANT_MARKERS = (
    "no harness wired",
    "not yet wired",
    "step definition not found",
    "step definition is not found",
)

_XFAIL_LINE = re.compile(r"^XFAIL\s+(\S+?)(?:\s+-\s+(.*))?$")
_UC_IN_NAME = re.compile(r"uc(\d+)", re.IGNORECASE)


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


def changed_paths(base_ref: str) -> list[str]:
    """Committed changes vs merge-base plus uncommitted working-tree changes."""
    merge_base = _git("merge-base", "HEAD", base_ref)
    committed = _git("diff", "--name-only", f"{merge_base}..HEAD").splitlines()
    working = [line[3:] for line in _git("status", "--porcelain").splitlines() if len(line) > 3]
    seen: list[str] = []
    for p in committed + working:
        p = p.strip().replace("\\", "/")
        if p and p not in seen:
            seen.append(p)
    return seen


def is_bdd_relevant(path: str) -> bool:
    p = path.replace("\\", "/")
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
        p = raw.replace("\\", "/")
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


def run_without_db(modules: list[Path]) -> str:
    """Run the modules with DATABASE_URL removed; return pytest's stdout."""
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
    out = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, env=env, check=False)
    return out.stdout


def classify(pytest_output: str) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Split XFAIL lines into (dormant, documented) — reason -> scenario names.

    Transport params are collapsed so one scenario counts once, and dormant
    means the xfail reason matches a wiring/step gap rather than a documented
    spec-production gap.
    """
    dormant: dict[str, set[str]] = defaultdict(set)
    documented: dict[str, set[str]] = defaultdict(set)
    for line in pytest_output.splitlines():
        m = _XFAIL_LINE.match(line.strip())
        if not m:
            continue
        nodeid, reason = m.group(1), (m.group(2) or "").strip()
        # Collapse parametrization (outline rows nest brackets, so split not regex)
        scenario = nodeid.split("::")[-1].split("[", 1)[0]
        reason_key = reason.split(". ")[0][:120] if reason else "(no reason recorded)"
        bucket = dormant if any(k in reason.lower() for k in _DORMANT_MARKERS) else documented
        bucket[reason_key].add(scenario)
    return dormant, documented


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
        return 0

    print(
        f"check-dormant: running {len(modules)} module(s) without a database "
        "(wired scenarios skip, dormant ones xfail)..."
    )
    dormant, documented = classify(run_without_db(modules))

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
