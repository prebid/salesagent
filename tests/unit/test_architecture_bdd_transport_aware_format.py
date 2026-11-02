"""Guard: the format a BDD step seeds must switch with the transport, not be a literal.

THE DEFECT THIS REPRODUCES (salesagent-6mm5z). `uc006_sync_creatives.py` carries two
transport-aware helpers, and their whole purpose is that a product's declared format and a
creative's declared format agree on EVERY transport:

    _format_payload(ctx, env)        -> creative side: display_300x250 in-process,
                                        display_300x250_image on e2e_rest
    _product_format_entry(ctx, env)  -> product  side: the same two, same branch
                                        ("MUST MATCH the creative format_id returned by
                                         _format_payload so that format compatibility
                                         checks pass on all transports including e2e_rest")

The creative side switches. The product side is CALLED AT FOUR SITES while thirteen others
hard-code ``display_300x250`` straight into ``ProductFactory(format_ids=...)``, and
``_build_creative_scope_payload`` hard-codes it on the creative side too. In-process both
halves say ``display_300x250``, so they agree BY COINCIDENCE. Under e2e_rest the creative
becomes ``display_300x250_image`` while the product still claims ``display_300x250`` -- they
disagree, and the seller answers CREATIVE_REJECTED (16 nodes) or cannot resolve the
creative's format at all (6). Twenty-two failures, byte-identical across three consecutive
runs, every one of them passing on all three in-process transports.

WHY A GUARD IS THE RIGHT FAILING ARTIFACT HERE. There is no behavioural reproduction to
write that is not already written: the 22 e2e_rest failures ARE the demonstration, and they
fail today. What no behavioural test can express is the INVARIANT -- that a scenario
expecting format compatibility must not name the format itself, because naming it is what
makes the two halves independent. This is the "helper-bypass" class, where the bypass is
the disease and the symptom is a transport away.

WHAT IS DELIBERATELY NOT FLAGGED. Only the DEFAULT literal counts as a bypass, because that
is precisely the value ``_product_format_entry`` returns in-process and swaps on e2e. Sites
naming a DIFFERENT format are making a different claim and must keep their literal:

    "video_30s_incompatible"   a scenario whose point is that the formats do NOT match
    "video_30s", "agent/video-30s", "agent/banner-300x250"
    format_ids=[]              the no-formats path

Normalising those would make the numbers better and delete the incompatibility tests, which
is the opposite of the invariant. A guard that cannot tell a bypass from an intentional
mismatch is worse than none.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STEPS = REPO_ROOT / "tests" / "bdd" / "steps" / "domain"

#: Both modules in the e2e-parametrized suite that seed a creative AND a product and must
#: therefore keep the two halves in agreement. The second one imports the first's constants.
STEP_FILES = (STEPS / "uc006_sync_creatives.py", STEPS / "uc006_storyboard_creative_sync.py")

#: The transport-switched pair. Hard-coding EITHER pins one half of a pairing that the other
#: half switches; the e2e value is no safer as a literal than the in-process one.
SWITCHED_FORMAT_IDS = frozenset({"display_300x250", "display_300x250_image"})

#: The only functions allowed to name them: the two that OWN the switch.
SANCTIONED = frozenset({"_format_payload", "_product_format_entry"})

#: Module-level constants that DEFINE the switched values. A definition is not a bypass --
#: the value has to be written down once, and this is where.
CONSTANT_NAMES = frozenset({"_E2E_FORMAT_ID", "_E2E_AGENT_URL", "_DEFAULT_FORMAT_ID"})

#: Methods whose scenarios CANNOT reach e2e_rest, so a literal in their arguments has no
#: switching counterpart to disagree with. Keyed on the CALLED METHOD, not on the calling
#: function's name: the first version of this exemption named one caller and a second caller
#: of the same method appeared two edits later. The evidence belongs to the method.
E2E_UNSUPPORTED_CALLS: dict[str, str] = {
    "configure_agent_served_creative": (
        "declared E2EUnsupportedSetup and PINNED in "
        "tests/unit/test_architecture_e2e_rest_escape_hatches.py -- the scenarios that call it "
        "never run on e2e_rest, so nothing switches for the literal to contradict"
    ),
}


def _exempt_lines(tree: ast.AST) -> set[int]:
    """Lines inside a call to a method whose scenarios never reach e2e_rest."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in E2E_UNSUPPORTED_CALLS
        ):
            out.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return out


def _enclosing_function(tree: ast.AST, line: int) -> str:
    """The innermost function containing *line*, or '<module>'."""
    best, best_span = "<module>", None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.lineno <= line <= (node.end_lineno or 0):
            span = (node.end_lineno or 0) - node.lineno
            if best_span is None or span < best_span:
                best, best_span = node.name, span
    return best


