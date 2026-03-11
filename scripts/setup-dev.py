#!/usr/bin/env python3
"""One-command developer bootstrap for the Prebid Sales Agent.

Usage:
    uv run python scripts/setup-dev.py

Idempotent: safe to run repeatedly. Existing .env values are preserved.
Pure functions are importable and unit-testable.
"""

from __future__ import annotations

import re
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT_DIR / ".env"
ENV_TEMPLATE = ROOT_DIR / ".env.template"
HEALTH_URL = "http://localhost:{port}/health"
DEFAULT_PORT = 8000
HEALTH_TIMEOUT_SECONDS = 120
HEALTH_POLL_INTERVAL = 3

PREREQUISITES: list[Prerequisite] = []  # populated after class definition


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Prerequisite:
    """A required external tool with version check."""

    name: str
    check_cmd: list[str]
    version_pattern: str | None = None
    min_version: tuple[int, ...] | None = None
    install_hint: str = ""


@dataclass
class StepResult:
    """Outcome of a single setup step."""

    name: str
    ok: bool
    message: str
    skipped: bool = False


@dataclass
class SetupReport:
    """Aggregated results from a full setup run."""

    steps: list[StepResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return all(s.ok for s in self.steps)

    def add(self, result: StepResult) -> StepResult:
        self.steps.append(result)
        return result


# ---------------------------------------------------------------------------
# Pure helper functions (importable / testable)
# ---------------------------------------------------------------------------


def parse_version(version_string: str, pattern: str) -> tuple[int, ...] | None:
    """Extract a version tuple from a string using a regex pattern.

    The pattern must have a capture group named 'version' or a single group.
    """
    match = re.search(pattern, version_string)
    if not match:
        return None
    version_text = match.group("version") if "version" in match.groupdict() else match.group(1)
    try:
        return tuple(int(x) for x in version_text.split("."))
    except (ValueError, AttributeError):
        return None


def check_version_meets_minimum(version: tuple[int, ...], minimum: tuple[int, ...]) -> bool:
    """Return True if *version* >= *minimum* using tuple comparison."""
    return version >= minimum


def _parse_env_lines(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines, skipping comments/blanks. Strips matching outer quotes."""
    entries: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        sep = line.find("=")
        if sep == -1:
            continue
        key = line[:sep].strip()
        value = line[sep + 1 :].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        entries[key] = value
    return entries


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict, preserving values. Comments/blanks skipped."""
    if not path.exists():
        return {}
    return _parse_env_lines(path.read_text())


def merge_env(existing: dict[str, str], defaults: dict[str, str]) -> dict[str, str]:
    """Merge *defaults* into *existing* without overwriting existing keys."""
    merged = dict(defaults)
    merged.update(existing)
    return merged


def generate_secret_key(nbytes: int = 32) -> str:
    """Generate a hex-encoded random secret."""
    return secrets.token_hex(nbytes)


def ensure_env_secrets(env: dict[str, str]) -> dict[str, str]:
    """Ensure critical secrets exist, generating stable values if missing."""
    result = dict(env)
    if not result.get("FLASK_SECRET_KEY"):
        result["FLASK_SECRET_KEY"] = generate_secret_key()
    if not result.get("ENCRYPTION_KEY"):
        from cryptography.fernet import Fernet

        result["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    return result


def serialize_env(entries: dict[str, str]) -> str:
    """Serialize a dict to .env file content, sorted by key for stability."""
    lines = [f"{k}={v}" for k, v in sorted(entries.items())]
    return "\n".join(lines) + "\n"


def build_env_from_template(template_path: Path) -> dict[str, str]:
    """Extract uncommented KEY=VALUE pairs from the template as defaults."""
    if not template_path.exists():
        return {}
    return _parse_env_lines(template_path.read_text())


def render_env_from_template(template_path: Path, values: dict[str, str]) -> str:
    """Render .env content by overlaying values onto the template structure.

    Preserves the template's comments, sections, and key ordering.
    Uncomments and sets values for keys present in *values*.
    Appends any extra keys not found in the template at the end.
    """
    if not template_path.exists():
        return serialize_env(values)

    lines = template_path.read_text().splitlines()
    output: list[str] = []
    used_keys: set[str] = set()

    for line in lines:
        stripped = line.strip()

        # Check if this is a commented-out KEY=VALUE line (e.g., "# KEY=value")
        if stripped.startswith("#"):
            uncommented = stripped.lstrip("#").strip()
            sep = uncommented.find("=")
            if sep != -1:
                key = uncommented[:sep].strip()
                if key and key in values:
                    output.append(f"{key}={values[key]}")
                    used_keys.add(key)
                    continue

        # Check if this is an uncommented KEY=VALUE line
        if stripped and not stripped.startswith("#"):
            sep = stripped.find("=")
            if sep != -1:
                key = stripped[:sep].strip()
                if key and key in values:
                    output.append(f"{key}={values[key]}")
                    used_keys.add(key)
                    continue

        # Pass through as-is (comments, blank lines, unchanged keys)
        output.append(line)

    # Append any extra keys not found in the template
    extra_keys = sorted(k for k in values if k not in used_keys)
    if extra_keys:
        output.append("")
        output.append("# ============================================")
        output.append("# [AUTO-GENERATED] Additional settings")
        output.append("# ============================================")
        for key in extra_keys:
            output.append(f"{key}={values[key]}")

    return "\n".join(output) + "\n"


def get_conductor_port(env: dict[str, str]) -> int:
    """Determine the port from CONDUCTOR_PORT or default."""
    try:
        return int(env.get("CONDUCTOR_PORT", str(DEFAULT_PORT)))
    except (ValueError, TypeError):
        return DEFAULT_PORT


# ---------------------------------------------------------------------------
# Side-effectful step functions
# ---------------------------------------------------------------------------


def _run(cmd: list[str], capture: bool = True, check: bool = True, **kwargs) -> subprocess.CompletedProcess[str]:  # type: ignore[type-arg]
    """Run a subprocess with sensible defaults."""
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
        cwd=str(ROOT_DIR),
        **kwargs,
    )


def _print_step(number: int, total: int, name: str) -> None:
    """Print a step header."""
    print(f"\n[{number}/{total}] {name}")


def assert_prerequisites() -> StepResult:
    """Check that all required tools are installed and meet version requirements."""
    missing: list[str] = []
    for prereq in PREREQUISITES:
        try:
            result = _run(prereq.check_cmd, check=False)
            if result.returncode != 0:
                hint = f"  -> {prereq.install_hint}" if prereq.install_hint else ""
                missing.append(f"  {prereq.name}: not found or not working{hint}")
                continue
            if prereq.version_pattern and prereq.min_version:
                output = result.stdout + result.stderr
                version = parse_version(output, prereq.version_pattern)
                if version is None:
                    missing.append(f"  {prereq.name}: could not determine version")
                elif not check_version_meets_minimum(version, prereq.min_version):
                    min_str = ".".join(str(x) for x in prereq.min_version)
                    got_str = ".".join(str(x) for x in version)
                    missing.append(f"  {prereq.name}: requires >= {min_str}, found {got_str}")
        except FileNotFoundError:
            hint = f"  -> {prereq.install_hint}" if prereq.install_hint else ""
            missing.append(f"  {prereq.name}: not installed{hint}")

    if missing:
        detail = "\n".join(missing)
        return StepResult(
            name="prerequisites",
            ok=False,
            message=f"Missing prerequisites:\n{detail}",
        )
    return StepResult(name="prerequisites", ok=True, message="All prerequisites met")


def ensure_dependencies() -> StepResult:
    """Run `uv sync` to install/update Python dependencies."""
    try:
        _run(["uv", "sync"])
        return StepResult(name="dependencies", ok=True, message="Dependencies synced")
    except subprocess.CalledProcessError as exc:
        return StepResult(name="dependencies", ok=False, message=f"uv sync failed: {exc.stderr or exc}")


def ensure_env() -> StepResult:
    """Create or update .env from template, preserving existing values and template structure."""
    existing = load_env_file(ENV_FILE)
    defaults = build_env_from_template(ENV_TEMPLATE)
    merged = merge_env(existing, defaults)
    merged = ensure_env_secrets(merged)

    if existing == merged:
        return StepResult(name="env", ok=True, message=".env already up to date", skipped=True)

    ENV_FILE.write_text(render_env_from_template(ENV_TEMPLATE, merged))
    verb = "Updated" if existing else "Created"
    return StepResult(name="env", ok=True, message=f"{verb} .env ({len(merged)} keys)")


def ensure_pre_commit() -> StepResult:
    """Install pre-commit hooks if not already installed."""
    # Check both default hooks dir and custom hooksPath (e.g. beads uses .beads/hooks)
    hook_file = ROOT_DIR / ".git" / "hooks" / "pre-commit"
    try:
        hooks_path = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            capture_output=True,
            text=True,
            cwd=ROOT_DIR,
        )
        if hooks_path.returncode == 0 and hooks_path.stdout.strip():
            custom_hook = ROOT_DIR / hooks_path.stdout.strip() / "pre-commit"
            if custom_hook.exists():
                return StepResult(
                    name="pre-commit",
                    ok=True,
                    message=f"Pre-commit hooks managed by {hooks_path.stdout.strip()}",
                    skipped=True,
                )
    except OSError:
        pass
    if hook_file.exists():
        return StepResult(
            name="pre-commit",
            ok=True,
            message="Pre-commit hooks already installed",
            skipped=True,
        )
    try:
        _run(["uvx", "pre-commit", "install"])
        return StepResult(name="pre-commit", ok=True, message="Pre-commit hooks installed")
    except subprocess.CalledProcessError as exc:
        return StepResult(
            name="pre-commit",
            ok=False,
            message=f"pre-commit install failed: {exc.stderr or exc}",
        )


def ensure_tox() -> StepResult:
    """Check if tox is available and suggest installation if not."""
    if shutil.which("tox"):
        return StepResult(name="tox", ok=True, message="tox is available", skipped=True)
    return StepResult(
        name="tox",
        ok=True,
        message="tox not found (optional). Install with: uv tool install tox --with tox-uv",
        skipped=True,
    )


def start_infrastructure() -> StepResult:
    """Start Docker Compose services."""
    try:
        _run(["docker", "compose", "up", "-d"])
        return StepResult(name="infrastructure", ok=True, message="Docker services started")
    except subprocess.CalledProcessError as exc:
        return StepResult(
            name="infrastructure",
            ok=False,
            message=f"docker compose up failed: {exc.stderr or exc}",
        )


def wait_for_migrations() -> StepResult:
    """Wait for db-init container to complete (migrations)."""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            result = _run(
                ["docker", "compose", "ps", "-a", "--format", "{{.Service}} {{.State}}"],
                check=False,
            )
            for line in result.stdout.strip().splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0] == "db-init":
                    state = parts[1].lower()
                    if "exited" in state:
                        # Check exit code
                        inspect = _run(
                            ["docker", "compose", "ps", "-a", "--format", "{{.Service}} {{.ExitCode}}"],
                            check=False,
                        )
                        for iline in inspect.stdout.strip().splitlines():
                            iparts = iline.strip().split()
                            if len(iparts) >= 2 and iparts[0] == "db-init":
                                if iparts[1] == "0":
                                    return StepResult(
                                        name="migrations",
                                        ok=True,
                                        message="Database migrations completed",
                                    )
                                else:
                                    return StepResult(
                                        name="migrations",
                                        ok=False,
                                        message=f"db-init exited with code {iparts[1]}",
                                    )
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        time.sleep(3)

    return StepResult(
        name="migrations",
        ok=False,
        message="Timed out waiting for db-init to complete",
    )


