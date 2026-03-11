"""Compile adcp-req BDD feature files into salesagent-consumable features.

Transforms upstream feature files by:
  - Extracting @contextgit metadata into scenario tags
  - Adding traceability tags from bdd-traceability.yaml
  - Writing compiled .feature files to tests/bdd/features/
  - Updating bdd-traceability.yaml with new scenario mappings

Usage:
    python scripts/compile_bdd.py --uc UC-005           # compile one UC
    python scripts/compile_bdd.py --all                 # compile all
    python scripts/compile_bdd.py --verify              # check if compiled files are up-to-date
    python scripts/compile_bdd.py --dry-run --uc UC-005 # show what would change
    python scripts/compile_bdd.py --adcp-req-path /path # override source path
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRACEABILITY_PATH = PROJECT_ROOT / "docs" / "test-obligations" / "bdd-traceability.yaml"
OUTPUT_DIR = PROJECT_ROOT / "tests" / "bdd" / "features"

DEFAULT_ADCP_REQ_PATH = Path(os.environ.get("ADCP_REQ_PATH", str(Path.home() / "projects" / "adcp-req")))

# ---------------------------------------------------------------------------
# Data structures for parsed Gherkin
# ---------------------------------------------------------------------------


@dataclass
class ContextGit:
    """Parsed ``# @contextgit`` comment."""

    id: str
    type: str = "test"
    upstream: list[str] = field(default_factory=list)


@dataclass
class ExamplesBlock:
    """An Examples: table within a Scenario Outline."""

    header_line: str  # e.g. "Examples:" or "Examples: Valid partitions"
    rows: list[str] = field(default_factory=list)  # includes header + data rows


@dataclass
class Step:
    """A single Gherkin step (Given/When/Then/And/But) with optional table."""

    keyword: str  # "Given", "When", "Then", "And", "But"
    text: str
    table_rows: list[str] = field(default_factory=list)


@dataclass
class Scenario:
    """A parsed Scenario or Scenario Outline block."""

    contextgit: ContextGit | None
    tags: list[str]  # e.g. ["@main-flow", "@rest", "@post-s1"]
    keyword: str  # "Scenario" or "Scenario Outline"
    name: str
    steps: list[Step] = field(default_factory=list)
    examples: list[ExamplesBlock] = field(default_factory=list)
    comment_lines: list[str] = field(default_factory=list)  # inline comments


@dataclass
class Feature:
    """A parsed Gherkin feature file."""

    contextgit: ContextGit | None
    feature_tags: list[str]  # tags on the Feature line itself
    feature_line: str  # "Feature: BR-UC-005 ..."
    description_lines: list[str]  # lines between Feature and Background
    background_lines: list[str]  # Background: block (raw lines)
    scenarios: list[Scenario] = field(default_factory=list)
    preamble_comments: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Contextgit parsing
# ---------------------------------------------------------------------------

_CONTEXTGIT_RE = re.compile(
    r"#\s*@contextgit\s+"
    r"id=(?P<id>\S+)"
    r"(?:\s+type=(?P<type>\S+))?"
    r"(?:\s+upstream=\[(?P<upstream>[^\]]*)\])?"
)


def _parse_contextgit(line: str) -> ContextGit | None:
    """Parse a ``# @contextgit ...`` comment line.  Returns None if not a match."""
    m = _CONTEXTGIT_RE.search(line)
    if not m:
        return None
    upstream_raw = m.group("upstream") or ""
    upstream = [s.strip() for s in upstream_raw.split(",") if s.strip()]
    return ContextGit(
        id=m.group("id"),
        type=m.group("type") or "test",
        upstream=upstream,
    )


# ---------------------------------------------------------------------------
# Tag parsing
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"@[\w\-.]+")


def _parse_tags(line: str) -> list[str]:
    """Extract all @tags from a line."""
    return _TAG_RE.findall(line.strip())


