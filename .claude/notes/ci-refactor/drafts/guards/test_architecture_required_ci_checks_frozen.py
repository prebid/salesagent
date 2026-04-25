"""Guard: ci.yml declares the 11 frozen required-check names per D17.

The check names are a contract with branch protection. Renaming any of them
is an atomic flip handled by PR 3 Phase B; the names cannot drift in code
without coordinating the branch-protection update.
"""

import re

from tests.unit._architecture_helpers import repo_root

# D17 — exact names. Order arbitrary.
_FROZEN_CHECK_NAMES: tuple[str, ...] = (
    "CI / Quality Gate",
    "CI / Type Check",
    "CI / Schema Contract",
    "CI / Unit Tests",
    "CI / Integration Tests",
    "CI / E2E Tests",
    "CI / Admin UI Tests",
    "CI / BDD Tests",
    "CI / Migration Roundtrip",
    "CI / Coverage",
    "CI / Summary",
)


def test_ci_yml_declares_all_frozen_check_names():
    text = (repo_root() / ".github" / "workflows" / "ci.yml").read_text()
    missing: list[str] = []
    for name in _FROZEN_CHECK_NAMES:
        # Match: name: 'CI / Foo'  or  name: "CI / Foo"
        if not re.search(rf"name:\s*['\"]{re.escape(name)}['\"]", text):
            missing.append(name)
    assert not missing, (
        "Frozen required-check name(s) missing from ci.yml — branch protection "
        "drift risk. Add the missing job(s) or coordinate a Phase-B-style flip:\n"
        + "\n".join(f"  - {n}" for n in missing)
    )
