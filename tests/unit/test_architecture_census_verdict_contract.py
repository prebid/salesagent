"""Guard: the literal census answers "is this invalid" with the PIN, and abstains out loud.

``scripts/audit/creative_literal_sites.py`` is the instrument every numeric target in
salesagent-b341x's creative half was read off. Today it answers "is this literal invalid"
with ``omits_assets`` -- a PROXY for "the pinned model rejects this" -- and the proxy is
wrong in both directions. Three children inherited that error as their scope.

WHAT THIS FILE PINS, one test per obligation, all of it measured on this tree first:

1. A THREE-STATE VERDICT. ``REJECTED_BY_PIN`` / ``ACCEPTED_BY_PIN`` /
   ``UNDECIDABLE_STATICALLY``, and no field anywhere that sums two of them into
   "invalid". A count that hides its own uncertainty is what produced the wrong scopes.
2. THE FULL REASON SET, never ``errors()[0]``. Measured: the payload
   ``{creative_id, name, format_id, unknown_key_xyz}`` returns BOTH ``missing('assets')``
   AND ``extra_forbidden('unknown_key_xyz')``, and pydantic emits ``missing`` FIRST -- so
   a classifier reading the first error is decided by emission order rather than by the
   payload. This is the defect that produced two contradictory measurements of the same
   population; it is the most important assertion here.
3. THE TWO OBLIGATIONS STAY DISTINGUISHABLE. Any non-``extra_forbidden`` reason means an
   environment-INDEPENDENT invalid-request obligation, and it DOMINATES when both are
   present. A reason set of exactly ``{extra_forbidden}`` means something else entirely:
   the ``_accept_only_declared_fields`` guarantee at ``src/core/schemas/_base.py:630``
   that the field never reaches the implementation -- not a wire-error assertion.
   Measured, scope tests: 108 of 230 sites carry ``extra_forbidden`` among their reasons,
   103 carry it PLUS another, and only 5 are exactly-``{extra_forbidden}``. Collapsing
   those 103 into the second obligation is the error the dominance rule refuses.
4. PROOF SCOPE. A literal census can only speak about the literal AS WRITTEN.
   ``uc006_sync_creatives.py``'s 5008/5028/5401 bind the display to a name, append it to
   ``ctx["creatives"]``, and a LATER Given repairs it with ``setdefault("assets",
   {}).update(...)`` before dispatch. A static reading has been fooled by that family
   twice in this epic. ``proof_scope`` names which claim is being made.
5. ABSTENTION ON NON-PAYLOADS. ``given_entities.py:271-273`` are format-catalog entries
   whose keys are ``{name, assets}`` -- both real fields, so ``model_fit`` is 1.0 and NO
   fit threshold can exclude them. The identifying key (``creative_id`` /
   ``pricing_option_id``) is the test that can.
6. THE EXTRA MODE IS OBSERVED, NEVER SET. ``extra=get_pydantic_extra_mode()`` is frozen at
   CLASS-DEFINITION time, so setting ``ENVIRONMENT`` after import changes nothing and
   setting it before import leaks into every other model in the process.
7. THE FROZEN API. ``tests/unit/test_architecture_marked_malformation.py`` and
   ``tests/unit/test_architecture_no_pre_311_creative_builders.py`` both import from this
   module and both run in ``make quality``. Neither reads a verdict field, so verdicts are
   additive -- but ``scan``/``SCOPES``/``CREATIVE_KEYS``/``PRE_311_KEYS`` must not move.

The census fixtures below are the REAL shapes, copied from the sites named above, not
invented ones: the escape fixture is ``given_creative_output_format_ids_present``, the
catalog fixture is ``given_seller_creative_agent_various_assets``, and the as-dispatched
fixture is ``test_operator_agent_mcp_seam_egress.py:356``.
"""

from __future__ import annotations

import ast
import collections
import importlib
import os
from pathlib import Path
from typing import Any

import pydantic
import pytest

