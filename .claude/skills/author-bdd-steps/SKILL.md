---
name: author-bdd-steps
description: >
  Write a new BDD step definition, or bind a new scenario line onto existing
  steps, correctly the first time: verify against the pinned AdCP spec, confirm
  the scenario is wireable through the cross-transport harness, use the
  guarded dispatch/assertion helpers (never hand-rolled wire parsing), and
  prove the scenario actually executes. Use before writing any step body under
  tests/bdd/steps/, or before letting a new scenario line bind to an existing
  step.
args: <feature-file-or-scenario-text>
---

# Authoring BDD Steps

Normative rules live in `tests/CLAUDE.md` §"BDD authoring discipline (the five
rules)" and §"Error Verification Policy" — read those first; this skill is the
wiring guide for applying them while writing a step, not a restatement.
`tests/bdd/CLAUDE.md` has the quick-reference helper table.

**Do not follow the generic `qa-bdd:step-development` plugin skill for this
project.** Its When-step template is `except Exception as exc: ctx["error"] =
exc` — the exact hand-stash antipattern rule 2 forbids. It predates this
repo's harness and doesn't know `dispatch_request` or the wire-envelope
helpers exist.

## 1. Authority gate

Locate the scenario in its generated `tests/bdd/features/BR-UC-*.feature` —
that file (mirroring the pinned schema + storyboard) is the authority, not the
step body and not a `docs/test-obligations/` bootstrap artifact. Spec-cite
before writing code:

```bash
uv run python -c "import adcp; print(adcp.get_adcp_spec_version())"   # confirm the pin, docs/adcp-spec-version.md
git -C ~/projects/adcp show v3.1.1:dist/schemas/3.1.1/core/<schema>.json
```

If the scenario's wording diverges from what the schema actually requires,
fix the scenario (generated features can be edited locally — mirror the diff
upstream) rather than coding to a wrong scenario. Cite the divergence with the
spec version + path.

## 2. Wireability gate (HARD STOP)

Before writing a `When`, confirm the domain env this scenario needs can reach
the wire:

1. Which env class serves this scenario? (`tests/CLAUDE.md`'s environment
   table, or `tests/harness/*.py`.)
2. Does `dispatch_request` (`tests/bdd/steps/generic/_dispatch.py`) already
   route this tool/domain through `call_via`? If yes, use it — it's the ONE
   writer of `ctx["result"]` / `ctx["wire_response"]` / `ctx["wire_error_envelope"]`.
3. If the env or route doesn't exist yet: **wire it first** (add the env
   method, or extend `_dispatch.py`'s routing), or **single-transport-tag +
   xfail-route** the scenario with an explicit wiring-gap reason. Do this
   instead of reaching for a shortcut.

**Never add a new `# TRANSPORT-BYPASS`.** Grep the two existing ones before
you're tempted to add a third:

```bash
grep -rn "TRANSPORT-BYPASS" tests/bdd/
```

Each is a Given/When that skips wire dispatch and calls `_impl` directly —
which means the scenario reports green across a2a/mcp/rest/e2e_rest while
actually running the same in-process code four times. It is also why the
"correct" wire-assertion helper is the one that fails on these scenarios
(`assert_wire_error` raises loudly when no `wire_error_envelope` was
captured) — reconstructed `ctx["error"]` becomes the only path that "works".
That is a bug signal, not a reason to reach for the reconstructed path: wire
the env instead.

## 3. Binding audit

pytest-bdd binds a scenario line to a step by **text**, not by review. Before
writing a new step function, check whether the scenario's wording already
matches an existing one:

```bash
grep -rn "the response is an error variant" tests/bdd/steps/
```

If it does, **open that step's body and grade it against rule 3** (wire vs.
reconstructed). A new scenario silently inheriting an old, pre-policy step is
how weak assertions spread — the policy's boy-scout migration rule
(`tests/CLAUDE.md` §"Migration path") is mandatory when a new scenario rides
an old step, not optional. Upgrading the step is part of *this* change.

## 4. Authoring table

| Step | Use | Never |
|---|---|---|
| Given | env-owned setup method; `realize_e2e` (`tests/harness/_realize.py`) for the e2e branch | hand-stashing wire data into `ctx` |
| When | `dispatch_request(ctx, **kwargs)` (`tests/bdd/steps/generic/_dispatch.py`) | a local `_get_error*`/`call_impl` shortcut, a new `TRANSPORT-BYPASS` |
| Then (error) | `ctx["result"].assert_wire_error(code, recovery=..., message_substr=..., field=...)` (`tests/harness/transport.py`) | `ctx["error"]`, `getattr(error, "error_code")` on a reconstructed exception, hand-rolled envelope `.get()` chains — even if the envelope came from `ctx["wire_error_envelope"]` first, always go through `assert_wire_error`/`assert_envelope_shape`, never parse it inline |
| Then (success) | `wire_field(ctx, "x")` / `wire_dict(ctx)` / `wire_lookup(ctx, path)` (`tests/bdd/steps/_outcome_helpers.py`) | `result.payload.model_dump()` round-trips — proves serializer self-consistency, not what the buyer received |

`recovery` on `assert_wire_error`/`assert_envelope_shape` is a required
keyword — it pins the buyer-facing retry semantics, not a soft default.

## 5. Dormancy proof

Run the touched slice serial with `-rxX` and read the output:

```bash
uv run pytest tests/bdd/ -k "<scenario or file>" -rxX
```

Sub-second wall time, or a `StepDefinitionNotFoundError` / "No harness wired"
reason, means the scenario never ran — it auto-xfailed at fixture setup. A
green run that took no time is not a pass.

## 6. Antipattern ↔ guard map

Each antipattern below names the guard that's supposed to catch it. If your
step trips none of them but still does the thing on the left, the guard is
silent for this spelling — say so and escalate (extend the guard), don't
treat the pass as clean.

| Antipattern | Guard |
|---|---|
| No-op / pass-only Then | `test_architecture_bdd_no_pass_steps.py` |
| Truthiness-only assertion (`assert result`, not `assert x == y`) | `test_architecture_bdd_no_trivial_assertions.py` |
| Raw dict used as a step registry instead of a factory | `test_architecture_bdd_no_dict_registry.py` |
| 3+ steps with identical bodies | `test_architecture_bdd_no_duplicate_steps.py` |
| `ctx.get("env")` / `hasattr(env, ...)` silent-env checks | `test_architecture_bdd_no_silent_env.py` |
| `_get_error_code`/`_get_error_dict` called with no wire reference | `test_architecture_bdd_wire_discipline.py` (Check B — currently name-matched, not pattern-matched; a step with its own local `_get_error`-style helper is not yet caught) |
| A `TRANSPORT-BYPASS`/allowlisted direct-`_impl` call | `test_architecture_bdd_no_direct_call_impl.py` |

For the deeper semantic question — "does this Then actually verify what its
text claims?" — run `/inspect-bdd-steps` after writing.

## See Also

- `tests/bdd/CLAUDE.md` — quick-reference pointer + helper table
- `.claude/rules/workflows/xpass-graduation.md` — per-scenario graduation once a scenario legitimately passes
- `/inspect-bdd-steps` — post-hoc semantic assertion audit
- `/derive-tests` — the sibling skill for the *obligation* harness (different helper vocabulary — `assert_envelope`, not `assert_wire_error`; don't mix the two)
