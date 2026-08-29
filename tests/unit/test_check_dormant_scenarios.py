"""Unit tests for scripts/check_dormant_scenarios.py.

The script's subprocess layer (git diff, pytest run) is environment-bound;
these tests pin the pure logic: xfail-line classification (dormant wiring
gaps vs documented spec gaps, transport params collapsed), the mapping from
touched paths to the BDD test modules that bind them, the ``git status
--porcelain`` parsing, and the run-result guard (mocked subprocess) that fails
a broken pytest run instead of reporting a false all-clear.

The two drift-pin tests (``test_every_conftest_dormant_reason_classifies_as_dormant``
and the conftest-emitter taxonomy-derivation guard) deliberately build reasons
from ``tests.bdd.xfail_taxonomy``'s builders — the same functions
``tests/bdd/conftest.py`` emits from — rather than restating markers, so a reason
reworded outside the shared module reddens here instead of silently reclassifying
dormant scenarios as documented gaps. The ``TestClassify`` cases and their
``SAMPLE_OUTPUT`` fixture DO spell the marker strings out verbatim; that is fine —
they are a parser fixture exercising the split, not a claim about the vocabulary.
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

import pytest

from tests.bdd import xfail_taxonomy as xt

#: The single owner of where the sibling scripts live — recomputed at three
#: sites before, mirroring the ``_CONFTEST`` path-constant pattern below.
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


def _load_script(filename: str) -> ModuleType:
    """Import a ``scripts/`` module from its file path.

    The single owner of *how* these path-loaded scripts import — the
    ``spec_from_file_location`` -> ``module_from_spec`` -> ``exec_module``
    sequence was repeated verbatim at two sites, each ignoring the
    ``spec``/``loader is None`` case. A change to the load (a ``sys.path``
    insert, this guard) now lands once.
    """
    path = _SCRIPTS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cds = _load_script("check_dormant_scenarios.py")


#: The two split ops that together ARE ``xfail_taxonomy.scenario_name``.
#: The index is the whole discriminator: both the collapse and the unrelated
#: param PULL split on ``"["`` and sit a line apart in the same files, but the
#: collapse keeps ``[0]`` (the function name) while the pull keeps ``[-1]``/``[1]``
#: (the param id). A matcher that keys on anything else flags the wrong idiom.
_COLLAPSE_OPS = frozenset({("::", -1), ("[", 0)})


def _const_index(node: ast.expr) -> int | None:
    """The integer of a constant subscript; ``None`` for slices and expressions."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _const_index(node.operand)
        return None if inner is None else -inner
    return None


def _peel_splits(node: ast.expr) -> tuple[ast.expr, set[tuple[str, int]]]:
    """Unwind a chain of ``X.split(SEP)[IDX]`` into its root and its ``(sep, idx)`` ops.

    Reads ``args[0]`` only, so the owner's ``split("[", 1)[0]`` maxsplit form
    peels the same as a bare ``split("[")[0]``.
    """
    ops: set[tuple[str, int]] = set()
    cur = node
    while (
        isinstance(cur, ast.Subscript)
        and isinstance(cur.value, ast.Call)
        and isinstance(cur.value.func, ast.Attribute)
        and cur.value.func.attr == "split"
        and cur.value.args
        and isinstance(cur.value.args[0], ast.Constant)
        and isinstance(cur.value.args[0].value, str)
    ):
        idx = _const_index(cur.slice)
        if idx is not None:
            ops.add((cur.value.args[0].value, idx))
        cur = cur.value.func.value
    return cur, ops


def _binding_scopes(tree: ast.Module) -> Iterator[list[ast.stmt]]:
    """Statement lists that share local name bindings: module level, then each function."""
    yield [s for s in tree.body if not isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)]
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node.body


def _inline_collapse_lines(source: str, filename: str) -> list[int]:
    """Lines re-implementing ``scenario_name`` inline: both collapse ops on one root.

    An AST walk rather than a regex, because the collapse has four shapes and a
    regex can only pin one split ORDER: chained ``::``-then-``[``, chained
    ``[``-then-``::``, and either of those spread across two statements via an
    intermediate binding (``func_with_param = nodeid.split("::")[-1]`` on one
    line, ``.split("[")[0]`` on the next). Ops are resolved through a one-level
    name binding so the two-statement form reduces to the chained one.
    """
    tree = ast.parse(source, filename)
    hits: set[int] = set()
    for body in _binding_scopes(tree):
        bindings: dict[str, set[tuple[str, int]]] = {}

        def resolve(expr: ast.expr, _bindings: dict[str, set[tuple[str, int]]] = bindings) -> set[tuple[str, int]]:
            root, ops = _peel_splits(expr)
            if isinstance(root, ast.Name) and root.id in _bindings:
                ops = ops | _bindings[root.id]
            return ops

        nodes = [n for stmt in body for n in ast.walk(stmt)]
        for node in sorted(nodes, key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0))):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                ops = resolve(node.value)
                if ops:
                    bindings[node.targets[0].id] = ops
            if isinstance(node, ast.Subscript) and _COLLAPSE_OPS <= resolve(node):
                hits.add(node.lineno)
    return sorted(hits)


#: The one file whose emitted xfail reasons the two AST guards below grade.
_CONFTEST = Path(__file__).resolve().parents[1] / "bdd" / "conftest.py"


def _is_route_relay(node: ast.expr) -> bool:
    """True for ``<something>.xfail_reason`` — a forward of an already-guarded row field."""
    return isinstance(node, ast.Attribute) and node.attr == "xfail_reason"


