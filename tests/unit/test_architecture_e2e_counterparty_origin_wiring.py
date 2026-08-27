"""Structural guard: the e2e COUNTERPARTY origin and the server's CA trust are wired.

salesagent-mp53.8 builds the accepted leg of the inbound RFC 9421 verifier: a
request signed by a counterparty whose key the server resolves BY WALKING that
counterparty's published trust root is accepted, over real HTTP. The walk is
three outbound hops made by the SERVER container —
``agent_url -> capabilities -> identity.brand_json_url -> brand.json agents[] ->
jwks_uri -> JWKS`` (``adcp.signing.agent_resolver``) — so the leg only exists if
the stack gives the server something real to walk, and a trust store that
accepts the leaf it will be handed.

Sibling of ``test_architecture_e2e_webhook_capture_wiring.py`` (mp53.9) and it
shares that module's four detectors, which now live in
``tests/unit/_architecture_helpers.py``. Same reasoning for existing at all: the
behaviour is graded in-network by
``tests/e2e/test_request_signature_accepted_e2e.py``, but a half-wired stack
fails at ``docker compose up`` or, far worse, comes up and produces a 401 whose
message names an unresolvable counterparty rather than the missing compose line.
This guard reads the REAL files at ``make quality`` time.

The five sites:

1. a ``counterparty-origin`` compose service running the static origin module —
   a SIBLING of the capture service, never a mode flag inside it. What would be
   reused there is ~30 lines of ``BaseHTTPRequestHandler``; what would be
   coupled is a handler whose other mode records raw bytes for the signing
   suite.
2. the compose network alias on the EXISTING ``tls-proxy`` service, so the
   counterparty is a real HTTPS origin behind the one terminator.
3. the nginx SNI map row, routing that hostname to that service — the upstream
   half checked too, because a hostname routed to the WRONG service is wired
   everywhere a guard would look and still reaches the wrong origin.
4. leaf-certificate coverage for the hostname. ``counterparty.adcp-e2e.dev`` sits
   under the EXISTING ``*.adcp.test`` wildcard, so this one passes today and is
   here as a regression guard: a future narrowing of ``SAN_DNS_NAMES`` would
   otherwise break the walk at certificate verification, which surfaces as
   ``capabilities_unreachable``.
5. ``SSL_CERT_FILE`` on ``adcp-server``, pointing at a CONCATENATED bundle.
   ``adcp.signing.ip_pinned_transport._build_ssl_context`` is a bare
   ``ssl.create_default_context()``; the ``trust_env=False`` on the SDK's httpx
   clients is an httpx-level flag and cannot reach it, because OpenSSL reads
   ``SSL_CERT_FILE`` itself via ``set_default_verify_paths()``. Without it the
   server's handshake to the counterparty's ``https://...:8443`` leaf (signed by
   the private stack CA) fails and the walk dies silently as a key it could not
   resolve.

**Not a relaxation, and the guard is written so it cannot become one.**
Certificate validation stays full-strength against a real CA — this is the
receiver-side twin of the runner's ``E2E_CA_BUNDLE`` and the tls-proxy
healthcheck's ``--cacert``. What the guard pins is the CONCATENATION: ``SSL_CERT_FILE``
REPLACES the file half of the trust store rather than adding to it, so pointing
it at ``ca.pem`` alone would drop the public roots from that half. (The often-repeated
justification "otherwise every other outbound TLS fails" is image-dependent —
``set_default_verify_paths()`` honours ``SSL_CERT_DIR`` independently, and the
Debian-based server image has a populated hashed ``/etc/ssl/certs``. The bundle is
right under every image, which is why it is required here rather than argued for.)
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import (
    TLS_FRONT_SERVICE,
    compose_service,
    compose_service_environment,
    format_failure,
    load_yaml,
    repo_root,
    san_covers,
    san_dns_names,
    sni_map_upstreams,
    tls_front_aliases,
)

#: The counterparty's hostname, under the ``*.adcp-e2e.dev`` wildcard SAN.
#:
#: ``.test`` was tried first and is WRONG here, for a reason distinct from the one
#: that ruled it out on the webhook path. The SSRF argument does hold — the
#: inbound walk is gated by ``resolve_and_validate_host``, which applies IP
#: arithmetic only, and ``src/core/signing/`` never calls
#: ``is_reserved_tld_host``. But TIER 3 is a second gate that argument misses:
#: brand authorization matches the agent url against the brand domain by
#: eTLD+1 (``adcp.signing.etld.registrable_domain``), and ``.test`` is not in the
#: public suffix list, so ``registrable_domain("counterparty.adcp.test")`` is
#: ``None`` and the check refuses with ``brand_domain_invalid``. Measured
#: in-network: the accepted leg came back 401
#: ``request_signature_brand_json_malformed``. ``adcp-e2e.dev`` resolves to a real
#: registrable domain, so the eTLD+1 match works.
COUNTERPARTY_HOSTNAME = "counterparty.adcp-e2e.dev"

#: The compose service serving the counterparty's three plain GETs. A SIBLING of
#: ``webhook-capture``, on the same bare ``python:3.12-slim`` + ``.:/app`` shape.
COUNTERPARTY_SERVICE = "counterparty-origin"

#: The module that service must run. The seller's own trust root cannot stand in
#: for it: ``agent_resolver._fetch_capabilities`` does a raw GET with
#: ``follow_redirects=False`` and demands a 200 JSON object, and our ``/mcp``
#: answers GET with an SSE redirect.
COUNTERPARTY_MODULE = "tests.e2e.counterparty_origin_service"

#: The service whose outbound TLS must trust the stack CA — the one that walks.
SERVER_SERVICE = "adcp-server"

#: The env var OpenSSL reads for the FILE half of the default trust store.
CA_BUNDLE_ENV_VAR = "SSL_CERT_FILE"

#: What it must point at: the CONCATENATED bundle, at the path the repo bind
#: mount already makes visible inside the server container (``.:/app``).
CA_BUNDLE_PATH = "/app/.test-tls/ca-bundle.pem"

#: What it must NOT point at — the bare CA. Named explicitly so the failure
#: message can say which mistake was made rather than only that the value is wrong.
BARE_CA_PATH = "/app/.test-tls/ca.pem"

#: The generator that must EMIT the concatenated bundle beside the CA it already
#: writes. Emitting it at generation time is what keeps the file in step with the
#: CA: a bundle built once by hand outlives the next certificate rotation.
_BUNDLE_FILENAME = "ca-bundle.pem"

_REPO_ROOT = repo_root()
_BASE_COMPOSE = _REPO_ROOT / "docker-compose.e2e.yml"
_NGINX_TLS_TEMPLATE = _REPO_ROOT / "config" / "nginx" / "nginx-tls-test.conf.template"
_GEN_TLS_SCRIPT = _REPO_ROOT / "scripts" / "dev" / "gen_test_tls.py"
_ORIGIN_MODULE_PATH = _REPO_ROOT / "tests" / "e2e" / "counterparty_origin_service.py"


# ---------------------------------------------------------------------------
# Detectors specific to this guard. The four shared with the capture-origin
# guard live in ``_architecture_helpers``; these two do not generalize.
# ---------------------------------------------------------------------------


#: Import roots a bare ``python:3.12-slim`` cannot satisfy. ``tests.helpers``'s
#: package ``__init__`` transitively imports ``tests.factories`` (factory-boy),
#: so reaching for either kills the container on import, before it ever binds a
#: socket — a failure mp53.9's capture service hit for real.
FORBIDDEN_IMPORT_ROOTS = ("tests.helpers", "tests.factories")


def _forbidden_imports(path: Path) -> list[str]:
    """``<file>:<line>: imports <name>`` for every import of a forbidden root.

    Walks import NODES rather than scanning text: a substring match reports the
    prose in a module docstring that explains the constraint, and still misses
    ``import tests.helpers as h`` or an import nested inside a function.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            if any(name == root or name.startswith(f"{root}.") for root in FORBIDDEN_IMPORT_ROOTS):
                found.append(f"{path.name}:{node.lineno}: imports {name}")
    return found


