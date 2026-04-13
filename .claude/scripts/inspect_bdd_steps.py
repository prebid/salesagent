"""BDD step assertion completeness inspector.

Three-pass pipeline:
  Pass 0 (AST): Deterministic — catches _pending(), missing assert, pytest.xfail in body
  Pass 1 (Sonnet): Per-step triage with context — FLAG or PASS
  Pass 2 (Opus): Deep trace on flagged steps — architectural judgment

Results are persisted incrementally to a JSONL file after every LLM call.
If the script crashes or is interrupted, re-running resumes from where it left off.

Usage:
  python .claude/scripts/inspect_bdd_steps.py
  python .claude/scripts/inspect_bdd_steps.py --pass1-only
  python .claude/scripts/inspect_bdd_steps.py --pass0-only
  python .claude/scripts/inspect_bdd_steps.py --resume   # resume from last run
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

STEP_DECORATOR_NAMES = {"given", "when", "then"}
PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    """Result from Pass 0 or Pass 1 triage."""

    step: BddStepInfo
    verdict: str  # "PASS" or "FLAG"
    reason: str
    source: str = "pass1"  # "pass0" (deterministic) or "pass1" (sonnet)


@dataclass
class DeepTraceResult:
    """Result from Pass 2 deep trace."""

    step: BddStepInfo
    claims: str
    actually_tests: str
    recommendation: str
    severity: str  # "COSMETIC", "WEAK", "MISSING"


# ── Incremental persistence (JSONL) ──────────────────────────────


class ResultStore:
    """Append-only JSONL store for incremental persistence and resume."""

    def __init__(self, path: Path):
        self.path = path
        self._triage: dict[str, TriageResult] = {}  # key = func_name:line
        self._deep: dict[str, DeepTraceResult] = {}
        if path.exists():
            self._load()

    def _key(self, step: BddStepInfo) -> str:
        # Match by function name only — line numbers may differ between
        # salvaged records (line_number=0) and live AST extraction.
        return step.function_name

    def _load(self) -> None:
        """Load existing results from JSONL."""
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = obj.get("kind")
            step = BddStepInfo(**obj["step"])
            key = self._key(step)
            if kind == "triage":
                self._triage[key] = TriageResult(
                    step=step,
                    verdict=obj["verdict"],
                    reason=obj["reason"],
                    source=obj.get("source", "pass1"),
                )
            elif kind == "deep":
                self._deep[key] = DeepTraceResult(
                    step=step,
                    claims=obj["claims"],
                    actually_tests=obj["actually_tests"],
                    recommendation=obj["recommendation"],
                    severity=obj["severity"],
                )

    def has_triage(self, step: BddStepInfo) -> bool:
        return self._key(step) in self._triage

    def get_triage(self, step: BddStepInfo) -> TriageResult | None:
        return self._triage.get(self._key(step))

    def has_deep(self, step: BddStepInfo) -> bool:
        return self._key(step) in self._deep

    def get_deep(self, step: BddStepInfo) -> DeepTraceResult | None:
        return self._deep.get(self._key(step))

    def save_triage(self, result: TriageResult) -> None:
        key = self._key(result.step)
        self._triage[key] = result
        self._append(
            {
                "kind": "triage",
                "step": result.step.__dict__,
                "verdict": result.verdict,
                "reason": result.reason,
                "source": result.source,
            }
        )

    def save_deep(self, result: DeepTraceResult) -> None:
        key = self._key(result.step)
        self._deep[key] = result
        self._append(
            {
                "kind": "deep",
                "step": result.step.__dict__,
                "claims": result.claims,
                "actually_tests": result.actually_tests,
                "recommendation": result.recommendation,
                "severity": result.severity,
            }
        )

    def _append(self, obj: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps(obj) + "\n")

    def all_triage(self) -> list[TriageResult]:
        return list(self._triage.values())

    def all_deep(self) -> list[DeepTraceResult]:
        return list(self._deep.values())


# ── AST extraction ────────────────────────────────────────────────


def _extract_step_text(decorator: ast.Call) -> str | None:
    """Extract the step text string from a @given/@when/@then decorator call."""
    if not decorator.args:
        return None
    arg = decorator.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.Call):
        if (
            isinstance(arg.func, ast.Attribute)
            and arg.func.attr in ("parse", "re")
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
    """Extract all BDD step functions from Python files in directory."""
    results: list[BddStepInfo] = []
    for py_file in sorted(directory.rglob("*.py")):
        try:
            source = py_file.read_text()
            tree = ast.parse(source, filename=str(py_file))
        except (OSError, UnicodeDecodeError, SyntaxError):
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
                break
    return results


# ── Pass 0: Deterministic AST checks ──────────────────────────────


def _has_assert_or_raise(node: ast.FunctionDef) -> bool:
    """Check if a function body contains any assert or raise statement."""
    for child in ast.walk(node):
        if isinstance(child, (ast.Assert, ast.Raise)):
            return True
    return False


def _has_pending_call(source: str) -> bool:
    """Check if source calls _pending()."""
    return "_pending(" in source


def _has_pytest_xfail(source: str) -> bool:
    """Check if source calls pytest.xfail()."""
    return "pytest.xfail(" in source


def _has_pytest_skip(source: str) -> bool:
    """Check if source calls pytest.skip()."""
    return "pytest.skip(" in source


def _is_pass_only_body(node: ast.FunctionDef) -> bool:
    """Check if function body is just `pass` (possibly with docstring)."""
    stmts = [s for s in node.body if not isinstance(s, ast.Expr) or not isinstance(s.value, ast.Constant)]
    return len(stmts) == 1 and isinstance(stmts[0], ast.Pass)


def run_pass0_deterministic(steps: list[BddStepInfo], steps_dir: Path, store: ResultStore) -> list[TriageResult]:
    """Deterministic AST-based triage. No LLM needed. Persists to store."""
    results: list[TriageResult] = []

    file_trees: dict[str, ast.Module] = {}
    for step in steps:
        if step.file_path not in file_trees:
            try:
                source = Path(step.file_path).read_text()
                file_trees[step.file_path] = ast.parse(source)
            except (OSError, SyntaxError):
                continue

    for step in steps:
        # Skip if already in store
        if store.has_triage(step):
            existing = store.get_triage(step)
            if existing and existing.source == "pass0":
                results.append(existing)
                continue

        tree = file_trees.get(step.file_path)
        if tree is None:
            continue

        func_node = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == step.function_name
                and node.lineno == step.line_number
            ):
                func_node = node
                break
        if func_node is None:
            continue

        result = None
        if _has_pending_call(step.source_text):
            result = TriageResult(
                step=step,
                verdict="FLAG",
                reason="body delegates to _pending() — no-op stub, no assertion",
                source="pass0",
            )
        elif _is_pass_only_body(func_node):
            result = TriageResult(
                step=step, verdict="FLAG", reason="body is just `pass` — no assertion", source="pass0"
            )
        elif _has_pytest_xfail(step.source_text):
            result = TriageResult(
                step=step,
                verdict="FLAG",
                reason="body calls pytest.xfail() — step never asserts, delegates to xfail",
                source="pass0",
            )
        elif _has_pytest_skip(step.source_text):
            result = TriageResult(
                step=step,
                verdict="FLAG",
                reason="body calls pytest.skip() — step never asserts, delegates to skip",
                source="pass0",
            )
        elif not _has_assert_or_raise(func_node):
            result = TriageResult(
                step=step,
                verdict="FLAG",
                reason="no assert or raise in body — function produces no verification",
                source="pass0",
            )

        if result:
            store.save_triage(result)
            results.append(result)

    return results


# ── Pass 1: Per-step Sonnet triage with context ─────────────────────


TRIAGE_PROMPT_TEMPLATE = """You are reviewing a single BDD Then step definition for assertion completeness.