from scripts.audit.creative_literal_sites import CREATIVE_KEYS, PRE_311_KEYS, SCOPES, scan
from src.core.schemas.creative import CreativeAssetRequest
from src.core.schemas.pricing import CpmPricingOption, PricingOption
from tests.unit._architecture_helpers import assert_guard_subject_resolves, iter_call_expressions, repo_root

CENSUS_MODULE = "scripts.audit.creative_literal_sites"

#: The API the two committed guards import. Verified by reading both: the malformation
#: guard takes ``scan``/``SCOPES``, the pre-3.1.1 guard takes the two key sets, and
#: NEITHER reads a verdict field. So verdicts may be added; these may not move.
FROZEN_API = ("scan", "SCOPES", "CREATIVE_KEYS", "PRE_311_KEYS")

#: Booleans that predate the verdict and state a STRUCTURAL fact, not a validity one.
#: Any other boolean-ish field name is the proxy coming back.
VERDICT_SHAPED_WORDS = ("valid", "invalid", "rejected", "accepted", "omits", "malformed", "conformant")

#: A format identifier the pinned model accepts, so the fixtures below fail for the reason
#: each is named after and not for a second, accidental one.
FORMAT_ID_SOURCE = '{"agent_url": "https://creative.example.com", "id": "display_300x250"}'


# ---------------------------------------------------------------------------
# Reaching the subject
# ---------------------------------------------------------------------------


def _census_attr(name: str) -> Any:
    """The named census export, or a failure that says what is missing and why it matters."""
    module = importlib.import_module(CENSUS_MODULE)
    attr = getattr(module, name, None)
    assert attr is not None, (
        f"{CENSUS_MODULE}.{name} does not exist. The census still answers 'is this literal "
        "invalid' with omits_assets, a proxy for the pinned model's verdict, so every count "
        "read off it carries a validity claim nothing measured (salesagent-b341x.18)."
    )
    return attr


def _report(tmp_path: Path, sources: dict[str, str]) -> dict[str, Any]:
    """Run the census over one throwaway file per named source."""
    for name, source in sources.items():
        (tmp_path / f"{name}.py").write_text(source)
    return _census_attr("census")([str(tmp_path / "*.py")])


def _rows_by_name(tmp_path: Path, sources: dict[str, str], kind: str = "creatives") -> dict[str, dict[str, Any]]:
    """``{source name: its single census row}`` -- one site per fixture file, enforced."""
    report = _report(tmp_path, sources)
    rows: dict[str, dict[str, Any]] = {}
    for row in report[kind]:
        stem = Path(row["file"]).stem
        assert stem not in rows, f"fixture {stem!r} produced more than one {kind} site; split it"
        rows[stem] = row
    assert set(rows) == set(sources), (
        f"census reported {kind} sites {sorted(rows)}, expected {sorted(sources)}. A fixture the "
        "site heuristic no longer selects grades nothing."
    )
    return rows


def _row(tmp_path: Path, source: str, kind: str = "creatives") -> dict[str, Any]:
    return _rows_by_name(tmp_path, {"site": source}, kind)["site"]


def _pin_reasons(payload: dict[str, Any], model: type[pydantic.BaseModel]) -> set[str]:
    """Every pydantic error type *model* raises on *payload* -- derived, never transcribed."""
    try:
        model.model_validate(payload)
    except pydantic.ValidationError as exc:
        return {error["type"] for error in exc.errors()}
    return set()


# ---------------------------------------------------------------------------
# Fixtures: the real shapes, from the sites this task named
# ---------------------------------------------------------------------------

#: ``given_creative_output_format_ids_present`` (uc006_sync_creatives.py:5008). The display
#: is bound to a name and appended into ``ctx["creatives"]``; a later Given repairs it.
ESCAPING_CREATIVE = """
def given_creative_output_format_ids_present(ctx):
    fmt = ctx["env"].generative_format()
    creative_payload = {
        "creative_id": "creative-generative-part-001",
        "name": "Generative Partition Creative",
        "format_id": fmt,
    }
    ctx.setdefault("creatives", []).append(creative_payload)
"""

