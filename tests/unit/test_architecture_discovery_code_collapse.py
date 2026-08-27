"""Guard: a signed-request discovery failure must not collapse into a generic code.

**The disease** (verbatim from the salesagent-hksr codebase scan, #1291): a signed-
request checklist step collapses a SPECIFIC discovery/key-origin failure into the
generic ``request_signature_key_unknown`` code — or drops the mandatory key-origin
check entirely by passing ``None`` where ``{}`` is required — instead of mapping the
failure to the spec-assigned code (security.mdx :1101-1127) and raising it at the
correct checklist step.

Two exact syntactic sites carried this disease pre-fix, both in
``src/core/signing/request_verifier_middleware.py``:

1. ``except AgentResolverError as exc:`` discarded ``exc.code`` entirely instead of
   mapping it through ``_map_agent_resolver_error`` — every discovery failure fell
   through to the generic ``key_unknown`` at step 7.
2. ``expected_key_origins=resolution.key_origins`` passed a bare (possibly ``None``)
   value instead of ``resolution.key_origins or {}`` — the SDK's own
   ``_maybe_check_key_origin`` treats ``None`` as "not declared" and SKIPS the
   mandatory check rather than running it and failing.

Method: SYNTACTIC/narrow disease (the original codebase scan used a plain grep, not a
semantic multi-lens scan) — an AST-node-precision guard rather than a whole-file regex,
so reformatting or an unrelated ``.key_origins`` read elsewhere (e.g.
``capability_declarations.py``, which validates a DIFFERENT dict, not
``VerifyOptions.expected_key_origins``) does not trip it.
"""

from __future__ import annotations

import ast

from tests.unit._architecture_helpers import (
    format_failure,
    iter_call_expressions,
    parse_module,
    repo_root,
    src_python_files,
)

_MAPPER_NAME = "_map_agent_resolver_error"
_FALLBACK_KEYWORD = "expected_key_origins"


def _handler_names(handler_type: ast.expr | None) -> set[str]:
    """Names an ``except`` clause catches — handles a bare Name or a Tuple of them."""
    if handler_type is None:
        return set()
    if isinstance(handler_type, ast.Name):
        return {handler_type.id}
    if isinstance(handler_type, ast.Tuple):
        return {elt.id for elt in handler_type.elts if isinstance(elt, ast.Name)}
    return set()


def find_unmapped_resolver_error_handlers(tree: ast.Module) -> list[int]:
    """Line numbers of ``except AgentResolverError`` blocks that never map ``.code``.

    "Maps" is decided by whether ``_map_agent_resolver_error`` is CALLED anywhere in
    the handler body — a name-precision check (AST call site), not a text grep, so a
    docstring or comment mentioning the mapper's name does not vouch for a handler
    that never actually calls it.
    """
    violations: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if "AgentResolverError" not in _handler_names(node.type):
            continue
        if not any(iter_call_expressions(node, name=_MAPPER_NAME)):
            violations.append(node.lineno)
    return violations


def find_bare_key_origins_fallback_violations(tree: ast.Module) -> list[int]:
    """Line numbers of ``expected_key_origins=`` keywords passing a bare ``.key_origins``.

    Flags a keyword value that reads a ``.key_origins`` attribute WITHOUT it being an
    operand of an ``or`` (``ast.BoolOp`` with ``ast.Or``) anywhere in the value
    expression — the shape that lets a ``None`` (no ``identity.key_origins`` map
    declared) reach ``VerifyOptions`` and silently skip the SDK's mandatory check
    instead of failing it. A conditional expression wrapping the ``or {}`` (as
    production does, ``(x.key_origins or {}) if x is not None else None``) is exempt:
    the ``or {}`` sub-node is what matters, not its position in an outer ternary.
    """
    violations: list[int] = []
    for node in iter_call_expressions(tree):
        for kw in node.keywords:
            if kw.arg != _FALLBACK_KEYWORD or kw.value is None:
                continue
            bare_reads = [
                inner
                for inner in ast.walk(kw.value)
                if isinstance(inner, ast.Attribute) and inner.attr == "key_origins"
            ]
            if not bare_reads:
                continue
            guarded = any(
                isinstance(parent, ast.BoolOp)
                and isinstance(parent.op, ast.Or)
                and any(read in ast.walk(value) for value in parent.values for read in bare_reads)
                for parent in ast.walk(kw.value)
            )
            if not guarded:
                violations.append(kw.value.lineno)
    return violations


