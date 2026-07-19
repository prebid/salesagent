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
OVERLAY_DIR = PROJECT_ROOT / "tests" / "bdd" / "overlays"

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
    # Coerce per-UC None to [] — older YAMLs wrote bare keys for empty UCs,
    # which YAML parses as None and crashes any iteration over them.
    for uc_key, scenarios in list(data["mappings"].items()):
        if scenarios is None:
            data["mappings"][uc_key] = []
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
            if not scenarios:
                # Preserve empty UCs as an explicit empty list — bare key
                # serializes as `null` and breaks the Pydantic schema check.
                output += f"  {uc_key}: []\n"
                continue
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
        # status=new: no tag needed — pytest_runtest_makereport auto-xfails
        # scenarios with missing step definitions at runtime
    else:
        # New scenario — create a mapping entry (no @pending tag needed)
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


# Transport phrases that should be stripped from Given/When steps.
# Transport is injected by pytest_generate_tests, not the feature file.
_TRANSPORT_SUFFIXES = [
    # Exact transport names (UC-011 patterns)
    " via MCP",
    " via A2A",
    " via REST",
    " via <transport>",
    # UC-006 patterns
    " via the MCP tool",
    " via the REST/A2A endpoint",
]


def _transform_step_text(keyword: str, text: str) -> str:
    """Strip transport-specific suffixes from Given/When step text.

    Transport is a test parameter injected by pytest_generate_tests,
    not a domain precondition. Given/When steps that embed transport
    (e.g., "via MCP") cause the step definition to clobber the
    parametrized transport value.
    """
    if keyword not in ("Given", "When"):
        return text
    for suffix in _TRANSPORT_SUFFIXES:
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _strip_transport_from_scenario(scenario: Scenario) -> None:
    """Strip <transport> placeholder from Scenario Outline if fully removed from steps.

    When _transform_step_text removes all transport references from steps:
    1. Strip " via <transport>" from scenario name
    2. Remove `transport` column from Examples tables
    3. If an Examples table has only 1 data row after column removal and no other
       placeholders remain, convert Scenario Outline to plain Scenario
    """
    # Check if <transport> appears in any step text after transformation
    has_transport_in_steps = any("<transport>" in step.text for step in scenario.steps)
    if has_transport_in_steps:
        return  # Still used in steps — don't strip

    # Check if the scenario name uses <transport>
    if "<transport>" not in scenario.name:
        return  # Not a transport-parametrized Outline

    # Strip from scenario name
    scenario.name = re.sub(r"\s*via\s+<transport>\s*", " ", scenario.name).strip()
    # Also clean up trailing parens with just whitespace inside
    scenario.name = re.sub(r"\s+\(", " (", scenario.name)

    # Strip transport column from Examples tables
    for eb in scenario.examples:
        if not eb.rows:
            continue
        # Parse the header row to find transport column index
        header = eb.rows[0]
        cols = [c.strip() for c in header.strip().strip("|").split("|")]
        transport_idx = None
        for i, col in enumerate(cols):
            if col == "transport":
                transport_idx = i
                break
        if transport_idx is None:
            continue
        # Remove transport column from all rows
        new_rows: list[str] = []
        for row in eb.rows:
            parts = [p.strip() for p in row.strip().strip("|").split("|")]
            parts.pop(transport_idx)
            if parts:
                new_rows.append("| " + " | ".join(f"{p:<17}" for p in parts) + " |")
            # If no columns remain, this Examples block is empty
        eb.rows = new_rows

    # If all remaining Examples have only 1 data row and no other placeholders
    # in the scenario, convert to plain Scenario
    remaining_placeholders = set()
    for step in scenario.steps:
        remaining_placeholders.update(re.findall(r"<(\w+)>", step.text))
    remaining_placeholders.discard("transport")

    if not remaining_placeholders and scenario.keyword == "Scenario Outline":
        # Check if all Examples blocks have at most 1 data row (header + 1 row)
        all_trivial = all(len(eb.rows) <= 2 for eb in scenario.examples)
        if all_trivial:
            scenario.keyword = "Scenario"
            scenario.examples = []


