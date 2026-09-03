"""Guard: a tool module that handles a buyer webhook URL routes it through a gate.

``push_notification_config.url`` and ``reporting_webhook.url`` are URLs the
protocol surface STORES now and DIALS later — by delivery time there is no
request left to refuse into, so the buyer-actionable refusal has to happen at
ingest. The GH #1802 disease scan found five hand-rolled copies of "pull
``url`` out of the config and store it", none of which validated; the fix routed
every protocol ingest of those fields through a validating gate.

This guard is the class-level half of that fix: a NEW tool under
``src/core/tools/`` that accepts either field and forgets the gate fails
``make quality`` here, instead of shipping the sixth unvalidated copy.

ONE gate is accepted (owner ruling "Strategy C", reconciling gh-#1697 with
gh-#1589): ``accept_push_notification_config``
(``src/core/webhooks/registration.py``) — the REGISTRATION gate, which runs
BOTH halves (URL and credential) and returns the ValidatedWebhookRegistration
value. It delegates the URL half to ``reject_unsafe_webhook_registration_url``
(``src/core/webhook_validator.py``), which stays public for ``reporting_webhook.url``
— a registration shape with no credential half — but is no longer an accepted
gate for push configs, because URL-only is exactly the half-gating this closes.
It raises
``AdCPValidationError`` → ``VALIDATION_ERROR`` / ``correctable`` with
``suggestion`` + ``field``. Decisively, it is DNS-FREE at registration: an
unresolvable but public hostname is deliberately ACCEPTED at ingest and
re-checked with DNS at send time by the egress seam, so a buyer whose DNS has
not yet propagated is not refused when registering.

The seam-helper gate is GONE. gh-#1589 briefly introduced a second gate —
``validated_push_notification_config`` / ``validated_reporting_webhook`` in
``src/core/webhook_ingest.py`` — which delegated the ingest verdict to the
outbound seam's ``validate_url``. That helper ALWAYS resolves DNS, which is
correct at SEND time and wrong at REGISTRATION time, so it refused the very
not-yet-propagated hostname Strategy C rules must be accepted. Worse, while two
gates coexisted the same buyer URL got two different answers depending on which
tool it arrived at (``update_media_buy`` refused what ``create_media_buy``
accepted). Every ingest site now uses the registration gate, the helpers have no
callers, and ``src/core/webhook_ingest.py`` is deleted; the seam remains the
SEND-time gate only. ACCEPTED_GATES is therefore single-entry — it may grow only
if a genuinely new gate is introduced, never to re-admit a DNS-resolving one.

The check is deliberately import-level — whether the module CALLS the gate on
the right value is semantic and belongs to the integration tests
(``tests/integration/test_webhook_url_ingest_refusal.py``); what is mechanical
is that a tools module which names these fields at all must at least be built
against a gate. The repository ``upsert`` is deliberately NOT the enforcement
point (it cannot know the request path, so ``error.field`` would be lost —
disposition row 19 of the scan).

Symbol-level, not module-level, on purpose: ``src.core.webhook_validator`` also
exports ``webhook_url_for_log``, a logging helper that validates nothing. A
module importing only that must NOT satisfy the guard.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import parse_module, rel, repo_root

WEBHOOK_FIELD_NAMES = frozenset({"push_notification_config", "reporting_webhook"})

# module path → the symbols in it that constitute a validating ingest gate.
# Anything else exported by these modules (notably webhook_url_for_log) does
# not count. This mapping may GROW only when a new real gate is introduced.
ACCEPTED_GATES: dict[str, frozenset[str]] = {
    # Strategy C: the DNS-free registration gate, AdCPValidationError.
    # The former src.core.webhook_ingest entry was removed with that module —
    # it resolved DNS, which is wrong for registration (see module docstring).
    "src.core.webhooks.registration": frozenset({"accept_push_notification_config"}),
}

ACCEPTED_GATE_SYMBOLS = frozenset(sym for symbols in ACCEPTED_GATES.values() for sym in symbols)

# Tool modules that legitimately name the fields WITHOUT importing a gate.
# A taxonomy, not a debt list -- reasons in writing, like
# test_architecture_no_call_site_backoff.py's NON_HTTP_BACKOFF: these are
# correct designs, not debt, so no FIXME. The set may only shrink.
CORRECT_DESIGN_NO_GATE = frozenset(
    {
        # Transport wrappers: they FORWARD the raw field to _sync_creatives_impl,
        # which runs the gate. Validating in the wrapper would be the layer
        # inversion pattern #5 forbids (and a second verdict per transport).
        "src/core/tools/creatives/sync_wrappers.py",
        # Stores the config into workflow request_data AFTER _sync.py's gate
        # verdict accepted it — a post-validation writer, same standing as the
        # delivery-time readers (scan disposition row 14).
        "src/core/tools/creatives/_workflow.py",
    }
)

FIX_HINT = (
    "This module handles push_notification_config / reporting_webhook — buyer URLs that are "
    "stored now and dialled later. Route them through accept_push_notification_config "
    "(src/core/webhooks/registration.py — the DNS-free registration gate, both halves) at ingest, "
    "so an unsafe URL or an undeliverable credential-less HMAC registration is refused with a "
    "correctable error naming error.field while the buyer's request still "
    "exists to carry the refusal. Do NOT re-introduce a DNS-resolving ingest gate: the egress "
    "seam's validate_url is the SEND-time gate only (Strategy C, gh-#1697 / gh-#1589)."
)


def module_names_webhook_fields(tree: ast.Module) -> bool:
    """True when the module refers to either webhook field by name.

    All the shapes the field arrives in are covered: a function parameter
    (``push_notification_config=None``), an attribute read
    (``req.reporting_webhook``), a bare name, or a string key
    (``params.get("push_notification_config")``). Over-matching (a comment
    cannot match; a docstring constant can) is acceptable: the allowlist is
    the pressure valve and a false positive surfaces at the PR, not at a buyer.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.arg in WEBHOOK_FIELD_NAMES:
            return True
        if isinstance(node, ast.Name) and node.id in WEBHOOK_FIELD_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in WEBHOOK_FIELD_NAMES:
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in WEBHOOK_FIELD_NAMES:
            return True
    return False