def _xfail_reason_nodes(scope: ast.AST) -> Iterator[ast.expr]:
    """Yield every xfail-reason expression in ``scope`` — the single owner of
    "what counts as an xfail-reason site" for both AST guards below.

    Three emitter shapes, because ``_harness_env`` has two of them since the
    routing table replaced its ``elif`` chain:

    * the first positional argument of an imperative ``pytest.xfail(...)`` /
      ``xfail(...)`` call — now only the router's catch-all;
    * the value assigned to ``report.wasxfail`` in the makereport hook;
    * the ``xfail_reason=`` keyword of an ``EnvRoute(...)`` row, which
      ``_run_env_route`` hands straight to ``pytest.xfail``. The per-UC reasons
      that used to be inline ``pytest.xfail`` calls live here now, so a finder
      that stopped at the first two shapes would go blind on seven reasons
      while still passing — the false-green this whole check exists to kill.

    ``pytest.mark.xfail(reason=...)`` markers are deliberately NOT collected:
    they are the documented spec-production-gap ledger, whose bespoke reasons
    classify as documented on purpose. The marker keyword is ``reason``, the
    route field is ``xfail_reason``, so keying on the field name keeps the
    ledger out of scope without needing to inspect the callee.

    ``_run_env_route``'s ``pytest.xfail(route.xfail_reason)`` is a RELAY, not a
    fourth site: it forwards a value already collected — and therefore already
    held to the taxonomy — at the row that authored it. Collecting it too would
    report the one generic consumer as an offender forever, since a forwarded
    attribute can never name the taxonomy. The exemption cannot go vacuous:
    ``test_finder_sees_every_emitter_kind`` pins that the ``xfail_reason=``
    branch still reaches the rows this relay reads.
    """
    for node in ast.walk(scope):
        if isinstance(node, ast.Call):
            func = node.func
            is_imperative_xfail = (
                isinstance(func, ast.Attribute) and func.attr == "xfail" and isinstance(func.value, ast.Name)
            ) or (isinstance(func, ast.Name) and func.id == "xfail")
            if is_imperative_xfail and node.args and not _is_route_relay(node.args[0]):
                yield node.args[0]
            for kw in node.keywords:
                if kw.arg == "xfail_reason":
                    yield kw.value
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Attribute) and t.attr == "wasxfail" for t in node.targets):
                yield node.value


SAMPLE_OUTPUT = """
XFAIL tests/bdd/test_uc026_package_media_buy.py::test_create_package_via_mcp[a2a] - No harness wired for None
XFAIL tests/bdd/test_uc026_package_media_buy.py::test_create_package_via_mcp[mcp] - No harness wired for None
XFAIL tests/bdd/test_uc003_update_media_buy.py::test_pause_campaign[a2a] - UC-003 harness not yet wired for non-extension scenarios
XFAIL tests/bdd/test_uc003_update_media_buy.py::test_new_step[mcp] - Step definition not found: Step definition is not found: Given "the buyer is authenticated". Line 12 in scenario "Pause a campaign" in tests/bdd/features/BR-UC-003-update-media-buy.feature
XFAIL tests/bdd/test_uc026_package_media_buy.py::test_e2e_dispatch[e2e_mcp] - Not implemented: E2E_MCP dispatcher is not yet implemented. Use Transport.MCP for in-process MCP dispatch.
XFAIL tests/bdd/test_uc003_update_media_buy.py::test_main_flow[a2a] - implementation_date, budget, sandbox not populated in update response — spec-production gap
XFAIL tests/bdd/test_uc005_creative_formats.py::test_disclosure_invalid[mcp] - disclosure_positions validation not implemented
XFAIL tests/bdd/test_uc003_update_media_buy.py::test_outline[a2a-account_unsupported-operator-["operator"]-absent] - No harness wired for None
XFAIL tests/bdd/test_uc011_accounts.py::test_spaced_outline[a2a-budget over cap] - UC-011 harness not yet wired for markers: {'billing'}
XFAIL tests/bdd/test_uc003_update_media_buy.py::test_idempotency_boundary[a2a-length 15 (min - 1)-<15 char string>-error "VALIDATION_ERROR" with suggestion] - UC-003 harness not yet wired for non-extension scenarios
XPASS tests/bdd/test_uc002_create_media_buy.py::test_replay[mcp] - graduated
1 passed, 4 xfailed in 2.10s
"""


def _names(bucket: dict[str, set[str]]) -> set[str]:
    return {s for names in bucket.values() for s in names}