def _render_feature(
    feature: Feature,
    traceability: dict,
    uc_key: str,
    feature_filename: str,
    commit_sha: str,
) -> tuple[str, list[dict], set[str]]:
    """Render a transformed feature file.

    Returns (rendered_text, list_of_new_mappings, all_scenario_ids_produced).
    The third element is needed by the prune step in compile_features so it
    can distinguish "renamed/removed" vs "already-mapped" entries.
    """
    new_mappings: list[dict] = []
    all_scenario_ids: set[str] = set()
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
        # Track every scenario_id this compile actually produced (whether new or
        # already mapped). The prune step needs this set, not just the new ones.
        if scenario.contextgit is not None:
            all_scenario_ids.add(scenario.contextgit.id)

        # Transform step text first (strip transport suffixes)
        for step in scenario.steps:
            step.text = _transform_step_text(step.keyword, step.text)

        # Strip <transport> from Scenario Outline if fully removed from steps
        _strip_transport_from_scenario(scenario)

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

    return "\n".join(lines) + "\n", new_mappings, all_scenario_ids


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

    Returns (uc_key, output_text, new_mappings, all_scenario_ids).
    """
    text = source_path.read_text()
    feature = parse_feature_file(text)
    feature_filename = source_path.name
    uc_key = _extract_uc_key(feature_filename)

    output_text, new_mappings, all_scenario_ids = _render_feature(
        feature, traceability, uc_key, feature_filename, commit_sha
    )
    output_text = _apply_scenario_overlays(feature_filename, output_text)

    if not dry_run:
        output_path = OUTPUT_DIR / feature_filename
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output_text)
        print(f"  Compiled: {feature_filename} -> {output_path.relative_to(PROJECT_ROOT)}")
    else:
        print(f"  [DRY RUN] Would compile: {feature_filename}")
        print(f"    Scenarios: {len(feature.scenarios)}")
        print(f"    New mappings: {len(new_mappings)}")

    return uc_key, output_text, new_mappings, all_scenario_ids


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
    # Per-UC: set of scenario ids THIS compile produced + the feature_filenames
    # the compile actually touched. The prune step uses both — only entries
    # whose adcp_feature matches a touched filename are eligible for pruning,
    # so salesagent-side hand-maintained features sharing the same UC key
    # (e.g. BR-UC-002-manual-overrides.feature alongside the adcp-req-sourced
    # BR-UC-002-create-media-buy.feature) are preserved.
    current_ids_per_uc: dict[str, set[str]] = {}
    touched_files_per_uc: dict[str, set[str]] = {}

    for source_path in feature_files:
        uc_key, _output_text, new_mappings, all_scenario_ids = compile_feature(
            source_path, traceability, commit_sha, dry_run=dry_run
        )
        current_ids_per_uc.setdefault(uc_key, set()).update(all_scenario_ids)
        touched_files_per_uc.setdefault(uc_key, set()).add(source_path.name)
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

    # Prune stale entries. For each UC the current compile touched:
    #   - For each entry whose adcp_feature is one of the touched filenames,
    #     KEEP iff its adcp_scenario_id was produced this run; drop otherwise
    #     (renamed/removed/consolidated upstream in adcp-req).
    #   - Entries pointing at adcp_feature filenames NOT touched this run are
    #     left alone (they belong to a sibling feature file owned by salesagent
    #     or to a UC subset the --uc filter excluded).
    # Without pruning, the YAML accumulates phantoms across rederive cycles
    # and the obligation-sync test fails. Without the per-filename scope,
    # salesagent-side companion features in the same UC namespace are wiped.
    pruned_count = 0
    for uc_key, current_ids in current_ids_per_uc.items():
        existing = traceability.get("mappings", {}).get(uc_key, [])
        touched_files = touched_files_per_uc.get(uc_key, set())
        kept: list[dict] = []
        for m in existing:
            if m.get("adcp_feature") in touched_files:
                if m["adcp_scenario_id"] in current_ids:
                    kept.append(m)
                else:
                    pruned_count += 1
            else:
                kept.append(m)  # owned by an untouched feature file — preserve
        traceability.setdefault("mappings", {})[uc_key] = kept

    # Update source metadata
    traceability["source"]["commit"] = commit_sha
    traceability["source"]["compiled_at"] = now

    if not dry_run:
        _save_traceability(TRACEABILITY_PATH, traceability)
        print(f"\n  Updated: {TRACEABILITY_PATH.relative_to(PROJECT_ROOT)}")
        if pruned_count:
            print(f"  Pruned: {pruned_count} stale traceability entry/entries (no longer produced by adcp-req)")
    else:
        total_new = sum(len(v) for v in all_new_mappings.values())
        print(f"\n  [DRY RUN] Would add {total_new} new mapping(s) to traceability YAML")
        if pruned_count:
            print(f"  [DRY RUN] Would prune {pruned_count} stale entry/entries")

    print("\nDone.")


# ---------------------------------------------------------------------------
# Merge mode (Layer 1 — deterministic classifier)
# ---------------------------------------------------------------------------
#
# Join adcp-req TARGET scenarios with salesagent's existing compiled LEGACY
# scenarios on the @T-<id> tag. Classify each pair into a bucket, apply the
# deterministic buckets directly, and emit a manifest of items needing
# Layer 2 semantic merging via claude -p.
#
# Buckets:
#   NO-OP                 — identical step text + tags + Examples. Keep LEGACY
#                           verbatim. If file uses @schema-v<MAJ>.<MIN> scheme
#                           and the version tag is missing, add @schema-v3.1.
#   EXAMPLES-ONLY         — steps + tags identical, Examples table differs.
#                           Replace LEGACY's Examples table with TARGET's.
#   NEEDS-SEMANTIC-MERGE  — steps or tags differ. Leave LEGACY in output,
#                           emit manifest entry for Layer 2 to patch later.
#   NEW-ADD               — id only in TARGET. Render TARGET via the standard
#                           compile transform (contextgit→@T-id tag, etc).
#   LEGACY-DELETE         — id only in LEGACY, no hand-edit marker. Drop.
#   LEGACY-PRESERVE       — id only in LEGACY, has @hand-edited tag or a
#                           "# HAND-EDITED" comment in the preamble. Keep.
#   LOCAL-OVERLAY         — exact scenario id has a deterministic local
#                           capability/spec reconciliation. Render the overlay.


HAND_EDITED_RE = re.compile(r"@hand-edited\b|#\s*HAND-EDITED", re.IGNORECASE)


def _extract_id_from_tags(tags: list[str]) -> str | None:
    """Return the contextgit id embedded as a leading @T-<id> tag.

    compile_bdd.py renders adcp-req's `# @contextgit id=T-...` comment as
    `@T-...` tag on the compiled scenario. That tag is the join key for
    merge mode.
    """
    for t in tags:
        if t.startswith("@T-"):
            return t[1:]  # strip leading @
    return None


def _scenario_id(scenario: Scenario) -> str | None:
    """Get the scenario id from either contextgit (adcp-req side) or @T- tag (compiled side)."""
    if scenario.contextgit is not None:
        return scenario.contextgit.id
    return _extract_id_from_tags(scenario.tags)


def _has_hand_edited_marker(scenario: Scenario) -> bool:
    """A LEGACY-only scenario is preserved (not deleted) iff it carries an
    explicit hand-edit marker — @hand-edited tag OR `# HAND-EDITED` comment
    inside the scenario body."""
    for t in scenario.tags:
        if HAND_EDITED_RE.search(t):
            return True
    for c in scenario.comment_lines:
        if HAND_EDITED_RE.search(c):
            return True
    return False


def _steps_match(a: list[Step], b: list[Step]) -> bool:
    """Byte-identical match of step keyword + text + table rows, in order."""
    if len(a) != len(b):
        return False
    for sa, sb in zip(a, b, strict=True):
        if sa.keyword != sb.keyword or sa.text != sb.text:
            return False
        if sa.table_rows != sb.table_rows:
            return False
    return True


def _tags_match_ignoring_id(a: list[str], b: list[str]) -> bool:
    """Set-equal modulo the @T-<id> join-key tag (which only appears on the
    compiled-side LEGACY scenario, never on the adcp-req TARGET source)."""
    aa = {t for t in a if not t.startswith("@T-")}
    bb = {t for t in b if not t.startswith("@T-")}
    return aa == bb


def _examples_match(a: list[ExamplesBlock], b: list[ExamplesBlock]) -> bool:
    """Byte-identical match of Examples blocks (header + rows), in order."""
    if len(a) != len(b):
        return False
    for ea, eb in zip(a, b, strict=True):
        if ea.header_line.strip() != eb.header_line.strip():
            return False
        if [r.strip() for r in ea.rows] != [r.strip() for r in eb.rows]:
            return False
    return True


def classify_scenario_pair(legacy: Scenario | None, target: Scenario | None) -> str:
    """Bucket a (legacy, target) scenario pair. Exactly one of legacy/target
    may be None; both being None is a caller bug."""
    if target is None and legacy is None:
        raise ValueError("both sides None")
    if legacy is None:
        return "NEW-ADD"
    if target is None:
        return "LEGACY-PRESERVE" if _has_hand_edited_marker(legacy) else "LEGACY-DELETE"
    if _steps_match(legacy.steps, target.steps) and _tags_match_ignoring_id(legacy.tags, target.tags):
        if _examples_match(legacy.examples, target.examples):
            return "NO-OP"
        return "EXAMPLES-ONLY"
    return "NEEDS-SEMANTIC-MERGE"


# --- Per-bucket apply ---------------------------------------------------------


def _apply_no_op(legacy: Scenario) -> Scenario:
    """Keep LEGACY verbatim. Tags are NEVER auto-injected — version tag
    presence is preserved from upstream (TARGET or LEGACY), period. This
    is the rzgb4.1 invariant: nothing in MERGED is invented downstream."""
    return legacy


def _apply_examples_only(legacy: Scenario, target: Scenario) -> Scenario:
    """Keep LEGACY steps/tags; replace Examples table with TARGET's."""
    base = _apply_no_op(legacy)
    return Scenario(
        contextgit=base.contextgit,
        tags=base.tags,
        keyword=base.keyword,
        name=base.name,
        steps=base.steps,
        examples=target.examples,
        comment_lines=base.comment_lines,
    )


