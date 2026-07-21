"""Guard: all wire error codes must be in WIRE_STANDARD_CODES.

AST-scans Error(code=...) construction sites in src/core/tools/ and
src/adapters/ to verify every string-literal error code is either in
WIRE_STANDARD_CODES (SDK STANDARD_ERROR_CODES + the pinned-spec supplement)
or in the justified INTERNAL_CODES set.

Also verifies that AdCPError subclass error_code class attributes are
standard or internal (complementing test_error_code_mapping.py).

Ref: GH #1248
"""

import ast
import logging
from pathlib import Path

import pytest

from src.core.exceptions import INTERNAL_CODES, WIRE_STANDARD_CODES

logger = logging.getLogger(__name__)

# Spec-required codes not yet in SDK STANDARD_ERROR_CODES.
# These are mandated by AdCP BDD feature files but the SDK hasn't added them yet.
_SPEC_CODES = {
    "BILLING_NOT_SUPPORTED",  # BR-UC-011 BR-RULE-059: unsupported billing model
}

# All acceptable codes: wire-standard (SDK + spec supplement) + justified
# internal + spec-required literals
_ALLOWED_CODES = set(WIRE_STANDARD_CODES) | INTERNAL_CODES | _SPEC_CODES

# Anchor scan paths on the test file's location so they resolve correctly
# regardless of pytest's working directory (CI runs from the repo root;
# agents/IDEs may launch pytest from a subdir, which would make the relative
# paths silently match nothing).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_DIRS = [
    _REPO_ROOT / "src/core/tools",
    _REPO_ROOT / "src/adapters",
]


from tests.unit._architecture_helpers import collect_error_aliases as _collect_error_aliases  # noqa: E402
from tests.unit._architecture_helpers import iter_call_expressions  # noqa: E402


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

            for node in iter_call_expressions(tree):  # Match calls to Error(...) / <alias>(...) / adcp.types.Error(...)
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

    @pytest.mark.arch_guard
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

    @pytest.mark.arch_guard
    def test_adcp_error_subclass_codes_are_compliant(self):
        """Every AdCPError subclass _default_error_code must be standard or internal.

        Reads ``_default_error_code`` (the ClassVar slot per option-A refactor
        salesagent-fnk9). The public ``error_code`` is an instance attribute
        set in ``__init__`` and is not present on the class object.
        """
        from src.core.exceptions import AdCPError

        violations = []
        queue = [AdCPError]
        while queue:
            cls = queue.pop()
            for sub in cls.__subclasses__():
                code = sub._default_error_code
                if code not in _ALLOWED_CODES:
                    violations.append(f"{sub.__name__}._default_error_code = {code!r}")
                queue.append(sub)

        assert not violations, "AdCPError subclasses with non-compliant codes:\n" + "\n".join(
            f"  {v}" for v in violations
        )


class TestAdvisoryCodeNormalization:
    """Runtime companion to the AST scan above.

    The scan proves no string-literal internal code is *constructed* in an
    advisory. This proves the other half: a code arriving from a typed
    exception at runtime is normalized before it reaches the buyer. Advisory
    ``errors[]`` serialize verbatim and never pass through the boundary
    translator that handles raised ``AdCPError``s, so an unnormalized code
    would leak with nothing to catch it.
    """

    def test_non_wire_typed_error_code_normalized_not_leaked(self):
        """``_failed_sync_result`` normalizes a non-wire typed code through
        ``to_wire_error_code`` at the one choke point every call site shares.

        Drives a real ``AdCPFormatNotFoundError`` (``FORMAT_NOT_FOUND``, recovery
        correctable, non-transient) exactly as the ``except AdCPError`` path in
        ``_sync_creatives_impl`` forwards it, and asserts the emitted code is the
        normalized wire value ``INVALID_REQUEST`` with the retry signal preserved.

        No DB and no transport — the choke-point check backing the in-process and
        wire behavioral tests in test_creative_sync_behavioral.py.
        """
        from src.core.exceptions import AdCPFormatNotFoundError
        from src.core.tools.creatives._processing import _failed_sync_result

        err = AdCPFormatNotFoundError("format_does_not_exist_xyz")
        assert err.error_code == "FORMAT_NOT_FOUND"  # a non-wire internal code

        result = _failed_sync_result("c_leak", str(err), recovery=err.recovery, code=err.error_code)

        emitted = result.errors[0]
        assert emitted.code == "INVALID_REQUEST", (
            f"non-wire typed code {err.error_code!r} must normalize to its wire value, got {emitted.code!r}"
        )
        assert emitted.code != "FORMAT_NOT_FOUND", "internal code leaked to the buyer verbatim"
        # the code normalizes; the retry signal is preserved unchanged
        assert emitted.recovery is not None and emitted.recovery.value == "correctable"
