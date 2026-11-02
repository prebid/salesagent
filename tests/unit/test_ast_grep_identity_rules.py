"""Proof that the two identity ast-grep rules fire, and stay silent on the shape they allow.

The ENFORCEMENT is the rule file, run by ``make quality-ci`` as ``ast-grep scan --config
sgconfig.yml``. This module stands to those rules as ``test_ruff_boundary_bans.py`` stands to
``ruff-boundary.toml``: a rule that is misspelled, filed outside ``ruleDirs``, or scoped by a
``files:`` glob that never matches reads exactly like a rule nobody violates, so each is
proven here against a known-bad and a known-good snippet written at the path the rule's
``files:`` glob covers, under a staged copy of the project config — never into the live
tree, where a probe is a file every other guard's scan can list and fail to read once this
case deletes it (``stage_ast_grep_root``). Every case shells out to the real ast-grep with
the real project config.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import stage_ast_grep_root, write_ast_grep_probe

# (rule id, directory the rule's ``files:`` covers, known-bad source, known-good source)
_RULES: tuple[tuple[str, str, str, str], ...] = (
    (
        "resolved-identity-constructed-only-by-its-owners",
        "tests/unit",
        "from src.core.resolved_identity import ResolvedIdentity\n\nidentity = ResolvedIdentity(principal=None)\n",
        "from src.core.resolved_identity import ResolvedIdentity\n\n\ndef f(identity: ResolvedIdentity) -> str | None:\n    return identity.principal_id\n",
    ),
    (
        "impl-signature-is-request-and-identity",
        "src/core/tools",
        "def _probe_impl(req: object, identity: object | None = None) -> None: ...\n",
        "async def _probe_impl(req: object, identity: ResolvedIdentity) -> None: ...\n",
    ),
    (
        "context-is-written-by-the-boundary-alone",
        "src/core/tools",
        "response = build(items=[], context=req.context)\n",
        # A different field: the regex is anchored, so context_id is not context.
        'row = create(context_id="ctx_1")\n',
    ),
)


def _scan(root: Path, rule_id: str, rel_path: str) -> list[dict]:
    """Run the real ast-grep from the staged *root*, project config, one rule, one file; return its matches."""
    binary = Path(sys.executable).parent / "ast-grep"
    assert binary.exists(), f"{binary} missing: ast-grep-cli is not installed in this venv"
    proc = subprocess.run(
        [str(binary), "scan", "--config", "sgconfig.yml", "--filter", f"^{rule_id}$", rel_path, "--json=compact"],
        capture_output=True,
        text=True,
        cwd=root,
        check=False,
    )
    # 0 is clean, 1 is violations; anything else means the rule did not run (3: filter
    # matched no rule, 6: unreadable rule file) and an empty match list proves nothing.
    assert proc.returncode in (0, 1), (
        f"ast-grep did not run {rule_id} (rc={proc.returncode})\n{proc.stdout}\n{proc.stderr}"
    )
    return json.loads(proc.stdout or "[]")


@pytest.mark.arch_guard
@pytest.mark.parametrize(("rule_id", "rel_dir", "bad", "good"), _RULES, ids=[r[0] for r in _RULES])
def test_rule_fires_on_bad_and_not_on_good(tmp_path: Path, rule_id: str, rel_dir: str, bad: str, good: str) -> None:
    root = stage_ast_grep_root(tmp_path)
    for label, source, expect_hit in (("bad", bad, True), ("good", good, False)):
        rel_path = write_ast_grep_probe(root, rel_dir, source, stem="_synthetic_identity_probe")
        matches = _scan(root, rule_id, rel_path)
        assert bool(matches) is expect_hit, (
            f"[{label}] {rule_id} {'did not flag' if expect_hit else 'flagged'}:\n{source}\nmatches={matches}"
        )