#: ``test_operator_agent_mcp_seam_egress.py:356``. Inline, never bound to a name, so
#: nothing in the enclosing function can reach it between the literal and the call.
AS_DISPATCHED_CREATIVE = """
def send(transport):
    return transport.dispatch(manifest={"creative_id": "c123", "name": "Banner Ad", "assets": {}})
"""

#: ``given_seller_creative_agent_various_assets`` (given_entities.py:271-273). A FORMAT
#: CATALOG: keys {name, assets} are both real model fields, so model_fit is 1.0.
FORMAT_CATALOG = """
def given_seller_creative_agent_various_assets(ctx):
    ctx["creative_agent_formats"] = [
        {"name": "image-format", "assets": [{"type": "image"}]},
    ]
"""

CONFORMANT_CREATIVE = f"""
def send(transport):
    return transport.dispatch(manifest={{
        "creative_id": "c1",
        "name": "Banner",
        "format_id": {FORMAT_ID_SOURCE},
        "assets": {{}},
    }})
"""

#: Conformant key SHAPE, every value a runtime name. The case a naive fix reports as valid.
UNEVALUABLE_CREATIVE = """
def send(transport, cid, nm, fid, slots):
    return transport.dispatch(manifest={"creative_id": cid, "name": nm, "format_id": fid, "assets": slots})
"""

BOTH_REASONS_CREATIVE = f"""
def send(transport):
    return transport.dispatch(manifest={{
        "creative_id": "c1",
        "name": "Banner",
        "format_id": {FORMAT_ID_SOURCE},
        "unknown_key_xyz": 1,
    }})
"""

MISSING_ONLY_CREATIVE = f"""
def send(transport):
    return transport.dispatch(manifest={{
        "creative_id": "c1",
        "name": "Banner",
        "format_id": {FORMAT_ID_SOURCE},
    }})
"""

EXTRA_ONLY_CREATIVE = f"""
def send(transport):
    return transport.dispatch(manifest={{
        "creative_id": "c1",
        "name": "Banner",
        "format_id": {FORMAT_ID_SOURCE},
        "assets": {{}},
        "unknown_key_xyz": 1,
    }})
"""

CONFORMANT_PRICING = """
def send(transport):
    return transport.dispatch(option={
        "pricing_option_id": "po1",
        "pricing_model": "cpm",
        "currency": "USD",
        "fixed_price": 5.0,
    })
"""

UNIDENTIFIED_PRICING = """
def send(transport):
    return transport.dispatch(option={"pricing_model": "cpm", "currency": "USD", "fixed_price": 5.0})
"""

BOTH_REASONS_PAYLOAD = {
    "creative_id": "c1",
    "name": "Banner",
    "format_id": {"agent_url": "https://creative.example.com", "id": "display_300x250"},
    "unknown_key_xyz": 1,
}

#: The two proof legs DISAGREE here, and that is the whole point of the pair below.
#: Key shape can see the undeclared ``unknown_key_xyz``; it cannot see that
#: ``creative_id`` holds an int, because that is a VALUE. So key shape proves
#: ``{extra_forbidden}`` and the pin raises that PLUS a type error.
UNION_DISAGREEMENT_CREATIVE = f"""
def send(transport):
    return transport.dispatch(manifest={{
        "creative_id": 123,
        "name": "Banner",
        "format_id": {FORMAT_ID_SOURCE},
        "assets": {{}},
        "unknown_key_xyz": 1,
    }})
"""

#: The SAME KEY SET with every value put out of static reach, so the census can only
#: reach it by key shape. What it reports for this twin IS the key-shape leg's proof,
#: measured by the instrument rather than transcribed by this test.
SHAPE_ONLY_TWIN = """
def send(transport, cid, nm, fid, slots, junk):
    return transport.dispatch(manifest={
        "creative_id": cid,
        "name": nm,
        "format_id": fid,
        "assets": slots,
        "unknown_key_xyz": junk,
    })
"""

UNION_DISAGREEMENT_PAYLOAD = {
    "creative_id": 123,
    "name": "Banner",
    "format_id": {"agent_url": "https://creative.example.com", "id": "display_300x250"},
    "assets": {},
    "unknown_key_xyz": 1,
}


