"""Developer-experience probes for the pinned adcp SDK.

Each probe is one thing a developer actually does with the SDK, with a pass criterion
that does not depend on knowing the SDK's internals. The point is a number that moves:
run it against the pin, run it again after a change, compare.

A probe FAILS when a developer doing the obvious thing gets the wrong answer or no
answer. It does not fail for an ugly name -- ergonomics are reported separately from
correctness, because only one of the two is worth breaking a public surface over.

Run:  cd <repo> && unset VIRTUAL_ENV && UV_FROZEN=1 uv run python scripts/audit/sdk_dx_probes.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import warnings
from typing import Any

# jsonschema deprecates RefResolver without shipping a drop-in replacement for a
# file-backed bundle. The warning is the library's, not this script's finding.
warnings.filterwarnings("ignore", category=DeprecationWarning, module="jsonschema")

import adcp
import adcp.types as T

RESULTS: list[tuple[str, str, str, str]] = []  # (id, verdict, headline, detail)

TOOLS = ("GetProducts", "CreateMediaBuy", "UpdateMediaBuy", "SyncCreatives", "GetMediaBuyDelivery")


def _bundle_root() -> pathlib.Path:
    """Return the schema bundle directory for the spec version the SDK reports.

    A literal `"3.2"` here is a defect waiting for the next pin bump: the SDK would report
    a newer spec while every probe kept grading the older bundle, and the suite would pass
    against a contract nobody ships. `adcp.get_adcp_spec_version()` is the authority.

    The directory does not always equal the spec version -- the SDK reports `3.2.3` and
    bundles `3.2` -- so this takes the exact directory when it exists, else the longest
    dotted prefix of the spec version. No match raises, because a probe suite that silently
    grades the wrong bundle is worse than one that does not run.
    """
    spec = adcp.get_adcp_spec_version()
    root = pathlib.Path(adcp.__file__).parent / "_schemas"
    names = {p.name for p in root.iterdir() if p.is_dir()}
    if spec in names:
        return root / spec
    parts = spec.split(".")
    for stop in range(len(parts) - 1, 0, -1):
        if (candidate := ".".join(parts[:stop])) in names:
            return root / candidate
    raise FileNotFoundError(f"no schema bundle for spec version {spec!r} under {root}; found {sorted(names)}")


SCHEMAS = _bundle_root()


def record(pid: str, ok: bool | None, headline: str, detail: str = "") -> None:
    verdict = "PASS" if ok else ("FAIL" if ok is False else "PARTIAL")
    RESULTS.append((pid, verdict, headline, detail))


def _schema(rel: str) -> dict[str, Any] | None:
    p = SCHEMAS / rel
    return json.loads(p.read_text()) if p.exists() else None


# ─── S1 · Can I find a tool's types by a predictable name? ────────────────────────
def s1_discovery() -> None:
    missing, found = [], []
    for tool in TOOLS:
        req = getattr(T, f"{tool}Request", None)
        resp = getattr(T, f"{tool}Response", None)
        (found if req is not None else missing).append(f"{tool}Request")
        (found if resp is not None else missing).append(f"{tool}Response")
    record(
        "S1 discovery",
        not missing,
        f"{len(found)}/{len(found) + len(missing)} tool types reachable as adcp.types.<Tool>{{Request,Response}}",
        f"missing: {missing}" if missing else "",
    )


# ─── S2 · Can I name the arms of a multi-arm response? ────────────────────────────
def s2_named_branches() -> None:
    rows = []
    for tool in TOOLS:
        doc = _schema(f"media-buy/{_kebab(tool)}-response.json") or _schema(f"creative/{_kebab(tool)}-response.json")
        if doc is None:
            continue
        arms = doc.get("oneOf") or doc.get("anyOf") or []
        if not arms:
            continue
        for i, arm in enumerate(arms):
            title = arm.get("title")
            by_title = getattr(T, title, None) if title else None
            # A hand alias with a `Response` suffix is a name, but not the schema's own name;
            # it is reported so the headline is not read as "no name at all" (§8 item 19).
            by_alias = getattr(T, f"{title}Response", None) if title else None
            positional = getattr(T, f"{tool}Response{i + 1}", None)
            rows.append((tool, i, title, by_title is not None, positional is not None, by_alias is not None))
    unreachable = [r for r in rows if not r[3]]
    aliased = sum(1 for r in unreachable if r[5])
    record(
        "S2 named branches",
        not unreachable,
        f"{len(rows) - len(unreachable)}/{len(rows)} response arms importable by their schema title "
        f"({aliased} of the rest reachable under a `<Title>Response` hand alias)",
        "; ".join(f"{t}.arm{i} title={ti!r} positional_exists={p} alias={a}" for t, i, ti, _, p, a in unreachable[:6]),
    )


def _kebab(camel: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", camel).lower()


# ─── S3 · Does the model accept exactly what the schema accepts? ──────────────────
def s3_validation_agreement() -> None:
    """The one case measured in the design doc, run as a probe rather than quoted."""
    try:
        import jsonschema
        from adcp.types.domains.media_buy.media_buy_commitment_response import Warning as NarrowedWarning
    except Exception as exc:  # noqa: BLE001
        record("S3 validation agreement", None, "could not run", f"{type(exc).__name__}: {exc}")
        return

    doc = _schema("media-buy/media-buy-commitment-response.json")
    node = _find_warnings_items(doc) if doc else None
    if node is None:
        record("S3 validation agreement", None, "narrowed warnings[] node not located", "")
        return

    payload = {
        "code": "fields_ignored_due_to_precedence",
        "message": "m",
        "affected_resource": {"resource_type": "media_buy", "media_buy_id": "mb_1"},
    }
    resolver = jsonschema.RefResolver(base_uri=f"{(SCHEMAS).as_uri()}/", referrer=node)
    try:
        jsonschema.validate(payload, node, resolver=resolver)
        schema_accepts = True
    except Exception:  # noqa: BLE001
        schema_accepts = False
    try:
        NarrowedWarning.model_validate(payload)
        model_accepts = True
    except Exception:  # noqa: BLE001
        model_accepts = False

    record(
        "S3 validation agreement",
        schema_accepts == model_accepts,
        f"schema_accepts={schema_accepts} model_accepts={model_accepts} on a narrowed enum",
        "the two validators the SDK ships disagree about the same bytes" if schema_accepts != model_accepts else "",
    )


def _find_warnings_items(node: Any) -> dict[str, Any] | None:
    if isinstance(node, dict):
        w = node.get("properties", {}).get("warnings") if isinstance(node.get("properties"), dict) else None
        if isinstance(w, dict) and isinstance(w.get("items"), dict) and "allOf" in json.dumps(w["items"])[:400]:
            return w["items"]
        for v in node.values():
            if (found := _find_warnings_items(v)) is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            if (found := _find_warnings_items(v)) is not None:
                return found
    return None


# ─── S4 · Can I emit an error, including a code the registry does not list? ───────
def s4_errors() -> None:
    Err = getattr(T, "Error", None) or getattr(adcp, "Error", None)
    code_enum = getattr(T, "ErrorCode", None)
    if Err is None:
        record("S4 errors", False, "Error not reachable from adcp or adcp.types", "")
        return
    try:
        Err.model_validate({"code": "UNSUPPORTED_FEATURE", "message": "m"})
        registered = True
    except Exception:  # noqa: BLE001
        registered = False
    try:
        Err.model_validate({"code": "SELLER_SPECIFIC_THING", "message": "m"})
        unregistered = True
    except Exception:  # noqa: BLE001
        unregistered = False
    record(
        "S4 errors",
        registered and unregistered,
        f"Error importable=True code_enum_exported={code_enum is not None} "
        f"registered_code={registered} unregistered_code={unregistered}",
        "error-code.json declares the vocabulary documentary, so an unregistered code must validate"
        if not unregistered
        else "",
    )


# ─── S5 · Can I emit a non-blocking observation? ──────────────────────────────────
def s5_warnings() -> None:
    W = getattr(T, "Warning", None) or getattr(adcp, "Warning", None)
    WC = getattr(T, "WarningCode", None) or getattr(adcp, "WarningCode", None)
    vocab = (_schema("enums/warning-code.json") or {}).get("enum") or []
    detail = f"vocabulary={len(vocab)} members {vocab}"
    if W is None or WC is None:
        record(
            "S5 warnings",
            False,
            f"Warning exported={W is not None} WarningCode exported={WC is not None}",
            detail + " · reachable only by deep module path",
        )
        return
    try:
        W.model_validate(
            {
                "code": "capability_requested_not_honoured",
                "message": "m",
                "affected_resource": {"resource_type": "media_buy", "media_buy_id": "mb_1"},
            }
        )
        extensible = True
    except Exception:  # noqa: BLE001
        extensible = False
    record("S5 warnings", extensible, f"unregistered warning code accepted={extensible}", detail)


# ─── S6 · Can I extend a response type with internal fields? ──────────────────────
def s6_extension() -> None:
    """salesagent's actual use case: subclass a success arm to attach a serializer mixin."""
    from adcp.types import aliases

    src = pathlib.Path(aliases.__file__).read_text()
    annotated = len(re.findall(r"^\s*\w+\s*:\s*TypeAlias\s*=", src, re.M))
    plain = len(re.findall(r"^\s*\w+\s*=\s*\w", src, re.M))
    record(
        "S6 extension",
        annotated == 0,
        f"semantic aliases usable as a base class: {plain} plain, {annotated} TypeAlias-annotated",
        "mypy refuses an explicit TypeAlias as a base class, so the annotated ones cannot be subclassed"
        if annotated
        else "",
    )