def _is_tag_line(line: str) -> bool:
    """Return True if the line consists entirely of @tags (and whitespace)."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    # Remove all tags — if nothing meaningful remains, it's a tag line
    remaining = _TAG_RE.sub("", stripped).strip()
    return len(remaining) == 0 and bool(_TAG_RE.search(stripped))


# ---------------------------------------------------------------------------
# Feature file parser
# ---------------------------------------------------------------------------

_FEATURE_RE = re.compile(r"^\s*Feature:\s*(.+)$")
_BACKGROUND_RE = re.compile(r"^\s*Background:\s*$")
_SCENARIO_RE = re.compile(r"^\s*(Scenario Outline|Scenario):\s*(.+)$")
_STEP_RE = re.compile(r"^\s*(Given|When|Then|And|But)\s+(.+)$")
_EXAMPLES_RE = re.compile(r"^\s*Examples:\s*(.*)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def parse_feature_file(text: str) -> Feature:
    """Parse a Gherkin feature file into structured data."""
    lines = text.splitlines()
    idx = 0
    total = len(lines)

    # --- Feature-level contextgit + tags ---
    feature_contextgit: ContextGit | None = None
    feature_tags: list[str] = []
    preamble_comments: list[str] = []

    # Consume leading lines before Feature:
    while idx < total:
        line = lines[idx]
        stripped = line.strip()

        cg = _parse_contextgit(stripped)
        if cg is not None:
            feature_contextgit = cg
            idx += 1
            continue

        if _is_tag_line(stripped):
            feature_tags.extend(_parse_tags(stripped))
            idx += 1
            continue

        if _FEATURE_RE.match(stripped):
            break

        if stripped.startswith("#") or stripped == "":
            preamble_comments.append(line)
            idx += 1
            continue

        idx += 1

    # --- Feature line ---
    feature_line = ""
    if idx < total:
        m = _FEATURE_RE.match(lines[idx].strip())
        if m:
            feature_line = lines[idx].strip()
        idx += 1

    # --- Description lines (between Feature and Background/Scenario) ---
    description_lines: list[str] = []
    while idx < total:
        stripped = lines[idx].strip()
        if _BACKGROUND_RE.match(stripped) or _SCENARIO_RE.match(stripped):
            break
        # Also break on a contextgit that precedes a scenario
        if _parse_contextgit(stripped) is not None:
            # Peek ahead — if next meaningful line is tags or scenario, stop
            break
        if _is_tag_line(stripped):
            break
        description_lines.append(lines[idx])
        idx += 1

    # --- Background ---
    background_lines: list[str] = []
    if idx < total and _BACKGROUND_RE.match(lines[idx].strip()):
        background_lines.append(lines[idx])
        idx += 1
        while idx < total:
            stripped = lines[idx].strip()
            # Background ends at scenario, contextgit, or tag line for a scenario
            if _SCENARIO_RE.match(stripped):
                break
            cg = _parse_contextgit(stripped)
            if cg is not None:
                break
            if _is_tag_line(stripped):
                break
            # Section comment headers also signal end of background
            if stripped.startswith("# ====="):
                break
            background_lines.append(lines[idx])
            idx += 1

    # --- Scenarios ---
    scenarios: list[Scenario] = []

    while idx < total:
        stripped = lines[idx].strip()

        # Skip blank lines and section comments between scenarios
        if stripped == "" or (stripped.startswith("#") and _parse_contextgit(stripped) is None):
            idx += 1
            continue

        # Collect contextgit + tags preceding a scenario
        scenario_contextgit: ContextGit | None = None
        scenario_tags: list[str] = []

        while idx < total:
            stripped = lines[idx].strip()
            cg = _parse_contextgit(stripped)
            if cg is not None:
                scenario_contextgit = cg
                idx += 1
                continue
            if _is_tag_line(stripped):
                scenario_tags.extend(_parse_tags(stripped))
                idx += 1
                continue
            break

        if idx >= total:
            break

        # Must be a Scenario/Scenario Outline line
        stripped = lines[idx].strip()
        m = _SCENARIO_RE.match(stripped)
        if not m:
            idx += 1
            continue

        scenario_keyword = m.group(1)
        scenario_name = m.group(2)
        idx += 1

        # Collect steps, inline comments, examples
        steps: list[Step] = []
        examples_blocks: list[ExamplesBlock] = []
        comment_lines: list[str] = []

        while idx < total:
            stripped = lines[idx].strip()

            # End of this scenario?
            if _SCENARIO_RE.match(stripped):
                break
            if _parse_contextgit(stripped) is not None:
                break
            if _is_tag_line(stripped):
                break
            # Section delimiter
            if stripped.startswith("# ====="):
                break

            # Examples block
            em = _EXAMPLES_RE.match(stripped)
            if em is not None:
                header = lines[idx].rstrip()
                idx += 1
                example_rows: list[str] = []
                # Consume blank line between Examples: header and table
                while idx < total and lines[idx].strip() == "":
                    idx += 1
                while idx < total and _TABLE_ROW_RE.match(lines[idx]):
                    example_rows.append(lines[idx].rstrip())
                    idx += 1
                examples_blocks.append(ExamplesBlock(header_line=header, rows=example_rows))
                continue

            # Step line
            sm = _STEP_RE.match(stripped)
            if sm is not None:
                step = Step(keyword=sm.group(1), text=sm.group(2))
                idx += 1
                # Collect any table rows directly under the step
                while idx < total and _TABLE_ROW_RE.match(lines[idx]):
                    step.table_rows.append(lines[idx].rstrip())
                    idx += 1
                steps.append(step)
                continue

            # Inline comment
            if stripped.startswith("#"):
                comment_lines.append(lines[idx].rstrip())
                idx += 1
                continue

            # Blank line inside scenario
            if stripped == "":
                idx += 1
                continue

            # Table row not preceded by step — skip
            if _TABLE_ROW_RE.match(stripped):
                idx += 1
                continue

            # Unknown line — skip
            idx += 1

        scenarios.append(
            Scenario(
                contextgit=scenario_contextgit,
                tags=scenario_tags,
                keyword=scenario_keyword,
                name=scenario_name,
                steps=steps,
                examples=examples_blocks,
                comment_lines=comment_lines,
            )
        )

    return Feature(
        contextgit=feature_contextgit,
        feature_tags=feature_tags,
        feature_line=feature_line,
        description_lines=description_lines,
        background_lines=background_lines,
        scenarios=scenarios,
        preamble_comments=preamble_comments,
    )


# ---------------------------------------------------------------------------
# Business rules extraction
# ---------------------------------------------------------------------------

_BR_RULE_RE = re.compile(r"@(BR-RULE-\d+)", re.IGNORECASE)


def _extract_business_rules(tags: list[str]) -> list[str]:
    """Extract BR-RULE-* identifiers from tag list."""
    rules: list[str] = []
    for tag in tags:
        m = _BR_RULE_RE.match(tag)
        if m:
            rules.append(m.group(1).upper())
    return sorted(set(rules))


# ---------------------------------------------------------------------------
# Traceability YAML I/O
# ---------------------------------------------------------------------------


def _load_traceability(path: Path) -> dict:
    """Load bdd-traceability.yaml, returning the raw dict."""
    if not path.exists():
        return {
            "schema_version": 1,
            "source": {"repository": "adcp-req", "commit": None, "compiled_at": None},
            "mappings": {},
        }
    text = path.read_text()
    data = yaml.safe_load(text)
    if data is None:
        return {
            "schema_version": 1,
            "source": {"repository": "adcp-req", "commit": None, "compiled_at": None},
            "mappings": {},
        }
    # Ensure mappings is a dict (yaml may parse empty {} with comment as None)
    if data.get("mappings") is None:
        data["mappings"] = {}
    return data


def _save_traceability(path: Path, data: dict) -> None:
    """Write bdd-traceability.yaml with human-readable formatting."""
    header = (
        "# BDD Traceability Mapping\n"
        "# Links adcp-req BDD scenarios to salesagent obligation IDs.\n"
        "# This is the single source of truth for cross-repository traceability.\n"
        "#\n"
        "# Maintained by: scripts/compile_bdd.py (auto-updates on compilation)\n"
        "# Manual edits: Only to change status or fix obligation_id mappings\n"
        "#\n"
        "# Status lifecycle:\n"
        "#   new      -> scenario exists in adcp-req but has no salesagent obligation yet\n"
        "#   mapped   -> scenario linked to an existing salesagent obligation ID\n"
        "#   stale    -> scenario references behavior salesagent doesn't implement\n"
        "#   conflict -> scenario contradicts what salesagent actually does\n"
        "#\n"
        "# When compile_bdd.py finds a new scenario not in this file, it adds it\n"
        "# with status: new and obligation_id: null.\n"
        "\n"
    )

    # Build ordered output
    output = header
    output += f"schema_version: {data['schema_version']}\n"
    output += "source:\n"
    output += f"  repository: {data['source']['repository']}\n"
    commit = data["source"].get("commit")
    output += f"  commit: {commit if commit else 'null'}\n"
    compiled_at = data["source"].get("compiled_at")
    output += f"  compiled_at: {_yaml_str(str(compiled_at)) if compiled_at else 'null'}\n"
    output += "\n"

    mappings = data.get("mappings", {})
    if not mappings:
        output += "mappings: {}\n"
    else:
        output += "mappings:\n"
        for uc_key in sorted(mappings.keys()):
            scenarios = mappings[uc_key]
            output += f"  {uc_key}:\n"
            for s in scenarios:
                output += f"    - adcp_scenario_id: {_yaml_str(s['adcp_scenario_id'])}\n"
                output += f"      adcp_feature: {_yaml_str(s['adcp_feature'])}\n"
                obligation = s.get("obligation_id")
                output += f"      obligation_id: {_yaml_str(obligation) if obligation else 'null'}\n"
                upstream = s.get("upstream_refs", [])
                output += f"      upstream_refs: {_yaml_list(upstream)}\n"
                rules = s.get("business_rules", [])
                output += f"      business_rules: {_yaml_list(rules)}\n"
                output += f"      status: {s['status']}\n"

    path.write_text(output)


def _yaml_str(val: str | None) -> str:
    """Format a value for inline YAML."""
    if val is None:
        return "null"
    # Quote if it contains special chars
    if any(c in val for c in ":#{}[]&*!|>'\"%@`"):
        return f'"{val}"'
    return f'"{val}"'


def _yaml_list(items: list[str]) -> str:
    """Format a short list for inline YAML."""
    if not items:
        return "[]"
    quoted = ", ".join(f'"{i}"' for i in items)
    return f"[{quoted}]"


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _get_commit_sha(repo_path: Path) -> str:
    """Get the HEAD commit SHA for a git repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


