"""Structural guard: every SNI-routed origin is actually STARTED by the runner.

An origin service can be wired perfectly — compose service, network alias, nginx
``$ssl_server_name`` route, certificate SAN — and still be absent at runtime,
because ``run_all_tests.sh`` brings services up by an EXPLICIT list. A service
missing from that list is not a compose error and not a startup error: nginx
resolves the SNI route, finds nothing listening, and answers **502**. That 502
surfaces wherever the test happens to touch it, which for a signing walk is
three hops downstream — as an unresolvable key, a ``capabilities_unreachable``,
or a signature that "failed to verify".

This is not hypothetical. salesagent-mp53.9 added the ``webhook-capture`` service
and did not add it here; nothing in-network dialled it (its egress test grades
DNS and gate arithmetic, its contract test runs the service in-process), so the
omission stayed invisible. salesagent-mp53.8's counterparty walk was the first leg
to actually need an origin up, and it failed on exactly that 502 — after a full
20-minute suite, with a message about the JWKS install rather than about a
service that was never started.

The pairing this guard pins is narrow and mechanical: if the TLS front routes a
hostname to ``<service>:<port>`` by SNI, then ``<service>`` is an origin the
server can dial, and the runner must start it.
"""

from __future__ import annotations

import builtins
import dis
import importlib
import re
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import format_failure, load_yaml, repo_root

_REPO_ROOT = repo_root()
_BASE_COMPOSE = _REPO_ROOT / "docker-compose.e2e.yml"
_NGINX_TLS_TEMPLATE = _REPO_ROOT / "config" / "nginx" / "nginx-tls-test.conf.template"
_RUNNER = _REPO_ROOT / "run_all_tests.sh"

#: The line that brings the in-network stack up. Matched rather than parsed: the
#: runner is a shell script, and the service list is the one thing here that has
#: to stay in step with compose.
_UP_LINE_RE = re.compile(r"^dc up -d (?P<services>.+)$", re.MULTILINE)

#: An SNI map row's upstream, e.g. ``webhook-capture:8080`` -> ``webhook-capture``.
_SNI_UPSTREAM_RE = re.compile(r"^\s*(?P<host>[\w.-]+)\s+(?P<service>[\w-]+):(?P<port>\d+)\s*;", re.MULTILINE)


def sni_routed_services(template: str) -> dict[str, str]:
    """``{hostname: upstream service}`` for every host the SNI map routes."""
    routed = {}
    for match in _SNI_UPSTREAM_RE.finditer(template):
        if match.group("host") not in {"default", "hostnames", "volatile"}:
            routed[match.group("host")] = match.group("service")
    return routed


def runner_started_services(source: str) -> list[str]:
    """The services ``run_all_tests.sh`` brings up, in declaration order."""
    match = _UP_LINE_RE.search(source)
    return match.group("services").split() if match else []


