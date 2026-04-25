"""Guard: Every actions/checkout step pins persist-credentials: false.

Default behavior leaves GITHUB_TOKEN in the local git config; subsequent
steps can leak it via ad-hoc git push. Explicit opt-in is safer.
"""

import re

from tests.unit._architecture_helpers import iter_workflow_files, repo_root

# Workflows that legitimately need persist-credentials (post-checkout git push).
_PERSIST_ALLOWED: set[str] = {
    # release-please pushes the release commit/tag back to main
    "release-please.yml",
}

_CHECKOUT_RE = re.compile(r"uses:\s*actions/checkout@")


def test_checkouts_disable_persist_credentials():
    violations: list[str] = []
    for wf in iter_workflow_files(repo_root()):
        if wf.name in _PERSIST_ALLOWED:
            continue
        text = wf.read_text()
        # Walk lines: each checkout block must have persist-credentials: false
        # within the next ~6 lines (the with: block).
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not _CHECKOUT_RE.search(line):
                continue
            window = "\n".join(lines[i : i + 8])
            if "persist-credentials: false" not in window:
                violations.append(f"{wf.name}:{i + 1}: missing persist-credentials: false")
    assert not violations, "\n".join(violations)
