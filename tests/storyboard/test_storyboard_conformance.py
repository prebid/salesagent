"""Storyboard-conformance grading via pytest (the storyboard-conformance job).

Grades a MEASURED run of the real ``@adcp/sdk`` storyboard runner (never
re-derived/inferred) as ordinary parametrized pytest tests, one per
``(protocol, track, storyboard_id, step_id)`` — reusing the exact
ledger/xfail/lock-test discipline ``tests/bdd/e2e_rest_known_failures.txt``
already established (``tests/storyboard/known_failures.txt`` +
``tests/storyboard/conftest.py``) instead of a second hand-rolled comparator
system (Core Invariant).

The runner is executed once per PROTOCOL. The agent serves both MCP and A2A and
the compliance checks apply to both, so grading only MCP would let the A2A
surface drift non-conformant with CI green. Each protocol gets its own agent
URL, its own summary artifact, and its own ledger namespace (test ids are
prefixed ``mcp::`` / ``a2a::``) — sharing any of the three would have the second
run silently overwrite the first.

Runner-reported skips (``missing_test_controller``, ``missing_tool``,
``prerequisite_failed``, ...) become native ``pytest.skip()`` calls — they are
never ledger entries. Only a genuine check FAILURE is ledgered.

Requires a live in-network stack and the runner's npm deps + the pinned
compliance/schema bundle (see ``tests/storyboard/runner/``; CI downloads it via
``.github/actions/_adcp-bundle``) — this module cannot be collected meaningfully without that
environment, matching how ``tests/bdd``'s e2e_rest transport and ``tests/e2e``
already require a live stack to collect.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.audit import ledger, storyboard_spec

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_DIR = Path(__file__).parent / "runner"
_ADCP_BIN = _RUNNER_DIR / "node_modules" / ".bin" / "adcp"

# Graded protocols, in ledger-namespace order. The runner's ``--protocol``
# (aliased ``--transport``) selects which surface of the SAME agent is exercised.
_PROTOCOLS: tuple[str, ...] = ("mcp", "a2a")

# In-network defaults, both behind the compose `proxy` service.
#
# MCP takes its endpoint directly: `/mcp/`, trailing slash included (FastMCP
# mounts it that way).
#
# A2A takes the agent's BASE url, NOT its JSON-RPC endpoint. A2A is card-first:
# the SDK calls buildCardUrls(), which appends `/.well-known/agent.json` then
# `/.well-known/agent-card.json` to the url verbatim — it does NOT strip a
# transport suffix the way computeBaseUrl() does. Passing `http://proxy:8000/a2a`
# therefore asks for `/a2a/.well-known/agent-card.json`, which 404s (verified live
# against the e2e stack), and the runner reports the agent unreachable without
# grading anything. From the base url the card is found at
# `/.well-known/agent-card.json` and the RPC endpoint (`/a2a`) comes off the card.
_DEFAULT_AGENT_URLS: dict[str, str] = {
    "mcp": "http://proxy:8000/mcp/",
    "a2a": "http://proxy:8000",
}

# Env vars the storyboard-conformance job MAY set. The compliance/schema paths
# have no default LITERAL, but they are DERIVED when unset: _bundle_path() resolves
# them through storyboard_spec.adcp_home(), whose second candidate is the pinned
# GitHub release bundle that .github/actions/_adcp-bundle extracts in-tree. The CI
# job therefore sets neither of them, and set-ness is NOT what decides whether a
# session can run — resolvability is (see _bundle_resolution_failure).
_AUTH_TOKEN_ENV = "STORYBOARD_AUTH_TOKEN"
_COMPLIANCE_DIR_ENV = "STORYBOARD_COMPLIANCE_DIR"
_SCHEMA_ROOT_ENV = "STORYBOARD_SCHEMA_ROOT"

# Where each lives INSIDE the extracted bundle. The bundle root comes from
# storyboard_spec.adcp_home(); only the leaf differs, so neither the version nor
# the containing path is written down here.
_BUNDLE_SUBDIR = {
    _COMPLIANCE_DIR_ENV: Path("compliance"),
    _SCHEMA_ROOT_ENV: Path("schemas"),
}

# Webhook receiver. Without one, every expect_webhook* step reports
# `requirement_unmet: webhook_receiver` and is silently ungraded.
#
# The address the SERVER must use to call back to this runner. In-network that is
# the runner container's compose alias (ADCP_WEBHOOK_HOST=tests, set on the tests
# service) — deliberately not "localhost", which the server rewrites to
# host.docker.internal. Unset on the host path, where loopback is correct.
_WEBHOOK_CALLBACK_HOST_ENV = "ADCP_WEBHOOK_HOST"
_WEBHOOK_PORT_ENV = "STORYBOARD_WEBHOOK_PORT"
_DEFAULT_WEBHOOK_PORT = "9998"


def _agent_url_env(protocol: str) -> str:
    """Per-protocol agent-URL override, e.g. ``STORYBOARD_AGENT_URL_A2A``."""
    return f"STORYBOARD_AGENT_URL_{protocol.upper()}"


def _summary_path(protocol: str) -> Path:
    """Per-protocol summary artifact.

    One shared path would have the second protocol's run overwrite the first's
    summary, silently grading one protocol twice.
    """
    return _RUNNER_DIR / "results" / f"ci-summary-{protocol}.json"


def _webhook_port(protocol: str) -> str:
    """Per-protocol receiver port, offset from the base by protocol index.

    The two runs are sequential, but giving each its own port removes any
    bind/TIME_WAIT interaction between them entirely. In-network the receiver is
    reached by compose alias, so any port is equally reachable.
    """
    base = int(os.environ.get(_WEBHOOK_PORT_ENV, _DEFAULT_WEBHOOK_PORT))
    return str(base + _PROTOCOLS.index(protocol))


def _bundle_resolution_failure() -> str | None:
    """Why the pinned bundle cannot be used, or ``None`` when it resolves.

    Set-ness of the two env vars is NOT the question: they are overrides, and
    _bundle_path() derives the paths when they are absent. Asking whether they were
    exported made this suite collect ONE skipped item and exit 0 in every automated
    run since it landed -- the CI job sets neither, because the bundle action puts
    the tree exactly where adcp_home() looks. Ask the resolver that already feeds
    the runner's own CLI args instead, so the gate cannot disagree with what the
    runner is handed.

    Resolution can itself fail: adcp_home() -> pinned_version() reads
    docs/adcp-spec-version.md and raises StoryboardAuditError on doc/SDK pin drift
    (or OSError when the file is missing). A contributor in a drifted or incomplete
    checkout must get the same SKIP as one who simply has no bundle -- never a
    collection error.
    """
    try:
        resolved = [_bundle_path(name) for name in (_COMPLIANCE_DIR_ENV, _SCHEMA_ROOT_ENV)]
    except (storyboard_spec.StoryboardAuditError, OSError) as exc:
        return f"pinned bundle could not be resolved: {type(exc).__name__}: {exc}"
    absent = [path for path in resolved if not Path(path).is_dir()]
    if absent:
        return "pinned bundle not found at: " + ", ".join(absent)
    return None


def _bundle_path(env_name: str) -> str:
    """Resolve a bundle path env var to an absolute path.

    The runner is spawned with ``cwd=_RUNNER_DIR`` so it can find its own
    ``node_modules``, but these paths are naturally written relative to the REPO
    ROOT (that is where the CI job's other paths are rooted, and where a developer
    runs pytest from). Passed through verbatim they resolve against the runner
    directory instead -- ``tests/storyboard/runner/tests/storyboard/runner/...`` --
    and the runner reports the cache as missing, which reads like a broken download
    rather than a path bug.

    Absolute values are passed through untouched.
    """
    override = os.environ.get(env_name)
    if override:
        raw = Path(override)
        return str(raw if raw.is_absolute() else (_REPO_ROOT / raw).resolve())
    # Unset: derive it. adcp_home() owns where the pinned tree lives, so the
    # version is never spelled outside it. tox.ini used to carry
    # `adcp-3.1.1/...` defaults, which is a version literal in a third place.
    return str((storyboard_spec.adcp_home(_REPO_ROOT) / _BUNDLE_SUBDIR[env_name]).resolve())


def _webhook_receiver_args(protocol: str) -> tuple[list[str], dict[str, str]]:
    """CLI args + extra env that let the runner host a reachable webhook receiver.

    Two topologies, and the difference is which interface the receiver must listen on:

    * **In-network** (the CI path): the server and this runner are separate
      containers. The server calls back to the runner's compose alias, so the
      receiver has to bind something other than loopback or the delivery lands on
      the container's eth0 with nothing listening. `proxy_url` mode is the SDK's
      sanctioned way to do that -- it takes the URL to advertise, and (unlike
      `loopback_mock`) permits a non-loopback bind.
    * **Host-side**: runner and published ports share a network namespace, so the
      SDK's default loopback receiver already works. Returns no args at all.

    ADCP_WEBHOOK_RECEIVER_HOST is NOT an upstream feature. The CLI has no
    `--webhook-receiver-host`, so it cannot pass `host` through to
    createWebhookReceiver() even though the library accepts it -- filed as
    adcontextprotocol/adcp-client#2448 and bridged meanwhile by
    tests/storyboard/runner/patches/ (version-keyed by patch-package), which adds the
    env var the issue proposes. Delete both when the flag ships.
    """
    callback_host = os.environ.get(_WEBHOOK_CALLBACK_HOST_ENV)
    if not callback_host:
        return [], {}

    port = _webhook_port(protocol)
    args = [
        "--webhook-receiver",
        "proxy",
        "--webhook-receiver-port",
        port,
        "--webhook-receiver-public-url",
        f"http://{callback_host}:{port}/",
    ]
    return args, {"ADCP_WEBHOOK_RECEIVER_HOST": "0.0.0.0"}


def _run_storyboard_runner(protocol: str) -> dict[str, Any]:
    """Shell out to the real @adcp/sdk storyboard runner once, return its summary JSON.

    Uses the pinned bundle CI downloads via ``.github/actions/_adcp-bundle``,
    pointed at the in-network agent rather than a host port, and forced onto
    ``protocol`` so the SAME compliance checks grade both of the agent's
    protocol surfaces.
    """
    agent_url = os.environ.get(_agent_url_env(protocol), _DEFAULT_AGENT_URLS[protocol])
    auth_token = os.environ.get(_AUTH_TOKEN_ENV, "ci-test-token")
    summary_path = _summary_path(protocol)
    cmd = [
        str(_ADCP_BIN),
        "storyboard",
        "run",
        agent_url,
        "--protocol",
        protocol,
        "--auth",
        auth_token,
        "--allow-http",
        "--compliance-version",
        storyboard_spec.pinned_version(_REPO_ROOT),
        "--compliance-dir",
        _bundle_path(_COMPLIANCE_DIR_ENV),
        "--schema-root",
        _bundle_path(_SCHEMA_ROOT_ENV),
        "--timeout",
        "600",
        "--json",
        "--summary-output",
        str(summary_path),
    ]
    webhook_args, webhook_env = _webhook_receiver_args(protocol)
    cmd += webhook_args
    # Grade only what THIS invocation measured. A summary left by an earlier run
    # would otherwise be read as if it were fresh whenever the runner dies before
    # writing one — inferred rather than measured, which is the Core Invariant.
    # The runner writes the summary here and will NOT create the directory. It is
    # deliberately not committed (a checked-in results/ was 1.8 MB of stale
    # host-side captures that nothing read), and it is gitignored, so a fresh
    # checkout has no results/ at all — create it rather than depending on an
    # empty directory surviving in git.
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.unlink(missing_ok=True)
    result = subprocess.run(  # noqa: S603
        cmd,
        cwd=_RUNNER_DIR,
        capture_output=True,
        text=True,
        timeout=700,
        env={**os.environ, **webhook_env},
    )
    if not summary_path.exists():
        pytest.fail(
            f"storyboard runner ({protocol}) did not produce a summary (exit={result.returncode}): "
            f"stdout={result.stdout[-2000:]!r} stderr={result.stderr[-2000:]!r}"
        )
    return json.loads(summary_path.read_text())


def _graded_total(summary: dict[str, Any]) -> int:
    """How many checks the runner actually graded, passes included.

    Not ``len(failures) + len(skip_causes)``: a protocol whose checks all PASS
    also has an empty failures list, and must not be confused with one where the
    runner never got far enough to grade anything.

    SKIPPED is not graded. Counting it made this function report a nonzero total
    for a run that graded nothing — 0 passed, 0 failed, any skipped — so
    :func:`_no_graded_checks` never fired and "measured nothing" read as
    "measured N". A skip is the runner declining to grade; only a pass or a
    failure is a verdict.
    """
    return sum(int(summary.get(key, 0)) for key in ("passed", "failed"))


def _no_graded_checks(protocol: str, summary: dict[str, Any]) -> dict[str, Any]:
    """The one synthetic FAILING check for a protocol the runner graded nothing on.

    A run that dies before grading (unreachable agent, rejected capability probe,
    wrong url) contributes zero parametrized tests. Left silent, that protocol's
    entire axis is vacuous while the job stays green — the precise false-green
    this module exists to prevent, and worse than a large ledger because nothing
    at all is being measured. So it becomes one ordinary failing check, ledgerable
    and graduating like any other: the day the protocol becomes reachable this
    entry xpasses and its real checks show up un-ledgered, failing CI until they
    are triaged.

    This is not a reclassification of runner-reported skips — those still map to
    native ``pytest.skip()``. It covers the case where the runner reports nothing.
    """
    return {
        "protocol": protocol,
        "track": "_runner",
        "storyboard_id": ledger.RUNNER_SYNTHETIC_STORYBOARD_ID,
        "step_id": ledger.RUNNER_SYNTHETIC_STEP_ID,
        "status": "fail",
        "reason": (
            f"runner graded 0 checks against {summary.get('agent_url')} "
            f"(overall_status={summary.get('overall_status')})"
        ),
        "reason_kind": "no_graded_checks",
    }


def _collect_checks(protocol: str) -> list[dict[str, Any]]:
    """One entry per (protocol, track, storyboard_id, step_id): a failure or a skip.

    Passed checks are not enumerated individually — the runner's summary
    reports a pass/fail/skip count, not a per-check pass record — so a
    passing check has no ledger identity to track; only failures and skips
    are gradeable per-check here.
    """
    summary = _run_storyboard_runner(protocol)
    checks: list[dict[str, Any]] = []
    for f in summary["failures"]:
        checks.append(
            {
                "protocol": protocol,
                "track": f["track"],
                "storyboard_id": f["storyboard_id"],
                "step_id": f["step_id"],
                "status": "fail",
                "reason": f["reason"],
                "reason_kind": f["reason_kind"],
            }
        )
    # skip_causes[].affected entries are "storyboard_id/step_id" (no track —
    # a gap in the runner's own summary shape; skips aren't ledgered so the
    # missing track doesn't affect grading, only the test id's display form).
    for cause in summary.get("skip_causes", []):
        for affected in cause.get("affected", []):
            storyboard_id, _, step_id = affected.partition("/")
            checks.append(
                {
                    "protocol": protocol,
                    "track": None,
                    "storyboard_id": storyboard_id,
                    "step_id": step_id,
                    "status": "skip",
                    "reason": cause.get("detail", ""),
                    "reason_kind": cause["cause"],
                }
            )
    if _graded_total(summary) == 0:
        checks.append(_no_graded_checks(protocol, summary))
    return checks


def _stale_ledger_entries(collected_ids: list[str]) -> list[str]:
    """Ledger entries with no corresponding collected check, in ledger order.

    The join the storyboard side never had. ``scripts.audit.ledger`` owns both
    the file and the id grammar, so this compares like with like rather than
    re-deriving either.
    """
    collected = set(collected_ids)
    return [entry.format() for entry in ledger.load(ledger.LEDGER) if entry.format() not in collected]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "storyboard_check" not in metafunc.fixturenames:
        return
    unusable = _bundle_resolution_failure()
    if unusable:
        metafunc.parametrize(
            "storyboard_check",
            [{"status": "skip", "reason": unusable, "reason_kind": "config"}],
            ids=["environment-not-configured"],
        )
        return
    checks = [check for protocol in _PROTOCOLS for check in _collect_checks(protocol)]
    # Built through the shared grammar (scripts.audit.ledger.LedgerCheckId) so
    # this producer and the ledger's parsers can never drift apart -- one
    # owner for the id shape on both ends of the join. `track` is None for
    # skip-cause entries; format()'s f-string renders that exactly the way
    # the old literal f-string did (the string "None"), so behavior here is
    # byte-for-byte unchanged.
    ids = [ledger.LedgerCheckId(c["protocol"], c["track"], c["storyboard_id"], c["step_id"]).format() for c in checks]

    # LEDGER FITNESS, computed IN-SESSION. Every ledger entry must
    # resolve to a check this session actually collected. A ledgered check that
    # starts passing simply VANISHES from `ids` — it produces no test item at all,
    # so without this join neither "graduation fails CI" nor "regression fails CI"
    # was true, and a stale entry sat there grading nothing.
    #
    # It has to be computed here rather than ported verbatim into tests/unit/ (the
    # shape the e2e_rest sibling uses): collection shells out to a live agent, so
    # an offline port would see one id and declare every entry unresolved. That
    # also means this only BITES in the in-network job — where `ids` is real.
    stale = _stale_ledger_entries(ids)
    if stale:
        checks.append(
            {
                "status": "fail",
                "protocol": "ledger",
                "track": "fitness",
                "storyboard_id": "ledger_fitness",
                "step_id": "stale_entries",
                "reason_kind": "ledger",
                "reason": (
                    "ledger entries resolve to no collected check — they graduated or were renamed, "
                    f"and are now grading nothing: {', '.join(stale)}. Remove them (the ledger only shrinks)."
                ),
            }
        )
        ids.append("ledger::fitness::ledger_fitness::stale_entries")

    metafunc.parametrize("storyboard_check", checks, ids=ids)


def test_storyboard_check(storyboard_check: dict[str, Any]) -> None:
    """One assertion per measured (protocol, track, storyboard_id, step_id) check.

    Known failures xfail(strict=False) via tests/storyboard/conftest.py's
    ledger loader (matched on this test's nodeid) — an un-ledgered failure is
    the regression signal this job exists to catch. The protocol is part of the
    ledger identity: an MCP-only fix must not silently graduate its A2A twin.
    """
    if storyboard_check["status"] == "skip":
        pytest.skip(f"{storyboard_check['reason_kind']}: {storyboard_check['reason']}")
    assert storyboard_check["status"] != "fail", (
        f"storyboard check failed: {storyboard_check['protocol']}/{storyboard_check['track']}/"
        f"{storyboard_check['storyboard_id']}/{storyboard_check['step_id']} "
        f"({storyboard_check['reason_kind']}) — {storyboard_check['reason']}"
    )
