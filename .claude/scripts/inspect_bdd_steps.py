"""BDD step assertion completeness inspector.

Two-pass pipeline:
  Pass 1 (Sonnet): Triage — is there a high chance this function doesn't implement its claim?
  Pass 2 (Opus): Deep trace — what should the correct assertion be?

Usage:
  python .claude/scripts/inspect_bdd_steps.py [--pass1-only] [--steps-dir PATH]
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

STEP_DECORATOR_NAMES = {"given", "when", "then"}

# ── Data classes ────────────────────────────────────────────────────


@dataclass
class BddStepInfo:
    """Metadata for a single BDD step function."""

    file_path: str
    line_number: int
    step_type: str  # "given", "when", or "then"
    step_text: str
    function_name: str
    source_text: str


@dataclass
class TriageResult:
    """Result from Pass 1 triage."""

    step: BddStepInfo
    verdict: str  # "PASS" or "FLAG"
    reason: str


@dataclass
class DeepTraceResult:
    """Result from Pass 2 deep trace."""

    step: BddStepInfo
    claims: str
    actually_tests: str
    recommendation: str
    severity: str  # "COSMETIC", "WEAK", "MISSING"


# ── Pass 0: AST extraction ─────────────────────────────────────────


def _extract_step_text(decorator: ast.Call) -> str | None:
    """Extract the step text string from a @given/@when/@then decorator call."""
    if not decorator.args:
        return None
    arg = decorator.args[0]
    # Form A: @then("plain string")
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    # Form B: @then(parsers.parse("string with {params}"))
    if isinstance(arg, ast.Call):
        if (
            isinstance(arg.func, ast.Attribute)
            and arg.func.attr == "parse"
            and arg.args
            and isinstance(arg.args[0], ast.Constant)
        ):
            return arg.args[0].value
    # Form C: @then(parsers.re(r"regex pattern"))
    if isinstance(arg, ast.Call):
        if (
            isinstance(arg.func, ast.Attribute)
            and arg.func.attr == "re"
            and arg.args
            and isinstance(arg.args[0], ast.Constant)
        ):
            return arg.args[0].value
    return None


def _get_decorator_step_type(decorator: ast.expr) -> str | None:
    """Get the step type (given/when/then) from a decorator node."""
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if isinstance(func, ast.Name) and func.id in STEP_DECORATOR_NAMES:
        return func.id
    if isinstance(func, ast.Attribute) and func.attr in STEP_DECORATOR_NAMES:
        return func.attr
    return None


def extract_bdd_steps(directory: Path) -> list[BddStepInfo]:
    """Extract all BDD step functions from Python files in directory.

    Walks all .py files recursively, finds functions decorated with
    @given, @when, or @then (from pytest_bdd), and extracts their
    step text and source code.
    """
    results: list[BddStepInfo] = []

    for py_file in sorted(directory.rglob("*.py")):
        try:
            source = py_file.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        source_lines = source.splitlines()

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                step_type = _get_decorator_step_type(decorator)
                if step_type is None:
                    continue
                step_text = _extract_step_text(decorator)  # type: ignore[arg-type]
                if step_text is None:
                    continue
                # Extract full function source (def line through end)
                body = "\n".join(source_lines[node.lineno - 1 : node.end_lineno])
                results.append(
                    BddStepInfo(
                        file_path=str(py_file),
                        line_number=node.lineno,
                        step_type=step_type,
                        step_text=step_text,
                        function_name=node.name,
                        source_text=body,
                    )
                )
                break  # only use first matching decorator per function

    return results


# ── Pass 1: Triage (Sonnet) ─────────────────────────────────────────


TRIAGE_PROMPT_TEMPLATE = """You are reviewing BDD step definitions for assertion completeness.