# ---------------------------------------------------------------------------
# 1. A three-state verdict, and no way back to a binary
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_scan_no_longer_emits_the_validity_proxy(tmp_path: Path) -> None:
    """``omits_assets`` is gone. A boolean named like a verdict IS the proxy."""
    (tmp_path / "site.py").write_text(MISSING_ONLY_CREATIVE)
    creatives, _pricing = scan([str(tmp_path / "*.py")])
    assert creatives, "fixture produced no site; the rest of this assertion would be vacuous"
    proxies = sorted(key for key in creatives[0] if any(word in key for word in VERDICT_SHAPED_WORDS))
    assert not proxies, (
        f"scan() still emits {proxies}. Whatever a field like this holds, it is a KEY-SHAPE "
        "heuristic wearing the pinned model's authority, and it is what every wrong count in "
        "this epic was read off. Carry keys/spread/escape instead and let verdicts() rule."
    )


@pytest.mark.arch_guard
def test_every_site_carries_one_of_three_verdicts(tmp_path: Path) -> None:
    """Rejected, accepted, undecidable -- and undecidable is a VERDICT, not an absence."""
    verdicts = _census_attr("VERDICTS")
    assert set(verdicts) == {"REJECTED_BY_PIN", "ACCEPTED_BY_PIN", "UNDECIDABLE_STATICALLY"}, (
        f"VERDICTS is {sorted(verdicts)}. Two states means the census is back to a binary; four "
        "means a state nobody has to read."
    )
    rows = _rows_by_name(
        tmp_path,
        {
            "rejected": MISSING_ONLY_CREATIVE,
            "accepted": CONFORMANT_CREATIVE,
            "undecidable": UNEVALUABLE_CREATIVE,
        },
    )
    observed = {name: row["verdict"] for name, row in rows.items()}
    assert observed == {
        "rejected": "REJECTED_BY_PIN",
        "accepted": "ACCEPTED_BY_PIN",
        "undecidable": "UNDECIDABLE_STATICALLY",
    }, (
        f"got {observed}. The third row is the regression: a conformant key shape whose values "
        "are all runtime names is exactly what a script that validates only what it can "
        "evaluate reports as valid, and that lie carries the pin's authority."
    )
    assert rows["undecidable"]["verdict_source"] != "membership-unproven", (
        "this site carries creative_id, so its membership is proven and only its VALUES are "
        "unreachable. Two undecidables with two different next actions must not share a source."
    )


@pytest.mark.arch_guard
def test_no_field_sums_two_verdicts_into_invalid(tmp_path: Path) -> None:
    """A consumer must not be able to write ``if row["invalid"]`` -- no such field exists."""
    report = _report(
        tmp_path,
        {"rejected": MISSING_ONLY_CREATIVE, "accepted": CONFORMANT_CREATIVE, "pricing": CONFORMANT_PRICING},
    )
    offenders = sorted(
        {
            f"{kind}.{key}"
            for kind in ("creatives", "pricing")
            for row in report[kind]
            for key in row
            if any(word in key for word in VERDICT_SHAPED_WORDS) and key != "verdict"
        }
    )
    assert not offenders, (
        f"{offenders} sit beside the verdict. A consumer reading one of these gets two of the "
        "three states folded into one boolean, which is the proxy this task exists to remove."
    )


@pytest.mark.arch_guard
def test_counts_carry_all_three_states_together(tmp_path: Path) -> None:
    """The undecided number ships in the SAME object as the rejected one."""
    verdicts = set(_census_attr("VERDICTS"))
    report = _report(
        tmp_path,
        {
            "rejected": MISSING_ONLY_CREATIVE,
            "accepted": CONFORMANT_CREATIVE,
            "undecidable": UNEVALUABLE_CREATIVE,
            "pricing": CONFORMANT_PRICING,
        },
    )
    counts = report["counts"]
    for kind, rows in (("creative", report["creatives"]), ("pricing", report["pricing"])):
        assert set(counts[kind]) == verdicts, (
            f"counts[{kind!r}] holds {sorted(counts[kind])}. All three keys are present or absent "
            "together, so a summary cannot be written without the undecided number."
        )
        expected = collections.Counter(row["verdict"] for row in rows)
        assert counts[kind] == {state: expected[state] for state in counts[kind]}, (
            f"counts[{kind!r}]={counts[kind]} disagrees with the rows it summarises ({dict(expected)})."
        )


