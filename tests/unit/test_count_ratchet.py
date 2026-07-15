"""Unit tests for the shared count-ratchet driver (#1613 / ADR-009)."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_HOOKS = _REPO / ".pre-commit-hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))


def _load(name: str):
    path = _HOOKS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


count_ratchet = _load("count_ratchet")
check_type_ignore_count = _load("check_type_ignore_count")
check_ruff_complexity_count = _load("check_ruff_complexity_count")
check_mypy_untyped_defs_count = _load("check_mypy_untyped_defs_count")


def _cp(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_count_ratchet_creates_missing_baseline(tmp_path: Path) -> None:
    baseline = tmp_path / "counts.json"
    writes: list[dict[str, int]] = []
    out, err = io.StringIO(), io.StringIO()

    rc = count_ratchet.run_count_ratchet(
        keys=("a",),
        current={"a": 3},
        baseline_file=baseline,
        update_baseline=False,
        read_baseline=lambda _p: None,
        write_baseline=lambda _p, counts: writes.append(dict(counts)),
        increase_header="up",
        increase_hints=(),
        out=out,
        err=err,
    )

    assert rc == 0
    assert writes == [{"a": 3}]
    assert "Creating" in out.getvalue()


def test_run_count_ratchet_update_baseline_rewrites(tmp_path: Path) -> None:
    baseline = tmp_path / "counts.json"
    writes: list[dict[str, int]] = []
    out, err = io.StringIO(), io.StringIO()

    rc = count_ratchet.run_count_ratchet(
        keys=("a",),
        current={"a": 9},
        baseline_file=baseline,
        update_baseline=True,
        read_baseline=lambda _p: {"a": 4},
        write_baseline=lambda _p, counts: writes.append(dict(counts)),
        increase_header="up",
        increase_hints=(),
        out=out,
        err=err,
    )

    assert rc == 0
    assert writes == [{"a": 9}]
    assert "Updating baseline" in out.getvalue()


def test_run_count_ratchet_increase_exits_without_write(tmp_path: Path) -> None:
    baseline = tmp_path / "counts.json"
    writes: list[dict[str, int]] = []
    out, err = io.StringIO(), io.StringIO()

    rc = count_ratchet.run_count_ratchet(
        keys=("a", "b"),
        current={"a": 5, "b": 1},
        baseline_file=baseline,
        update_baseline=False,
        read_baseline=lambda _p: {"a": 4, "b": 1},
        write_baseline=lambda _p, counts: writes.append(dict(counts)),
        increase_header="COUNTS UP",
        increase_hints=("hint-line",),
        out=out,
        err=err,
    )

    assert rc == 1
    assert writes == []
    assert "COUNTS UP" in err.getvalue()
    assert "hint-line" in err.getvalue()


def test_run_count_ratchet_decrease_auto_lowers(tmp_path: Path) -> None:
    baseline = tmp_path / "counts.json"
    writes: list[dict[str, int]] = []
    out, err = io.StringIO(), io.StringIO()

    rc = count_ratchet.run_count_ratchet(
        keys=("a",),
        current={"a": 2},
        baseline_file=baseline,
        update_baseline=False,
        read_baseline=lambda _p: {"a": 5},
        write_baseline=lambda _p, counts: writes.append(dict(counts)),
        increase_header="up",
        increase_hints=(),
        out=out,
        err=err,
    )

    assert rc == 0
    assert writes == [{"a": 2}]
    assert "Automatically updating" in out.getvalue()


def test_run_count_ratchet_all_equal_no_write(tmp_path: Path) -> None:
    writes: list[dict[str, int]] = []
    out, err = io.StringIO(), io.StringIO()

    rc = count_ratchet.run_count_ratchet(
        keys=("a", "b"),
        current={"a": 1, "b": 2},
        baseline_file=tmp_path / "counts.json",
        update_baseline=False,
        read_baseline=lambda _p: {"a": 1, "b": 2},
        write_baseline=lambda _p, counts: writes.append(dict(counts)),
        increase_header="up",
        increase_hints=(),
        out=out,
        err=err,
    )

    assert rc == 0
    assert writes == []


def test_run_count_ratchet_mixed_up_down_fails_without_write(tmp_path: Path) -> None:
    baseline = tmp_path / "counts.json"
    writes: list[dict[str, int]] = []
    out, err = io.StringIO(), io.StringIO()

    rc = count_ratchet.run_count_ratchet(
        keys=("a", "b"),
        current={"a": 6, "b": 0},
        baseline_file=baseline,
        update_baseline=False,
        read_baseline=lambda _p: {"a": 5, "b": 2},
        write_baseline=lambda _p, counts: writes.append(dict(counts)),
        increase_header="up",
        increase_hints=(),
        out=out,
        err=err,
    )

    assert rc == 1
    assert writes == []


def test_read_json_baseline_rejects_non_object(tmp_path: Path) -> None:
    baseline = tmp_path / "bad.json"
    baseline.write_text("[227]\n", encoding="utf-8")
    assert count_ratchet.read_json_baseline(baseline, ("C901",)) is None


def test_read_json_baseline_reads_object(tmp_path: Path) -> None:
    baseline = tmp_path / "ok.json"
    baseline.write_text('{"C901": 1, "PLR0912": 2}\n', encoding="utf-8")
    assert count_ratchet.read_json_baseline(baseline, ("C901", "PLR0912", "PLR0915")) == {
        "C901": 1,
        "PLR0912": 2,
        "PLR0915": 0,
    }


def test_run_counting_tool_rc0_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _cp(stdout="ok", returncode=0)
    monkeypatch.setattr(count_ratchet.subprocess, "run", lambda *_a, **_k: expected)
    got = count_ratchet.run_counting_tool(
        ["true"],
        cwd=_REPO,
        has_findings=lambda _r: False,
        label="tool",
    )
    assert got is expected


def test_run_counting_tool_rc1_with_findings_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _cp(stdout="finding", returncode=1)
    monkeypatch.setattr(count_ratchet.subprocess, "run", lambda *_a, **_k: expected)
    got = count_ratchet.run_counting_tool(
        ["tool"],
        cwd=_REPO,
        has_findings=lambda r: bool((r.stdout or "").strip()),
        label="tool",
    )
    assert got is expected


def test_run_counting_tool_rc1_empty_findings_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(count_ratchet.subprocess, "run", lambda *_a, **_k: _cp(stdout="", returncode=1))
    with pytest.raises(SystemExit) as exc:
        count_ratchet.run_counting_tool(
            ["tool"],
            cwd=_REPO,
            has_findings=lambda r: bool((r.stdout or "").strip()),
            label="tool",
        )
    assert exc.value.code == 2


def test_run_counting_tool_rc2_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        count_ratchet.subprocess,
        "run",
        lambda *_a, **_k: _cp(stdout="findings", returncode=2),
    )
    with pytest.raises(SystemExit) as exc:
        count_ratchet.run_counting_tool(
            ["tool"],
            cwd=_REPO,
            has_findings=lambda r: True,
            label="tool",
        )
    assert exc.value.code == 2


def test_count_rule_violations_tallies_selected_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        [
            {"code": "C901"},
            {"code": "C901"},
            {"code": "PLR0912"},
            {"code": "OTHER"},
        ]
    )
    monkeypatch.setattr(
        count_ratchet.subprocess,
        "run",
        lambda *_a, **_k: _cp(stdout=payload, returncode=1),
    )
    assert check_ruff_complexity_count.count_rule_violations(_REPO, _REPO / "src") == {
        "C901": 2,
        "PLR0912": 1,
        "PLR0915": 0,
    }


def test_count_rule_violations_empty_findings_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(count_ratchet.subprocess, "run", lambda *_a, **_k: _cp(stdout="", returncode=1))
    with pytest.raises(SystemExit) as exc:
        check_ruff_complexity_count.count_rule_violations(_REPO, _REPO / "src")
    assert exc.value.code == 2


def test_count_untyped_defs_errors_tallies_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = "\n".join(
        [
            "a.py:1: error: x",
            "noise without sentinel",
            "b.py:2: error: y",
            "c.py:3: note: not an error",
            "d.py:4: error: z",
        ]
    )
    monkeypatch.setattr(
        count_ratchet.subprocess,
        "run",
        lambda *_a, **_k: _cp(stdout=stdout, returncode=1),
    )
    assert check_mypy_untyped_defs_count.count_untyped_defs_errors(_REPO) == 3


def test_count_untyped_defs_errors_zero_on_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(count_ratchet.subprocess, "run", lambda *_a, **_k: _cp(stdout="", returncode=0))
    assert check_mypy_untyped_defs_count.count_untyped_defs_errors(_REPO) == 0


def test_count_untyped_defs_errors_rc1_without_sentinel_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        count_ratchet.subprocess,
        "run",
        lambda *_a, **_k: _cp(stdout="error: crash without sentinel", returncode=1),
    )
    with pytest.raises(SystemExit) as exc:
        check_mypy_untyped_defs_count.count_untyped_defs_errors(_REPO)
    assert exc.value.code == 2


def test_count_untyped_defs_errors_rc2_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        count_ratchet.subprocess,
        "run",
        lambda *_a, **_k: _cp(stdout="a.py:1: error: x", returncode=2),
    )
    with pytest.raises(SystemExit) as exc:
        check_mypy_untyped_defs_count.count_untyped_defs_errors(_REPO)
    assert exc.value.code == 2


def test_mypy_main_tooling_failure_does_not_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path
    (repo / "src").mkdir()
    baseline = repo / ".mypy-untyped-defs-baseline"
    baseline.write_text("227\n", encoding="utf-8")
    before = baseline.read_text(encoding="utf-8")

    monkeypatch.setattr(
        check_mypy_untyped_defs_count,
        "resolve_ratchet_paths",
        lambda **_kwargs: (repo, repo / "src", baseline),
    )
    monkeypatch.setattr(
        check_mypy_untyped_defs_count,
        "parse_ratchet_args",
        lambda _description: type("Args", (), {"update_baseline": False})(),
    )
    monkeypatch.setattr(
        check_mypy_untyped_defs_count,
        "count_untyped_defs_errors",
        lambda _repo: (_ for _ in ()).throw(SystemExit(2)),
    )

    with pytest.raises(SystemExit) as exc:
        check_mypy_untyped_defs_count.main()
    assert exc.value.code == 2
    assert baseline.read_text(encoding="utf-8") == before


def test_ruff_complexity_rules_match_pyproject_ratchet_bucket() -> None:
    """S1: RULES must equal the pyproject 'Ratchet targets' ignore bucket."""
    text = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(
        r"# --- Ratchet targets \(count-ratcheted via \.ruff-complexity-baseline; ADR-009\) ---\n"
        r"(.*?)\n\s*# --- Permanently accepted",
        text,
        flags=re.DOTALL,
    )
    assert match, "pyproject.toml missing Ratchet targets / Permanently accepted headers"
    from_pyproject = tuple(re.findall(r'"([A-Z0-9]+)"', match.group(1)))
    assert check_ruff_complexity_count.RULES == from_pyproject


@pytest.mark.parametrize(
    ("baseline_count", "current_count", "update_baseline", "expected"),
    [
        (None, 10, False, 10),
        (5, 10, True, 10),
        (8, 5, False, 5),
        (3, 9, False, 3),
    ],
)
def test_raise_probe_value_cases(
    baseline_count: int | None,
    current_count: int,
    update_baseline: bool,
    expected: int,
) -> None:
    assert (
        check_type_ignore_count.raise_probe_value(
            baseline_count=baseline_count,
            current_count=current_count,
            update_baseline=update_baseline,
        )
        == expected
    )


def test_raise_probe_failure_does_not_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Anti-tamper: failed origin/main probe returns 1 before any baseline write."""
    repo = tmp_path
    (repo / "src").mkdir()
    baseline = repo / ".type-ignore-baseline"
    baseline.write_text("4\n", encoding="utf-8")
    writes: list[object] = []

    monkeypatch.setattr(check_type_ignore_count, "count_type_ignores", lambda _p: 7)
    monkeypatch.setattr(
        check_type_ignore_count,
        "check_baseline_not_raised",
        lambda _repo, probe: writes.append(("probe", probe)) or 1,
    )
    monkeypatch.setattr(
        check_type_ignore_count,
        "resolve_ratchet_paths",
        lambda **_kwargs: (repo, repo / "src", baseline),
    )
    monkeypatch.setattr(
        check_type_ignore_count,
        "parse_ratchet_args",
        lambda _description: type("Args", (), {"update_baseline": True})(),
    )
    monkeypatch.setattr(
        check_type_ignore_count,
        "run_count_ratchet",
        lambda **_kwargs: writes.append("ratchet-ran") or 0,
    )

    assert check_type_ignore_count.main() == 1
    assert writes == [("probe", 7)]


