"""Behavioral contract for the optional ``scripts/run-test.sh`` hook."""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_TEST = REPO_ROOT / "scripts" / "run-test.sh"


def _run(hook: Path, *args: str, remote_active: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "SA_RUN_TEST_HOOK": str(hook)}
    if remote_active is None:
        env.pop("REMOTE_TEST_ACTIVE", None)
    else:
        env["REMOTE_TEST_ACTIVE"] = remote_active
    return subprocess.run(
        ["bash", str(RUN_TEST), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_run_test_hook_receives_original_arguments_and_can_take_over(tmp_path: Path) -> None:
    hook = tmp_path / "run-test-hook.sh"
    hook.write_text('printf "hook:%s\\n" "$*"\nexit 0\n')

    result = _run(hook, "--db", "tests/integration/test_example.py", "-k", "focused case")

    assert result.returncode == 0
    assert result.stdout == "hook:--db tests/integration/test_example.py -k focused case\n"


def test_remote_test_active_bypasses_run_test_hook(tmp_path: Path) -> None:
    hook = tmp_path / "run-test-hook.sh"
    hook.write_text('printf "hook-ran\\n"\nexit 0\n')

    result = _run(hook, remote_active="1")

    assert result.returncode == 1
    assert "Usage: scripts/run-test.sh" in result.stderr
    assert "hook-ran" not in result.stdout