class TestClassify:
    def test_splits_dormant_from_documented(self):
        dormant, documented = cds.classify(SAMPLE_OUTPUT)
        dormant_scenarios = _names(dormant)
        assert "test_create_package_via_mcp" in dormant_scenarios
        assert "test_pause_campaign" in dormant_scenarios
        assert "test_new_step" in dormant_scenarios
        # documented spec gap is NOT dormant
        documented_scenarios = _names(documented)
        assert "test_main_flow" in documented_scenarios
        assert "test_main_flow" not in dormant_scenarios

    def test_not_implemented_dispatcher_is_dormant_not_a_documented_gap(self):
        """conftest converts NotImplementedError to ``Not implemented: ...``.

        The E2E_MCP/E2E_A2A dispatchers in tests/harness/dispatchers.py raise it
        today, so every scenario parametrized onto those transports is dormant —
        it executes nowhere. Before the shared taxonomy the checker had no
        marker for that prefix and filed them under "documented spec-production
        gaps (fine)", the exact false-green this check targets.
        """
        dormant, documented = cds.classify(SAMPLE_OUTPUT)
        assert "test_e2e_dispatch" in _names(dormant)
        assert "test_e2e_dispatch" not in _names(documented)

    def test_documented_not_implemented_gap_is_not_swallowed(self):
        """ "... validation not implemented" (no colon) is a production gap, not a wiring gap."""
        dormant, documented = cds.classify(SAMPLE_OUTPUT)
        assert "test_disclosure_invalid" in _names(documented)
        assert "test_disclosure_invalid" not in _names(dormant)

    @pytest.mark.parametrize(
        "reason",
        [
            xt.step_definition_not_found('Step definition is not found: Given "something"'),
            xt.not_implemented("E2E_MCP dispatcher is not yet implemented"),
            xt.no_harness_wired("UC-026"),
            xt.not_yet_wired("UC-003", "for non-extension scenarios"),
            xt.not_yet_wired("UC-011", "for these markers"),
        ],
        ids=["step-missing", "not-implemented", "no-harness", "uc003-unwired", "uc011-unwired"],
    )
    def test_every_conftest_dormant_reason_classifies_as_dormant(self, reason):
        """Anti-drift pin: reasons built by the SHARED builders must bucket dormant.

        conftest.py emits these exact strings via the same builders/constants.
        Reword one outside tests/bdd/xfail_taxonomy and this reddens — which
        a hand-authored sample of the script's own markers can never do.
        """
        dormant, documented = cds.classify(f"XFAIL tests/bdd/test_x.py::test_y[mcp] - {reason}")
        assert _names(dormant) == {"test_y"}, f"{reason!r} should be dormant"
        assert documented == {}

    def test_e2e_unsupported_setup_is_not_dormant(self):
        """A declared impl-only setup is a documented gap: other transports still run."""
        reason = xt.e2e_unsupported_setup("set_registry_formats has no live surface")
        dormant, documented = cds.classify(f"XFAIL tests/bdd/test_x.py::test_y[e2e_rest] - {reason}")
        assert dormant == {}
        assert _names(documented) == {"test_y"}

    def test_transport_params_collapse_to_one_scenario(self):
        dormant, _ = cds.classify(SAMPLE_OUTPUT)
        # Exact contents, not a count: two transports of test_create_package_via_mcp
        # plus the nested-bracket outline row, all collapsed. A bracketed variant
        # leaking through the collapse fails this.
        assert dormant["No harness wired for None"] == {"test_create_package_via_mcp", "test_outline"}

    def test_multi_sentence_reason_keys_on_its_first_sentence(self):
        """The printed headline key is the reason's first sentence, capped at 120.

        ``SAMPLE_OUTPUT``'s step-definition line carries a two-sentence reason
        whose tail (``Line 12 in scenario ...``) is per-scenario; the bucket key
        must be exactly the first sentence, or per-scenario tails explode one
        gap into N single-scenario buckets and the headline count shifts.
        """
        dormant, _ = cds.classify(SAMPLE_OUTPUT)
        key = 'Step definition not found: Step definition is not found: Given "the buyer is authenticated"'
        assert dormant[key] == {"test_new_step"}
        assert not any("Line 12" in k for k in dormant)

    def test_reasons_sharing_a_first_sentence_collapse_into_one_bucket(self):
        output = (
            "XFAIL tests/bdd/test_x.py::test_a[mcp] - No harness wired for None. Detail A.\n"
            "XFAIL tests/bdd/test_x.py::test_b[a2a] - No harness wired for None. Detail B.\n"
        )
        dormant, _ = cds.classify(output)
        assert dormant == {"No harness wired for None": {"test_a", "test_b"}}

    def test_reason_longer_than_120_chars_is_truncated_in_the_key(self):
        reason = "No harness wired for " + "x" * 150
        dormant, _ = cds.classify(f"XFAIL tests/bdd/test_x.py::test_y[mcp] - {reason}")
        assert set(dormant) == {reason[:120]}

    def test_nested_brackets_in_outline_params_collapse(self):
        dormant, _ = cds.classify(SAMPLE_OUTPUT)
        all_names = _names(dormant)
        assert "test_outline" in all_names
        assert not any("[" in s for s in all_names)

    def test_param_id_containing_a_space_is_not_dropped(self):
        """Outline example values can contain spaces; a \\S+ nodeid group loses the line."""
        dormant, _ = cds.classify(SAMPLE_OUTPUT)
        assert "test_spaced_outline" in _names(dormant)

    def test_param_id_containing_the_reason_separator_is_parsed_correctly(self):
        """Real UC-003 outline id: ``[a2a-length 15 (min - 1)-...]`` embeds " - ".

        Splitting on the FIRST " - " cuts the nodeid mid-param and files the
        rest of the id as the reason, producing a garbage bucket key like
        ``1)-<15 char string>-error "VALIDATION_ERROR" ...``. The bracket-depth
        scan must keep the whole id together.
        """
        dormant, _ = cds.classify(SAMPLE_OUTPUT)
        assert "test_idempotency_boundary" in dormant["UC-003 harness not yet wired for non-extension scenarios"]
        assert not any("VALIDATION_ERROR" in key for key in dormant), (
            f"a param id leaked into a reason key: {sorted(dormant)}"
        )

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            (
                "XFAIL tests/bdd/test_x.py::test_y[mcp] - No harness wired for None",
                ("tests/bdd/test_x.py::test_y[mcp]", "No harness wired for None"),
            ),
            (
                'XFAIL tests/bdd/test_x.py::test_y[a2a-length 15 (min - 1)-error "E"] - UC-003 harness not yet wired',
                ('tests/bdd/test_x.py::test_y[a2a-length 15 (min - 1)-error "E"]', "UC-003 harness not yet wired"),
            ),
            (
                'XFAIL tests/bdd/test_x.py::test_y[a2a-op-["operator"]-absent] - No harness wired for None',
                ('tests/bdd/test_x.py::test_y[a2a-op-["operator"]-absent]', "No harness wired for None"),
            ),
            ("XFAIL tests/bdd/test_x.py::test_y", ("tests/bdd/test_x.py::test_y", "")),
            ("XPASS tests/bdd/test_x.py::test_y - graduated", None),
            # A param id embedding a lone "[" from a string param never reaches
            # depth 0 again, so the scan runs off the end of the line; the
            # partition fallback must still split nodeid from reason instead of
            # swallowing the reason into the nodeid.
            (
                "XFAIL tests/bdd/test_x.py::test_y[a2a-op-[-broken - No harness wired for None",
                ("tests/bdd/test_x.py::test_y[a2a-op-[-broken", "No harness wired for None"),
            ),
        ],
        ids=["plain", "separator-in-param", "nested-brackets", "no-reason", "not-an-xfail", "unbalanced-fallback"],
    )
    def test_split_xfail_line(self, line, expected):
        assert cds.split_xfail_line(line) == expected

    def test_xpass_lines_ignored(self):
        dormant, documented = cds.classify(SAMPLE_OUTPUT)
        every = _names(dormant) | _names(documented)
        assert "test_replay" not in every