def service_command(compose: dict, name: str) -> str:
    """A service's ``command:``, flattened to one string for either compose spelling."""
    command = compose_service(compose, name).get("command")
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return "" if command is None else str(command)


def service_mounts_repo(compose: dict, name: str) -> bool:
    """Whether the service bind-mounts the repository at ``/app``.

    The origin module lives in ``tests/e2e/``, so a service running it on a bare
    image has to see the repo — the same ``.:/app`` mount ``webhook-capture``
    uses, and the same mount that already puts ``.test-tls/`` inside the server.
    """
    volumes = compose_service(compose, name).get("volumes") or []
    return any(str(volume).split(":")[:2] == [".", "/app"] for volume in volumes)


# ---------------------------------------------------------------------------
# Site 1 — the counterparty is its own service, running the origin module
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_counterparty_origin_is_its_own_compose_service() -> None:
    """A ``counterparty-origin`` service exists, runs the origin module, and sees the repo."""
    compose = load_yaml(_BASE_COMPOSE)
    command = service_command(compose, COUNTERPARTY_SERVICE)

    violations = []
    if not compose_service(compose, COUNTERPARTY_SERVICE):
        violations.append(f"docker-compose.e2e.yml: no services.{COUNTERPARTY_SERVICE}")
    if COUNTERPARTY_MODULE not in command:
        violations.append(f"docker-compose.e2e.yml: services.{COUNTERPARTY_SERVICE}.command = {command!r}")
    if not service_mounts_repo(compose, COUNTERPARTY_SERVICE):
        violations.append(f"docker-compose.e2e.yml: services.{COUNTERPARTY_SERVICE} does not bind-mount .:/app")

    assert not violations, format_failure(
        summary="the e2e counterparty origin is not wired as its own compose service",
        violations=violations,
        fix_hint=(
            f"Add a {COUNTERPARTY_SERVICE} service running `python -m {COUNTERPARTY_MODULE}` on a bare "
            "python:3.12-slim with a .:/app mount, modelled on webhook-capture. A 'counterparty mode' "
            "inside webhook_capture_service.py is the wrong shape: its other mode records raw bytes for "
            "the signing suite and its do_POST semantics must not bleed into an identity origin."
        ),
    )


