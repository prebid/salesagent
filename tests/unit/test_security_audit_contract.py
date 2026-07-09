"""Contract tests for security audit suppression handling."""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SECURITY_AUDIT = _REPO_ROOT / "scripts" / "security-audit.sh"
_SECURITY_IGNORES = _REPO_ROOT / "scripts" / "security-ignored-vulns.sh"


def _source_security_ignores() -> tuple[str, str]:
    proc = subprocess.run(
        [
            "bash",
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


def test_security_ignored_vulns_has_no_active_suppressions() -> None:
    """Recent CVE suppressions were removed, so both tool lists must be empty."""
    assert _source_security_ignores() == ("", "")


def test_security_audit_omits_uv_secure_ignore_flags_when_no_suppressions() -> None:
    """An empty suppression list must not become an empty uv-secure ignore argument."""
    script = _SECURITY_AUDIT.read_text()

    assert '[[ -n "$UV_SECURE_IGNORED_VULNS" ]]' in script
    assert 'exec uvx uv-secure --ignore-vulns "$UV_SECURE_IGNORED_VULNS"' not in script
