# Xpass Graduation Workflow

Protocol for graduating an xpassed BDD ledger entry (a scenario marked
"spec-production gap" that now passes). Graduation = removing the xfail
routing — a tag in a conftest xfail set (e.g. `_UC019_XFAIL_TAGS` in
`tests/bdd/conftest.py`) or a line in `tests/bdd/e2e_rest_known_failures.txt`.

**One scenario at a time. Never bulk-remove.** An xpass has two possible
causes: production now implements the behavior, or the scenario/steps are too
weak to fail. Only the first graduates; the second gets fixed first.

## Per scenario

### 1. Map test → tag → scenario

Find which tag (or ledger line) routes the xpassed test, and open the scenario
in the generated `BR-UC-*` feature file. The generated feature is the
authority (schema + storyboard) — not the conftest comment, not the step file.

### 2. The scenario must encode the FULL logic

The scenario itself — not the step definitions — carries the behavioral
contract: the logic, the boundary conditions as examples, the exact expected
outcome (error code, response fields). Step defs just execute it.

If the scenario is vague ("the result is valid", missing boundary examples,
outcome under-specified), **correct the scenario first** — then re-evaluate
whether it still xpasses. Generated features CAN be edited locally (generation
merges semantically); mirror any divergence upstream.

### 3. Re-verify the obligation against the pinned spec

Check what the scenario demands — especially the error code — against the
pinned AdCP version's enums and prose. A ledgered xfail can be over-specified
(demand non-spec behavior); the fix for that is upstream reconciliation, not
graduation and not patching production to match a wrong scenario.

### 4. Inspect the GIVEN steps — cross-transport setup

Setup must go through the shared cross-transport environment harness (the
domain env + factories), so the scenario logic and step defs are literally the
same across all transports — mcp, a2a, rest, e2e_rest. A Given that hand-rolls
transport-specific setup (raw SQL, per-transport branches, direct mock pokes)
means the transports are not running the same scenario; fix the setup before
trusting the xpass.

### 5. Inspect the THEN steps — wire-envelope assertions

Assertions must go through the transport-dependent helpers on the EXACT
response from the run — `assert_envelope_shape(result.wire_error_envelope,
CODE, recovery=...)` is the one for error paths (success paths:
`wire_response` / typed payload per tests/CLAUDE.md). Truthiness checks,
reconstructed-exception asserts (`isinstance`, `.error_code`), or asserts on
harness-side reconstructions do not count — a pass through those can be
vacuous. If the assertion is weak, strengthen it to the exact obligation
(never delete assertions), then re-check whether the scenario still passes.

### 6. Trace production

Follow the concrete request through the full call chain: transport wrapper →
`_impl` → typed `AdCPError` → boundary translation → wire envelope. The
xpass must be explained by real production behavior you can point at, not
inferred from the green mark.

### 7. Check sibling transports

Removing an in-process tag un-xfails the scenario on ALL in-process transports
at once (impl/a2a/mcp/rest) — verify each, not just the ones seen xpassing.
Check `tests/bdd/e2e_rest_known_failures.txt` for the same scenario: if listed
and xpassing in bdd-in-network runs, graduate it in the same change; if listed
and still failing there, record why (transport-specific gap) and leave it.

### 8. Remove the routing with evidence

Delete the tag / ledger line and add a `# Graduated: ...` comment citing the
inspection and the run id, mirroring the existing precedent comments in the
file.

### 9. Verify

- Run the scenario's module on the box, serial (`saci test bdd <module>` —
  xdist deadlocks on a single agent-db): the graduated scenarios must show as
  plain PASS on all in-process transports, zero new failures.
- If the e2e_rest ledger was touched, a bdd-in-network run is required — it is
  the only job that grades that ledger.
- Final gate: `./run_all_tests.sh`.

## Anti-patterns

- Bulk-deleting xpassed entries because "they pass now"
- Graduating on a vacuous pass (weak Then, transport-divergent Given)
- Patching a generated scenario to match production instead of reconciling
  with the spec (or fixing production)
- Treating the conftest comment or xfail reason as the obligation — the
  generated scenario + pinned spec are the authority