## Project Context

This is a BDD test suite for an AdCP (Ad Context Protocol) platform. Steps test production code
through a harness that manages DB sessions, mocks external adapters, and dispatches across
multiple transports (impl, mcp, a2a, rest, e2e_rest).

Key infrastructure (pre-computed type stubs — the agent MUST read these before implementing fixes):
- .agent-index/factories.pyi — all available test factories
- .agent-index/harness/envs.pyi — domain-specific test environment classes
- .agent-index/harness/transport.pyi — Transport enum and dispatch methods
- .agent-index/errors.pyi — AdCPError hierarchy

Use `ast-grep --pattern '@then($_)' tests/bdd/steps/` to find existing step patterns.

## The Step Under Review

Step text: "{step_text}"
Function name: {func_name}
File: {file_path}:{line_number}

```python
{source_text}
```

## Feature File Context

The step appears in scenarios from:
{feature_context}

## Task

Answer FLAG or PASS:

- **FLAG** if the function does NOT meaningfully implement what the step text claims:
  - Asserts only truthiness/existence (is not None, hasattr) when step text promises VALUE checks
  - Uses getattr(obj, "field", None) on a field that may not exist on the Pydantic model (vacuous)
  - Checks ctx dict keys instead of inspecting the actual response object
  - Has conditional branches that silently pass (if X: pass, if X is None: return)
  - E2E branch weakens the assertion vs non-E2E without documenting why
  - Compares against hardcoded values instead of seeded test data from ctx

