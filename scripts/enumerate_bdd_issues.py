#!/usr/bin/env python3
"""Deterministic BDD issue enumerator.

Reads a test-results JSON and classifies every test into actionable buckets:
  - PASS: working correctly
  - XFAIL_LEGIT: expected failure, reason matches actual error
  - XFAIL_STEP_MISSING: xfailed due to StepDefinitionNotFoundError
  - XFAIL_BROAD: xfail tag is too broad (some examples pass → XPASS strict)
  - FAIL_E2E_REST: fails only on e2e_rest transport
  - FAIL_REGRESSION: was passing, now fails (not e2e_rest specific)
  - XPASS_STALE: xfail marker present but test passes (remove xfail)
  - XPASS_WEAK: xfail passes but assertion is in guard allowlist (strengthen)

Usage:
  python3 scripts/enumerate_bdd_issues.py test-results/170426_2037/bdd.json
  python3 scripts/enumerate_bdd_issues.py test-results/170426_2037/bdd.json --output issues.json
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def load_guard_allowlist() -> set[str]:
    """Extract weak-assertion function names from structural guard allowlists."""
    funcs: set[str] = set()
    for guard_file in [
        "tests/unit/test_architecture_bdd_assertion_strength.py",
        "tests/unit/test_architecture_bdd_no_trivial_assertions.py",
    ]:
        p = Path(guard_file)
        if not p.exists():
            continue
        for m in re.finditer(r'\d+ (\w+)"', p.read_text()):
            funcs.add(m.group(1))
    return funcs


def classify_tests(results_path: str) -> dict:
    """Classify every test into actionable buckets."""
    d = json.loads(Path(results_path).read_text())
    weak_funcs = load_guard_allowlist()

    buckets: dict[str, list[dict]] = defaultdict(list)

    for t in d["tests"]:
        nid = t["nodeid"]
        outcome = t["outcome"]
        func = nid.split("::")[-1].split("[")[0]
        transport = "unknown"
        if "[" in nid:
            param = nid.split("[")[-1].rstrip("]")
            for tr in ["e2e_rest", "impl", "a2a", "mcp", "rest"]:
                if param.startswith(tr):
                    transport = tr
                    break

        call_repr = (t.get("call", {}).get("longrepr", "") or "")[:500]
        entry = {"nodeid": nid, "func": func, "transport": transport}

        if outcome == "passed":
            buckets["PASS"].append(entry)

        elif outcome == "skipped":
            buckets["SKIPPED"].append(entry)

        elif outcome == "failed":
            if "XPASS(strict)" in call_repr:
                # xfail tag too broad — test passes but strict xfail makes it fail
                reason_m = re.search(r"XPASS\(strict\)\] (.+?)(?:\n|$)", call_repr)
                entry["xfail_reason"] = reason_m.group(1)[:200] if reason_m else ""
                buckets["XFAIL_BROAD"].append(entry)
            elif transport == "e2e_rest":
                err_m = re.search(r"AssertionError: (.+?)(?:\n|$)", call_repr)
                entry["error"] = err_m.group(1)[:200] if err_m else call_repr[:200].replace("\n", " ")
                buckets["FAIL_E2E_REST"].append(entry)
            else:
                err_m = re.search(r"AssertionError: (.+?)(?:\n|$)", call_repr)
                entry["error"] = err_m.group(1)[:200] if err_m else call_repr[:200].replace("\n", " ")
                buckets["FAIL_REGRESSION"].append(entry)

        elif outcome == "xfailed":
            if "StepDefinitionNotFoundError" in call_repr:
                step_m = re.search(r'Step definition is not found: (\w+) "([^"]+)"', call_repr)
                entry["missing_step"] = f'{step_m.group(1)} "{step_m.group(2)}"' if step_m else ""
                buckets["XFAIL_STEP_MISSING"].append(entry)
            elif "XFailed" in call_repr:
                reason_m = re.search(r"XFailed: (.+?)(?:\n|$)", call_repr)
                entry["reason"] = reason_m.group(1)[:200] if reason_m else ""
                buckets["XFAIL_EXPLICIT"].append(entry)
            else:
                err_m = re.search(r"AssertionError: (.+?)(?:\n|$)", call_repr)
                entry["error"] = err_m.group(1)[:200] if err_m else call_repr[:200].replace("\n", " ")
                buckets["XFAIL_ASSERTION"].append(entry)

        elif outcome == "xpassed":
            if func in weak_funcs or any(f"then_{w}" == func for w in weak_funcs):
                buckets["XPASS_WEAK"].append(entry)
            else:
                buckets["XPASS_STALE"].append(entry)

    return dict(buckets)


def print_summary(buckets: dict) -> None:
    """Print human-readable summary."""
    total = sum(len(v) for v in buckets.values())
    print(f"TOTAL: {total} tests\n")

    order = [
        ("PASS", "Working correctly"),
        ("XFAIL_EXPLICIT", "Expected failure (explicit reason)"),
        ("XFAIL_ASSERTION", "Expected failure (assertion fires)"),
        ("XFAIL_STEP_MISSING", "Missing step definition"),
        ("XPASS_STALE", "Stale xfail — remove marker"),
        ("XPASS_WEAK", "Weak assertion — strengthen"),
        ("XFAIL_BROAD", "xfail too broad — narrow to specific examples"),
        ("FAIL_E2E_REST", "e2e_rest-only failure — needs xfail or fix"),
        ("FAIL_REGRESSION", "Regression — was passing, now fails"),
        ("SKIPPED", "Skipped"),
    ]

    for bucket, desc in order:
        items = buckets.get(bucket, [])
        if not items:
            continue
        marker = "✅" if bucket == "PASS" else "⚠️ " if bucket.startswith("XFAIL") or bucket == "SKIPPED" else "❌"
        print(f"{marker} {bucket:25s} {len(items):5d}  — {desc}")

    # Action items
    action_buckets = ["XFAIL_STEP_MISSING", "XPASS_STALE", "XPASS_WEAK", "XFAIL_BROAD", "FAIL_E2E_REST", "FAIL_REGRESSION"]
    action_count = sum(len(buckets.get(b, [])) for b in action_buckets)
    print(f"\nACTION NEEDED: {action_count} tests across {sum(1 for b in action_buckets if buckets.get(b))} categories")

    # Detail for action items
    for bucket in action_buckets:
        items = buckets.get(bucket, [])
        if not items:
            continue
        print(f"\n--- {bucket} ({len(items)}) ---")

        if bucket == "XFAIL_STEP_MISSING":
            steps = Counter(i.get("missing_step", "") for i in items)
            print(f"  {len(steps)} unique missing steps:")
            for step, count in steps.most_common(10):
                print(f"    {count:3d}x {step[:90]}")
            if len(steps) > 10:
                print(f"    ... +{len(steps) - 10} more")

        elif bucket == "XFAIL_BROAD":
            by_reason = defaultdict(list)
            for i in items:
                by_reason[i.get("xfail_reason", "")].append(i)
            for reason, tests in by_reason.items():
                print(f"  {len(tests)}x reason: {reason[:100]}")
                for t in tests[:3]:
                    print(f"    {t['nodeid'].split('::')[-1][:90]}")

        elif bucket in ("FAIL_E2E_REST", "FAIL_REGRESSION"):
            by_func = defaultdict(list)
            for i in items:
                by_func[i["func"]].append(i)
            for func, tests in sorted(by_func.items(), key=lambda x: -len(x[1])):
                err = tests[0].get("error", "")[:80]
                print(f"  {len(tests):3d}x {func}")
                print(f"       {err}")

        elif bucket == "XPASS_STALE":
            by_uc = Counter()
            for i in items:
                nid = i["nodeid"]
                for u in ["uc002", "uc003", "uc004", "uc005", "uc006", "uc011", "uc019", "uc026"]:
                    if u in nid:
                        by_uc[u.upper().replace("UC0", "UC-0")] += 1
                        break
            for k, v in sorted(by_uc.items(), key=lambda x: -x[1]):
                print(f"  {k}: {v}")

        elif bucket == "XPASS_WEAK":
            by_func = Counter(i["func"] for i in items)
            for func, count in by_func.most_common():
                print(f"  {count:3d}x {func}")


def main():
    parser = argparse.ArgumentParser(description="Enumerate BDD test issues deterministically")
    parser.add_argument("results", help="Path to bdd.json test results")
    parser.add_argument("--output", "-o", help="Write JSON classification to file")
    args = parser.parse_args()

    buckets = classify_tests(args.results)
    print_summary(buckets)

    if args.output:
        # Serialize for machine consumption
        Path(args.output).write_text(json.dumps(buckets, indent=2))
        print(f"\nJSON written to {args.output}")


if __name__ == "__main__":
    main()
