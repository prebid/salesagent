"""Unit tests for the untyped-``data``-blob coercers in list_creatives (#1508).

``_list_creatives_impl`` reads several typed ``Creative`` fields straight from the
untyped JSON ``data`` blob, where an out-of-band producer may write a value of the
wrong shape. Three pure helpers coerce those reads so one corrupt row cannot crash
the whole listing:

* ``_coerce_blob_scalar``   — string fields (``concept_id`` / ``concept_name``)
* ``_coerce_blob_str_list`` — ``list[str]`` (``tags``)
* ``_coerce_blob_dict``     — ``dict``      (``assets``)

The integration guards in ``tests/integration/test_list_creatives_concept_filter.py``
pin the wiring (DB → reader → every wire transport, that omission/stringify survives
serialization). These unit tests pin the pure branch logic directly — every element
and collapse branch, the required per-field label, and the No-Quiet-Failures log — which
a full DB+HTTP round-trip exercises only indirectly (restoring a ``field_label="tags"``
default or dropping the ``el is None`` branch reddens nothing at the integration layer).
"""

from unittest.mock import patch

import pytest

from src.core.tools.creatives.listing import (
    _blob_log_context,
    _coerce_blob_dict,
    _coerce_blob_scalar,
    _coerce_blob_str_list,
)
from tests.helpers import rendered_log_calls


@pytest.fixture
def mock_logger():
    """Patch the module logger so drop-warnings / collapse-debug lines are inspectable."""
    with patch("src.core.tools.creatives.listing.logger") as m:
        yield m


def _warnings(mock) -> list[str]:
    """Render the captured ``logger.warning`` calls to their final messages.

    Thin adapter over the shared ``rendered_log_calls`` helper (``tests/helpers``), which owns
    the ``fmt % args`` substitution and its ``if self.args`` guard so the idiom lives in one
    place rather than being copy-pasted here and in the integration guards."""
    return rendered_log_calls(mock.warning)