class TestRunWithoutDbForcesNoColor:
    """A colorized pytest summary silently defeats the ``^XFAIL`` parser.

    ``split_xfail_line`` matches at the start of a line. With ``PY_COLORS=1`` or
    ``FORCE_COLOR=1`` in the environment (common in CI images) pytest prepends an
    ANSI SGR to each summary line, ``^XFAIL`` matches nothing, and the check
    reports a false all-clear — the exact silent pass this tool exists to
    prevent. ``run_without_db`` must force color off on the command AND scrub the
    inheriting env, since either alone can be overridden.

    Deletion oracle: drop ``--color=no`` and the first test reddens; drop the
    ``PY_COLORS``/``FORCE_COLOR`` env scrub and the second reddens.
    """

    def _capture(self, monkeypatch):
        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env", {})

            class _R:
                returncode = 0
                stdout = ""
                stderr = ""

            return _R()

        monkeypatch.setattr(cds.subprocess, "run", fake_run)
        monkeypatch.setenv("FORCE_COLOR", "1")
        monkeypatch.setenv("PY_COLORS", "1")
        # Set in the parent env so the DATABASE_URL-strip assertion below is real:
        # the child run must NOT inherit it, or a wired scenario connects and skips
        # instead of xfailing, and the dormant/documented split the tool exists to
        # make silently breaks.
        monkeypatch.setenv("DATABASE_URL", "postgresql://should-be-stripped/x")
        cds.run_without_db([cds.REPO_ROOT / "tests" / "bdd" / "test_uc018_list_creatives.py"])
        return captured

    def test_color_disabled_on_the_command(self, monkeypatch):
        assert "--color=no" in self._capture(monkeypatch)["cmd"]

    def test_color_env_vars_scrubbed(self, monkeypatch):
        env = self._capture(monkeypatch)["env"]
        assert env.get("PY_COLORS") == "0"
        assert "FORCE_COLOR" not in env

    def test_database_url_stripped_from_child_env(self, monkeypatch):
        """The child pytest must run WITHOUT a DB so wired scenarios skip (not run).

        That skip-vs-xfail split is the whole classification: a wired scenario that
        connects would run green and never surface as dormant. Deletion oracle:
        drop the ``if k != "DATABASE_URL"`` filter in ``run_without_db`` and this
        reddens.
        """
        assert "DATABASE_URL" not in self._capture(monkeypatch)["env"]


