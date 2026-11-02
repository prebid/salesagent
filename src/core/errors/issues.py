"""The pinned ``issues[]`` channel: one structured item per rejected field.

AdCP 3.1.1 ``core/error.json`` declares a top-level ``issues`` array —
"Structured list of validation failures. Primary use is ``VALIDATION_ERROR``,
where multi-field rejections are common and ``field`` (singular) cannot carry
the full pointer map." Items require ``pointer``, ``message`` and ``keyword``,
and the schema leaves them ``additionalProperties: true``.

This repo already emitted a hand-rolled version of exactly that:
``build_validation_error_details`` produced ``details.validation_errors[]`` with
``loc``/``msg``/``type`` — the pointer under a different name, the sentence
authored by pydantic, and pydantic's own error type where the pin asks for a
JSON Schema keyword. ``schemas/_base.py`` went further and set
``field=present[0]`` beside an ``immutable_fields`` list, which IS the pin's MUST
("populate ``field`` from ``issues[0]``") written out by hand.

Two properties this module keeps, both of which the hand-rolled version lost:

* ``message`` is DERIVED, never authored. ``ErrorIssue`` has no ``message``
  parameter, so no raise site can write one — the same rule ``CODE_TABLE``
  already enforces for the error-level sentence, applied one level down. The pin
  requires the FIELD to be present; it does not require a human to fill it.
* ``keyword`` is a JSON Schema keyword, not a pydantic error type. The map below
  is TOTAL over ``pydantic_core.ErrorType`` and checked at import, so a pydantic
  upgrade that adds a member breaks the build rather than a buyer's error path.
  Members with no honest JSON Schema equivalent map to ``None``, which OMITS the
  issue rather than mis-attributing a cause it cannot name.
"""

from __future__ import annotations

import typing
from typing import Any, ClassVar, Literal

import pydantic_core
from adcp.types.generated_poc.core.error import Issue as LibraryIssue
from pydantic import ConfigDict, model_validator

from src.core.config import get_pydantic_extra_mode

__all__ = [
    "ErrorIssue",
    "JsonPointer",
    "JsonSchemaKeyword",
    "issue_from_pydantic_error",
    "issues_from_validation_error",
    "pointer_to_field",
]


JsonSchemaKeyword = Literal[
    "additionalProperties",
    "enum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "multipleOf",
    "oneOf",
    "pattern",
    "readOnly",
    "required",
    "type",
]
"""The JSON Schema keywords this seller can honestly attribute a rejection to.

Deliberately a closed set, and deliberately smaller than the JSON Schema
vocabulary: a keyword is a CLAIM about why a value was refused, so one we cannot
substantiate is worse than none. See ``_KEYWORD_SENTENCES`` for the pairing.
"""


class JsonPointer:
    """A path into the request payload, renderable in both formats the pin uses.

    The pin needs two spellings of one fact and MUST-requires they agree:
    ``issues[].pointer`` is RFC 6901 (``/packages/0/targeting``) while top-level
    ``field`` is JSONPath-lite (``packages[0].targeting``). Owning both here is
    what makes ``field`` derived rather than typed twice at a raise site.

    RFC 6901 escaping is why the pin chose that format over the ``field`` one:
    ``~`` becomes ``~0`` and ``/`` becomes ``~1``, in that order, so a member
    literally named ``a/b`` is addressable as ``/a~1b``. JSONPath-lite cannot
    express it at all.
    """

    __slots__ = ("_segments",)

    def __init__(self, segments: tuple[str | int, ...]) -> None:
        self._segments = segments

    @classmethod
    def of(cls, *segments: str | int) -> JsonPointer:
        return cls(segments)

    @classmethod
    def from_pydantic_loc(cls, loc: typing.Sequence[str | int]) -> JsonPointer:
        """Build from a pydantic ``ValidationError.errors()[i]["loc"]`` tuple.

        ``loc`` is already the pointer this channel needs; the hand-rolled
        predecessor emitted it verbatim as ``details.validation_errors[].loc``.
        """
        return cls(tuple(loc))

    @staticmethod
    def _escape(token: str) -> str:
        # Order matters: escaping `/` first would then double-escape its own `~`.
        return token.replace("~", "~0").replace("/", "~1")

    @property
    def pointer(self) -> str:
        """RFC 6901, for ``issues[].pointer``. Empty path is the whole document."""
        return "".join(f"/{self._escape(str(s))}" for s in self._segments)

    @property
    def field(self) -> str:
        """JSONPath-lite, for the top-level ``field``. Ints render as ``[i]``."""
        out = ""
        for seg in self._segments:
            if isinstance(seg, int):
                out += f"[{seg}]"
            else:
                out += f".{seg}" if out else str(seg)
        return out

    def __repr__(self) -> str:
        return f"JsonPointer({self.pointer!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, JsonPointer) and self._segments == other._segments

    def __hash__(self) -> int:
        return hash(self._segments)


