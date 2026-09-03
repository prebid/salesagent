"""Guard: a creative-agent registry dial must not let ``OutboundError`` fall into a bare catch-all.

KEPT DELIBERATELY, with the measurement that decided it. A proposal retired this
guard, or narrowed it to one function, on the grounds that its subject "became a
type error". It did not: handler ORDERING is not expressible in the type system
at all — mypy accepts a generic ``except`` placed ahead of the specific one —
and the four subject sites in ``src/core/tools/creatives/_processing.py`` are
live. Neither deleting nor narrowing this guard is safe.

``CreativeAgentRegistry.preview_creative``/``build_creative`` dial the egress
seam (``call_mcp_tool`` -> ``validate_url``), which raises
``OutboundRequestBlocked``/``OutboundDeliveryFailed`` (``src/core/security/
outbound_http.py``) — already correctly classified by the seam. A ``try``
around one of these calls whose only broad handler is ``except Exception``
(with no ``except OutboundError`` ahead of it) launders that classification
into a generic message with the WRONG recovery hint (GH #1802: a
terminal refusal reported as ``recovery="transient"``, "Retry recommended").

The fix (GH #1802) added ``except OutboundError: raise_mapped_outbound_error(...)``
ahead of the generic arm in both ``_create_new_creative`` and
``_update_existing_creative`` (``src/core/tools/creatives/_processing.py``).
This guard pins that shape going forward — codebase-scan (GH #1802)
found exactly these 2 dial methods swallowed by exactly this arm ordering; the
scan set is a fixed, named pair of registry methods, not a broad heuristic.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    iter_call_expressions,
    parse_module,
    repo_root,
    scan_src,
)

# The two CreativeAgentRegistry methods that dial the egress seam and can
# raise OutboundError. Not a broad heuristic — the exact pair codebase-scan
# (GH #1802) enumerated.
DIALLING_METHODS = frozenset({"preview_creative", "build_creative"})


def _try_body_calls_dialling_method(try_node: ast.Try) -> bool:
    """True when *try_node*'s body (not its handlers) calls a dialling method."""
    for stmt in try_node.body:
        for call in iter_call_expressions(stmt):
            func = call.func
            if isinstance(func, ast.Attribute) and func.attr in DIALLING_METHODS:
                return True
    return False


def _handler_catches_outbound_error(handler: ast.ExceptHandler) -> bool:
    """True when *handler*'s type mentions OutboundError, an ancestor, or is bare."""
    if handler.type is None:
        return True  # bare `except:` — catches everything, no laundering possible
    names: list[ast.expr] = list(handler.type.elts) if isinstance(handler.type, ast.Tuple) else [handler.type]
    # Matches `OutboundError` / `outbound_http.OutboundError` / `AdCPError` (an
    # ancestor typed arm ahead of the generic one is equally safe). Deliberately
    # does NOT match `Exception`/`BaseException` — a handler catching those IS
    # the generic catch-all this guard is looking for, not a safe narrower one;
    # see `_handler_is_generic_exception` for that check.
    safe_names = {"OutboundError", "AdCPError"}
    for n in names:
        if isinstance(n, ast.Name) and n.id in safe_names:
            return True
        if isinstance(n, ast.Attribute) and n.attr in safe_names:
            return True
    return False


def _handler_is_generic_exception(handler: ast.ExceptHandler) -> bool:
    """True for `except Exception` / `except Exception as e` (not bare, not narrower)."""
    if handler.type is None:
        return False
    if isinstance(handler.type, ast.Name):
        return handler.type.id == "Exception"
    if isinstance(handler.type, ast.Attribute):
        return handler.type.attr == "Exception"
    return False


def _try_launders_outbound_error(try_node: ast.Try) -> bool:
    """True when a dialling call's try has a generic `except Exception` with no
    OutboundError-aware (or ancestor/bare) handler ahead of it."""
    if not _try_body_calls_dialling_method(try_node):
        return False
    for handler in try_node.handlers:
        if _handler_catches_outbound_error(handler):
            return False  # a safe handler comes first — nothing laundered
        if _handler_is_generic_exception(handler):
            return True  # generic catch-all reached with no safe handler ahead
    return False


def find_laundered_outbound_error_violations(tree: ast.Module) -> list[int]:
    """Line numbers of ``try`` statements that launder OutboundError.

    Shaped as a ``(tree) -> list[int]`` detector so the meta-tests can feed it
    synthetic sources directly.
    """
    return sorted(
        node.lineno for node in ast.walk(tree) if isinstance(node, ast.Try) and _try_launders_outbound_error(node)
    )


def _scan_src() -> dict[str, list[int]]:
    """Map every offending module under src/ to its violation line numbers. No exemptions."""
    return scan_src(find_laundered_outbound_error_violations)