class TestConftestUsesSharedTaxonomy:
    """conftest must BUILD its xfail reasons from the taxonomy, not re-type them.

    A hand-typed reason in conftest is invisible to the classifier's markers,
    which is how ``Not implemented: ...`` scenarios ended up in the "documented
    spec-production gaps (fine)" bucket. The scan is scoped to the xfail-reason
    positions — the string arguments of ``pytest.xfail(...)`` calls and the
    values assigned to ``report.wasxfail`` — so a docstring or an explanatory
    comment that quotes a reason stays legal; only an actual emitted reason
    counts. An f-string like ``f"... {NOT_YET_WIRED} ..."`` is built, not
    retyped, so its literal fragments never contain a marker and it passes.
    """

    @staticmethod
    def _emitted_reason_strings() -> list[str]:
        tree = ast.parse(_CONFTEST.read_text(encoding="utf-8"))
        return [
            node.value
            for node in _xfail_reason_nodes(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]

    @pytest.mark.parametrize(
        "fragment",
        [
            xt.STEP_DEFINITION_NOT_FOUND,
            xt.NOT_IMPLEMENTED + ":",
            xt.NO_HARNESS_WIRED,
            xt.NOT_YET_WIRED,
            xt.E2E_UNSUPPORTED_SETUP,
        ],
    )
    def test_no_dormant_reason_is_retyped_in_conftest(self, fragment):
        offenders = [s for s in self._emitted_reason_strings() if fragment.lower() in s.lower()]
        assert offenders == [], (
            f"tests/bdd/conftest.py re-types the xfail reason fragment {fragment!r} instead of "
            f"building it from tests/bdd/xfail_taxonomy. The dormant-scenario checker classifies "
            f"on that vocabulary, so a hand-typed copy drifts silently. Offenders: {offenders}"
        )


class TestConftestEmittedReasonsAreTaxonomyDerived:
    """Every xfail reason conftest EMITS must be BUILT from ``xfail_taxonomy``.

    This is the positive class-closer the negative anti-retype guard above does
    not provide. That guard only fires on a re-typed KNOWN marker; a brand-new
    no-marker wiring phrasing — the shape the ``_harness_env`` sites had before
    the routing fix (e.g. ``"...create_media_buy harness wiring is tracked in
    #1652"``) — contains no marker, so ``is_dormant_reason`` returns False, the
    scenario silently re-buckets as a documented gap, and the negative guard
    stays green.

    The scope is the emitter ROLE, not one function: ``_xfail_reason_nodes``
    over the whole conftest reaches both places a classification-bearing reason
    is emitted — the ``_harness_env`` ``pytest.xfail(...)`` calls AND the
    ``pytest_runtest_makereport`` ``report.wasxfail`` assignments — plus any
    future site of either shape, by construction. Each reason must REFERENCE
    ``xfail_taxonomy``: a builder call (``xfail_taxonomy.no_harness_wired(uc)``)
    or an f-string / constant naming a taxonomy constant (``f"...
    {xfail_taxonomy.NOT_YET_WIRED} ..."``). A bare string literal, or an
    f-string with no taxonomy reference, is an offender.
    ``pytest.mark.xfail(reason=...)`` markers stay out of scope on purpose:
    they are the documented spec-production-gap ledger, whose bespoke reasons
    classify as documented deliberately.

    Deletion oracle: revert any routed reason to a bare literal or marker-less
    f-string — in ``_harness_env`` OR in the makereport hook (e.g.
    ``report.wasxfail = f"placeholder dispatcher for {call.excinfo.value}"``) —
    and this reddens.
    """

    @staticmethod
    def _references_taxonomy(node: ast.AST) -> bool:
        return any(
            isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == "xfail_taxonomy"
            for sub in ast.walk(node)
        )

    @classmethod
    def _reason_is_derived(cls, reason: ast.expr) -> bool:
        if isinstance(reason, ast.Call):  # a builder call, e.g. xfail_taxonomy.no_harness_wired(uc)
            return cls._references_taxonomy(reason.func)
        if isinstance(reason, ast.JoinedStr | ast.Attribute | ast.Name):  # f-string or bare constant ref
            return cls._references_taxonomy(reason)
        return False  # a bare Constant string, or anything with no taxonomy reference

    def test_finder_sees_every_emitter_kind(self):
        """Anti-vacuity pin: the shared finder must reach ALL THREE emitter shapes.

        If the ``report.wasxfail`` (Assign) branch of ``_xfail_reason_nodes``
        broke, the derivation test below would silently skip the makereport
        hook and pass on the ``pytest.xfail`` sites alone — re-opening the
        exact gap this guard was widened to close. Same for the Call branch and
        the router catch-all in ``_harness_env``, and same for the
        ``xfail_reason=`` branch and the ``ENV_ROUTES`` rows, which is where
        every per-UC not-wired reason now lives.
        """
        tree = ast.parse(_CONFTEST.read_text(encoding="utf-8"))
        hook = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "pytest_runtest_makereport"
        )
        env = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_harness_env")
        routes = next(
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "ENV_ROUTES"
        )
        assert sum(1 for _ in _xfail_reason_nodes(hook)) >= 3, "makereport wasxfail assignments not found"
        assert sum(1 for _ in _xfail_reason_nodes(env)) >= 1, "_harness_env pytest.xfail calls not found"
        assert sum(1 for _ in _xfail_reason_nodes(routes)) >= 7, "ENV_ROUTES xfail_reason rows not found"

    def test_every_emitted_xfail_reason_is_taxonomy_derived(self):
        source = _CONFTEST.read_text(encoding="utf-8")
        offenders: list[str] = [
            ast.get_source_segment(source, reason) or ast.dump(reason)
            for reason in _xfail_reason_nodes(ast.parse(source))
            if not self._reason_is_derived(reason)
        ]
        assert offenders == [], (
            "tests/bdd/conftest.py emits an xfail reason (a pytest.xfail(...) argument or a report.wasxfail "
            "value) that is not built from tests/bdd/xfail_taxonomy. A reason with no taxonomy marker "
            "classifies as a documented gap, so its scenarios silently drop out of the dormant count. Route "
            f"it through a builder or an f-string referencing a taxonomy constant. Offenders: {offenders}"
        )


