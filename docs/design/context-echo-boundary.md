# Context echo from the boundary (retired)

This file was the frozen scope authority for salesagent-3cs7o.1, written before the
work landed. The design shipped, so the description of it lives with the request path it
is a step of:

- **What the code does:**
  [Request lifecycle § The context echo](../development/request-lifecycle.md#the-context-echo)
  — `_served` in `src/core/tools/_boundary.py` is the one writer, on a fresh run, a
  replay, and a failure alike, and the four refusals that keep it the only one.
- **Why a failure is a response and not a detached dict:**
  [Request lifecycle § Failure](../development/request-lifecycle.md#failure), and
  `AdcpErrorResponse` in `src/core/schemas/_base.py`, whose docstring carries the
  grounding this file used to hold (`core/protocol-envelope.json` declares
  `adcp_error`, `context`, and a required `status` on every response envelope).

Two statements in the retired text disagreed with what shipped, so do not carry them
forward: `serve` takes the request **headers**, not a credential, and so does
`invoke_tool`; and the `context=` keyword ban
(`.ast-grep/rules/context-is-written-by-the-boundary-alone.yml`) is scoped to `src/`
only, not to `scripts/`.

This file survives as a pointer rather than a deletion because
`docs/reviews/epic-3cs7o-verification.html` cites it by path as the baseline that
lane was graded against.
