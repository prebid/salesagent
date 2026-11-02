#!/usr/bin/env python3
"""Locate every hand-built creative and pricing-option literal in the test tree, and
say — per site — whether the PINNED MODEL rejects it, accepts it, or was never asked.

The step-defect counts have had a committed classifier since they were first
quoted (``build_bdd_decisions.py``); the seeding counts did not, and a review
caught the asymmetry: the 191/110/84 figures lived only inside a 547 KB HTML of
agent reports, so the first engineer to act on them would have started with
archaeology instead of a command.

This is that command. It re-derives the counts, prints the file list a migration
shards on, and states its own definition — because a differently-drawn definition
produces materially different numbers, which is exactly what the review found when
it re-counted by hand.

DEFINITION, stated so it can be argued with rather than guessed at:

    A CREATIVE LITERAL is a dict display whose keys include at least two of
    creative_id / name / format_id / format / format_kind / assets / snippet /
    url / media_url. Two keys, not one, because ``{"format_id": x}`` on its own is
    a reference, not a creative.

    It is HAND-BUILT when it is not an argument to a factory call — no enclosing
    ``*Factory(...)``, ``.build(...)``, ``.create(...)`` or ``.payload(...)``.
    Those go through an owner; the hand-built ones go through nothing, which is
    what makes them the migration target.

    ``--scope`` decides whether ``tests/integration`` and ``tests/unit`` count.
    The reviewer's re-count differed from the report's largely on this axis, so it
    is a flag rather than a silent choice.

    uv run python scripts/audit/creative_literal_sites.py [--scope bdd|tests] [--json]

    RUN IT THROUGH THE PROJECT ENVIRONMENT. This used to be a dependency-free AST
    scan and bare ``python3`` worked; it now puts every site to the PINNED models,
    so it needs ``adcp`` and ``src`` on the path. Bare ``python3`` exits 2 with a
    message naming the fix rather than a traceback that blames ``adcp``.

VALIDITY IS A VERDICT WITH THREE STATES, NEVER A BOOLEAN (salesagent-b341x.18)

    This script used to answer "is this literal invalid" with ``omits_assets`` — a
    PROXY for "the pinned model rejects this" — and the proxy was wrong in both
    directions. Three tickets took their scope from it and all three were
    materially wrong. ``omits_assets`` is gone; a boolean named like a verdict IS
    the proxy, so no field in the output carries one.

    REJECTED_BY_PIN         the pinned model refuses this payload, proven
    ACCEPTED_BY_PIN         the pinned model accepts this payload, proven
    UNDECIDABLE_STATICALLY  nothing was proven, and that is a VERDICT, not an
                            absence: it is counted, printed, and never folded
                            into either side.

    A rejection is proven two ways, and BOTH are consulted:

    (a) KEY SHAPE, which needs no values. Under ``extra="forbid"`` a constant key
        that is no field or alias of the model is a proof. A required field's key
        being absent is a proof ONLY when the display carries no ``**`` spread and
        no computed key, either of which could supply it. It is the ONLY leg that
        reaches a site ``ast.literal_eval`` cannot touch, which is most of them:
        measured, it is the sole proof for 11 of 53 scope-tests rejections and 3
        of 4 scope-bdd ones.
    (b) FULL EVALUATION. ``ast.literal_eval`` succeeds and ``model_validate`` is
        asked. Acceptance can only ever come from this leg — key shape can prove a
        refusal but never a pass.

    When the payload evaluates, the pin's own answer is the ground truth and the
    reasons are the UNION of both legs: key shape can only ever prove a SUBSET of
    what the model raises, and reporting the subset would let a site whose real
    reason set is ``{extra_forbidden, missing}`` be read as exactly-
    ``{extra_forbidden}`` — which flips its obligation to the weaker one. That is
    the same defect as reading ``errors()[0]``, one leg further upstream.

    Anything that reaches neither proof is UNDECIDABLE_STATICALLY. There is no
    symbolic evaluator and no constant folding here on purpose: the blockers are
    names bound at runtime (step parameters, fixtures, Scenario Outline Examples
    rows), and binding those is per-site analysis rather than a census operation.

    THE VERDICT CARRIES EVERY REASON, never ``errors()[0]``. The payload
    ``{creative_id, name, format_id, unknown_key_xyz}`` raises BOTH
    ``missing('assets',)`` and ``extra_forbidden('unknown_key_xyz',)``, and
    pydantic emits ``missing`` first — so a classifier reading the first error is
    decided by emission order rather than by the payload.

PROOF SCOPE: a literal census speaks about the literal AS WRITTEN

    ``proof_scope`` is ``as-dispatched`` only when nothing between the literal and
    its use can reach it — no name binding, no container write. A display that
    escapes is reported ``as-written`` with the escape route named, because a later
    statement may repair it: ``uc006_sync_creatives.py`` binds three creative
    displays to names, appends them to ``ctx["creatives"]``, and a later Given does
    ``creatives[-1].setdefault("assets", {}).update(...)`` before dispatch.

ABSTENTION: no verdict without the key that names the object

    A dict is only evidence of a creative request if it carries ``creative_id``,
    and of a pricing option if it carries ``pricing_option_id``. ``given_entities``
    seeds format-catalog entries whose keys are ``{name, assets}`` — both real
    model fields, so ``model_fit`` is 1.0 and no fit threshold could ever exclude
    them. Membership can, and does: ``verdict_source="membership-unproven"``.

THE EXTRA MODE IS OBSERVED, NEVER SET

    ``extra=get_pydantic_extra_mode()`` is evaluated at CLASS-DEFINITION time.
    Setting ``ENVIRONMENT`` after import changes nothing; setting it before import
    changes every model in the process. So the census READS ``model_config`` off
    the live classes and reports what it saw, in the header and under
    ``config.observed_extra_mode``. A headline that silently depends on the
    caller's shell is the same defect as the proxy.

WHAT THIS CENSUS DOES NOT READ

    ``.py`` files only, through ``ast``. JSON fixtures and ``.feature`` Examples
    rows are named in ``not_examined`` rather than silently omitted — Examples rows
    especially, since they bind the very names that make most bdd sites
    undecidable. Widening ``scan()`` to read them would move two committed guards'
    census for a reason unrelated to validity, so it is declared, not covered.
"""