- **PASS** if the function plausibly implements what the step text claims:
  - Asserts specific values from the response against expected values
  - Error-existence check for "the operation should fail" steps
  - Value comparison (==, in, >=) against data from ctx or seeded fixtures

Respond with EXACTLY this format (no markdown):
VERDICT: FLAG or PASS
REASON: <one sentence explanation>"""


def _find_feature_context(step: BddStepInfo) -> str:
    """Find which feature file(s) reference this step text."""
    features_dir = PROJECT_ROOT / "tests" / "bdd" / "features"
    if not features_dir.exists():
        return "Feature files not found."

    matches = []
    # Simplify step text for searching (remove parser placeholders)
    search_text = step.step_text
    # Remove {param} placeholders for matching
    import re

    search_parts = re.split(r"\{[^}]+\}", search_text)
    search_key = search_parts[0].strip() if search_parts else search_text[:40]

    if len(search_key) < 5:
        return "Step text too short for feature file matching."

    for feature_file in sorted(features_dir.glob("*.feature")):
        try:
            content = feature_file.read_text()
            if search_key in content:
                # Find the scenario containing this step
                lines = content.splitlines()
                for i, line in enumerate(lines):
                    if search_key in line:
                        # Look backward for the Scenario line
                        for j in range(i, max(i - 20, -1), -1):
                            if lines[j].strip().startswith(("Scenario:", "Scenario Outline:")):
                                matches.append(f"{feature_file.name}:{j + 1} — {lines[j].strip()}")
                                break
                        break
        except OSError:
            continue

    if matches:
        return "\n".join(matches[:5])
    return "No matching feature file scenario found."


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


def run_pass1_triage(steps: list[BddStepInfo], store: ResultStore) -> list[TriageResult]:
    """Run Pass 1 triage — one step per claude -p call. Resumes from store."""
    results: list[TriageResult] = []
    skipped = 0

    for i, step in enumerate(steps):
        # Resume: skip if already in store
        if store.has_triage(step):
            results.append(store.get_triage(step))
            skipped += 1
            continue

        feature_context = _find_feature_context(step)

        prompt = TRIAGE_PROMPT_TEMPLATE.format(
            step_text=step.step_text,
            func_name=step.function_name,
            file_path=step.file_path,
            line_number=step.line_number,
            source_text=step.source_text,
            feature_context=feature_context,
        )

        print(f"  [{i + 1}/{len(steps)}] {step.function_name}...", end="", flush=True)
        output = _run_claude(prompt, model="sonnet")

        verdict = ""
        reason = ""
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("VERDICT:"):
                verdict = line[8:].strip().upper()
            elif line.startswith("REASON:"):
                reason = line[7:].strip()

        if verdict in ("FLAG", "PASS"):
            result = TriageResult(step=step, verdict=verdict, reason=reason, source="pass1")
        else:
            result = TriageResult(
                step=step,
                verdict="FLAG",
                reason=f"Sonnet parse failure (raw: {output[:100]})",
                source="pass1",
            )
            print(" FLAG (parse failure)")

        store.save_triage(result)
        results.append(result)
        if verdict in ("FLAG", "PASS"):
            print(f" {verdict}")

    if skipped:
        print(f"  (resumed: {skipped} already in store)")
    return results


# ── Pass 2: Deep trace (Opus) ───────────────────────────────────────


DEEP_TRACE_PROMPT_TEMPLATE = """You are an expert reviewing a BDD Then step definition that was flagged
as potentially NOT implementing what its step text claims.