class TestCoerceBlobScalar:
    """Scalars pass through / stringify silently; non-scalars drop to None + warn."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, None),
            ("keep", "keep"),
            ("", ""),  # empty string is a valid str, not a drop
            (0, "0"),  # str(0) is falsy-input but a valid coercion, must not be dropped
            (123, "123"),
            (1.5, "1.5"),
            (True, "True"),  # bool is an int subclass; str(True) == "True"
            (False, "False"),
        ],
    )
    def test_scalars_pass_or_stringify_without_warning(self, value, expected, mock_logger):
        assert _coerce_blob_scalar(value, "concept_id") == expected
        assert mock_logger.warning.call_count == 0

    @pytest.mark.parametrize("value", [["x"], {"k": "v"}])
    def test_non_scalar_is_dropped_and_logged_with_its_field(self, value, mock_logger):
        assert _coerce_blob_scalar(value, "concept_name") is None
        # The drop names the field it was passed — the per-field attribution the required
        # label (D1) exists to deliver, not a hardcoded "concept"/"tags".
        assert _warnings(mock_logger) == [
            f"Dropping non-scalar concept_name value of type {type(value).__name__} from creative listing"
        ]


class TestCoerceBlobStrList:
    """List coercion: non-list drops, element stringify/drop (incl. null), empty collapse."""

    def test_none_returns_none_silently(self, mock_logger):
        assert _coerce_blob_str_list(None, "tags") is None
        assert mock_logger.warning.call_count == 0
        assert mock_logger.debug.call_count == 0

    @pytest.mark.parametrize("value", ["premium", 123, True, {"k": "v"}])
    def test_non_list_is_dropped_and_logged(self, value, mock_logger):
        assert _coerce_blob_str_list(value, "tags") is None
        assert _warnings(mock_logger) == [
            f"Dropping non-list tags value of type {type(value).__name__} from creative listing"
        ]

    def test_scalar_elements_stringified_order_preserved(self, mock_logger):
        assert _coerce_blob_str_list([1, "keep", 1.5, True, ""], "tags") == ["1", "keep", "1.5", "True", ""]
        assert mock_logger.warning.call_count == 0

    def test_non_scalar_elements_dropped_and_logged(self, mock_logger):
        # A nested list and a dict element are both corrupt for a str list — each drops
        # and warns via the shared scalar coercer, order of the survivors preserved.
        assert _coerce_blob_str_list(["keep", {"k": "v"}, ["nested"]], "tags") == ["keep"]
        assert _warnings(mock_logger) == [
            "Dropping non-scalar tags value of type dict from creative listing",
            "Dropping non-scalar tags value of type list from creative listing",
        ]

    def test_null_element_dropped_and_logged(self, mock_logger):
        # A JSON null element is corrupt (null != absent for items:{type:string}); it must
        # be dropped WITH a log, not silently swallowed by the filter.
        assert _coerce_blob_str_list(["keep", None], "tags") == ["keep"]
        assert _warnings(mock_logger) == ["Dropping null tags element from creative listing"]

    def test_all_null_elements_collapse_to_none_each_logged(self, mock_logger):
        assert _coerce_blob_str_list([None, None], "tags") is None
        assert _warnings(mock_logger) == [
            "Dropping null tags element from creative listing",
            "Dropping null tags element from creative listing",
        ]
        # Both nulls drop, the emptied list then collapses to None — that collapse logs once
        # at debug (matching the sibling collapse tests), never a drop-warning.
        assert mock_logger.debug.call_count == 1

    def test_empty_list_collapses_to_none_at_debug_not_warning(self, mock_logger):
        # A well-formed empty list is valid input, not corruption: collapse to None so
        # exclude_none omits the key, logged at debug — NOT a drop-warning.
        assert _coerce_blob_str_list([], "tags") is None
        assert mock_logger.warning.call_count == 0
        assert mock_logger.debug.call_count == 1

    def test_fully_emptied_list_collapses_to_none(self, mock_logger):
        # A non-empty list whose only element is corrupt: the element warns, then the
        # emptied list collapses to None at debug (one warning, one debug, no double drop).
        assert _coerce_blob_str_list([["a"]], "tags") is None
        assert _warnings(mock_logger) == ["Dropping non-scalar tags value of type list from creative listing"]
        assert mock_logger.debug.call_count == 1


class TestCoerceBlobDict:
    """Dicts (and None) pass through; a non-dict drops to None + warn."""

    @pytest.mark.parametrize("value", [None, {}, {"banner": {"url": "x"}}])
    def test_dicts_and_none_pass_without_warning(self, value, mock_logger):
        assert _coerce_blob_dict(value, "assets") == value
        assert mock_logger.warning.call_count == 0

    @pytest.mark.parametrize("value", [["a"], "str", 123, True])
    def test_non_dict_is_dropped_and_logged(self, value, mock_logger):
        assert _coerce_blob_dict(value, "assets") is None
        assert _warnings(mock_logger) == [
            f"Dropping non-dict assets value of type {type(value).__name__} from creative listing"
        ]


class TestFieldLabelIsRequired:
    """``field_label`` is a required positional on all three coercers (no default), so a
    call site that forgets it fails loudly rather than mislabeling its drop as some other
    field. Restoring a ``field_label="tags"`` default (a prior review round's regression)
    turns this hard TypeError into a silent mislabel — this is the guard against that."""

    @pytest.mark.parametrize(
        "coercer,arg",
        [
            (_coerce_blob_scalar, ["x"]),
            (_coerce_blob_str_list, [1]),
            (_coerce_blob_dict, ["x"]),
        ],
    )
    def test_field_label_has_no_default(self, coercer, arg):
        # match on the arg name so an UNRELATED TypeError can't satisfy the oracle: the intended
        # failure is the missing-required-positional, and restoring a ``field_label`` default
        # makes the call return instead of raise — which then reddens here.
        with pytest.raises(TypeError, match="field_label"):
            coercer(arg)  # missing required field_label


class TestLogContextAttribution:
    """The optional ``log_context`` suffix (the corrupt row's creative/tenant/principal ids) is
    appended to every drop-warning so an operator can trace the bad row and repair it. Default-
    empty keeps the pure-branch tests above attribution-free; the listing loop passes the real
    suffix (built by ``_blob_log_context``). Dropping the ``log_context`` arg from any warning
    (or its ``%s`` from the format string) reddens here."""

    _CTX = " (creative_id=c1 tenant_id=t1 principal_id=p1)"

    def test_scalar_non_scalar_drop_carries_context(self, mock_logger):
        _coerce_blob_scalar(["x"], "concept_id", log_context=self._CTX)
        assert _warnings(mock_logger) == [
            f"Dropping non-scalar concept_id value of type list from creative listing{self._CTX}"
        ]

    def test_dict_non_dict_drop_carries_context(self, mock_logger):
        _coerce_blob_dict(["x"], "assets", log_context=self._CTX)
        assert _warnings(mock_logger) == [
            f"Dropping non-dict assets value of type list from creative listing{self._CTX}"
        ]

    def test_list_non_list_drop_carries_context(self, mock_logger):
        _coerce_blob_str_list("premium", "tags", log_context=self._CTX)
        assert _warnings(mock_logger) == [f"Dropping non-list tags value of type str from creative listing{self._CTX}"]

    def test_null_and_forwarded_element_drops_carry_context(self, mock_logger):
        # The list coercer must forward log_context to BOTH its own null-element warning and the
        # per-element scalar coercion it delegates to — so a corrupt element names its row too.
        _coerce_blob_str_list([None, {"k": "v"}], "tags", log_context=self._CTX)
        assert _warnings(mock_logger) == [
            f"Dropping null tags element from creative listing{self._CTX}",
            f"Dropping non-scalar tags value of type dict from creative listing{self._CTX}",
        ]


class TestBlobLogContext:
    """``_blob_log_context`` builds the attribution suffix that ``TestLogContextAttribution`` feeds
    the coercers as a literal — so without this, no test exercises the builder itself, and a
    transposed field (a creative_id rendered under the ``tenant_id`` label, say) would pass every
    other test. Distinct value per slot so any transposition reddens."""

    def test_names_each_id_under_its_own_label(self):
        assert _blob_log_context("creative_9", "tenant_9", "principal_9") == (
            " (creative_id=creative_9 tenant_id=tenant_9 principal_id=principal_9)"
        )

    def test_crlf_in_ids_is_neutralized(self):
        # A buyer-supplied id carrying CR/LF would forge log entries (CodeQL py/log-injection).
        # Every id routes through log_safe here, so the newline is stripped and the suffix stays
        # a single line. Reddens if the log_safe wrap is removed from any slot.
        suffix = _blob_log_context("c\r\n9", "t\n9", "p\r9")
        assert "\n" not in suffix and "\r" not in suffix
        assert suffix == " (creative_id=c9 tenant_id=t9 principal_id=p9)"