# ---------------------------------------------------------------------------
# Feature file discovery
# ---------------------------------------------------------------------------


def _find_feature_files(adcp_req_path: Path, uc_filter: str | None = None) -> list[Path]:
    """Find feature files in adcp-req, optionally filtered by UC number."""
    features_dir = adcp_req_path / "tests" / "features"
    if not features_dir.exists():
        print(f"ERROR: Features directory not found: {features_dir}", file=sys.stderr)
        sys.exit(1)

    pattern = "BR-UC-*.feature"
    if uc_filter:
        # UC-005 -> BR-UC-005-*.feature
        # Strip leading "UC-" if present to normalize
        uc_num = uc_filter.replace("UC-", "")
        pattern = f"BR-UC-{uc_num}-*.feature"

    files = sorted(features_dir.glob(pattern))
    if not files:
        print(f"WARNING: No feature files matching {pattern} in {features_dir}", file=sys.stderr)
    return files


# ---------------------------------------------------------------------------
# UC key extraction
# ---------------------------------------------------------------------------

_UC_RE = re.compile(r"BR-UC-(\d+)")


def _extract_uc_key(filename: str) -> str:
    """Extract UC-XXX key from a feature filename like BR-UC-005-discover-creative-formats.feature."""
    m = _UC_RE.search(filename)
    if m:
        return f"UC-{m.group(1)}"
    return filename