def _is_constant_definition(tree: ast.AST, node: ast.Constant) -> bool:
    """True if *node* is the value of a module-level ``_E2E_*``-style assignment."""
    for stmt in getattr(tree, "body", []):
        if isinstance(stmt, ast.Assign) and stmt.value is node:
            return any(isinstance(t, ast.Name) and t.id in CONSTANT_NAMES for t in stmt.targets)
    return False


def _all_switched_mentions(path: Path) -> list[tuple[int, str, str]]:
    """Every mention of a switched format id, exemptions and helpers INCLUDED.

    The staleness check needs the unfiltered set: :func:`_bypasses` removes exactly the rows
    an exemption is about, so asking it whether an exemption still matches anything would
    always answer no.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    return sorted(
        (node.lineno, node.value, _enclosing_function(tree, node.lineno))
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in SWITCHED_FORMAT_IDS
    )


def _bypasses(path: Path) -> list[tuple[int, str, str]]:
    """Every ``(line, value, enclosing_function)`` naming a switched format outside the helpers.

    Deliberately NOT position-aware. The first version of this guard matched a literal written
    directly into a seeding dict and reported 19 sites; the real number is 38, because
    ``format_id = "display_300x250"`` followed by ``{"id": format_id}`` is invisible to a
    dict-position scan while being exactly the same defect. Any mention outside the two owning
    helpers is a bypass -- that rule has no blind spot to find later.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    exempt = _exempt_lines(tree)
    out: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if node.value not in SWITCHED_FORMAT_IDS:
            continue
        if _is_constant_definition(tree, node):
            continue
        func = _enclosing_function(tree, node.lineno)
        if func in SANCTIONED:
            continue
        if node.lineno in exempt:
            continue
        out.append((node.lineno, node.value, func))
    return sorted(out)


def test_no_step_hardcodes_the_transport_switched_format() -> None:
    """A seeded format that the helpers switch must come FROM the helpers."""
    found = {path: _bypasses(path) for path in STEP_FILES}
    total = sum(len(v) for v in found.values())

    detail = "\n".join(
        f"  {path.name}\n" + "\n".join(f"    line {line:5}  {value!r}  in {func}" for line, value, func in rows)
        for path, rows in found.items()
        if rows
    )
    assert total == 0, (
        f"{total} site(s) name a transport-switched format id outside the helpers that own the "
        f"switch:\n{detail}\n\n"
        f"Seed it through the helper instead:\n"
        f"    product  -> _product_format_entry(ctx, env)\n"
        f"    creative -> _format_payload(ctx, env)   (returns id, agent_url AND assets --\n"
        f"                the assets differ per transport too, because the two formats have\n"
        f"                different asset ids; taking only the id moves the failure from\n"
        f"                format-mismatch to asset-key-mismatch)\n"
        f"and where a scenario carries the value forward, put it on ctx['creative_format_id'] "
        f"and read it back with the helper as the FALLBACK -- a literal default is the same bug "
        f"one level down.\n"
        f"A site naming a DIFFERENT format ('video_30s_incompatible') is making a different "
        f"claim and is correctly not flagged (salesagent-6mm5z)."
    )


def test_the_helpers_this_guard_defers_to_still_switch() -> None:
    """The guard is only worth anything while the helpers it names actually branch.

    Without this, deleting the ``is_e2e`` branch from either helper would leave every call
    site 'compliant' and every scenario broken -- the guard would keep passing over the exact
    defect it exists to prevent, because it only checks that callers DELEGATE.
    """
    owner = STEP_FILES[0]
    tree = ast.parse(owner.read_text(), filename=str(owner))
    by_name = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    for helper in sorted(SANCTIONED):
        func = by_name.get(helper)
        assert func is not None, f"{helper} is gone from {owner.name}; this guard names a helper that no longer exists."
        branches = any(
            isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "is_e2e"
            for sub in ast.walk(func)
        )
        assert branches, (
            f"{helper} no longer branches on is_e2e(), so it returns one format on every "
            f"transport. Delegating to it is then no better than a literal, and the sibling "
            f"guard above would pass over a fully broken suite."
        )


