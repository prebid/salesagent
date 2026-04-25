"""Guard: Every workflow declares concurrency with PR-only cancel-in-progress.

Avoids wasted runner minutes on superseded PR pushes. Releases must NOT
cancel-in-progress (canceling mid-release leaves partial artifacts).
"""

import yaml
from tests.unit._architecture_helpers import iter_workflow_files, repo_root

# Workflows where cancel-in-progress is wrong (release artifacts must complete).
# Note: PR 6 EXTENDS release-please.yml's publish-docker job with cosign signing —
# no separate `release.yml` exists. (Decision-5 P0 sweep.)
_NO_CONCURRENCY_REQUIRED: set[str] = {
    "release-please.yml",
}


def test_workflows_have_concurrency_block():
    violations: list[str] = []
    for wf in iter_workflow_files(repo_root()):
        if wf.name in _NO_CONCURRENCY_REQUIRED:
            continue
        cfg = yaml.safe_load(wf.read_text())
        if not isinstance(cfg, dict):
            continue
        concurrency = cfg.get("concurrency")
        if concurrency is None:
            violations.append(f"{wf.name}: missing top-level concurrency:")
            continue
        if isinstance(concurrency, dict):
            cip = concurrency.get("cancel-in-progress")
            # Accept literal True OR an expression that mentions pull_request.
            if cip is True:
                continue
            if isinstance(cip, str) and "pull_request" in cip:
                continue
            violations.append(
                f"{wf.name}: concurrency.cancel-in-progress must be true on PRs "
                "(use ${{ github.event_name == 'pull_request' }} to scope)"
            )
    assert not violations, "\n".join(violations)