For each step below, answer FLAG or PASS:
- FLAG: High chance the function does NOT implement what the step text claims.
  Examples: body is just `pass`, assertions only check truthiness/existence
  but the step text promises content-specific validation (e.g., "should indicate
  which parameters" but only asserts `assert msg`).
- PASS: The function plausibly implements what the step text claims.
  A function that checks error existence for "the operation should fail" is PASS.

Respond with EXACTLY one line per step in format: <number>|<FLAG or PASS>|<reason>

{steps_block}"""


def _format_steps_for_triage(steps: list[BddStepInfo]) -> str:
    """Format steps into a numbered block for the triage prompt."""
    parts = []
    for i, step in enumerate(steps, 1):
        parts.append(f'--- Step {i} ---\nStep text: "{step.step_text}"\nFunction:\n{step.source_text}\n')
    return "\n".join(parts)


def _run_claude(prompt: str, model: str = "sonnet") -> str:
    """Run claude -p and return the text output."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    result = subprocess.run(
        ["claude", "-p", prompt, "--model", model, "--output-format", "text"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    return result.stdout.strip()


def run_pass1_triage(steps: list[BddStepInfo], batch_size: int = 10) -> list[TriageResult]:
    """Run Pass 1 triage on steps, batched for efficiency."""
    results: list[TriageResult] = []

    for batch_start in range(0, len(steps), batch_size):
        batch = steps[batch_start : batch_start + batch_size]
        prompt = TRIAGE_PROMPT_TEMPLATE.format(steps_block=_format_steps_for_triage(batch))

        output = _run_claude(prompt, model="sonnet")

        # Parse responses
        for line in output.splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = line.split("|", 2)
            if len(parts) < 2:
                continue
            try:
                idx = int(parts[0].strip()) - 1
            except (ValueError, IndexError):
                continue
            if 0 <= idx < len(batch):
                verdict = parts[1].strip().upper()
                reason = parts[2].strip() if len(parts) > 2 else ""
                if verdict in ("FLAG", "PASS"):
                    results.append(
                        TriageResult(
                            step=batch[idx],
                            verdict=verdict,
                            reason=reason,
                        )
                    )

    return results


# ── Pass 2: Deep trace (Opus) ───────────────────────────────────────


DEEP_TRACE_PROMPT_TEMPLATE = """You are an expert reviewing a BDD Then step definition that was flagged
as potentially NOT implementing what its step text claims.

Your job is to make an ARCHITECTURAL JUDGMENT: what should this function
actually verify? You are NOT writing code — you are deciding what the
correct semantic assertion should be.

## Flagged Function

Step text: "{step_text}"
Function name: {func_name}
File: {file_path}:{line_number}

```python
{source_text}
```

## Triage Reason
{triage_reason}

## Production Context
{context}

## Instructions

Analyze and respond with EXACTLY this format (no markdown, no extra text):

CLAIMS: <what the step text says the function should verify>
ACTUALLY_TESTS: <what the function body actually tests>
SEVERITY: <COSMETIC|WEAK|MISSING>
RECOMMENDATION: <what the correct assertion should be — describe the semantic check, not code>

Severity guide:
- COSMETIC: naming/wording mismatch but the assertion is functionally correct
- WEAK: assertion checks something related but is significantly weaker than what's claimed
- MISSING: assertion doesn't check what's claimed at all (pass body, pure existence check for content claim)"""


def _collect_context_for_step(step: BddStepInfo) -> str:
    """Collect production schema/model context for a flagged step."""
    context_parts: list[str] = []

    # Read the full file the step lives in (gives access to helpers)
    try:
        full_source = Path(step.file_path).read_text()
        # Only include imports and helper functions, not the full file
        lines = full_source.splitlines()
        imports = [l for l in lines if l.startswith(("import ", "from "))]
        if imports:
            context_parts.append("## Imports in step file\n" + "\n".join(imports))
    except OSError:
        pass

    # Check for common schemas referenced
    project_root = Path(__file__).resolve().parents[2]
    schema_keywords = ["AdCPError", "ListCreativeFormatsResponse", "ValidationError"]
    for kw in schema_keywords:
        if kw in step.source_text:
            # Try to find the schema definition
            for schema_file in [
                project_root / "src" / "core" / "exceptions.py",
                project_root / "src" / "core" / "schemas" / "creative.py",
            ]:
                if schema_file.exists():
                    try:
                        schema_source = schema_file.read_text()
                        if kw in schema_source:
                            context_parts.append(
                                f"## {schema_file.name} (contains {kw})\n```python\n{schema_source[:3000]}\n```"
                            )
                    except OSError:
                        pass

    # Check helper functions called by this step
    if "ctx.get(" in step.source_text or "ctx[" in step.source_text:
        context_parts.append(
            "## Context keys used\n"
            "ctx['response'] = ListCreativeFormatsResponse (real production object)\n"
            "ctx['error'] = Exception (real AdCPError or pydantic.ValidationError)"
        )

    return "\n\n".join(context_parts) if context_parts else "No additional context available."


def run_pass2_deep_trace(
    flagged: list[TriageResult],
) -> list[DeepTraceResult]:
    """Run Pass 2 deep trace on flagged steps with Opus."""
    results: list[DeepTraceResult] = []

    for triage in flagged:
        step = triage.step
        context = _collect_context_for_step(step)

        prompt = DEEP_TRACE_PROMPT_TEMPLATE.format(
            step_text=step.step_text,
            func_name=step.function_name,
            file_path=step.file_path,
            line_number=step.line_number,
            source_text=step.source_text,
            triage_reason=triage.reason,
            context=context,
        )

        output = _run_claude(prompt, model="opus")

        # Parse structured response
        claims = ""
        actually_tests = ""
        severity = "WEAK"
        recommendation = ""

        for line in output.splitlines():
            line = line.strip()
            if line.startswith("CLAIMS:"):
                claims = line[7:].strip()
            elif line.startswith("ACTUALLY_TESTS:"):
                actually_tests = line[15:].strip()
            elif line.startswith("SEVERITY:"):
                severity = line[9:].strip()
            elif line.startswith("RECOMMENDATION:"):
                recommendation = line[15:].strip()

        results.append(
            DeepTraceResult(
                step=step,
                claims=claims,
                actually_tests=actually_tests,
                recommendation=recommendation,
                severity=severity,
            )
        )

    return results


# ── Report generation ───────────────────────────────────────────────


def generate_report(
    all_steps: list[BddStepInfo],
    triage_results: list[TriageResult],
    deep_results: list[DeepTraceResult],
    output_path: Path,
) -> None:
    """Generate a markdown report of the inspection results."""
    flagged = [r for r in triage_results if r.verdict == "FLAG"]
    passed = [r for r in triage_results if r.verdict == "PASS"]

    lines = [
        "# BDD Step Assertion Completeness Audit",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Summary",
        "",
        f"- **Steps scanned**: {len(all_steps)}",
        f"- **Then steps analyzed**: {len(triage_results)}",
        f"- **Passed triage**: {len(passed)}",
        f"- **Flagged for deep inspection**: {len(flagged)}",
        f"- **Confirmed issues**: {len(deep_results)}",
        "",
    ]

    if deep_results:
        # Group by severity
        by_severity: dict[str, list[DeepTraceResult]] = {}
        for r in deep_results:
            by_severity.setdefault(r.severity, []).append(r)

        lines.append("## Issues by Severity")
        lines.append("")

        for severity in ["MISSING", "WEAK", "COSMETIC"]:
            items = by_severity.get(severity, [])
            if not items:
                continue
            lines.append(f"### {severity} ({len(items)})")
            lines.append("")
            for r in items:
                rel_path = r.step.file_path
                # Try to make path relative
                try:
                    rel_path = str(Path(r.step.file_path).relative_to(Path.cwd()))
                except ValueError:
                    pass
                lines.extend(
                    [
                        f"#### `{r.step.function_name}` ({rel_path}:{r.step.line_number})",
                        "",
                        f'**Step text**: "{r.step.step_text}"',
                        "",
                        f"**Claims**: {r.claims}",
                        "",
                        f"**Actually tests**: {r.actually_tests}",
                        "",
                        f"**Recommendation**: {r.recommendation}",
                        "",
                    ]
                )

    if flagged:
        lines.append("## All Flagged Steps (Pass 1)")
        lines.append("")
        lines.append("| # | Function | Step Text | Reason |")
        lines.append("|---|----------|-----------|--------|")
        for i, r in enumerate(flagged, 1):
            step_text_short = r.step.step_text[:60] + "..." if len(r.step.step_text) > 60 else r.step.step_text
            lines.append(f"| {i} | `{r.step.function_name}` | {step_text_short} | {r.reason} |")
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))


