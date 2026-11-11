"""Run the CI disk action against controlled filesystem readings.

This process contract has no buyer wire or database. Bash executes the real
control flow; only the disk probe and destructive external command are replaced.
"""

import subprocess
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize(
    ("readings", "cleanup_status", "expected_status", "reclaims"),
    [
        ([85 * 1024**3, 85 * 1024**3], 0, 0, False),
        ([14 * 1024**3, 85 * 1024**3], 0, 0, True),
        ([76 * 1024**3, 76 * 1024**3], 0, 0, False),
        ([14 * 1024**3, 76 * 1024**3], 0, 0, True),
        ([76 * 1024**3 - 1, 76 * 1024**3 - 1], 0, 1, True),
        ([1024**3, 14 * 1024**3], 0, 1, True),
        ([1024**3, 1024**3], 0, 1, True),
        ([], 0, 1, False),
        ([1024**3], 0, 1, True),
        ([1024**3, 85 * 1024**3], 1, 1, True),
    ],
    ids=[
        "enough-before-cleanup",
        "reclaim-sufficient",
        "exact-floor-before-cleanup",
        "exact-floor-after-cleanup",
        "one-byte-below-floor",
        "historic-overflow",
        "reclaim-insufficient",
        "initial-probe-fails",
        "post-reclaim-probe-fails",
        "cleanup-command-fails",
    ],
)
def test_free_runner_disk_action(
    tmp_path: Path, readings: list[int], cleanup_status: int, expected_status: int, reclaims: bool
) -> None:
    action_path = Path(__file__).resolve().parents[2] / ".github/actions/_free-runner-disk/action.yml"
    script = yaml.safe_load(action_path.read_text())["runs"]["steps"][0]["run"]
    (tmp_path / "readings").write_text("".join(f"{value}\n" for value in readings))
    (tmp_path / "cleanup_status").write_text(str(cleanup_status))
    # Probe state lives on disk because command substitutions run in subshells.
    commands = """
    df() {
      local available
      read -r available < readings || return 1
      tail -n +2 readings > remaining
      mv remaining readings
      printf 'Avail\\n%s\\n' "$available"
    }
    sudo() { printf 'reclaim\\n' >> cleanup; return "$(cat cleanup_status)"; }
    """
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", commands + script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == expected_status, result.stderr
    cleanup = tmp_path / "cleanup"
    assert cleanup.exists() is reclaims
    if reclaims:
        assert cleanup.read_text().splitlines() == ["reclaim"]