class TestNoLaunderedOutboundError:
    """No try/except under src/ launders OutboundError from a creative-agent dial.

    There is no allowlist: the scan set (codebase-scan GH #1802) was
    emptied by the GH #1802 fix and stays empty.
    """

    @pytest.mark.arch_guard
    def test_no_dialling_call_launders_outbound_error(self):
        """Every preview_creative/build_creative try must have except OutboundError ahead of except Exception."""
        offenders = _scan_src()

        if offenders:
            lines = ["OutboundError laundering found under src/:", ""]
            lines.extend(
                f"  {module}: line(s) {', '.join(str(n) for n in lineno)}"
                for module, lineno in sorted(offenders.items())
            )
            lines += [
                "",
                "A try around registry.preview_creative()/build_creative() must catch",
                "`OutboundError` (delegating to raise_mapped_outbound_error) BEFORE any generic",
                "`except Exception` — otherwise a correctly-classified seam refusal is laundered",
                "into a hardcoded transient 'Retry recommended' message. See",
                "src/core/tools/creatives/_processing.py for the reference shape. There is no allowlist.",
            ]
            raise AssertionError("\n".join(lines))


class TestLaunderedOutboundErrorDetector:
    """The detector's own correctness, on synthetic sources."""

    @pytest.mark.arch_guard
    def test_detector_catches_known_bad(self):
        """Every laundering form is reported."""
        assert_detector_catches_ast_snippets(
            find_laundered_outbound_error_violations,
            snippets={
                "preview_creative laundered": (
                    "def f(registry):\n"
                    "    try:\n"
                    "        registry.preview_creative(agent_url='x', format_id='y', creative_manifest={})\n"
                    "    except AdCPConfigurationError as e:\n"
                    "        return e\n"
                    "    except Exception as e:\n"
                    "        return e\n"
                ),
                "build_creative laundered, no other handler": (
                    "def f(registry):\n"
                    "    try:\n"
                    "        registry.build_creative(agent_url='x', format_id='y', message='m',"
                    " gemini_api_key='k')\n"
                    "    except Exception as e:\n"
                    "        return e\n"
                ),
                # The regex-slip-equivalent case for an AST scanner: a nested call
                # (not a bare top-level statement) must not defeat the match — the
                # "would-be-missed" case a naive top-level-statement-only scan drops.
                "dialling call nested inside another expression": (
                    "def f(registry):\n"
                    "    try:\n"
                    "        result = (registry.preview_creative(agent_url='x', format_id='y',"
                    " creative_manifest={}) or {})\n"
                    "    except Exception as e:\n"
                    "        return e\n"
                ),
            },
        )

    @pytest.mark.arch_guard
    @pytest.mark.parametrize(
        ("label", "source"),
        [
            (
                "OutboundError caught ahead of Exception",
                (
                    "def f(registry):\n"
                    "    try:\n"
                    "        registry.preview_creative(agent_url='x', format_id='y', creative_manifest={})\n"
                    "    except OutboundError as e:\n"
                    "        raise_mapped_outbound_error(e, provenance=OperatorEndpoint('x'), logger=logger)\n"
                    "    except Exception as e:\n"
                    "        return e\n"
                ),
            ),
            (
                "bare except ahead of nothing else",
                (
                    "def f(registry):\n"
                    "    try:\n"
                    "        registry.build_creative(agent_url='x', format_id='y', message='m',"
                    " gemini_api_key='k')\n"
                    "    except:\n"
                    "        return None\n"
                ),
            ),
            (
                "no generic Exception handler at all",
                (
                    "def f(registry):\n"
                    "    try:\n"
                    "        registry.preview_creative(agent_url='x', format_id='y', creative_manifest={})\n"
                    "    except AdCPConfigurationError as e:\n"
                    "        return e\n"
                    "    except OutboundError as e:\n"
                    "        raise\n"
                ),
            ),
            (
                "unrelated call, generic except",
                (
                    "def f(registry):\n"
                    "    try:\n"
                    "        registry.list_all_formats(tenant_id='t')\n"
                    "    except Exception as e:\n"
                    "        return e\n"
                ),
            ),
            (
                "generic except with no dialling call in try body",
                (
                    "def f():\n    try:\n        do_something_unrelated()\n    except Exception as e:\n        return e\n"
                ),
            ),
            (
                "dialling call in the except handler, not the try body",
                (
                    "def f(registry):\n"
                    "    try:\n"
                    "        do_something_unrelated()\n"
                    "    except Exception:\n"
                    "        registry.preview_creative(agent_url='x', format_id='y', creative_manifest={})\n"
                ),
            ),
        ],
    )
    def test_detector_ignores_non_violations(self, label, source):
        """A safe handler, no generic catch-all, or no dialling call is not a violation."""
        assert find_laundered_outbound_error_violations(ast.parse(source)) == [], f"false positive on {label}"

    @pytest.mark.arch_guard
    def test_processing_module_is_scanned_and_clean(self):
        """The fixed module IS subject to this scan, and is clean post-fix."""
        module = repo_root() / "src/core/tools/creatives/_processing.py"
        assert find_laundered_outbound_error_violations(parse_module(module)) == []