from __future__ import annotations

import argparse
import ast
import collections
import glob
import json
import pathlib
import sys
from typing import Any

CREATIVE_KEYS = {
    "creative_id",
    "name",
    "format_id",
    "format",
    "format_kind",
    "assets",
    "snippet",
    "url",
    "media_url",
}
PRICING_KEYS = {"pricing_option_id", "pricing_model", "rate", "currency", "fixed_price", "floor_price"}

#: Fields a creative payload carried BEFORE 3.1.1, when content sat inline instead of in
#: the ``assets`` slot map. Named rather than inlined in ``scan()`` because a second
#: consumer grades it: ``tests/unit/test_architecture_no_pre_311_creative_builders.py``
#: refuses to let a shared test BUILDER seed any of them, and a guard with its own copy
#: of the set would drift from the census that motivates it.
PRE_311_KEYS = {"snippet", "snippet_type", "template_variables", "duration", "variants"}
FACTORY_CALLS = ("Factory", "build", "create", "payload")

SCOPES = {
    "bdd": ["tests/bdd/**/*.py", "tests/harness/**/*.py"],
    "tests": ["tests/**/*.py"],
}

# ---------------------------------------------------------------------------
# The verdict vocabulary
# ---------------------------------------------------------------------------

REJECTED = "REJECTED_BY_PIN"
ACCEPTED = "ACCEPTED_BY_PIN"
UNDECIDABLE = "UNDECIDABLE_STATICALLY"

#: Canonical order, so every counts object prints its three states the same way.
VERDICTS = (REJECTED, ACCEPTED, UNDECIDABLE)

#: How a rejection was proven, or why no verdict was reached. Two undecidables with
#: two different next actions must not share a source: "the values are not reachable"
#: is answered by binding them, "this is not a payload" is answered by leaving it alone.
SOURCE_KEY_SHAPE = "key-shape"
SOURCE_FULL_EVAL = "full-eval"
SOURCE_MEMBERSHIP = "membership-unproven"
SOURCE_MODEL_FIT = "fit-below-threshold"
SOURCE_UNEVALUABLE = "values-unevaluable"

