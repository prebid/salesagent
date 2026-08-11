# BDD Tests — Pointer

Normative content lives in `tests/CLAUDE.md` §"BDD authoring discipline (the
five rules)" and §"Error Verification Policy" — read those before touching
anything in this directory. This file only adds what's missing from there:
where things live, and which helper to reach for.

Writing a new step or wiring a new scenario? Use `/author-bdd-steps` — it
walks the authority gate, the wireability gate, and the binding audit before
you write a line of step code.

## Authority hierarchy

Generated `tests/bdd/features/BR-UC-*.feature` (mirrors the pinned AdCP
schema + storyboard; can be edited locally, mirror the diff upstream) is the
authority — not the step body, not a docs/test-obligations bootstrap. See
`.claude/rules/workflows/xpass-graduation.md` for the full authority chain
and the per-scenario graduation protocol.

## Which helper for which situation

| Situation | Use | Never |
|---|---|---|
| Given: seed state | env-owned setup method (factories via the harness env; `realize_e2e` in `tests/harness/_realize.py` for the e2e branch) | hand-stashing wire data into `ctx` |
| When: make the request | `dispatch_request(ctx, **kwargs)` (`tests/bdd/steps/generic/_dispatch.py`) — the ONE writer of `ctx["result"]` / `ctx["wire_response"]` / `ctx["wire_error_envelope"]` | a local `_get_error*`/`call_impl` shortcut, a `# TRANSPORT-BYPASS` in new code |
| Then: error path | `ctx["result"].assert_wire_error(code, recovery=..., message_substr=..., field=...)` (`tests/harness/transport.py`) | `ctx["error"]`, `.error_code` on a reconstructed exception, hand-rolled envelope `.get()` chains |
| Then: success path | `wire_field(ctx, "x")` / `wire_dict(ctx)` / `wire_lookup(ctx, path)` (`tests/bdd/steps/_outcome_helpers.py`) | `result.payload.model_dump()` round-trips (proves serializer self-consistency, not what the buyer received) |

## Where domain-env wiring lives

- Env classes (one per domain): `tests/harness/*.py` — see `tests/CLAUDE.md`'s
  environment-hierarchy table for the current list.
- Transport dispatch routing for a new tool: `tests/bdd/steps/generic/_dispatch.py`.
- E2E realization of an env setup intent (DB row vs. mock): `tests/harness/_realize.py`.
- Step files: `tests/bdd/steps/generic/` (reusable) vs. `tests/bdd/steps/domain/` (use-case-specific).

## Dormancy + graduation

After touching step files, run the touched slice with `-rxX` (rule 5 in
`tests/CLAUDE.md`) — a scenario that never ran reads as a pass otherwise.
Removing an xfail/allowlist entry once a scenario genuinely passes follows
`.claude/rules/workflows/xpass-graduation.md`, one scenario at a time.