Your job is to make an ARCHITECTURAL JUDGMENT: what should this function
actually verify? You are NOT writing code — you are deciding what the
correct semantic assertion should be.

## Project Infrastructure

Pre-computed type stubs for orientation:
- .agent-index/factories.pyi — test factories (TenantFactory, MediaBuyFactory, etc.)
- .agent-index/harness/envs.pyi — harness environments (DeliveryPollEnv, MediaBuyCreateEnv, etc.)
- .agent-index/harness/transport.pyi — Transport enum, call_via() dispatch
- .agent-index/errors.pyi — AdCPError, AdCPNotFoundError, AdCPValidationError, etc.
- .agent-index/schemas/core.pyi — Pydantic models for requests/responses

Use `ast-grep --pattern '@then($_)' tests/bdd/steps/` to find similar step patterns.

## Flagged Function

Step text: "{step_text}"
Function name: {func_name}
File: {file_path}:{line_number}

```python
{source_text}
```

## Triage Source and Reason
Source: {triage_source}
Reason: {triage_reason}

## Feature File Context
{feature_context}

## Production Context
{production_context}

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


def _collect_production_context(step: BddStepInfo) -> str:
    """Collect production schema/model context for a flagged step."""
    context_parts: list[str] = []

    # Imports from step file
    try:
        full_source = Path(step.file_path).read_text()
        lines = full_source.splitlines()
        imports = [l for l in lines if l.startswith(("import ", "from "))]
        if imports:
            context_parts.append("## Imports in step file\n" + "\n".join(imports))
    except OSError:
        pass

    # .agent-index stubs relevant to this step
    agent_index = PROJECT_ROOT / ".agent-index"
    if agent_index.exists():
        stub_files = ["errors.pyi", "schemas/core.pyi"]
        for stub in stub_files:
            stub_path = agent_index / stub
            if stub_path.exists():
                try:
                    content = stub_path.read_text()
                    if len(content) < 5000:
                        context_parts.append(f"## .agent-index/{stub}\n```python\n{content}\n```")
                except OSError:
                    pass

    # Context keys used
    if "ctx.get(" in step.source_text or "ctx[" in step.source_text:
        context_parts.append(
            "## Context keys convention\n"
            "ctx['response'] = production response object (Pydantic model)\n"
            "ctx['error'] = Exception (AdCPError subclass or pydantic.ValidationError)\n"
            "ctx['error_response'] = response object from error path\n"
            "ctx['env'] = harness environment (manages DB, mocks, transport dispatch)"
        )

    return "\n\n".join(context_parts) if context_parts else "No additional context available."


