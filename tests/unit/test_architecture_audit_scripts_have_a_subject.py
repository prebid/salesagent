"""Structural guard: a committed audit script may not require an input the repo lacks.

Commit b09479143 (2026-08-26) deleted the 40 storyboard re-grounding proposal files
that lived in an agent working directory, with an explicit rule: "committed code that
reads an agent's working directory as data ... is a layering violation by this
repository's own standards". It corrected ``storyboard_roadmap.py`` in the same commit.

``storyboard_reconciliation.py`` was left behind, and nothing noticed for thirteen
days. It declared ``--proposals`` as a REQUIRED path with no default, so its only
possible input was the directory that had just been deleted on purpose; run against an
absent or empty directory it printed "**0 of 40 scenarios assessed** — 40 outstanding"
and exited 0. ``storyboard_roadmap.py``'s own text told readers to run it.

This is the shape salesagent-v03pe collects, one level up: not an instrument answering
a question it cannot answer, but an instrument with no subject at all, still published
as a way to get an answer.

The rule this encodes: every ``scripts/audit/*.py`` CLI takes its inputs from the
repository and the pinned bundle — ``--repo`` and ``--adcp``, both defaulted. A
required path argument means the caller has to supply a tree from somewhere else, and
"somewhere else" is what has no owner and cannot be kept correct.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = REPO_ROOT / "scripts" / "audit"


def _required_path_arguments(module: Path) -> list[str]:
    """Argument names the module declares as ``required=True`` with ``type=Path``."""
    required: list[str] = []
    for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "add_argument":
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        is_required = isinstance(kwargs.get("required"), ast.Constant) and kwargs["required"].value is True
        has_default = "default" in kwargs
        if is_required and not has_default and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                required.append(first.value)
    return required


def test_no_audit_script_requires_an_input_the_repository_does_not_carry() -> None:
    """Every audit CLI's subject is the repo plus the pinned bundle, and both default."""
    offenders = sorted(
        f"{module.relative_to(REPO_ROOT)}: {', '.join(args)}"
        for module in sorted(AUDIT_DIR.glob("*.py"))
        if (args := _required_path_arguments(module))
    )
    assert offenders == [], (
        f"{len(offenders)} audit script(s) declare a required path argument with no default. "
        "The caller must then supply a tree from outside the repository, which has no owner "
        "and cannot be kept correct — storyboard_reconciliation.py pointed at 40 proposal "
        "files that b09479143 had deleted as a layering violation, and went on printing "
        '"0 of 40 scenarios assessed" for thirteen days:\n' + "\n".join(f"  {o}" for o in offenders)
    )


def test_the_guard_reads_add_argument_calls_it_is_meant_to_catch(tmp_path: Path) -> None:
    """Meta-test: a guard that cannot fail is not a guard."""
    subject = tmp_path / "fake_audit.py"
    subject.write_text(
        "import argparse\n"
        "from pathlib import Path\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--proposals', type=Path, required=True)\n"
        "p.add_argument('--repo', type=Path, default=Path.cwd())\n"
        "p.add_argument('--markdown', action='store_true')\n",
        encoding="utf-8",
    )
    assert _required_path_arguments(subject) == ["--proposals"], (
        "the reader must flag the required-and-undefaulted argument and only that one"
    )