def test_ruff_raise_probe_failure_does_not_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path
    (repo / "src").mkdir()
    baseline = repo / ".ruff-complexity-baseline"
    baseline.write_text(json.dumps({"C901": 1, "PLR0912": 1, "PLR0915": 1}) + "\n", encoding="utf-8")
    writes: list[object] = []

    monkeypatch.setattr(
        check_ruff_complexity_count,
        "count_rule_violations",
        lambda *_a, **_k: {"C901": 2, "PLR0912": 1, "PLR0915": 1},
    )
    monkeypatch.setattr(
        check_ruff_complexity_count,
        "check_baseline_not_raised",
        lambda _repo, probe: writes.append(("probe", probe)) or 1,
    )
    monkeypatch.setattr(
        check_ruff_complexity_count,
        "resolve_ratchet_paths",
        lambda **_kwargs: (repo, repo / "src", baseline),
    )
    monkeypatch.setattr(
        check_ruff_complexity_count,
        "parse_ratchet_args",
        lambda _description: type("Args", (), {"update_baseline": False})(),
    )
    monkeypatch.setattr(
        check_ruff_complexity_count,
        "run_count_ratchet",
        lambda **_kwargs: writes.append("ratchet-ran") or 0,
    )

    assert check_ruff_complexity_count.main() == 1
    assert writes == [("probe", {"C901": 1, "PLR0912": 1, "PLR0915": 1})]