def run_pass2_deep_trace(flagged: list[TriageResult], store: ResultStore) -> list[DeepTraceResult]:
    """Run Pass 2 deep trace on flagged steps with Opus. Resumes from store."""
    results: list[DeepTraceResult] = []
    skipped = 0

    for i, triage in enumerate(flagged):
        step = triage.step

        # Resume: skip if already in store
        if store.has_deep(step):
            results.append(store.get_deep(step))
            skipped += 1
            continue

        feature_context = _find_feature_context(step)
        production_context = _collect_production_context(step)

        prompt = DEEP_TRACE_PROMPT_TEMPLATE.format(
            step_text=step.step_text,
            func_name=step.function_name,
            file_path=step.file_path,
            line_number=step.line_number,
            source_text=step.source_text,
            triage_source=triage.source,
            triage_reason=triage.reason,
            feature_context=feature_context,
            production_context=production_context,
        )

        print(f"  [{i + 1}/{len(flagged)}] {step.function_name}...", end="", flush=True)
        output = _run_claude(prompt, model="opus")

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
                sev = line[9:].strip().upper()
                if sev in ("COSMETIC", "WEAK", "MISSING"):
                    severity = sev
            elif line.startswith("RECOMMENDATION:"):
                recommendation = line[15:].strip()

        result = DeepTraceResult(
            step=step,
            claims=claims,
            actually_tests=actually_tests,
            recommendation=recommendation,
            severity=severity,
        )
        store.save_deep(result)
        results.append(result)
        print(f" [{severity}]")

    if skipped:
        print(f"  (resumed: {skipped} already in store)")
    return results


# ── Report generation ───────────────────────────────────────────────


def generate_report(
    all_steps: list[BddStepInfo],
    pass0_results: list[TriageResult],
    pass1_results: list[TriageResult],
    deep_results: list[DeepTraceResult],
    output_path: Path,
) -> None:
    """Generate a markdown report of the inspection results."""
    all_triage = pass0_results + pass1_results
    flagged = [r for r in all_triage if r.verdict == "FLAG"]
    passed = [r for r in all_triage if r.verdict == "PASS"]
    pass0_flags = [r for r in pass0_results if r.verdict == "FLAG"]
    pass1_flags = [r for r in pass1_results if r.verdict == "FLAG"]

    lines = [
        "# BDD Step Assertion Completeness Audit",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Summary",
        "",
        f"- **Steps scanned**: {len(all_steps)}",
        f"- **Then steps analyzed**: {len(all_triage)}",
        f"- **Pass 0 (deterministic) flags**: {len(pass0_flags)}",
        f"- **Pass 1 (Sonnet) flags**: {len(pass1_flags)}",
        f"- **Total flagged**: {len(flagged)}",
        f"- **Passed**: {len(passed)}",
        f"- **Deep trace results**: {len(deep_results)}",
        "",
    ]

    # Pass 0 deterministic findings
    if pass0_flags:
        lines.append("## Pass 0: Deterministic Findings")
        lines.append("")
        lines.append("These are automatically detected by AST analysis — no LLM judgment needed.")
        lines.append("")
        lines.append("| # | Function | File | Reason |")
        lines.append("|---|----------|------|--------|")
        for i, r in enumerate(pass0_flags, 1):
            try:
                rel_path = str(Path(r.step.file_path).relative_to(Path.cwd()))
            except ValueError:
                rel_path = r.step.file_path
            lines.append(f"| {i} | `{r.step.function_name}` | {rel_path}:{r.step.line_number} | {r.reason} |")
        lines.append("")

    # Deep trace results (Pass 2)
    if deep_results:
        by_severity: dict[str, list[DeepTraceResult]] = {}
        for r in deep_results:
            by_severity.setdefault(r.severity, []).append(r)

        lines.append("## Issues by Severity (Pass 2 Deep Trace)")
        lines.append("")

        for severity in ["MISSING", "WEAK", "COSMETIC"]:
            items = by_severity.get(severity, [])
            if not items:
                continue
            lines.append(f"### {severity} ({len(items)})")
            lines.append("")
            for r in items:
                try:
                    rel_path = str(Path(r.step.file_path).relative_to(Path.cwd()))
                except ValueError:
                    rel_path = r.step.file_path
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

    # All flagged (both passes)
    if flagged:
        lines.append("## All Flagged Steps")
        lines.append("")
        lines.append("| # | Source | Function | Step Text | Reason |")
        lines.append("|---|--------|----------|-----------|--------|")
        for i, r in enumerate(flagged, 1):
            step_text_short = r.step.step_text[:50] + "..." if len(r.step.step_text) > 50 else r.step.step_text
            lines.append(f"| {i} | {r.source} | `{r.step.function_name}` | {step_text_short} | {r.reason[:80]} |")
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))