def imported_gate_symbols(tree: ast.Module) -> set[str]:
    """Accepted gate symbols this module has actually bound, as ``module.symbol``.

    Handles both binding shapes:

    - ``from src.core.webhooks.registration import accept_push_notification_config``
      — the symbol must be one of the module's accepted gate symbols, so
      importing only ``webhook_url_for_log`` yields nothing.
    - ``import src.core.webhooks.registration`` — a whole-module import binds no gate
      by itself, so it counts only when an accepted gate symbol is also used as
      an attribute somewhere in the module
      (``…webhooks.registration.accept_push_notification_config(…)``).
    """
    found: set[str] = set()
    whole_module_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ACCEPTED_GATES:
            for alias in node.names:
                if alias.name in ACCEPTED_GATES[node.module]:
                    found.add(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Import):
            whole_module_imports.update(alias.name for alias in node.names if alias.name in ACCEPTED_GATES)
    if whole_module_imports:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            for module in whole_module_imports:
                if node.attr in ACCEPTED_GATES[module]:
                    found.add(f"{module}.{node.attr}")
    return found


def module_routes_through_a_gate(tree: ast.Module) -> bool:
    """True when the module is built against at least one accepted ingest gate."""
    return bool(imported_gate_symbols(tree))


def find_unguarded_modules(tools_dir: Path | None = None, root: Path | None = None) -> list[str]:
    """Tool modules naming a webhook field with no accepted-gate import.

    ``tools_dir`` / ``root`` are injectable so the self-test below can run this
    exact scanner over a synthetic tree — a guard whose scanner is never
    exercised on a known-bad tree is not a guard.
    """
    scan_root = root if root is not None else repo_root()
    scan_dir = tools_dir if tools_dir is not None else scan_root / "src" / "core" / "tools"
    violations: list[str] = []
    for path in sorted(scan_dir.rglob("*.py")):
        relpath = rel(path) if root is None else str(path.relative_to(scan_root))
        if relpath in CORRECT_DESIGN_NO_GATE:
            continue
        tree = parse_module(path)
        if module_names_webhook_fields(tree) and not module_routes_through_a_gate(tree):
            violations.append(relpath)
    return violations


