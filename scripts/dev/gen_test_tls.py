#!/usr/bin/env python3
"""Generate the test stacks' private CA and server certificate (salesagent-tgzb, GH #1291).

WHY THIS EXISTS. ``_get_protocol_for_domain`` (``src/core/domain_config.py``)
publishes ``https`` only for a dotted, non-loopback host — deliberately, because
advertising https for a host with no certificate publishes a trust root nothing
can reach. Every test host was such a host, so the https branch of the trust
root could never be exercised end to end. This mints the material that lets a
test host EARN https by actually serving it: a private CA and one leaf covering
the dotted names the test stacks are reachable at.

THE CAVEAT, STATED HERE RATHER THAN DISCOVERED LATER. A private CA is not
"publicly trusted", which is the exact wording of ``_get_protocol_for_domain``'s
rationale. That rationale is about reachability BY A COUNTERPARTY; in the test
stack the counterparty is our own client, which trusts this CA explicitly. The
production predicate needs no change and must not be weakened.

LIFECYCLE
    * Output lands in ``.test-tls/`` at the repo root — gitignored, and already
      inside the ``.:/app`` bind mount both the server and the runner get, so no
      compose plumbing has to distribute it.
    * Validity is deliberately SHORT (30 days) and regeneration is automatic:
      missing, unparseable, expiring within 7 days, or no longer covering the
      names we serve all trigger a rewrite. "Exists" is not the same as "valid",
      and a certificate that expires mid-suite is a flaky-test generator.
    * ``not_before`` is backdated an hour to absorb container/host clock skew.
    * NOTHING DELETES IT. It is a build artifact: regenerating per run would
      race parallel stacks that are already serving the previous leaf.

Run it directly, or via ``scripts/dev/ensure-test-tls.sh`` (which finds an
interpreter that has ``cryptography``, a direct dependency of this project).
It also runs inside the runner image (``dc run --rm --no-deps -T tests python
scripts/dev/gen_test_tls.py``), so it stays on the standard library plus
``cryptography`` and never imports the application. Every write happens inside
a function reached from ``main()`` — importing this module touches no file.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import ipaddress
import os
import uuid
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

REPO_ROOT = Path(__file__).resolve().parents[2]
TLS_DIR = REPO_ROOT / ".test-tls"
CA_CERT = TLS_DIR / "ca.pem"
CA_KEY = TLS_DIR / "ca.key"
SERVER_CERT = TLS_DIR / "server.pem"
SERVER_KEY = TLS_DIR / "server.key"
_LOCK_FILE = TLS_DIR / ".gen-lock"

# TWO TRUST ANCHORS, DELIBERATELY DIFFERENT FILES.
#
# CA_CERT alone (the private CA) is what ``--cacert`` flags and ``E2E_CA_BUNDLE``
# use: a caller checking one of this stack's TLS fronts should trust ONLY this
# stack's own leaf, not the whole public web.
#
# The CONCATENATED bundle below is a different job. It is what ``SSL_CERT_FILE``
# points at, and ``SSL_CERT_FILE`` REPLACES the file half of the process's
# default store rather than adding to it — so a CA-only file leaves the process
# trusting our test CA and nothing else. Two consumers need the combination:
#   * the server's OUTBOUND counterparty walk (salesagent-mp53.8) — the inbound
#     verifier resolves an unknown signer by fetching capabilities/brand.json/
#     JWKS over the stack's own TLS front, and the SDK builds that client with a
#     bare ``ssl.create_default_context()``; ``trust_env=False`` on its httpx
#     clients is an httpx-level flag and provably cannot reach it, so only the
#     OpenSSL env vars do.
#   * the GH #1802 egress seam and anything else in the same process that still
#     needs REAL public HTTPS (``uv sync`` fetching from pypi.org in particular —
#     this broke a full suite run before this file learned to emit public roots).
#
# (Note the honest limit of the CA-only failure mode: ``SSL_CERT_DIR`` is
# honoured independently, so on an image with a populated /etc/ssl/certs the
# system roots would still resolve — the concatenation is right because it is
# correct under EVERY image, not because omitting it always breaks.)
#
# The same bytes are published under BOTH historical names because both are
# wired in this tree: ``docker-compose.e2e.yml`` points one service's
# ``SSL_CERT_FILE`` at ``ca-bundle.pem`` and others at ``combined-ca.pem``, the
# structural guards read the literal ``ca-bundle.pem``, and tests read the
# ``COMBINED_CERT`` attribute. One computation, two published names.
CA_BUNDLE = TLS_DIR / "ca-bundle.pem"
COMBINED_CERT = TLS_DIR / "combined-ca.pem"
_TRUST_BUNDLES = (CA_BUNDLE, COMBINED_CERT)

# Debian/Ubuntu's system bundle is first (confirmed present in
# python:3.12-slim-bookworm, this project's base image — the same file
# ``ssl.get_default_verify_paths()`` resolves to via /usr/lib/ssl/cert.pem when
# SSL_CERT_FILE is unset). RHEL/Fedora and macOS/BSD paths follow, and
# ``certifi`` is the cross-platform fallback so a developer's laptop running
# ``make quality`` gets real combined trust too, not just the CI box.
_SYSTEM_CA_BUNDLE_CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian/Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",  # RHEL/Fedora
    "/etc/ssl/cert.pem",  # macOS/BSD
)

VALIDITY = dt.timedelta(days=30)
RENEW_WITHIN = dt.timedelta(days=7)
CLOCK_SKEW = dt.timedelta(hours=1)

# ``*.adcp.test`` is the whole per-worker mechanism: the bdd_e2e TLS sidecars are
# named ``<compose-project>-tls-gwN.adcp.test`` and COMPOSE_PROJECT_NAME never
# contains a dot, so every worker of every concurrent stack is exactly one label
# under this wildcard. It equally covers the shared front's aliases —
# ``proxy.adcp.test``, ``creative-agent.adcp.test``, ``webhooks.adcp.test``.
# ``agent.localhost`` is the host-published name: RFC 6761 reserves
# ``.localhost`` for loopback, so it resolves with no DNS and no /etc/hosts edit,
# and it has a dot — which is what makes the predicate answer https for it
# without being told to.
# ``*.adcp-e2e.dev`` covers every e2e origin the SERVER dials OUTBOUND: the
# webhook receiver (salesagent-mp53.9) and the counterparty identity origin
# (salesagent-mp53.8). A wildcard rather than a growing literal list — each new
# origin is one label under it and costs no certificate change.
#
# Neither can live under ``*.adcp.test``, and they are barred for DIFFERENT
# reasons, which is worth writing down because the first one does not imply the
# second:
#   * the webhook receiver — AdCP 3.1.1 lists ``.test`` among the RFC 6761
#     special-use names a seller MUST refuse, and the proof-of-control prover
#     enforces that unconditionally (``notification_proof_service`` ->
#     ``is_reserved_tld_host``) BEFORE the SSRF check. A receiver under ``.test``
#     is refused before a socket opens — correct production behaviour, not
#     something to relax.
#   * the counterparty origin — that reserved-TLD rule does NOT reach the inbound
#     walk (it is gated by IP arithmetic alone). ``.test`` fails there for an
#     unrelated reason: Tier 3 brand authorization matches agent url to brand
#     domain by eTLD+1, and ``.test`` is not in the public suffix list, so
#     ``registrable_domain()`` returns None and the check refuses with
#     ``brand_domain_invalid``.
# Both names are deliberately UNREGISTERED and resolve only via the compose
# network alias — nothing is ever published to real DNS.
SAN_DNS_NAMES = (
    "adcp.test",
    "*.adcp.test",
    "*.adcp-e2e.dev",
    "localhost",
    "agent.localhost",
    "*.localhost",
)
# 127.0.0.2 (GH #1757): a SECOND loopback address, distinct from
# 127.0.0.1, that tests/integration/test_protocol_webhook_egress.py needs to
# prove a per-host IP pin actually pins per host — two origins that differed
# only by port would share the same pinned address and grade nothing.
SAN_IP_ADDRESSES = ("127.0.0.1", "127.0.0.2", "::1")

_CA_SUBJECT = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AdCP test stack CA")])
_LEAF_SUBJECT = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "adcp.test")])


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _subject_alt_name() -> x509.SubjectAlternativeName:
    names: list[x509.GeneralName] = [x509.DNSName(name) for name in SAN_DNS_NAMES]
    names.extend(x509.IPAddress(ipaddress.ip_address(addr)) for addr in SAN_IP_ADDRESSES)
    return x509.SubjectAlternativeName(names)


def _sign(
    *,
    subject: x509.Name,
    issuer: x509.Name,
    public_key: ec.EllipticCurvePublicKey,
    signing_key: ec.EllipticCurvePrivateKey,
    extensions: list[tuple[x509.ExtensionType, bool]],
) -> x509.Certificate:
    """Build and sign one certificate. Shared by the CA and the leaf."""
    now = _now()
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - CLOCK_SKEW)
        .not_valid_after(now + VALIDITY)
    )
    for extension, critical in extensions:
        builder = builder.add_extension(extension, critical=critical)
    return builder.sign(signing_key, hashes.SHA256())


def _key_usage(**enabled: bool) -> x509.KeyUsage:
    flags: dict[str, Any] = dict.fromkeys(
        (
            "digital_signature",
            "content_commitment",
            "key_encipherment",
            "data_encipherment",
            "key_agreement",
            "key_cert_sign",
            "crl_sign",
            "encipher_only",
            "decipher_only",
        ),
        False,
    )
    flags.update(enabled)
    return x509.KeyUsage(**flags)


def _write(path: Path, data: bytes, *, private: bool) -> None:
    """Write *data* to *path* atomically.

    ``_refresh_trust_bundles()`` rewrites the bundles on every
    ``ensure_test_tls()`` call, including the already-current fast path — under
    a parallel xdist run, many workers call it concurrently. A direct
    ``write_bytes`` lets a concurrent reader (an httpx client building its TLS
    trust store from ``SSL_CERT_FILE`` at the OS level) observe a torn,
    partially-written file mid-overwrite, which reads as a genuine but
    nondeterministic ``CERTIFICATE_VERIFY_FAILED``. Writing to a sibling
    temp file and ``os.replace``-ing it over the target is atomic on POSIX: a
    concurrent open() always sees either the complete old file or the complete
    new one, never a partial one.
    """
    tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_bytes(data)
    tmp_path.chmod(0o600 if private else 0o644)
    os.replace(tmp_path, path)


def _pem_cert(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _pem_key(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _is_current() -> bool:
    """True when the material on disk can still be served and still matches.

    Three independent reasons to regenerate, all of which have to be checked or
    "the file exists" quietly stands in for "the file works": it is missing or
    unreadable, it expires soon, or the SAN set drifted from what the stacks are
    reachable at (a name added to ``SAN_DNS_NAMES`` must actually reach a leaf).

    The trust bundles are deliberately NOT part of this check: they are DERIVED
    from ``CA_CERT`` and rebuilt on every call by ``_refresh_trust_bundles()``,
    so a missing or stale one must not force an expensive regeneration that
    disturbs stacks already serving the current leaf.
    """
    if not all(path.is_file() for path in (CA_CERT, CA_KEY, SERVER_CERT, SERVER_KEY)):
        return False
    try:
        ca = x509.load_pem_x509_certificate(CA_CERT.read_bytes())
        leaf = x509.load_pem_x509_certificate(SERVER_CERT.read_bytes())
        served = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except Exception:  # noqa: BLE001 - any parse failure means "regenerate", never "trust it"
        return False
    if set(served.get_values_for_type(x509.DNSName)) != set(SAN_DNS_NAMES):
        return False
    return min(ca.not_valid_after_utc, leaf.not_valid_after_utc) > _now() + RENEW_WITHIN


def _public_roots() -> bytes:
    """The public root store to concatenate our test CA onto.

    Emitted HERE, at generation time, rather than assembled once by hand: a
    bundle built by hand outlives the next certificate rotation and then quietly
    pins a CA that no longer signs anything.

    Prefers the system bundle, falls back to certifi, and FAILS LOUDLY if neither
    is available. It must never silently emit a CA-only file — that would leave
    the process trusting our test CA and nothing else, which fails as an opaque
    handshake error on the first real outbound TLS call and looks nothing like a
    missing-roots problem. Note that ``ensure-test-tls.sh``'s interpreter
    contract is literally ``import cryptography``; its fallback interpreter can
    have that and NOT certifi, which is exactly why the system paths are tried
    first and why the failure is explicit rather than an empty prefix.
    """
    for candidate in _SYSTEM_CA_BUNDLE_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return path.read_bytes()
    try:
        import certifi
    except ImportError as exc:
        raise RuntimeError(
            "cannot build .test-tls/ca-bundle.pem (nor .test-tls/combined-ca.pem): no system CA "
            "bundle found and certifi is not importable. Refusing to emit a CA-only bundle — "
            "SSL_CERT_FILE REPLACES the file half of the trust store, so that would leave the "
            "process trusting the test CA alone. Install certifi or run where a system bundle exists."
        ) from exc
    return Path(certifi.where()).read_bytes()


def _refresh_trust_bundles() -> None:
    """Rebuild every ``SSL_CERT_FILE`` bundle: the public roots + our private CA.

    Cheap (two file reads and a concatenation, no crypto), so it runs on every
    call regardless of whether the CA/leaf themselves needed regenerating — the
    bundles must stay in step with ``CA_CERT`` even on the "already current"
    fast path (e.g. upgrading a ``.test-tls/`` directory written before one of
    these filenames existed). One computation, published under both names.
    """
    bundle = _public_roots() + CA_CERT.read_bytes()
    for path in _TRUST_BUNDLES:
        _write(path, bundle, private=False)


@contextlib.contextmanager
def _regeneration_lock():
    """Serialize ``ensure_test_tls()`` across CONCURRENT PROCESSES (xdist workers).

    An in-process ``threading.Lock`` cannot help here — pytest-xdist workers
    are separate OS processes, each importing this module fresh. Without a
    real file lock, two workers racing the "not current" branch below could
    interleave: e.g. worker A's fresh ``ca_key`` signs worker A's leaf, but
    worker B's atomic write of ``CA_CERT`` (from a DIFFERENT, concurrently
    generated CA key) lands last — the leaf on disk no longer chains to the
    CA on disk, and every TLS handshake against it fails with a genuine but
    maddeningly intermittent certificate error. ``fcntl.flock`` blocks the
    whole regenerate-and-write sequence to one worker at a time; the others
    proceed only once the lock holder's now-current material is on disk, at
    which point their own ``_is_current()`` check (called again after
    acquiring the lock, not just before) finds it and short-circuits.
    """
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_LOCK_FILE, "w") as lock_fp:
        fcntl.flock(lock_fp, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_fp, fcntl.LOCK_UN)


def ensure_test_tls(*, force: bool = False) -> Path:
    """Make sure ``.test-tls/`` holds a usable CA + leaf; return the CA path.

    Idempotent: a run that finds current material mints no new key material, so
    concurrent stacks serving the existing leaf are never disturbed — it still
    refreshes the DERIVED trust bundles, atomically. The whole check +
    (re)generate sequence runs under a process-wide file lock (see
    :func:`_regeneration_lock`) so concurrent xdist workers can never interleave
    a regeneration into a CA/leaf pair that no longer chain to each other.
    """
    if not force and _is_current():
        _refresh_trust_bundles()
        return CA_CERT

    with _regeneration_lock():
        if not force and _is_current():
            # Another worker regenerated while we waited for the lock.
            _refresh_trust_bundles()
            return CA_CERT
        return _regenerate()


def _regenerate() -> Path:
    """Generate a fresh CA + leaf and write them, plus the trust bundles. Caller holds the lock."""
    TLS_DIR.mkdir(parents=True, exist_ok=True)

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = _sign(
        subject=_CA_SUBJECT,
        issuer=_CA_SUBJECT,
        public_key=ca_key.public_key(),
        signing_key=ca_key,
        extensions=[
            (x509.BasicConstraints(ca=True, path_length=0), True),
            (_key_usage(key_cert_sign=True, crl_sign=True), True),
            (x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False),
        ],
    )

    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_cert = _sign(
        subject=_LEAF_SUBJECT,
        issuer=ca_cert.subject,
        public_key=leaf_key.public_key(),
        signing_key=ca_key,
        extensions=[
            (_subject_alt_name(), False),
            (x509.BasicConstraints(ca=False, path_length=None), True),
            (_key_usage(digital_signature=True, key_agreement=True), True),
            (x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False),
            (x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), False),
            (x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False),
        ],
    )

    _write(CA_KEY, _pem_key(ca_key), private=True)
    _write(CA_CERT, _pem_cert(ca_cert), private=False)
    _write(SERVER_KEY, _pem_key(leaf_key), private=True)
    # Full chain: the leaf first, then the issuer. Clients that trust ca.pem do
    # not need it, but a chain costs nothing and makes the file usable by tools
    # that do walk it.
    _write(SERVER_CERT, _pem_cert(leaf_cert) + _pem_cert(ca_cert), private=False)
    _refresh_trust_bundles()
    return CA_CERT


def main() -> None:
    ca = ensure_test_tls()
    print(f"test TLS material ready: {ca} (serving {', '.join(SAN_DNS_NAMES)})")


if __name__ == "__main__":
    main()
