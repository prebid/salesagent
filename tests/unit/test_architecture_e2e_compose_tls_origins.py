"""Structural guard: one TLS terminator, every dialed origin https, one set of TLS material.

GH #1802 gave the creative-agent origin in ``docker-compose.e2e.yml``
a real TLS front specifically so ``ADCP_OUTBOUND_ALLOW_INSECURE`` could
eventually close for it (GH #1802). GH #1802/.3/.4 then
collapsed every outbound-only origin (creative-agent, webhook-capture) onto
ONE shared ``tls-proxy`` front, one ``*.adcp.test`` alias per origin, one
generated CA/leaf. GH #1802 generalizes what had been two
origin-specific checks (a CREATIVE_AGENT_URL-is-https check, and an
ADCP_WEBHOOK_HOST-never-resurfaces check) into the structural invariant that
made those checks correct in the first place, so a FUTURE origin (not just
the two named here) is covered without anyone remembering to add a new
env-var-specific check for it:

1. exactly ONE TLS-terminating service in the shared stack
2. every ``*.adcp.test`` origin any service dials is ``https://...:8443``
3. no TLS material lives anywhere but ``.test-tls/``
4. ``ADCP_OUTBOUND_ALLOW_INSECURE`` (the deleted escape hatch) never returns,
   in ``src/`` or any compose file

Each has a live detector proven against a synthetic regression — a detector
that cannot go red is not a guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_FILE = _REPO_ROOT / "docker-compose.e2e.yml"
_TLS_TEMPLATE_MOUNT = "config/nginx/nginx-tls-test.conf.template"

# Matches an http(s) URL fragment ending in an .adcp.test host, wherever it
# appears in a value — including inside a compose ${VAR:-default} wrapper
# (e.g. "${E2E_TLS_BASE_URL:-https://proxy.adcp.test:8443}"), so the scan
# does not need to special-case that shell-interpolation syntax.
_ADCP_TEST_URL_RE = re.compile(r"""https?://[^\s"'}]*\.adcp\.test(?::\d+)?""")


def find_multiple_tls_terminators(compose: dict) -> list[str]:
    """Return one message per EXTRA service that mounts the TLS-terminating nginx template.

    Exactly one service may mount ``nginx-tls-test.conf.template`` — the
    template that runs an ``ssl_certificate`` server block. A second service
    mounting it is the tls-proxy-creative disease GH #1802 removed,
    reappearing under a different name.
    """
    terminators = [
        name
        for name, service in compose.get("services", {}).items()
        if any(_TLS_TEMPLATE_MOUNT in str(v) for v in (service.get("volumes") or []))
    ]
    if len(terminators) <= 1:
        return []
    return [f"multiple TLS-terminating services found: {sorted(terminators)} (expected exactly 1)"]


def find_non_https_adcp_test_origins(compose: dict) -> list[str]:
    """Return one message per service env value naming a non-https or non-8443 .adcp.test origin.

    Generalizes the old CREATIVE_AGENT_URL-only check: ANY env var whose value
    references a ``*.adcp.test`` alias (the shared front's namespace) must use
    it as ``https://...:8443``, not just the two origins named when this guard
    was written. A plain internal reference with no ``.adcp.test`` in it (e.g.
    ``ADCP_SERVER_URL: http://proxy:8000``, the intentionally-plaintext
    listener) is out of scope by construction — it never matches the pattern.
    """
    violations: list[str] = []
    for service_name, service in compose.get("services", {}).items():
        env = service.get("environment") or {}
        if not isinstance(env, dict):
            continue
        for key, value in env.items():
            if value is None:
                continue
            for match in _ADCP_TEST_URL_RE.finditer(str(value)):
                url = match.group(0)
                if not url.startswith("https://") or ":8443" not in url:
                    violations.append(f"{service_name}.{key} has a non-https/non-8443 .adcp.test origin: {url!r}")
    return violations


def find_dead_webhook_env_var(compose: dict) -> list[str]:
    """Return one message per service that resurrects the deleted ``ADCP_WEBHOOK_HOST`` mechanism.

    GH #1802 deleted this var entirely — the webhook-capture service
    is reachable at the fixed ``webhooks.adcp.test`` alias, never an
    env-configurable hostname. Its reappearance means a future change
    resurrected the per-launcher host-configuration disease this ticket removed.
    """
    violations: list[str] = []
    for service_name, service in compose.get("services", {}).items():
        env = service.get("environment") or {}
        if not isinstance(env, dict):
            continue
        if "ADCP_WEBHOOK_HOST" in env:
            violations.append(f"{service_name}.ADCP_WEBHOOK_HOST resurrected: {env['ADCP_WEBHOOK_HOST']!r}")
    return violations


