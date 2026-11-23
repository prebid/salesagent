"""Exception-hierarchy behavior that has no other grader in this suite.

Every per-class *value* this module once asserted for a code's ``message``,
``recovery`` or ``suggestion`` is gone: all three are read-only properties over
``CODE_TABLE`` (``src/core/errors/codes.py``, built at import from the pinned
adcp SDK's own enums), so pinning them per exception class copies the pinned
table into a second place instead of grading production. What survives that
excision is the *absence of the arguments* that used to author those values —
a constructor-signature fact, not a table copy — kept below. The "class code is in the
vocabulary" checks are gone for the same reason inverted:
``AdCPSalesAgentError.__init_subclass__`` raises ``TypeError`` at class-creation
time for a ``_code`` the table does not classify, so a violating class cannot be
constructed for a test to catch. The wire-code translation suite is gone with
the symbols it exercised (``ERROR_CODE_MAPPING``, ``translate_error_code``,
``to_dict``, ``to_adcp_error``, ``wire_error_code``): the AdCP error vocabulary
is open, codes now reach the buyer verbatim, and the envelope has one builder.

What remains is behavior that lives in *code* rather than in the table, and that
nothing else in the suite exercises:

- Coverage of the authored code → HTTP-status map. The values themselves are a
  table, and re-listing them here would only copy it; what is graded is that
  every code a typed class can emit HAS a row, so no raise site falls through to
  the unclassified default.
- ``AdCPSalesAgentError.iter_concrete_subclasses()``. It backs the error-code
  compliance tests, which iterate it but pin none of the walk's promises:
  transitive, deduplicated across diamond inheritance, never yielding ``cls``
  itself, skipping abstract bases.
- The excision of the ``message`` / ``recovery`` / ``suggestion`` constructor
  arguments. A raise site does not choose a classification, it chooses a CLASS;
  the free kwargs let any call site pair any code with any text or recovery, and
  the wire carried the contradiction. Only ``status_code=`` has an equivalent
  grader elsewhere (the REST-tagged scenarios of
  ``tests/bdd/features/local-pre-dispatch-refusals.feature`` read the HTTP status
  off the wire, and it is the code's own through ``AdcpErrorResponse.http_status``),
  so the other three are pinned here.
- The two dead A2A translation symbols staying dead. A2A has no translation of
  its own: ``_dispatch_skill`` in ``adcp_a2a_server.py`` serializes the
  boundary's ``AdcpErrorResponse`` with ``to_wire``; ``exceptions.py`` carried a
  second, unreachable copy (PR #1083 review), and nothing else in the suite
  notices if it comes back.

Retry-after on both envelope layers, the IDEMPOTENCY_* code/recovery pairs, and
the two-layer envelope shape itself are all graded elsewhere (respectively
``tests/helpers/envelope_assertions.py::assert_envelope_shape`` +
``tests/integration/test_idempotency_rate_limit.py``,
``tests/integration/test_idempotency_replay.py`` and the BR-UC-003 feature, and
every BDD error scenario), so they are not restated here.
"""

from __future__ import annotations

import abc

import pytest
from adcp.types import ErrorCode

from src.core.errors.codes import _HTTP_STATUS, _UNCLASSIFIED_STATUS, CODE_TABLE
from src.core.exceptions import (
    AdCPProductNotFoundError,
    AdCPSalesAgentError,
    AdCPValidationError,
)

# ---------------------------------------------------------------------------
# The excised constructor arguments
# ---------------------------------------------------------------------------


