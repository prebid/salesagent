"""Guard: a signed-request discovery failure must not collapse into a generic code.

**The disease** (verbatim from the salesagent-hksr codebase scan, #1291): a signed-
request checklist step collapses a SPECIFIC discovery failure into the generic
``request_signature_key_unknown`` code instead of mapping the failure to the
spec-assigned code (security.mdx :1101-1127) and raising it at the correct checklist
step.

Two exact syntactic sites carried this disease pre-fix. Both now live in
``src/core/signing/verifier.py`` — inbound verification no longer sits in a
middleware of its own, it is reached from ``_resolve_identity`` — but the guard
never names a file: it scans all of ``src/``, so the sites moving does not weaken it.

1. ``except AgentResolverError as exc:`` discarded ``exc.code`` entirely instead of
   mapping it through ``_map_agent_resolver_error`` — every discovery failure fell
   through to the generic ``key_unknown`` at step 7.
2. ``expected_key_origins=`` was passed at all. The map is how a verifier pins a JWKS
   whose LOCATION can vary, and this seller reads keys from
   ``<agent origin>/.well-known/jwks.json`` and nowhere else, so there is no origin to
   pin — and threading a map through would put a counterparty-controlled document back
   inside the decision the fixed location took out of it
   (``docs/design/request-signing-subset.md``). The disease this half now refuses is the
   RE-ADDITION, in either of its spellings: a bare map (which makes the SDK warn and
   skip) or an empty one (which makes it run and reject on an origin nobody declared).

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
_MAP_KEYWORD = "expected_key_origins"


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


def find_key_origin_map_passthrough_violations(tree: ast.Module) -> list[int]:
    """Line numbers of ``expected_key_origins=`` keywords, anywhere in ``src/``.

    Passing the map AT ALL is the violation now. This seller reads a counterparty's keys
    from ``<agent origin>/.well-known/jwks.json`` and nowhere else
    (``src.core.signing.verifier._jwks_is_well_known``), so there is no origin to pin and
    nothing to compare -- and threading a map through would put a
    counterparty-controlled document back inside the decision the fixed location took out
    of it.

    Keyword-precision, so a docstring naming the parameter does not trip it.
    """
    return [
        kw.value.lineno
        for node in iter_call_expressions(tree)
        for kw in node.keywords
        if kw.arg == _MAP_KEYWORD and kw.value is not None
    ]


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

    def test_no_key_origin_map_reaches_the_verifier(self):
        repo = repo_root()
        violations = [
            f"{path.relative_to(repo)}:{lineno}: expected_key_origins= is passed to the SDK verifier"
            for path in src_python_files(repo)
            for lineno in find_key_origin_map_passthrough_violations(parse_module(path))
        ]
        assert not violations, format_failure(
            summary="A key-origin map is threaded into VerifyOptions, which this seller does not consult:",
            violations=violations,
            fix_hint=(
                "Delete the argument. Keys come from <agent origin>/.well-known/jwks.json and nowhere "
                "else, so there is no origin to pin; re-adding the map puts a counterparty document "
                "back in the decision (docs/design/request-signing-subset.md)."
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

    def test_detector_flags_a_key_origin_map_passthrough(self):
        tree = ast.parse("VerifyOptions(expected_key_origins=resolution.key_origins)\n")
        assert find_key_origin_map_passthrough_violations(tree) == [1]

    def test_detector_flags_an_empty_map_passthrough(self):
        """The shape that engages the SDK check rather than skipping it is equally out."""
        tree = ast.parse("VerifyOptions(\n    expected_key_origins={},\n)\n")
        assert find_key_origin_map_passthrough_violations(tree) == [2]


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

    def test_a_key_origins_read_that_is_not_a_verify_option_is_not_this_disease(self):
        """The ``capability_declarations.py`` shape: a DIFFERENT map, validated for emission.

        We still PUBLISH ``identity.key_origins``; the narrowing is about what we consult.
        """
        tree = ast.parse("origins = self.identity.key_origins if self.identity else None\n")
        assert find_key_origin_map_passthrough_violations(tree) == []

    def test_a_differently_named_keyword_is_not_scanned(self):
        """Only the exact `expected_key_origins=` keyword is in scope."""
        tree = ast.parse("SomeOtherCall(other_key_origins=resolution.key_origins)\n")
        assert find_key_origin_map_passthrough_violations(tree) == []
