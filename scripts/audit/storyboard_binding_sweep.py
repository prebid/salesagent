#!/usr/bin/env python3
"""Audit every @storyboard-v3.1 BDD scenario against the pinned AdCP storyboards.

Answers, per scenario, the questions that decide whether its tag is honest:

  1. Does the ``@source`` footer cite a path that EXISTS at the pinned version?
  2. Does the cited phase/step exist in that file?
  3. If not, where does it actually live?
  4. Is the behaviour GRADED (under ``validations:``) or narrative (``expected:``)?
  5. Which storyboard tier owns it -- universal / protocol / domain / specialism?
  6. Do we DECLARE the specialism or protocol that gates it?

Read-only. Emits JSON on stdout; ``--markdown`` renders the checked-in baseline.

The pinned version comes from docs/adcp-spec-version.md, never hardcoded here --
a sweep that hardcodes the version rots the same way the pins it audits did.

Parsing primitives (pinned version, declared capabilities, phase/check grading,
@source footer parsing, path normalization, tier classification) come from
scripts/audit/storyboard_spec.py -- the shared L0 module also used by
storyboard_coverage_map.py and the tests/fixtures/adcp_storyboards_pinned index,
so this sweep's findings agree with the coverage map and the make quality guard
by construction.
"""

from __future__ import annotations

import dataclasses
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.audit import storyboard_spec  # noqa: E402

#: The bucket vocabulary, worst last. Every value here is ASSIGNED by :func:`audit`,
#: and ``test_architecture_storyboard_binding_buckets.py`` derives the assignable set
#: from this module's AST to keep it that way.
#:
#: ``D under-asserts`` and ``E prod-blocked`` USED to be declared in this comment and in
#: the rendered legend, and were assigned by no code path. The published histogram
#: therefore reported zero under-asserting and zero production-blocked scenarios — two
#: claims this sweep has no data for. Deleted rather than implemented: whether a
#: scenario under-asserts is what the step-assertion inspector grades, and whether it is
#: blocked on production is a judgement, not a parse.
BUCKET_VERIFIED = "A"
BUCKET_UNVERIFIED = "U"
BUCKET_WRONG_SOURCE = "B"
BUCKET_TAG_UNJUSTIFIED = "C"

#: Bucket -> what it means, in assignment order. Also the render's legend, so the legend
#: cannot name a bucket the code does not assign.
BUCKET_LEGEND: dict[str, str] = {
    BUCKET_VERIFIED: "binding verified — at least one grading check RAN and none fired",
    BUCKET_UNVERIFIED: "NOT MEASURED — a check the sweep needed could not run",
    BUCKET_WRONG_SOURCE: "wrong or stale `@source`",
    BUCKET_TAG_UNJUSTIFIED: "tag unjustified — ungraded, malformed, or an undeclared gate",
}

#: Worst-wins ordering. Before this, one branch used ``max(binding.bucket, "B")`` and
#: every other used plain assignment, so a scenario that earned C and then hit the
#: phase-mismatch branch was DOWNGRADED to B — the bucket a scenario landed in depended
#: on the order the checks happened to run in. One rule, applied by :meth:`Binding.flag`.
_BUCKET_RANK: dict[str, int] = {name: rank for rank, name in enumerate(BUCKET_LEGEND)}


