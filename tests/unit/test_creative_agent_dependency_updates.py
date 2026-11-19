"""The creative-agent image's dependency-update merge must never downgrade a package.

``scripts/creative-agent-stack.sh`` embeds a Node script (the ``MERGE`` heredoc in
``_dockerfile_with_dependency_updates``) that writes a list of fixed versions into the
reference agent's ``package.json`` before the lockfile is regenerated. Once a later
``ADCP_PIN`` locks one of those packages at the listed version or newer, the entry is stale
and would set the package back, so the merge must stop and name the entry instead.

These tests run the real embedded script with ``node`` against small ``package.json`` /
``package-lock.json`` fixtures, the same two files the image's first stage copies in. Node is
on the CI runner and in ``Dockerfile.test``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STACK_SCRIPT = REPO_ROOT / "scripts" / "creative-agent-stack.sh"


def _merge_script() -> str:
    lines = STACK_SCRIPT.read_text(encoding="utf-8").splitlines()
    start = lines.index("COPY <<'MERGE' /tmp/salesagent-dependency-updates.cjs") + 1
    end = lines.index("MERGE", start)
    return "\n".join(lines[start:end]) + "\n"


def _lock(**packages: str) -> dict:
    return {
        "lockfileVersion": 3,
        "packages": {"": {"name": "app"}, **{path: {"version": v} for path, v in packages.items()}},
    }


def _run(tmp_path: Path, package: dict, lock: dict, updates: dict) -> subprocess.CompletedProcess[str]:
    (tmp_path / "merge.cjs").write_text(_merge_script(), encoding="utf-8")
    (tmp_path / "updates.json").write_text(json.dumps(updates), encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps(package), encoding="utf-8")
    (tmp_path / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    return subprocess.run(
        ["node", "merge.cjs", "updates.json"], cwd=tmp_path, capture_output=True, text=True, check=False
    )


def test_older_locked_versions_are_updated(tmp_path: Path) -> None:
    package = {"dependencies": {"multer": "^2.4.0", "undici": "^8.10.2"}}
    lock = _lock(
        **{
            "node_modules/multer": "2.4.0",
            "node_modules/undici": "8.10.2",
            "node_modules/@slack/bolt/node_modules/undici": "7.29.0",
            "node_modules/hono": "4.13.1",
        }
    )
    updates = {"multer": "2.5.0", "undici@8": "8.12.2", "undici@7": "7.30.1", "hono": "4.13.16"}

    proc = _run(tmp_path, package, lock, updates)

    assert proc.returncode == 0, proc.stderr
    merged = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert merged["dependencies"] == {"multer": "2.5.0", "undici": "8.12.2"}
    assert merged["overrides"] == {
        "multer": "$multer",
        "undici@8": "$undici",
        "undici@7": "7.30.1",
        "hono": "4.13.16",
    }


def test_entry_the_lock_already_meets_stops_the_build(tmp_path: Path) -> None:
    package = {"dependencies": {"multer": "^2.7.0", "undici": "^8.14.0"}}
    lock = _lock(**{"node_modules/multer": "2.7.0", "node_modules/undici": "8.14.0"})
    original = json.dumps(package)

    proc = _run(tmp_path, package, lock, {"multer": "2.5.0", "undici@8": "8.12.2", "hono": "4.13.16"})

    assert proc.returncode != 0
    assert "multer" in proc.stderr
    assert "undici@8" in proc.stderr
    assert "hono" not in proc.stderr
    assert (tmp_path / "package.json").read_text(encoding="utf-8") == original


def test_equal_locked_version_is_stale(tmp_path: Path) -> None:
    lock = _lock(**{"node_modules/a/node_modules/qs": "6.16.1"})

    proc = _run(tmp_path, {"dependencies": {}}, lock, {"qs@6": "6.16.1"})

    assert proc.returncode != 0
    assert "qs@6" in proc.stderr


def test_entry_still_needed_by_one_copy_is_kept(tmp_path: Path) -> None:
    lock = _lock(
        **{
            "node_modules/brace-expansion": "1.1.21",
            "node_modules/fstream/node_modules/brace-expansion": "1.1.18",
        }
    )

    proc = _run(tmp_path, {"dependencies": {}}, lock, {"brace-expansion@1": "1.1.21"})

    assert proc.returncode == 0, proc.stderr


def test_newer_copy_on_another_line_does_not_count(tmp_path: Path) -> None:
    lock = _lock(
        **{
            "node_modules/undici": "8.14.0",
            "node_modules/@adcp/sdk/node_modules/undici": "6.28.0",
        }
    )

    proc = _run(tmp_path, {"dependencies": {}}, lock, {"undici@6": "6.29.1"})

    assert proc.returncode == 0, proc.stderr


def test_newer_copy_of_an_unqualified_entry_is_stale(tmp_path: Path) -> None:
    lock = _lock(**{"node_modules/hono": "4.13.1", "node_modules/x/node_modules/hono": "4.14.0"})

    proc = _run(tmp_path, {"dependencies": {}}, lock, {"hono": "4.13.16"})

    assert proc.returncode != 0
    assert "hono" in proc.stderr


def _npm_version() -> str:
    for line in STACK_SCRIPT.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith('local npm_version="'):
            return line.split('"')[1]
    raise AssertionError("npm_version not found in creative-agent-stack.sh")


def _bump(version: str, index: int) -> str:
    parts = [int(p) for p in version.split(".")]
    parts[index] += 1
    return ".".join(str(p) for p in parts[: index + 1] + [0] * (2 - index))


def _run_npm_check(tmp_path: Path, bundled: str) -> subprocess.CompletedProcess[str]:
    # The runtime stage writes these three files before `npm install -g`: an empty package.json,
    # a lock naming the base image's bundled npm, and an "npm" entry for npm_version.
    lock = {"packages": {"node_modules/npm": {"version": bundled}}}
    return _run(tmp_path, {}, lock, {"npm": _npm_version()})


def test_bundled_npm_below_npm_version_is_replaced(tmp_path: Path) -> None:
    older = "11.6.2"
    assert older != _npm_version()

    proc = _run_npm_check(tmp_path, older)

    assert proc.returncode == 0, proc.stderr


def test_bundled_npm_at_or_above_npm_version_stops_the_build(tmp_path: Path) -> None:
    for bundled in (_npm_version(), _bump(_npm_version(), 1), _bump(_npm_version(), 0)):
        proc = _run_npm_check(tmp_path, bundled)

        assert proc.returncode != 0, bundled
        assert "npm (" in proc.stderr, proc.stderr
        assert bundled in proc.stderr
