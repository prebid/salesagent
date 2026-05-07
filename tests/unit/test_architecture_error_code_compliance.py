"""Guard: all wire error codes must be in SDK STANDARD_ERROR_CODES.

AST-scans Error(code=...) construction sites in src/core/tools/ and
src/adapters/ to verify every string-literal error code is either in
STANDARD_ERROR_CODES or in the justified INTERNAL_CODES set.

Also verifies that AdCPError subclass error_code class attributes are
standard or internal (complementing test_error_code_mapping.py).

Ref: GH #1248
"""

import ast
import logging
from pathlib import Path

from adcp.server.helpers import STANDARD_ERROR_CODES

from src.core.exceptions import INTERNAL_CODES

logger = logging.getLogger(__name__)

# All acceptable codes: SDK standard + justified internal
_ALLOWED_CODES = set(STANDARD_ERROR_CODES) | INTERNAL_CODES

_SCAN_DIRS = [
    Path("src/core/tools"),
    Path("src/adapters"),
]


def _collect_error_aliases(tree: ast.AST) -> set[str]:
    """Collect names that alias the adcp Error type.

    Tracks both module-level and function-level imports of the form:

        from adcp...error import Error
        from adcp...error import Error as <alias>

    Returns the set of local names that refer to the adcp Error class
    (always includes "Error" itself, plus any aliases).
    """
    aliases: set[str] = {"Error"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        # Only track imports whose module path mentions "error"
        module = node.module or ""
        if "error" not in module.split("."):
            continue
        for alias in node.names:
            if alias.name == "Error":
                aliases.add(alias.asname or alias.name)
    return aliases


def _collect_error_code_literals() -> list[tuple[str, int, str]]:
    """AST-scan for Error(code="...") and return (file, line, code) triples.

    Tracks `from ... import Error as <alias>` so call sites that use the
    aliased name (e.g. ``AdCPErrorDetail(code=...)``) are also validated.
    """
    violations: list[tuple[str, int, str]] = []

    for scan_dir in _SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for py_file in sorted(scan_dir.rglob("*.py")):
            source = py_file.read_text()
            try:
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError:
                continue

            error_aliases = _collect_error_aliases(tree)

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # Match calls to Error(...) / <alias>(...) / adcp.types.Error(...)
                func = node.func
                matched = False
                if isinstance(func, ast.Name) and func.id in error_aliases:
                    matched = True
                elif isinstance(func, ast.Attribute) and func.attr == "Error":
                    matched = True
                if not matched:
                    continue

                # Extract the code= keyword argument
                code_value = None
                for kw in node.keywords:
                    if kw.arg == "code":
                        if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                            code_value = kw.value.value
                        else:
                            # Non-literal code — skip with warning
                            logger.warning(
                                "%s:%d: Error(code=<non-literal>) — cannot validate statically",
                                py_file,
                                node.lineno,
                            )
                        break

                if code_value is not None and code_value not in _ALLOWED_CODES:
                    violations.append((str(py_file), node.lineno, code_value))

    return violations


class TestErrorCodeCompliance:
    """Every Error(code=...) literal must be in STANDARD_ERROR_CODES or INTERNAL_CODES."""

    def test_no_nonstandard_error_codes_in_tools_and_adapters(self):
        """AST-scan Error(code=...) sites for non-standard codes."""
        violations = _collect_error_code_literals()
        if violations:
            msg_lines = [f"  {f}:{line}: code={code!r}" for f, line, code in violations]
            raise AssertionError(
                f"{len(violations)} Error(code=...) sites use non-standard codes:\n"
                + "\n".join(msg_lines)
                + "\n\nEach code must be in STANDARD_ERROR_CODES or INTERNAL_CODES."
            )

    def test_adcp_error_subclass_codes_are_compliant(self):
        """Every AdCPError subclass error_code must be standard or internal."""
        from src.core.exceptions import AdCPError

        violations = []
        queue = [AdCPError]
        while queue:
            cls = queue.pop()
            for sub in cls.__subclasses__():
                code = sub.error_code
                if code not in _ALLOWED_CODES:
                    violations.append(f"{sub.__name__}.error_code = {code!r}")
                queue.append(sub)

        assert not violations, "AdCPError subclasses with non-compliant codes:\n" + "\n".join(
            f"  {v}" for v in violations
        )
