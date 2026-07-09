"""Contract tests for security audit suppression handling."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SECURITY_AUDIT = _REPO_ROOT / "scripts" / "security-audit.sh"
_SECURITY_IGNORES = _REPO_ROOT / "scripts" / "security-ignored-vulns.sh"
_BASH = "/bin/bash" if Path("/bin/bash").exists() else "bash"


def _source_security_ignores() -> tuple[str, str]:
    proc = subprocess.run(
        [
            _BASH,
            "-c",
            (
                "set -euo pipefail; "
                f"source {_SECURITY_IGNORES}; "
                'printf "%s\\n%s\\n" "$UV_SECURE_IGNORED_VULNS" "$PIP_AUDIT_IGNORED_VULNS"'
            ),
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    uv_secure_ignores, pip_audit_ignores = proc.stdout.splitlines()
    return uv_secure_ignores, pip_audit_ignores


def _write_uvx_stub(bin_dir: Path, argv_file: Path) -> None:
    bin_dir.mkdir()
    uvx = bin_dir / "uvx"
    uvx.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import json",
                "import os",
                "import sys",
                "Path = __import__('pathlib').Path",
                "Path(os.environ['UVX_ARGV_FILE']).write_text(json.dumps(sys.argv[1:]))",
            ]
        )
        + "\n"
    )
    uvx.chmod(0o755)


def _write_security_ignore_file(path: Path, uv_ignores: str, pip_ignores: str = "") -> None:
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                f'UV_SECURE_IGNORED_VULNS="{uv_ignores}"',
                f'PIP_AUDIT_IGNORED_VULNS="{pip_ignores}"',
            ]
        )
        + "\n"
    )


def _run_security_audit(tmp_path: Path, uv_ignores: str) -> list[str]:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    audit_script = scripts_dir / "security-audit.sh"
    audit_script.write_text(_SECURITY_AUDIT.read_text())
    audit_script.chmod(0o755)
    _write_security_ignore_file(scripts_dir / "security-ignored-vulns.sh", uv_ignores)
    (tmp_path / "uv.lock").write_text("")

    bin_dir = tmp_path / "bin"
    argv_file = tmp_path / "uvx-argv.json"
    _write_uvx_stub(bin_dir, argv_file)

    proc = subprocess.run(
        [_BASH, str(audit_script), "--no-check-uv-tool"],
        cwd=tmp_path,
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "UVX_ARGV_FILE": str(argv_file),
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    return json.loads(argv_file.read_text())


def test_security_ignored_vulns_are_valid_comma_separated_lists() -> None:
    """Suppression lists may be empty, but populated lists must be compact CSV."""
    for ignore_list in _source_security_ignores():
        if not ignore_list:
            continue

        ids = ignore_list.split(",")
        assert all(ids)
        assert all(ignore_id == ignore_id.strip() for ignore_id in ids)
        assert all(" " not in ignore_id for ignore_id in ids)


def test_security_audit_omits_uv_secure_ignore_flags_when_no_suppressions(tmp_path: Path) -> None:
    """An empty suppression list must not become an empty uv-secure ignore argument."""
    argv = _run_security_audit(tmp_path, uv_ignores="")

    assert argv == [
        "uv-secure",
        "--no-check-uv-tool",
        str(tmp_path / "uv.lock"),
    ]


def test_security_audit_passes_uv_secure_ignore_flags_when_suppressions_exist(tmp_path: Path) -> None:
    """Non-empty suppressions still flow to uv-secure with allow-unused handling."""
    argv = _run_security_audit(tmp_path, uv_ignores="PYSEC-1,GHSA-2")

    assert argv == [
        "uv-secure",
        "--ignore-vulns",
        "PYSEC-1,GHSA-2",
        "--allow-unused-ignores",
        "--no-check-uv-tool",
        str(tmp_path / "uv.lock"),
    ]
