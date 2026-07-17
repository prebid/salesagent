"""Shared compliance-report collector base for e2e compliance suites.

The A2A and schema compliance suites both collect per-operation results and
render/persist the same summary framing. This is the single home for the
counters, the summary header, and the JSON ``save_report`` writer (GH #1423);
subclasses own only what genuinely differs — their result shape
(``add_result``) and the per-result detail rendering (``_print_details``).
"""

import json
from pathlib import Path
from typing import Any


class ComplianceReportBase:
    """Counters + summary/save framing shared by compliance report collectors."""

    title = "COMPLIANCE SUMMARY"

    def __init__(self):
        self.results: list[dict[str, Any]] = []
        self.passed = 0
        self.failed = 0
        self.warnings = 0

    def print_summary(self):
        """Print the shared summary header, then the subclass's detail section."""
        print("\n" + "=" * 60)
        print(self.title)
        print("=" * 60)
        print(f"✓ Passed: {self.passed}")
        print(f"⚠ Warnings: {self.warnings}")
        print(f"✗ Failed: {self.failed}")
        print(f"Total Tests: {len(self.results)}")
        self._print_details()

    def _print_details(self):
        """Per-result rendering. Subclass hook — result shapes differ per suite."""

    def save_report(self, filepath: Path):
        """Save compliance report to JSON file."""
        report_data = {
            "summary": {
                "passed": self.passed,
                "failed": self.failed,
                "warnings": self.warnings,
                "total": len(self.results),
            },
            "results": self.results,
        }

        with open(filepath, "w") as f:
            json.dump(report_data, f, indent=2)