# ---------------------------------------------------------------------------
# Transformation
# ---------------------------------------------------------------------------


def _lookup_scenario_mapping(traceability: dict, uc_key: str, scenario_id: str) -> dict | None:
    """Look up a scenario in the traceability mappings."""
    uc_mappings = traceability.get("mappings", {}).get(uc_key, [])
    for mapping in uc_mappings:
        if mapping["adcp_scenario_id"] == scenario_id:
            return mapping
    return None


def _transform_scenario_tags(
    scenario: Scenario,
    traceability: dict,
    uc_key: str,
    feature_filename: str,
) -> tuple[list[str], dict | None]:
    """Compute the output tags for a scenario and return any new mapping to add.

    Returns (output_tags, new_mapping_or_None).
    """
    output_tags = list(scenario.tags)  # preserve existing Gherkin tags
    new_mapping: dict | None = None

    if scenario.contextgit is None:
        return output_tags, new_mapping

    cg = scenario.contextgit
    scenario_id = cg.id

    # Add the contextgit id as a tag
    if f"@{scenario_id}" not in output_tags:
        output_tags.insert(0, f"@{scenario_id}")

    # Look up in traceability
    mapping = _lookup_scenario_mapping(traceability, uc_key, scenario_id)

    if mapping is not None:
        status = mapping.get("status", "new")
        if status == "mapped":
            obligation_id = mapping.get("obligation_id")
            if obligation_id and f"@{obligation_id}" not in output_tags:
                output_tags.insert(1, f"@{obligation_id}")
        elif status in ("stale", "conflict"):
            if "@skip" not in output_tags:
                output_tags.append("@skip")
    else:
        # New scenario — add @pending tag and create a new mapping entry
        if "@pending" not in output_tags:
            output_tags.append("@pending")
        business_rules = _extract_business_rules(scenario.tags)
        new_mapping = {
            "adcp_scenario_id": scenario_id,
            "adcp_feature": feature_filename,
            "obligation_id": None,
            "upstream_refs": cg.upstream,
            "business_rules": business_rules,
            "status": "new",
        }

    return output_tags, new_mapping