# ── Main ────────────────────────────────────────────────────────────


def main() -> None:
    """Run the two-pass BDD step inspection pipeline."""
    parser = argparse.ArgumentParser(description="BDD step assertion completeness inspector")
    parser.add_argument(
        "--steps-dir",
        type=Path,
        default=Path("tests/bdd/steps"),
        help="Directory containing BDD step definitions",
    )
    parser.add_argument(
        "--pass1-only",
        action="store_true",
        help="Run only Pass 1 (triage) — skip deep trace",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output report path (default: .claude/reports/bdd-step-audit-<date>.md)",
    )
    parser.add_argument(
        "--then-only",
        action="store_true",
        default=True,
        help="Only inspect Then steps (default: true)",
    )
    args = parser.parse_args()

    # Determine output path
    if args.output is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        args.output = Path(f".claude/reports/bdd-step-audit-{timestamp}.md")

    print(f"Scanning {args.steps_dir} for BDD step functions...")
    all_steps = extract_bdd_steps(args.steps_dir)
    print(f"  Found {len(all_steps)} step functions total")

    # Filter to Then steps for assertion completeness
    if args.then_only:
        target_steps = [s for s in all_steps if s.step_type == "then"]
        print(f"  Filtering to {len(target_steps)} Then steps")
    else:
        target_steps = all_steps

    # Pass 1: Triage
    print("\n=== Pass 1: Triage (Sonnet) ===")
    triage_results = run_pass1_triage(target_steps)
    flagged = [r for r in triage_results if r.verdict == "FLAG"]
    print(f"  {len(flagged)} flagged, {len(triage_results) - len(flagged)} passed")

    # Pass 2: Deep trace (if not pass1-only)
    deep_results: list[DeepTraceResult] = []
    if not args.pass1_only and flagged:
        print(f"\n=== Pass 2: Deep Trace (Opus) — {len(flagged)} functions ===")
        deep_results = run_pass2_deep_trace(flagged)
        for r in deep_results:
            print(f"  [{r.severity}] {r.step.function_name}: {r.recommendation[:80]}")

    # Generate report
    generate_report(all_steps, triage_results, deep_results, args.output)
    print(f"\nReport written to {args.output}")


if __name__ == "__main__":
    main()
