"""Unit tests for scripts/check_dormant_scenarios.py.

The script's subprocess layer (git diff, pytest run) is environment-bound;
these tests pin the pure logic: xfail-line classification (dormant wiring
gaps vs documented spec gaps, transport params collapsed), the mapping from
touched paths to the BDD test modules that bind them, the ``git status
--porcelain`` parsing, and the run-result guard (mocked subprocess) that fails
a broken pytest run instead of reporting a false all-clear.

The classification tests deliberately do NOT restate the script's own marker
literals — that would only prove the classifier agrees with itself. They build
reasons with ``tests.bdd.xfail_taxonomy``'s builders, the same functions
``tests/bdd/conftest.py`` emits from, so a reason reworded outside the shared
module reddens here instead of silently reclassifying dormant scenarios as
documented gaps.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.bdd import xfail_taxonomy as xt

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_dormant_scenarios.py"
_spec = importlib.util.spec_from_file_location("check_dormant_scenarios", _SCRIPT)
cds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cds)


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
            f"UC-003 harness {xt.NOT_YET_WIRED} for non-extension scenarios",
            f"UC-011 harness {xt.NOT_YET_WIRED} for markers: {{'billing'}}",
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
        ],
        ids=["plain", "separator-in-param", "nested-brackets", "no-reason", "not-an-xfail"],
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
        cds.run_without_db([cds.REPO_ROOT / "tests" / "bdd" / "test_uc018_list_creatives.py"])
        return captured

    def test_color_disabled_on_the_command(self, monkeypatch):
        assert "--color=no" in self._capture(monkeypatch)["cmd"]

    def test_color_env_vars_scrubbed(self, monkeypatch):
        env = self._capture(monkeypatch)["env"]
        assert env.get("PY_COLORS") == "0"
        assert "FORCE_COLOR" not in env


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
    def _emitted_reason_strings(path: Path) -> list[str]:
        import ast

        tree = ast.parse(path.read_text(encoding="utf-8"))
        out: list[str] = []
        for node in ast.walk(tree):
            # pytest.xfail("...") / xfail("...") positional reason
            if isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "xfail"
                    or isinstance(func, ast.Name)
                    and func.id == "xfail"
                ):
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            out.append(arg.value)
            # report.wasxfail = "..."
            elif isinstance(node, ast.Assign):
                targets_wasxfail = any(isinstance(t, ast.Attribute) and t.attr == "wasxfail" for t in node.targets)
                if targets_wasxfail and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    out.append(node.value.value)
        return out

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
        conftest = Path(__file__).resolve().parents[1] / "bdd" / "conftest.py"
        offenders = [s for s in self._emitted_reason_strings(conftest) if fragment.lower() in s.lower()]
        assert offenders == [], (
            f"tests/bdd/conftest.py re-types the xfail reason fragment {fragment!r} instead of "
            f"building it from tests/bdd/xfail_taxonomy. The dormant-scenario checker classifies "
            f"on that vocabulary, so a hand-typed copy drifts silently. Offenders: {offenders}"
        )


class TestSharedTaxonomy:
    """Structural, not behavioural: the two nodeid collapses must be ONE function.

    Reverting either call site to its inline expression leaves every behavioural
    assertion green (the expressions are equivalent), so identity is the only
    honest pin. The shared implementation's own behaviour is exercised below.
    """

    #: The idiom both scripts used to inline: ``nid.split("::")[-1].split("[")...``.
    #: Narrow on purpose — enumerate_bdd_issues legitimately does
    #: ``nid.split("[")[-1]`` elsewhere to pull the transport out of a param id.
    _INLINE_COLLAPSE = re.compile(r"""split\(\s*["']::["']\s*\)\[-1\]\s*\.\s*split\(\s*["']\[["']""")

    def test_check_dormant_uses_the_shared_collapse(self):
        assert cds.scenario_name is xt.scenario_name

    def test_enumerate_bdd_issues_uses_the_shared_collapse(self):
        script = Path(__file__).resolve().parents[2] / "scripts" / "enumerate_bdd_issues.py"
        spec = importlib.util.spec_from_file_location("enumerate_bdd_issues", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.scenario_name is xt.scenario_name

    @pytest.mark.parametrize("script", ["check_dormant_scenarios.py", "enumerate_bdd_issues.py"])
    def test_no_script_reimplements_the_collapse_inline(self, script):
        path = Path(__file__).resolve().parents[2] / "scripts" / script
        hits = self._INLINE_COLLAPSE.findall(path.read_text(encoding="utf-8"))
        assert hits == [], (
            f"scripts/{script} re-implements the nodeid->scenario collapse inline; "
            f"call tests.bdd.xfail_taxonomy.scenario_name so the two scripts cannot drift."
        )

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

    def test_conftest_touch_yields_note_not_full_sweep(self):
        modules, notes = cds.map_paths_to_modules(["tests/bdd/conftest.py"])
        assert modules == set()
        assert any("--all" in n for n in notes)

    def test_unbound_feature_file_is_the_loudest_note(self):
        """A .feature no test module binds: every scenario in it is dormant."""
        modules, notes = cds.map_paths_to_modules(["tests/bdd/features/BR-UC-999-nonexistent.feature"])
        assert modules == set()
        assert len(notes) == 1
        assert "NO test module binds this feature" in notes[0]

    def test_uc_step_module_with_no_test_module_is_noted(self):
        """steps/domain/ucNNN_*.py whose ucNNN matches no test module binds nothing."""
        modules, notes = cds.map_paths_to_modules(["tests/bdd/steps/domain/uc999_imaginary.py"])
        assert modules == set()
        assert len(notes) == 1
        assert "no test module matches uc999" in notes[0]
        assert "its steps bind nothing" in notes[0]

    def test_non_uc_domain_module_is_noted_with_all_hint(self):
        """A real domain step module without a ucNNN name cannot be mapped — say so."""
        modules, notes = cds.map_paths_to_modules(["tests/bdd/steps/domain/compat_normalization.py"])
        assert modules == set()
        assert len(notes) == 1
        assert "without a ucNNN name" in notes[0]
        assert "--all" in notes[0]

    def test_bdd_relevance_filter(self):
        assert cds.is_bdd_relevant("tests/bdd/steps/domain/uc003_update_media_buy.py")
        assert cds.is_bdd_relevant("tests/harness/media_buy_dual.py")
        assert not cds.is_bdd_relevant("src/core/tools/media_buy_update.py")


class TestRunGuard:
    """The clean no-DB run is exit 0 (skips + xfails); any nonzero exit means the
    run broke (collection/import error, failed test), so the checker must surface
    it and fail -- never report a false 'no dormant scenarios'."""

    def _run_main(self, monkeypatch, returncode, stdout="", stderr="", argv=None):
        monkeypatch.setattr(
            cds,
            "run_without_db",
            lambda modules: subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr),
        )
        monkeypatch.setattr(
            sys,
            "argv",
            argv or ["check_dormant_scenarios.py", "--paths", "tests/bdd/test_uc003_update_media_buy.py"],
        )
        return cds.main()

    def test_broken_run_surfaced_not_reported_clean(self, monkeypatch, capsys):
        rc = self._run_main(monkeypatch, returncode=1, stderr="ImportError: cannot import name 'ByGeoItem'")
        out = capsys.readouterr().out
        assert rc == 1, "a broken pytest run must fail the checker, not exit 0"
        assert "did not run cleanly" in out
        assert "ByGeoItem" in out
        assert "no dormant scenarios" not in out

    def test_collection_error_exit_2_also_surfaced(self, monkeypatch, capsys):
        rc = self._run_main(monkeypatch, returncode=2, stderr="ERROR collecting test_x.py")
        out = capsys.readouterr().out
        assert rc == 1
        assert "did not run cleanly (exit 2)" in out
        assert "no dormant scenarios" not in out

    def test_clean_run_still_classifies_dormant(self, monkeypatch, capsys):
        rc = self._run_main(monkeypatch, returncode=0, stdout=SAMPLE_OUTPUT)
        out = capsys.readouterr().out
        assert "DORMANT scenario" in out
        assert rc == 0  # informational (non-strict) reports but does not fail

    def test_paths_mapping_to_nothing_says_so(self, monkeypatch, capsys):
        """Parity with the git-diff path: a silent exit 0 reads as 'checked, all clear'."""
        rc = self._run_main(
            monkeypatch,
            returncode=0,
            argv=["check_dormant_scenarios.py", "--paths", "src/core/tools/media_buy_update.py"],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "nothing to check" in out