@pytest.mark.arch_guard
def test_tool_modules_handling_webhook_urls_route_through_a_validating_gate():
    violations = find_unguarded_modules()
    assert violations == [], f"{FIX_HINT}\nUnguarded modules: {violations}"


@pytest.mark.arch_guard
def test_scan_population_is_not_empty():
    """Non-vacuity: modules naming the fields must actually exist to be graded."""
    tools_dir = repo_root() / "src" / "core" / "tools"
    named = [rel(path) for path in sorted(tools_dir.rglob("*.py")) if module_names_webhook_fields(parse_module(path))]
    assert named, "non-vacuity: no tools module names the webhook fields — the detector or scan dir is wrong"


@pytest.mark.arch_guard
def test_every_accepted_gate_has_a_live_caller():
    """Non-vacuity for ACCEPTED_GATES: each declared gate is really in use.

    If a gate loses every caller under ``src/core/tools/`` it is dead as an
    ingest gate and must be removed from ACCEPTED_GATES rather than left as a
    silent extra way to satisfy the guard. This is exactly what retired the
    ``src.core.webhook_ingest`` entry when the last ``_impl`` moved to the
    registration gate.
    """
    tools_dir = repo_root() / "src" / "core" / "tools"
    used_modules = set()
    for path in sorted(tools_dir.rglob("*.py")):
        used_modules.update(symbol.rsplit(".", 1)[0] for symbol in imported_gate_symbols(parse_module(path)))
    assert used_modules == set(ACCEPTED_GATES), (
        "ACCEPTED_GATES must match the gates actually used under src/core/tools/ — "
        f"declared {sorted(ACCEPTED_GATES)}, in use {sorted(used_modules)}"
    )


@pytest.mark.arch_guard
def test_allowlist_only_shrinks():
    """Stale-entry check: an allowlisted module that stopped naming the fields

    (or started importing a gate) must leave the allowlist, so the list can only
    shrink and never quietly masks a live violation.
    """
    for relpath in sorted(CORRECT_DESIGN_NO_GATE):
        path = repo_root() / relpath
        assert path.exists(), f"stale allowlist entry (file gone): {relpath}"
        tree = parse_module(path)
        assert module_names_webhook_fields(tree) and not module_routes_through_a_gate(tree), (
            f"stale allowlist entry — {relpath} no longer needs it; remove the entry"
        )
    assert len(CORRECT_DESIGN_NO_GATE) == 2, (
        f"CORRECT_DESIGN_NO_GATE is {len(CORRECT_DESIGN_NO_GATE)} entries, expected exactly 2 — "
        "fix the new module instead of listing it, or update this pin if a listed one genuinely left."
    )


# ── Meta-tests: the detector itself ─────────────────────────────────


@pytest.mark.arch_guard
def test_detector_flags_a_module_that_names_the_field_without_a_gate():
    """Positive: every arrival shape of the field is caught without a gate."""
    shapes = [
        "def _impl(req, push_notification_config=None):\n    return push_notification_config\n",
        "def _impl(req):\n    return req.reporting_webhook\n",
        'def _impl(params):\n    return params.get("push_notification_config")\n',
    ]
    for src in shapes:
        tree = ast.parse(src)
        assert module_names_webhook_fields(tree), f"detector missed: {src!r}"
        assert not module_routes_through_a_gate(tree)