class TestSharedTaxonomy:
    """Structural, not behavioural: the two nodeid collapses must be ONE function.

    Reverting either call site to its inline expression leaves every behavioural
    assertion green (the expressions are equivalent), so identity is the only
    honest pin. The shared implementation's own behaviour is exercised below.
    """

    def test_check_dormant_uses_the_shared_collapse(self):
        assert cds.scenario_name is xt.scenario_name

    def test_enumerate_bdd_issues_uses_the_shared_collapse(self):
        mod = _load_script("enumerate_bdd_issues.py")
        assert mod.scenario_name is xt.scenario_name

    def test_no_script_reimplements_the_collapse_inline(self):
        """Graded over the ROLE — every ``scripts/*.py`` — not a hand-kept list.

        A list of the scripts known to inline the collapse only ever names the
        sites someone already found: two further copies (graduate_pending,
        detect_misrouted_transport) sat green because they were not on it. The
        glob makes a new script's inline copy red on arrival.
        """
        offenders = {
            path.name: lines
            for path in sorted(_SCRIPTS_DIR.glob("*.py"))
            if (lines := _inline_collapse_lines(path.read_text(encoding="utf-8"), str(path)))
        }
        assert offenders == {}, (
            "these scripts re-implement the nodeid->scenario collapse inline instead of calling "
            "tests.bdd.xfail_taxonomy.scenario_name, so a fix to the collapse (a new pytest id shape) "
            f"lands in the owner while they keep a divergent scenario name. {{file: [lines]}} = {offenders}"
        )

    @pytest.mark.parametrize(
        ("source", "is_collapse"),
        [
            ('def f(n):\n    return n.split("::")[-1].split("[")[0]\n', True),
            ('def f(n):\n    return n.split("::")[-1].split("[", 1)[0]\n', True),
            ('def f(n):\n    return n.split("[")[0].split("::")[-1]\n', True),
            ('def f(n):\n    a = n.split("::")[-1]\n    return a.split("[")[0]\n', True),
            ('def f(n):\n    a = n.split("[")[0]\n    return a.split("::")[-1]\n', True),
            ('def f(n):\n    return n.split("[")[-1].rstrip("]")\n', False),
            ('def f(n):\n    a = n.split("::")[-1]\n    return a.split("[")[1].rstrip("]")\n', False),
            ('def f(n):\n    return n.split("::")[-1]\n', False),
        ],
        ids=[
            "chained-sep-first",
            "chained-with-maxsplit",
            "chained-bracket-first",
            "two-statement-sep-first",
            "two-statement-bracket-first",
            "param-pull-negative-index",
            "param-pull-after-sep-split",
            "module-strip-only",
        ],
    )
    def test_matcher_sees_every_collapse_shape_and_no_param_pull(self, source, is_collapse):
        """An empty offender list is only meaningful if the matcher models every shape.

        The regex this replaced returned clean on rows 3-5 — it pinned ONE split
        order — which is precisely why two inline copies went unseen. Rows 6-7 are
        the param EXTRACTION that lives a line away from the collapse in the same
        files; flagging those would make the guard unusable.
        """
        assert bool(_inline_collapse_lines(source, "<synthetic>")) is is_collapse

    @pytest.mark.parametrize(
        ("nodeid", "expected"),
        [
            ("tests/bdd/test_x.py::test_plain", "test_plain"),
            ("tests/bdd/test_x.py::test_param[mcp]", "test_param"),
            ('tests/bdd/test_x.py::test_outline[a2a-operator-["operator"]-absent]', "test_outline"),
            ("tests/bdd/test_x.py::TestC::test_method[a2a]", "test_method"),
        ],
        # Explicit ids: the sample nodeids contain "::" and brackets, which the
        # collect-only scraper in test_architecture_test_marker_coverage.py reads
        # as node ids of their own.
        ids=["unparametrized", "single-param", "nested-brackets", "class-nested"],
    )
    def test_scenario_name_collapse(self, nodeid, expected):
        assert xt.scenario_name(nodeid) == expected


class TestPorcelainPaths:
    """``git status --porcelain`` rename entries must yield the DESTINATION path.

    ``R  old.py -> new.py`` sliced at [3:] gives the pseudo-path
    "old.py -> new.py", which maps to no module — so an uncommitted ``git mv``
    of a feature/step file, exactly when scenarios go dormant, was dropped.
    """

    def test_rename_yields_destination_path(self):
        paths = cds._porcelain_paths(
            [
                " M tests/bdd/conftest.py",
                "R  tests/bdd/features/BR-UC-003-A.feature -> tests/bdd/features/BR-UC-003-B.feature",
                "?? tests/bdd/test_new.py",
            ]
        )
        assert paths == [
            "tests/bdd/conftest.py",
            "tests/bdd/features/BR-UC-003-B.feature",
            "tests/bdd/test_new.py",
        ]
        assert not any(" -> " in p for p in paths)

    def test_backslash_paths_normalized(self):
        assert cds._porcelain_paths([" M tests\\bdd\\conftest.py"]) == ["tests/bdd/conftest.py"]

    def test_short_lines_skipped(self):
        assert cds._porcelain_paths(["", " M ", "??"]) == []


class TestSummary:
    """``python -OO`` strips docstrings; the argparse description must survive it."""

    def test_none_docstring_falls_back(self):
        assert cds._summary(None) == cds._SUMMARY_FALLBACK

    def test_empty_docstring_falls_back(self):
        assert cds._summary("") == cds._SUMMARY_FALLBACK
        assert cds._summary("\n\nbody") == cds._SUMMARY_FALLBACK

    def test_real_docstring_first_line(self):
        assert cds._summary(cds.__doc__).startswith("Informational check")