def test_every_exemption_still_names_a_live_call() -> None:
    """An exemption whose method is gone is a stale claim, and must be deleted.

    E2E_UNSUPPORTED_CALLS rows are evidence, not permission: each says a specific method's
    scenarios cannot reach e2e_rest. If the method disappears or stops being called, the row
    outlives its reason and would silently excuse whatever later takes that name.
    """
    called = {
        node.func.attr
        for path in STEP_FILES
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    stale = sorted(name for name in E2E_UNSUPPORTED_CALLS if name not in called)
    assert not stale, (
        f"E2E_UNSUPPORTED_CALLS names method(s) nothing calls any more: {stale}. Delete the "
        f"row -- an exemption whose subject is gone excuses the next thing to take that name."
    )


#: Names that resolve a format through the transport switch. A value built from any of these
#: switches; anything else is pinned.
SWITCHING_SOURCES = ("_format_payload", "_product_format_entry", "_creative_format_id_entry", "_scenario_format_")


def test_no_factory_pairs_a_switched_format_with_a_pinned_agent_url() -> None:
    """A format's identity is the PAIR (agent_url, id) -- switch both halves or neither.

    WRITTEN BECAUSE THE FIX FOR salesagent-6mm5z INTRODUCED THIS, and the id-only guard above
    could not see it. Four CreativeFactory calls ended up as::

        agent_url=env.DEFAULT_AGENT_URL,          # pinned
        format=_scenario_format_id(ctx, env),     # switched

    which on e2e_rest persists a row claiming the REAL catalog format at an agent that does
    not serve it -- so it resolves to nothing. That is the original bug with its halves
    swapped, and it is easy to introduce while fixing the original because the id is the
    visible half and the agent_url sits quietly beside it.

    ``format_id_identity`` (src/core/schemas/_base.py) treats the pair as the identity, which
    is why half a switch is not half a fix.
    """
    offenders: list[tuple[str, int, str, str]] = []
    for path in STEP_FILES:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if not node.func.id.endswith("Factory"):
                continue
            kwargs = {k.arg: ast.unparse(k.value) for k in node.keywords if k.arg}
            agent_url, fmt = kwargs.get("agent_url"), kwargs.get("format")
            if agent_url is None or fmt is None:
                continue
            switched = lambda v: any(src in v for src in SWITCHING_SOURCES)  # noqa: E731
            if switched(fmt) and not switched(agent_url):
                offenders.append((path.name, node.lineno, agent_url, fmt))

    assert not offenders, (
        "A factory pairs a SWITCHED format with a PINNED agent_url:\n"
        + "\n".join(f"    {name}:{line}  agent_url={au}  format={f}" for name, line, au, f in offenders)
        + "\n\nTake BOTH halves from one switch (_scenario_format_entry returns the pair). A "
        "format's identity is (agent_url, id), so a switched id at a pinned agent names a "
        "format that agent does not serve."
    )


def _local_aliases(tree: ast.AST) -> dict[str, str]:
    """``name -> the expression last assigned to it``, for simple single-target assignments.

    Needed because the pinned half is usually one hop away: ``agent_url =
    env.DEFAULT_AGENT_URL`` and then ``{"agent_url": agent_url, ...}``. Reading the dict alone
    sees a Name and cannot tell whether it switches.
    """
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = ast.unparse(node.value)
    return out


def test_no_format_dict_pairs_a_switched_half_with_a_pinned_half() -> None:
    """The ``{agent_url, id}`` DICT form of the pair rule.

    The factory-keyword guard above missed this, and it cost a whole verification cycle: the
    first fix left five ``format_ids=[{"agent_url": agent_url, "id": <switched>}]`` sites, the
    e2e run came back 22 -> 3 instead of 22 -> 0, and all three survivors traced here. Same
    invariant, second syntactic position -- which is the actual lesson: the rule is about the
    PAIR, so every place the pair can be written needs the check, not just the first one found.

    Tuple-unpacked sources (``id, agent_url, _ = _format_payload(...)``) are not resolved by
    the alias map, so a helper that unpacks and re-packs reads as a mismatch. The two owning
    helpers are exempt for that reason -- they are the definition of a correct pair.
    """
    offenders: list[tuple[str, int, str, str]] = []
    for path in STEP_FILES:
        tree = ast.parse(path.read_text(), filename=str(path))
        alias = _local_aliases(tree)

        def switching(expr: str, alias: dict[str, str] = alias) -> bool:
            """True if *expr* resolves, possibly through one local alias, to a switching source."""
            return any(src in alias.get(expr, expr) for src in SWITCHING_SOURCES)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            pair = {
                ast.unparse(k).strip("'\""): ast.unparse(v)
                for k, v in zip(node.keys, node.values, strict=False)
                if isinstance(k, ast.Constant)
            }
            if not {"agent_url", "id"} <= pair.keys():
                continue
            if _enclosing_function(tree, node.lineno) in SANCTIONED | {"_scenario_format_entry"}:
                continue
            if switching(pair["id"]) != switching(pair["agent_url"]):
                offenders.append((path.name, node.lineno, pair["agent_url"], pair["id"]))

    assert not offenders, (
        "A format dict pairs a switched half with a pinned half:\n"
        + "\n".join(f"    {n}:{ln}  agent_url={a}  id={i}" for n, ln, a, i in offenders)
        + "\n\nUse _scenario_format_entry(ctx, env) (or _product_format_entry) for the whole "
        "dict. A format's identity is the PAIR -- a switched id at a pinned agent names a "
        "format that agent does not serve."
    )
