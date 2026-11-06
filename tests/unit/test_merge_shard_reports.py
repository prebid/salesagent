"""The fold of the BDD shards' artifacts, and the check that the full-tree record arrived."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.ci import assert_full_tree_record, merge_shard_reports
from scripts.ci.merge_shard_reports import ShardMergeError, merge_payloads, merge_reports
from scripts.ci.shard_split import SHARD_COUNTS

_SHARDS = SHARD_COUNTS["bdd"]


def _report(**overrides: Any) -> dict[str, Any]:
    return {
        "exitcode": 0,
        "duration": 10.0,
        "summary": {"collected": 2, "total": 2, "passed": 2, "deselected": 0},
        "tests": [{"nodeid": "a"}],
        "collectors": [{"nodeid": "c"}],
        "warnings": [{"message": "w"}],
        **overrides,
    }


def _publish(results: Path, *, reports: int = _SHARDS, payloads: int = _SHARDS) -> None:
    (results / "payloads").mkdir(parents=True)
    for shard in range(1, reports + 1):
        (results / f"bdd-inprocess-s{shard}.json").write_text(json.dumps(_report()), encoding="utf-8")
    for shard in range(1, payloads + 1):
        artifact = {"run": {"collected": 2}, "nodes": {f"node-{shard}": []}}
        (results / "payloads" / f"bdd-inprocess-s{shard}.json").write_text(json.dumps(artifact), encoding="utf-8")


def test_reports_sum_counts_and_concatenate_their_lists() -> None:
    merged = merge_reports([_report(), _report(summary={"total": 3, "passed": 1, "failed": 2})])

    assert merged["summary"] == {"collected": 2, "total": 5, "passed": 3, "deselected": 0, "failed": 2}
    assert [len(merged[key]) for key in ("tests", "collectors", "warnings")] == [2, 2, 2]


def test_a_failed_second_shard_fails_the_merged_report() -> None:
    assert merge_reports([_report(), _report(exitcode=1)])["exitcode"] == 1


def test_duration_is_the_slowest_shard_because_shards_run_concurrently() -> None:
    assert merge_reports([_report(duration=3.0), _report(duration=7.0)])["duration"] == 7.0


def test_payloads_union_nodes_and_sum_their_scope() -> None:
    merged = merge_payloads(
        [
            {"run": {"collected": 2, "selection": "", "exitstatus": 0}, "nodes": {"b": []}},
            {"run": {"collected": 3, "selection": "", "exitstatus": 1}, "nodes": {"a": []}},
        ]
    )

    assert merged["run"] == {"collected": 5, "selection": "", "exitstatus": 1}
    assert list(merged["nodes"]) == ["a", "b"]


def test_a_node_in_two_shards_is_refused() -> None:
    with pytest.raises(ShardMergeError, match="two shards"):
        merge_payloads([{"nodes": {"a": []}}, {"nodes": {"a": []}}])


def test_main_publishes_one_suite_and_removes_the_parts(tmp_path: Path) -> None:
    _publish(tmp_path)

    assert merge_shard_reports.main(str(tmp_path), "payloads") == 0

    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["bdd_inprocess.json"]
    assert sorted(p.name for p in (tmp_path / "payloads").glob("*.json")) == ["bdd_inprocess.json"]
    assert json.loads((tmp_path / "bdd_inprocess.json").read_text())["summary"]["total"] == 2 * _SHARDS


@pytest.mark.parametrize("published", [{"reports": 1}, {"payloads": 1}])
def test_one_missing_shard_writes_nothing(tmp_path: Path, published: dict[str, int]) -> None:
    _publish(tmp_path, **published)

    assert merge_shard_reports.main(str(tmp_path), "payloads") == 1

    assert not (tmp_path / "bdd_inprocess.json").exists()
    assert not (tmp_path / "payloads" / "bdd_inprocess.json").exists()


def test_an_unreadable_shard_writes_nothing(tmp_path: Path) -> None:
    _publish(tmp_path)
    (tmp_path / "bdd-inprocess-s2.json").write_text("{not json", encoding="utf-8")

    assert merge_shard_reports.main(str(tmp_path), "payloads") == 1

    assert not (tmp_path / "bdd_inprocess.json").exists()


def _record(directory: Path, name: str, target: list[str]) -> None:
    (directory / name).write_text(json.dumps({"target": target, "filter": ""}), encoding="utf-8")


def test_full_tree_record_present_passes(tmp_path: Path) -> None:
    _record(tmp_path, "a.json", ["tests/bdd/test_uc001.py"])
    _record(tmp_path, "b.json", ["tests/bdd"])

    assert assert_full_tree_record.main(str(tmp_path)) == 0


def test_only_shard_records_fail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _record(tmp_path, "a.json", ["tests/bdd/test_uc001.py"])

    assert assert_full_tree_record.main(str(tmp_path)) == 1
    assert "a.json" in capsys.readouterr().out


def test_an_empty_directory_fails(tmp_path: Path) -> None:
    assert assert_full_tree_record.main(str(tmp_path)) == 1
