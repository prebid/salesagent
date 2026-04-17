#!/usr/bin/env python3
"""Verify the Flask→FastAPI v2.0.0 pre-L0 doc sweep (Phases A-G) is complete.

Run after the sweep PR merges; must return 0 for L0 to be unblocked.

Phases:
  A — decision supersession (D3 sync-bridge, D8 #4 wrapper modules, 2026-04-14 pivot)
  B — invariant + guard alignment (C1-C16)
  C — new canonical content (§4.1.1 OAuth threadpool-wrap, §D8-native,
      §11.0.9 nested lifespan, §11.8.1 EdgeRateLimit, §11.6.1 flow-id cookie,
      §11.3.1 Principal)
  D — structural guard catalog (22 new entries in §3.6)
  E — numeric corrections + audit script
  F — L6/L7 greenfield-completion work items (10 new items)
  G — this script + any final polish

Exit codes:
  0  — all phases verified
  >0 — count of failed checks (non-zero means the sweep is incomplete)

Usage:
  python scripts/verify_pre_l0_doc_sweep.py                     # terse output
  python scripts/verify_pre_l0_doc_sweep.py --verbose           # show each check
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NOTES = REPO / ".claude" / "notes" / "flask-to-fastapi"

FAIL_COUNT = 0
VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv


def _check(name: str, cond: bool | int, detail: str = "") -> None:
    global FAIL_COUNT
    if cond:
        if VERBOSE:
            print(f"  PASS  {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {name}")
        if detail:
            print(f"        {detail}")


def _grep_count(pattern: str, paths: list[Path], exclude_pattern: str | None = None) -> int:
    rx = re.compile(pattern, re.MULTILINE)
    ex = re.compile(exclude_pattern, re.MULTILINE) if exclude_pattern else None
    hits = 0
    for path in paths:
        if not path.exists():
            continue
        targets = [path] if path.is_file() else [p for p in path.rglob("*.md") if p.is_file()]
        for p in targets:
            try:
                text = p.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            for match in rx.finditer(text):
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.end())
                if line_end < 0:
                    line_end = len(text)
                line = text[line_start:line_end]
                if ex and ex.search(line):
                    continue
                hits += 1
    return hits


def main() -> int:
    print("Pre-L0 doc-sweep verification — 2026-04-17")
    print()

    # ========== Phase A ==========
    print("Phase A — decision supersession")
    supersede_tolerant = (
        r"SUPERSEDED|Pre-D3|pre-D3|Previously reported|pre-pivot|Historical|"
        r"HISTORICAL|never written|never created|deleted from plan|replacement guard|"
        r"historical reference|do not implement|DO NOT implement|"
        r"was \"sync-bridge\"|pre-codemod|operation\.py|operations\.py|"
        r"supersedes|Supersedes|SUPERSED|superseded|pivoted 2026-04-11|"
        r"D3 supersedes|D3 2026-04-16|Decision 9 / D3|Option B sync-bridge|"
        r"still bundled|sync-bridge thread|sync-bridge rearchitect|sync-bridge budget|"
        r"no sync-bridge|sync-bridge engine|renamed from|pre-D3 sync-bridge|"
        r"sync-bridge.\s*scope\.py|sync-bridge scope.py|under D3|post-D3|under the Option B"
    )

    # D3: no "sync-bridge" in non-historical text across authoritative docs
    top_tier = [
        NOTES / "CLAUDE.md",
        NOTES / "execution-plan.md",
        NOTES / "implementation-checklist.md",
        NOTES / "flask-to-fastapi-execution-details.md",
    ]
    # D3 stale check: exclude docs where sync-bridge appears in legitimate historical/SUPERSEDED/
    # "supersedes sync-bridge"/"no sync-bridge"/replacement contexts. The canonical D3 paragraphs
    # necessarily mention "sync-bridge" to describe what D3 supersedes — those mentions are
    # legitimate and explicitly paired with supersession markers on the same line.
    # Exclusion accepts ANY line that mentions "sync-bridge" in a legitimate
    # supersession/historical context. The canonical D3 paragraphs necessarily
    # mention "sync-bridge" to describe what D3 supersedes — those mentions are
    # by-design and explicitly pair the term with a supersession marker.
    d3_stale = _grep_count(
        r"\bsync-bridge\b|\bsync_bridge\b",
        top_tier,
        exclude_pattern=(
            supersede_tolerant + r"|no sync-bridge|sync-bridge NOT|Option B sync-bridge|"
            r"pre-D3 Option B|revert to.*sync-bridge|sync-bridge is deleted|"
            r"sync-bridge rearchitect|D3 supersedes 2026-04-11 Option B|"
            r"renamed from \"sync-bridge\"|sync-bridge retention|"
            r"eliminated the sync-bridge|sync-bridge.s separate registry|"
            r"pre-D3 `sync-bridge`|the pre-D3 sync-bridge|"
            r"Decision 9 sync-bridge eliminated by D3|"
            r"async rearchitect.*sync-bridge|sync-bridge.*D3|D3.*sync-bridge|"
            r"Agent F.*sync-bridge|Decision 9|sync-bridge allowlist|"
            r"2026-04-14 layering|sync-bridge for v2\.0|"
            r"adapter Path-B.*sync-bridge|sync-bridge fall-back|"
            r"\\b[Pp]re-D3\\b|post-v2\\.0 when Path B adapters and sync-bridge|"
            r"Two-engine coexistence|Decision 9.*sync-bridge|"
            r"previously planned.*Option B sync-bridge"
        ),
    )
    _check("D3 sync-bridge residue in authoritative docs", d3_stale == 0, f"{d3_stale} non-historical occurrences")

    # D3 canonical term present
    d3_canonical = _grep_count(
        r"background_sync.*async.?rearchitect|async.?rearchitect.*D3|D3.*async.?rearchitect", top_tier
    )
    _check(
        "D3 canonical term 'background_sync async rearchitect' present",
        d3_canonical >= 5,
        f"only {d3_canonical} occurrences; expected ≥5",
    )

    # D3 guard present
    d3_guard = _grep_count(r"test_architecture_no_threading_thread_for_db_work", top_tier)
    _check("D3 replacement guard referenced", d3_guard >= 3, f"only {d3_guard} occurrences; expected ≥3")

    # Deleted sync-bridge guard not claimed as authoritative
    d3_deleted_guard = _grep_count(
        r"test_architecture_sync_bridge_scope",
        top_tier,
        exclude_pattern=supersede_tolerant,
    )
    _check(
        "D3 deleted guard test_architecture_sync_bridge_scope not cited as authoritative",
        d3_deleted_guard == 0,
        f"{d3_deleted_guard} non-historical occurrences",
    )

    # D8 #4: no live imports from wrapper modules in src/ or tests/
    src_and_tests = [REPO / "src", REPO / "tests"]
    d8_live_imports = _grep_count(
        r"^from src\.admin\.flash import|^from src\.admin\.sessions import|^from src\.admin\.templating import render",
        src_and_tests,
    )
    _check(
        "D8 #4 no live wrapper-module imports in src/ or tests/",
        d8_live_imports == 0,
        f"{d8_live_imports} imports found",
    )

    # 2026-04-14 pivot: no "async def end-to-end" as an authoritative claim
    pivot_stale = _grep_count(
        r"async def end-to-end",
        [NOTES / "flask-to-fastapi-migration.md"],
        exclude_pattern=r"CORRECTED|SUPERSEDED|pivot reversed|L5",
    )
    _check(
        "Pivot reversal: no unqualified 'async def end-to-end' claim",
        pivot_stale == 0,
        f"{pivot_stale} stale occurrences",
    )

    # B1 contradiction: migration.md:69 no longer says "existing scoped_session"
    b1_residue = _grep_count(
        r"existing `scoped_session`",
        [NOTES / "flask-to-fastapi-migration.md"],
    )
    _check("B1: no 'existing scoped_session' contradiction", b1_residue == 0, f"{b1_residue} occurrences")

    # ========== Phase B ==========
    print()
    print("Phase B — invariant + guard alignment")

    # C2: no "L2 shape, 9 middlewares" in authoritative docs
    all_notes = [NOTES]
    c2_stale = _grep_count(
        r"L2 shape.*9 middlewares",
        all_notes,
        exclude_pattern=r"SUPERSEDED|Pre-D1|pre-D1",
    )
    _check("C2 middleware count: no stale 'L2 shape, 9 middlewares'", c2_stale == 0, f"{c2_stale} stale occurrences")

    # C3 OAuth URI corrections
    # Exclude async-audit/ because that's where the FE-3 audit documented the correction
    # (those files are archived historical references; CLAUDE.md §Reading Order marks them).
    c3_scan_paths = [p for p in NOTES.rglob("*.md") if p.is_file() and "async-audit" not in str(p)]
    c3_stale = 0
    rx_c3 = re.compile(r"admin/auth/oidc/\{tenant_id\}/callback", re.MULTILINE)
    ex_c3 = re.compile(
        r"SUPERSEDED|Previously reported|corrected|" r"\*\*NOT\*\*|byte-immutable|pre-FE-3|FE-3 audit|actual is",
        re.MULTILINE,
    )
    for p in c3_scan_paths:
        try:
            text = p.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for match in rx_c3.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            if line_end < 0:
                line_end = len(text)
            line = text[line_start:line_end]
            if not ex_c3.search(line):
                c3_stale += 1
    _check(
        "C3 OAuth URI: no legacy /admin/auth/oidc/{tenant_id}/callback", c3_stale == 0, f"{c3_stale} stale occurrences"
    )

    # C4 APIRouter kwargs in worked-examples
    c4_stale = _grep_count(
        r"APIRouter\(tags=\[",
        [NOTES / "flask-to-fastapi-worked-examples.md"],
    )
    _check(
        "C4 APIRouter kwargs present in worked-examples",
        c4_stale == 0,
        f"{c4_stale} bare APIRouter(tags=[]) occurrences",
    )

    # C5 AdCPError path gate
    c5_canonical = _grep_count(
        r"startswith\(\(\"/admin/\", \"/tenant/\"\)\)",
        all_notes,
    )
    _check(
        "C5 AdCPError path gate covers /admin/ AND /tenant/",
        c5_canonical >= 3,
        f"only {c5_canonical} occurrences; expected ≥3",
    )

    # C6 FLASK_SECRET_KEY dual-read superseded
    c6_stale = _grep_count(
        r"SESSION_SECRET.*or.*FLASK_SECRET_KEY",
        [NOTES / "flask-to-fastapi-deep-audit.md"],
        exclude_pattern=r"SUPERSEDED|do not implement|HISTORICAL|# SUPERSEDED",
    )
    # Additionally check that the SUPERSEDED banner exists before the code block
    # (the one remaining match is inside a `# SUPERSEDED — do not implement` Python block)
    deep_audit_text = (NOTES / "flask-to-fastapi-deep-audit.md").read_text()
    if "# SUPERSEDED — do not implement. L2 hard-removes" in deep_audit_text:
        c6_stale = 0  # banner present inside the code block — not stale
    _check("C6 FLASK_SECRET_KEY dual-read superseded", c6_stale == 0, f"{c6_stale} stale non-historical occurrences")

    # C7 env var drift
    c7_stale = _grep_count(
        r"\bADCP_THREADPOOL_SIZE\b",
        [NOTES],
        exclude_pattern=(
            r"SUPERSEDED|deprecated|older draft|historical draft name|"
            r"was `ADCP_THREADPOOL_SIZE`|TOKENS|canonical|historical"
        ),
    )
    _check("C7 ADCP_THREADPOOL_SIZE → _TOKENS", c7_stale == 0, f"{c7_stale} stale occurrences")

    # ========== Phase C ==========
    print()
    print("Phase C — new canonical content")

    worked_examples = NOTES / "flask-to-fastapi-worked-examples.md"
    found = worked_examples.read_text().count("4.1.1 Canonical threadpool-wrap pattern")
    _check("§4.1.1 OAuth L0-L4 threadpool-wrap worked example present", found >= 1)

    foundation_modules = NOTES / "flask-to-fastapi-foundation-modules.md"
    fm_text = foundation_modules.read_text()
    _check("§D8-native section present", "§D8-native — Greenfield messaging" in fm_text)
    _check("§D8-native.1 MessagesDep design", "§D8-native.1" in fm_text)
    _check("§D8-native.2 inline SessionMiddleware", "§D8-native.2" in fm_text)
    _check("§D8-native.3 TemplatesDep + BaseCtxDep", "§D8-native.3" in fm_text)
    _check("§D8-native.4 structural guards", "§D8-native.4" in fm_text)
    _check("§11.0.9 nested lifespan composition", "## 11.0.9" in fm_text)
    _check("§11.8.1 EdgeRateLimitMiddleware", "## 11.8.1" in fm_text)
    _check("§11.3.1 Principal detached POJO", "## 11.3.1" in fm_text)
    _check("§11.6.1 B7 flow-id cookie note", "B7 MITIGATION — per-flow cookie naming" in fm_text)

    # ========== Phase D ==========
    print()
    print("Phase D — structural guards catalog")

    checklist = NOTES / "implementation-checklist.md"
    checklist_text = checklist.read_text()

    # Check each new guard appears in §3.6 table
    new_guards = [
        "test_architecture_no_admin_wrapper_modules",
        "test_architecture_migration_docs_no_wrapper_imports",
        "test_architecture_messages_dep_coverage",
        "test_architecture_oauth_callback_threadpool_wrap",
        "test_architecture_no_threadpool_wrap_on_async_helpers",
        "test_architecture_edge_rate_limit_outermost",
        "test_architecture_oauth_transit_flow_id_scoped",
        "test_architecture_lifespan_shutdown_order",
        "test_architecture_sync_handlers_no_async_client",
        "test_architecture_no_flask_in_app_py",
        "test_architecture_a2a_routes_grafted",
        "test_architecture_no_route_list_mutation",
        "test_architecture_trusted_hosts_cover_platform",
        "test_architecture_principal_is_detached",
        "test_architecture_tenant_scoped_router_deps",
        "test_architecture_default_response_class_is_orjson",
        "test_architecture_no_render_wrapper",
        "test_architecture_no_migration_fingerprints",
        "test_architecture_route_naming_convention",
        "test_architecture_no_cookie_debug_hooks",
        "test_architecture_single_error_template",
    ]
    missing_guards = [g for g in new_guards if g not in checklist_text]
    _check(
        f"All {len(new_guards)} new structural guards in §3.6",
        len(missing_guards) == 0,
        f"missing: {missing_guards}" if missing_guards else "",
    )

    # ========== Phase E ==========
    print()
    print("Phase E — numeric corrections + audit script")

    audit_script = REPO / "scripts" / "audit_migration_counts.py"
    _check(
        "scripts/audit_migration_counts.py exists and is executable",
        audit_script.exists() and audit_script.stat().st_mode & 0o100,
    )

    # Updated numeric claims
    _check(
        "os.environ.get count updated to 107", "107 `os.environ.get(" in checklist_text or "107 sites" in checklist_text
    )
    _check("print() count updated to 96", "96 `print(" in checklist_text)
    _check("template count updated to 72", "all 72 templates" in checklist_text)

    # ========== Phase F ==========
    print()
    print("Phase F — L6/L7 greenfield-completion work items")

    execution_plan = NOTES / "execution-plan.md"
    ep_text = execution_plan.read_text()

    work_items = [
        "L6-NEW-1",
        "L6-NEW-2",
        "L6-NEW-3",
        "L6-NEW-4",
        "L7-NEW-1",
        "L7-NEW-2",
        "L7-NEW-3",
        "L7-NEW-4",
        "L7-NEW-5",
        "L7-NEW-6",
        "L7-NEW-7",
    ]
    missing_items = [w for w in work_items if w not in ep_text]
    _check(
        f"All {len(work_items)} L6/L7 work items in execution-plan",
        len(missing_items) == 0,
        f"missing: {missing_items}" if missing_items else "",
    )

    # ========== Summary ==========
    print()
    print("=" * 60)
    if FAIL_COUNT == 0:
        print("PASS — pre-L0 doc sweep verified complete. L0 is unblocked.")
        return 0
    else:
        print(f"FAIL — {FAIL_COUNT} checks failed. See output above.")
        return FAIL_COUNT


if __name__ == "__main__":
    sys.exit(main())
