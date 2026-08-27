"""Structural guard: the e2e webhook-capture origin is wired at all four sites.

salesagent-mp53.9 ports the long-lived HTTPS webhook-capture receiver into this
branch. Its acceptance is an e2e stack BEHAVIOUR (graded in-network by
``tests/e2e/test_webhook_capture_egress_gate_e2e.py``), but the wiring that
behaviour depends on is spread across four files that no in-process test reads,
and a half-wired stack fails at ``docker compose up`` — minutes into a full run,
with a message that names nginx or openssl rather than the missing line. This
guard reads the REAL files at ``make quality`` time instead.

The four sites, per OWNER DECISION 4 (2026-08-06):

1. ``SAN_DNS_NAMES`` in ``scripts/dev/gen_test_tls.py`` — the leaf must cover
   ``webhooks.adcp-e2e.dev``; ``*.adcp.test`` does not.
2. the compose network alias on the EXISTING ``tls-proxy`` service.
3. the nginx SNI map key in ``config/nginx/nginx-tls-test.conf.template``,
   extending the EXISTING front — never a second ``listen ... ssl`` server.
4. a NON-PRIVATE, PER-STACK-ALLOCATED subnet for the compose network, so the
   resolved address genuinely satisfies production's SSRF gate rather than
   being exempted from it.

Site 4's two halves are both load-bearing and pull in opposite directions:

* NON-PRIVATE is the acceptance itself — today every service sits on the
  compose default bridge (a 172.16/12 address) which ``check_url_ssrf``
  refuses, so the gate can only pass if the network moves off it.
* PER-STACK is a hard constraint measured on the box: ``docker network create
  --subnet 192.88.99.0/24`` succeeds once and the SECOND stack fails with
  "Pool overlaps with other one on this address space" — even for a /28 inside
  the held /24. This repo runs concurrent stacks BY DESIGN
  (``scripts/test-stack.sh`` sets ``COMPOSE_PROJECT_NAME="adcp-test-$$"``), so
  a hardcoded CIDR in the BASE compose file — the file the in-network runner
  uses alone — means the second stack on the box never comes up.

Deliberately NOT ported from the a2-fetch sibling
(``tests/unit/test_architecture_e2e_compose_tls_origins.py``): its
``CREATIVE_AGENT_URL``-is-https and "never resurrects ``ADCP_WEBHOOK_HOST``"
assertions. Both encode post-#1589 invariants that are false on this branch —
``ADCP_WEBHOOK_HOST`` is still live here (``docker-compose.e2e.yml``), and
retiring it is scoped to salesagent-og9k.8, not here. A guard that fails for a
reason its own ticket does not own is a guard people learn to ignore.
"""

from __future__ import annotations

import ipaddress
import re

import pytest

from src.core.security.url_validator import BLOCKED_NETWORKS
from tests.unit._architecture_helpers import (
    TLS_FRONT_SERVICE,
    format_failure,
    load_yaml,
    repo_root,
    san_covers,
    san_dns_names,
    sni_map_hostnames,
    tls_front_aliases,
)

#: The success-leg receiver's hostname (OWNER DECISION 4). An UNREGISTERED name
#: under a normal delegable gTLD: ``.dev`` is not an RFC 6761 special-use name,
#: so ``is_reserved_tld_host`` returns False for it on its own terms (measured),
#: unlike ``.internal`` / ``.local`` / ``.test`` which the AdCP 3.1.1 SSRF rules
#: name explicitly. It resolves ONLY inside the compose network, via the alias
#: below — real DNS is never consulted.
CAPTURE_HOSTNAME = "webhooks.adcp-e2e.dev"

#: ``TLS_FRONT_SERVICE`` — the service that must carry the alias and the SNI
#: route — is imported from ``_architecture_helpers`` alongside the four
#: detectors below: every in-stack HTTPS origin asks the same four questions of
#: the same front, so one home for them is what keeps two origin guards from
#: drifting into two answers.

_REPO_ROOT = repo_root()
_BASE_COMPOSE = _REPO_ROOT / "docker-compose.e2e.yml"
_PORTS_COMPOSE = _REPO_ROOT / "docker-compose.e2e.ports.yml"
_NGINX_TLS_TEMPLATE = _REPO_ROOT / "config" / "nginx" / "nginx-tls-test.conf.template"
_GEN_TLS_SCRIPT = _REPO_ROOT / "scripts" / "dev" / "gen_test_tls.py"

#: ``${NAME}`` or ``${NAME:-default}`` — compose's own interpolation syntax.
_COMPOSE_VAR_RE = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")