# ─── S7 · Does a semantic name resolve to the class the schema describes? ─────────
def s7_binding() -> None:
    from adcp.types import aliases
    from adcp.types.domains.media_buy import create_media_buy_response as arm_mod

    alias = getattr(aliases, "CreateMediaBuySuccessResponse", None)
    arm = getattr(arm_mod, "CreateMediaBuyResponse1", None)
    if alias is None or arm is None:
        record("S7 binding", None, "could not resolve both names", "")
        return
    record(
        "S7 binding",
        alias is arm,
        f"semantic alias is the generated arm: {alias is arm}",
        f"alias -> {alias.__module__}.{alias.__name__}; arm -> {arm.__module__}.{arm.__name__}; "
        f"subclass={issubclass(alias, arm)}",
    )


# ─── S8 · Is a positional name in the surface a developer imports? ────────────────
def s8_positional_surface() -> None:
    exported = [n for n in (getattr(T, "__all__", None) or []) if re.search(r"\d+$", n)]
    gen = getattr(T, "_generated", None)
    gen_all = [n for n in (getattr(gen, "__all__", None) or []) if re.search(r"\d+$", n)] if gen else []
    record(
        "S8 positional surface",
        not exported and not gen_all,
        f"positional names in adcp.types.__all__={len(exported)} · in _generated.__all__={len(gen_all)}",
        f"e.g. {sorted(set(exported + gen_all))[:6]}" if (exported or gen_all) else "",
    )


