"""Executable-exemption meta-test for the boundary and ownership import bans.

``ruff-boundary.toml`` is a second ruff config, run as its own ``make quality`` line:
``uv run ruff check --config ruff-boundary.toml --no-respect-gitignore src/ scripts/``.
It bans the names that would let a caller re-create the architecture the boundary collapse
removed: every tool's ``_impl``, the private identity resolver, the stamped entry, and
fastmcp's ``ToolError``. ``ruff-ownership.toml`` is the third, run the same way: it bans the
modules that can load a principal or account row, so a tool obtains both from the identity
and nowhere else. ``ruff-serialization.toml`` bans the bare serialization calls outside the
named edges, and ``ruff-environment.toml`` bans ``os.environ`` / ``os.getenv`` outside the
settings loader. Every table is proven here; each is a separate config because ruff exempts
a whole rule per path, so a file exempt from one table stays under the others.

Until this module existed those bans were unproven. ``ruff-egress.toml`` has had a
non-vacuity proof since GH #1589 and this one did not, so a ban could have been misspelled,
shadowed by a per-file-ignore, or silently dropped, and the only symptom would have been a
rule that never fired -- which reads exactly like a rule nobody violates.

Two cases, both shelling out to the real ruff with the real config:

(a) POSITIVE -- every banned entry fires TID251 when imported from a path no exemption
    covers. Parametrized over the table PARSED FROM THE CONFIG, so a ban added later is
    proven automatically and a ban deleted later takes its own case with it.
(b) NEGATIVE -- an innocuous import in the same position yields no TID251, which is what
    makes (a) evidence rather than a rule that fires on everything.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS = (
    REPO_ROOT / "ruff-boundary.toml",
    REPO_ROOT / "ruff-ownership.toml",
    REPO_ROOT / "ruff-serialization.toml",
    REPO_ROOT / "ruff-environment.toml",
)


def _banned_names() -> list[tuple[Path, str]]:
    """Every (config, banned name) pair, read from the configs rather than restated here."""
    pairs: list[tuple[Path, str]] = []
    for config in CONFIGS:
        with config.open("rb") as handle:
            data = tomllib.load(handle)
        table = data["lint"]["flake8-tidy-imports"]["banned-api"]
        pairs.extend((config, dotted) for dotted in sorted(table))
    return pairs


def _ruff_on(source: str, tmp_path: Path, config: Path) -> str:
    """Run the real ruff with the real *config* over *source*, return its output.

    The file is written OUTSIDE ``src/``. Every exemption in the configs is a path pattern
    under ``src/`` or ``scripts/``, so a temp path is covered by none of them -- which is the
    point: the bans are proven at a location where nothing can excuse them.
    """
    probe = tmp_path / "boundary_ban_probe.py"
    probe.write_text(source)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            str(config),
            "--no-respect-gitignore",
            "--ignore-noqa",
            str(probe),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    return result.stdout + result.stderr


@pytest.mark.arch_guard
@pytest.mark.parametrize(("config", "dotted"), _banned_names(), ids=[f"{c.name}:{d}" for c, d in _banned_names()])
def test_every_boundary_ban_actually_fires(config: Path, dotted: str, tmp_path: Path) -> None:
    """A banned name must produce TID251 in ruff's OUTPUT, not merely a non-zero exit."""
    module, _, name = dotted.rpartition(".")
    output = _ruff_on(f"from {module} import {name}\n", tmp_path, config)

    assert "TID251" in output, (
        f"{dotted} is listed in {config.name}'s banned-api table but importing it "
        f"produces no TID251. The ban is not in force -- a misspelled module path, a "
        f"per-file-ignore that is wider than intended, or a rule not selected at all.\n"
        f"ruff said:\n{output}"
    )
    assert dotted in output, (
        f"TID251 fired for {dotted!r} but named something else, so the message a developer "
        f"reads does not identify what they imported.\nruff said:\n{output}"
    )


@pytest.mark.arch_guard
@pytest.mark.parametrize("config", CONFIGS, ids=[c.name for c in CONFIGS])
def test_an_unbanned_import_is_not_reported(config: Path, tmp_path: Path) -> None:
    """The negative half. Without it, a config that flagged EVERY import would pass above."""
    output = _ruff_on("from dataclasses import dataclass\n", tmp_path, config)

    assert "TID251" not in output, (
        f"{config.name} reports TID251 for an ordinary stdlib import, so the positive "
        f"cases above prove nothing about the ban table.\nruff said:\n{output}"
    )
