"""Run a bash script with stub commands placed ahead of everything else on ``PATH``.

Tests that exercise a real shell script (a workflow ``run:`` block, ``run_all_tests.sh``)
replace its external commands (``docker``, ``cosign``, ``curl``, ...) with small bash
stubs, then read what the script did from its exit code, its output, and any log the
stubs write.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path


def run_bash_with_stubs(
    args: Sequence[str],
    *,
    stub_dir: Path,
    stubs: Mapping[str, str],
    env: Mapping[str, str] | None = None,
    unset: Iterable[str] = (),
    cwd: Path | None = None,
    timeout: float = 60,
) -> subprocess.CompletedProcess[str]:
    """Write each ``stubs`` entry as an executable in *stub_dir*, then run ``bash *args``.

    *stub_dir* is prepended to the inherited ``PATH`` with ``os.pathsep``, so the stubs
    win on Linux and under Git Bash on Windows alike. *env* is laid over the inherited
    environment and *unset* names are removed from it, so a value the caller's shell
    happens to export cannot reach the script.
    """
    stub_dir.mkdir(parents=True, exist_ok=True)
    for name, body in stubs.items():
        stub = stub_dir / name
        stub.write_text(body, encoding="utf-8", newline="\n")
        stub.chmod(0o755)

    run_env = {**os.environ, **(env or {})}
    for name in unset:
        run_env.pop(name, None)
    run_env["PATH"] = f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    # The bash on PATH, by full path: on Windows a bare "bash" can resolve to WSL's
    # System32\bash.exe ahead of Git Bash.
    return subprocess.run(
        [shutil.which("bash") or "bash", *args],
        cwd=cwd,
        env=run_env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