# ─── S11 · Does a root with branches hand back the branch's class? ────────────────
def s11_branch_dispatch() -> None:
    """DX11: `Root.model_validate(doc)` returns the branch the document satisfies.

    Two populations. The roots that declare `_branch_models` (the pin's `BranchDispatchMixin`)
    are checked on a document built from each branch's own required fields, so the probe does
    not depend on knowing a payload in advance. The tool response roots in TOOLS are checked
    separately, because a plain `types.UnionType` has no dispatch of its own -- a TypeAdapter
    over it picks an arm by pydantic's smart union, and the public name cannot be constructed.
    """
    import typing

    import adcp.types._eager as eager

    roots = sorted(
        {
            getattr(eager, n)
            for n in dir(eager)
            if isinstance(getattr(eager, n), type) and "_branch_models" in vars(getattr(eager, n))
        },
        key=lambda c: c.__name__,
    )
    dispatched, stuck, ambiguous = [], [], []
    for root in roots:
        for branch in root._branch_models():
            doc = {n: _placeholder(f) for n, f in branch.model_fields.items() if f.is_required()}
            try:
                got = root.model_validate(doc)
            except Exception:  # noqa: BLE001
                # A placeholder that fails a scalar constraint is this probe's limit, not DX11's.
                continue
            if type(got) is branch:
                dispatched.append(branch.__name__)
            elif type(got) is root:
                stuck.append(f"{root.__name__} stayed the base for {branch.__name__}")
            else:
                # The required-only document also satisfies a sibling; the probe cannot tell a
                # dispatch defect from an under-specified placeholder here, so it is reported, not graded.
                ambiguous.append(f"{branch.__name__}->{type(got).__name__}")
    # (tool, verdict) where the verdict is one of: "dispatch" (a class carrying the mixin),
    # "single class" (no branches to dispatch), "union" (a bare typing union: no dispatch).
    responses: list[tuple[str, str]] = []
    for tool in TOOLS:
        resp = getattr(T, f"{tool}Response", None)
        if resp is None:
            continue
        if not isinstance(resp, type) and typing.get_args(resp):
            responses.append((tool, "union"))
        elif isinstance(resp, type) and "_branch_models" in vars(resp):
            responses.append((tool, "dispatch"))
        else:
            responses.append((tool, "single class"))
    roots_ok = bool(dispatched) and not stuck
    responses_ok = all(v != "union" for _, v in responses)
    record(
        "S11 branch dispatch",
        True if (roots_ok and responses_ok) else (None if roots_ok else False),
        f"{len(roots)} exported roots declare branches; {len(dispatched)} branch documents came back as their "
        f"branch, {len(stuck)} as the base, {len(ambiguous)} as a sibling the document also satisfies; "
        f"tool responses without dispatch: {sum(v == 'union' for _, v in responses)}/{len(responses)}",
        "; ".join([f"{t}Response: {v}" for t, v in responses] + stuck + ambiguous),
    )