#: A TLS ``listen`` directive — one per terminating front.
_TLS_LISTEN_RE = re.compile(r"^\s*listen\s+[^;]*\bssl\b[^;]*;", re.MULTILINE)


# ---------------------------------------------------------------------------
# Detectors — each takes the parsed artifact, so every one is exercised twice:
# once against the real file, once against a synthetic known-bad input. The four
# shared with the counterparty-origin guard (``san_dns_names``, ``san_covers``,
# ``tls_front_aliases``, ``sni_map_hostnames``) live in
# ``tests/unit/_architecture_helpers.py``; their self-tests stay here.
# ---------------------------------------------------------------------------


def count_tls_fronts(template: str) -> int:
    """How many TLS-terminating ``listen`` directives the template declares."""
    return len(_TLS_LISTEN_RE.findall(template))


def declared_network_subnets(compose: dict) -> list[tuple[str, str]]:
    """``(network_name, raw subnet value)`` for every ipam subnet a compose file declares."""
    subnets: list[tuple[str, str]] = []
    for name, network in (compose.get("networks") or {}).items():
        if not isinstance(network, dict):
            continue
        for config in (network.get("ipam") or {}).get("config") or []:
            if isinstance(config, dict) and "subnet" in config:
                subnets.append((name, str(config["subnet"])))
    return subnets


def find_hardcoded_subnets(compose: dict) -> list[str]:
    """Return one message per subnet declared as a bare CIDR literal instead of a compose variable.

    A bare literal is the measured concurrency break: the second stack on the
    box fails to create its network. The value must come from a ``${VAR}`` the
    per-stack allocator sets.
    """
    return [
        f"networks.{name}.ipam.config.subnet is a hardcoded CIDR: {raw!r}"
        for name, raw in declared_network_subnets(compose)
        if "${" not in raw
    ]


def _literal_cidrs(raw: str) -> list[str]:
    """Every concrete CIDR in a subnet value: the bare literal, or a ``${VAR:-default}`` default."""
    if "${" not in raw:
        return [raw]
    return [m.group("default") for m in _COMPOSE_VAR_RE.finditer(raw) if m.group("default")]


def find_private_subnets(compose: dict) -> list[str]:
    """Return one message per declared subnet that production's own SSRF arithmetic would refuse.

    Graded against ``BLOCKED_NETWORKS`` itself rather than a re-listed copy of
    RFC1918/loopback/link-local/CGNAT — the whole acceptance is "the gate passes
    on its own terms", so the guard has to ask the gate's own data.
    """
    violations: list[str] = []
    for name, raw in declared_network_subnets(compose):
        for cidr in _literal_cidrs(raw):
            try:
                network = ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                violations.append(f"networks.{name} subnet {cidr!r} is not a valid CIDR")
                continue
            if network.is_private or network.is_loopback or network.is_link_local:
                violations.append(f"networks.{name} subnet {cidr} is private/loopback/link-local")
                continue
            blocking = [str(b) for b in BLOCKED_NETWORKS if network.overlaps(b)]
            if blocking:
                violations.append(f"networks.{name} subnet {cidr} overlaps BLOCKED_NETWORKS {blocking}")
    return violations


def _merged_compose() -> dict:
    """Base + per-stack ports overlay, for checks that do not care which file declares a thing."""
    merged = load_yaml(_BASE_COMPOSE)
    overlay = load_yaml(_PORTS_COMPOSE)
    merged_networks = {**(merged.get("networks") or {}), **(overlay.get("networks") or {})}
    if merged_networks:
        merged["networks"] = merged_networks
    return merged


# ---------------------------------------------------------------------------
# Site 1 — the certificate covers the name
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_test_certificate_covers_the_webhook_capture_hostname() -> None:
    """The generated leaf's SANs cover ``webhooks.adcp-e2e.dev``.

    ``*.adcp.test`` does not reach it, and TLS validates by NAME — without a SAN
    entry the delivery leg dies at certificate verification and reads as a
    network error rather than a missing config line.
    """
    names = san_dns_names(_GEN_TLS_SCRIPT.read_text(encoding="utf-8"))

    assert san_covers(names, CAPTURE_HOSTNAME), format_failure(
        summary=f"{CAPTURE_HOSTNAME} is not covered by SAN_DNS_NAMES",
        violations=[f"scripts/dev/gen_test_tls.py: SAN_DNS_NAMES = {names}"],
        fix_hint=(
            f"Add {CAPTURE_HOSTNAME} (or a wildcard covering it) to SAN_DNS_NAMES, and regenerate "
            "BEFORE any stack starts — _is_current() compares served SANs for EXACT equality, so a "
            "forced regeneration mid-run replaces the leaf a live front is serving."
        ),
    )