class TestTheRaiseSiteCannotAuthorTheCodesOwnValues:
    """A raise site cannot choose a ``message``, ``recovery`` or ``suggestion``.

    REPLACES ``test_recovery_can_be_overridden_per_instance`` and
    ``test_to_dict_includes_overridden_recovery``, which pinned the exact
    behavior this epic exists to remove: free ``message=`` / ``recovery=`` /
    ``suggestion=`` arguments let a call site pair any code with any text and any
    classification, and the wire carried the contradiction (SERVICE_UNAVAILABLE
    paired with ``terminal``) with a green test grading it. The contract is
    excised, so the tests that pinned it are replaced rather than deleted — what
    was "callers can" is now "callers cannot", asserted the only way an excised
    argument can be.

    This is the one thing about those three names that is NOT a copy of
    ``CODE_TABLE``: the values live in the table and are graded there, but the
    *shape of the constructor* lives in ``src/core/exceptions.py`` and is graded
    nowhere else. The sibling excision, ``status_code=``, is graded on the wire by
    the REST-tagged scenarios of ``local-pre-dispatch-refusals.feature`` and is
    deliberately not restated here.
    """

    @pytest.mark.parametrize("excised", ["message", "recovery", "suggestion"])
    def test_the_authoring_kwarg_is_gone(self, excised: str):
        """Naming an excised value is a ``TypeError``, not a silently ignored kwarg.

        Keyword-only and unknown: the constructor has no ``**kwargs`` sink, so an
        argument that used to be honored now fails loudly at the raise site
        instead of being dropped on the way to the wire.
        """
        with pytest.raises(TypeError):
            AdCPValidationError(**{excised: "terminal"})  # type: ignore[arg-type]

    def test_there_is_no_positional_message_either(self):
        """``AdCPValidationError("some text")`` is a ``TypeError``.

        Closing the kwarg alone would leave the older spelling open — the whole
        pre-epic codebase raised these positionally — and a positional message
        would reach ``BaseException.args`` and, through ``__str__``, the wire.
        """
        with pytest.raises(TypeError):
            AdCPValidationError("permanent schema mismatch")  # type: ignore[call-arg]

    def test_the_derived_values_stand_on_their_own(self):
        """Non-vacuity: with nothing passed, the three values still resolve.

        A constructor that rejected everything would satisfy the assertions above
        while emitting nothing. VALIDATION_ERROR's classification is the one
        value transcribed here, and only as the anchor for that check — its
        delivery to a buyer is graded on the wire by every
        ``assert_wire_error(code, recovery=...)`` call in the BDD error scenarios.
        """
        exc = AdCPValidationError()

        assert exc.recovery == "correctable"
        assert exc.message and exc.message == str(exc)
        assert isinstance(exc.suggestion, str)


# ---------------------------------------------------------------------------
# HTTP status is the one graded value CODE_TABLE does not own
# ---------------------------------------------------------------------------


class TestEveryEmittedCodeHasAnAuthoredStatus:
    """No typed class can reach a buyer with an unclassified HTTP status.

    ``status_code`` is a read-only function of the wire code, read from
    ``CODE_TABLE``; the published codes get their number from ``_HTTP_STATUS``
    and any published code missing from it takes ``_UNCLASSIFIED_STATUS`` (500).
    That default is right for the 60-odd published codes this seller never
    raises, and wrong for every code it does — a new typed class whose code has
    no row would answer a buyer-correctable failure with a server fault, and
    nothing else would say so. Platform codes need no check: an
    :class:`AppErrorCode` member carries its own ``CodeEntry``, status included,
    so a member without one cannot be declared.

    The values are deliberately NOT transcribed here. They are authored in one
    place, and a second list of them would be a copy to keep in sync, not a
    grader. Delivery of the value to the wire is graded by the REST-tagged scenarios
    of ``tests/bdd/features/local-pre-dispatch-refusals.feature``.
    """

    def test_no_class_falls_through_to_the_unclassified_default(self):
        emitted = {cls._code for cls in AdCPSalesAgentError.iter_concrete_subclasses() if getattr(cls, "_code", None)}

        # Non-vacuity: an empty walk would satisfy the assertion below.
        assert emitted, "no concrete AdCPSalesAgentError subclass declares a code — the walk is vacuous"

        unclassified = sorted(str(code) for code in emitted if isinstance(code, ErrorCode) and code not in _HTTP_STATUS)
        assert not unclassified, (
            "these published codes are emitted by a typed class but have no row in _HTTP_STATUS "
            f"(src/core/errors/codes.py), so they answer {_UNCLASSIFIED_STATUS}: {unclassified}"
        )

    def test_an_unclassified_code_is_a_server_fault(self):
        """A published code this seller does not raise resolves to 500.

        ``BUDGET_CAP_REACHED`` is one: no class emits it, so it has no row. The
        default is not a judgement about that code — it is the floor that used to
        be the base exception's own class default.

        Read off ``CODE_TABLE`` rather than off a constructed error, and that is not a
        weakening: an error naming a code no class declares is no longer CONSTRUCTIBLE.
        ``error_code=`` is gone from the constructor, so the only way to hold that code is
        to look it up — which is what the table's consumers do anyway (``status_code`` is
        ``CODE_TABLE[self._error_code].status``, the same expression).
        """
        assert CODE_TABLE[ErrorCode.BUDGET_CAP_REACHED].status == _UNCLASSIFIED_STATUS == 500


