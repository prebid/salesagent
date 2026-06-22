#!/usr/bin/env python3
"""Assemble context bundle for BDD Then-step strengthening tasks.

Pipeline:
  Stage 1 (deterministic): collect verbatim artifacts from disk —
    the Gherkin scenario text, referenced business rules from adcp-req,
    the current Then step implementation, production code signatures
    via .agent-index/ stubs, and ast-grep patterns.
  Stage 2 (LLM compression): `claude -p` trims long-form prose while
    preserving the load-bearing contract verbatim: the Gherkin scenario,
    business rule text, code pointers, and agent instructions.

Usage:
  # Assemble context for a specific Then step:
  python3 .claude/scripts/assemble_test_context.py \
      --step "uc004_delivery.py:1112 then_has_metrics" \
      --output .claude/context-bundles/

  # Assemble context for all weak steps in a domain file:
  python3 .claude/scripts/assemble_test_context.py \
      --file tests/bdd/steps/domain/uc004_delivery.py \
      --allowlist count-only \
      --output .claude/context-bundles/

  # Stage 1 only (no LLM compression):
  python3 .claude/scripts/assemble_test_context.py \
      --step "uc004_delivery.py:1112 then_has_metrics" --raw
"""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FEATURES_DIR = PROJECT_ROOT / "tests" / "bdd" / "features"
STEPS_DIR = PROJECT_ROOT / "tests" / "bdd" / "steps"
HARNESS_DIR = PROJECT_ROOT / "tests" / "harness"
AGENT_INDEX_DIR = PROJECT_ROOT / ".agent-index"
ADCP_REQ_DIR = Path("/Users/konst/projects/adcp-req")
SRC_DIR = PROJECT_ROOT / "src"

AGENT_INSTRUCTIONS = """## Agent Instructions (non-negotiable)

- Read .agent-index/ stubs FIRST before diving into source. They give you
  the full type signature inventory without hallucination risk.
- Use `ast-grep` — never `grep`/`find` — for structural code queries:
    ast-grep --pattern 'def $NAME($$$):' tests/bdd/steps/
    ast-grep --pattern 'class $NAME:' tests/harness/
    ast-grep --pattern '@then($$$)' tests/bdd/steps/domain/
- The Then step must assert on ACTUAL VALUES from the production response,
  not just existence (hasattr) or count (len > 0). Compare against the
  business rule's stated invariant.
- If production does not exhibit the asserted behavior, add
  `pytest.mark.xfail(reason="<business rule text>")` — NEVER weaken the
  assertion or check only count/existence.
- Real PostgreSQL via integration_db fixture. No DB mocks.
- Run the specific test after fixing: `uv run python -m pytest tests/bdd/<file>.py -k "<scenario>" --tb=short`
- Do NOT mention AI assistants in code or commits.
"""


@dataclass
class StepContext:
    """Collected context for one weak Then step."""

    step_file: str  # e.g., "tests/bdd/steps/domain/uc004_delivery.py"
    step_line: int  # line number
    step_name: str  # e.g., "then_has_metrics"
    weakness: str  # "count-only", "assert-hasattr", "getattr-existence"
    step_source: str = ""  # the actual function body
    gherkin_text: str = ""  # the Gherkin Then line(s) using this step
    scenario_text: str = ""  # full scenario(s) that use this step
    feature_file: str = ""  # which feature file
    business_rules: dict[str, str] = field(default_factory=dict)  # BR-RULE-XXX → text from adcp-req
    production_signatures: list[str] = field(default_factory=list)  # from .agent-index/
    harness_env: str = ""  # which harness env this UC uses
    ast_grep_patterns: list[str] = field(default_factory=list)


def extract_step_source(filepath: Path, line: int, name: str) -> str:
    """Extract the full function body starting at `line`."""
    lines = filepath.read_text().splitlines()
    start = line - 1  # 0-indexed
    if start >= len(lines):
        return f"# Line {line} out of range (file has {len(lines)} lines)"

    # Find the function def
    buf = []
    in_func = False
    indent = None
    for i in range(start, len(lines)):
        l = lines[i]
        if not in_func:
            if l.strip().startswith("def ") or l.strip().startswith("async def "):
                in_func = True
                indent = len(l) - len(l.lstrip())
                buf.append(l)
            elif l.strip().startswith("@"):
                buf.append(l)  # decorators
        elif l.strip() == "":
            buf.append(l)
        elif len(l) - len(l.lstrip()) <= indent and l.strip() and not l.strip().startswith("#"):
            break
        else:
            buf.append(l)
    return "\n".join(buf)