#: What each pydantic error type means for the caller of the tool being seeded.
KIND_MALFORMED = "MALFORMED_PAYLOAD"
KIND_UNDECLARED_KEY = "UNDECLARED_KEY"

#: The two obligations a rejection can carry, and they are NOT interchangeable.
#: A reason set of exactly ``{extra_forbidden}`` says the undeclared field is dropped
#: before the implementation ever sees it (``_accept_only_declared_fields``,
#: ``src/core/schemas/_base.py``) — there is no wire error to assert. ANY other reason
#: is an environment-independent invalid-request obligation, and it DOMINATES when both
#: are present: measured on scope tests, most sites carrying ``extra_forbidden`` carry a
#: second reason too, and routing those to the drop guarantee would put real wire-error
#: obligations on the floor.
OBLIGATION_INVALID_REQUEST = "INVALID_REQUEST_WIRE_ERROR"
OBLIGATION_UNDECLARED_FIELD_DROPPED = "UNDECLARED_FIELD_DROPPED_BEFORE_IMPL"

#: The pydantic error type that means "this key is not declared on the model".
EXTRA_FORBIDDEN = "extra_forbidden"

#: The key without which a dict is not evidence of the object at all.
IDENTIFYING_KEY = {"creative": "creative_id", "pricing": "pricing_option_id"}

#: Fraction of a site's constant keys that must be fields or aliases of the model before
#: the census will render a verdict on it at all.
#:
#: The site heuristic is written in the PRE-3.1.1 vocabulary (``format``, ``media_url``,
#: ``snippet``, ``url`` are in CREATIVE_KEYS and are not fields of CreativeAssetRequest),
#: so it selects dicts for carrying keys the pinned model forbids and the model then
#: rejects them for carrying them. That circle would manufacture rejections out of
#: ad-server unit dicts in the broadstreet and GAM tests, which are not creative requests
#: at all.
#:
#: MEASURED creative model_fit over scope tests (230 sites, at eb4779efe), rounded to a
#: tenth:  0.0:2  0.2:22  0.3:15  0.4:4  0.5:16  0.6:3  0.7:35  0.8:11  1.0:122.
#: The distribution is bimodal with the trough between 0.5 and 0.7, so the bar sits at
#: 0.6. Scope bdd has no tail at all: all 45 of its sites are 1.0. At this bar 29 sites
#: are set aside, all of them ad-server unit dicts. A site below it is reported
#: UNDECIDABLE with its foreign keys named, never rejected.
MODEL_FIT_THRESHOLD = 0.6

CREATIVE_MODEL = "src.core.schemas.creative.CreativeAssetRequest"
PRICING_MODEL = "src.core.schemas.pricing.PricingOption"

#: Seed kinds this census does not read, declared rather than silently omitted.
NOT_EXAMINED = (
    ("json-fixture", "tests/**/*.json"),
    ("feature-examples", "tests/bdd/features/*.feature"),
)

#: Method names that write their argument into a container the caller still holds.
CONTAINER_WRITES = {"append", "extend", "insert", "add", "update", "setdefault"}


# ---------------------------------------------------------------------------
# Structure: the site census. stdlib only, no models, no verdicts.
# ---------------------------------------------------------------------------


def factory_spans(tree: ast.AST) -> list[tuple[int, int]]:
    """Line ranges covered by a call that goes through an owner."""
    spans = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        label = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
        owner = getattr(getattr(f, "value", None), "id", "")
        if any(k in label for k in FACTORY_CALLS) or "Factory" in owner:
            spans.append((node.lineno, getattr(node, "end_lineno", node.lineno)))
    return spans


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _enclosing_scope(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST | None:
    """The function (or module) whose statements could still reach *node*'s value."""
    cur = parents.get(node)
    while cur is not None and not isinstance(cur, ast.FunctionDef | ast.AsyncFunctionDef | ast.Module):
        cur = parents.get(cur)
    return cur


def _name_use_reasons(name: str, scope: ast.AST | None, parents: dict[ast.AST, ast.AST]) -> list[str]:
    """How *name* is used after it is bound, in the scope that can still reach it."""
    if scope is None:
        return []
    reasons: list[str] = []
    for node in ast.walk(scope):
        if not (isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load)):
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.Attribute):
            grand = parents.get(parent)
            verb = f"mutated via .{parent.attr}()" if isinstance(grand, ast.Call) else f"read as .{parent.attr}"
            reasons.append(f"{name} {verb}")
        elif isinstance(parent, ast.Call):
            reasons.append(f"{name} passed to a call")
        elif isinstance(parent, ast.Subscript):
            reasons.append(f"{name} subscripted")
        elif isinstance(parent, ast.Return):
            reasons.append(f"{name} returned")
        elif isinstance(parent, ast.List | ast.Tuple | ast.Set | ast.Dict | ast.keyword):
            reasons.append(f"{name} stored in a container")
        else:
            reasons.append(f"{name} read at line {node.lineno}")
    return sorted(set(reasons))