@pytest.mark.arch_guard
def test_registration_gate_satisfies_the_detector():
    """Negative: the accepted gate satisfies the guard in both binding shapes;
    the retired seam helper no longer does; an unrelated module is not in the
    population at all."""
    registration_gate = ast.parse(
        "from src.core.webhooks.registration import accept_push_notification_config\n"
        "def _impl(req, push_notification_config=None):\n"
        '    accept_push_notification_config(push_notification_config, field_prefix="push_notification_config")\n'
    )
    assert module_names_webhook_fields(registration_gate)
    assert imported_gate_symbols(registration_gate) == {
        "src.core.webhooks.registration.accept_push_notification_config"
    }

    whole_module = ast.parse(
        "import src.core.webhooks.registration\n"
        "def _impl(req, push_notification_config=None):\n"
        "    src.core.webhooks.registration.accept_push_notification_config(\n"
        '        req.push_notification_config, field_prefix="push_notification_config"\n'
        "    )\n"
    )
    assert imported_gate_symbols(whole_module) == {"src.core.webhooks.registration.accept_push_notification_config"}

    # The gh-#1589 seam helper is retired: importing it validates nothing here.
    retired_seam_gate = ast.parse(
        "from src.core.webhook_ingest import validated_push_notification_config\n"
        "def _impl(req, push_notification_config=None):\n"
        "    validated_push_notification_config(push_notification_config)\n"
    )
    assert module_names_webhook_fields(retired_seam_gate)
    assert not module_routes_through_a_gate(retired_seam_gate)

    unrelated = ast.parse("def _impl(req):\n    return req.budget\n")
    assert not module_names_webhook_fields(unrelated)


@pytest.mark.arch_guard
def test_log_helper_alone_does_not_satisfy_the_guard():
    """A module importing only webhook_url_for_log validates nothing.

    This is why the check is symbol-level: ``webhook_url_for_log`` lives in the
    same module as the registration gate but is a log sanitizer, not a verdict.
    """
    log_only = ast.parse(
        "from src.core.webhook_validator import webhook_url_for_log\n"
        "def _impl(req, push_notification_config=None):\n"
        "    log.info(webhook_url_for_log(push_notification_config.get('url')))\n"
    )
    assert module_names_webhook_fields(log_only)
    assert not module_routes_through_a_gate(log_only)
    assert "webhook_url_for_log" not in ACCEPTED_GATE_SYMBOLS


@pytest.mark.arch_guard
def test_scanner_reports_an_ungated_synthetic_module(tmp_path: Path):
    """The real scanner, not just the predicate, must be able to FAIL.

    Builds a synthetic ``src/core/tools`` tree holding two ungated ingest sites
    (one hand-rolled, one still on the retired seam helper) plus a correctly
    gated module, and asserts ``find_unguarded_modules`` names exactly the
    ungated ones.
    """
    tools_dir = tmp_path / "src" / "core" / "tools"
    (tools_dir / "nested").mkdir(parents=True)

    (tools_dir / "ungated_tool.py").write_text(
        "def _do_it_impl(req, push_notification_config=None):\n    return store(push_notification_config['url'])\n",
        encoding="utf-8",
    )
    (tools_dir / "nested" / "registration_gated_tool.py").write_text(
        "from src.core.webhooks.registration import accept_push_notification_config\n"
        "def _do_it_impl(req, push_notification_config=None):\n"
        '    accept_push_notification_config(push_notification_config, field_prefix="push_notification_config")\n',
        encoding="utf-8",
    )
    (tools_dir / "nested" / "retired_seam_gated_tool.py").write_text(
        "from src.core.webhook_ingest import validated_reporting_webhook\n"
        "def _do_it_impl(req):\n"
        "    validated_reporting_webhook(req.reporting_webhook)\n",
        encoding="utf-8",
    )
    (tools_dir / "log_only_tool.py").write_text(
        "from src.core.webhook_validator import webhook_url_for_log\n"
        "def _do_it_impl(req, push_notification_config=None):\n"
        "    log.info(webhook_url_for_log(push_notification_config['url']))\n",
        encoding="utf-8",
    )
    (tools_dir / "unrelated_tool.py").write_text(
        "def _do_it_impl(req):\n    return req.budget\n",
        encoding="utf-8",
    )

    violations = find_unguarded_modules(tools_dir=tools_dir, root=tmp_path)
    assert violations == [
        "src/core/tools/log_only_tool.py",
        "src/core/tools/nested/retired_seam_gated_tool.py",
        "src/core/tools/ungated_tool.py",
    ], f"scanner verdict wrong on the synthetic tree: {violations}"
