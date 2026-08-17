"""Guard: no BDD xfail reason may attribute its failure to #1462.

#1462 reported that the ``get_media_buy_delivery`` request path dropped
``attribution_window.post_click`` before validation could run. Re-derived
against main on 2026-07-27: it never reproduced, on any transport. Every
transport builds its request through the one shared
``_build_get_media_buy_delivery_request``, which preserves ``post_click``, and
a direct in-process probe confirms ``_validate_attribution_window`` rejects
``{"interval": 2, "unit": "campaign"}`` as it should.

The reported symptom was manufactured in the BDD step layer: the generic
``with {request_params}`` step matched the specific attribution step's text and
``_parse_request_params`` harvested only ``key=value`` pairs, so the space-form
window yielded ``{}`` and the request dispatched with NO attribution_window at
all. Production then echoed ``post_click=None, model=last_touch`` — the
signature of "the field never arrived", not "post_click was stripped". #1545
narrowed that step to the ``\\w+=`` form, so the cause is dead everywhere.

So a reason string blaming #1462 is always wrong, and not merely because BDD
skips the IMPL transport: it names a request-path defect that never existed.
Do not replace it with the step-shadowing attribution either — that cause is
also dead. Find what the failing transport actually exercises today.

Scanning approach: AST, over every ``*.py`` under ``tests/bdd/``. The scan is
deliberately **not** scoped to ``pytest.mark.xfail(reason=...)`` calls, because
this repo routes xfail reasons through three different mechanisms and a
call-scoped scan reached only one of them:

1. ``pytest.mark.xfail(reason="...literal...")`` — a literal kwarg.
2. ``pytest.xfail("...")`` at runtime inside a step body — the reason is a
   POSITIONAL argument, so a ``reason=`` keyword scan never saw it. There are
   ~144 such calls under ``tests/bdd/``.
3. ``pytest.mark.xfail(reason=reason, strict=False)`` where ``reason`` is a
   loop variable bound from a table (``_UC004_PARTITION_SELECTIVE`` and its
   siblings in ``conftest.py``). The reason text is a string literal in the
   table, but at the call site it is an ``ast.Name`` carrying no literal at
   all.

In all three the reason text is a string literal *somewhere* in the file, so
the guard flags any string literal mentioning 1462 and exempts the two places
the text is legitimately prose rather than data:

- **Comments** are not AST literals, so a ``# #1462 never reproduced`` note
  explaining the history is untouched.
- **Docstrings** (module, class, function) are exempt by node identity, so a
  helper may document the history in prose. This file's own docstring is the
  motivating case.

Anything else — a marker reason, a positional ``pytest.xfail`` argument, a
table entry, a dict value, an f-string fragment — is runtime reason data and is
flagged. A line that genuinely needs the digits at runtime can carry a
``# noqa: gh1462`` comment, which must be justified in review; it is a waiver,
not a routine escape hatch.

GH: #1462 (the disproven attribution), #1545 (the step-binding fix that made it
unreproducible), #1750 / #1797 (the in-step ``pytest.xfail`` mechanism this
scan now reaches)
"""

from __future__ import annotations

import ast
from pathlib import Path

_BDD_DIR = Path(__file__).resolve().parents[1] / "bdd"

_WAIVER = "noqa: gh1462"


def _docstring_node_ids(tree: ast.Module) -> set[int]:
    """Return the id() of every docstring Constant in ``tree``.

    Docstrings carry prose that may legitimately explain #1462's history.
    Identity is used rather than value comparison so a docstring that happens
    to share its text with a real reason string does not exempt the reason.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            ids.add(id(first.value))
    return ids


def _find_1462_reasons(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, text) for every non-docstring string literal naming 1462.

    Covers all three xfail-reason mechanisms — literal ``reason=`` kwargs,
    positional ``pytest.xfail(...)`` calls, and table entries bound to a
    ``reason`` variable — because each stores its text as a literal in the
    file. Lines carrying the ``noqa: gh1462`` waiver are skipped.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    exempt = _docstring_node_ids(tree)

    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in exempt or "1462" not in node.value:
            continue
        # The literal may span lines; honour a waiver on any line it covers.
        start, end = node.lineno, getattr(node, "end_lineno", node.lineno) or node.lineno
        if any(_WAIVER in line for line in lines[start - 1 : end]):
            continue
        hits.append((node.lineno, node.value))
    return hits


def _scan_dir(root: Path) -> list[str]:
    """Return a formatted violation per #1462-naming literal under ``root``."""
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for lineno, reason in _find_1462_reasons(path):
            try:
                rel: Path | str = path.relative_to(root.parents[1])
            except ValueError:
                rel = path
            violations.append(f"{rel}:{lineno}: reason data names #1462 -> {reason!r}")
    return violations