# ---------------------------------------------------------------------------
# Site 2 — the alias is on the EXISTING front
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_webhook_capture_hostname_is_an_alias_on_the_existing_tls_front() -> None:
    """``webhooks.adcp-e2e.dev`` resolves to the shared ``tls-proxy``, not a second terminator."""
    aliases = tls_front_aliases(load_yaml(_BASE_COMPOSE))

    assert CAPTURE_HOSTNAME in aliases, format_failure(
        summary=f"{CAPTURE_HOSTNAME} is not a network alias on the {TLS_FRONT_SERVICE} service",
        violations=[f"docker-compose.e2e.yml: services.{TLS_FRONT_SERVICE}.networks.default.aliases = {aliases}"],
        fix_hint=(
            f"Add {CAPTURE_HOSTNAME} to the EXISTING {TLS_FRONT_SERVICE} aliases (alongside "
            "proxy.adcp.test). A second TLS service would mean a second certificate and a second "
            "place to keep in step."
        ),
    )


# ---------------------------------------------------------------------------
# Site 3 — the SNI map routes it, on one front
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_nginx_sni_map_routes_the_webhook_capture_hostname() -> None:
    """The TLS template routes the capture hostname to the capture service by SNI."""
    template = _NGINX_TLS_TEMPLATE.read_text(encoding="utf-8")
    routed = sni_map_hostnames(template)

    assert CAPTURE_HOSTNAME in routed, format_failure(
        summary=f"{CAPTURE_HOSTNAME} has no route in the nginx SNI map",
        violations=[f"config/nginx/nginx-tls-test.conf.template: map $ssl_server_name keys = {routed}"],
        fix_hint=(
            "Add a `map $ssl_server_name` entry pointing the capture hostname at the capture "
            "service's plaintext port. The front terminates TLS; the capture service never does."
        ),
    )


@pytest.mark.arch_guard
def test_tls_template_declares_exactly_one_terminating_front() -> None:
    """One terminator, one certificate. A second ``listen ... ssl`` is the regression."""
    fronts = count_tls_fronts(_NGINX_TLS_TEMPLATE.read_text(encoding="utf-8"))

    assert fronts == 1, format_failure(
        summary="config/nginx/nginx-tls-test.conf.template declares more than one TLS front",
        violations=[f"{fronts} `listen ... ssl` directives found"],
        fix_hint="Route new origins through the existing front's SNI map, never a second server block.",
    )


# ---------------------------------------------------------------------------
# Site 4 — a non-private, per-stack subnet
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_e2e_network_declares_a_subnet() -> None:
    """The e2e compose network declares its own subnet instead of taking the default bridge.

    The default bridge is 172.16/12 — squarely inside ``BLOCKED_NETWORKS``, so
    every in-stack origin is refused by ``check_url_ssrf`` no matter what it is
    called. Declaring the network is what makes the gate passable on its own
    terms rather than by exemption.
    """
    subnets = declared_network_subnets(_merged_compose())

    assert subnets, format_failure(
        summary="the e2e compose network declares no ipam subnet",
        violations=["docker-compose.e2e.yml / docker-compose.e2e.ports.yml: no networks.*.ipam.config.subnet"],
        fix_hint=(
            "Declare the network with a per-stack-allocated, non-private subnet. Without it every "
            "service keeps a 172.16/12 default-bridge address, which production's SSRF gate refuses."
        ),
    )


@pytest.mark.arch_guard
def test_e2e_network_subnet_is_non_private() -> None:
    """Every declared subnet sits outside the ranges production's own gate blocks."""
    violations = find_private_subnets(_merged_compose())

    assert not violations, format_failure(
        summary="the e2e compose network subnet is one production's SSRF gate refuses",
        violations=violations,
        fix_hint=(
            "Pick a range outside RFC1918/loopback/link-local/CGNAT/multicast. 192.88.99.0/24 "
            "(RFC 7526-deprecated 6to4 relay anycast) is the least-bad choice — squatting "
            "deprecated global space, confined to a container netns."
        ),
    )