@pytest.mark.arch_guard
def test_the_counterparty_origin_module_exists_and_is_stdlib_only() -> None:
    """The module the service runs exists and imports nothing the bare image lacks.

    ``tests.helpers`` and ``tests.factories`` are the two that kill it: importing
    either runs a package ``__init__`` that pulls in factory-boy et al., and the
    container exits on import before ever binding a socket. mp53.9's capture
    service records that failure as one it hit for real.
    """
    assert _ORIGIN_MODULE_PATH.is_file(), format_failure(
        summary=f"{_ORIGIN_MODULE_PATH.name} does not exist",
        violations=[f"tests/e2e/{_ORIGIN_MODULE_PATH.name}: missing"],
        fix_hint=(
            "Write the static counterparty origin: the capabilities document at the agent path, "
            "/.well-known/brand.json (lists the agent url), /.well-known/brand-unlisted.json (does "
            "NOT), and /.well-known/jwks.json. All plain GETs answering 200 with a JSON object — "
            "that is what agent_resolver._fetch_capabilities requires."
        ),
    )

    forbidden = _forbidden_imports(_ORIGIN_MODULE_PATH)

    assert not forbidden, format_failure(
        summary=f"tests/e2e/{_ORIGIN_MODULE_PATH.name} reaches into a package a bare image cannot import",
        violations=forbidden,
        fix_hint=(
            "Keep it stdlib-only. tests.helpers.__init__ transitively imports tests.factories "
            "(factory-boy), which python:3.12-slim does not have; the container exits on import."
        ),
    )