def test_no_bdd_xfail_reason_attributes_to_1462() -> None:
    """No BDD xfail reason may blame #1462 (a defect that never reproduced)."""
    violations = _scan_dir(_BDD_DIR)
    assert not violations, (
        "BDD xfail reasons must not attribute failures to #1462. It reported a request "
        "path that drops attribution_window.post_click; re-derived 2026-07-27, that never "
        "reproduced on any transport — all transports share "
        "_build_get_media_buy_delivery_request, which preserves it. Do not swap in the "
        "generic-step-shadowing attribution either: #1545 narrowed that step, so the cause "
        "is dead too. Identify what the failing transport actually exercises today. "
        "Comments and docstrings may discuss the history; runtime reason data may not. "
        "Violations:\n" + "\n".join(violations)
    )


# ── Meta-tests ────────────────────────────────────────────────────────────────
#
# These call the SAME functions the production test calls — _find_1462_reasons
# for the literal forms and _scan_dir for the directory walk. An earlier version
# of this guard duplicated the walk into a `_scan_source` helper that only the
# meta-tests exercised, so the production path was unprotected: pointing
# _BDD_DIR at a non-existent directory, and making the finder return []
# unconditionally, both left the file reporting 5 passed.


def _write(tmp_path: Path, src: str, name: str = "sample.py") -> Path:
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return path


def test_meta_positive_catches_literal_reason_kwarg(tmp_path: Path) -> None:
    """Mechanism 1: a literal ``reason=`` kwarg blaming #1462."""
    src = 'import pytest\npytest.mark.xfail(reason="window dropped (#1462)", strict=True)\n'
    assert _find_1462_reasons(_write(tmp_path, src))


def test_meta_positive_catches_multiline_concatenated_reason(tmp_path: Path) -> None:
    """Mechanism 1, split form: a reason implicitly concatenated across lines."""
    src = (
        "import pytest\n"
        "pytest.mark.xfail(\n"
        '    reason="attribution_window: validation can\'t fire — "\n'
        '    "request path drops post_click (#1462)",\n'
        "    strict=True,\n"
        ")\n"
    )
    assert _find_1462_reasons(_write(tmp_path, src))


def test_meta_positive_catches_positional_runtime_xfail(tmp_path: Path) -> None:
    """Mechanism 2: ``pytest.xfail("...")`` — the reason is positional, not a kwarg.

    The previous keyword-only scan reached 0 of the ~144 runtime xfail calls
    under tests/bdd/, which is the larger of the two blind spots it had.
    """
    src = 'import pytest\n\n\ndef step(ctx):\n    pytest.xfail("post_click dropped in-process (#1462)")\n'
    assert _find_1462_reasons(_write(tmp_path, src))


def test_meta_positive_catches_table_entry_bound_to_reason_variable(tmp_path: Path) -> None:
    """Mechanism 3: the marker's ``reason=`` is an ``ast.Name`` from a table.

    This is the exact shape of ``_UC004_PARTITION_SELECTIVE`` in
    tests/bdd/conftest.py. Writing "#1462" into such a table left the previous
    guard green, because the call site carries no string literal at all.
    """
    src = (
        "import pytest\n"
        "TABLE = [\n"
        '    ("T-UC-004-partition-attribution", {"interval_zero"}, "window never arrives (#1462)"),\n'
        "]\n"
        "for tag, substrings, reason in TABLE:\n"
        "    pytest.mark.xfail(reason=reason, strict=False)\n"
    )
    assert _find_1462_reasons(_write(tmp_path, src))


