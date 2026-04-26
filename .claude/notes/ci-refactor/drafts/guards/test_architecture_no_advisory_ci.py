# pyright: reportMissingImports=false
# Draft file: imports `tests.unit._architecture_helpers` which doesn't exist on disk
# until PR 2 creates the baseline + PR 4 lifts this guard from drafts/ to tests/unit/.
# Pyright cannot resolve the import in its current draft location; suppression is
# correct because the import will resolve once lifted to its destination path.
"""Guard: No advisory-only steps in workflow files.

Enforces that CI gates fail loudly. `|| true` and `continue-on-error: true`
on lint/test steps mask regressions. Allowed only for specific (file, step-action)
combinations where ignoring failure is correct (e.g., D10 Path C advisory CodeQL).
"""

import re

from tests.unit._architecture_helpers import iter_workflow_files, repo_root

# (filename, action-substring) entries: when `continue-on-error: true` or `|| true`
# fires, we look BACKWARD up to 5 non-blank lines for `uses:` matching the substring.
# The CodeQL Path C entry is removed in PR 6 commit 5 when CodeQL flips to gating.
_ALLOWLIST: set[tuple[str, str]] = {
    ("codeql.yml", "github/codeql-action/analyze"),  # D10 Path C (until PR 6 commit 5)
    ("security.yml", "uvx zizmor --format sarif"),  # SARIF still uploads even on findings
}

_PATTERNS = (
    re.compile(r"\|\|\s*true\b"),
    re.compile(r"continue-on-error:\s*true"),
)


def _is_allowlisted(filename: str, lines: list[str], idx: int) -> bool:
    """Look back up to 5 lines from violation idx for an allowlisted `uses:` step."""
    start = max(0, idx - 5)
    context = "\n".join(lines[start : idx + 1])
    return any(filename == f and substr in context for f, substr in _ALLOWLIST)


def test_no_advisory_steps_in_workflows():
    violations: list[str] = []
    for wf_path in iter_workflow_files(repo_root()):
        lines = wf_path.read_text().splitlines()
        for i, line in enumerate(lines):
            if not any(p.search(line) for p in _PATTERNS):
                continue
            if _is_allowlisted(wf_path.name, lines, i):
                continue
            violations.append(f"{wf_path.name}:{i + 1}: {line.strip()}")
    assert not violations, "Advisory-only step in workflow (every gate must fail loudly):\n" + "\n".join(
        f"  {v}" for v in violations
    )