# ---------------------------------------------------------------------------
# pydantic error type -> JSON Schema keyword. TOTAL over ErrorType, verified at
# import. ``None`` means "no keyword can honestly name this", which omits the
# issue instead of attributing a cause we cannot support.
# ---------------------------------------------------------------------------

_TYPE_FAMILY: JsonSchemaKeyword = "type"

PYDANTIC_KEYWORD_MAP: dict[str, JsonSchemaKeyword | None] = {
    # Wrong type, or unparseable as the declared type. One keyword covers both:
    # JSON Schema has no separate "could not parse".
    **{t: _TYPE_FAMILY for t in typing.get_args(pydantic_core.ErrorType) if t.endswith(("_type", "_parsing"))},
    "is_instance_of": "type",
    "is_subclass_of": "type",
    "none_required": "type",
    "int_from_float": "type",
    "needs_python_object": "type",
    # Absent where required.
    "missing": "required",
    "missing_argument": "required",
    "missing_keyword_only_argument": "required",
    "missing_positional_only_argument": "required",
    # Present where forbidden.
    "extra_forbidden": "additionalProperties",
    "unexpected_keyword_argument": "additionalProperties",
    "unexpected_positional_argument": "additionalProperties",
    # Outside a closed set.
    "enum": "enum",
    "literal_error": "enum",
    # Numeric bounds. pydantic distinguishes inclusive from exclusive and so
    # does JSON Schema, so this pairing is exact.
    "greater_than": "exclusiveMinimum",
    "greater_than_equal": "minimum",
    "less_than": "exclusiveMaximum",
    "less_than_equal": "maximum",
    "multiple_of": "multipleOf",
    # Length and size. String/bytes length and collection cardinality are
    # different keywords in JSON Schema.
    "string_too_long": "maxLength",
    "string_too_short": "minLength",
    "bytes_too_long": "maxLength",
    "bytes_too_short": "minLength",
    "url_too_long": "maxLength",
    "too_long": "maxItems",
    "too_short": "minItems",
    # Lexical shape.
    "string_pattern_mismatch": "pattern",
    "url_parsing": "format",
    "url_scheme": "format",
    "url_syntax_violation": "format",
    "uuid_parsing": "format",
    "uuid_version": "format",
    "json_invalid": "format",
    "string_unicode": "format",
    "bytes_invalid_encoding": "format",
    "complex_str_parsing": "format",
    "datetime_object_invalid": "format",
    "date_from_datetime_inexact": "format",
    "int_parsing_size": "format",
    # Variant selection failed.
    "union_tag_invalid": "oneOf",
    "union_tag_not_found": "oneOf",
    # No honest JSON Schema keyword exists for these, so they carry none and the
    # issue is omitted. Each is either a constraint JSON Schema cannot express
    # (temporal ordering, decimal placement, finiteness) or an internal pydantic
    # condition that is not a payload-shape rejection at all.
    "assertion_error": None,
    "value_error": None,
    "date_future": None,
    "date_past": None,
    "datetime_future": None,
    "datetime_past": None,
    "timezone_aware": None,
    "timezone_naive": None,
    "timezone_offset": None,
    "decimal_max_digits": None,
    "decimal_max_places": None,
    "decimal_whole_digits": None,
    "finite_number": None,
    "frozen_field": None,
    "frozen_instance": None,
    "get_attribute_error": None,
    "invalid_key": None,
    "iteration_error": None,
    "no_such_attribute": None,
    "recursion_loop": None,
    "set_item_not_hashable": None,
    "default_factory_not_called": None,
    "missing_sentinel_error": None,
    "multiple_argument_values": None,
}


def _verify_keyword_map_is_total() -> None:
    """Refuse to import with an unclassified pydantic error type.

    Same mechanism ``_build_code_table()`` uses for ``CODE_TABLE``: a member the
    table does not classify is a build failure, not a runtime surprise on a
    buyer's error path. A pydantic upgrade that adds an ErrorType lands here.
    """
    known = set(typing.get_args(pydantic_core.ErrorType))
    mapped = set(PYDANTIC_KEYWORD_MAP)
    missing = known - mapped
    if missing:
        raise RuntimeError(
            f"PYDANTIC_KEYWORD_MAP does not classify {sorted(missing)}. Every "
            "pydantic_core.ErrorType member must map to a JSON Schema keyword or to "
            "None (meaning: omit the issue rather than name a cause we cannot support)."
        )
    unknown = mapped - known
    if unknown:
        raise RuntimeError(
            f"PYDANTIC_KEYWORD_MAP classifies {sorted(unknown)}, which pydantic_core "
            "no longer defines. Remove the stale entries."
        )