# --- Per-scenario render (lighter than _render_feature — used for merge mode) -


def _render_scenario_lines(scenario: Scenario) -> list[str]:
    """Render a single scenario to a list of lines (no trailing blank).

    Designed to emit either LEGACY scenarios verbatim (NO-OP / EXAMPLES-ONLY /
    LEGACY-PRESERVE / NEEDS-SEMANTIC-MERGE buckets) or a TARGET scenario that
    has been pre-transformed (NEW-ADD bucket, after calling the existing
    `_transform_scenario_tags` + `_transform_step_text` + `_strip_transport`
    pipeline)."""
    lines: list[str] = []
    if scenario.tags:
        lines.append("  " + " ".join(scenario.tags))
    lines.append(f"  {scenario.keyword}: {scenario.name}")
    for step in scenario.steps:
        lines.append(f"    {step.keyword} {step.text}")
        for tr in step.table_rows:
            lines.append(f"    {tr.strip()}")
    for cl in scenario.comment_lines:
        lines.append(f"    {cl.strip()}")
    for eb in scenario.examples:
        lines.append("")
        lines.append(f"    {eb.header_line.strip()}")
        for row in eb.rows:
            lines.append(f"      {row.strip()}")
    return lines


def _load_scenario_overlays(feature_filename: str) -> dict[str, Scenario]:
    """Load local compiled-scenario replacements for one generated feature.

    Overlay scenarios use the generated ``@T-...`` scenario id as their join
    key. They are intentionally compiled-form Gherkin: no source
    ``# @contextgit`` metadata or traceability mutation is required because an
    overlay reconciles an existing upstream scenario rather than inventing a
    new contract id.
    """
    overlay_path = OVERLAY_DIR / feature_filename
    if not overlay_path.exists():
        return {}

    overlays: dict[str, Scenario] = {}
    overlay_feature = parse_feature_file(overlay_path.read_text())
    for scenario in overlay_feature.scenarios:
        scenario_id = _scenario_id(scenario)
        if scenario_id is None:
            raise ValueError(f"BDD overlay scenario in {overlay_path} has no @T-... id")
        if scenario_id in overlays:
            raise ValueError(f"BDD overlay {overlay_path} repeats scenario id {scenario_id}")
        overlays[scenario_id] = scenario
    return overlays


