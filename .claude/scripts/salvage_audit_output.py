"""Salvage partial inspect_bdd_steps output into the JSONL store.

Parses the raw terminal output from a crashed/interrupted run and writes
results into the JSONL store so the next run can resume from where it stopped.

Usage:
    python3 .claude/scripts/salvage_audit_output.py \\
        .claude/reports/bdd-step-audit-raw-output.txt \\
        --store .claude/reports/bdd-step-audit.jsonl \\
        --step-index .claude/reports/bdd-step-index.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO, TypedDict

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from bdd_audit_common import StepRecord, strip_ansi  # noqa: E402


class ParsedRow(TypedDict, total=False):
    """Fixed-shape row from ``parse_raw_output`` (pass-1 and/or pass-2 fields)."""

    index: int
    func_name: str
    verdict: str
    severity: str


class ParsedOutput(TypedDict):
    """Structured salvage parse of a partial inspect_bdd_steps run."""

    pass1: list[ParsedRow]
    pass2: list[ParsedRow]
    pass1_total: int
    pass2_total: int
    pass2_crashed_at: int


def parse_raw_output(raw_path: Path) -> ParsedOutput:
    """Parse the raw terminal output into structured results.

    Strips ANSI color codes before matching so colored captures parse the
    same as plain text. Asserts ``len(pass1) == pass1_total`` when the
    totals line is present (inconsistent captures raise ``ValueError``).
    """
    text = strip_ansi(raw_path.read_text(encoding="utf-8", errors="replace"))

    pass1_results: list[ParsedRow] = []
    pass2_results: list[ParsedRow] = []

    # Parse Pass 1 lines: [N/370] then_xxx... FLAG/PASS
    for m in re.finditer(r"\[(\d+)/\d+\]\s+(then_\w+)\.\.\.\s+(FLAG|PASS)", text):
        idx, func_name, verdict = int(m.group(1)), m.group(2), m.group(3)
        pass1_results.append({"index": idx, "func_name": func_name, "verdict": verdict})

    # Parse Pass 2 lines: [N/127] then_xxx... [SEVERITY]
    for m in re.finditer(r"\[(\d+)/\d+\]\s+(then_\w+)\.\.\.\s+\[(WEAK|MISSING|COSMETIC)\]", text):
        idx, func_name, severity = int(m.group(1)), m.group(2), m.group(3)
        pass2_results.append({"index": idx, "func_name": func_name, "severity": severity})

    # Extract totals
    pass1_total = 0
    pass2_total = 0
    m1 = re.search(r"(\d+) flagged, (\d+) passed", text)
    if m1:
        pass1_total = int(m1.group(1)) + int(m1.group(2))
    m2 = re.search(r"Pass 2: Deep Trace.*?(\d+) functions", text)
    if m2:
        pass2_total = int(m2.group(1))

    if pass1_total and len(pass1_results) != pass1_total:
        raise ValueError(
            f"pass1 row count {len(pass1_results)} != pass1_total {pass1_total} "
            "(ANSI-stripped parse inconsistent — check capture)"
        )

    return {
        "pass1": pass1_results,
        "pass2": pass2_results,
        "pass1_total": pass1_total,
        "pass2_total": pass2_total,
        "pass2_crashed_at": len(pass2_results) + 1 if pass2_results else 0,
    }


def _normalize_step(step: dict[str, Any], fallback_name: str = "") -> StepRecord:
    """Coerce a JSON step blob into the fixed six-key StepRecord shape."""
    name = step.get("function_name") or fallback_name
    return {
        "file_path": str(step.get("file_path") or "unknown"),
        "line_number": int(step.get("line_number") or 0),
        "step_type": str(step.get("step_type") or "then"),
        "step_text": str(step.get("step_text") or ""),
        "function_name": str(name or ""),
        "source_text": str(step.get("source_text") or ""),
    }


def _store_key(kind: str, step: StepRecord, fallback_name: str = "") -> str:
    """Kind-scoped dedupe key so triage and deep traces do not collide."""
    name = step["function_name"] or fallback_name
    return f"{kind}:{name}:{step['line_number']}"


def _placeholder_step(func_name: str) -> StepRecord:
    """Minimal step record when the step index is unavailable."""
    return _normalize_step({}, func_name)


def iter_jsonl_records(path: Path) -> Iterator[dict[str, Any]]:
    """Yield parsed JSON objects from a JSONL file (skip blanks / bad lines)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _load_existing_keys(store_path: Path) -> set[str]:
    """Load kind-scoped keys already present in the JSONL store."""
    existing_keys: set[str] = set()
    for obj in iter_jsonl_records(store_path):
        step = obj.get("step", {})
        if not isinstance(step, dict):
            step = {}
        existing_keys.add(_store_key(str(obj.get("kind")), _normalize_step(step)))
    return existing_keys