class TestNoDiscoveryCodeCollapse:
    """The class-level pin: neither disease site regresses, anywhere in src/."""

    def test_no_unmapped_agent_resolver_error_handler(self):
        repo = repo_root()
        violations = [
            f"{path.relative_to(repo)}:{lineno}: except AgentResolverError never calls {_MAPPER_NAME}(exc)"
            for path in src_python_files(repo)
            for lineno in find_unmapped_resolver_error_handlers(parse_module(path))
        ]
        assert not violations, format_failure(
            summary="A discovery failure is caught but its .code is discarded instead of mapped:",
            violations=violations,
            fix_hint=(
                f"Route the caught AgentResolverError through {_MAPPER_NAME}(exc) so its specific "
                "code reaches the wire at step 7, instead of collapsing every discovery failure "
                "into the generic request_signature_key_unknown."
            ),
        )

    def test_no_bare_key_origins_fallback(self):
        repo = repo_root()
        violations = [
            f"{path.relative_to(repo)}:{lineno}: expected_key_origins= passes a bare .key_origins (no `or {{}}`)"
            for path in src_python_files(repo)
            for lineno in find_bare_key_origins_fallback_violations(parse_module(path))
        ]
        assert not violations, format_failure(
            summary="expected_key_origins= can receive None instead of {}, silently skipping the SDK's check:",
            violations=violations,
            fix_hint=(
                "Pass `resolution.key_origins or {}` — the SDK's _maybe_check_key_origin treats None "
                "as 'not declared' and SKIPS the mandatory key-origin consistency check instead of "
                "running it and failing (request_signature_key_origin_missing)."
            ),
        )


class TestDetectorCatchesTheDisease:
    """Meta-tests. A guard whose detector finds nothing is worthless."""

    def test_detector_flags_a_handler_that_never_maps_the_error(self):
        tree = ast.parse(
            "try:\n    pass\nexcept AgentResolverError as exc:\n    logger.warning('could not resolve: %s', exc)\n"
        )
        assert find_unmapped_resolver_error_handlers(tree) == [3]

    def test_detector_flags_a_tuple_handler_that_never_maps_the_error(self):
        """A handler catching AgentResolverError alongside other types is still in scope."""
        tree = ast.parse(
            "try:\n    pass\nexcept (ValueError, AgentResolverError) as exc:\n    logger.warning('failed: %s', exc)\n"
        )
        assert find_unmapped_resolver_error_handlers(tree) == [3]

    def test_detector_flags_a_bare_key_origins_passthrough(self):
        tree = ast.parse("VerifyOptions(expected_key_origins=resolution.key_origins)\n")
        assert find_bare_key_origins_fallback_violations(tree) == [1]

    def test_detector_flags_a_bare_key_origins_passthrough_inside_a_ternary(self):
        """The exact pre-fix shape: guarded by a None-check, but not by `or {}`."""
        tree = ast.parse(
            "VerifyOptions(\n    expected_key_origins=resolution.key_origins if resolution is not None else None,\n)\n"
        )
        assert find_bare_key_origins_fallback_violations(tree) == [2]


class TestDetectorDoesNotOverfire:
    """Negative meta-tests — the detector must stay silent on what is NOT the disease."""

    def test_a_handler_that_maps_the_error_is_clean(self):
        tree = ast.parse(
            "try:\n"
            "    pass\n"
            "except AgentResolverError as exc:\n"
            "    mapped = _map_agent_resolver_error(exc)\n"
            "    logger.warning('mapped to %s', mapped)\n"
        )
        assert find_unmapped_resolver_error_handlers(tree) == []

    def test_an_unrelated_except_clause_is_not_scanned(self):
        tree = ast.parse("try:\n    pass\nexcept ValueError as exc:\n    logger.warning('%s', exc)\n")
        assert find_unmapped_resolver_error_handlers(tree) == []

    def test_key_origins_or_default_dict_is_clean(self):
        """The production fix shape: `or {}`, reformatted across lines."""
        tree = ast.parse(
            "VerifyOptions(\n"
            "    expected_key_origins=(\n"
            "        resolution.key_origins or {}\n"
            "    ) if resolution is not None else None,\n"
            ")\n"
        )
        assert find_bare_key_origins_fallback_violations(tree) == []

    def test_a_different_key_origins_read_is_not_this_disease(self):
        """`.key_origins` read for an unrelated purpose (not `expected_key_origins=`) is out of scope.

        This is the ``capability_declarations.py`` shape: reading a DIFFERENT
        ``identity.key_origins`` dict entirely, not building ``VerifyOptions``.
        """
        tree = ast.parse("origins = self.identity.key_origins if self.identity else None\n")
        assert find_bare_key_origins_fallback_violations(tree) == []

    def test_a_differently_named_keyword_is_not_scanned(self):
        """Only the exact `expected_key_origins=` keyword is in scope."""
        tree = ast.parse("SomeOtherCall(other_key_origins=resolution.key_origins)\n")
        assert find_bare_key_origins_fallback_violations(tree) == []
