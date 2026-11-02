#!/usr/bin/env python3
"""Phase 7 layer 5: bare node-id comparison of the MERGED tree against BOTH parents.

A green suite cannot detect regression-to-xfail or regression-to-skip -- a test that slides
from passing to expected-failure never fails anything. This layer compares the COLLECTED
node-id SETS instead, which is a claim about what exists rather than about what passed, and
it is the only layer that sees a scenario quietly stop being graded.

Naming, fixed by the baseline files already on disk:

    "ours"  = merge/main-into-rfc9421  (the INCOMING branch)
    "mine"  = feat/rfc9421-on-1721     (this branch, #1721 + the signing layer)

A node id present on a parent and absent from the merge is a DROP, and every drop is guilty
until explained individually. There are three innocent explanations and no others:

  deleted-by-1721      -- its whole FILE was deleted by #1721, deliberately and with a
                          recorded verdict in the manifest.
  rewritten-by-1721    -- its file survives but #1721 re-expressed what it grades; the
                          successor must be NAMED.
  in-surface           -- the file is in this merge's resolution surface, so the drop is
                          this merge's doing and needs a per-stage ledger entry.

Anything outside those three is an unexplained drop, which is a merge defect.

Usage: nodeid_compare.py collect <out-dir>     # collect the merged tree's node ids
       nodeid_compare.py report  <out-dir>     # compare against baselines/ and explain
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE / "baselines"
SUITES = ("unit", "integration", "bdd", "e2e", "admin")


def collect(out: Path) -> int:
    """Collect bare node ids per suite from the merged tree.

    ``-o addopts=""`` is mandatory: this repo's ``addopts`` carries reporting plugins that
    suppress the node listing, and a suite then reports as "0 collected" -- which reads as
    an empty suite and is in fact a collection error. Zero is therefore treated as failure,
    never as a pass.
    """
    out.mkdir(parents=True, exist_ok=True)
    rc = 0
    for suite in SUITES:
        proc = subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "pytest",
                f"tests/{suite}",
                "--collect-only",
                "-q",
                "-o",
                "addopts=",
                "-p",
                "no:randomly",
                "-c",
                "pytest.ini",
            ],
            capture_output=True,
            text=True,
            cwd=HERE.parents[1],
        )
        ids = sorted({line.strip() for line in proc.stdout.splitlines() if "::" in line})
        (out / f"nodeids-merged-{suite}.txt").write_text("\n".join(ids) + "\n")
        print(f"  {suite}: {len(ids)}")
        if not ids:
            print(f"  COLLECTION ERROR in {suite}:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
            rc = 1
    return rc


#: A parametrized id, e.g. ``...::test_x[e2e_rest]`` -> ``...::test_x``.
_PARAM = re.compile(r"\[.*\]$")


def _bare(nid: str) -> str:
    """The node id without its parametrization suffix.

    BOTH sides are compared bare, because the parent baselines were collected bare and a
    mixed comparison is not a comparison: matching ``test_x`` against ``test_x[mcp]``
    reports every parametrized test as both dropped AND gained. That is what it did on the
    first run -- bdd showed 1159 "dropped" and 8550 "gained" with ONE id in common, which
    reads as catastrophe and means nothing.

    Parametrization is not thrown away: :func:`report` prints the per-suite param COUNT
    beside the bare count, so a test that quietly loses a transport arm still shows up as a
    falling ratio even though its bare id survives.
    """
    return _PARAM.sub("", nid)


#: Explained drops: a bare id present on a parent, absent here, with its SUCCESSOR named.
#: The gate's doctrine is that a drop is guilty until each member is individually explained —
#: "deliberate retirement with citation, or deduplication with the surviving test named" — so
#: an entry here is that explanation, written down, not a silencer. Each must name a test that
#: exists in the merged tree and grades at least what the dropped one did.
_RENAMED: dict[str, str] = {
    # Renamed in the stage-7 behaviour pass, and STRENGTHENED in the same edit. The old test
    # asserted a uc005 slice was live. The successor grades an EXACT partition of the same
    # graded ids into ledgered-xfail and live-pass, reads which is which from the live
    # ``_XFAIL_TAGS`` routing map rather than a frozen list, and requires a ledgered record to
    # carry the ledger's own reason VERBATIM. Its docstring states why the relaxed
    # "either live or ledgered" predicate it replaced was not good enough.
    "tests/integration/test_bdd_scenario_liveness_real_run.py"
    "::test_real_run_records_uc005_format_id_roundtrip_scenarios_as_live": "tests/integration/test_bdd_scenario_liveness_real_run.py"
    "::test_real_run_records_uc005_scenarios_as_ledgered_or_live",
}


def _read(path: Path, *, bare: bool = True) -> set[str]:
    if not path.exists():
        return set()
    ids = (line.strip() for line in path.read_text().splitlines())
    return {(_bare(i) if bare else i) for i in ids if i}


def report(out: Path) -> int:
    deleted = _read(BASE / "ours-only-files-deleted-by-1721.txt")
    rewritten = _read(BASE / "ours-only-files-rewritten-by-1721.txt")
    in_surface = _read(BASE / "ours-only-files-in-surface.txt")
    unexplained: dict[str, list[str]] = {}
    summary = {}

    for suite in SUITES:
        merged = _read(out / f"nodeids-merged-{suite}.txt")
        merged_raw = _read(out / f"nodeids-merged-{suite}.txt", bare=False)
        print(
            f"# {suite}: {len(merged)} bare ids over {len(merged_raw)} collected (param ratio "
            f"{len(merged_raw) / max(len(merged), 1):.2f})"
        )
        for side in ("ours", "mine"):
            parent = _read(BASE / f"nodeids-{side}-{suite}.txt")
            if not parent:
                continue
            dropped = sorted(parent - merged)
            gained = sorted(merged - parent)
            buckets = {
                "deleted-by-1721": [],
                "rewritten-by-1721": [],
                "in-surface": [],
                "renamed": [],
                "UNEXPLAINED": [],
            }
            for nid in dropped:
                f = nid.split("::", 1)[0]
                successor = _RENAMED.get(nid)
                if successor:
                    assert successor in merged, (
                        f"{nid} is recorded as renamed to {successor}, but that successor is "
                        "not in the merged tree — the explanation is stale and the drop is real"
                    )
                    buckets["renamed"].append(nid)
                elif f in deleted:
                    buckets["deleted-by-1721"].append(nid)
                elif f in rewritten:
                    buckets["rewritten-by-1721"].append(nid)
                elif f in in_surface:
                    buckets["in-surface"].append(nid)
                else:
                    buckets["UNEXPLAINED"].append(nid)
            summary[f"{suite}/{side}"] = {
                "parent": len(parent),
                "merged": len(merged),
                "dropped": len(dropped),
                "gained": len(gained),
                **{k: len(v) for k, v in buckets.items()},
            }
            if buckets["UNEXPLAINED"]:
                unexplained[f"{suite}/{side}"] = buckets["UNEXPLAINED"]

    print(json.dumps(summary, indent=1))
    (out / "nodeid-comparison.json").write_text(json.dumps({"summary": summary, "unexplained": unexplained}, indent=1))
    if unexplained:
        print("\nUNEXPLAINED DROPS — each is a merge defect until individually explained:")
        for k, v in unexplained.items():
            print(f"\n{k}: {len(v)}")
            byfile: dict[str, int] = {}
            for nid in v:
                byfile[nid.split("::", 1)[0]] = byfile.get(nid.split("::", 1)[0], 0) + 1
            for f, n in sorted(byfile.items(), key=lambda kv: -kv[1]):
                print(f"   {n:5d} {f}")
        return 1
    print("\nno unexplained drops")
    return 0


if __name__ == "__main__":
    mode, target = sys.argv[1], Path(sys.argv[2])
    raise SystemExit(collect(target) if mode == "collect" else report(target))
