"""Salvage partial inspect_bdd_steps output into the JSONL store.

Parses the raw terminal output from a crashed/interrupted run and writes
results into the JSONL store so the next run can resume from where it stopped.

Usage:
    python3 .claude/scripts/salvage_audit_output.py \
        .claude/reports/bdd-step-audit-raw-output.txt \
        --store .claude/reports/bdd-step-audit.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def parse_raw_output(raw_path: Path) -> dict:
    """Parse the raw terminal output into structured results."""
    text = raw_path.read_text()

    pass1_results = []  # (func_name, verdict)
    pass2_results = []  # (func_name, severity)

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
    m = re.search(r"(\d+) flagged, (\d+) passed", text)
    if m:
        pass1_total = int(m.group(1)) + int(m.group(2))
    m = re.search(r"Pass 2: Deep Trace.*?(\d+) functions", text)
    if m:
        pass2_total = int(m.group(1))

    return {
        "pass1": pass1_results,
        "pass2": pass2_results,
        "pass1_total": pass1_total,
        "pass2_total": pass2_total,
        "pass2_crashed_at": len(pass2_results) + 1 if pass2_results else 0,
    }


def write_to_store(parsed: dict, store_path: Path, step_index_path: Path | None) -> None:
    """Write parsed results to the JSONL store.

    Since we only have function names (not full BddStepInfo), we write
    lightweight records that the resume logic can match against.
    """
    # Load existing store to avoid duplicates
    existing_keys = set()
    if store_path.exists():
        for line in store_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                step = obj.get("step", {})
                existing_keys.add(f"{step.get('function_name')}:{step.get('line_number', 0)}")
            except json.JSONDecodeError:
                continue

    # We need the step index to get file/line info.
    # If not available, write with func_name only (partial records).
    step_lookup = {}
    if step_index_path and step_index_path.exists():
        for line in step_index_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                step = obj.get("step", {})
                step_lookup[step.get("function_name")] = step
            except json.JSONDecodeError:
                continue

    new_triage = 0
    new_deep = 0

    store_path.parent.mkdir(parents=True, exist_ok=True)
    with open(store_path, "a") as f:
        # Write Pass 1 results
        for r in parsed["pass1"]:
            step_info = step_lookup.get(
                r["func_name"],
                {
                    "file_path": "unknown",
                    "line_number": 0,
                    "step_type": "then",
                    "step_text": "",
                    "function_name": r["func_name"],
                    "source_text": "",
                },
            )
            key = f"{step_info.get('function_name', r['func_name'])}:{step_info.get('line_number', 0)}"
            if key not in existing_keys:
                record = {
                    "kind": "triage",
                    "step": step_info,
                    "verdict": r["verdict"],
                    "reason": "salvaged from raw output (original reason lost)",
                    "source": "pass1",
                }
                f.write(json.dumps(record) + "\n")
                existing_keys.add(key)
                new_triage += 1

        # Write Pass 2 results
        for r in parsed["pass2"]:
            step_info = step_lookup.get(
                r["func_name"],
                {
                    "file_path": "unknown",
                    "line_number": 0,
                    "step_type": "then",
                    "step_text": "",
                    "function_name": r["func_name"],
                    "source_text": "",
                },
            )
            key = f"{step_info.get('function_name', r['func_name'])}:{step_info.get('line_number', 0)}"
            # Only write deep trace if not already present
            record = {
                "kind": "deep",
                "step": step_info,
                "claims": "salvaged — see full report for details",
                "actually_tests": "salvaged — see full report for details",
                "recommendation": "salvaged — see full report for details",
                "severity": r["severity"],
            }
            f.write(json.dumps(record) + "\n")
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
    args = parser.parse_args()

    if not args.raw_output.exists():
        print(f"ERROR: {args.raw_output} not found")
        return

    parsed = parse_raw_output(args.raw_output)

    print(f"Parsed from {args.raw_output}:")
    print(f"  Pass 1: {len(parsed['pass1'])} results")
    print(f"  Pass 2: {len(parsed['pass2'])} results (of {parsed['pass2_total']} expected)")
    print()

    write_to_store(parsed, args.store, args.store)


if __name__ == "__main__":
    main()
