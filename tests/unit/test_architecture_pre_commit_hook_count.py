"""Guard: commit-stage pre-commit hook count stays within D27 ceiling."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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
    cfg = yaml.safe_load(Path(".pre-commit-config.yaml").read_text(encoding="utf-8"))
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


@pytest.mark.arch_guard
def test_commit_hook_counter_counts_hooks_without_explicit_stages() -> None:
    cfg = {"repos": [{"hooks": [{"id": "ruff"}, {"id": "mypy"}]}]}
    assert _count_commit_stage_hooks(cfg) == 2


@pytest.mark.arch_guard
def test_commit_hook_counter_detects_under_ceiling() -> None:
    under_cfg = {
        "default_stages": ["pre-commit", "commit"],
        "repos": [{"hooks": [{"stages": ["commit"]}] * (COMMIT_STAGE_MIN - 1)}],
    }
    count = _count_commit_stage_hooks(under_cfg)
    assert count == COMMIT_STAGE_MIN - 1
    with pytest.raises(AssertionError):
        assert count >= COMMIT_STAGE_MIN
