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

import hashlib
import json
import os
import subprocess
import tarfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from scripts.audit import ledger, storyboard_spec
from scripts.setup.seed_storyboard_tenant import STORYBOARD_SUBDOMAIN, STORYBOARD_TOKEN
from tests.storyboard import collected

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
# transport suffix the way computeBaseUrl() does. Passing the url WITH its `/a2a`
# suffix therefore asks for `/a2a/.well-known/agent-card.json`, which 404s (verified
# live against the e2e stack), and the runner reports the agent unreachable without
# grading anything. From the base url the card is found at
# `/.well-known/agent-card.json` and the RPC endpoint (`/a2a`) comes off the card.
#
# The agent is `adcp-server-storyboard`, NOT the `adcp-server` behind `proxy` that the
# other in-network suites drive. It is the same image, the same database and the same
# anchored configuration, differing in one variable: ENVIRONMENT=production, so the
# boundary runs `extra="ignore"` and DROPS a property this seller's schemas do not
# declare instead of refusing it. That is what a deployed seller does, and a conformance
# storyboard grades a deployed seller. Under the development strictness the other suites
# want, the five read tools answered INVALID_REQUEST to a request carrying
# `idempotency_key` -- a field AdCP 3.1 puts on every task request and
# compliance/universal/read-tool-idempotency.yaml requires sellers to TOLERATE.
#
# ONE ORIGIN, BOTH PROTOCOLS. Same host, same TLS front, same Host header, therefore the
# same tenant resolution and the same published identity — differing only in the path,
# which the protocols themselves fix. That sameness is the POINT of grading two surfaces:
# what the axes are for is proving one deployment behaves the same either way, and two
# axes on two origins compare nothing. A green A2A reached by dialing it differently from
# MCP is a label, not evidence.
#
# The origin is `storyboard.adcp.test:8443`, behind `tls-proxy` (alias in
# docker-compose.e2e.yml, SNI map in config/nginx/nginx-tls-test.conf.template) rather
# than the service on plaintext :8080 — and A2A is why the scheme has to be real. A2A is
# card-first: the runner reads the RPC endpoint off `/.well-known/agent-card.json` rather
# than being told it, and `src/app.py`'s `get_protocol` renders **https** for any host
# that is not loopback. Dialed plaintext, the card published
# `https://adcp-server-storyboard:8080/a2a` — TLS to a plaintext port — so every
# card-derived call failed with `fetch failed`: 25 checks on run sa-0c74d963, one of them
# the capability probe the runner SELECTS storyboards from, which is why that axis
# executed 25 storyboards where MCP executed 44 and passed 3 where MCP passed 33. The card
# was right and the origin was wrong. This front forwards `Host` verbatim and sets
# `X-Forwarded-Proto`, the signal `get_protocol` prefers, so what it publishes is what it
# speaks.
#
# MCP takes its endpoint directly (`/mcp/`, trailing slash included — FastMCP mounts it
# that way). A2A takes the BASE url: the SDK appends `/.well-known/...` verbatim, so a
# `/a2a` suffix would ask for `/a2a/.well-known/agent-card.json`, which 404s.
_DEFAULT_AGENT_URLS: dict[str, str] = {
    "mcp": "https://storyboard.adcp.test:8443/mcp/",
    "a2a": "https://storyboard.adcp.test:8443",
}

# Env vars the storyboard-conformance job MAY set. The compliance/schema paths
# have no default LITERAL, but they are DERIVED when unset: _bundle_path() resolves
# them through storyboard_spec.adcp_home(), whose second candidate is the pinned
# GitHub release bundle that .github/actions/_adcp-bundle extracts in-tree. The CI
# job therefore sets neither of them, and set-ness is NOT what decides whether a
# session can run — resolvability is (see _bundle_gate).
_AUTH_TOKEN_ENV = "STORYBOARD_AUTH_TOKEN"
_COMPLIANCE_DIR_ENV = "STORYBOARD_COMPLIANCE_DIR"
_SCHEMA_ROOT_ENV = "STORYBOARD_SCHEMA_ROOT"