# ---------------------------------------------------------------------------
# 2. The reason SET, not errors()[0]
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_verdict_carries_every_reason_not_the_first_one_emitted(tmp_path: Path) -> None:
    """The single most important assertion here: emission ORDER must not decide the verdict.

    Measured on this tree: this payload returns ``missing('assets',)`` FIRST and
    ``extra_forbidden('unknown_key_xyz',)`` second. A classifier reading ``errors()[0]``
    labels it malformed and the undeclared key disappears from the tally -- which is how
    the same 108 sites got measured twice, incompatibly, inside this one task.
    """
    expected = _pin_reasons(BOTH_REASONS_PAYLOAD, CreativeAssetRequest)
    assert len(expected) > 1, (
        "the fixture no longer produces two distinct error types against the pinned model, so "
        f"this test cannot grade the defect it exists for; got {sorted(expected)}"
    )
    row = _row(tmp_path, BOTH_REASONS_CREATIVE)
    assert set(row["reasons"]) == expected, (
        f"census reports reasons {sorted(row['reasons'])}; the pin raises {sorted(expected)}. "
        "Reading one error means the verdict is decided by pydantic's emission order rather "
        "than by the payload."
    )


@pytest.mark.arch_guard
def test_reasons_are_the_union_of_both_proof_legs(tmp_path: Path) -> None:
    """When both legs reach a site, the reason set is their UNION -- not the first one that fires.

    Key shape can only ever prove a SUBSET of what the model raises: it reads keys, so a
    wrong VALUE is invisible to it. Preferring the key-shape proof and skipping the
    evaluation therefore reports that subset, and a site whose real reason set is
    ``{extra_forbidden, string_type}`` reads as exactly-``{extra_forbidden}`` -- which
    flips its obligation from the environment-independent wire-error one to the weaker
    "the field never reaches the impl" one. That is the errors()[0] defect one leg
    further upstream, and it moved two real sites on this tree.

    The key-shape leg's proof is not transcribed here. It is MEASURED, by running the
    census over a twin carrying the same key set with every value out of static reach.
    """
    rows = _rows_by_name(tmp_path, {"union": UNION_DISAGREEMENT_CREATIVE, "shape_only": SHAPE_ONLY_TWIN})
    union, shape_only = rows["union"], rows["shape_only"]
    assert (union["verdict_source"], shape_only["verdict_source"]) == ("full-eval", "key-shape"), (
        f"got {union['verdict_source']!r} and {shape_only['verdict_source']!r}. This test compares "
        "the two legs, so one site must be reached by evaluation and the other only by key shape; "
        "two rows from the same leg would compare nothing."
    )
    assert set(union["reasons"]) == _pin_reasons(UNION_DISAGREEMENT_PAYLOAD, CreativeAssetRequest), (
        f"census reports {sorted(union['reasons'])}; the pin raises "
        f"{sorted(_pin_reasons(UNION_DISAGREEMENT_PAYLOAD, CreativeAssetRequest))}. A leg was dropped."
    )
    assert set(shape_only["reasons"]) < set(union["reasons"]), (
        f"the key-shape leg proves {sorted(shape_only['reasons'])} and the union reports "
        f"{sorted(union['reasons'])}; these must differ, or this fixture no longer exercises a "
        "disagreement between the legs and the assertion above is satisfied by either one."
    )
    assert union["obligation"] != shape_only["obligation"], (
        f"both sites carry obligation {union['obligation']!r}. The reason only key shape could "
        "not see is what makes this an invalid-request obligation; dropping it downgrades the "
        "site to the undeclared-field-dropped guarantee, which asserts nothing on the wire."
    )