def _append_if_new(
    f: TextIO,
    kind: str,
    r: ParsedRow,
    step_lookup: dict[str, StepRecord],
    existing_keys: set[str],
    record: dict[str, Any],
) -> bool:
    """Write ``record`` once per kind-scoped key. Returns True if written."""
    func_name = r["func_name"]
    step_info = step_lookup.get(func_name, _placeholder_step(func_name))
    key = _store_key(kind, step_info, func_name)
    if key in existing_keys:
        return False
    payload = {**record, "kind": kind, "step": step_info}
    f.write(json.dumps(payload) + "\n")
    existing_keys.add(key)
    return True


def write_to_store(parsed: ParsedOutput, store_path: Path, step_index_path: Path | None) -> None:
    """Write parsed results to the JSONL store.

    Since we only have function names (not full BddStepInfo), we write
    lightweight records that the resume logic can match against.
    """
    existing_keys = _load_existing_keys(store_path)

    # We need the step index to get file/line info.
    # If not available, write with func_name only (partial records).
    step_lookup: dict[str, StepRecord] = {}
    if step_index_path is not None:
        for obj in iter_jsonl_records(step_index_path):
            step = obj.get("step", {})
            if not isinstance(step, dict):
                continue
            name = step.get("function_name")
            if name:
                step_lookup[str(name)] = _normalize_step(step)

    new_triage = 0
    new_deep = 0

    store_path.parent.mkdir(parents=True, exist_ok=True)
    with open(store_path, "a") as f:
        for r in parsed["pass1"]:
            if _append_if_new(
                f,
                "triage",
                r,
                step_lookup,
                existing_keys,
                {
                    "verdict": r["verdict"],
                    "reason": "salvaged from raw output (original reason lost)",
                    "source": "pass1",
                },
            ):
                new_triage += 1

        for r in parsed["pass2"]:
            if _append_if_new(
                f,
                "deep",
                r,
                step_lookup,
                existing_keys,
                {
                    "claims": "salvaged — see full report for details",
                    "actually_tests": "salvaged — see full report for details",
                    "recommendation": "salvaged — see full report for details",
                    "severity": r["severity"],
                },
            ):
                new_deep += 1

    print(f"Salvaged to {store_path}:")
    print(f"  Pass 1: {new_triage} new triage results (of {len(parsed['pass1'])} total)")
    print(f"  Pass 2: {new_deep} new deep trace results (of {len(parsed['pass2'])} total)")
    print(f"  Pass 2 crashed at: {parsed['pass2_crashed_at']}/{parsed['pass2_total']}")
    print(f"  Remaining: {parsed['pass2_total'] - len(parsed['pass2'])} Opus calls needed")


def main():
    parser = argparse.ArgumentParser(description="Salvage partial audit output into JSONL store")
    parser.add_argument("raw_output", type=Path, help="Raw terminal output file")
    parser.add_argument("--store", type=Path, default=Path(".claude/reports/bdd-step-audit.jsonl"))
    parser.add_argument(
        "--step-index",
        type=Path,
        default=None,
        help="JSONL step index for file/line normalization (defaults to --store if omitted)",
    )
    args = parser.parse_args()

    if not args.raw_output.exists():
        print(f"ERROR: {args.raw_output} not found")
        return

    parsed = parse_raw_output(args.raw_output)

    print(f"Parsed from {args.raw_output}:")
    print(f"  Pass 1: {len(parsed['pass1'])} results")
    print(f"  Pass 2: {len(parsed['pass2'])} results (of {parsed['pass2_total']} expected)")
    print()

    step_index = args.step_index if args.step_index is not None else args.store
    write_to_store(parsed, args.store, step_index)


if __name__ == "__main__":
    main()