def find_gherkin_usage(step_name: str) -> list[tuple[str, str, str]]:
    """Find Gherkin Then lines that match this step's @then decorator pattern.

    Returns [(feature_file, scenario_name, then_line), ...]
    """
    # Get the @then decorator pattern from the step file
    results = []
    for feature_path in FEATURES_DIR.glob("*.feature"):
        text = feature_path.read_text()
        current_scenario = ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("Scenario:"):
                current_scenario = stripped
            elif stripped.startswith("Then ") or stripped.startswith("And "):
                # Heuristic: match step name tokens against Gherkin text
                name_tokens = step_name.replace("then_", "").split("_")
                gherkin_lower = stripped.lower()
                if all(tok in gherkin_lower for tok in name_tokens if len(tok) > 2):
                    results.append((feature_path.name, current_scenario, stripped))
    return results


def extract_business_rules(scenario_text: str, feature_text: str) -> dict[str, str]:
    """Extract BR-RULE-XXX tags and look up their text from adcp-req."""
    rules = {}
    # Find BR-RULE tags in scenario and surrounding context
    for m in re.finditer(r"BR-RULE-(\d+)", feature_text):
        rule_id = f"BR-RULE-{m.group(1)}"
        if rule_id in rules:
            continue
        # Look up in adcp-req
        rule_text = _lookup_business_rule(m.group(1))
        if rule_text:
            rules[rule_id] = rule_text
    return rules