_verify_keyword_map_is_total()


_KEYWORD_SENTENCES: dict[JsonSchemaKeyword, str] = {
    "additionalProperties": "This field is not accepted here.",
    "enum": "This field must be one of the accepted values.",
    "exclusiveMaximum": "This field must be below the maximum.",
    "exclusiveMinimum": "This field must be above the minimum.",
    "format": "This field is not in the required format.",
    "maxItems": "This field has too many entries.",
    "maxLength": "This field is too long.",
    "maximum": "This field is above the maximum.",
    "minItems": "This field has too few entries.",
    "minLength": "This field is too short.",
    "minimum": "This field is below the minimum.",
    "multipleOf": "This field must be a multiple of the required step.",
    "oneOf": "This field does not match any accepted variant.",
    "pattern": "This field does not match the required pattern.",
    "readOnly": "This field cannot be changed.",
    "required": "This field is required.",
    "type": "This field is the wrong type.",
}
"""One sentence per keyword. The item's ``message`` is a function of THIS, so no
raise site can author it — the rule ``CODE_TABLE`` applies to the error-level
sentence, applied to the item level. Keyed on ``keyword`` alone because the
keyword IS the field-level reason; the error's own code is a separate fact that
already travels on the error and is not duplicated onto every item."""

assert set(_KEYWORD_SENTENCES) == set(typing.get_args(JsonSchemaKeyword)), (
    "every JsonSchemaKeyword needs a sentence, or `message` cannot be derived"
)


class ErrorIssue(LibraryIssue):
    """One rejected field, spec-shaped, with no way to author its prose.

    Extends the SDK type per Critical Pattern #1 rather than restating it. The
    SDK ``Issue`` is a plain ``BaseModel`` (unlike the pricing options, which are
    RootModels and cannot be subclassed — adcp-client-python#1077), so its
    ``extra="allow"`` can be overridden to this repo's policy.

    There is NO ``message`` parameter. It is resolved from ``keyword`` by the
    validator below, and passing one is refused explicitly rather than left to
    the extra-mode: ``message`` is a REQUIRED DECLARED field on the SDK parent,
    so ``extra="ignore"`` in production would otherwise accept it silently and
    the guarantee would hold only in CI.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode(), frozen=True)

    pointer: str
    keyword: JsonSchemaKeyword

    # Legal extras (the pinned item is additionalProperties: true), both named by
    # core/error.json's own canonical rejection-set vocabulary.
    rejected_value: str | None = None
    accepted_values: list[str] | None = None

    # The CONSTRAINT the keyword refers to -- in JSON Schema terms, the keyword's
    # value in the schema: 32 for `minLength`, the pattern for `pattern`. It was
    # previously readable only inside pydantic's authored sentence ("String should
    # have at least 32 characters"), which meant a buyer had to parse English for
    # a number. Structural here, and strictly more than the prose carried.
    keyword_value: str | None = None

    _AUTHORED: ClassVar[str] = (
        "ErrorIssue takes no `message`: the item's sentence is a function of its "
        "keyword, exactly as the error-level sentence is a function of its code. "
        "Pass `keyword` and let the table answer."
    )

    @model_validator(mode="before")
    @classmethod
    def _derive_message(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "message" in data:
            raise ValueError(cls._AUTHORED)
        keyword = data.get("keyword")
        sentence = _KEYWORD_SENTENCES.get(typing.cast("JsonSchemaKeyword", keyword))
        if sentence is None:
            # Unknown keyword: leave it for the field validator to reject, rather
            # than masking a bad keyword with a KeyError from this table.
            return data
        return {**data, "message": sentence}

    @classmethod
    def of(
        cls,
        *,
        pointer: str,
        keyword: JsonSchemaKeyword,
        rejected_value: str | None = None,
        accepted_values: list[str] | None = None,
        keyword_value: str | None = None,
    ) -> ErrorIssue:
        """Build an issue. THE construction surface for one.

        There is no ``message`` parameter, so a call site cannot author one --
        not "should not", cannot: the name does not exist to pass.

        This exists rather than redeclaring the inherited ``message`` field with
        a default. mypy requires an inherited REQUIRED field at every call site,
        so without this constructor every site would have to pass the sentence
        the validator is about to overwrite. It is the same shape as
        ``Error.of()``, for the same reason, one level down.
        """
        payload: dict[str, Any] = {"pointer": pointer, "keyword": keyword}
        if rejected_value is not None:
            payload["rejected_value"] = rejected_value
        if accepted_values is not None:
            payload["accepted_values"] = accepted_values
        if keyword_value is not None:
            payload["keyword_value"] = keyword_value
        return cls.model_validate(payload)

    def to_wire(self) -> dict[str, Any]:
        """Render for the ``issues[]`` slot. Unset optional fields are omitted."""
        return self.model_dump(mode="json", exclude_none=True)


def issues_from_validation_error(errors: typing.Sequence[typing.Mapping[str, Any]]) -> list[ErrorIssue]:
    """Convert a whole ``ValidationError.errors()`` list, dropping the unattributable.

    This replaces ``build_validation_error_details``, which projected the same
    pydantic errors into ``details.validation_errors[]`` as ``loc``/``msg``/
    ``type`` -- the pointer under a non-canonical name, pydantic's authored
    sentence, and a pydantic token where the pin asks for a JSON Schema keyword.
    Same input, same information, the channel and vocabulary the pin defines.
    """
    return [issue for e in errors if (issue := issue_from_pydantic_error(e)) is not None]


_KEYWORD_CTX_KEY: dict[JsonSchemaKeyword, str] = {
    "minLength": "min_length",
    "maxLength": "max_length",
    "minItems": "min_length",
    "maxItems": "max_length",
    "minimum": "ge",
    "exclusiveMinimum": "gt",
    "maximum": "le",
    "exclusiveMaximum": "lt",
    "multipleOf": "multiple_of",
    "pattern": "pattern",
    "enum": "expected",
}
"""Where pydantic keeps the constraint value for each keyword.

