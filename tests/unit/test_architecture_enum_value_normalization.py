"""Guard + characterization for the enum->str normalization DRY fix (#1399 jkfl).

src/core/enum_helpers.py::enum_value() is the single canonical normalizer for
"enum-or-str -> its string value" (None->None; Enum->.value; duck .value->str;
else str(v)). Several sites hand-rolled the same logical operation expressed
differently (``X.value if isinstance(X, SomeEnum) else ...``,
``X.value if X else default``, ``str(X.value)``), which is exactly the
"semantically equivalent code expressed differently" the project DRY invariant
targets.

This module pins two things:

1. ``test_no_handrolled_enum_to_str_normalization`` -- a structural guard that
   fails if any hand-rolled enum->str ternary / ``str(x.value)`` wrap reappears
   in ``src/core`` outside the canonical helper. This is the regression gate for
   the de-duplication.
2. ``TestEnumValueNormalizationBehavior`` -- a behavioral characterization that
   pins the byte-identical output the refactor must preserve (the Core
   Invariant): enum input -> ``.value``, str input -> passthrough, None ->
   per-site default.
"""

import re
from pathlib import Path

from src.core.enum_helpers import enum_value

_SRC_CORE = Path(__file__).resolve().parents[2] / "src" / "core"

# The two hand-rolled forms enum_value() replaces. ``str(x.value)`` is a manual
# wrap of an assumed-enum; ``.value if`` is the guarded ternary (isinstance or
# truthiness). enum_helpers.py defines the canonical helper and is exempt.
_TERNARY = re.compile(r"\.value\s+if\s+")
_STR_VALUE_WRAP = re.compile(r"\bstr\(\s*[A-Za-z_][\w.]*\.value\s*\)")
_EXEMPT = {"enum_helpers.py"}


def test_no_handrolled_enum_to_str_normalization():
    """No src/core site may hand-roll enum->str normalization; use enum_value().

    RED before the jkfl refactor (the hand-rolled sites still exist), GREEN after.
    """
    violations: list[str] = []
    for py in _SRC_CORE.rglob("*.py"):
        if py.name in _EXEMPT:
            continue
        for lineno, line in enumerate(py.read_text().splitlines(), start=1):
            if _TERNARY.search(line) or _STR_VALUE_WRAP.search(line):
                rel = py.relative_to(_SRC_CORE.parents[1])
                violations.append(f"{rel}:{lineno}: {line.strip()}")

    assert not violations, (
        "Hand-rolled enum->str normalization found -- route through "
        "src.core.enum_helpers.enum_value() instead:\n" + "\n".join(violations)
    )


class TestEnumValueNormalizationBehavior:
    """Behavioral characterization the refactor must preserve byte-for-byte."""

    def test_build_request_asset_types_form_independent(self):
        # creative_formats.py site: the normalization exists so the builder yields
        # the SAME request whether asset_types arrive as enum members or as their
        # string values. (The request model re-validates, so this input-form
        # independence is the observable invariant the refactor must preserve.)
        from adcp.types import AssetContentType

        from src.core.tools.creative_formats import build_list_creative_formats_request

        members = list(AssetContentType)[:2]
        from_enums = build_list_creative_formats_request(asset_types=members)
        from_strs = build_list_creative_formats_request(asset_types=[m.value for m in members])

        assert from_enums.asset_types == from_strs.asset_types

    def test_build_request_asset_types_none_stays_none(self):
        from src.core.tools.creative_formats import build_list_creative_formats_request

        req = build_list_creative_formats_request(asset_types=None)
        assert req.asset_types is None

    def test_enum_value_matches_handrolled_status_normalization(self):
        # creative.py site: status.value if isinstance(status, CreativeStatus) else status.
        from src.core.schemas.creative import CreativeStatus

        member = next(iter(CreativeStatus))
        assert enum_value(member) == member.value  # enum -> .value
        assert enum_value("approved") == "approved"  # str -> passthrough
        assert enum_value(None) is None  # None -> None

    def test_enum_value_or_default_preserves_strict_fallback(self):
        # sync_wrappers.py site: enum_value(validation_mode) or "strict".
        from src.core.schemas.creative import CreativeStatus

        member = next(iter(CreativeStatus))
        assert (enum_value(None) or "strict") == "strict"
        assert (enum_value(member) or "strict") == member.value