def verify_setup(port: int) -> StepResult:
    """Poll the health endpoint until it responds 200."""
    url = HEALTH_URL.format(port=port)
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    last_error = ""
    while time.monotonic() < deadline:
        try:
            result = _run(
                ["curl", "-sf", "--max-time", "5", url],
                check=False,
            )
            if result.returncode == 0:
                return StepResult(name="health", ok=True, message=f"Health check passed ({url})")
            last_error = result.stderr.strip() if result.stderr else f"HTTP error (curl exit {result.returncode})"
        except FileNotFoundError:
            last_error = "curl not found"
            break
        time.sleep(HEALTH_POLL_INTERVAL)

    return StepResult(
        name="health",
        ok=False,
        message=f"Health check failed after {HEALTH_TIMEOUT_SECONDS}s: {last_error}",
    )


def print_summary(report: SetupReport, port: int) -> None:
    """Print a human-readable summary."""
    print("\n" + "=" * 60)
    if report.success:
        print("Setup complete!")
    else:
        print("Setup finished with errors.")
    print("=" * 60)

    for step in report.steps:
        icon = "OK" if step.ok else "FAIL"
        skip = " (no change)" if step.skipped else ""
        print(f"  [{icon}] {step.name}{skip}: {step.message}")

    if report.success:
        print(f"\nAdmin UI:   http://localhost:{port}/admin/")
        print(f"MCP Server: http://localhost:{port}/mcp/")
        print(f"A2A Server: http://localhost:{port}/a2a")
        print("\nLogin: Click 'Log in to Dashboard' (password: test123)")
        print("\nNext steps:")
        print("  docker compose logs -f    # View logs")
        print("  make quality              # Run checks before committing")
        print("  docker compose down       # Stop services")


