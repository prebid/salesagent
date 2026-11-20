"""Proof that the admin blanket-``except`` ban fires on every form it names, and on nothing else.

The ENFORCEMENT is ``.ast-grep/rules/admin-no-blanket-except.yml``, run by ``make quality`` /
``make quality-ci`` as ``ast-grep scan --config sgconfig.yml``. This module stands to it as
``tests/unit/test_ast_grep_identity_rules.py`` stands to the identity rules: a rule that is
misspelled, filed outside ``ruleDirs``, or scoped by a ``files:`` glob that never matches
reads exactly like a rule nobody violates. Each case writes a probe at the path the rule's
``files:`` glob covers, under a staged copy of the project config (``stage_ast_grep_root``),
and runs the real ast-grep on it. It does not re-implement the detection.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import stage_ast_grep_root, write_ast_grep_probe

RULE_ID = "admin-no-blanket-except"

_BLANKET_FORMS = (
    "except Exception:",
    "except Exception as e:",
    "except BaseException:",
    "except BaseException as e:",
    "except:",
    "except (ValueError, Exception):",
    "except (KeyError, BaseException) as e:",
    "except ((Exception)):",
    "except (ValueError, (KeyError, Exception)):",
    "except builtins.Exception:",
)

_NAMED_FORMS = (
    "except ValueError:",
    "except ValueError as e:",
    "except (ValueError, KeyError):",
    "except (ValueError, KeyError) as e:",
)


def _source(handler: str) -> str:
    return f"try:\n    work()\n{handler}\n    pass\n"


def _scan(root: Path, rel_path: str) -> subprocess.CompletedProcess[str]:
    binary = Path(sys.executable).parent / "ast-grep"
    assert binary.exists(), f"{binary} missing: ast-grep-cli is not installed in this venv"
    return subprocess.run(
        [str(binary), "scan", "--config", "sgconfig.yml", "--filter", f"^{RULE_ID}$", rel_path, "--json=compact"],
        capture_output=True,
        text=True,
        cwd=root,
        check=False,
    )


def _matches(proc: subprocess.CompletedProcess[str]) -> list[dict]:
    # 0 is clean, 1 is violations; anything else means the rule did not run (3: the filter
    # matched no rule, 6: a rule file could not be read), and an empty stdout would then
    # read as "no matches" for every negative case below.
    assert proc.returncode in (0, 1), f"ast-grep exited {proc.returncode}: {proc.stderr[:400]!r}"
    return json.loads(proc.stdout or "[]")


@pytest.mark.parametrize("handler", _BLANKET_FORMS)
def test_a_blanket_handler_in_src_admin_is_refused(tmp_path: Path, handler: str) -> None:
    root = stage_ast_grep_root(tmp_path)
    rel_path = write_ast_grep_probe(root, "src/admin/blueprints", _source(handler))

    proc = _scan(root, rel_path)
    found = _matches(proc)

    assert [m["ruleId"] for m in found] == [RULE_ID], f"{handler!r}: {found}"
    assert proc.returncode == 1, "a violation must fail the build"


@pytest.mark.parametrize("handler", _NAMED_FORMS)
def test_a_named_handler_in_src_admin_is_clean(tmp_path: Path, handler: str) -> None:
    root = stage_ast_grep_root(tmp_path)
    rel_path = write_ast_grep_probe(root, "src/admin/blueprints", _source(handler))

    proc = _scan(root, rel_path)

    assert _matches(proc) == [], handler
    assert proc.returncode == 0


def test_a_blanket_handler_outside_src_admin_is_not_this_rules_business(tmp_path: Path) -> None:
    """``best_effort`` in src/core/operator_errors.py is the sanctioned catch-all, so the scope is src/admin alone."""
    root = stage_ast_grep_root(tmp_path)
    rel_path = write_ast_grep_probe(root, "src/core", _source("except Exception as e:"))

    proc = _scan(root, rel_path)

    assert _matches(proc) == []
    assert proc.returncode == 0