def test_the_stdlib_only_detector_catches_a_real_import_and_ignores_prose(tmp_path) -> None:
    """The AST detector fires on an import and stays silent on a docstring mentioning one.

    Both halves matter. The silent half is why this detector is AST-based at all
    (the module it guards documents the constraint in its own docstring); the
    firing half is what stops that precision from becoming vacuity.
    """
    module = tmp_path / "origin.py"
    module.write_text('"""Importing tests.helpers here would break the bare image."""\n', encoding="utf-8")
    prose_only = _forbidden_imports(module)

    module.write_text("from tests.helpers.signing import keypair_for\n", encoding="utf-8")
    real_import = _forbidden_imports(module)

    assert prose_only == []
    assert real_import == ["origin.py:1: imports tests.helpers.signing"]


# ---------------------------------------------------------------------------
# Site 2 — the alias is on the EXISTING front
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_counterparty_hostname_is_an_alias_on_the_existing_tls_front() -> None:
    """``counterparty.adcp-e2e.dev`` resolves to the shared ``tls-proxy``, not a second terminator."""
    aliases = tls_front_aliases(load_yaml(_BASE_COMPOSE))

    assert COUNTERPARTY_HOSTNAME in aliases, format_failure(
        summary=f"{COUNTERPARTY_HOSTNAME} is not a network alias on the {TLS_FRONT_SERVICE} service",
        violations=[f"docker-compose.e2e.yml: services.{TLS_FRONT_SERVICE}.networks.default.aliases = {aliases}"],
        fix_hint=(
            f"Add {COUNTERPARTY_HOSTNAME} to the EXISTING {TLS_FRONT_SERVICE} aliases. Without it the "
            "server's walk cannot resolve the counterparty at all and the accepted leg 401s as "
            "request_signature_key_unknown."
        ),
    )


# ---------------------------------------------------------------------------
# Site 3 — the SNI map routes it, to the RIGHT upstream
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_nginx_sni_map_routes_the_counterparty_hostname_to_the_counterparty_service() -> None:
    """The TLS front routes the counterparty name to the counterparty origin by SNI."""
    routes = sni_map_upstreams(_NGINX_TLS_TEMPLATE.read_text(encoding="utf-8"))
    upstream = routes.get(COUNTERPARTY_HOSTNAME, "")

    assert upstream.startswith(f"{COUNTERPARTY_SERVICE}:"), format_failure(
        summary=f"{COUNTERPARTY_HOSTNAME} is not routed to {COUNTERPARTY_SERVICE} in the nginx SNI map",
        violations=[f"config/nginx/nginx-tls-test.conf.template: map $ssl_server_name = {routes}"],
        fix_hint=(
            f"Add `{COUNTERPARTY_HOSTNAME}  {COUNTERPARTY_SERVICE}:8080;` to the existing "
            "`map $ssl_server_name` block. The front terminates TLS; the origin never does. Routing "
            "the name to the wrong upstream passes every other check here and still fails the walk."
        ),
    )