def _render_feature(
    feature: Feature,
    traceability: dict,
    uc_key: str,
    feature_filename: str,
    commit_sha: str,
) -> tuple[str, list[dict]]:
    """Render a transformed feature file.

    Returns (rendered_text, list_of_new_mappings).
    """
    new_mappings: list[dict] = []
    lines: list[str] = []

    # Generation stamp
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines.append(f"# Generated from adcp-req @ {commit_sha} on {now}")
    lines.append("# DO NOT EDIT -- re-run: python scripts/compile_bdd.py")
    lines.append("")

    # Feature-level tags (preserve original, skip contextgit-derived ones)
    if feature.feature_tags:
        lines.append(" ".join(feature.feature_tags))

    # Feature line
    lines.append(feature.feature_line)

    # Description
    for dl in feature.description_lines:
        lines.append(dl.rstrip())

    # Background
    if feature.background_lines:
        for bl in feature.background_lines:
            lines.append(bl.rstrip())
        lines.append("")

    # Scenarios
    for scenario in feature.scenarios:
        output_tags, new_mapping = _transform_scenario_tags(scenario, traceability, uc_key, feature_filename)
        if new_mapping is not None:
            new_mappings.append(new_mapping)

        # Tags line
        if output_tags:
            lines.append("  " + " ".join(output_tags))

        # Scenario line
        lines.append(f"  {scenario.keyword}: {scenario.name}")

        # Steps
        for step in scenario.steps:
            lines.append(f"    {step.keyword} {step.text}")
            for tr in step.table_rows:
                lines.append(f"    {tr.strip()}")

        # Inline comments (postcondition references)
        for cl in scenario.comment_lines:
            lines.append(f"    {cl.strip()}")

        # Examples blocks
        for eb in scenario.examples:
            lines.append("")
            lines.append(f"    {eb.header_line.strip()}")
            for row in eb.rows:
                lines.append(f"      {row.strip()}")

        lines.append("")

    return "\n".join(lines) + "\n", new_mappings


# ---------------------------------------------------------------------------
# Compile pipeline
# ---------------------------------------------------------------------------


def compile_feature(
    source_path: Path,
    traceability: dict,
    commit_sha: str,
    *,
    dry_run: bool = False,
) -> tuple[str, str, list[dict]]:
    """Compile a single feature file.

    Returns (uc_key, output_text, new_mappings).
    """
    text = source_path.read_text()
    feature = parse_feature_file(text)
    feature_filename = source_path.name
    uc_key = _extract_uc_key(feature_filename)

    output_text, new_mappings = _render_feature(feature, traceability, uc_key, feature_filename, commit_sha)

    if not dry_run:
        output_path = OUTPUT_DIR / feature_filename
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output_text)
        print(f"  Compiled: {feature_filename} -> {output_path.relative_to(PROJECT_ROOT)}")
    else:
        print(f"  [DRY RUN] Would compile: {feature_filename}")
        print(f"    Scenarios: {len(feature.scenarios)}")
        print(f"    New mappings: {len(new_mappings)}")

    return uc_key, output_text, new_mappings