# ---------------------------------------------------------------------------
# Prerequisites definitions
# ---------------------------------------------------------------------------

PREREQUISITES = [
    Prerequisite(
        name="Python",
        check_cmd=[sys.executable, "--version"],
        version_pattern=r"Python (?P<version>\d+\.\d+)",
        min_version=(3, 12),
        install_hint="https://www.python.org/downloads/",
    ),
    Prerequisite(
        name="Docker",
        check_cmd=["docker", "--version"],
        install_hint="https://docs.docker.com/get-docker/",
    ),
    Prerequisite(
        name="Docker Compose",
        check_cmd=["docker", "compose", "version"],
        install_hint="Docker Compose is included with Docker Desktop",
    ),
    Prerequisite(
        name="uv",
        check_cmd=["uv", "--version"],
        install_hint="curl -LsSf https://astral.sh/uv/install.sh | sh",
    ),
    Prerequisite(
        name="git",
        check_cmd=["git", "--version"],
        install_hint="https://git-scm.com/downloads",
    ),
]


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def run_setup() -> SetupReport:
    """Execute all setup steps in order. Returns a report."""
    report = SetupReport()
    total = 8
    step_num = 0

    # Step 1: Prerequisites
    step_num += 1
    _print_step(step_num, total, "Checking prerequisites...")
    result = assert_prerequisites()
    report.add(result)
    print(f"  {result.message}")
    if not result.ok:
        print("\nFix the above issues and re-run this script.")
        return report

    # Step 2: Dependencies
    step_num += 1
    _print_step(step_num, total, "Syncing dependencies...")
    result = ensure_dependencies()
    report.add(result)
    print(f"  {result.message}")
    if not result.ok:
        return report

    # Step 3: Environment
    step_num += 1
    _print_step(step_num, total, "Ensuring .env configuration...")
    result = ensure_env()
    report.add(result)
    print(f"  {result.message}")

    # Step 4: Pre-commit hooks
    step_num += 1
    _print_step(step_num, total, "Setting up pre-commit hooks...")
    result = ensure_pre_commit()
    report.add(result)
    print(f"  {result.message}")

    # Step 5: Tox check
    step_num += 1
    _print_step(step_num, total, "Checking tox availability...")
    result = ensure_tox()
    report.add(result)
    print(f"  {result.message}")

    # Step 6: Infrastructure
    step_num += 1
    _print_step(step_num, total, "Starting Docker infrastructure...")
    result = start_infrastructure()
    report.add(result)
    print(f"  {result.message}")
    if not result.ok:
        return report

    # Step 7: Migrations
    step_num += 1
    _print_step(step_num, total, "Waiting for database migrations...")
    result = wait_for_migrations()
    report.add(result)
    print(f"  {result.message}")
    if not result.ok:
        return report

    # Step 8: Health check
    env = load_env_file(ENV_FILE)
    port = get_conductor_port(env)
    step_num += 1
    _print_step(step_num, total, f"Verifying setup (http://localhost:{port}/health)...")
    result = verify_setup(port)
    report.add(result)
    print(f"  {result.message}")

    return report


def main() -> int:
    """Entry point. Returns exit code."""
    print("Prebid Sales Agent — Developer Setup")
    print("=" * 60)

    report = run_setup()

    env = load_env_file(ENV_FILE)
    port = get_conductor_port(env)
    print_summary(report, port)

    return 0 if report.success else 1


if __name__ == "__main__":
    sys.exit(main())