# WHICH SELLER the credential belongs to, as the `-H KEY=VALUE` the runner sends on every
# request. Without it the credential is rejected: a token is only ever verified INSIDE the
# tenant the request addresses. `_detect_tenant` (src/core/resolved_identity.py) tries the
# Host as a virtual_host, then its first label as a subdomain, then this header; with no
# tenant identified the token was looked up in none and every credentialed step answered
# AUTH_INVALID -> 401 (26 checks on run innet_140926_2318).
#
# The storyboard now has its OWN seeded tenant, and the header names it. It used to name the
# CI tenant, and this comment used to say "not a second seeded tenant" — that was wrong, and
# the agent card is what proved it. The card publishes a tenant's STORED host
# (`canonical_agent_url`), so with the CI tenant's identity it advertised
# `ci-test.<SALES_AGENT_DOMAIN>` — a name nothing on the compose network answers. A2A is
# card-first: the runner fetched the card, followed that URL, and every check errored
# `getaddrinfo ENOTFOUND` (0 passed, 64 failed, 25 of 72 storyboards executed, while MCP
# kept 30/21/249 because it reads no card). The storyboard tenant declares
# `virtual_host = STORYBOARD_VIRTUAL_HOST`, the front it is actually served on, so the card
# it publishes is reachable.
#
# The value is the seeded SUBDOMAIN, not the tenant_id: the seeder mints the id as a fresh
# uuid4 per database, so the subdomain is the only stable spelling, and `_detect_tenant`
# tries the hint as a subdomain before taking it as an id. Both it and the token are
# IMPORTED from the seeding script rather than spelled again here — that script is what
# makes them true in the database, and a second literal is a silent 401 the day either
# moves. They must move TOGETHER: a principal belongs to one tenant, so the storyboard's
# token is only valid inside the storyboard's tenant.
#
# The pinned runner SDK carries the header for exactly this case: `-H, --header K=V  Extra
# HTTP header on every request ... Common use: -H x-adcp-tenant=<id> for tenant routing
# behind a reverse proxy` (bin/adcp.js), and its storyboard options type documents it as
# "Forwarded into `AgentConfig.headers`, so MCP and A2A transports both see them" — one
# spelling, both graded axes. It softens no graded check: the pinned compliance tree says
# nothing about tenant routing, so no storyboard step grades how a buyer selects a seller.
_TENANT_ROUTING_HEADER = f"x-adcp-tenant={STORYBOARD_SUBDOMAIN}"

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


def _node_ca_env(agent_url: str) -> dict[str, str]:
    """``NODE_EXTRA_CA_CERTS`` for an https agent, or nothing for plaintext.

    The runner is Node, and the TLS front serves a leaf signed by the test CA that
    ``scripts/dev/gen_test_tls.py`` generates. Node trusts its own bundle only, so
    without this every https call fails the handshake — indistinguishable, in the
    runner's output, from the plaintext-port failure this URL change fixes.

    The path comes from ``E2E_CA_BUNDLE``, the variable compose already sets and tox
    already passes through, with the same repo-relative fallback ``tests/e2e/conftest.py``
    uses. Absent for an http URL: pointing Node at a CA it does not need would make a
    missing bundle look like a passing configuration.
    """
    if not agent_url.startswith("https://"):
        return {}
    bundle = os.environ.get("E2E_CA_BUNDLE") or str(_REPO_ROOT / ".test-tls" / "ca.pem")
    return {"NODE_EXTRA_CA_CERTS": bundle}


def _webhook_port(protocol: str) -> str:
    """Per-protocol receiver port, offset from the base by protocol index.

    The two runs are sequential, but giving each its own port removes any
    bind/TIME_WAIT interaction between them entirely. In-network the receiver is
    reached by compose alias, so any port is equally reachable.
    """
    base = int(os.environ.get(_WEBHOOK_PORT_ENV, _DEFAULT_WEBHOOK_PORT))
    return str(base + _PROTOCOLS.index(protocol))


# The pinned bundle is a release asset of the spec repo, not something this repo vendors.
_BUNDLE_REPO = "adcontextprotocol/adcp"
_BUNDLE_URL = "https://github.com/{repo}/releases/download/v{version}/{asset}"
_BUNDLE_FETCH_TIMEOUT = 120


