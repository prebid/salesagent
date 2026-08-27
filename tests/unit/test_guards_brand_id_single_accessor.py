"""Guard: ``brand_id`` is read out of a brand reference in exactly ONE place.

Disease (#1721): ``BrandReference.brand_id`` is ``BrandId``, a
``RootModel[str]`` that does not override ``__str__``, so ``str(brand.brand_id)``
yields the repr ``"root='brand_one'"`` instead of ``brand_one``. Five hand-rolled
copies of that extraction existed; four used bare ``str()`` and one used ``.root``.
Because ``brand_id`` is a component of the account natural key (BR-RULE-056), the
copies disagreeing meant a sync-provisioned account could never be resolved by its
own key — and since ``JSONType`` validates on read but not on write, the persisted
row could not be loaded through the ORM at all.

The fix consolidated all of them onto ``brand_key_parts``
(``src/core/helpers/brand_key.py``). This guard pins that consolidation.

**What is banned:** reading the ``.brand_id`` ATTRIBUTE anywhere in ``src/``
outside the accessor module. That covers the reintroduction of the original bug
(``str(x.brand_id)`` must first read the attribute) AND the subtler half — a NEW
correct-but-separate ``x.brand_id.root`` copy, which is how two copies come to
disagree in the first place.

**Why a name-based type guard was rejected:** ``brand_id`` is declared as a plain
``str`` on some SDK models and as ``BrandId`` on others, so no AST rule can infer
from the name alone whether a given read needs unwrapping. Pinning the single
accessor needs no type inference and is therefore sound.

**Scope boundary:** dict/SQL subscripts (``brand["brand_id"]``,
``Account.brand["brand_id"].as_string()``) are NOT flagged. A subscript yields a
plain string or a SQL column expression — neither can carry the RootModel repr, so
neither can reproduce this defect.
"""

import ast

from tests.unit._architecture_helpers import (
    REPO_ROOT,
    assert_detector_catches_ast_snippets,
    parse_module,
    src_python_files,
)

#: The one module permitted to read the attribute — it IS the accessor.
ACCESSOR_MODULE = "src/core/helpers/brand_key.py"


def find_brand_id_attribute_reads(tree: ast.Module) -> list[int]:
    """Return line numbers of ``.brand_id`` attribute READS.

    Loads only: an assignment target (``brand.brand_id = x``) is construction, not
    natural-key extraction, and cannot mangle a value.
    """
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "brand_id" and isinstance(node.ctx, ast.Load)
    )


def test_brand_id_is_read_only_through_the_shared_accessor():
    violations: list[str] = []
    for path in src_python_files(REPO_ROOT):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == ACCESSOR_MODULE:
            continue
        for lineno in find_brand_id_attribute_reads(parse_module(path)):
            violations.append(f"{rel}:{lineno}")
    assert not violations, (
        "brand_id read outside the shared accessor. BrandId is a RootModel[str] whose "
        "str() is \"root='x'\", and brand_id is a component of the account natural key "
        "(BR-RULE-056) — a second extraction site is how the two copies came to disagree "
        "and corrupt the key (#1721). Use "
        "src.core.helpers.brand_key.brand_key_parts:\n  " + "\n  ".join(violations)
    )


def test_guard_catches_known_bad_shapes():
    """Positive meta-tests: every way a second extraction site can appear."""
    assert_detector_catches_ast_snippets(
        find_brand_id_attribute_reads,
        snippets={
            # The original defect, verbatim.
            "bare_str_of_rootmodel": ("def f(entry):\n    brand_id = str(entry.brand.brand_id)\n    return brand_id\n"),
            # Correct unwrap, wrong place: a second copy that can drift from the first.
            "correct_but_duplicated_root_unwrap": (
                "def f(ref):\n"
                "    brand_id = None\n"
                "    if ref.brand.brand_id is not None:\n"
                "        brand_id = ref.brand.brand_id.root\n"
                "    return brand_id\n"
            ),
            # A text guard anchored on "str(" would MISS this one: the mangling
            # happens through f-string interpolation, which calls __str__ with no
            # str( token anywhere on the line. The AST sees the attribute read.
            "fstring_interpolation_no_str_token": ('def f(entry):\n    return f"{entry.brand.brand_id}"\n'),
            # Same miss for %-formatting and .format().
            "percent_format_no_str_token": ('def f(entry):\n    return "%s" % (entry.brand.brand_id,)\n'),
            # A defensive probe still ends in a raw read.
            "getattr_probe_then_read": (
                "def f(brand):\n"
                '    if getattr(brand, "brand_id", None) is not None:\n'
                "        return brand.brand_id\n"
                "    return None\n"
            ),
        },
    )


def test_guard_ignores_the_fixed_shape():
    """Negative meta-test: going through the accessor must NOT be flagged."""
    fixed = (
        "from src.core.helpers.brand_key import brand_key_parts\n"
        "\n"
        "def f(entry, repo):\n"
        "    brand_domain, brand_id = brand_key_parts(entry.brand)\n"
        "    return repo.get_by_natural_key(brand_domain=brand_domain, brand_id=brand_id)\n"
    )
    assert find_brand_id_attribute_reads(ast.parse(fixed)) == []


def test_guard_ignores_subscripts_and_writes():
    """Negative meta-test: subscripts and assignment targets cannot carry the repr.

    ``Account.brand["brand_id"].as_string()`` is a SQL column expression and
    ``brand["brand_id"] = value`` is construction from an already-plain string —
    flagging either would be a false positive, not a caught defect.
    """
    benign = (
        "def f(brand, brand_id, stmt):\n"
        '    brand["brand_id"] = brand_id\n'
        '    return stmt.where(Account.brand["brand_id"].as_string() == brand_id)\n'
    )
    assert find_brand_id_attribute_reads(ast.parse(benign)) == []
