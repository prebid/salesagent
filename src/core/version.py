"""Version and build metadata for the AdCP Sales Agent."""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_SHA_LEN = 7


def get_version() -> str:
    """Get the sales agent version from package metadata or pyproject.toml.

    Returns:
        Version string (e.g., "1.2.0")
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("adcp-sales-agent")
    except PackageNotFoundError:
        # Package not installed (development checkout) — fall through.
        pass

    try:
        import tomllib

        pyproject_path = _PROJECT_ROOT / "pyproject.toml"
        if pyproject_path.exists():
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
                return data.get("project", {}).get("version", "0.0.0")
    except (FileNotFoundError, tomllib.TOMLDecodeError, KeyError) as e:
        logger.debug("Failed to read version from pyproject.toml: %s", e)

    return "0.0.0"


def _git(*args: str) -> str | None:
    """Run a git command in the project root. Returns stripped stdout or None."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            out = result.stdout.strip()
            return out or None
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.debug("git %s failed: %s", " ".join(args), e)
    return None


def get_git_sha() -> str | None:
    """Short git commit SHA (7 chars). Reads APP_GIT_SHA env, falls back to ``git``."""
    env_sha = os.environ.get("APP_GIT_SHA", "").strip()
    if env_sha and env_sha != "unknown":
        return env_sha[:_SHA_LEN]
    sha = _git("rev-parse", f"--short={_SHA_LEN}", "HEAD")
    return sha[:_SHA_LEN] if sha else None


def get_git_branch() -> str | None:
    """Git branch (or tag). Reads APP_GIT_BRANCH env, falls back to ``git``."""
    env_branch = os.environ.get("APP_GIT_BRANCH", "").strip()
    if env_branch and env_branch != "unknown":
        return env_branch
    return _git("rev-parse", "--abbrev-ref", "HEAD")


@dataclass(frozen=True)
class BuildInfo:
    version: str
    git_sha: str | None
    git_branch: str | None

    def _branch_label(self) -> str | None:
        """Return the branch only when it adds information.

        Suppressed when:
        - missing
        - default branches (main, master)
        - detached HEAD
        - branch equals the SHA (CI tag builds)
        - branch matches the version tag (e.g. ``v1.7.0``)
        """
        b = self.git_branch
        if not b:
            return None
        if b in {"HEAD", "main", "master"}:
            return None
        if self.git_sha and b == self.git_sha:
            return None
        if b == f"v{self.version}":
            return None
        return b

    @property
    def display(self) -> str:
        """Full one-line label: ``v1.7.0 · abc1234 · branch``."""
        parts = [f"v{self.version}"]
        if self.git_sha:
            parts.append(self.git_sha)
        branch = self._branch_label()
        if branch:
            parts.append(branch)
        return " · ".join(parts)

    @property
    def display_short(self) -> str:
        """Version + SHA only — used for embedded surfaces where leaking
        the operator's internal branch names to publishers isn't desirable."""
        parts = [f"v{self.version}"]
        if self.git_sha:
            parts.append(self.git_sha)
        return " · ".join(parts)


def get_build_info() -> BuildInfo:
    return BuildInfo(
        version=get_version(),
        git_sha=get_git_sha(),
        git_branch=get_git_branch(),
    )