# ---------------------------------------------------------------------------
# 3. The two obligations stay distinguishable, and invalid-request dominates
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_invalid_request_dominates_an_undeclared_key(tmp_path: Path) -> None:
    """A site carrying both reasons takes the invalid-request obligation, never the other.

    Measured, scope tests: 103 of the 108 sites carrying ``extra_forbidden`` carry a second
    reason too. Routing those to "the field never reaches the impl" would drop 103
    environment-INDEPENDENT invalid-request obligations on the floor.
    """
    rows = _rows_by_name(
        tmp_path,
        {"both": BOTH_REASONS_CREATIVE, "missing_only": MISSING_ONLY_CREATIVE, "extra_only": EXTRA_ONLY_CREATIVE},
    )
    assert set(rows["extra_only"]["reasons"]) == {"extra_forbidden"}, (
        "the exactly-{extra_forbidden} fixture no longer isolates that reason "
        f"({sorted(rows['extra_only']['reasons'])}), so the contrast below grades nothing"
    )
    assert rows["both"]["obligation"] == rows["missing_only"]["obligation"], (
        f"a site with BOTH reasons takes obligation {rows['both']['obligation']!r} but a site with "
        f"only the malformation takes {rows['missing_only']['obligation']!r}. The non-extra_forbidden "
        "reason is environment-independent and must dominate."
    )
    assert rows["both"]["obligation"] != rows["extra_only"]["obligation"], (
        "a site with both reasons is being given the same obligation as one whose reason set is "
        "exactly {extra_forbidden}. Those are different obligations: the first is a wire error, "
        "the second is the _accept_only_declared_fields guarantee at src/core/schemas/_base.py:630 "
        "that the field never reaches the implementation."
    )
    assert set(rows["both"]["kinds"]) == set(rows["missing_only"]["kinds"]) | set(rows["extra_only"]["kinds"]), (
        f"kinds {sorted(rows['both']['kinds'])} is not the union of the two single-reason sites' "
        "kinds, so one of this site's two wrongnesses is invisible to anyone reading the row."
    )


# ---------------------------------------------------------------------------
# 4. Proof scope: a literal census speaks about the literal AS WRITTEN
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_an_escaping_display_is_claimed_only_as_written(tmp_path: Path) -> None:
    """The uc006 family. Bound to a name, appended to a container, repaired by a later Given."""
    row = _row(tmp_path, ESCAPING_CREATIVE)
    assert row["verdict"] == "REJECTED_BY_PIN", (
        f"got {row['verdict']}. The key shape proves this one without any value: assets is "
        f"required, absent, and there is no ** spread to supply it. Details: {row.get('verdict_detail')!r}"
    )
    assert row["verdict_source"] == "key-shape", (
        f"got {row['verdict_source']!r}. ast.literal_eval cannot reach this display (fmt is a "
        "runtime name), so key shape is the only proof available -- and it is where 6 of 7 bdd "
        "and 132 of 135 scope-tests rejections come from."
    )
    assert set(row["reasons"]) == {"missing"}, f"got {sorted(row['reasons'])}"
    assert row["proof_scope"] == "as-written", (
        f"got {row['proof_scope']!r}. This display is bound to a name and appended into "
        "ctx['creatives'], and uc006_sync_creatives.py:5049 does "
        "creatives[-1].setdefault('assets', {}).update(...) before dispatch. Reading the "
        "as-written proof as an as-dispatched one has fooled two agents in this epic."
    )
    assert row["escape"], (
        "the escape analysis reports nothing for a name that is passed to a call, so a later "
        "mutation of the same object is invisible to it"
    )


