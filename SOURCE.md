# Where the work for #1440 comes from

## Scope: HALF of #1440 only

#1440 describes TWO defects. This branch fixes the FIRST one only.

**IN SCOPE — canonical agent-card paths.** `/.well-known/agent.json` and
`/agent.json` return 404; only `/.well-known/agent-card.json` is served.

**OUT OF SCOPE — the v0.3 dual-emit.** Do NOT add a second `AgentInterface`
entry at `protocol_version=0.3`. That was investigated and decided against on
2026-08-31; see
https://github.com/prebid/salesagent/issues/1440#issuecomment-5476083375
Summary: no live v0.3-only consumer was identified. The only reader wanting the
top-level `url` is `@a2a-js/sdk 0.3.14`, a transitive dep of `@adcp/sdk 11.0.0`
in the conformance runner — and that library is on 1.1.0. The runner is behind;
the protocol is not. The fix belongs upstream, not as compat surface here.

There IS a prior implementation of the 0.3 dual-emit at commit `c5379a515`
("fix(a2a): dual-emit the agent card so the 0.3-era compat path resolves").
**Do not cherry-pick it.** It is recorded here so nobody rediscovers it and
assumes it was an oversight.

## The defect, located

`src/app.py`:

- `:308-311` — `create_agent_card_routes(card_url="/.well-known/agent-card.json")`
  creates a route for exactly ONE path.
- `:394` — `_AGENT_CARD_PATHS` names all THREE paths.
- `:397-413` — `_replace_routes()` walks `app.routes` and REPLACES any route
  whose path is in that set. It never CREATES one. So the two paths the SDK
  did not make are never made.
- `:415-417` — the code then logs its own bug on every startup:
  `_replace_routes: expected SDK routes not found for paths:
   ['/agent.json', '/.well-known/agent.json']`

Write this fresh — it is a handful of lines, and there is no prior commit to
lift. Fixing it should also remove that permanent false-alarm warning, or turn
it into an assertion that can actually fail.

## Verify

Serve the app and assert 200 (not 404) on all three paths, with byte-identical
bodies. There is an existing e2e module for the A2A surface —
`tests/e2e/test_a2a_endpoints_working.py` — which #1440's own sequencing note
names as the shared test file.
