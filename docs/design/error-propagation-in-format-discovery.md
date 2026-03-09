# Decision: Error Propagation in Format Discovery

**Date:** 2026-03-07
**Status:** Decided
**Author:** Konstantin Mirin

## Context

`list_available_formats()` queries creative agents to build the format catalog.
When a creative agent is unreachable or fails, the function currently catches
all exceptions and returns an empty list. This makes "no formats exist"
indistinguishable from "the format catalog is unreachable."

The AdCP `list-creative-formats-response.json` schema provides both a required
`formats` array and an optional `errors` array (`error.json` items with `code`,
`message`, `field`, `suggestion`). The spec allows but does not mandate using
`errors[]` for partial failures.

## Decision

**Use `errors[]` per the AdCP response pattern.** When creative agents fail:

1. **Per-agent failure:** Return formats from healthy agents + one error per
   failed agent in the `errors[]` array. Callers get partial results and can
   see which agents failed.

2. **Registry creation failure:** Return empty `formats` + `errors[]` explaining
   the infrastructure failure. This is a total failure, not just "no formats."

3. **Never silently return `[]` for infrastructure errors.** An empty `formats`
   array without `errors[]` means "this tenant genuinely has no formats
   configured," not "something broke."

## Rationale

- The AdCP spec already provides the `errors[]` field for exactly this purpose
- Buyers need to distinguish "no formats" from "formats unavailable" to decide
  whether to retry or escalate
- Operators need visibility into which creative agents are failing without
  parsing server logs
- Partial success (some agents healthy, some not) is the common case in
  multi-agent setups — total failure should not be the only option

## Obligations Derived From This Decision

These are product obligations, not AdCP spec obligations. Tests reference this
document as their source of truth.

- **FD-ERR-01:** When a creative agent fails, `list_available_formats` returns
  formats from other agents plus an error entry for the failed agent.
- **FD-ERR-02:** When all creative agents fail, `list_available_formats` returns
  empty `formats` plus `errors[]` (not bare `[]`).
- **FD-ERR-03:** When registry creation fails, `list_available_formats` returns
  empty `formats` plus `errors[]` describing the infrastructure failure.
- **FD-ERR-04:** Each error entry follows AdCP `error.json` schema (`code`,
  `message`, at minimum).
- **FD-ERR-05:** When all agents succeed, `errors[]` is absent or empty.

## Affected Code

- `src/core/format_resolver.py` — `list_available_formats()` (L158-219)
- `tests/unit/test_format_resolver.py` — SUSPECT tests to be replaced with
  obligation tests

## Implementation

Tracked in beads task `salesagent-ofuk`.