def find_tls_material_outside_test_tls(compose: dict) -> list[str]:
    """Return one message per env/volume value naming cert material outside ``.test-tls/``.

    Every service that needs TLS material reaches it through the ONE
    generator (``scripts/dev/gen_test_tls.py``), which writes under
    ``.test-tls/``. A ``.pem``/``.key`` path that does not mention
    ``.test-tls`` is a second, parallel TLS mechanism — exactly the disease
    the in-process webhook-capture front was before GH #1802.
    """
    violations: list[str] = []
    for service_name, service in compose.get("services", {}).items():
        env = service.get("environment") or {}
        values: list[str] = [str(v) for v in env.values()] if isinstance(env, dict) else []
        values += [str(v) for v in (service.get("volumes") or [])]
        for value in values:
            if (".pem" in value or ".key" in value) and ".test-tls" not in value:
                violations.append(f"{service_name} names cert material outside .test-tls/: {value!r}")
    return violations


def find_resurrected_allow_insecure_in_compose(compose: dict) -> list[str]:
    """Return one message per service that re-adds the deleted ``ADCP_OUTBOUND_ALLOW_INSECURE`` hatch."""
    violations: list[str] = []
    for service_name, service in compose.get("services", {}).items():
        env = service.get("environment") or {}
        if isinstance(env, dict) and "ADCP_OUTBOUND_ALLOW_INSECURE" in env:
            violations.append(
                f"{service_name}.ADCP_OUTBOUND_ALLOW_INSECURE resurrected: {env['ADCP_OUTBOUND_ALLOW_INSECURE']!r}"
            )
    return violations


def find_resurrected_allow_insecure_in_src(src_root: Path) -> list[str]:
    """Return one message per ``src/`` file referencing the deleted ``ADCP_OUTBOUND_ALLOW_INSECURE`` hatch.

    GH #1802 deleted this flag from ``src/`` entirely once every
    outbound origin the server dials became TLS-fronted. Its reappearance —
    even as a bare string, since the only legitimate use was ``os.getenv``/
    ``os.environ`` lookups — means the escape hatch came back.
    """
    violations: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        if "ADCP_OUTBOUND_ALLOW_INSECURE" in path.read_text():
            violations.append(f"{path.relative_to(src_root.parent)} references ADCP_OUTBOUND_ALLOW_INSECURE")
    return violations


def test_e2e_compose_exactly_one_tls_terminator() -> None:
    """The real docker-compose.e2e.yml has exactly one TLS-terminating service."""
    compose = yaml.safe_load(_COMPOSE_FILE.read_text())
    assert find_multiple_tls_terminators(compose) == []


def test_e2e_compose_adcp_test_origins_stay_https_8443() -> None:
    """No service in the real docker-compose.e2e.yml dials a .adcp.test origin over plain http or a stray port."""
    compose = yaml.safe_load(_COMPOSE_FILE.read_text())
    assert find_non_https_adcp_test_origins(compose) == []


def test_e2e_compose_never_resurrects_adcp_webhook_host() -> None:
    """The real docker-compose.e2e.yml never re-adds ADCP_WEBHOOK_HOST to any service."""
    compose = yaml.safe_load(_COMPOSE_FILE.read_text())
    assert find_dead_webhook_env_var(compose) == []


def test_e2e_compose_tls_material_stays_under_test_tls() -> None:
    """No service in the real docker-compose.e2e.yml names cert material outside .test-tls/."""
    compose = yaml.safe_load(_COMPOSE_FILE.read_text())
    assert find_tls_material_outside_test_tls(compose) == []


def test_e2e_compose_never_resurrects_allow_insecure() -> None:
    """The real docker-compose.e2e.yml never re-adds ADCP_OUTBOUND_ALLOW_INSECURE to any service."""
    compose = yaml.safe_load(_COMPOSE_FILE.read_text())
    assert find_resurrected_allow_insecure_in_compose(compose) == []


def test_src_never_resurrects_allow_insecure() -> None:
    """No file under src/ references ADCP_OUTBOUND_ALLOW_INSECURE."""
    assert find_resurrected_allow_insecure_in_src(_REPO_ROOT / "src") == []


def test_detector_catches_a_second_tls_terminator() -> None:
    """The live detector reports a synthetic second service mounting the TLS template."""
    synthetic = {
        "services": {
            "tls-proxy": {"volumes": ["./config/nginx/nginx-tls-test.conf.template:/etc/nginx/templates/x:ro"]},
            "tls-proxy-creative": {
                "volumes": ["./config/nginx/nginx-tls-test.conf.template:/etc/nginx/templates/x:ro"]
            },
        }
    }
    assert find_multiple_tls_terminators(synthetic) == [
        "multiple TLS-terminating services found: ['tls-proxy', 'tls-proxy-creative'] (expected exactly 1)"
    ]