@pytest.mark.arch_guard
def test_every_sni_routed_origin_is_started_by_the_runner() -> None:
    """A hostname the TLS front routes must reach a service the runner started."""
    routed = sni_routed_services(_NGINX_TLS_TEMPLATE.read_text(encoding="utf-8"))
    started = set(runner_started_services(_RUNNER.read_text(encoding="utf-8")))

    violations = [
        f"config/nginx/nginx-tls-test.conf.template routes {host} -> {service}, "
        f"but run_all_tests.sh never starts {service}"
        for host, service in sorted(routed.items())
        if service not in started
    ]

    assert not violations, format_failure(
        summary="an SNI-routed origin is never started in-network",
        violations=violations,
        fix_hint=(
            "Add the service to the `dc up -d` list in run_all_tests.sh. Without it nginx answers "
            "502 for that hostname, which surfaces as an unresolvable key or an unreachable "
            "capabilities document rather than as a missing service."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_every_started_service_actually_exists_in_compose() -> None:
    """The runner never names a service compose does not define.

    The other direction of the same drift: a typo or a renamed service makes
    ``docker compose up`` fail late, after the image build, rather than at
    ``make quality``.
    """
    defined = set((load_yaml(_BASE_COMPOSE).get("services") or {}).keys())
    started = runner_started_services(_RUNNER.read_text(encoding="utf-8"))

    violations = [
        f"run_all_tests.sh starts {name}, which docker-compose.e2e.yml does not define"
        for name in started
        if name not in defined
    ]

    assert not violations, format_failure(
        summary="run_all_tests.sh starts a service that does not exist",
        violations=violations,
        fix_hint="Fix the name, or add the service to docker-compose.e2e.yml.",
    )


def test_sni_upstream_detector_reads_a_synthetic_map() -> None:
    synthetic = (
        "map $ssl_server_name $tls_upstream_named {\n"
        '    default                 "";\n'
        "    proxy.adcp.test         adcp-server:8080;\n"
        "    webhooks.adcp-e2e.dev   webhook-capture:8080;  # comment\n"
        "}\n"
    )

    assert sni_routed_services(synthetic) == {
        "proxy.adcp.test": "adcp-server",
        "webhooks.adcp-e2e.dev": "webhook-capture",
    }


def test_runner_service_detector_reads_the_up_line() -> None:
    assert runner_started_services("set -e\ndc up -d postgres adcp-server tls-proxy\necho done\n") == [
        "postgres",
        "adcp-server",
        "tls-proxy",
    ]


def test_the_guard_catches_a_routed_service_the_runner_forgets(tmp_path: Path) -> None:
    """The detector pair reports the exact regression this guard exists for."""
    routed = sni_routed_services("map $ssl_server_name $u {\n    x.adcp.test  lonely-origin:8080;\n}\n")
    started = set(runner_started_services("dc up -d postgres adcp-server\n"))

    assert [s for s in routed.values() if s not in started] == ["lonely-origin"]


#: Modules that a compose service runs via ``python -m``. Each must not merely
#: IMPORT cleanly — it must survive the call the container actually makes.
_ORIGIN_MODULES = ("tests.e2e.webhook_capture_service", "tests.e2e.counterparty_origin_service")


@pytest.mark.arch_guard
@pytest.mark.parametrize("module", _ORIGIN_MODULES)
def test_every_origin_module_defines_every_name_its_entry_point_uses(module: str) -> None:
    """``main()`` resolves every global it touches — not just the import block.

    Importing a module proves nothing about ``python -m <module>``: the entry
    point's body does not execute at import time, so a name it uses that was
    never imported is invisible until the container starts and dies. That is not
    hypothetical — a refactor removed ``import os`` from the capture service
    while ``main()`` still read ``os.environ``; the module imported fine, the
    stdlib-only guard passed, the container exited with a NameError, and the
    failure surfaced a full suite later as "webhook-capture is not answering /
    Temporary failure in name resolution" — a DNS-shaped message for a missing
    import.

    Compiling the module and resolving ``main``'s global loads against the
    module namespace plus builtins catches it in-process, in milliseconds.
    """
    imported = importlib.import_module(module)
    main = getattr(imported, "main", None)

    assert main is not None, format_failure(
        summary=f"{module} has no main() but is run as a compose entry point",
        violations=[f"{module}: no main"],
        fix_hint="A service run via `python -m` needs a main() the container can call.",
    )

    unresolved = sorted(
        name
        for instruction in dis.get_instructions(main)
        if instruction.opname == "LOAD_GLOBAL"
        for name in [instruction.argval]
        if not hasattr(imported, name) and not hasattr(builtins, name)
    )

    assert not unresolved, format_failure(
        summary=f"{module}.main() uses names the module never defines",
        violations=[f"{module}.main: {name} is not defined or imported" for name in unresolved],
        fix_hint=(
            "Import it. The module still IMPORTS cleanly without it — only `python -m` fails, "
            "and it fails as an unreachable service rather than as a missing name."
        ),
        docs_link="docs/development/structural-guards.md",
    )
