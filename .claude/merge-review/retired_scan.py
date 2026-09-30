#!/usr/bin/env python3
"""Layer 2 of the merge gate: is a retired symbol USED, or merely MENTIONED?

Shell-quoting this was a mistake twice over. The first version anchored its comment filter
at ``^`` while ``grep -rn`` emits ``path:lineno:content``, so the filter tested the PATH and
was a silent no-op. The second survived that but tripped over backtick quoting inside a
nested ``bash -c``. A check whose failure mode is "passes by doing nothing" does not belong
in shell, so it lives here, where the controls at the bottom run on every invocation.

USE vs MENTION: #1721's own docstrings name the symbols it retired, to say what was deleted
and why, and this repo writes such a citation as ``symbol``. So a line is PROSE when it is a
comment, or when the match sits inside a double-backtick citation, or when it is inside a
docstring. Everything else is USE.

Usage: retired_scan.py <paths-file|-> ...   (exits 1 and prints offenders when any USE is found)
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

RETIRED = (
    "ERROR_CODE_MAPPING",
    "translate_error_code",
    "WIRE_STANDARD_CODES",
    "RECOVERY_BY_WIRE_CODE",
    "_default_status_code",
    "_default_recovery",
    "_default_suggestion",
    "synthesized_error_envelope",
    "ImplDispatcher",
    "Transport.IMPL",
    "build_two_layer_error_envelope",
)
DELETED_MODULES = (
    "src.core.tool_context",
    "src.core.transport_helpers",
    "src.core.testing_hooks",
    "src.core.testing_api",
    "src.core.request_compat",
    "src.core.protocol_envelope",
    "src.core.version_compat",
    "src.routes.rest_compat_middleware",
    "src.core.signing_contract",
    "src.core.validation",
    "src.core.security.url_validator",
    "request_verifier_middleware",
)
# Module names get a RIGHT boundary: `src.core.validation` must not match
# `src.core.validation_helpers`, which #1721 KEPT. That prefix collision already produced a
# 22-importer overcount once during this merge; it is the same class of error as an
# unanchored grep, pointed the other way.
_NEEDLE = re.compile(
    "|".join([re.escape(s) for s in RETIRED] + [re.escape(m) + r"(?![A-Za-z0-9_])" for m in DELETED_MODULES])
)
_AUTHORED = re.compile(r"AdCP[A-Za-z]*Error\(.*message\s*=")


def _specimen_lines(source: str) -> set[int]:
    """Lines inside a MULTI-LINE string literal — a test specimen, not code.

    The discriminator is deliberately narrow. A structural guard proves it works by parsing a
    specimen that CONTAINS the forbidden shape (``test_synthesized_fallback_disjunction_is_flagged``
    is exactly that, and deleting its specimen would gut the guard), and specimens are written
    as triple-quoted blocks. A SINGLE-line string is the opposite case -- ``ctx.get("synthesized_
    error_envelope")`` is a real use reached by string key -- so excluding all string literals
    would blind the scanner precisely where it matters most.
    """
    covered: set[int] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return covered
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            end = node.end_lineno or node.lineno
            if end > node.lineno:
                covered.update(range(node.lineno, end + 1))
    return covered


def _docstring_lines(source: str) -> set[int]:
    """Every line number covered by a module/class/function docstring."""
    covered: set[int] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return covered
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        doc = node.body[0] if node.body else None
        if isinstance(doc, ast.Expr) and isinstance(doc.value, ast.Constant) and isinstance(doc.value.value, str):
            covered.update(range(doc.value.lineno, (doc.value.end_lineno or doc.value.lineno) + 1))
    return covered


def offenders(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    docs = (_docstring_lines(source) | _specimen_lines(source)) if path.suffix == ".py" else set()
    out: list[str] = []
    for n, line in enumerate(source.splitlines(), 1):
        if n in docs or line.lstrip().startswith("#") or "``" in line:
            continue
        if _NEEDLE.search(line) or _AUTHORED.search(line):
            out.append(f"{path}:{n}: {line.strip()[:140]}")
    return out


def _controls() -> None:
    """Prove BOTH directions on every run. A one-directional control is no control."""
    use = "x = translate_error_code('X')\nfrom src.core.tool_context import ToolContext\n"
    mention = '"""``synthesized_error_envelope`` is a citation."""\n# translate_error_code is retired\n'
    tmp = Path("/tmp/_retired_ctl")
    tmp.mkdir(exist_ok=True)
    (tmp / "use.py").write_text(use)
    (tmp / "mention.py").write_text(mention)
    hits_use = offenders(tmp / "use.py")
    hits_mention = offenders(tmp / "mention.py")
    if len(hits_use) < 2:
        sys.exit(f"CONTROL FAILED: scanner missed real USE ({hits_use}) — the check is vacuous")
    if hits_mention:
        sys.exit(f"CONTROL FAILED: scanner flagged prose ({hits_mention}) — false positives")
    # A single-line ctx-key access is a USE and must still be caught; a multi-line specimen is
    # not. Both directions are controlled, because the specimen rule is exactly the kind of
    # exclusion that quietly widens into "ignore every string".
    (tmp / "key.py").write_text('v = ctx.get("synthesized_error_envelope")\n')
    (tmp / "spec.py").write_text('SRC = """\nv = ctx.get("synthesized_error_envelope")\n"""\n')
    if not offenders(tmp / "key.py"):
        sys.exit("CONTROL FAILED: scanner missed a single-line ctx-key USE")
    if offenders(tmp / "spec.py"):
        sys.exit("CONTROL FAILED: scanner flagged a multi-line test specimen")


def main() -> int:
    _controls()
    paths: list[Path] = []
    for arg in sys.argv[1:]:
        lines = sys.stdin.read().splitlines() if arg == "-" else Path(arg).read_text().splitlines()
        paths += [Path(p) for p in lines if p.strip()]
    # PYTHON ONLY. A markdown file cannot USE a symbol, it can only name one, and prose
    # conventions differ per format (``x`` in rst-flavoured docstrings, `x` in markdown), so
    # scanning them yields nothing but false positives.
    found = [o for p in paths if p.is_file() and p.suffix == ".py" for o in offenders(p)]
    if found:
        print("retired/deleted symbols USED (not merely cited):")
        print("\n".join(found))
        return 1
    print(f"   ok (controls: use-detected, prose-ignored; {len(paths)} paths scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