# ── Main ────────────────────────────────────────────────────────────


def main() -> None:
    """Run the three-pass BDD step inspection pipeline."""
    parser = argparse.ArgumentParser(description="BDD step assertion completeness inspector")
    parser.add_argument("--steps-dir", type=Path, default=Path("tests/bdd/steps"))
    parser.add_argument("--pass0-only", action="store_true", help="Deterministic checks only")
    parser.add_argument("--pass1-only", action="store_true", help="Pass 0 + Pass 1 (skip deep trace)")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--then-only", action="store_true", default=True)
    parser.add_argument(
        "--store",
        type=Path,
        default=Path(".claude/reports/bdd-step-audit.jsonl"),
        help="JSONL store for incremental persistence and resume",
    )
    args = parser.parse_args()

    if args.output is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        args.output = Path(f".claude/reports/bdd-step-audit-{timestamp}.md")

    store = ResultStore(args.store)
    existing = len(store.all_triage())
    if existing:
        print(f"Resuming: {existing} results loaded from {args.store}")

    print(f"Scanning {args.steps_dir} for BDD step functions...")
    all_steps = extract_bdd_steps(args.steps_dir)
    print(f"  Found {len(all_steps)} step functions total")

    if args.then_only:
        target_steps = [s for s in all_steps if s.step_type == "then"]
        print(f"  Filtering to {len(target_steps)} Then steps")
    else:
        target_steps = all_steps

    # Pass 0: Deterministic
    print("\n=== Pass 0: Deterministic AST checks ===")
    pass0_results = run_pass0_deterministic(target_steps, args.steps_dir, store)
    pass0_flagged = [r for r in pass0_results if r.verdict == "FLAG"]
    print(f"  {len(pass0_flagged)} flagged deterministically")

    # Steps that passed Pass 0 go to Pass 1
    pass0_flagged_names = {r.step.function_name for r in pass0_flagged}
    pass1_candidates = [s for s in target_steps if s.function_name not in pass0_flagged_names]

    pass1_results: list[TriageResult] = []
    deep_results: list[DeepTraceResult] = []

    if not args.pass0_only and pass1_candidates:
        print(f"\n=== Pass 1: Sonnet triage — {len(pass1_candidates)} steps (1 per call) ===")
        pass1_results = run_pass1_triage(pass1_candidates, store)
        pass1_flagged = [r for r in pass1_results if r.verdict == "FLAG"]
        pass1_passed = [r for r in pass1_results if r.verdict == "PASS"]
        print(f"  {len(pass1_flagged)} flagged, {len(pass1_passed)} passed")

        # Pass 2: Deep trace on ALL flags (pass0 + pass1)
        all_flagged = pass0_results + [r for r in pass1_results if r.verdict == "FLAG"]

        if not args.pass1_only and all_flagged:
            print(f"\n=== Pass 2: Deep Trace (Opus) — {len(all_flagged)} functions ===")
            deep_results = run_pass2_deep_trace(all_flagged, store)
            for r in deep_results:
                print(f"  [{r.severity}] {r.step.function_name}: {r.recommendation[:80]}")
    elif args.pass0_only:
        print("\n  --pass0-only: skipping Pass 1 and Pass 2")

    generate_report(all_steps, pass0_results, pass1_results, deep_results, args.output)
    print(f"\nReport written to {args.output}")
    print(f"Store: {args.store} ({len(store.all_triage())} triage + {len(store.all_deep())} deep trace)")


if __name__ == "__main__":
    main()
