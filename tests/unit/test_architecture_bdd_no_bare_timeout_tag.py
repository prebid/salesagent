"""Guard: BDD feature tags must not use bare ``@timeout``.

``pytest-bdd`` turns Gherkin tags into ``pytest.mark.<tag>``. The bare tag
``@timeout`` becomes ``pytest.mark.timeout`` with no arguments. With
``pytest-timeout`` installed, that marker **requires** a seconds argument, and
collection crashes with::

    TypeError: Timeout marker must have at least one argument

That INTERNALERROR aborted BDD Shard 1 and BDD In-Network (e2e_rest) on PR
#1933 after ``tests/bdd/test_uc021_preview_creative.py`` started binding
``BR-UC-021-preview-creative.feature`` (which had ``@timeout`` on the agent
timeout scenario). Use a descriptive tag such as ``@T-UC-021-ext-j-timeout``
instead — never the plugin marker name alone.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Word-boundary bare @timeout tag (not @T-…-timeout / @agent-timeout).
_BARE_TIMEOUT_TAG = re.compile(r"(^|\s)@timeout(\s|$)")


def _feature_files() -> list[Path]:
    """Repo-tracked ``tests/bdd/features/*.feature`` via ``git ls-files``."""
    out = subprocess.check_output(
        ["git", "ls-files", "tests/bdd/features/*.feature"],
        cwd=_REPO_ROOT,
        text=True,
    )
    paths = [Path(line) for line in out.splitlines() if line.strip()]
    assert paths, "empty feature scan — git ls-files returned nothing"
    return paths


@pytest.mark.arch_guard
def test_bdd_features_forbid_bare_timeout_tag() -> None:
    """No feature may tag a scenario with bare ``@timeout`` (pytest-timeout clash)."""
    hits: list[str] = []
    for rel in _feature_files():
        text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            if _BARE_TIMEOUT_TAG.search(line):
                hits.append(f"{rel}:{i}: {line.strip()}")
    assert not hits, (
        "Bare @timeout BDD tag(s) collide with pytest-timeout "
        "(Timeout marker must have at least one argument):\n" + "\n".join(f"  {h}" for h in hits)
    )