def _materialize_bundle() -> str | None:
    """Put the pinned compliance tree on disk, fetching the release asset if needed.

    THE GRADING SUITE RESOLVES ITS OWN BUNDLE. It used to require that a separate step had
    already downloaded and extracted the tree: ``adcp_home()`` looks for
    ``tests/storyboard/runner/adcp-<version>/``, which is gitignored, and otherwise falls
    through to ``~/projects/adcp`` -- one maintainer's personal clone. Any environment that
    did not run ``.github/actions/_adcp-bundle`` first resolved to a path that has never
    existed, every check de-collected, and the job exited 0 having graded nothing (measured:
    run innet_080926_1118, storyboard.json summary {'passed': 1, 'skipped': 1}).

    Three sources, first hit wins, so every environment lands somewhere:

    1. The extracted tree. Nothing to do.
    2. A tarball already beside the runner -- what the CI action leaves behind, and what a
       second run in the same container reuses.
    3. The pinned release asset, over plain https. The version comes from the installed SDK,
       so the asset cannot disagree with the code under audit, and the archive is public: no
       ``gh``, no token, no environment variable pointing anywhere.

    The checksum is verified BEFORE extracting. A corrupt or truncated archive that unpacks
    far enough to look like a tree would otherwise grade a buyer contract against whatever it
    contained.

    Returns a reason string when the tree cannot be produced, or None on success. Callers
    treat that reason as a FAILURE, never a skip.
    """
    version = storyboard_spec.pinned_version(_REPO_ROOT)
    target = _RUNNER_DIR / f"adcp-{version}"
    if target.is_dir():
        return None

    archive = _RUNNER_DIR / f"{version}.tgz"
    checksum = _RUNNER_DIR / f"{version}.tgz.sha256"
    if not archive.is_file() or not checksum.is_file():
        fetch_failure = _fetch_bundle(version, archive, checksum)
        if fetch_failure is not None:
            return fetch_failure

    expected = checksum.read_text(encoding="utf-8").split()[0]
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected:
        return f"bundle checksum mismatch for {archive}: expected {expected}, got {digest}"

    with tarfile.open(archive, "r:gz") as tar:
        _safe_extract(tar, _RUNNER_DIR)
    if not target.is_dir():
        return f"bundle extracted, but {target} is not a directory"
    return None