@pytest.mark.arch_guard
def test_a_display_that_never_escapes_is_claimed_as_dispatched(tmp_path: Path) -> None:
    """Inline, unnamed, fully evaluable -- the one case where the stronger claim is true."""
    row = _row(tmp_path, AS_DISPATCHED_CREATIVE)
    assert row["verdict"] == "REJECTED_BY_PIN", f"got {row['verdict']}: {row.get('verdict_detail')!r}"
    assert set(row["reasons"]) == _pin_reasons(
        {"creative_id": "c123", "name": "Banner Ad", "assets": {}}, CreativeAssetRequest
    ), f"got {sorted(row['reasons'])}"
    assert row["proof_scope"] == "as-dispatched", (
        f"got {row['proof_scope']!r}. Nothing binds this display to a name, so no statement "
        "between the literal and the call can reach it. Reporting every site as-written would "
        "make the scope field carry no information."
    )


# ---------------------------------------------------------------------------
# 5. Abstention: no verdict without the key that names the object
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_a_format_catalog_entry_gets_no_verdict_despite_a_perfect_model_fit(tmp_path: Path) -> None:
    """given_entities.py:271-273. model_fit is 1.0, so no fit threshold can ever exclude it."""
    row = _row(tmp_path, FORMAT_CATALOG)
    assert row["model_fit"] == 1.0, (
        f"model_fit is {row['model_fit']}; measured on the real site it is 1.0, because name and "
        "assets are both CreativeAssetRequest fields. If that ever stops being true this test "
        "stops proving that fit cannot do this job."
    )
    assert row["verdict"] == "UNDECIDABLE_STATICALLY", (
        f"got {row['verdict']} on a FORMAT CATALOG entry. Validating a format descriptor against "
        "a creative request is a category error, and the census cannot tell 'a creative request "
        "that forgot its id' from 'not a creative request at all'."
    )
    assert row["verdict_source"] == "membership-unproven", (
        f"got {row['verdict_source']!r}. This abstention has a different next action from 'the "
        "values are not evaluable' and needs its own source and its own counter."
    )


@pytest.mark.arch_guard
def test_pricing_abstains_without_its_identifying_key_but_not_otherwise(tmp_path: Path) -> None:
    """The census applies NO validity test to pricing today. It must -- and must still abstain."""
    rows = _rows_by_name(
        tmp_path, {"identified": CONFORMANT_PRICING, "unidentified": UNIDENTIFIED_PRICING}, kind="pricing"
    )
    assert rows["unidentified"]["verdict"] == "UNDECIDABLE_STATICALLY", (
        f"got {rows['unidentified']['verdict']}: a dict with no pricing_option_id is not evidence "
        "of a pricing option at all."
    )
    assert rows["unidentified"]["verdict_source"] == "membership-unproven", (
        f"got {rows['unidentified']['verdict_source']!r}"
    )
    assert rows["identified"]["verdict"] == "ACCEPTED_BY_PIN", (
        f"got {rows['identified']['verdict']} ({rows['identified'].get('verdict_detail')!r}) on a "
        "payload PricingOption accepts. An abstention that swallows every pricing site would be "
        "the proxy again, one gap wider: 2 of 3 in-scope pricing literals were never flagged."
    )


# ---------------------------------------------------------------------------
# 6. The extra mode is reported, never set
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_config_reports_the_observed_extra_mode(tmp_path: Path) -> None:
    """model_config['extra'] is frozen at class-definition time, so it can only be READ."""
    report = _report(tmp_path, {"site": CONFORMANT_CREATIVE})
    observed = report["config"]["observed_extra_mode"]
    assert observed.get("CreativeAssetRequest") == CreativeAssetRequest.model_config["extra"], (
        f"config reports {observed.get('CreativeAssetRequest')!r} for CreativeAssetRequest; the "
        f"live class says {CreativeAssetRequest.model_config['extra']!r}. A headline that silently "
        "depends on the caller's shell is the same class of defect as the proxy."
    )
    pricing_modes = {name: mode for name, mode in observed.items() if "Pricing" in name}
    assert pricing_modes, (
        f"config names no pricing model among {sorted(observed)}, so the leg that decides every "
        "pricing verdict reports nothing about what it was measuring"
    )
    assert set(pricing_modes.values()) == {CpmPricingOption.model_config["extra"]}, (
        f"pricing members report {pricing_modes}; the live union member says {CpmPricingOption.model_config['extra']!r}"
    )
    assert PricingOption.model_fields["root"].annotation is not None, "PricingOption lost its root union"