def _apply_scenario_overlays(feature_filename: str, output_text: str) -> str:
    """Replace exact generated scenario blocks with local reconciliations.

    This post-render hook is shared by wholesale compilation and verify mode.
    Merge mode consumes the same overlay objects before classification so it
    neither emits a false semantic-merge conflict nor restores stale upstream
    behavior on the next regeneration.
    """
    overlays = _load_scenario_overlays(feature_filename)
    if not overlays:
        return output_text

    lines = output_text.splitlines()
    provenance = f"# Local scenario overlays applied: tests/bdd/overlays/{feature_filename}"
    if provenance not in lines:
        lines.insert(2, provenance)
    starts: list[tuple[str, int]] = []
    for line_index, line in enumerate(lines):
        if not _is_tag_line(line):
            continue
        scenario_id = _extract_id_from_tags(_parse_tags(line))
        if scenario_id is not None:
            starts.append((scenario_id, line_index))

    ranges: dict[str, tuple[int, int]] = {}
    for position, (scenario_id, start) in enumerate(starts):
        if scenario_id in ranges:
            raise ValueError(f"Compiled feature {feature_filename} repeats scenario id {scenario_id}")
        end = starts[position + 1][1] if position + 1 < len(starts) else len(lines)
        ranges[scenario_id] = (start, end)

    missing = sorted(set(overlays) - set(ranges))
    if missing:
        raise ValueError(f"BDD overlay {feature_filename} targets missing generated scenarios: {missing}")

    replacements = sorted(overlays.items(), key=lambda item: ranges[item[0]][0], reverse=True)
    for scenario_id, scenario in replacements:
        start, end = ranges[scenario_id]
        lines[start:end] = [*_render_scenario_lines(scenario), ""]

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Binding ground truth (which scenarios actually have step-defs in salesagent)
# ---------------------------------------------------------------------------
#
# Empirical truth from pytest-bdd baseline collection (see
# phase5-snapshot/baseline-2026-06-01/bdd.json):
#   - Only 9 UCs have a test_<uc>_*.py driver that loads the generated feature.
#     Scenarios in unwired UCs cannot have bindings (pytest-bdd never sees
#     them), so v3.1 TARGET wins unconditionally.
#   - Within wired UCs, only scenarios whose baseline outcome was 'passed'
#     have working bindings. The rest auto-xfailed at runtime
#     (StepDefinitionNotFoundError) — no binding to preserve.
#
# Together these reduce the "needs semantic merge" set from 661 to ~106
# scenarios (only wired UC + bound scenario). The rest take TARGET
# mechanically via the NEW-ADD render path.
WIRED_UCS = frozenset(
    {
        "UC-002",
        "UC-003",
        "UC-004",
        "UC-005",
        "UC-006",
        "UC-010",
        "UC-011",
        "UC-019",
        "UC-026",
    }
)