@dataclass
class Binding:
    """One @storyboard-v3.1 scenario and everything the sweep can prove about it."""

    feature: str
    line: int
    identifier: str
    tags: list[str]
    title: str
    sources: list[dict[str, str]] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    #: Checks that could NOT run for this scenario. Distinct from ``findings``: a finding
    #: is something measured; one of these is a measurement that did not happen.
    unverified: list[str] = field(default_factory=list)
    #: How many grading checks actually ran. ``A`` requires this to be non-zero, which is
    #: what makes it a POSITIVE verdict rather than the absence of a negative one.
    checks_run: int = 0
    #: Empty until :meth:`finalize`. It used to default to ``"A"`` with the comment
    #: "A ok", so every check the sweep could not run rendered as a pass.
    bucket: str = ""

    def flag(self, bucket: str) -> None:
        """Record *bucket*, keeping the worst seen so far."""
        if _BUCKET_RANK[bucket] > _BUCKET_RANK.get(self.bucket, -1):
            self.bucket = bucket

    def measured(self) -> None:
        """One grading check ran and did not fire."""
        self.checks_run += 1

    def not_measured(self, reason: str) -> None:
        """A check the sweep needed could not run. Never a pass."""
        self.unverified.append(reason)
        self.flag(BUCKET_UNVERIFIED)

    def finalize(self) -> Binding:
        """Decide the bucket once, from what was actually measured.

        ``A`` is earned, not defaulted: a scenario reaches it only when at least one
        grading check RAN and nothing fired. A scenario whose every source was a schema
        path, or that resolved no storyboard at all, ran no check and is ``U`` — not ok.
        """
        if not self.bucket:
            if self.checks_run:
                self.flag(BUCKET_VERIFIED)
            else:
                self.not_measured("no grading check ran for this scenario at all")
        return self

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the JSON report.

        Every field, mechanically — a hand-written mirror drifts the moment a field is added.
        """
        return dataclasses.asdict(self)


def audit(repo: Path, adcp: Path) -> dict[str, Any]:
    version = storyboard_spec.pinned_version(repo)
    dist = storyboard_spec.dist_root(adcp, version)
    if not dist.is_dir():
        raise storyboard_spec.StoryboardAuditError(f"pinned compliance tree missing: {dist}")

    declared = storyboard_spec.declared_capabilities(repo)
    scenarios = storyboard_spec.tagged_scenarios(repo / "tests" / "bdd" / "features")
    phases = storyboard_spec.phase_index(dist)

    bindings: list[Binding] = []
    for scenario in scenarios:
        binding = Binding(
            feature=scenario.feature,
            line=scenario.line,
            identifier=scenario.identifier,
            tags=scenario.tags,
            title=scenario.title,
        )
        for match in re.finditer(r"@source\s+\S.*", scenario.block):
            try:
                footer = storyboard_spec.parse_source_footer(match.group(0))
            except storyboard_spec.SourceFooterError as exc:
                binding.findings.append(f"malformed @source footer: {exc}")
                binding.flag(BUCKET_TAG_UNJUSTIFIED)
                continue
            if footer is not None:
                binding.sources.append(
                    {
                        "repo": footer.repo,
                        "ref": footer.ref,
                        "commit": footer.commit or "",
                        "phase": footer.phase or "",
                        "step": footer.step or "",
                        "path": footer.path,
                    }
                )

        # Phases the scenario names in prose, whether or not it set `phase=`.
        #
        # Match only an explicit phase REFERENCE ("<id> phase:", "phase=<id>",
        # "step <id>"), never a bare token: most phase ids are also tool names
        # (`create_media_buy`, `get_products`, `list_creatives`) that appear in
        # ordinary prose, and treating those as bindings manufactures findings.
        named = sorted(
            p
            for p in phases
            if re.search(
                rf"(?:\b{re.escape(p)}\s+(?:phase|step)\b|\bphase[=:]\s*{re.escape(p)}\b"
                rf"|\bstep\s+{re.escape(p)}\b)",
                scenario.block,
            )
        )
        cited_files = {
            storyboard_spec.normalize_cited_path(s["path"]) for s in binding.sources if "schemas" not in s["path"]
        }
        # Scenarios name their own storyboard in a summary line ("# <name>: <claim>")
        # immediately above the @source footer. When that self-declared name does not
        # match the cited file, the footer points somewhere the scenario never claimed.
        declared_names = scenario.self_declared_names
        cited_stems = {storyboard_spec.storyboard_key(p) for p in cited_files}
        if declared_names and cited_stems and not (declared_names & cited_stems):
            # Only a finding when the declared name is a real storyboard/phase id.
            real = {
                n
                for n in declared_names
                if n in phases or any(storyboard_spec.storyboard_key(f) == n for f in phases.get(n, []))
            }
            real |= {
                n
                for n in declared_names
                if any((dist / f).exists() for f in [f"protocols/media-buy/scenarios/{n}.yaml"])
            }
            if real:
                binding.findings.append(
                    f"self-declared storyboard {sorted(real)} does not match cited file {sorted(cited_stems)} "
                    "— footer points at a storyboard this scenario never claims"
                )
                binding.flag(BUCKET_WRONG_SOURCE)

        for phase in named:
            owners = phases[phase]
            if cited_files and not (cited_files & set(owners)):
                binding.findings.append(
                    f"names phase {phase!r} but cites {sorted(cited_files)} — that phase lives in {owners}"
                )
                binding.flag(BUCKET_WRONG_SOURCE)

        if not binding.sources:
            binding.findings.append("NO @source footer — binding is unverifiable")
            binding.flag(BUCKET_TAG_UNJUSTIFIED)
            bindings.append(binding.finalize())
            continue

        for source in binding.sources:
            raw_path, ref, phase, step = source["path"], source["ref"], source["phase"], source["step"]

            if version not in ref:
                binding.findings.append(f"stale ref {ref!r} — pinned version is {version}")
                binding.flag(BUCKET_WRONG_SOURCE)

            rel = storyboard_spec.normalize_cited_path(raw_path)
            is_schema = "schemas" in raw_path
            if is_schema:
                # No storyboard check can run against a schema path. Recorded as a
                # measurement that did not happen, so a scenario citing ONLY schema
                # paths cannot reach A by having had nothing to fail.
                source["verdict"] = "schema-path (not a storyboard)"
                binding.not_measured(f"{raw_path}: schema path — this sweep grades storyboards, not schemas")
                continue

            target = dist / rel
            source["resolved"] = str(target.relative_to(adcp)) if target.exists() else ""
            if not target.exists():
                binding.findings.append(f"cited path does not exist at {version}: {rel}")
                binding.flag(BUCKET_WRONG_SOURCE)
                continue

            text = target.read_text(encoding="utf-8")
            tier = storyboard_spec.storyboard_tier(rel)
            source["tier"] = tier
            grading = storyboard_spec.phase_is_graded(text, phase)
            source["grading"] = grading or "not-measured"
            if grading is None:
                # `phase_is_graded` answers None for a FALSY phase — the footer cited a
                # file and named nothing inside it, so questions 2 and 4 (does the phase
                # exist; is it graded or narrative) were never asked. This used to be
                # the silent path to bucket A.
                binding.not_measured(
                    f"{rel}: @source declares no `phase=`, so neither the phase-exists nor the "
                    "graded-vs-narrative check ran for it"
                )
            elif grading == "absent":
                binding.findings.append(f"phase {phase!r} not in cited file at {version}")
                binding.flag(BUCKET_WRONG_SOURCE)
            elif grading == "prose":
                binding.findings.append(f"phase {phase!r} is narrative (expected:) not graded (validations:)")
                binding.flag(BUCKET_TAG_UNJUSTIFIED)
            else:
                binding.measured()

            if step:
                # `step` is the addressable unit the conformance ledger keys on
                # (protocol, track, storyboard_id, step_id) -- resolved against
                # the cited file exactly like `phase` is, so a `step=` that
                # names nothing real fails loudly instead of being carried
                # unchecked.
                step_grading = storyboard_spec.phase_is_graded(text, step)
                if step_grading == "absent":
                    binding.findings.append(f"step {step!r} not in cited file at {version}")
                    binding.flag(BUCKET_WRONG_SOURCE)
                else:
                    binding.measured()

            if tier == "specialisms":
                name = rel.split("/")[1]
                if name not in declared["specialisms"]:
                    binding.findings.append(f"gated by specialism {name!r} which we do NOT declare — tag is wrong")
                    binding.flag(BUCKET_TAG_UNJUSTIFIED)

        bindings.append(binding.finalize())

    # Seeded with every bucket in the vocabulary, so a bucket nothing landed in prints
    # its explicit zero. A histogram of only the buckets that occurred cannot be told
    # apart from a vocabulary that has shrunk.
    buckets: dict[str, int] = dict.fromkeys(BUCKET_LEGEND, 0)
    for binding in bindings:
        buckets[binding.bucket] += 1

    return {
        "pinned_version": version,
        "declared": {k: sorted(v) for k, v in declared.items()},
        "scenario_count": len(bindings),
        "buckets": buckets,
        "bucket_legend": dict(BUCKET_LEGEND),
        #: How many scenarios carry at least one check that could not run. Not the same
        #: as the U bucket: a scenario with an unrunnable check AND a real finding is
        #: bucketed on the finding, and the hole in its verdict would otherwise vanish.
        "scenarios_with_unrunnable_checks": sum(1 for b in bindings if b.unverified),
        "bindings": [b.to_dict() for b in bindings],
    }


def render_markdown(result: dict[str, Any]) -> str:
    version = result["pinned_version"]
    out = [
        f"# Storyboard binding baseline — AdCP {version}",
        "",
        f"`{result['scenario_count']}` scenarios tagged `{storyboard_spec.TAG}`. "
        f"Declared specialisms: `{', '.join(result['declared']['specialisms']) or 'none'}`; "
        f"protocols: `{', '.join(result['declared']['protocols']) or 'none'}`.",
        "",
        "Buckets — " + " · ".join(f"**{k}** {v}" for k, v in result["bucket_legend"].items()) + ".",
        "",
        "Every bucket carries its count, including the zeros: "
        + ", ".join(f"**{k}** {n}" for k, n in result["buckets"].items())
        + f" of **{result['scenario_count']}** scenarios. "
        + f"**{result['scenarios_with_unrunnable_checks']}** scenario(s) carry at least one check "
        "this sweep could not run — listed in the Not measured column, and a hole in whatever "
        "their bucket says.",
        "",
        "| Scenario | Feature:line | Bucket | Findings | Not measured |",
        "|---|---|---|---|---|",
    ]
    for b in result["bindings"]:
        findings = "<br>".join(b["findings"]) or "—"
        unverified = "<br>".join(b["unverified"]) or "—"
        out.append(
            f"| `{b['identifier']}` | {b['feature']}:{b['line']} | **{b['bucket']}** | {findings} | {unverified} |"
        )
    return "\n".join(out) + "\n"


def main() -> int:
    return storyboard_spec.run_cli(__doc__ or "", audit, render_markdown)


if __name__ == "__main__":
    sys.exit(main())
