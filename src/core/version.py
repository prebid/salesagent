"""Version utilities for the AdCP Sales Agent."""

import logging

logger = logging.getLogger(__name__)


def get_version() -> str:
    """Get the sales agent version from package metadata or pyproject.toml.

    Returns:
        Version string (e.g., "1.2.0")
    """
    # Try importlib.metadata first (works when package is installed)
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("adcp-sales-agent")
    except PackageNotFoundError:
        # Package not installed, fall through to pyproject.toml
        pass

    # Fall back to reading pyproject.toml directly (works in development)
    try:
        import tomllib
        from pathlib import Path

        # Look for pyproject.toml relative to this file
        project_root = Path(__file__).parent.parent.parent
        pyproject_path = project_root / "pyproject.toml"

        if pyproject_path.exists():
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
                return data.get("project", {}).get("version", "0.0.0")
    except (FileNotFoundError, tomllib.TOMLDecodeError, KeyError) as e:
        logger.debug("Failed to read version from pyproject.toml: %s", e)

    return "0.0.0"
