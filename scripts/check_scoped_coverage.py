#!/usr/bin/env python3
"""Scoped coverage checker — deterministic quality gate for behavioral test coverage.

Reads tests/coverage_scopes.yaml, runs pytest with coverage for each scope,
and reports per-scope coverage against thresholds.

Usage:
    # Check all scopes
    scripts/check_scoped_coverage.py

    # Check a single scope
    scripts/check_scoped_coverage.py delivery

    # Show uncovered lines (verbose)
    scripts/check_scoped_coverage.py delivery --verbose

    # JSON output for CI
    scripts/check_scoped_coverage.py --json

    # Fail if any scope is below threshold (for CI gates)
    scripts/check_scoped_coverage.py --strict
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_scopes(scope_filter: str | None = None) -> dict:
    config_path = PROJECT_ROOT / "tests" / "coverage_scopes.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    scopes = config.get("scopes", {})
    if scope_filter:
        if scope_filter not in scopes:
            print(f"ERROR: scope '{scope_filter}' not found. Available: {', '.join(scopes)}")
            sys.exit(1)
        return {scope_filter: scopes[scope_filter]}
    return scopes


def find_existing_files(file_list: list[str]) -> list[str]:
    """Filter to only files that exist on disk."""
    return [f for f in file_list if (PROJECT_ROOT / f).exists()]


def run_coverage(scope_name: str, sources: list[str], tests: list[str]) -> dict:
    """Run pytest with coverage for a single scope, return results."""
    existing_tests = find_existing_files(tests)
    existing_sources = find_existing_files(sources)

    if not existing_tests:
        return {"scope": scope_name, "status": "skip", "reason": "no test files found"}
    if not existing_sources:
        return {"scope": scope_name, "status": "skip", "reason": "no source files found"}

    # Build coverage flags
    cov_flags = []
    for src in existing_sources:
        module = src.replace("/", ".").removesuffix(".py")
        cov_flags.extend(["--cov", module])
    cov_flags.extend(["--cov-report", "json:-.json"])

    run_test_sh = PROJECT_ROOT / "scripts" / "run-test.sh"
    cmd = [str(run_test_sh), *existing_tests, "-q", "--no-header", "--tb=no", *cov_flags]

    subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)

    # Parse coverage JSON from file
    cov_json_path = PROJECT_ROOT / "-.json"
    if not cov_json_path.exists():
        # Fallback: parse term output for coverage numbers
        return _parse_term_output(scope_name, existing_tests, existing_sources)

    try:
        with open(cov_json_path) as f:
            cov_data = json.load(f)
    finally:
        cov_json_path.unlink(missing_ok=True)

    # Extract per-file coverage
    files = {}
    total_stmts = 0
    total_miss = 0
    for src in existing_sources:
        # coverage.py uses absolute paths or relative — try both
        file_data = None
        for key in cov_data.get("files", {}):
            if key.endswith(src) or src in key:
                file_data = cov_data["files"][key]
                break

        if file_data:
            stmts = file_data["summary"]["num_statements"]
            miss = file_data["summary"]["missing_lines"]
            pct = file_data["summary"]["percent_covered"]
            missing_lines = file_data.get("missing_lines", [])
            files[src] = {
                "statements": stmts,
                "missing": miss,
                "coverage": round(pct, 1),
                "missing_lines": missing_lines,
            }
            total_stmts += stmts
            total_miss += miss
        else:
            files[src] = {
                "statements": 0,
                "missing": 0,
                "coverage": 0,
                "missing_lines": [],
                "note": "not found in coverage data",
            }

    total_pct = round((total_stmts - total_miss) / total_stmts * 100, 1) if total_stmts else 0

    return {
        "scope": scope_name,
        "status": "ok",
        "total_coverage": total_pct,
        "total_statements": total_stmts,
        "total_missing": total_miss,
        "files": files,
    }


def _parse_term_output(scope_name: str, tests: list[str], existing_sources: list[str]) -> dict:
    """Fallback: run with term-missing and parse output."""
    cov_flags = []
    for src in existing_sources:
        module = src.replace("/", ".").removesuffix(".py")
        cov_flags.extend(["--cov", module])
    cov_flags.extend(["--cov-report", "term-missing"])

    run_test_sh = PROJECT_ROOT / "scripts" / "run-test.sh"
    cmd = [str(run_test_sh), *tests, "-q", "--no-header", "--tb=no", *cov_flags]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)

    files = {}
    total_stmts = 0
    total_miss = 0

    for line in result.stdout.splitlines():
        for src in existing_sources:
            filename = Path(src).name.removesuffix(".py")
            if filename in line and "%" in line:
                parts = line.split()
                for _i, p in enumerate(parts):
                    if "%" in p:
                        pct = float(p.replace("%", ""))
                        stmts = int(parts[1]) if len(parts) > 1 else 0
                        miss = int(parts[2]) if len(parts) > 2 else 0
                        missing_str = parts[-1] if len(parts) > 4 else ""
                        files[src] = {
                            "statements": stmts,
                            "missing": miss,
                            "coverage": pct,
                            "missing_lines_str": missing_str,
                        }
                        total_stmts += stmts
                        total_miss += miss
                        break

    total_pct = round((total_stmts - total_miss) / total_stmts * 100, 1) if total_stmts else 0

    return {
        "scope": scope_name,
        "status": "ok",
        "total_coverage": total_pct,
        "total_statements": total_stmts,
        "total_missing": total_miss,
        "files": files,
    }


def print_report(results: list[dict], verbose: bool = False) -> bool:
    """Print human-readable report. Returns True if all scopes pass threshold."""
    all_pass = True

    print("\n" + "=" * 70)
    print("SCOPED COVERAGE REPORT")
    print("=" * 70)

    for r in results:
        scope = r["scope"]

        if r["status"] == "skip":
            print(f"\n  {scope}: SKIPPED ({r['reason']})")
            continue

        threshold = r.get("threshold", 100)
        pct = r["total_coverage"]
        passed = pct >= threshold
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False

        print(
            f"\n  {scope}: {pct}% ({r['total_statements']} stmts, {r['total_missing']} uncovered) [{status} threshold={threshold}%]"
        )

        for filepath, fdata in r.get("files", {}).items():
            fname = Path(filepath).name
            fcov = fdata["coverage"]
            fmiss = fdata["missing"]
            indicator = " *" if fmiss > 0 else ""
            print(f"    {fname:<45} {fcov:>5}%  ({fmiss} uncovered){indicator}")

            if verbose and fdata.get("missing_lines"):
                lines = fdata["missing_lines"]
                # Group consecutive lines into ranges
                ranges = []
                start = lines[0] if lines else None
                end = start
                for ln in lines[1:]:
                    if ln == end + 1:
                        end = ln
                    else:
                        ranges.append(f"{start}-{end}" if start != end else str(start))
                        start = end = ln
                if start is not None:
                    ranges.append(f"{start}-{end}" if start != end else str(start))
                print(f"      Missing: {', '.join(ranges)}")

    print("\n" + "-" * 70)
    overall = "ALL PASS" if all_pass else "SOME SCOPES BELOW THRESHOLD"
    print(f"  Result: {overall}")
    print("=" * 70 + "\n")

    return all_pass


def main():
    args = sys.argv[1:]
    verbose = "--verbose" in args or "-v" in args
    json_output = "--json" in args
    strict = "--strict" in args
    scope_filter = None

    for arg in args:
        if not arg.startswith("-"):
            scope_filter = arg
            break

    scopes = load_scopes(scope_filter)
    results = []

    for name, cfg in scopes.items():
        print(f"  Running scope: {name}...", file=sys.stderr)
        r = run_coverage(name, cfg["sources"], cfg["tests"])
        r["threshold"] = cfg.get("threshold", 100)
        results.append(r)

    if json_output:
        print(json.dumps(results, indent=2))
    else:
        all_pass = print_report(results, verbose=verbose)

    if strict and not all_pass:
        sys.exit(1)


if __name__ == "__main__":
    main()