# ---------------------------------------------------------------------------
# Site 4 — the certificate covers the name
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_test_certificate_covers_the_counterparty_hostname() -> None:
    """The generated leaf's SANs cover ``counterparty.adcp-e2e.dev``.

    Satisfied today by the ``*.adcp.test`` wildcard — a regression guard, not a
    red one. A narrowed SAN list would break the walk at certificate
    verification, which the middleware reports as ``capabilities_unreachable``:
    a message that names neither the certificate nor this file.
    """
    names = san_dns_names(_GEN_TLS_SCRIPT.read_text(encoding="utf-8"))

    assert san_covers(names, COUNTERPARTY_HOSTNAME), format_failure(
        summary=f"{COUNTERPARTY_HOSTNAME} is not covered by SAN_DNS_NAMES",
        violations=[f"scripts/dev/gen_test_tls.py: SAN_DNS_NAMES = {names}"],
        fix_hint=(
            f"Keep a SAN covering {COUNTERPARTY_HOSTNAME} (the existing *.adcp.test wildcard does). "
            "Regenerate BEFORE any stack starts — _is_current() compares served SANs for EXACT "
            "equality, so a forced regeneration mid-run replaces the leaf a live front is serving."
        ),
    )


# ---------------------------------------------------------------------------
# Site 5 — the server trusts the stack CA, via a CONCATENATED bundle
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_adcp_server_trusts_the_stack_ca_for_its_outbound_walk() -> None:
    """``adcp-server`` sets ``SSL_CERT_FILE`` at the bundle the repo mount already provides."""
    environment = compose_service_environment(load_yaml(_BASE_COMPOSE), SERVER_SERVICE)
    configured = environment.get(CA_BUNDLE_ENV_VAR)

    assert configured == CA_BUNDLE_PATH, format_failure(
        summary=f"{SERVER_SERVICE} does not point {CA_BUNDLE_ENV_VAR} at the stack CA bundle",
        violations=[
            f"docker-compose.e2e.yml: services.{SERVER_SERVICE}.environment.{CA_BUNDLE_ENV_VAR} = {configured!r}"
        ],
        fix_hint=(
            f"Set {CA_BUNDLE_ENV_VAR}: {CA_BUNDLE_PATH}. The SDK builds its own "
            "ssl.create_default_context() per hop, so nothing short of the OpenSSL env vars reaches "
            "it — trust_env=False on the httpx clients provably does not. The file is already inside "
            "the container via the .:/app mount; only the variable is missing. Verification stays ON."
        ),
    )


@pytest.mark.arch_guard
def test_the_server_ca_bundle_is_concatenated_and_not_the_bare_ca() -> None:
    """``SSL_CERT_FILE`` REPLACES the file half of the trust store, so it must carry the public roots too."""
    environment = compose_service_environment(load_yaml(_BASE_COMPOSE), SERVER_SERVICE)
    configured = environment.get(CA_BUNDLE_ENV_VAR, "")

    assert configured != BARE_CA_PATH, format_failure(
        summary=f"{SERVER_SERVICE} points {CA_BUNDLE_ENV_VAR} at the bare stack CA",
        violations=[
            f"docker-compose.e2e.yml: services.{SERVER_SERVICE}.environment.{CA_BUNDLE_ENV_VAR} = {configured!r}"
        ],
        fix_hint=(
            f"Point it at {CA_BUNDLE_PATH} — a concatenation of the system/certifi roots and the stack "
            f"CA. {CA_BUNDLE_ENV_VAR} replaces the file half of the default store rather than adding "
            "to it, so the bare CA drops every public root from that half."
        ),
    )


@pytest.mark.arch_guard
def test_the_tls_material_generator_emits_the_concatenated_bundle() -> None:
    """The bundle is written where the CA is written, by the same generator.

    Built by hand once, it survives exactly until the next certificate rotation
    and then silently pins a CA that no longer signs anything. It must also FAIL
    LOUDLY when it cannot find public roots: ``ensure-test-tls.sh``'s interpreter
    contract is literally ``import cryptography``, and its ``try_python``
    fallback can find an interpreter with cryptography and no certifi — emitting
    a CA-only bundle under that name would be the bare-CA mistake wearing the
    bundle's filename.
    """
    source = _GEN_TLS_SCRIPT.read_text(encoding="utf-8")

    assert _BUNDLE_FILENAME in source, format_failure(
        summary=f"scripts/dev/gen_test_tls.py never writes {_BUNDLE_FILENAME}",
        violations=[f"scripts/dev/gen_test_tls.py: no reference to {_BUNDLE_FILENAME}"],
        fix_hint=(
            f"Emit .test-tls/{_BUNDLE_FILENAME} alongside ca.pem: the SYSTEM bundle when present, "
            "else certifi.where(), else raise. Never write a CA-only file under that name — a silent "
            "fallback there is indistinguishable from the bare-CA mistake at every later reader."
        ),
    )