def compile_features(
    adcp_req_path: Path,
    uc_filter: str | None = None,
    *,
    dry_run: bool = False,
) -> None:
    """Main compilation pipeline."""
    feature_files = _find_feature_files(adcp_req_path, uc_filter)
    if not feature_files:
        return

    commit_sha = _get_commit_sha(adcp_req_path)
    traceability = _load_traceability(TRACEABILITY_PATH)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Compiling {len(feature_files)} feature file(s) from adcp-req @ {commit_sha[:10]}")
    if dry_run:
        print("[DRY RUN MODE]")
    print()

    all_new_mappings: dict[str, list[dict]] = {}

    for source_path in feature_files:
        uc_key, _output_text, new_mappings = compile_feature(source_path, traceability, commit_sha, dry_run=dry_run)
        if new_mappings:
            all_new_mappings.setdefault(uc_key, []).extend(new_mappings)

    # Update traceability YAML
    if all_new_mappings:
        for uc_key, new_maps in all_new_mappings.items():
            existing = traceability["mappings"].setdefault(uc_key, [])
            existing_ids = {m["adcp_scenario_id"] for m in existing}
            for nm in new_maps:
                if nm["adcp_scenario_id"] not in existing_ids:
                    existing.append(nm)
                    existing_ids.add(nm["adcp_scenario_id"])

    # Update source metadata
    traceability["source"]["commit"] = commit_sha
    traceability["source"]["compiled_at"] = now

    if not dry_run:
        _save_traceability(TRACEABILITY_PATH, traceability)
        print(f"\n  Updated: {TRACEABILITY_PATH.relative_to(PROJECT_ROOT)}")
    else:
        total_new = sum(len(v) for v in all_new_mappings.values())
        print(f"\n  [DRY RUN] Would add {total_new} new mapping(s) to traceability YAML")

    print("\nDone.")


# ---------------------------------------------------------------------------
# Verify mode
# ---------------------------------------------------------------------------


def verify_features(adcp_req_path: Path) -> bool:
    """Check if compiled feature files are up-to-date.

    Returns True if all files are current, False otherwise.
    """
    commit_sha = _get_commit_sha(adcp_req_path)
    traceability = _load_traceability(TRACEABILITY_PATH)

    recorded_commit = traceability.get("source", {}).get("commit")
    if recorded_commit != commit_sha:
        print(f"STALE: traceability records commit {recorded_commit or 'null'}, but adcp-req HEAD is {commit_sha[:10]}")
        return False

    feature_files = _find_feature_files(adcp_req_path)
    if not feature_files:
        print("No feature files found to verify.")
        return True

    stale = []
    missing = []

    for source_path in feature_files:
        output_path = OUTPUT_DIR / source_path.name
        if not output_path.exists():
            missing.append(source_path.name)
            continue

        # Re-compile in memory and compare
        text = source_path.read_text()
        feature = parse_feature_file(text)
        uc_key = _extract_uc_key(source_path.name)
        expected, _new = _render_feature(feature, traceability, uc_key, source_path.name, commit_sha)
        actual = output_path.read_text()
        # Compare ignoring timestamp differences in the generation stamp
        # (first two lines contain the timestamp)
        expected_body = "\n".join(expected.splitlines()[2:])
        actual_body = "\n".join(actual.splitlines()[2:])
        if expected_body != actual_body:
            stale.append(source_path.name)

    if missing:
        print(f"MISSING compiled files: {', '.join(missing)}")
    if stale:
        print(f"STALE compiled files: {', '.join(stale)}")

    if missing or stale:
        print("\nRe-run: python scripts/compile_bdd.py --all")
        return False

    print(f"All {len(feature_files)} compiled feature files are up-to-date.")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile adcp-req BDD features into salesagent test features.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--uc",
        type=str,
        help="Compile a single UC (e.g. UC-005). Matches BR-UC-005-*.feature.",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Compile all feature files.",
    )
    group.add_argument(
        "--verify",
        action="store_true",
        help="Check if compiled files are up-to-date (no writes).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without writing files.",
    )
    parser.add_argument(
        "--adcp-req-path",
        type=Path,
        default=DEFAULT_ADCP_REQ_PATH,
        help=f"Path to adcp-req repository (default: {DEFAULT_ADCP_REQ_PATH}).",
    )

    args = parser.parse_args()

    adcp_req_path = args.adcp_req_path.resolve()
    if not adcp_req_path.exists():
        print(f"ERROR: adcp-req path not found: {adcp_req_path}", file=sys.stderr)
        sys.exit(1)

    if args.verify:
        ok = verify_features(adcp_req_path)
        sys.exit(0 if ok else 1)

    uc_filter = args.uc if hasattr(args, "uc") else None
    compile_features(adcp_req_path, uc_filter, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
