"""Guard: commit-stage pre-commit hook count stays within D27 ceiling."""

from __future__ import annotations

import pytest

from tests.unit._architecture_helpers import load_pre_commit_config

COMMIT_STAGE_MIN = 10
COMMIT_STAGE_MAX = 12


def _count_commit_stage_hooks(cfg: dict) -> int:
    default = cfg.get("default_stages", ["pre-commit", "commit"])
    return sum(
        1
        for repo in cfg["repos"]
        for hook in repo["hooks"]
        if "pre-commit" in hook.get("stages", default) or "commit" in hook.get("stages", default)
    )


@pytest.mark.arch_guard
def test_pre_commit_hook_count_within_ceiling() -> None:
    cfg = load_pre_commit_config()
    count = _count_commit_stage_hooks(cfg)
    assert count >= COMMIT_STAGE_MIN, (
        f"commit-stage hook count {count} < {COMMIT_STAGE_MIN} — likely over-deletion; see .pre-commit-coverage-map.yml"
    )
    assert count <= COMMIT_STAGE_MAX, (
        f"commit-stage hook count {count} > {COMMIT_STAGE_MAX} — D27 ceiling exceeded; move hooks to pre-push"
    )


@pytest.mark.arch_guard
def test_commit_hook_counter_detects_over_ceiling() -> None:
    over_cfg = {
        "default_stages": ["pre-commit", "commit"],
        "repos": [{"hooks": [{"stages": ["commit"]}] * (COMMIT_STAGE_MAX + 1)}],
    }
    assert _count_commit_stage_hooks(over_cfg) == COMMIT_STAGE_MAX + 1