# ---------------------------------------------------------------------------
# Detector self-tests — a guard that cannot fail grades nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "flattened"),
    [
        (["python", "-m", COUNTERPARTY_MODULE], f"python -m {COUNTERPARTY_MODULE}"),
        (f"python -m {COUNTERPARTY_MODULE}", f"python -m {COUNTERPARTY_MODULE}"),
        (None, ""),
    ],
)
def test_command_detector_flattens_both_compose_spellings(command, flattened) -> None:
    synthetic = {"services": {COUNTERPARTY_SERVICE: {"command": command}}}

    assert service_command(synthetic, COUNTERPARTY_SERVICE) == flattened


def test_command_detector_reports_a_missing_service_as_empty() -> None:
    assert service_command({"services": {}}, COUNTERPARTY_SERVICE) == ""


@pytest.mark.parametrize(
    ("volumes", "mounted"),
    [
        ([".:/app"], True),
        ([".:/app:ro"], True),
        (["./config:/app/config"], False),
        ([], False),
    ],
)
def test_repo_mount_detector_reads_the_volume_list(volumes, mounted) -> None:
    synthetic = {"services": {COUNTERPARTY_SERVICE: {"volumes": volumes}}}

    assert service_mounts_repo(synthetic, COUNTERPARTY_SERVICE) is mounted


def test_environment_detector_reads_the_mapping_spelling() -> None:
    synthetic = {"services": {SERVER_SERVICE: {"environment": {CA_BUNDLE_ENV_VAR: CA_BUNDLE_PATH}}}}

    assert compose_service_environment(synthetic, SERVER_SERVICE) == {CA_BUNDLE_ENV_VAR: CA_BUNDLE_PATH}


def test_environment_detector_reads_the_list_spelling() -> None:
    """The ``KEY=value`` list form — a guard that only reads the mapping form grades nothing here."""
    synthetic = {"services": {SERVER_SERVICE: {"environment": [f"{CA_BUNDLE_ENV_VAR}={CA_BUNDLE_PATH}", "BARE"]}}}

    assert compose_service_environment(synthetic, SERVER_SERVICE) == {
        CA_BUNDLE_ENV_VAR: CA_BUNDLE_PATH,
        "BARE": "",
    }


def test_sni_upstream_detector_reads_the_target_not_just_the_key() -> None:
    synthetic = (
        "map $ssl_server_name $tls_upstream_named {\n"
        '    default                    "";\n'
        "    webhooks.adcp-e2e.dev      webhook-capture:8080;\n"
        f"    {COUNTERPARTY_HOSTNAME}    {COUNTERPARTY_SERVICE}:8080;  # the identity origin\n"
        "}\n"
    )

    assert sni_map_upstreams(synthetic) == {
        "webhooks.adcp-e2e.dev": "webhook-capture:8080",
        COUNTERPARTY_HOSTNAME: f"{COUNTERPARTY_SERVICE}:8080",
    }


def test_sni_upstream_detector_reports_a_name_routed_to_the_wrong_service() -> None:
    """The mis-routed case the key-only check cannot see."""
    synthetic = (
        "map $ssl_server_name $tls_upstream_named {\n"
        '    default                    "";\n'
        f"    {COUNTERPARTY_HOSTNAME}    webhook-capture:8080;\n"
        "}\n"
    )

    assert sni_map_upstreams(synthetic)[COUNTERPARTY_HOSTNAME] == "webhook-capture:8080"