def _placeholder(field: Any) -> Any:
    """A value that satisfies the field's annotation often enough to select a branch."""
    import enum
    import typing

    ann = field.annotation
    origin = typing.get_origin(ann)
    if origin is typing.Literal:
        return typing.get_args(ann)[0]
    if origin in (typing.Union, getattr(__import__("types"), "UnionType", None)):
        for arg in typing.get_args(ann):
            if arg is not type(None):
                return _placeholder(type("F", (), {"annotation": arg})())
    if isinstance(ann, type) and issubclass(ann, enum.Enum):
        return next(iter(ann)).value
    if ann is str or (isinstance(ann, type) and issubclass(ann, str)):
        return "x" * 40
    if ann in (int, float):
        return 1
    if ann is bool:
        return True
    if origin is list:
        return []
    if origin is dict or ann is dict:
        return {}
    return {}


# ─── S12 · Does a refusal name the JSON-Schema keyword? ───────────────────────────
def s12_keyword_refusal() -> None:
    """DX12: a document satisfying no branch is refused with the combinator keyword as its type.

    `core/error.json` requires `issues[].keyword` to be a JSON-Schema keyword. The pin's
    `name_the_combinator` supplies `anyOf`/`oneOf` on the dispatch roots; everything else is a
    pydantic type name (`missing`, `string_too_short`), which a seller must map by hand.
    """
    import adcp.types._eager as eager
    from pydantic import ValidationError

    keywords = {
        "anyOf",
        "oneOf",
        "allOf",
        "required",
        "enum",
        "const",
        "type",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "additionalProperties",
    }
    roots = [
        getattr(eager, n)
        for n in dir(eager)
        if isinstance(getattr(eager, n), type) and "_branch_models" in vars(getattr(eager, n))
    ]
    named, unnamed, types_seen = 0, 0, set()
    for root in roots:
        try:
            root.model_validate({"__no_branch__": 1})
        except ValidationError as exc:
            errs = exc.errors(include_url=False)
            types_seen |= {e["type"] for e in errs}
            if any(e["type"] in {"anyOf", "oneOf"} for e in errs):
                named += 1
            else:
                unnamed += 1
        except Exception:  # noqa: BLE001
            unnamed += 1
    non_schema = sorted(t for t in types_seen if t not in keywords)
    combinators_named = bool(roots) and unnamed == 0
    record(
        "S12 keyword refusal",
        True if (combinators_named and not non_schema) else (None if combinators_named else False),
        f"{named}/{len(roots)} dispatch roots refuse a no-branch document with the combinator keyword; "
        f"{len(non_schema)} pydantic type names leak beside it",
        f"non-keyword types seen: {non_schema} (root-required fields are reported in pydantic's spelling, not as `required`)"
        if non_schema
        else "",
    )


def main() -> int:
    for probe in (
        s1_discovery,
        s2_named_branches,
        s3_validation_agreement,
        s4_errors,
        s5_warnings,
        s6_extension,
        s7_binding,
        s8_positional_surface,
        s11_branch_dispatch,
        s12_keyword_refusal,
    ):
        try:
            probe()
        except Exception as exc:  # noqa: BLE001
            record(probe.__name__, None, "probe raised", f"{type(exc).__name__}: {exc}")

    width = max(len(r[0]) for r in RESULTS)
    print(f"\nadcp pin: {getattr(adcp, '__version__', '?')}\n")
    for pid, verdict, headline, detail in RESULTS:
        print(f"{verdict:8} {pid:{width}}  {headline}")
        if detail:
            print(f"{'':8} {'':{width}}  └─ {detail}")
    bad = sum(1 for r in RESULTS if r[1] != "PASS")
    print(f"\n{len(RESULTS) - bad}/{len(RESULTS)} pass\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