def escape_routes(node: ast.Dict, parents: dict[ast.AST, ast.AST]) -> list[str]:
    """Every way a later statement could still reach this display's value.

    Empty means nothing can: the display is consumed inline by the expression that
    contains it, and only then may the census claim ``as-dispatched``. Over-reporting
    an escape only weakens a claim, so ambiguity resolves toward reporting one.
    """
    reasons: list[str] = []
    cur: ast.AST | None = parents.get(node)
    while cur is not None:
        if isinstance(cur, ast.Call):
            attr = getattr(cur.func, "attr", "")
            if attr in CONTAINER_WRITES:
                reasons.append(f"written into a container via .{attr}()")
            break
        if isinstance(cur, ast.List | ast.Tuple | ast.Set | ast.Dict):
            reasons.append("element of a container display")
        elif isinstance(cur, ast.Assign | ast.AnnAssign | ast.NamedExpr):
            targets = cur.targets if isinstance(cur, ast.Assign) else [cur.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    reasons.append(f"bound to name {target.id}")
                    reasons.extend(_name_use_reasons(target.id, _enclosing_scope(node, parents), parents))
                elif isinstance(target, ast.Subscript):
                    reasons.append(f"subscript-assigned into {ast.unparse(target.value)}")
                elif isinstance(target, ast.Attribute):
                    reasons.append(f"attribute-assigned onto {ast.unparse(target.value)}")
            break
        elif isinstance(cur, ast.Return | ast.Yield | ast.YieldFrom):
            reasons.append("returned")
            break
        elif isinstance(cur, ast.stmt):
            break
        cur = parents.get(cur)
    return sorted(set(reasons))


def _iter_sites(paths: list[str]):
    """Yield ``(kind, node, row)`` for every site, once. The single definition of "site".

    ``scan()`` and ``census()`` both consume this, so the structural census and the
    verdicts can never disagree about what they are counting.
    """
    for pattern in paths:
        for path in sorted(glob.glob(pattern, recursive=True)):
            if "__pycache__" in path:
                continue
            text = pathlib.Path(path).read_text()
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            spans = factory_spans(tree)
            parents = _parents(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                keys = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                inside = any(lo <= node.lineno <= hi for lo, hi in spans)
                row = {
                    "file": path,
                    "line": node.lineno,
                    "hand_built": not inside,
                    "keys": sorted(keys),
                    "spread": any(k is None for k in node.keys),
                    "dynamic_keys": any(
                        k is not None and not (isinstance(k, ast.Constant) and isinstance(k.value, str))
                        for k in node.keys
                    ),
                    "escape": escape_routes(node, parents),
                }
                if len(keys & CREATIVE_KEYS) >= 2:
                    yield "creative", node, {**row, "pre_311": bool(keys & PRE_311_KEYS)}
                elif len(keys & PRICING_KEYS) >= 2:
                    yield "pricing", node, row


def scan(paths: list[str]):
    """``(creative_rows, pricing_rows)`` — structure only, no model is imported.

    Frozen: ``tests/unit/test_architecture_marked_malformation.py`` keys its allowlist
    and its identity exemptions on the ``(file, line)`` pairs this returns, and it runs
    in ``make quality``. Fields may be added; the site SET may not move.
    """
    creatives: list[dict[str, Any]] = []
    pricing: list[dict[str, Any]] = []
    for kind, _node, row in _iter_sites(paths):
        (creatives if kind == "creative" else pricing).append(row)
    return creatives, pricing


# ---------------------------------------------------------------------------
# Verdicts: the pinned models, imported lazily so scan() stays stdlib-only
# ---------------------------------------------------------------------------


class PinnedModelsUnavailable(RuntimeError):
    """The pinned models could not be imported, so no verdict can be rendered.

    Raised rather than degraded on purpose. A census that quietly dropped its verdict
    leg would report every site UNDECIDABLE_STATICALLY and print a clean-looking run —
    "could not measure" collapsing into "fine", which is the exact disease this
    instrument exists to remove. It refuses instead.
    """


ENVIRONMENT_HINT = (
    "the pinned models could not be imported, so no site can be graded.\n"
    "This census validates every site against the PINNED models and must run inside the "
    "project environment:\n\n"
    "    uv run python scripts/audit/creative_literal_sites.py --scope bdd|tests [--json]\n\n"
    "Bare `python3` reaches neither `src` nor the `adcp` pin. The verdict leg is not "
    "optional: without it every site would report as unmeasured and the run would look "
    "clean, which is the defect this instrument exists to remove."
)


def _pinned_models() -> dict[str, Any]:
    """The live pinned classes, plus everything derived from them, read once."""
    import typing

    try:
        from src.core.schemas.creative import CreativeAssetRequest
        from src.core.schemas.pricing import PricingOption
    except ImportError as exc:
        raise PinnedModelsUnavailable(ENVIRONMENT_HINT) from exc

    members = typing.get_args(PricingOption.model_fields["root"].annotation)
    by_discriminator = {}
    for member in members:
        for literal in typing.get_args(member.model_fields["pricing_model"].annotation):
            by_discriminator[literal] = member
    return {
        "creative": CreativeAssetRequest,
        "pricing_root": PricingOption,
        "pricing_members": members,
        "pricing_by_discriminator": by_discriminator,
        "observed_extra_mode": {
            model.__name__: model.model_config.get("extra") for model in (CreativeAssetRequest, *members)
        },
    }


def _declared(model: Any) -> set[str]:
    """Every key *model* will accept: field names plus their aliases."""
    names = set()
    for name, field in model.model_fields.items():
        names.add(field.alias or name)
        names.add(name)
    return names


def _required(model: Any) -> set[str]:
    return {(field.alias or name) for name, field in model.model_fields.items() if field.is_required()}


def _model_fit(keys: set[str], declared: set[str]) -> float:
    """Fraction of the site's constant keys that this model actually declares."""
    return round(len(keys & declared) / len(keys), 4) if keys else 0.0


def _reason_kinds(reasons: set[str]) -> list[str]:
    return sorted({KIND_UNDECLARED_KEY if r == EXTRA_FORBIDDEN else KIND_MALFORMED for r in reasons})


def _obligation(reasons: set[str]) -> str | None:
    """Invalid-request DOMINATES; only an exactly-``{extra_forbidden}`` set is the other."""
    if not reasons:
        return None
    if reasons - {EXTRA_FORBIDDEN}:
        return OBLIGATION_INVALID_REQUEST
    return OBLIGATION_UNDECLARED_FIELD_DROPPED


def _key_shape_reasons(row: dict[str, Any], declared: set[str], required: set[str], extra_mode: str | None):
    """Reasons provable from the KEY SHAPE alone, with no value ever evaluated."""
    keys = set(row["keys"])
    reasons: set[str] = set()
    detail: list[str] = []
    foreign = keys - declared
    if foreign and extra_mode == "forbid":
        reasons.add(EXTRA_FORBIDDEN)
        detail.append(f"undeclared keys {sorted(foreign)}")
    absent = required - keys
    if absent and not row["spread"] and not row["dynamic_keys"]:
        reasons.add("missing")
        detail.append(f"required keys absent {sorted(absent)}")
    return reasons, "; ".join(detail)


def _full_eval(node: ast.Dict, model: Any):
    """``(reached, reasons, detail)`` — the payload evaluated and put to the pinned model."""
    import pydantic

    try:
        payload = ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return False, set(), ""
    try:
        model.model_validate(payload)
    except pydantic.ValidationError as exc:
        errors = exc.errors()
        return True, {e["type"] for e in errors}, "; ".join(f"{e['type']}{tuple(e['loc'])}" for e in errors)
    return True, set(), ""


def _unevaluable_detail(node: ast.Dict) -> str:
    """The AST node kinds that blocked evaluation — the answer to "why not measured"."""
    blockers = sorted(
        {
            type(value).__name__
            for value in ast.walk(node)
            if isinstance(value, ast.Name | ast.Attribute | ast.Call | ast.JoinedStr | ast.Starred)
        }
    )
    return f"values not statically knowable: {blockers}" if blockers else "value not statically knowable"


def _resolve_pricing_model(node: ast.Dict, pinned: dict[str, Any]):
    """``(model, declared, required, dotted_path)`` for the union member this site names.

    With no constant ``pricing_model`` the only sound key-shape proof is a key that is
    in NO member, so ``declared`` widens to the union and ``required`` narrows to the
    intersection.
    """
    for key, value in zip(node.keys, node.values, strict=False):
        if isinstance(key, ast.Constant) and key.value == "pricing_model" and isinstance(value, ast.Constant):
            member = pinned["pricing_by_discriminator"].get(value.value)
            if member is not None:
                return member, _declared(member), _required(member), f"src.core.schemas.pricing.{member.__name__}"
    members = pinned["pricing_members"]
    return (
        pinned["pricing_root"],
        set().union(*(_declared(m) for m in members)),
        set.intersection(*(_required(m) for m in members)),
        PRICING_MODEL,
    )


def verdict_for(kind: str, node: ast.Dict, row: dict[str, Any], pinned: dict[str, Any]) -> dict[str, Any]:
    """The three-state verdict for one site, and the authority that rendered it."""
    if kind == "creative":
        model = pinned["creative"]
        declared, required, dotted = _declared(model), _required(model), CREATIVE_MODEL
    else:
        model, declared, required, dotted = _resolve_pricing_model(node, pinned)
    extra_mode = pinned["observed_extra_mode"].get(getattr(model, "__name__", ""), None)
    if kind == "pricing" and model is pinned["pricing_root"]:
        # The RootModel wrapper carries no extra policy; every member of its union does.
        extra_mode = pinned["observed_extra_mode"].get(pinned["pricing_members"][0].__name__)

    keys = set(row["keys"])
    fit = _model_fit(keys, declared)
    base = {
        "model": dotted,
        "model_fit": fit,
        "proof_scope": "as-written" if row["escape"] else "as-dispatched",
        "reasons": [],
        "kinds": [],
        "obligation": None,
    }

    identifying = IDENTIFYING_KEY[kind]
    if identifying not in keys:
        return {
            **base,
            "verdict": UNDECIDABLE,
            "verdict_source": SOURCE_MEMBERSHIP,
            "verdict_detail": (
                f"no {identifying}: this dict is not evidence of a {kind} payload, so "
                f"{dotted} is not the model that grades it"
            ),
        }

    if fit < MODEL_FIT_THRESHOLD:
        return {
            **base,
            "verdict": UNDECIDABLE,
            "verdict_source": SOURCE_MODEL_FIT,
            "verdict_detail": (
                f"model_fit {fit} < {MODEL_FIT_THRESHOLD}; foreign keys {sorted(keys - declared)} "
                f"suggest the site heuristic selected this dict, not {dotted}"
            ),
        }

    shape_reasons, shape_detail = _key_shape_reasons(row, declared, required, extra_mode)
    reached, eval_reasons, eval_detail = _full_eval(node, model)

    if not reached:
        if shape_reasons:
            return {
                **base,
                "verdict": REJECTED,
                "verdict_source": SOURCE_KEY_SHAPE,
                "verdict_detail": shape_detail,
                "reasons": sorted(shape_reasons),
                "kinds": _reason_kinds(shape_reasons),
                "obligation": _obligation(shape_reasons),
            }
        return {
            **base,
            "verdict": UNDECIDABLE,
            "verdict_source": SOURCE_UNEVALUABLE,
            "verdict_detail": _unevaluable_detail(node),
        }

    if not eval_reasons:
        # The pin was handed the real payload and took it. That is ground truth, and it
        # overrules a key-shape inference — a disagreement here would mean the shape rule
        # is unsound, so it is REPORTED rather than silently resolved either way.
        detail = (
            f"model accepted the evaluated payload; key shape had inferred: {shape_detail}" if shape_reasons else ""
        )
        return {**base, "verdict": ACCEPTED, "verdict_source": SOURCE_FULL_EVAL, "verdict_detail": detail}

    reasons = eval_reasons | shape_reasons
    return {
        **base,
        "verdict": REJECTED,
        "verdict_source": SOURCE_FULL_EVAL,
        "verdict_detail": "; ".join(filter(None, (eval_detail, shape_detail))),
        "reasons": sorted(reasons),
        "kinds": _reason_kinds(reasons),
        "obligation": _obligation(reasons),
    }


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _not_examined() -> list[dict[str, Any]]:
    return [
        {"kind": kind, "glob": pattern, "files": len(glob.glob(pattern, recursive=True))}
        for kind, pattern in NOT_EXAMINED
    ]


def census(paths: list[str]) -> dict[str, Any]:
    """Every site, its verdict, the three-state tallies, and what was NOT measured."""
    pinned = _pinned_models()
    rows: dict[str, list[dict[str, Any]]] = {"creative": [], "pricing": []}
    for kind, node, row in _iter_sites(paths):
        rows[kind].append({**row, **verdict_for(kind, node, row, pinned)})
    return {
        "creatives": rows["creative"],
        "pricing": rows["pricing"],
        "counts": {
            kind: {state: sum(1 for r in kind_rows if r["verdict"] == state) for state in VERDICTS}
            for kind, kind_rows in rows.items()
        },
        "config": {
            "observed_extra_mode": pinned["observed_extra_mode"],
            "model_fit_threshold": MODEL_FIT_THRESHOLD,
        },
        "not_examined": _not_examined(),
    }


def _verdict_line(label: str, counts: dict[str, int]) -> str:
    total = sum(counts.values())
    share = f"{counts[UNDECIDABLE] / total:.0%}" if total else "0%"
    return (
        f"  {label:<26}{total:5}   REJECTED {counts[REJECTED]:4}"
        f"   ACCEPTED {counts[ACCEPTED]:4}   UNDECIDABLE {counts[UNDECIDABLE]:4}  ({share} unmeasured)"
    )


def main() -> int:
    # Run as a script (``python3 scripts/audit/...``) the repo root is not on the path,
    # and the verdict leg imports the pinned models from ``src``. Done here, not at
    # import time, so the two committed guards keep importing a stdlib-only module.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=sorted(SCOPES), default="tests")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        report = census(SCOPES[args.scope])
    except PinnedModelsUnavailable as exc:
        print(f"{pathlib.Path(__file__).name}: {exc}", file=sys.stderr)
        return 2
    creatives, pricing, counts = report["creatives"], report["pricing"], report["counts"]

    if args.json:
        json.dump(report, sys.stdout, indent=1)
        return 0

    modes = sorted(set(report["config"]["observed_extra_mode"].values()), key=str)
    print(f"scope={args.scope}   observed extra mode={modes}   model_fit threshold={MODEL_FIT_THRESHOLD}")
    print("  (mode is READ off the live classes, never set: it is frozen at class-definition time)\n")

    print(_verdict_line("creative literals", counts["creative"]))
    hand = [c for c in creatives if c["hand_built"]]
    print(f"    hand-built (no owner)      {len(hand):5}   <- the migration target")
    print(f"    carrying pre-3.1.1 fields  {sum(1 for c in creatives if c['pre_311']):5}")
    print(_verdict_line("pricing-option literals", counts["pricing"]))
    print(f"    hand-built (no owner)      {sum(1 for p in pricing if p['hand_built']):5}")

    for label, source in (
        ("not a payload (no id key)", SOURCE_MEMBERSHIP),
        ("below the model-fit bar", SOURCE_MODEL_FIT),
        ("values unevaluable", SOURCE_UNEVALUABLE),
    ):
        n = sum(1 for r in [*creatives, *pricing] if r["verdict_source"] == source)
        print(f"    set aside: {label:<26}{n:5}")

    undecided = collections.Counter(c["file"] for c in hand if c["verdict"] == UNDECIDABLE)
    print("\n  hand-built creative sites by file (the shard list) -- sites / of those UNDECIDABLE:")
    for f, n in collections.Counter(c["file"] for c in hand).most_common():
        print(f"    {n:4} / {undecided[f]:<4}  {f}")

    print("\n  NOT examined by this census:")
    for entry in report["not_examined"]:
        print(f"    {entry['files']:4}  {entry['kind']:<18} {entry['glob']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