def _lookup_business_rule(rule_num: str) -> str:
    """Look up a business rule from the adcp-req repository."""
    if not ADCP_REQ_DIR.exists():
        return f"(adcp-req not found at {ADCP_REQ_DIR})"

    # Search for the rule in requirements files
    try:
        result = subprocess.run(
            ["grep", "-r", f"BR-RULE-{rule_num}", str(ADCP_REQ_DIR / "requirements")],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.stdout.strip():
            # Return first few lines of context
            lines = result.stdout.strip().splitlines()[:5]
            return "\n".join(lines)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return f"(BR-RULE-{rule_num}: not found in adcp-req)"


def collect_agent_index_signatures(uc_prefix: str) -> list[str]:
    """Collect relevant .agent-index/ stub signatures for this UC."""
    sigs = []
    stub_files = {
        "UC-002": ["api/impl.pyi", "schemas/core.pyi"],
        "UC-003": ["api/impl.pyi", "schemas/core.pyi"],
        "UC-004": ["api/impl.pyi", "schemas/delivery.pyi"],
        "UC-005": ["api/impl.pyi", "schemas/creative.pyi"],
        "UC-006": ["api/impl.pyi", "schemas/creative.pyi"],
        "UC-011": ["api/impl.pyi", "schemas/core.pyi"],
        "UC-026": ["api/impl.pyi", "schemas/core.pyi"],
    }
    for stub_file in stub_files.get(uc_prefix, ["api/impl.pyi"]):
        stub_path = AGENT_INDEX_DIR / stub_file
        if stub_path.exists():
            sigs.append(f"# {stub_file}\n{stub_path.read_text()[:2000]}")
    return sigs


def detect_uc_from_path(step_file: str) -> str:
    """Detect UC prefix from step file path."""
    if "uc002" in step_file:
        return "UC-002"
    if "uc003" in step_file:
        return "UC-003"
    if "uc004" in step_file:
        return "UC-004"
    if "uc005" in step_file:
        return "UC-005"
    if "uc006" in step_file:
        return "UC-006"
    if "uc011" in step_file:
        return "UC-011"
    if "uc019" in step_file:
        return "UC-019"
    if "uc026" in step_file:
        return "UC-026"
    if "get_products" in step_file:
        return "UC-GET-PRODUCTS"
    return "GENERIC"


def detect_harness_env(uc_prefix: str) -> str:
    """Return the harness env class name for this UC."""
    mapping = {
        "UC-002": "MediaBuyCreateEnv / MediaBuyAccountEnv",
        "UC-003": "MediaBuyUpdateIntegrationEnv",
        "UC-004": "DeliveryPollEnv / WebhookEnv / CircuitBreakerEnv",
        "UC-005": "CreativeFormatsEnv",
        "UC-006": "CreativeSyncEnv",
        "UC-011": "AccountSyncEnv / AdminAccountEnv",
        "UC-026": "MediaBuyDualEnv",
        "UC-GET-PRODUCTS": "ProductEnv",
        "GENERIC": "(check conftest.py _harness_env)",
    }
    return mapping.get(uc_prefix, "(unknown)")


def assemble_step_context(step_ref: str, weakness: str = "unknown") -> StepContext:
    """Assemble full context for one weak Then step.

    step_ref format: "uc004_delivery.py:1112 then_has_metrics"
    """
    parts = step_ref.split()
    file_line = parts[0]
    step_name = parts[1] if len(parts) > 1 else "unknown"

    file_part, line_str = file_line.rsplit(":", 1)
    line = int(line_str)

    # Resolve full path
    step_file = None
    for candidate in STEPS_DIR.rglob(f"*{file_part}"):
        step_file = candidate
        break
    if step_file is None:
        step_file = PROJECT_ROOT / "tests" / "bdd" / "steps" / file_part

    uc_prefix = detect_uc_from_path(str(step_file))

    ctx = StepContext(
        step_file=str(step_file),
        step_line=line,
        step_name=step_name,
        weakness=weakness,
    )

    # 1. Extract step source
    if step_file.exists():
        ctx.step_source = extract_step_source(step_file, line, step_name)

    # 2. Find Gherkin usage
    usages = find_gherkin_usage(step_name)
    if usages:
        ctx.feature_file = usages[0][0]
        ctx.gherkin_text = "\n".join(f"  {u[2]}" for u in usages[:5])
        ctx.scenario_text = "\n".join(f"  [{u[0]}] {u[1]}" for u in usages[:5])

    # 3. Business rules from feature file
    if ctx.feature_file:
        feature_path = FEATURES_DIR / ctx.feature_file
        if feature_path.exists():
            ctx.business_rules = extract_business_rules(ctx.scenario_text, feature_path.read_text())

    # 4. Production signatures from .agent-index/
    ctx.production_signatures = collect_agent_index_signatures(uc_prefix)

    # 5. Harness env
    ctx.harness_env = detect_harness_env(uc_prefix)

    # 6. ast-grep patterns
    ctx.ast_grep_patterns = [
        f"ast-grep --pattern 'def {step_name}($$$):' tests/bdd/steps/",
        f"ast-grep --pattern '@then($$$)' {step_file}",
        "ast-grep --pattern 'def _*impl($$$):' src/core/tools/",
    ]

    return ctx


def format_bundle(ctx: StepContext) -> str:
    """Format a StepContext into a markdown context bundle."""
    sections = []
    sections.append(f"# Context Bundle: {ctx.step_name}")
    sections.append(f"**Weakness:** {ctx.weakness}")
    sections.append(f"**File:** `{ctx.step_file}:{ctx.step_line}`")
    sections.append(f"**Harness env:** {ctx.harness_env}")
    sections.append("")

    sections.append(AGENT_INSTRUCTIONS)

    sections.append("## Current Step Implementation (WEAK — must strengthen)")
    sections.append(f"```python\n{ctx.step_source}\n```")
    sections.append("")

    if ctx.gherkin_text:
        sections.append("## Gherkin Usage (Then steps referencing this function)")
        sections.append(f"```gherkin\n{ctx.gherkin_text}\n```")
        sections.append("")

    if ctx.scenario_text:
        sections.append("## Scenarios")
        sections.append(ctx.scenario_text)
        sections.append("")

    if ctx.business_rules:
        sections.append("## Business Rules (from adcp-req)")
        for rule_id, text in ctx.business_rules.items():
            sections.append(f"### {rule_id}")
            sections.append(text)
            sections.append("")

    if ctx.production_signatures:
        sections.append("## Production Code Signatures (.agent-index/)")
        sections.append("Read these stubs to understand the response shape:")
        for sig in ctx.production_signatures[:2]:  # limit to avoid huge bundles
            sections.append(f"```python\n{sig[:1500]}\n```")
        sections.append("")

    if ctx.ast_grep_patterns:
        sections.append("## ast-grep Patterns (use these to find code)")
        for p in ctx.ast_grep_patterns:
            sections.append(f"  {p}")
        sections.append("")

    return "\n".join(sections)


def compress_with_claude(raw_bundle: str, output_path: Path) -> str:
    """Stage 2: compress via claude -p, preserving load-bearing sections."""
    prompt = f"""You are compressing a test context bundle. Keep these sections VERBATIM:
- Agent Instructions
- Current Step Implementation
- Gherkin Usage
- Business Rules
- ast-grep Patterns

Summarize (do not remove) these sections to key facts only:
- Production Code Signatures (keep function names + return types, drop bodies)
- Scenarios (keep scenario names, drop details)

Output the compressed bundle as markdown.

--- RAW BUNDLE ---
{raw_bundle}
"""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            compressed = result.stdout.strip()
            output_path.write_text(compressed)
            return compressed
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    # Fallback: write raw
    output_path.write_text(raw_bundle)
    return raw_bundle


def main():
    parser = argparse.ArgumentParser(description="Assemble BDD Then-step context bundles")
    parser.add_argument("--step", help='Step ref: "uc004_delivery.py:1112 then_has_metrics"')
    parser.add_argument("--file", help="Step file path — assemble all weak steps in this file")
    parser.add_argument(
        "--allowlist",
        choices=["count-only", "hasattr", "getattr", "all"],
        default="all",
        help="Which allowlist to process",
    )
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / ".claude" / "context-bundles", help="Output directory for bundles"
    )
    parser.add_argument("--raw", action="store_true", help="Stage 1 only (no LLM compression)")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    if args.step:
        ctx = assemble_step_context(args.step)
        raw = format_bundle(ctx)
        if args.raw:
            print(raw)
        else:
            out_file = args.output / f"{ctx.step_name}.md"
            compress_with_claude(raw, out_file)
            print(f"Wrote: {out_file}")
    elif args.file:
        # TODO: parse allowlist from test_architecture_bdd_assertion_strength.py
        # and filter to steps in the specified file
        print(f"TODO: batch mode for {args.file}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