def test_detector_ignores_a_single_tls_terminator() -> None:
    """The live detector does not flag exactly one service mounting the TLS template."""
    synthetic = {
        "services": {
            "tls-proxy": {"volumes": ["./config/nginx/nginx-tls-test.conf.template:/etc/nginx/templates/x:ro"]},
            "adcp-server": {"volumes": [".:/app"]},
        }
    }
    assert find_multiple_tls_terminators(synthetic) == []


def test_detector_catches_a_reverted_adcp_test_url() -> None:
    """The live detector reports a synthetic .adcp.test origin reverted to plain http."""
    synthetic = {
        "services": {
            "adcp-server": {"environment": {"CREATIVE_AGENT_URL": "http://creative-agent.adcp.test:8443/api"}},
        }
    }
    assert find_non_https_adcp_test_origins(synthetic) == [
        "adcp-server.CREATIVE_AGENT_URL has a non-https/non-8443 .adcp.test origin: "
        "'http://creative-agent.adcp.test:8443'"
    ]


def test_detector_catches_a_reverted_adcp_test_url_inside_a_default_wrapper() -> None:
    """The live detector unwraps a compose ${VAR:-default} shell-interpolation wrapper."""
    synthetic = {
        "services": {
            "tls-proxy": {"environment": {"E2E_TLS_BASE_URL": "${E2E_TLS_BASE_URL:-http://proxy.adcp.test:8443}"}},
        }
    }
    assert find_non_https_adcp_test_origins(synthetic) == [
        "tls-proxy.E2E_TLS_BASE_URL has a non-https/non-8443 .adcp.test origin: 'http://proxy.adcp.test:8443'"
    ]


def test_detector_catches_a_resurrected_adcp_webhook_host() -> None:
    """The live detector reports a synthetic ADCP_WEBHOOK_HOST resurrection, regardless of its value."""
    synthetic = {
        "services": {
            "tests": {"environment": {"ADCP_WEBHOOK_HOST": "tests.adcp.test"}},
        }
    }
    assert find_dead_webhook_env_var(synthetic) == ["tests.ADCP_WEBHOOK_HOST resurrected: 'tests.adcp.test'"]


def test_detector_catches_tls_material_outside_test_tls() -> None:
    """The live detector reports a synthetic cert path living outside .test-tls/."""
    synthetic = {
        "services": {
            "tls-proxy": {"environment": {}, "volumes": ["./other-tls/server.pem:/app/other-tls/server.pem:ro"]},
        }
    }
    assert find_tls_material_outside_test_tls(synthetic) == [
        "tls-proxy names cert material outside .test-tls/: './other-tls/server.pem:/app/other-tls/server.pem:ro'"
    ]


def test_detector_catches_a_resurrected_allow_insecure_in_compose() -> None:
    """The live detector reports a synthetic ADCP_OUTBOUND_ALLOW_INSECURE resurrection."""
    synthetic = {"services": {"adcp-server": {"environment": {"ADCP_OUTBOUND_ALLOW_INSECURE": "true"}}}}
    assert find_resurrected_allow_insecure_in_compose(synthetic) == [
        "adcp-server.ADCP_OUTBOUND_ALLOW_INSECURE resurrected: 'true'"
    ]


def test_detector_catches_a_resurrected_allow_insecure_in_src(tmp_path) -> None:
    """The live detector reports a synthetic src/ file referencing ADCP_OUTBOUND_ALLOW_INSECURE."""
    offender = tmp_path / "outbound_http.py"
    offender.write_text('ALLOW_INSECURE = os.getenv("ADCP_OUTBOUND_ALLOW_INSECURE")\n')
    assert find_resurrected_allow_insecure_in_src(tmp_path) == [
        f"{tmp_path.name}/outbound_http.py references ADCP_OUTBOUND_ALLOW_INSECURE"
    ]


def test_detector_ignores_unrelated_env_vars() -> None:
    """Unrelated service env vars (e.g. a plain-http health-check literal, or a plain internal URL) are not flagged."""
    synthetic = {
        "services": {
            "proxy": {"environment": {"SOME_OTHER_URL": "http://localhost:8080/health"}},
            "adcp-server": {"environment": {"ADCP_SERVER_URL": "http://proxy:8000"}},
        }
    }
    assert find_non_https_adcp_test_origins(synthetic) == []
    assert find_dead_webhook_env_var(synthetic) == []
    assert find_tls_material_outside_test_tls(synthetic) == []
    assert find_resurrected_allow_insecure_in_compose(synthetic) == []