@pytest.mark.arch_guard
def test_the_census_never_sets_the_environment(tmp_path: Path) -> None:
    """Setting ENVIRONMENT after import is a no-op; setting it before leaks into every model."""
    before = dict(os.environ)
    _report(tmp_path, {"site": CONFORMANT_CREATIVE})
    assert dict(os.environ) == before, "running the census mutated os.environ"

    tree = ast.parse((repo_root() / "scripts/audit/creative_literal_sites.py").read_text())
    setters = sorted(
        {
            node.func.attr
            for node in iter_call_expressions(tree)
            if isinstance(node.func, ast.Attribute)
            and node.func.attr in ("putenv", "setdefault", "setenv")
            and "environ" in ast.unparse(node.func.value)
        }
    )
    assignments = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Subscript) and "environ" in ast.unparse(target.value)
    ]
    assert not setters and not assignments, (
        f"the census sets the environment (calls {setters}, assignments at lines {assignments}). "
        "get_pydantic_extra_mode() is read at CLASS-DEFINITION time: after import this changes "
        "nothing, and before import it changes the mode of every model in the process -- "
        "including the two unit guards' pytest session."
    )


# ---------------------------------------------------------------------------
# 7. The frozen API the two committed guards depend on
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_frozen_api_still_resolves() -> None:
    """Both consumers run in ``make quality``; a rename breaks the build, not this file only."""
    assert_guard_subject_resolves(
        CENSUS_MODULE,
        *FROZEN_API,
        why=(
            "test_architecture_marked_malformation.py would lose its shared definition of 'site' "
            "and test_architecture_no_pre_311_creative_builders.py its key sets, so both would "
            "invent a second definition and drift from the audit."
        ),
    )
    assert set(SCOPES) == {"bdd", "tests"}, f"SCOPES keys moved to {sorted(SCOPES)}"
    assert {"assets", "creative_id", "format_id", "name"} <= CREATIVE_KEYS
    assert "snippet" in PRE_311_KEYS and "variants" in PRE_311_KEYS


@pytest.mark.arch_guard
def test_frozen_api_scan_still_returns_the_same_site_rows(tmp_path: Path) -> None:
    """The malformation guard keys its allowlist on (path, enclosing function) from these lines."""
    (tmp_path / "site.py").write_text(ESCAPING_CREATIVE)
    creatives, pricing = scan([str(tmp_path / "*.py")])
    assert len(creatives) == 1 and pricing == [], f"got {len(creatives)} creative, {len(pricing)} pricing"
    row = creatives[0]
    assert {"file", "line", "hand_built"} <= set(row), f"row lost a field the guards read: {sorted(row)}"
    assert Path(row["file"]).name == "site.py"
    assert row["line"] == ESCAPING_CREATIVE.splitlines().index("    creative_payload = {") + 1
    assert row["hand_built"] is True


@pytest.mark.arch_guard
def test_neither_committed_guard_reads_a_verdict_field() -> None:
    """Verdicts are ADDITIVE. If a guard starts reading one, this file is no longer the only gate."""
    consumers = (
        "tests/unit/test_architecture_marked_malformation.py",
        "tests/unit/test_architecture_no_pre_311_creative_builders.py",
    )
    imported: dict[str, set[str]] = {}
    for relpath in consumers:
        tree = ast.parse((repo_root() / relpath).read_text())
        imported[relpath] = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == CENSUS_MODULE
            for alias in node.names
        }
    extra = {path: sorted(names - set(FROZEN_API)) for path, names in imported.items() if names - set(FROZEN_API)}
    assert not extra, (
        f"{extra} import beyond the frozen API. A committed guard reading a verdict would make "
        "make quality depend on the verdict logic, which is a different contract from the one "
        "this file grades."
    )
    assert all(imported[path] for path in consumers), (
        f"a consumer stopped importing from the census entirely: {imported}. Two definitions of "
        "'site' would drift silently in both directions."
    )
