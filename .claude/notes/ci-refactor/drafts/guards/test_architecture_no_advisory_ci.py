"""Guard: No advisory-only steps in workflow files.

Enforces that CI gates fail loudly. `|| true` and `continue-on-error: true`
on lint/test steps mask regressions. Allowed only for cleanup steps where
ignoring failure is correct.
"""

import re

from tests.unit._architecture_helpers import iter_workflow_files, repo_root

# (filename, line-pattern-substring) entries that are legitimately advisory.
# CodeQL Path C uses continue-on-error per D10 — that allowlist entry is
# removed in PR 6 commit 5 when CodeQL flips to gating.
_ALLOWLIST: set[tuple[str, str]] = {
    ("codeql.yml", "github/codeql-action/analyze"),  # D10 Path C
    ("security.yml", "uvx zizmor --format sarif"),  # SARIF still uploads
}

_PATTERNS = (
    re.compile(r"\|\|\s*true\b"),
    re.compile(r"continue-on-error:\s*true"),
)


def _is_allowlisted(filename: str, line: str) -> bool:
    return any(filename == f and substr in line for f, substr in _ALLOWLIST)


def test_no_advisory_steps_in_workflows():
    violations: list[str] = []
    for wf_path in iter_workflow_files(repo_root()):
        for i, line in enumerate(wf_path.read_text().splitlines(), 1):
            if not any(p.search(line) for p in _PATTERNS):
                continue
            if _is_allowlisted(wf_path.name, line):
                continue
            violations.append(f"{wf_path.name}:{i}: {line.strip()}")
    assert not violations, "Advisory-only step in workflow (every gate must fail loudly):\n" + "\n".join(
        f"  {v}" for v in violations
    )