class TestMapPaths:
    def test_domain_step_module_maps_to_uc_test_module(self):
        modules, notes = cds.map_paths_to_modules(["tests/bdd/steps/domain/uc026_package_media_buy.py"])
        assert any(m.name == "test_uc026_package_media_buy.py" for m in modules)
        assert notes == []

    def test_test_module_maps_to_itself(self):
        modules, _ = cds.map_paths_to_modules(["tests/bdd/test_uc003_update_media_buy.py"])
        assert [m.name for m in modules] == ["test_uc003_update_media_buy.py"]

    def test_feature_file_maps_to_binding_module(self):
        modules, _ = cds.map_paths_to_modules(["tests/bdd/features/BR-UC-003-update-media-buy.feature"])
        assert any(m.name == "test_uc003_update_media_buy.py" for m in modules)

    @pytest.mark.parametrize(
        ("path", "note_substrs"),
        [
            pytest.param(
                "tests/bdd/features/BR-UC-999-nonexistent.feature",
                ("NO test module binds this feature",),
                id="unbound-feature-loudest-note",
            ),
            pytest.param(
                "tests/bdd/steps/domain/uc999_imaginary.py",
                ("no test module matches uc999", "its steps bind nothing"),
                id="uc-step-module-without-test-module",
            ),
            pytest.param(
                "tests/bdd/steps/domain/compat_normalization.py",
                ("without a ucNNN name", "--all"),
                id="non-uc-domain-module-all-hint",
            ),
            pytest.param(
                "tests/bdd/conftest.py",
                ("affects every scenario", "--all"),
                id="conftest-touch-all-hint",
            ),
        ],
    )
    def test_note_branch_maps_nothing_and_says_why(self, path, note_substrs):
        """The four note-only branches: no module mapped, exactly one note, specific text.

        unbound-feature: a ``.feature`` no test module binds — every scenario in
        it is dormant, the loudest thing the tool says. uc-step-module: a
        ``steps/domain/ucNNN_*.py`` whose ucNNN matches no test module binds
        nothing. non-uc-domain-module: a real domain step module without a ucNNN
        name cannot be mapped — point at ``--all``. conftest-touch: affects every
        scenario, so the default run cannot sweep it. The BR-UC-999/uc999 paths
        are deliberately absent from the tree — that absence IS the branch under
        test; ``compat_normalization.py`` and ``conftest.py`` are real files.
        """
        modules, notes = cds.map_paths_to_modules([path])
        assert modules == set()
        assert len(notes) == 1
        for substr in note_substrs:
            assert substr in notes[0], f"expected {substr!r} in note: {notes[0]!r}"

    def test_bdd_relevance_filter(self):
        assert cds.is_bdd_relevant("tests/bdd/steps/domain/uc003_update_media_buy.py")
        assert cds.is_bdd_relevant("tests/harness/media_buy_dual.py")
        assert not cds.is_bdd_relevant("src/core/tools/media_buy_update.py")


class TestMainExitBoundary:
    """Every branch of ``main()`` that prints an outcome and returns a code, pinned
    as a ROLE rather than one path per review round.

    Two earlier rounds each added exactly one exit oracle -- the broken pytest run,
    then the merge-base bail -- which left the other branches unpinned: the
    no-usable-base bail, the no-BDD-relevant-changes all-clear, and the ``--strict``
    half of the dormant split could each be replaced with a bare ``return 0`` and
    the suite stayed green. That is the headline contract ("exit 0 by default,
    ``--strict`` flips to 1 on findings") going silently no-op with nothing red.

    The cannot-assess bails each assert their own message AND that the all-clear
    phrasings ("nothing to check" / "no dormant scenarios") are ABSENT -- the
    load-bearing half, since a silent exit 0 reads as "checked, all clear", which
    is the exact false green this tool exists to kill.

    ``test_every_main_exit_is_pinned`` closes the loop: a new branch in ``main()``
    is red until it is added to ``_CASES``, so the next exit path is covered by
    construction rather than by memory.
    """

    _BDD_MODULE = "tests/bdd/test_uc003_update_media_buy.py"
    _NON_BDD = "src/core/tools/media_buy_update.py"
    _ALL_CLEAR = ("nothing to check", "no dormant scenarios")

    class _Case(NamedTuple):
        argv: list[str]
        returncode: int
        stdout: str
        expected_rc: int
        #: Substring that BOTH locates the branch in the script source and is
        #: asserted on stdout -- one string, so the two cannot drift apart.
        marker: str
        also_prints: tuple[str, ...] = ()
        must_not_print: tuple[str, ...] = ()

    @staticmethod
    def _git_stub(*, rev_parse="0123456789abcdef", merge_base="0123456789abcdef", diff="", status=""):
        """A fake ``cds._git`` keyed on the git subcommand."""
        responses = {"rev-parse": rev_parse, "merge-base": merge_base, "diff": diff, "status": status}
        return lambda *args: responses.get(args[0], "")

    #: One row per outcome-returning branch of ``main()``.
    _CASES: dict[str, _Case] = {
        "no-usable-base-ref": _Case(
            argv=[],
            returncode=0,
            stdout="",
            expected_rc=0,
            marker="no usable base ref",
            must_not_print=_ALL_CLEAR,
        ),
        "no-merge-base": _Case(
            argv=[],
            returncode=0,
            stdout="",
            expected_rc=0,
            marker="no merge-base with",
            also_prints=("upstream/main",),
            must_not_print=_ALL_CLEAR,
        ),
        "no-bdd-relevant-changes": _Case(
            argv=[],
            returncode=0,
            stdout="",
            expected_rc=0,
            marker="no BDD-relevant changes",
            must_not_print=("no dormant scenarios",),
        ),
        "paths-map-to-no-module": _Case(
            argv=["--paths", _NON_BDD],
            returncode=0,
            stdout="",
            expected_rc=0,
            marker="map to no BDD test module",
            must_not_print=("no dormant scenarios",),
        ),
        "broken-run-exit-1": _Case(
            argv=["--paths", _BDD_MODULE],
            returncode=1,
            stdout="",
            expected_rc=1,
            marker="did not run cleanly",
            also_prints=("(exit 1)",),
            must_not_print=("no dormant scenarios",),
        ),
        "broken-run-exit-2": _Case(
            argv=["--paths", _BDD_MODULE],
            returncode=2,
            stdout="",
            expected_rc=1,
            marker="did not run cleanly",
            also_prints=("(exit 2)",),
            must_not_print=("no dormant scenarios",),
        ),
        "clean-all-clear": _Case(
            argv=["--paths", _BDD_MODULE],
            returncode=0,
            stdout="",
            expected_rc=0,
            marker="no dormant scenarios in the touched area",
            must_not_print=("DORMANT scenario",),
        ),
        "clean-all-clear-under-strict": _Case(
            # --strict must NOT flip a clean run; it only grades findings.
            argv=["--strict", "--paths", _BDD_MODULE],
            returncode=0,
            stdout="",
            expected_rc=0,
            marker="no dormant scenarios in the touched area",
            must_not_print=("DORMANT scenario",),
        ),
        "dormant-informational": _Case(
            argv=["--paths", _BDD_MODULE],
            returncode=0,
            stdout=SAMPLE_OUTPUT,
            expected_rc=0,
            marker="Use --strict to make it fail.",
            also_prints=("DORMANT scenario",),
        ),
        "dormant-strict": _Case(
            argv=["--strict", "--paths", _BDD_MODULE],
            returncode=0,
            stdout=SAMPLE_OUTPUT,
            expected_rc=1,
            marker="Use --strict to make it fail.",
            also_prints=("DORMANT scenario",),
        ),
    }

    def _git_for(self, case_id: str):
        """The ``_git`` stub a case needs; ``None`` when the case never shells out."""
        return {
            "no-usable-base-ref": self._git_stub(rev_parse=""),
            "no-merge-base": self._git_stub(merge_base=""),
            "no-bdd-relevant-changes": self._git_stub(diff=self._NON_BDD),
        }.get(case_id)

    def _run_main(self, monkeypatch, case_id: str, case: _Case) -> int:
        monkeypatch.setattr(
            cds,
            "run_without_db",
            lambda modules: subprocess.CompletedProcess(
                args=[], returncode=case.returncode, stdout=case.stdout, stderr=""
            ),
        )
        git = self._git_for(case_id)
        if git is not None:
            monkeypatch.setattr(cds, "_git", git)
        monkeypatch.setattr(sys, "argv", ["check_dormant_scenarios.py", *case.argv])
        return cds.main()

    @pytest.mark.parametrize("case_id", list(_CASES), ids=list(_CASES))
    def test_exit_boundary(self, case_id, monkeypatch, capsys):
        case = self._CASES[case_id]
        rc = self._run_main(monkeypatch, case_id, case)
        out = capsys.readouterr().out
        assert rc == case.expected_rc, f"{case_id}: expected exit {case.expected_rc}, got {rc}. stdout:\n{out}"
        for substr in (case.marker, *case.also_prints):
            assert substr in out, f"{case_id}: expected {substr!r} in stdout:\n{out}"
        for substr in case.must_not_print:
            assert substr not in out, (
                f"{case_id}: {substr!r} must NOT print -- a bail that reads as a checked all-clear "
                f"is the false green this checker exists to kill. stdout:\n{out}"
            )

    def test_every_main_exit_is_pinned(self):
        """The table covers every ``return`` in ``main()`` -- no branch pinned by memory.

        Resolves each case's marker to the ``return`` its branch falls through to,
        then compares that set against main()'s actual ``Return`` nodes. Adding a
        branch to ``main()`` without a ``_CASES`` row reddens here, which is what
        makes the next exit path covered by construction. Line numbers are derived,
        never hardcoded, so ordinary edits to the script do not churn this test.
        """
        source = (_SCRIPTS_DIR / "check_dormant_scenarios.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        main_fn = next(
            n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == "main"
        )
        returns = {n.lineno for n in ast.walk(main_fn) if isinstance(n, ast.Return)}

        # Scan only main()'s own line range: "no merge-base with" also appears in
        # changed_paths' docstring above it, which would resolve to the wrong return.
        body = list(enumerate(source.splitlines(), start=1))[main_fn.lineno - 1 : main_fn.end_lineno]

        pinned: dict[int, list[str]] = {}
        for case_id, case in self._CASES.items():
            hit = next((i for i, line in body if case.marker in line), None)
            assert hit is not None, f"{case_id}: marker {case.marker!r} no longer appears inside main()"
            later = sorted(r for r in returns if r >= hit)
            assert later, f"{case_id}: marker at line {hit} has no return after it"
            pinned.setdefault(later[0], []).append(case_id)

        assert set(pinned) == returns, (
            "main() has outcome-returning branches that no _CASES row pins (or a row now resolves "
            f"outside main()). unpinned return lines: {sorted(returns - set(pinned))}; "
            f"pinned: {dict(sorted(pinned.items()))}"
        )