# ---------------------------------------------------------------------------
# The subclass walk behind the error-code compliance tests
# ---------------------------------------------------------------------------


class TestIterConcreteSubclasses:
    """Lock the contract of ``AdCPSalesAgentError.iter_concrete_subclasses()``.

    The error-code compliance tests depend on this walk visiting every
    transitive subclass exactly once; they iterate the generator but pin none of
    its behavior, so a regression in transitivity, dedup or self-exclusion would
    silently narrow what they grade.
    """

    def test_yields_transitive_descendants_once_excluding_cls(self):
        """Generic walk: transitive, deduplicated across diamonds, never yields cls."""
        # Exercise the underlying function with a local root so the real subclass
        # tree stays untouched — subclassing AdCPSalesAgentError here would leak
        # these throwaway classes into every other test that enumerates it (and
        # each would have to declare a CODE_TABLE code to be creatable at all).
        walk = AdCPSalesAgentError.iter_concrete_subclasses.__func__

        class _Base: ...

        class _Mid(_Base): ...

        class _Leaf(_Mid): ...

        class _Other(_Base): ...

        class _Diamond(_Mid, _Other): ...  # reachable via both _Mid and _Other

        result = list(walk(_Base))

        # Transitive: the grandchild (_Leaf) and the diamond are reached, not
        # just the direct children.
        assert set(result) == {_Mid, _Leaf, _Other, _Diamond}
        # Deduplicated despite two parent paths to _Diamond.
        assert result.count(_Diamond) == 1
        # Never yields the class it was called on.
        assert _Base not in result

    def test_real_tree_is_transitive_and_excludes_base(self):
        """On the real hierarchy: a two-level-deep subclass is yielded, the base is not."""
        concrete = set(AdCPSalesAgentError.iter_concrete_subclasses())

        # AdCPSalesAgentError -> AdCPNotFoundError -> AdCPProductNotFoundError.
        assert AdCPProductNotFoundError in concrete
        assert AdCPSalesAgentError not in concrete

    def test_skips_abstract_bases_yields_concrete_descendants(self):
        """Abstract bases are walked through but not yielded — the 'concrete' promise."""
        walk = AdCPSalesAgentError.iter_concrete_subclasses.__func__

        class _Root: ...

        class _AbstractMid(_Root, abc.ABC):
            @abc.abstractmethod
            def handle(self) -> None: ...

        class _Concrete(_AbstractMid):
            def handle(self) -> None: ...

        result = list(walk(_Root))

        assert _Concrete in result  # concrete descendant of an abstract base is yielded
        assert _AbstractMid not in result  # the abstract base itself is skipped


# ---------------------------------------------------------------------------
# The dead A2A translation map must stay dead
# ---------------------------------------------------------------------------


class TestNoDeadA2AMap:
    """Dead A2A error map must not exist in exceptions module (PR #1083 review).

    A2A has no error translation of its own: ``_dispatch_skill`` in
    ``adcp_a2a_server.py`` serializes the boundary's ``AdcpErrorResponse`` with
    ``to_wire``. ``exceptions.py`` once carried a second, unreachable copy; a
    re-introduction would compile, pass every other test, and only show up as
    two transports disagreeing about a code. Kept from origin/main because
    nothing else in the suite grades the absence.
    """

    def test_no_a2a_error_code_map_in_exceptions(self):
        """_A2A_ERROR_CODE_MAP was dead code — A2A serializes the boundary's AdcpErrorResponse."""
        import src.core.exceptions as exc_module

        msg = (
            "_A2A_ERROR_CODE_MAP is dead code — A2A serializes the boundary's AdcpErrorResponse "
            "in _dispatch_skill in adcp_a2a_server.py"
        )
        assert not hasattr(exc_module, "_A2A_ERROR_CODE_MAP"), msg

    def test_no_to_a2a_error_code_in_exceptions(self):
        """to_a2a_error_code() was dead code — A2A serializes the boundary's AdcpErrorResponse."""
        import src.core.exceptions as exc_module

        msg = (
            "to_a2a_error_code() is dead code — A2A serializes the boundary's AdcpErrorResponse "
            "in _dispatch_skill in adcp_a2a_server.py"
        )
        assert not hasattr(exc_module, "to_a2a_error_code"), msg
