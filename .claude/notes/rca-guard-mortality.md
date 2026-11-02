# Which guards die under the five structural changes

Classification of all 143 structural-guard files (`tests/unit/test_architecture_*.py` +
`test_guards*.py`) against the five changes in `rca-synthesis.md`. Inventory is mechanical
(AST: line counts, test counts, allowlist-literal entry counts, scan target). The bucket
assignment per row is judgment — arguable rows flagged at the bottom.

```
bucket                                       files   lines  allowlist
----------------------------------------------------------------------
S1  mypy check_untyped_defs = True              14    2879         10
S2  session private; UoW refuses to nest        15    5138       1122
S3  typed wire slot + SDK error catalogue       22    3863         31
S4  one typed entry point per tool              20    3850         24
S5  SDK delegation; models own predicates        8    1354          4
S6  unit corpus collapses (no subject left)     20    4031        108
GG  guards policing guards                       4     564          8
->  becomes a boot/CI assertion                 18    2593         44
==  genuinely needs a scan                      22    5084         58
----------------------------------------------------------------------
TOTAL                                          143   29356       1409

DEAD (deleted outright): 103 files, 21679 lines, 1307 allowlist entries
KEEP: 22 files, 5084 lines, 58 allowlist

--- KEEP detail ---
   1083   14  test_architecture_no_silent_loop_failures.py
    788    8  test_architecture_bdd_wire_discipline.py
    286    0  test_architecture_bdd_assertion_strength.py
    281   23  test_architecture_harness_realize_e2e_coverage.py
    225    0  test_guards_before_validator_no_mutation.py
    207    4  test_architecture_bdd_no_request_in_then.py
    185    9  test_architecture_bdd_no_pass_steps.py
    176    0  test_guards_bdd_no_duplicate_elif_branches.py
    168    0  test_architecture_harness_mcp_with_error_logging.py
    167    0  test_guards_bdd_duplicate_step_literals.py
    161    0  test_architecture_bdd_no_swallowed_dispatch_errors.py
    160    0  test_architecture_bdd_no_trivial_assertions.py
    157    0  test_architecture_no_silent_except.py
    153    0  test_architecture_bdd_no_silent_env.py
    142    0  test_architecture_bdd_no_duplicate_steps.py
    142    0  test_architecture_natural_key_immutability.py
    118    0  test_architecture_bdd_no_dict_registry.py
    112    0  test_architecture_no_raw_thread_registry.py
    108    0  test_architecture_bdd_step_text_alignment.py
     98    0  test_guards_a2a_integer_restoration.py
     93    0  test_architecture_bdd_no_shadowed_steps.py
     74    0  test_guards_wire_guard_none_fallback.py
```


## The headline

| | files | lines | allowlist |
|---|---|---|---|
| **Deleted outright** | **103** | **21,679** | **1,307** (93%) |
| Converted to a boot/CI assertion | 18 | 2,593 | 44 |
| Genuinely still needs a scan | 22 | 5,084 | 58 |

Of the 22 that survive, **16 are BDD/harness integrity** — the category that polices the one thing
that should exist. **Six guards survive over production code**: `no_silent_loop_failures`,
`no_silent_except`, `before_validator_no_mutation`, `natural_key_immutability`,
`no_raw_thread_registry`, `a2a_integer_restoration`.

143 → 6.

## The single highest-leverage change

**S2 alone — make `get_db_session` module-private, session reachable only through a UoW, and
`BaseUoW.__enter__` refuse to nest — removes 1,122 of 1,409 allowlist entries (80%) and 5,138
lines of guard code.** `repository_pattern` (833 entries) and `no_raw_select` (270) both enforce
"do not touch the session here," which becomes unwritable when the session has no public name.

That is one afternoon of visibility work against the two largest ledgers in the repo.

## Rows I am least confident about

- **`no_silent_loop_failures` (1,083 lines, 14 allowlist)** — the largest KEEP. "Per-item failures
  in `_impl` loops must be surfaced" may be expressible as a result type that must be consumed,
  in which case it is DEAD under S3/S4 and the production residue drops to five.
- **`local_schema_imports`** — filed under S4, but it currently enforces the *opposite* direction
  of the fix (use the local subclass, not the SDK type). Under S4/S5 it is not merely dead, it is
  backwards. Worth reading before deleting.
- **The xfail-ledger guards** (`e2e_rest_escape_hatches`, `uc010_dormancy_citations`,
  `bdd_stale_xfail_reason_text`, …) — filed S6 as test bookkeeping, but an xfail ledger is
  legitimate when production genuinely has a gap. What dies is the ledger's *permanence*, not the
  concept.
- **`bdd_wire_discipline` (788 lines)** — kept, and it is also the guard that let `_error_details`
  through on subject scope. Keeping it means keeping the thing that already failed once.

## What this does NOT measure

The guard corpus is 29,356 lines. `tests/unit` is **135,574 lines across 533 files and 5,245 test
functions**, against 2,743 BDD scenarios. Collapsing the unit corpus is the larger exercise and is
not classified here — S6 only counts the guards that lose their subject when it collapses.
