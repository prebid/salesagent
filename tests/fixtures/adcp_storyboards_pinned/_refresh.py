#!/usr/bin/env python3
"""Refresh the pinned AdCP storyboard index used by test_architecture_storyboard_binding.

Source of truth: adcontextprotocol/adcp, the compliance tree for the spec version
this repo pins in ``docs/adcp-spec-version.md`` (``dist/compliance/<version>/``).

Why an index rather than the storyboards themselves: the guard only needs to answer
three questions offline — does a cited storyboard path exist at the pin, does a named
phase live in that file, and what gates that storyboard. Vendoring the full YAML tree
(hundreds of files, most of them narrative prose) to answer three structural questions
would be noise. The index is a few KB and diffs legibly when the pin advances.

This deliberately mirrors the ``adcp_schemas_pinned`` fixture next door, with one
difference worth knowing: that fixture is frozen at commit 04f59d2d5 as an intentional
reference point for "AdCP 3.1 semantics", which is now OLDER than the 3.1.1 the repo
actually pins. This index tracks ``docs/adcp-spec-version.md`` instead, so the two can
disagree — and when they do, that disagreement is the finding.

To refresh (a deliberate, reviewed change — normally only when the pin advances):
    uv run python tests/fixtures/adcp_storyboards_pinned/_refresh.py

Reads from a local clone at ~/projects/adcp. Parsing primitives (the storyboard
universe filter, required_tools/requires_capability/requires_scenarios extraction,
phase-id parsing) come from scripts/audit/storyboard_spec.py — the shared L0 module
also used by storyboard_coverage_map.py and storyboard_binding_sweep.py, so this
index's universe and gate fields agree with theirs by construction.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts.audit import storyboard_spec  # noqa: E402

ADCP = storyboard_spec.adcp_home(REPO_ROOT)
OUT = Path(__file__).parent / "index.json"


def build() -> dict[str, object]:
    version = storyboard_spec.pinned_version(REPO_ROOT)
    dist = storyboard_spec.dist_root(ADCP, version)
    if not dist.is_dir():
        raise SystemExit(f"pinned compliance tree not found: {dist}\nIs ~/projects/adcp cloned and current?")

    required_by = storyboard_spec.requiring_indexes(dist)

    storyboards: dict[str, dict[str, object]] = {}
    for sb in storyboard_spec.storyboards(dist):
        entry: dict[str, object] = {"phases": sorted(storyboard_spec.phases(sb.text))}
        if capability := storyboard_spec.requires_capability(sb.text):
            # Keyed by the MATCHER the storyboard declared. Writing "equals"
            # unconditionally is what made the index a third copy of the
            # equals-only assumption: 28 of 54 declaring storyboards use
            # `contains` or `present` and were transcribed as ungated.
            path, matcher, value = capability
            entry["requires_capability"] = {"path": path, matcher: value}
        if tools := storyboard_spec.required_tools(sb.text):
            entry["required_tools"] = sorted(tools)
        if owners := required_by.get(sb.stem):
            entry["required_by"] = sorted(owners)
        tier = storyboard_spec.storyboard_tier(sb.rel)
        if tier == "specialisms":
            entry["specialism"] = sb.rel.split("/")[1]
        elif tier == "protocols":
            entry["protocol"] = sb.rel.split("/")[1]
        elif tier == "universal":
            entry["universal"] = True
        storyboards[sb.rel] = entry

    return {
        "adcp_spec_version": version,
        "source": f"adcontextprotocol/adcp dist/compliance/{version}",
        "storyboard_count": len(storyboards),
        "storyboards": storyboards,
    }


if __name__ == "__main__":
    index = build()
    OUT.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"wrote {OUT.relative_to(REPO_ROOT).as_posix()} — {index['storyboard_count']} storyboards at {index['adcp_spec_version']}"
    )