def _fetch_bundle(version: str, archive: Path, checksum: Path) -> str | None:
    """Download the pinned release asset and its checksum. Returns a reason on failure."""
    _RUNNER_DIR.mkdir(parents=True, exist_ok=True)
    for path, asset in ((archive, archive.name), (checksum, checksum.name)):
        url = _BUNDLE_URL.format(repo=_BUNDLE_REPO, version=version, asset=asset)
        try:
            with urllib.request.urlopen(url, timeout=_BUNDLE_FETCH_TIMEOUT) as response:  # noqa: S310 - fixed https URL
                body = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return (
                f"could not fetch {url}: {exc}. Run .github/actions/_adcp-bundle's two "
                f"commands, or place {archive.name} beside the runner"
            )
        path.write_bytes(body)
    return None


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract *tar* under *dest*, refusing any member that escapes it.

    The archive is a published artifact rather than buyer input, so this is not a defence
    against an attacker. It keeps a malformed archive from scattering files across the repo,
    which is the failure that is hard to diagnose afterwards.
    """
    root = dest.resolve()
    for member in tar.getmembers():
        if (root / member.name).resolve().is_relative_to(root):
            continue
        raise RuntimeError(f"refusing tar member outside {root}: {member.name}")
    tar.extractall(dest)  # noqa: S202 - every member checked above


def _unresolvable_bundle_paths() -> list[str]:
    """The bundle paths that do not resolve to a directory on disk.

    Asks the FILESYSTEM, not the environment. The two env vars are OVERRIDES; when one is
    unset :func:`_bundle_path` derives the location from ``storyboard_spec.adcp_home()``,
    and that derivation is the normal case rather than the exception -- nothing sets them,
    not CI (the storyboard job runs ``./run_all_tests.sh storyboard`` after extracting the
    bundle and sets neither) and not a developer.

    This used to read ``os.environ.get(name)`` and call an absent var missing. tox.ini
    declares both as ``{env:NAME:}``, which sets them to the EMPTY STRING on every run, so
    the check reported both missing every time, parametrized a single config skip, and
    returned before the derivation could run.
    The branch :func:`_bundle_path` documents as "Unset: derive it" was therefore
    unreachable, and every storyboard run graded nothing -- 1 passed, 1 skipped, exit 0.
    That is the false green ``_no_graded_checks`` exists to refuse, arriving one layer above
    it where that guard cannot see it (measured: cassini run 097b0091, storyboard.json
    summary {'passed': 1, 'skipped': 1}).

    An empty list here means the bundle really is on disk; a non-empty one names the paths
    that do not exist, and :func:`_bundle_gate` turns that into a failure.
    """
    return [name for name in (_COMPLIANCE_DIR_ENV, _SCHEMA_ROOT_ENV) if not Path(_bundle_path(name)).is_dir()]


def _bundle_gate() -> tuple[str, dict[str, Any]] | None:
    """The ``(test id, check)`` to grade when the pinned bundle cannot be used, or None.

    Asks :func:`_bundle_path` -- the SAME resolver that builds the runner's
    ``--compliance-dir``/``--schema-root`` arguments -- so the gate and the runner can
    never disagree about where the bundle is. Set-ness of the two env vars is not the
    question: they are overrides, and the derivation is the normal case.

    Two dispositions, and what separates them is whether this environment could have
    graded at all:

    * **skip** -- the PIN itself does not resolve. ``pinned_version()`` reads
      ``docs/adcp-spec-version.md`` and raises ``StoryboardAuditError`` when the doc has
      drifted from the installed SDK (``OSError`` when the file is absent). A contributor
      in a drifted or incomplete checkout has no version to fetch a bundle FOR, so there
      is nothing to grade and nothing to blame -- a skip, never a collection error.
    * **fail** -- the pin resolves, so the bundle is obtainable: already extracted, a
      tarball beside the runner, or the pinned release asset (:func:`_materialize_bundle`).
      If the paths still do not resolve after that, the run would grade zero conformance
      checks and exit 0, which is indistinguishable from a clean run at a glance and is
      how a bundle-path regression survives. There is no legitimate run in which grading
      nothing is a pass, so the absence is a FAILURE whose reason names what was missing.

    Each disposition keeps its own test id, and both are load-bearing:
    ``environment-not-configured`` is the id the collection-gate and ledger-fitness
    graders join on for the unconfigured-session case, and ``bundle-not-present`` names
    the failure so a false green cannot hide behind a word meaning "skipped".
    """
    try:
        storyboard_spec.pinned_version(_REPO_ROOT)
    except (storyboard_spec.StoryboardAuditError, OSError) as exc:
        return "environment-not-configured", _bundle_gate_check(
            "skip",
            f"pinned bundle could not be resolved: {type(exc).__name__}: {exc}",
            "unresolvable-pin",
        )
    # Resolve our own bundle before deciding anything. The suite grades a buyer contract;
    # it does not wait for a separate download step to have happened.
    extraction_failure = _materialize_bundle()
    unresolved = _unresolvable_bundle_paths()
    if not unresolved:
        return None
    detail = ", ".join(f"{name}={_bundle_path(name)}" for name in unresolved)
    cause = f"{extraction_failure}; " if extraction_failure else ""
    return "bundle-not-present", _bundle_gate_check(
        "fail", f"{cause}pinned AdCP bundle not found: {detail}", "not-present"
    )


def _bundle_gate_check(status: str, reason: str, step_id: str) -> dict[str, Any]:
    """One synthetic check in the shape every other check has.

    ``test_storyboard_check``'s assertion message reads the identity keys, so a synthetic
    check carries them too and the failure reads like every other one rather than as a
    ``KeyError``.
    """
    return {
        "status": status,
        "reason": reason,
        "reason_kind": "config",
        "protocol": "-",
        "track": "-",
        "storyboard_id": "bundle",
        "step_id": step_id,
    }


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

    The bind address is passed as ``--webhook-receiver-host``, a first-class CLI flag.
    It was bridged by a patch-package edit while the flag did not exist (filed as
    adcontextprotocol/adcp-client#2448); the flag ships in the pinned SDK, so the patch
    and the ADCP_WEBHOOK_RECEIVER_HOST env var it added are both gone.
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
        # Not loopback: the server is a DIFFERENT container and calls back to this
        # runner's compose alias, so a receiver bound to 127.0.0.1 puts the delivery on
        # the container's eth0 with nothing listening.
        "--webhook-receiver-host",
        "0.0.0.0",
    ]
    return args, {}


def _run_storyboard_runner(protocol: str) -> dict[str, Any]:
    """Shell out to the real @adcp/sdk storyboard runner once, return its summary JSON.

    Uses the pinned bundle CI downloads via ``.github/actions/_adcp-bundle``,
    pointed at the in-network agent rather than a host port, and forced onto
    ``protocol`` so the SAME compliance checks grade both of the agent's
    protocol surfaces.
    """
    agent_url = os.environ.get(_agent_url_env(protocol), _DEFAULT_AGENT_URLS[protocol])
    auth_token = os.environ.get(_AUTH_TOKEN_ENV, STORYBOARD_TOKEN)
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
        "-H",
        _TENANT_ROUTING_HEADER,
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
    tls_env = _node_ca_env(agent_url)
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
        env={**os.environ, **webhook_env, **tls_env},
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


#: Per-protocol floor on the runner's own ``passed`` count, the one number no pytest
#: outcome can carry. See :func:`_below_pass_floor`.
_PASS_FLOOR_PATH = _REPO_ROOT / ".storyboard-pass-floor"


def _pass_floor(protocol: str) -> int:
    """The recorded floor for *protocol*, or 0 when none is recorded."""
    if not _PASS_FLOOR_PATH.is_file():
        return 0
    return int(json.loads(_PASS_FLOOR_PATH.read_text(encoding="utf-8")).get(protocol, 0))


def _below_pass_floor(protocol: str, summary: dict[str, Any]) -> dict[str, Any] | None:
    """One synthetic FAILING check when *protocol* graded fewer passes than the floor.

    THE HOLE THIS CLOSES. A parametrized item exists only for a FAILURE or a SKIP:
    ``_collect_checks`` builds them from ``summary["failures"]`` and
    ``summary["skip_causes"]``, because the runner publishes no per-check pass record,
    so a passing check has no id to carry. The item count therefore moves INVERSELY
    to health, and the pass count reaches pytest through nothing at all.

    So a check that regresses from PASS to SKIP makes the suite greener: its failing
    item never existed, its skip item does not fail, and the only trace is a number in
    a JSON artifact. 30 passes could become 5 with every item still xfail-or-skip and
    CI still green. ``agent_reachability`` catches only the total collapse to zero.

    Only reached when the runner graded SOMETHING: a run that graded nothing is
    ``agent_reachability``'s case, and reporting both would double-count one cause.

    ONE-DIRECTIONAL, deliberately. A surplus does not fail; it prints in the
    scoreboard so the floor gets raised on purpose. The mypy and duplication ratchets
    fail on slack because their counts are deterministic -- this one is a live run
    against a real stack, where one environment-dependent skip cause moves the number,
    and a ratchet that manufactures a red for GOOD news teaches people to edit the
    file without reading it.
    """
    # Only a session that actually DIALLED an agent has a pass count worth flooring.
    # The ledger-fitness module drives this same collection through a stub runner
    # (``stub://`` urls, a handful of synthetic checks) to grade the fitness join; a
    # floor of 30 against a stub that grades 1 is not a regression, it is a category
    # error, and it changed that module's expected outcome counts when this landed.
    if not str(summary.get("agent_url", "")).startswith("http"):
        return None
    floor = _pass_floor(protocol)
    passed = int(summary.get("passed", 0))
    if passed >= floor:
        return None
    return {
        "protocol": protocol,
        "track": "_runner",
        "storyboard_id": ledger.PASS_FLOOR_STORYBOARD_ID,
        "step_id": ledger.PASS_FLOOR_STEP_ID,
        "status": "fail",
        "reason": (
            f"graded {passed} passing checks against {summary.get('agent_url')}, "
            f"below the recorded floor of {floor}. Either a check regressed (look at "
            f"skip_causes: a pass that became a SKIP removes its own failing item and "
            f"shows up nowhere else), or the floor in {_PASS_FLOOR_PATH.name} is stale "
            f"and lowering it is a deliberate decision."
        ),
        "reason_kind": "below_pass_floor",
    }


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


def _publish_summary(protocol: str, summary: dict[str, Any]) -> None:
    """Copy the runner's summary into ``test-results/`` so it leaves the box.

    The runner writes it under ``tests/storyboard/runner/results/``, which is gitignored
    and outside the three paths a remote run pulls home (``test-results/``,
    ``coverage.json``, ``htmlcov/`` — cassini's ``results.py``). So the one artifact
    recording how many checks PASSED stayed on the runner box, and reading the score
    meant an ssh. Published beside ``storyboard_collected.json``, which already rides
    home this way.
    """
    dest = _REPO_ROOT / "test-results" / f"storyboard_summary_{protocol}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(summary, indent=2, sort_keys=True))


def _scoreboard(protocol: str, summary: dict[str, Any]) -> str:
    """The runner's own verdict for *protocol*, as one line.

    Printed because the pytest outcome line CANNOT carry it: only failures and skips
    become test items, so a protocol passing 33 checks and one passing none produce the
    same "0 passed" in the suite total. Every denominator is named together —
    passed/failed/skipped/not_selected plus how many storyboards were EXECUTED, which is
    what explains one axis grading fewer checks than its sibling.
    """
    return (
        f"storyboard[{protocol}] {summary.get('overall_status')}: "
        f"passed={summary.get('passed')} failed={summary.get('failed')} "
        f"skipped={summary.get('skipped')} not_selected={summary.get('not_selected_count')} "
        f"storyboards_executed={len(summary.get('storyboards_executed', []))} "
        f"floor={_pass_floor(protocol)}{_surplus_note(protocol, summary)} "
        f"agent_url={summary.get('agent_url')}"
    )


def _surplus_note(protocol: str, summary: dict[str, Any]) -> str:
    """`` (+N, raise the floor)`` when this run beat the recorded floor.

    The other half of the floor, and deliberately a PRINT rather than a failure: a
    surplus is good news, and a ratchet that reds the build for good news gets its
    file edited without being read. Named here so raising it is a decision someone
    makes on seeing the number, not a thing nobody knows to do.
    """
    surplus = int(summary.get("passed", 0)) - _pass_floor(protocol)
    return f" (+{surplus}, raise the floor)" if surplus > 0 else ""


def _collect_checks(protocol: str) -> list[dict[str, Any]]:
    """One entry per (protocol, track, storyboard_id, step_id): a failure or a skip.

    Passed checks are not enumerated individually — the runner's summary
    reports a pass/fail/skip count, not a per-check pass record — so a
    passing check has no ledger identity to track; only failures and skips
    are gradeable per-check here.
    """
    summary = _run_storyboard_runner(protocol)
    _publish_summary(protocol, summary)
    print(_scoreboard(protocol, summary))
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
        # The total collapse. Reported by ``agent_reachability`` alone: the floor below
        # would also fire here, and two failing checks for one cause reads as two
        # problems. The floor covers "graded FEWER than before", which only means
        # anything once the runner graded something at all.
        checks.append(_no_graded_checks(protocol, summary))
    elif (breach := _below_pass_floor(protocol, summary)) is not None:
        checks.append(breach)
    return checks


def _stale_ledger_entries(collected_ids: list[str]) -> list[str]:
    """Ledger entries with no corresponding collected check, in ledger order.

    The join the storyboard side never had. ``scripts.audit.ledger`` owns both
    the file and the id grammar, so this compares like with like rather than
    re-deriving either.
    """
    collected = set(collected_ids)
    entries = ledger.load(ledger.ledger_path(_REPO_ROOT))
    return [entry.format() for entry in entries if entry.format() not in collected]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "storyboard_check" not in metafunc.fixturenames:
        return
    gate = _bundle_gate()
    if gate is not None:
        gate_id, check = gate
        metafunc.parametrize("storyboard_check", [check], ids=[gate_id])
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

    # PUBLISH WHAT THIS SESSION COLLECTED. `checks` here is the exact set the run will
    # grade, which storyboard_check_index otherwise has to INFER from the failure
    # ledger (salesagent-v03pe.3). Stashed rather than written here because
    # pytest_generate_tests runs once per parametrized function, not once per session;
    # tests/storyboard/conftest.py writes it at sessionfinish. Deliberately set only on
    # this path -- the bundle-missing return above collects one synthetic skip, and
    # publishing that would claim a run that never started.
    setattr(metafunc.config, collected.STASH_ATTR, list(checks))

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