@pytest.mark.arch_guard
def test_base_compose_never_hardcodes_the_network_subnet() -> None:
    """The BASE compose file templates its subnet — a literal there breaks concurrent stacks.

    Measured: ``docker network create --subnet 192.88.99.0/24`` succeeds once;
    the second stack fails with "Pool overlaps with other one on this address
    space", and even a /28 inside the held /24 is refused. The base file is the
    one the in-network runner uses alone, and this repo runs stacks concurrently
    by design (``COMPOSE_PROJECT_NAME="adcp-test-$$"``).
    """
    violations = find_hardcoded_subnets(load_yaml(_BASE_COMPOSE))

    assert not violations, format_failure(
        summary="docker-compose.e2e.yml hardcodes a network subnet",
        violations=violations,
        fix_hint=(
            "Template it (${E2E_NETWORK_SUBNET}) and let the per-stack allocator hand out a "
            "DISJOINT slice, the same way host ports are allocated. Verified coexisting: "
            "192.88.99.0/28 and 192.88.99.16/28."
        ),
    )


# ---------------------------------------------------------------------------
# Detector self-tests — a guard that cannot fail grades nothing
# ---------------------------------------------------------------------------


def test_san_detector_reads_a_synthetic_declaration() -> None:
    source = 'SAN_DNS_NAMES = (\n    "adcp.test",\n    "*.adcp.test",\n)\n'

    assert san_dns_names(source) == ("adcp.test", "*.adcp.test")


@pytest.mark.parametrize(
    ("sans", "hostname", "covered"),
    [
        (("webhooks.adcp-e2e.dev",), "webhooks.adcp-e2e.dev", True),
        (("*.adcp-e2e.dev",), "webhooks.adcp-e2e.dev", True),
        (("*.adcp.test", "localhost"), "webhooks.adcp-e2e.dev", False),
        (("*.adcp-e2e.dev",), "a.b.adcp-e2e.dev", False),
    ],
)
def test_san_coverage_matches_rfc6125_wildcard_rules(sans, hostname, covered) -> None:
    assert san_covers(sans, hostname) is covered


def test_alias_detector_reports_a_front_missing_the_capture_alias() -> None:
    synthetic = {"services": {TLS_FRONT_SERVICE: {"networks": {"default": {"aliases": ["proxy.adcp.test"]}}}}}

    assert tls_front_aliases(synthetic) == ["proxy.adcp.test"]


def test_sni_map_detector_reads_keys_and_ignores_the_default_row() -> None:
    synthetic = (
        "map $ssl_server_name $tls_upstream_named {\n"
        '    default                 "";\n'
        "    proxy.adcp.test         adcp-server:8080;\n"
        "    webhooks.adcp-e2e.dev   webhook-capture:8080;  # the capture origin\n"
        "}\n"
    )

    assert sni_map_hostnames(synthetic) == ["proxy.adcp.test", "webhooks.adcp-e2e.dev"]


def test_front_counter_reports_a_second_terminator() -> None:
    synthetic = "server {\n    listen 8443 ssl;\n}\nserver {\n    listen 9443 ssl http2;\n}\n"

    assert count_tls_fronts(synthetic) == 2


def test_hardcoded_subnet_detector_reports_a_literal_and_accepts_a_template() -> None:
    literal = {"networks": {"default": {"ipam": {"config": [{"subnet": "192.88.99.0/24"}]}}}}
    templated = {"networks": {"default": {"ipam": {"config": [{"subnet": "${E2E_NETWORK_SUBNET:-192.88.99.0/28}"}]}}}}

    assert find_hardcoded_subnets(literal) == [
        "networks.default.ipam.config.subnet is a hardcoded CIDR: '192.88.99.0/24'"
    ]
    assert find_hardcoded_subnets(templated) == []


def test_private_subnet_detector_reports_the_default_bridge_range() -> None:
    bridge = {"networks": {"default": {"ipam": {"config": [{"subnet": "172.18.0.0/16"}]}}}}

    assert find_private_subnets(bridge) == ["networks.default subnet 172.18.0.0/16 is private/loopback/link-local"]


def test_private_subnet_detector_reports_cgnat_which_is_non_private_but_blocked() -> None:
    """CGNAT is ``is_private=False`` yet explicitly in ``BLOCKED_NETWORKS`` — a naive check misses it."""
    cgnat = {"networks": {"default": {"ipam": {"config": [{"subnet": "${E2E_NETWORK_SUBNET:-100.64.0.0/28}"}]}}}}

    assert find_private_subnets(cgnat) == [
        "networks.default subnet 100.64.0.0/28 overlaps BLOCKED_NETWORKS ['100.64.0.0/10']"
    ]


def test_private_subnet_detector_accepts_the_chosen_range() -> None:
    chosen = {"networks": {"default": {"ipam": {"config": [{"subnet": "${E2E_NETWORK_SUBNET:-192.88.99.0/28}"}]}}}}

    assert find_private_subnets(chosen) == []