class TestChangedPathsSignal:
    """``[]`` is indistinguishable from "nothing changed" -- main's all-clear branch
    -- so the no-merge-base case needs its own signal.

    ``_git`` runs with ``check=False``, so on a shallow clone (or against an
    unrelated ref) ``merge-base`` returns ""; the interpolated range collapses to
    ``"..HEAD"``, which git resolves to HEAD..HEAD -- exit 0, empty diff. The
    committed half of the change set silently vanishes and the checker prints its
    all-clear on a branch that DID edit BDD files.

    Deletion oracle: drop the ``if not merge_base`` guard in ``changed_paths`` and
    both of these redden alongside the ``no-merge-base`` exit case above.
    """

    def test_changed_paths_signals_none_rather_than_an_empty_diff(self, monkeypatch):
        monkeypatch.setattr(cds, "_git", lambda *args: "")
        assert cds.changed_paths("upstream/main") is None

    def test_no_diff_range_is_issued_without_a_merge_base(self, monkeypatch, capsys):
        """Beyond the exit code: the bogus ``"..HEAD"`` range must never reach git."""
        calls: list[tuple[str, ...]] = []

        def fake_git(*args: str) -> str:
            calls.append(args)
            # The base ref resolves (rev-parse) but shares no history with HEAD.
            return "0123456789abcdef" if args[0] == "rev-parse" else ""

        monkeypatch.setattr(cds, "_git", fake_git)
        monkeypatch.setattr(sys, "argv", ["check_dormant_scenarios.py"])
        assert cds.main() == 0
        assert not any("..HEAD" in arg for call in calls for arg in call), (
            f"a diff range was issued with an empty merge-base: {calls}"
        )
