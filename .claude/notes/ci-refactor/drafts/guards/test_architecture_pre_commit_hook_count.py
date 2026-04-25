"""Guard: pre-commit commit-stage hook count <= 12.

Per PR 4 of issue #1234, the pre-commit (commit) stage is reserved for fast
formatters and hygiene checks. Heavier checks live in pre-push or CI.
"""

import yaml
from tests.unit._architecture_helpers import repo_root

_MAX_COMMIT_STAGE_HOOKS = 12


def _count_hooks_at_stage(stage: str) -> int:
    cfg = yaml.safe_load((repo_root() / ".pre-commit-config.yaml").read_text())
    default_stages = cfg.get("default_stages", ["pre-commit", "commit"])
    n = 0
    for repo in cfg.get("repos", []):
        for hook in repo.get("hooks", []):
            stages = hook.get("stages") or default_stages
            if stage in stages or (stage == "pre-commit" and "commit" in stages):
                n += 1
    return n


def test_commit_stage_hook_count_within_limit():
    n = _count_hooks_at_stage("pre-commit")
    assert n <= _MAX_COMMIT_STAGE_HOOKS, (
        f"pre-commit stage has {n} hooks (max {_MAX_COMMIT_STAGE_HOOKS}). "
        "Move medium-cost hooks to stages: [pre-push] or to CI."
    )