def load_bound_scenarios(baseline_bdd_json_path: Path) -> set[str]:
    """Read baseline bdd.json, return set of T-UC-NNN-... scenario_ids where
    at least one parametrized test instance had outcome='passed'. These are
    the scenarios with working step-def bindings — the only ones for which
    a true semantic merge protects binding stability."""
    import json
    import re

    t_id = re.compile(r"^T-UC-[\w\-.]+$")
    by_sid: dict[str, set[str]] = {}
    try:
        data = json.loads(baseline_bdd_json_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[load_bound_scenarios] cannot read {baseline_bdd_json_path}: {exc!r}", file=sys.stderr)
        return set()
    for t in data.get("tests", []):
        if "__scenario__" not in t.get("keywords", []):
            continue
        sid = next((k for k in t["keywords"] if t_id.match(k)), None)
        if not sid:
            continue
        by_sid.setdefault(sid, set()).add(t.get("outcome", ""))
    return {sid for sid, outs in by_sid.items() if "passed" in outs}


def merge_feature(
    target_source_path: Path,
    legacy_compiled_path: Path | None,
    traceability: dict,
    commit_sha: str,
    *,
    lockfile_root: Path | None = None,
    adcp_spec_pin: str | None = None,
    bound_scenarios: set[str] | None = None,
) -> tuple[str, str, list[dict], set[str], list[dict], dict[str, int]]:
    """Merge one UC: adcp-req TARGET source ⨝ salesagent LEGACY compiled.

    Returns (uc_key, output_text, manifest_entries, all_scenario_ids,
             new_mappings, bucket_counts).
    manifest_entries lists NEEDS-SEMANTIC-MERGE scenarios for Layer 2;
    bucket_counts is per-classifier-bucket count for the manifest summary.

    bound_scenarios: when provided, scenarios outside this set (OR in UCs
    not in WIRED_UCS) are classified TARGET-WINS instead of
    NEEDS-SEMANTIC-MERGE — applied mechanically via the NEW-ADD render path
    since there's no binding to preserve.
    """
    target_feature = parse_feature_file(target_source_path.read_text())
    feature_filename = target_source_path.name
    uc_key = _extract_uc_key(feature_filename)
    scenario_overlays = _load_scenario_overlays(feature_filename)

    # Index TARGET by id (skip scenarios without id — orphan source error)
    target_by_id: dict[str, Scenario] = {}
    for scen in target_feature.scenarios:
        sid = _scenario_id(scen)
        if sid is not None:
            target_by_id[sid] = scen

    # Index LEGACY by id (if any)
    legacy_feature: Feature | None = None
    legacy_by_id: dict[str, Scenario] = {}
    if legacy_compiled_path is not None and legacy_compiled_path.exists():
        legacy_feature = parse_feature_file(legacy_compiled_path.read_text())
        for scen in legacy_feature.scenarios:
            sid = _scenario_id(scen)
            if sid is not None:
                legacy_by_id[sid] = scen

    # Lockfile consultation (rzgb4.4). Optional: if lockfile_root is None or
    # the lockfile module isn't importable, fall back to plain classification.
    lockfile = None
    _compute_cache_key = None
    if lockfile_root is not None and adcp_spec_pin is not None:
        try:
            import sys as _sys

            phase5_dir = (lockfile_root / "scripts" / "phase5").resolve()
            if str(phase5_dir) not in _sys.path:
                _sys.path.insert(0, str(phase5_dir))
            from lockfile import Lockfile  # type: ignore[import-not-found]
            from lockfile import compute_cache_key as _cck

            lockfile = Lockfile(uc_key, lockfile_root)
            _compute_cache_key = _cck
        except (ImportError, ValueError) as exc:
            print(
                f"[merge_feature] lockfile disabled for {uc_key}: {exc!r}",
                file=sys.stderr,
            )

    # Backgrounds extracted for cache-key composition.
    legacy_bg_text = "\n".join(legacy_feature.background_lines) if legacy_feature is not None else ""
    target_bg_text = "\n".join(target_feature.background_lines)

    # --- Classify and apply ---
    manifest_entries: list[dict] = []
    all_scenario_ids: set[str] = set()
    new_mappings: list[dict] = []
    bucket_counts: dict[str, int] = {}
    merged_scenarios: list[Scenario] = []
    # Set True when Background should come from TARGET (not LEGACY) — used
    # when UC is unwired (no bindings to preserve) and bg_changed.
    use_target_background = False

    # --- Background as a merge unit (rzgb4.4) ---
    # If LEGACY and TARGET disagree on Background, emit a __background__
    # manifest entry (or check lockfile cache hit) so Layer 2 can merge it.
    # Otherwise, the existing Background-from-LEGACY render path is correct.
    bg_changed = (
        legacy_feature is not None
        and legacy_feature.background_lines != target_feature.background_lines
        and (legacy_feature.background_lines or target_feature.background_lines)
    )
    if bg_changed:
        bg_legacy_gherkin = "\n".join(legacy_feature.background_lines)
        bg_target_gherkin = "\n".join(target_feature.background_lines)
        bg_cache_key = None
        bg_hit = False
        if lockfile is not None and _compute_cache_key is not None:
            bg_cache_key = _compute_cache_key(
                target_gherkin=bg_target_gherkin,
                legacy_gherkin=bg_legacy_gherkin,
                background_legacy="",
                background_target="",
                binding_annotations=[],
                adcp_spec_sha=adcp_spec_pin,
            )
            if lockfile.matches("__background__", bg_cache_key):
                bg_hit = True
        if bg_hit:
            bucket_counts["RESOLVED-FROM-LOCKFILE"] = bucket_counts.get("RESOLVED-FROM-LOCKFILE", 0) + 1
        elif uc_key not in WIRED_UCS:
            # UC has no test driver — no Background-step binding to preserve.
            bucket_counts["TARGET-WINS"] = bucket_counts.get("TARGET-WINS", 0) + 1
            use_target_background = True
        else:
            bucket_counts["NEEDS-SEMANTIC-MERGE"] = bucket_counts.get("NEEDS-SEMANTIC-MERGE", 0) + 1
            manifest_entries.append(
                {
                    "scenario_id": "__background__",
                    "uc_key": uc_key,
                    "legacy_gherkin": bg_legacy_gherkin,
                    "target_gherkin": bg_target_gherkin,
                    "legacy_background": "",
                    "target_background": "",
                    "legacy_file": str(legacy_compiled_path) if legacy_compiled_path else None,
                    "target_file": str(target_source_path),
                    "cache_key": list(bg_cache_key) if bg_cache_key is not None else None,
                }
            )

    # Pass 1: scenarios that appear in TARGET (deterministic order)
    for scen in target_feature.scenarios:
        sid = _scenario_id(scen)
        if sid is None:
            # Source scenario without an id — skip (operator error in adcp-req)
            continue
        all_scenario_ids.add(sid)
        if sid in scenario_overlays:
            bucket_counts["LOCAL-OVERLAY"] = bucket_counts.get("LOCAL-OVERLAY", 0) + 1
            merged_scenarios.append(scenario_overlays[sid])
            continue
        legacy_scen = legacy_by_id.get(sid)
        bucket = classify_scenario_pair(legacy_scen, scen)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        if bucket == "NO-OP":
            merged_scenarios.append(_apply_no_op(legacy_scen))
        elif bucket == "EXAMPLES-ONLY":
            merged_scenarios.append(_apply_examples_only(legacy_scen, scen))
        elif bucket == "NEW-ADD":
            # Transform TARGET via existing compile pipeline (contextgit→@T-id,
            # transport strip, etc).
            output_tags, new_mapping = _transform_scenario_tags(scen, traceability, uc_key, feature_filename)
            if new_mapping is not None:
                new_mappings.append(new_mapping)
            for step in scen.steps:
                step.text = _transform_step_text(step.keyword, step.text)
            _strip_transport_from_scenario(scen)
            merged_scenarios.append(
                Scenario(
                    contextgit=scen.contextgit,
                    tags=output_tags,
                    keyword=scen.keyword,
                    name=scen.name,
                    steps=scen.steps,
                    examples=scen.examples,
                    comment_lines=scen.comment_lines,
                )
            )
        elif bucket == "NEEDS-SEMANTIC-MERGE":
            # Demote to TARGET-WINS when pytest-bdd has no working binding to
            # preserve (UC has no test driver OR scenario auto-xfails on
            # baseline). Render TARGET via the standard compile pipeline —
            # same path NEW-ADD uses — since legacy phrasing has nothing to
            # protect.
            # UC-level gate is ALWAYS active: if the UC has no
            # test_<uc>_*.py driver in salesagent (i.e., not in WIRED_UCS),
            # pytest-bdd never collects its scenarios — there is no step-def
            # binding to preserve, regardless of what the gherkin says.
            # Demote to TARGET-WINS (mechanical render, no LLM).
            #
            # Scenario-level gate is OPTIONAL: when --bound-scenarios-from is
            # provided, narrow within a wired UC to only the scenarios
            # pytest-bdd actually bound. WARNING — known false-positive prone:
            # baseline bdd.json's "xfailed" outcomes can be harness-not-wired
            # XFails (e.g. "No harness wired for None") rather than
            # StepDefinitionNotFoundError. Only enable when bdd.json is
            # known clean.
            no_binding = uc_key not in WIRED_UCS or (bound_scenarios is not None and sid not in bound_scenarios)
            if no_binding:
                bucket_counts[bucket] = bucket_counts.get(bucket, 0) - 1
                bucket_counts["TARGET-WINS"] = bucket_counts.get("TARGET-WINS", 0) + 1
                output_tags, new_mapping = _transform_scenario_tags(scen, traceability, uc_key, feature_filename)
                if new_mapping is not None:
                    new_mappings.append(new_mapping)
                for step in scen.steps:
                    step.text = _transform_step_text(step.keyword, step.text)
                _strip_transport_from_scenario(scen)
                merged_scenarios.append(
                    Scenario(
                        contextgit=scen.contextgit,
                        tags=output_tags,
                        keyword=scen.keyword,
                        name=scen.name,
                        steps=scen.steps,
                        examples=scen.examples,
                        comment_lines=scen.comment_lines,
                    )
                )
                continue

            legacy_gherkin = "\n".join(_render_scenario_lines(legacy_scen))
            target_gherkin = "\n".join(_render_scenario_lines(scen))
            cache_key = None
            lockfile_hit = False
            if lockfile is not None and _compute_cache_key is not None:
                cache_key = _compute_cache_key(
                    target_gherkin=target_gherkin,
                    legacy_gherkin=legacy_gherkin,
                    background_legacy=legacy_bg_text,
                    background_target=target_bg_text,
                    binding_annotations=[],
                    adcp_spec_sha=adcp_spec_pin,
                )
                if lockfile.matches(sid, cache_key):
                    lockfile_hit = True
            if lockfile_hit:
                # RESOLVED-FROM-LOCKFILE: replace bucket count, splice
                # merged_gherkin via the renderer when feasible. For now we
                # mark the scenario as resolved and keep LEGACY in the output
                # (Layer 3 / rzgb4.8 will render from lockfile directly).
                bucket_counts[bucket] = bucket_counts.get(bucket, 0) - 1
                bucket_counts["RESOLVED-FROM-LOCKFILE"] = bucket_counts.get("RESOLVED-FROM-LOCKFILE", 0) + 1
                merged_scenarios.append(legacy_scen)
            else:
                # Defer to Layer 2: keep LEGACY in output for now, emit manifest entry
                merged_scenarios.append(legacy_scen)
                manifest_entries.append(
                    {
                        "scenario_id": sid,
                        "uc_key": uc_key,
                        "legacy_gherkin": legacy_gherkin,
                        "target_gherkin": target_gherkin,
                        "legacy_background": legacy_bg_text,
                        "target_background": target_bg_text,
                        "legacy_file": str(legacy_compiled_path) if legacy_compiled_path else None,
                        "target_file": str(target_source_path),
                        "cache_key": list(cache_key) if cache_key is not None else None,
                    }
                )

    # Pass 2: LEGACY-only scenarios (id in legacy but not in target)
    for sid, legacy_scen in legacy_by_id.items():
        if sid in target_by_id:
            continue
        bucket = classify_scenario_pair(legacy_scen, None)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        if bucket == "LEGACY-PRESERVE":
            merged_scenarios.append(legacy_scen)
            all_scenario_ids.add(sid)
        # LEGACY-DELETE: omit entirely

    # --- Render the merged file ---
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    out_lines: list[str] = []
    out_lines.append(f"# Generated from adcp-req @ {commit_sha} on {now} (merge mode)")
    out_lines.append("# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge")
    out_lines.append("")

    # Feature header: take from TARGET (v3.1 description / postconditions
    # supersede). Background: take from LEGACY (preserves binding stability).
    if target_feature.feature_tags:
        out_lines.append(" ".join(target_feature.feature_tags))
    out_lines.append(target_feature.feature_line)
    for dl in target_feature.description_lines:
        out_lines.append(dl.rstrip())
    if use_target_background and target_feature.background_lines:
        # Unwired UC + Background diff → TARGET wins (no bindings to keep).
        for bl in target_feature.background_lines:
            out_lines.append(bl.rstrip())
        out_lines.append("")
    elif legacy_feature is not None and legacy_feature.background_lines:
        for bl in legacy_feature.background_lines:
            out_lines.append(bl.rstrip())
        out_lines.append("")
    elif target_feature.background_lines:
        for bl in target_feature.background_lines:
            out_lines.append(bl.rstrip())
        out_lines.append("")

    for scen in merged_scenarios:
        out_lines.extend(_render_scenario_lines(scen))
        out_lines.append("")

    output_text = _apply_scenario_overlays(feature_filename, "\n".join(out_lines) + "\n")

    # Also pass through new mappings so the caller can update traceability
    # (mirrors compile_feature's contract for the prune step).
    return uc_key, output_text, manifest_entries, all_scenario_ids, new_mappings, bucket_counts


def merge_features(
    adcp_req_path: Path,
    uc_filter: str | None = None,
    *,
    dry_run: bool = False,
    manifest_path: Path | None = None,
    lockfile_root: Path | None = None,
    adcp_spec_pin: str | None = None,
    bound_scenarios: set[str] | None = None,
) -> None:
    """Top-level merge orchestrator. Replaces wholesale-overwrite for routine
    Phase 5 runs. The wholesale `compile_features` path is retained only for
    the verify-mode comparison (`--verify` still works against last compile)."""
    feature_files = _find_feature_files(adcp_req_path, uc_filter)
    if not feature_files:
        print("No feature files matched.", file=sys.stderr)
        return

    commit_sha = _get_commit_sha(adcp_req_path)
    traceability = _load_traceability(TRACEABILITY_PATH)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Merging {len(feature_files)} feature file(s) from adcp-req @ {commit_sha[:10]} (merge mode)")
    if dry_run:
        print("[DRY RUN MODE — no files written]")
    print()

    manifest: dict = {
        "schema_version": 2,
        "adcp_req_sha": commit_sha,
        "adcp_spec_pin": adcp_spec_pin,
        "compiled_at": now,
        "per_uc": {},
    }
    all_new_mappings: dict[str, list[dict]] = {}
    current_ids_per_uc: dict[str, set[str]] = {}
    touched_files_per_uc: dict[str, set[str]] = {}

    for source_path in feature_files:
        legacy_compiled_path = OUTPUT_DIR / source_path.name
        uc_key, output_text, manifest_entries, all_scenario_ids, new_mappings, bucket_counts = merge_feature(
            source_path,
            legacy_compiled_path,
            traceability,
            commit_sha,
            lockfile_root=lockfile_root,
            adcp_spec_pin=adcp_spec_pin,
            bound_scenarios=bound_scenarios,
        )
        current_ids_per_uc.setdefault(uc_key, set()).update(all_scenario_ids)
        touched_files_per_uc.setdefault(uc_key, set()).add(source_path.name)
        if new_mappings:
            all_new_mappings.setdefault(uc_key, []).extend(new_mappings)

        uc_bucket = manifest["per_uc"].setdefault(
            uc_key,
            {
                "buckets": {},
                "needs_semantic_merge": [],
            },
        )
        for b, n in bucket_counts.items():
            uc_bucket["buckets"][b] = uc_bucket["buckets"].get(b, 0) + n
        uc_bucket["needs_semantic_merge"].extend(manifest_entries)

        if not dry_run:
            output_path = OUTPUT_DIR / source_path.name
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            output_path.write_text(output_text)
        summary = " ".join(f"{k}={v}" for k, v in sorted(bucket_counts.items()))
        verb = "[DRY] would merge" if dry_run else "Merged"
        print(f"  {verb}: {source_path.name}  [{summary}]")

    # Traceability update + prune (mirrors compile_features semantics)
    if all_new_mappings:
        for uc_key, new_maps in all_new_mappings.items():
            existing = traceability["mappings"].setdefault(uc_key, [])
            existing_ids = {m["adcp_scenario_id"] for m in existing}
            for nm in new_maps:
                if nm["adcp_scenario_id"] not in existing_ids:
                    existing.append(nm)
                    existing_ids.add(nm["adcp_scenario_id"])

    pruned_count = 0
    for uc_key, current_ids in current_ids_per_uc.items():
        existing = traceability.get("mappings", {}).get(uc_key, [])
        touched_files = touched_files_per_uc.get(uc_key, set())
        kept: list[dict] = []
        for m in existing:
            if m.get("adcp_feature") in touched_files:
                if m["adcp_scenario_id"] in current_ids:
                    kept.append(m)
                else:
                    pruned_count += 1
            else:
                kept.append(m)
        traceability.setdefault("mappings", {})[uc_key] = kept

    traceability["source"]["commit"] = commit_sha
    traceability["source"]["compiled_at"] = now

    if not dry_run:
        _save_traceability(TRACEABILITY_PATH, traceability)
        print(f"\n  Updated: {TRACEABILITY_PATH.relative_to(PROJECT_ROOT)}")
        if pruned_count:
            print(f"  Pruned: {pruned_count} stale traceability entry/entries")

    # Write manifest
    if manifest_path is None:
        manifest_path = PROJECT_ROOT / ".merge-manifest.json"
    if not dry_run:
        import json

        manifest_path.write_text(json.dumps(manifest, indent=2))
        total_nsm = sum(len(uc["needs_semantic_merge"]) for uc in manifest["per_uc"].values())
        try:
            display = manifest_path.relative_to(PROJECT_ROOT)
        except ValueError:
            display = manifest_path
        print(f"  Manifest: {display} ({total_nsm} NEEDS-SEMANTIC-MERGE)")
    else:
        total_nsm = sum(len(uc["needs_semantic_merge"]) for uc in manifest["per_uc"].values())
        print(f"  [DRY] Would write manifest ({total_nsm} NEEDS-SEMANTIC-MERGE)")

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
        expected = _apply_scenario_overlays(source_path.name, expected)
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
        "--merge",
        action="store_true",
        help=(
            "Use scenario-by-scenario merge mode instead of wholesale overwrite. "
            "Joins adcp-req TARGET with existing salesagent LEGACY on @T-<id> tag; "
            "applies NO-OP / EXAMPLES-ONLY / NEW-ADD / LEGACY-DELETE / "
            "LEGACY-PRESERVE directly; emits .merge-manifest.json listing "
            "NEEDS-SEMANTIC-MERGE scenarios for Layer 2 (claude -p driver)."
        ),
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=None,
        help="Where to write .merge-manifest.json (default: <salesagent>/.merge-manifest.json).",
    )
    parser.add_argument(
        "--adcp-req-path",
        type=Path,
        default=DEFAULT_ADCP_REQ_PATH,
        help=f"Path to adcp-req repository (default: {DEFAULT_ADCP_REQ_PATH}).",
    )
    parser.add_argument(
        "--lockfile-root",
        type=Path,
        default=None,
        help=(
            "Path to adcp-req repository for lockfile lookup "
            "(defaults to --adcp-req-path when --merge is used). "
            "When set, NEEDS-SEMANTIC-MERGE scenarios are checked against "
            "phase5-lockfile/UC-NNN.yaml first; cache hits become "
            "RESOLVED-FROM-LOCKFILE and skip the manifest."
        ),
    )
    parser.add_argument(
        "--adcp-spec-pin",
        type=str,
        default=None,
        help=(
            "adcp spec sha pin (e.g. 04f59d2d5 for v3.1). Required component "
            "of the lockfile cache key. Defaults to AdCP v3.1 pin if "
            "--merge is used and --lockfile-root is set."
        ),
    )
    parser.add_argument(
        "--bound-scenarios-from",
        type=Path,
        default=None,
        help=(
            "Path to a baseline bdd.json (e.g., "
            "adcp-req/phase5-snapshot/baseline-2026-06-01/bdd.json). When set, "
            "scenarios outside wired UCs OR not in the baseline-passed set are "
            "demoted from NEEDS-SEMANTIC-MERGE to TARGET-WINS (taken from v3.1 "
            "mechanically — no LLM merge needed)."
        ),
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
    if args.merge:
        lockfile_root = args.lockfile_root if args.lockfile_root else adcp_req_path
        adcp_spec_pin = args.adcp_spec_pin or "04f59d2d5"
        bound = load_bound_scenarios(args.bound_scenarios_from) if args.bound_scenarios_from is not None else None
        if bound is not None:
            print(
                f"[merge] bound-scenario gate active: {len(bound)} bound IDs loaded; non-bound scenarios → TARGET-WINS",
                file=sys.stderr,
            )
        merge_features(
            adcp_req_path,
            uc_filter,
            dry_run=args.dry_run,
            manifest_path=args.manifest_path,
            lockfile_root=lockfile_root,
            adcp_spec_pin=adcp_spec_pin,
            bound_scenarios=bound,
        )
    else:
        compile_features(adcp_req_path, uc_filter, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
