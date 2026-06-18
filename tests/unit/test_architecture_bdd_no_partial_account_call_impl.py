"""Guard: BDD steps must not call env.call_impl(account_ref=...) to build a request.

A step that calls ``env.call_impl(account_ref=...)`` only works on
MediaBuyAccountEnv (whose call_impl runs production resolve_account). On a
full-create env (MediaBuyCreateEnv / MediaBuyDualEnv) the same call builds a
``CreateMediaBuyRequest(account_ref=...)`` — which is BOTH a wrong field name
(the schema field is ``account``) AND a partial request missing required fields
— so it crashes with a ValidationError before the scenario's When step runs.

This was the salesagent-rkb9 bug: account-not-found Given steps used
``call_impl(account_ref=...)`` as a precondition and crashed. The fix routes
account-not-found scenarios through the full create flow with a complete request
carrying the ``account`` field.

Allowlisted: the canonical account-resolution helper, which is only reached for
account-resolution-only scenarios (MediaBuyAccountEnv).

beads: salesagent-rkb9
"""

from pathlib import Path

from tests.unit._architecture_helpers import assert_violations_match_allowlist

_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"
_NEEDLE = "call_impl(account_ref"

# Files permitted to use the pattern (canonical resolve_account helper for the
# account-resolution-only path on MediaBuyAccountEnv). Allowlist only shrinks.
_ALLOWLIST = {"_account_resolution.py"}


def _scan_hits() -> list[tuple[str, int]]:
    """(relative_path, line_number) for every call_impl(account_ref=...) under steps/."""
    hits: list[tuple[str, int]] = []
    for py_file in _STEPS_DIR.rglob("*.py"):
        for i, line in enumerate(py_file.read_text().splitlines(), start=1):
            if _NEEDLE in line:
                hits.append((py_file.name, i))
    return hits


def test_no_partial_account_call_impl_in_bdd_steps():
    """No BDD step may build a request via env.call_impl(account_ref=...) (outside the allowlist)."""
    violations = [(f, ln) for f, ln in _scan_hits() if f not in _ALLOWLIST]
    assert violations == [], (
        f"BDD step(s) call env.call_impl(account_ref=...) at {violations}. On a full-create env this "
        f"builds a partial CreateMediaBuyRequest and crashes with a ValidationError (salesagent-rkb9). "
        f"Route account scenarios through the full create flow with a complete request carrying 'account'."
    )


def test_allowlist_not_stale():
    """Every allowlisted file must still contain the pattern (else remove it)."""
    present = {f for f, _ in _scan_hits()}
    # found = allowlisted files that still use the pattern; the helper's "stale"
    # mode then flags any allowlisted file that no longer does.
    assert_violations_match_allowlist(
        present & _ALLOWLIST,
        _ALLOWLIST,
        fix_hint="Allowlisted file(s) no longer use the pattern; remove them from the allowlist.",
    )


# --- Meta-tests: verify the scan logic ---


def test_scan_detects_pattern(tmp_path):
    """Meta: the scanner finds the disease pattern."""
    f = tmp_path / "steps" / "x.py"
    f.parent.mkdir(parents=True)
    f.write_text("def given_x(ctx):\n    env.call_impl(account_ref=ctx['account_ref'])\n")
    hits = [ln for _, ln in _scan_for(f)]
    assert hits == [2]


def test_scan_ignores_clean_code(tmp_path):
    """Meta: a step that does not use the pattern is not flagged."""
    f = tmp_path / "steps" / "y.py"
    f.parent.mkdir(parents=True)
    f.write_text("def given_y(ctx):\n    env.call_impl(req=ctx['req'])\n")
    assert _scan_for(f) == []


def _scan_for(py_file: Path) -> list[tuple[str, int]]:
    return [(py_file.name, i) for i, line in enumerate(py_file.read_text().splitlines(), start=1) if _NEEDLE in line]