pydantic puts it in the error's ``ctx`` under its own name; JSON Schema names the
same thing after the keyword. Keywords absent from this table have no constraint
value to carry (``required`` and ``type`` are not parameterized), so they emit
none rather than an invented one.
"""


SELLER_RAISED_KEYWORDS: typing.Final[dict[str, JsonSchemaKeyword]] = {
    "oneOf": "oneOf",
}
"""Keywords a SELLER-RAISED ``PydanticCustomError`` names for itself.

Separate from ``PYDANTIC_KEYWORD_MAP`` because that map is closed in both
directions against ``pydantic_core.ErrorType`` -- it refuses to classify a type
pydantic does not define, which is what keeps it honest about pydantic's own
vocabulary. A constraint WE express in a validator has no pydantic ErrorType to
be classified under, and a plain ``ValueError`` maps to ``None``, so the issue
was silently dropped from ``issues[]`` and the buyer got no structured reason at
all.

The bar is the module's own: a keyword is a CLAIM, and one we cannot substantiate
is worse than none. These we can. A oneOf expressed as a model validator (see
``CreativeAssetRequest``, where core/creative-asset.json's format_id-XOR-
format_kind is flattened onto one model) rejects for exactly the reason JSON
Schema spells ``oneOf``. The error type IS the keyword, so a raise site cannot
name one this table does not carry.
"""


def issue_from_pydantic_error(error: typing.Mapping[str, Any]) -> ErrorIssue | None:
    """Convert one ``ValidationError.errors()`` entry, or None if unattributable.

    Returns None when the pydantic type maps to no JSON Schema keyword. Omitting
    is the pin-consistent outcome: ``issues`` itself is optional, so a partial
    map degrades to a shorter array, never to a wrong ``keyword``.
    """
    error_type = str(error.get("type"))
    keyword = PYDANTIC_KEYWORD_MAP.get(error_type) or SELLER_RAISED_KEYWORDS.get(error_type)
    if keyword is None:
        return None
    ctx = error.get("ctx") or {}
    ctx_key = _KEYWORD_CTX_KEY.get(keyword)
    raw = ctx.get(ctx_key) if ctx_key else None
    return ErrorIssue.of(
        pointer=JsonPointer.from_pydantic_loc(error.get("loc", ())).pointer,
        keyword=keyword,
        keyword_value=None if raw is None else str(raw),
    )


def pointer_to_field(pointer: str) -> str:
    """Translate RFC 6901 to the JSONPath-lite spelling top-level ``field`` uses.

    This IS the pin's MUST: "When ``issues`` is present, sellers MUST also
    populate ``field`` from ``issues[0]`` ... translating the RFC 6901 ``pointer``
    format to the JSONPath-lite format ``field`` uses (e.g.,
    ``/packages/0/targeting`` -> ``packages[0].targeting``)."

    A numeric token renders as an index, which is how RFC 6901 spells one. The
    escapes are undone in the reverse order they were applied.
    """
    if not pointer:
        return ""
    out = ""
    for raw in pointer.lstrip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if token.isdigit():
            out += f"[{token}]"
        else:
            out += f".{token}" if out else token
    return out