def test_meta_positive_catches_fstring_reason(tmp_path: Path) -> None:
    """An f-string reason: the digits live in a JoinedStr fragment."""
    src = 'import pytest\n\n\ndef step(ctx, transport):\n    pytest.xfail(f"{transport}: post_click dropped (#1462)")\n'
    assert _find_1462_reasons(_write(tmp_path, src))


def test_meta_negative_allows_corrected_reason(tmp_path: Path) -> None:
    """A reason naming a live, transport-specific cause is not flagged."""
    src = (
        'import pytest\npytest.mark.xfail(reason="e2e_rest: seller attribution default not implemented", strict=True)\n'
    )
    assert not _find_1462_reasons(_write(tmp_path, src))


def test_meta_negative_ignores_1462_in_comments(tmp_path: Path) -> None:
    """#1462 in a comment explaining the history is fine — comments are not literals."""
    src = (
        "import pytest\n"
        "# #1462 alleged a request-path drop; it never reproduced (see module docstring)\n"
        'pytest.mark.xfail(reason="e2e_rest: seller attribution default not implemented", strict=True)\n'
    )
    assert not _find_1462_reasons(_write(tmp_path, src))


def test_meta_negative_ignores_1462_in_docstrings(tmp_path: Path) -> None:
    """Docstring prose may discuss #1462 — that exemption is what keeps this file legal."""
    src = (
        '"""Module prose about #1462 and why it never reproduced."""\n'
        "\n"
        "\n"
        "def helper():\n"
        '    """Function prose about #1462 too."""\n'
        "    return None\n"
    )
    assert not _find_1462_reasons(_write(tmp_path, src))


def test_meta_negative_honours_the_waiver(tmp_path: Path) -> None:
    """A justified ``# noqa: gh1462`` waiver suppresses one line."""
    src = 'import pytest\npytest.mark.xfail(reason="quoting #1462 verbatim", strict=True)  # noqa: gh1462\n'
    assert not _find_1462_reasons(_write(tmp_path, src))


def test_meta_the_directory_walk_itself_reports_violations(tmp_path: Path) -> None:
    """Cover ``_scan_dir`` — the production path, not just the per-file leaf.

    Without this, pointing ``_BDD_DIR`` at an empty or non-existent directory
    was indistinguishable from a clean tree.
    """
    pkg = tmp_path / "bdd" / "steps"
    pkg.mkdir(parents=True)
    _write(pkg, 'import pytest\npytest.mark.xfail(reason="dropped (#1462)")\n', name="test_bad.py")

    violations = _scan_dir(tmp_path / "bdd")
    assert len(violations) == 1, violations
    assert "test_bad.py:2" in violations[0]
    assert "#1462" in violations[0]


def test_meta_the_scan_set_is_not_empty() -> None:
    """``_BDD_DIR`` must resolve to the real BDD tree.

    The walk returns [] for a directory that does not exist, which is
    indistinguishable from a clean tree — so a typo in ``_BDD_DIR`` would
    silently disable the guard while every other test here kept passing. This
    was the one mutation the tmp_path meta-tests could not catch.
    """
    assert _BDD_DIR.is_dir(), f"_BDD_DIR does not resolve to a directory: {_BDD_DIR}"
    scanned = list(_BDD_DIR.rglob("*.py"))
    assert len(scanned) >= 20, (
        f"_BDD_DIR resolved to {_BDD_DIR} but found only {len(scanned)} Python files — "
        "the guard is scanning the wrong tree"
    )
    assert (_BDD_DIR / "conftest.py") in scanned, (
        "tests/bdd/conftest.py is not in the scan set; it carries the xfail reason tables this guard exists to police"
    )


def test_meta_the_directory_walk_is_clean_on_a_clean_tree(tmp_path: Path) -> None:
    """The walk returns empty for a tree with only comment/docstring mentions."""
    pkg = tmp_path / "bdd"
    pkg.mkdir()
    _write(pkg, '"""Prose about #1462."""\n# and a comment about #1462\n', name="test_ok.py")

    assert _scan_dir(pkg) == []
