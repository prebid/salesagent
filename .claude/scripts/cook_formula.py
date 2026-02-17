#!/usr/bin/env python3
"""Cook a formula YAML into beads atoms.

Reads a protomolecule formula and creates beads issues (atoms) with
proper dependencies, labels, and an epic parent.

Supports three atom sections:
  - setup: run-once atoms at the start
  - iterate: repeated atoms per item (per-task, per-field, per-feature, etc.)
  - finalize: run-once atoms after all iterations

Usage:
    # With iteration (per-task):
    python3 .claude/scripts/cook_formula.py \
        --formula .claude/formulas/task-execute.yaml \
        --var "TASK_IDS=fck o0d a53" \
        --epic-title "Execute: fck, o0d, a53"

    # Dry run (prints plan without calling bd):
    python3 .claude/scripts/cook_formula.py \
        --formula .claude/formulas/task-execute.yaml \
        --var "TASK_IDS=fck o0d a53" \
        --epic-title "Execute: fck, o0d, a53" \
        --dry-run
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

# ── Dry-run tracking ──────────────────────────────────────────────

_dry_run = False
_dry_run_counter = 0


def _next_dry_id() -> str:
    global _dry_run_counter
    _dry_run_counter += 1
    return f"dry-{_dry_run_counter:03d}"


# ── BD interaction ────────────────────────────────────────────────


def run_bd(args: list[str], *, capture: bool = True) -> str:
    """Run a bd command and return stdout."""
    if _dry_run:
        return _next_dry_id()
    cmd = ["bd", *args]
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if result.returncode != 0:
        print(f"ERROR: bd {' '.join(args)}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def create_issue(
    *,
    title: str,
    description: str,
    issue_type: str = "task",
    parent: str | None = None,
    acceptance: str | None = None,
) -> str:
    """Create a beads issue and return its ID."""
    if _dry_run:
        dry_id = _next_dry_id()
        print(f"  [DRY] create {issue_type}: {dry_id} — {title}")
        return dry_id
    args = [
        "create",
        "--silent",
        "--type",
        issue_type,
        "--title",
        title,
        "--description",
        description,
    ]
    if parent:
        args.extend(["--parent", parent])
    if acceptance:
        args.extend(["--acceptance", acceptance])
    return run_bd(args)


def add_dependency(issue_id: str, depends_on: str) -> None:
    """Add a dependency: issue_id depends on depends_on."""
    if _dry_run:
        print(f"  [DRY] dep: {issue_id} depends on {depends_on}")
        return
    run_bd(["dep", "add", issue_id, depends_on])


def add_label(issue_id: str, label: str) -> None:
    """Add a label to an issue."""
    if _dry_run:
        return  # Skip label noise in dry run
    run_bd(["label", "add", issue_id, label])


# ── Template helpers ──────────────────────────────────────────────


def substitute(text: str, variables: dict[str, str]) -> str:
    """Replace {VAR} placeholders in text."""
    result = text
    for key, value in variables.items():
        result = result.replace(f"{{{key}}}", value)
    return result


def parse_variables(var_args: list[str]) -> dict[str, str]:
    """Parse --var KEY=VALUE arguments into a dict."""
    variables = {}
    for v in var_args:
        if "=" not in v:
            print(f"ERROR: --var must be KEY=VALUE, got: {v}", file=sys.stderr)
            sys.exit(1)
        key, value = v.split("=", 1)
        variables[key] = value
    return variables


# ── Atom creation helpers ─────────────────────────────────────────


def create_atom(
    atom_def: dict,
    variables: dict[str, str],
    *,
    epic_id: str,
    atom_ids: dict[str, str],
    global_labels: list[str],
    barrier_ids: list[str],
    barrier_label: str,
    prev_item_barrier_id: str | None = None,
) -> str:
    """Create a single atom from its definition. Returns the bead ID."""
    atom_formula_id = substitute(atom_def["id"], variables)
    title = substitute(atom_def["title"], variables)
    desc = substitute(atom_def["description"], variables)
    acceptance = substitute(atom_def.get("acceptance", ""), variables) or None

    bead_id = create_issue(title=title, description=desc, parent=epic_id, acceptance=acceptance)
    atom_ids[atom_formula_id] = bead_id

    # Inject TRIAGE_ID for self-referencing triage atoms
    all_labels = atom_def.get("labels", [])
    if "triage" in all_labels or "atom:triage" in all_labels:
        if not _dry_run:
            run_bd(["update", bead_id, "--description", desc.replace("{TRIAGE_ID}", bead_id)])

    # Apply labels
    for label in global_labels + all_labels:
        add_label(bead_id, label)

    # Track barrier atoms (for depends_on_all_barriers)
    if barrier_label and (barrier_label in all_labels or atom_formula_id.startswith("commit-")):
        barrier_ids.append(bead_id)

    # Handle depends_on_all_barriers
    if atom_def.get("depends_on_all_barriers"):
        for bid in barrier_ids:
            add_dependency(bead_id, bid)

    # Handle depends_on_prev_barrier: chain this atom to the previous item's barrier
    if atom_def.get("depends_on_prev_barrier") and prev_item_barrier_id:
        add_dependency(bead_id, prev_item_barrier_id)

    # Resolve explicit dependencies
    for dep_formula_id in atom_def.get("depends_on", []):
        resolved_dep = substitute(dep_formula_id, variables)
        if resolved_dep in atom_ids:
            add_dependency(bead_id, atom_ids[resolved_dep])

    return bead_id


# ── Main cook logic ───────────────────────────────────────────────


def cook(formula_path: str, variables: dict[str, str], epic_title: str) -> None:
    """Cook a formula into beads atoms."""
    path = Path(formula_path)
    if not path.exists():
        print(f"ERROR: Formula not found: {formula_path}", file=sys.stderr)
        sys.exit(1)

    with open(path) as f:
        formula = yaml.safe_load(f)

    # Validate required variables
    for var_name, var_def in formula.get("variables", {}).items():
        if var_def.get("required") and var_name not in variables:
            print(f"ERROR: Required variable missing: {var_name}", file=sys.stderr)
            print(f"  Description: {var_def.get('description', 'N/A')}", file=sys.stderr)
            sys.exit(1)

    global_labels = formula.get("labels", [])
    iterate_config = formula.get("iterate")

    # ── Resolve iteration items (if iterate section exists) ──
    items: list[str] = []
    item_var = ""
    all_var = ""
    count_var = ""
    barrier_label = "atom:commit"  # default

    if iterate_config:
        over_var = iterate_config["over"]
        item_var = iterate_config["item_var"]
        all_var = iterate_config.get("all_var", "ALL_ITEMS")
        count_var = iterate_config.get("count_var", "N")
        barrier_label = iterate_config.get("barrier_label", "atom:commit")

        raw = variables.get(over_var, "")
        items = raw.split() if raw else []
        if not items:
            print(f"ERROR: {over_var} variable is empty or missing", file=sys.stderr)
            sys.exit(1)

    all_items_str = ", ".join(items)
    n_items = str(len(items))

    # ── Create epic ──
    epic_desc = substitute(formula.get("description", ""), variables)
    epic_id = create_issue(
        title=epic_title,
        description=epic_desc,
        issue_type="epic",
    )
    if not _dry_run:
        print(f"Epic: {epic_id} — {epic_title}")

    for label in global_labels:
        add_label(epic_id, label)

    # Track atom IDs by their formula id (for dependency resolution)
    atom_ids: dict[str, str] = {}
    barrier_ids: list[str] = []

    # Build shared variables for finalize context
    shared_vars = {**variables, "EPIC_ID": epic_id}
    if iterate_config:
        shared_vars[all_var] = all_items_str
        shared_vars[count_var] = n_items

    # ── Setup atoms ──
    setup_count = 0
    for atom_def in formula.get("setup", []):
        bead_id = create_atom(
            atom_def,
            shared_vars,
            epic_id=epic_id,
            atom_ids=atom_ids,
            global_labels=global_labels,
            barrier_ids=barrier_ids,
            barrier_label=barrier_label,
        )
        if not _dry_run:
            print(f"  Setup: {bead_id} — {substitute(atom_def['title'], shared_vars)}")
        setup_count += 1

    # ── Iterate atoms ──
    iterate_atom_count = 0
    if iterate_config:
        iterate_atoms = iterate_config.get("atoms", [])
        prev_item_barrier_id: str | None = None
        for item in items:
            item_vars = {**shared_vars, item_var: item}
            item_barrier_id: str | None = None

            for atom_def in iterate_atoms:
                bead_id = create_atom(
                    atom_def,
                    item_vars,
                    epic_id=epic_id,
                    atom_ids=atom_ids,
                    global_labels=global_labels,
                    barrier_ids=barrier_ids,
                    barrier_label=barrier_label,
                    prev_item_barrier_id=prev_item_barrier_id,
                )
                if not _dry_run:
                    print(f"  {item}: {bead_id} — {substitute(atom_def['title'], item_vars)}")
                iterate_atom_count += 1

                # Track this item's barrier for cross-item chaining
                atom_labels = atom_def.get("labels", [])
                atom_fid = substitute(atom_def["id"], item_vars)
                if barrier_label and (barrier_label in atom_labels or atom_fid.startswith("commit-")):
                    item_barrier_id = bead_id

            prev_item_barrier_id = item_barrier_id

    # ── Finalize atoms ──
    finalize_count = 0
    for atom_def in formula.get("finalize", []):
        bead_id = create_atom(
            atom_def,
            shared_vars,
            epic_id=epic_id,
            atom_ids=atom_ids,
            global_labels=global_labels,
            barrier_ids=barrier_ids,
            barrier_label=barrier_label,
        )
        if not _dry_run:
            print(f"  Finalize: {bead_id} — {substitute(atom_def['title'], shared_vars)}")
        finalize_count += 1

    # ── Summary ──
    total = len(atom_ids)
    print(f"\nCooked {total} atoms under epic {epic_id}")
    print(f"  Setup: {setup_count} atoms")
    if iterate_config:
        template_count = len(iterate_config.get("atoms", []))
        print(f"  Iterate: {template_count} templates x {len(items)} items = {iterate_atom_count} atoms")
    else:
        print("  Iterate: (none — linear pipeline)")
    print(f"  Finalize: {finalize_count} atoms")

    # Print dependency summary
    print("\nNext: bd ready")


def main() -> None:
    parser = argparse.ArgumentParser(description="Cook a formula into beads atoms")
    parser.add_argument("--formula", required=True, help="Path to formula YAML")
    parser.add_argument("--var", action="append", default=[], help="Variable in KEY=VALUE format (repeatable)")
    parser.add_argument("--epic-title", required=True, help="Title for the epic")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without calling bd")
    args = parser.parse_args()

    global _dry_run
    _dry_run = args.dry_run

    variables = parse_variables(args.var)
    cook(args.formula, variables, args.epic_title)


if __name__ == "__main__":
    main()