# ---------------------------------------------------------------------------
# Site 4b — the template must have an ALLOCATOR behind it
# ---------------------------------------------------------------------------

#: Every entry point that runs `docker compose up` on the e2e stack. Each must
#: set E2E_NETWORK_SUBNET first, or the compose default applies and the second
#: concurrent stack dies on network creation.
_STACK_ENTRY_POINTS = ("run_all_tests.sh", "scripts/test-stack.sh")

_SUBNET_ALLOCATOR = "scripts/dev/alloc-e2e-subnet.sh"


@pytest.mark.arch_guard
def test_a_subnet_allocator_exists_and_is_executable() -> None:
    """Templating the subnet without shipping an allocator just moves the collision.

    This is the gap that let a broken change through once already: the
    hardcoded-CIDR guard above is satisfied by ANY ``${VAR}``, including one
    whose default every stack shares. A template with no allocator behind it is
    a fixed subnet wearing a variable's clothes.
    """
    allocator = repo_root() / _SUBNET_ALLOCATOR

    assert allocator.is_file(), format_failure(
        summary=f"{_SUBNET_ALLOCATOR} does not exist",
        violations=[f"{_SUBNET_ALLOCATOR}: missing"],
        fix_hint=(
            "docker-compose.e2e.yml templates ${E2E_NETWORK_SUBNET}. Something must hand each "
            "stack a DISJOINT slice, the same way host ports are allocated, or every stack falls "
            "back to the shared compose default."
        ),
    )
    assert allocator.stat().st_mode & 0o111, format_failure(
        summary=f"{_SUBNET_ALLOCATOR} is not executable",
        violations=[f"{_SUBNET_ALLOCATOR}: mode {allocator.stat().st_mode & 0o777:o}"],
        fix_hint="chmod +x it — the entry points invoke it directly.",
    )


@pytest.mark.arch_guard
@pytest.mark.parametrize("entry_point", _STACK_ENTRY_POINTS)
def test_every_stack_entry_point_allocates_a_subnet(entry_point: str) -> None:
    """Each script that brings the e2e stack up sets E2E_NETWORK_SUBNET before it does.

    Measured failure when it does not: the second stack on the box fails with
    "Pool overlaps with other one on this address space" and the run dies
    seconds in, before a single suite reports.
    """
    source = (repo_root() / entry_point).read_text(encoding="utf-8")

    assert _SUBNET_ALLOCATOR in source, format_failure(
        summary=f"{entry_point} brings the e2e stack up without allocating a network slice",
        violations=[f"{entry_point}: no reference to {_SUBNET_ALLOCATOR}"],
        fix_hint=(
            "Export E2E_NETWORK_SUBNET from the allocator before `docker compose up`, guarded by "
            '`if [ -z "${E2E_NETWORK_SUBNET:-}" ]` so an explicit caller value still wins.'
        ),
    )


@pytest.mark.arch_guard
def test_the_subnet_reaches_pytest_through_both_hops() -> None:
    """``E2E_NETWORK_SUBNET`` is declared in the runner's env AND in tox's pass_env.

    Two hops, and closing only one leaves the value set on the host, set in the
    container, and still invisible to the test — which then cannot tell "never
    allocated" from "not passed through". That is not hypothetical: this guard
    exists because exactly that happened, twice in a row, and the second time
    the compose half was already fixed. ``tox.ini`` already carries the same
    warning for ``E2E_TLS_BASE_URL``/``E2E_CA_BUNDLE``.
    """
    subnet_env_var = "E2E_NETWORK_SUBNET"
    compose = load_yaml(_BASE_COMPOSE)
    runner_env = (compose.get("services", {}).get("tests", {}) or {}).get("environment", {}) or {}
    tox_ini = (repo_root() / "tox.ini").read_text(encoding="utf-8")

    violations = []
    if subnet_env_var not in runner_env:
        violations.append(f"docker-compose.e2e.yml: services.tests.environment has no {subnet_env_var}")
    if not re.search(rf"^\s*{re.escape(subnet_env_var)}\s*$", tox_ini, re.MULTILINE):
        violations.append(f"tox.ini: {subnet_env_var} is not listed in pass_env")

    assert not violations, format_failure(
        summary=f"{subnet_env_var} does not reach pytest through both hops",
        violations=violations,
        fix_hint=(
            "Declare it in the tests service's `environment:` AND in tox.ini's `pass_env`. "
            "Either alone is a silent no-op at the test."
        ),
    )
