"""Unit tests for webhook auth header builder (slice 3 of signing-non-embedded).

Two surfaces are exercised:

* :func:`build_auth_headers` — pure header builder. Takes a
  :class:`LoadedSigningCredential` snapshot and a signing mode; returns
  the auth headers to merge onto the outgoing request. No DB, no IO.

* :func:`load_active_signing_credential` — DB + filesystem loader.
  Reads the active webhook-signing row for a tenant, validates the
  backend, reads the PEM atomically, and returns a frozen snapshot.
  Failure modes (no row, KMS backend, missing PEM, unsupported JWK)
  raise :class:`SigningConfigurationError` with a precise message
  rather than handing the caller a mystery ``None``.

The split mirrors production: the loader runs once per webhook batch
(in :class:`WebhookDeliveryService._send_webhook_enhanced`) so kid +
PEM bytes can never desync, and the builder runs per endpoint with
just the snapshot.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from adcp.signing.crypto import ALG_ED25519
from adcp.signing.keygen import generate_signing_keypair

from src.services.webhook_signing import (
    SIGNING_MODE_BOTH,
    SIGNING_MODE_HMAC,
    SIGNING_MODE_RFC9421,
    LoadedSigningCredential,
    SigningConfigurationError,
    build_auth_headers,
    invalidate_credential_cache,
    load_active_signing_credential,
)


@pytest.fixture(autouse=True)
def _isolate_credential_cache():
    """Each test starts with an empty cache. The TTLCache survives across
    tests in the same process; without isolation a test that pollutes it
    would silently shadow DB mocks in the next test."""
    invalidate_credential_cache()
    yield
    invalidate_credential_cache()


# Fixture body: stable JSON the wire and signature share.
PAYLOAD = {"adcp_version": "v3", "notification_type": "scheduled", "n": 1}
BODY_BYTES = json.dumps(PAYLOAD, sort_keys=True, separators=(",", ":")).encode("utf-8")
TIMESTAMP = "2026-05-07T10:00:00+00:00"
URL = "https://buyer.example.com/webhook"
BASE_HEADERS = {"Content-Type": "application/json", "X-ADCP-Timestamp": TIMESTAMP}


@pytest.fixture
def ed25519_keypair():
    """Real Ed25519 keypair from the SDK keygen — used by both fixtures."""
    return generate_signing_keypair(alg="ed25519", purpose="webhook-signing")


@pytest.fixture
def ed25519_snapshot(ed25519_keypair):
    """A pre-loaded snapshot mirroring what the loader returns at runtime."""
    pem_bytes, jwk = ed25519_keypair
    return LoadedSigningCredential(key_id=jwk["kid"], alg=ALG_ED25519, pem_bytes=pem_bytes)


@pytest.fixture
def ed25519_jwk(ed25519_keypair):
    """The public JWK for the matching keypair, for the verifier roundtrip."""
    return ed25519_keypair[1]


# ---------------------------------------------------------------------------
# build_auth_headers — header builder behavior
# ---------------------------------------------------------------------------


class TestHmacMode:
    """Legacy mode: only X-ADCP-Signature, computed off the wire body."""

    def test_hmac_with_strong_secret_attaches_signature(self):
        secret = "x" * 32
        out = build_auth_headers(
            signing_mode=SIGNING_MODE_HMAC,
            method="POST",
            url=URL,
            body=BODY_BYTES,
            timestamp=TIMESTAMP,
            base_headers=BASE_HEADERS,
            webhook_secret=secret,
            active_credential=None,
        )
        # HMAC-SHA256 over the raw bytes ``timestamp.encode() + b"." + body``.
        msg = TIMESTAMP.encode("ascii") + b"." + BODY_BYTES
        expected = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
        assert out["X-ADCP-Signature"] == expected
        assert "Signature-Input" not in out
        assert "Signature" not in out

    def test_hmac_no_secret_omits_signature(self):
        out = build_auth_headers(
            signing_mode=SIGNING_MODE_HMAC,
            method="POST",
            url=URL,
            body=BODY_BYTES,
            timestamp=TIMESTAMP,
            base_headers=BASE_HEADERS,
            webhook_secret=None,
            active_credential=None,
        )
        assert "X-ADCP-Signature" not in out
        # Base headers preserved unchanged.
        assert out["Content-Type"] == "application/json"

    def test_hmac_weak_secret_logs_and_omits(self, caplog):
        out = build_auth_headers(
            signing_mode=SIGNING_MODE_HMAC,
            method="POST",
            url=URL,
            body=BODY_BYTES,
            timestamp=TIMESTAMP,
            base_headers=BASE_HEADERS,
            webhook_secret="too-short",
            active_credential=None,
        )
        assert "X-ADCP-Signature" not in out
        assert any("too weak" in r.message for r in caplog.records)

    def test_hmac_handles_non_utf8_body_bytes(self):
        # The HMAC pre-image is byte-level (no decode), so a body that
        # isn't valid UTF-8 must still produce a signature instead of
        # crashing the signer mid-delivery. Today's caller always sends
        # JSON, but ``body: bytes`` permits more.
        secret = "z" * 32
        non_utf8 = b"\xff\xfe\x00binary\x00payload"
        out = build_auth_headers(
            signing_mode=SIGNING_MODE_HMAC,
            method="POST",
            url=URL,
            body=non_utf8,
            timestamp=TIMESTAMP,
            base_headers=BASE_HEADERS,
            webhook_secret=secret,
            active_credential=None,
        )
        msg = TIMESTAMP.encode("ascii") + b"." + non_utf8
        expected = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
        assert out["X-ADCP-Signature"] == expected


class TestRfc9421Mode:
    """RFC 9421 mode: real sign call, real headers, no HMAC."""

    def test_rfc9421_emits_signature_and_digest_headers(self, ed25519_snapshot):
        out = build_auth_headers(
            signing_mode=SIGNING_MODE_RFC9421,
            method="POST",
            url=URL,
            body=BODY_BYTES,
            timestamp=TIMESTAMP,
            base_headers=BASE_HEADERS,
            webhook_secret=None,
            active_credential=ed25519_snapshot,
        )
        assert "Signature" in out
        assert "Signature-Input" in out
        assert "Content-Digest" in out
        # In rfc9421 mode the legacy HMAC header is NOT attached.
        assert "X-ADCP-Signature" not in out
        # The kid is wired through to Signature-Input.
        assert ed25519_snapshot.key_id in out["Signature-Input"]
        # And the webhook profile tag is pinned (anti-cross-profile-replay).
        assert 'tag="adcp/webhook-signing/v1"' in out["Signature-Input"]

    def test_rfc9421_content_digest_matches_body(self, ed25519_snapshot):
        out = build_auth_headers(
            signing_mode=SIGNING_MODE_RFC9421,
            method="POST",
            url=URL,
            body=BODY_BYTES,
            timestamp=TIMESTAMP,
            base_headers=BASE_HEADERS,
            webhook_secret=None,
            active_credential=ed25519_snapshot,
        )
        # Content-Digest is "sha-256=:<base64>:" per RFC 9530.
        digest_header = out["Content-Digest"]
        expected_b64 = base64.b64encode(hashlib.sha256(BODY_BYTES).digest()).decode()
        assert f"sha-256=:{expected_b64}:" == digest_header

    def test_rfc9421_missing_credential_fails_closed(self):
        with pytest.raises(SigningConfigurationError, match="active webhook-signing credential"):
            build_auth_headers(
                signing_mode=SIGNING_MODE_RFC9421,
                method="POST",
                url=URL,
                body=BODY_BYTES,
                timestamp=TIMESTAMP,
                base_headers=BASE_HEADERS,
                webhook_secret="x" * 32,
                active_credential=None,
            )

    def test_rfc9421_does_not_cover_authorization_header(self, ed25519_snapshot):
        # Defense-in-depth: bearer tokens must NEVER enter the signature
        # base, even if a future SDK starts auto-covering Authorization.
        # The wire request still carries the Authorization header (caller
        # added it to base_headers), but Signature-Input must not list it.
        out = build_auth_headers(
            signing_mode=SIGNING_MODE_RFC9421,
            method="POST",
            url=URL,
            body=BODY_BYTES,
            timestamp=TIMESTAMP,
            base_headers={**BASE_HEADERS, "Authorization": "Bearer secret-token-xyz"},
            webhook_secret=None,
            active_credential=ed25519_snapshot,
        )
        # Authorization survives to the wire — buyer needs it for transport auth.
        assert out["Authorization"] == "Bearer secret-token-xyz"
        # …but it's NOT among the signed components.
        assert '"authorization"' not in out["Signature-Input"].lower()
        # And the bearer value never appears in any signing header.
        for header_value in (out["Signature-Input"], out["Signature"], out["Content-Digest"]):
            assert "secret-token-xyz" not in header_value

    def test_rfc9421_missing_content_type_fails_closed(self, ed25519_snapshot):
        # Webhook profile pins content-type as a covered component. If a
        # future refactor drops it from base_headers, the resulting
        # signature would parse but fail buyer verifiers — fail at sign
        # time instead.
        with pytest.raises(SigningConfigurationError, match="Content-Type"):
            build_auth_headers(
                signing_mode=SIGNING_MODE_RFC9421,
                method="POST",
                url=URL,
                body=BODY_BYTES,
                timestamp=TIMESTAMP,
                base_headers={"X-ADCP-Timestamp": TIMESTAMP},  # no Content-Type
                webhook_secret=None,
                active_credential=ed25519_snapshot,
            )


class TestBothMode:
    """Migration window: HMAC + RFC 9421 both attached so the buyer can
    verify with whichever they support. Strict — no silent downgrade."""

    def test_both_attaches_hmac_and_rfc9421(self, ed25519_snapshot):
        secret = "y" * 32
        out = build_auth_headers(
            signing_mode=SIGNING_MODE_BOTH,
            method="POST",
            url=URL,
            body=BODY_BYTES,
            timestamp=TIMESTAMP,
            base_headers=BASE_HEADERS,
            webhook_secret=secret,
            active_credential=ed25519_snapshot,
        )
        assert "X-ADCP-Signature" in out
        assert "Signature" in out
        assert "Signature-Input" in out
        assert "Content-Digest" in out

    def test_both_with_no_credential_fails_closed(self):
        # Buyer asked for both; missing credential is a config error,
        # NOT a quiet fallback to HMAC-only.
        with pytest.raises(SigningConfigurationError, match="active webhook-signing credential"):
            build_auth_headers(
                signing_mode=SIGNING_MODE_BOTH,
                method="POST",
                url=URL,
                body=BODY_BYTES,
                timestamp=TIMESTAMP,
                base_headers=BASE_HEADERS,
                webhook_secret="x" * 32,
                active_credential=None,
            )

    def test_both_with_missing_secret_fails_closed(self, ed25519_snapshot):
        # Silently dropping the HMAC half because the secret is missing
        # would downgrade ``both`` → ``rfc9421`` without anyone noticing.
        with pytest.raises(SigningConfigurationError, match="webhook_secret"):
            build_auth_headers(
                signing_mode=SIGNING_MODE_BOTH,
                method="POST",
                url=URL,
                body=BODY_BYTES,
                timestamp=TIMESTAMP,
                base_headers=BASE_HEADERS,
                webhook_secret=None,
                active_credential=ed25519_snapshot,
            )

    def test_both_with_weak_secret_fails_closed(self, ed25519_snapshot):
        with pytest.raises(SigningConfigurationError, match="webhook_secret"):
            build_auth_headers(
                signing_mode=SIGNING_MODE_BOTH,
                method="POST",
                url=URL,
                body=BODY_BYTES,
                timestamp=TIMESTAMP,
                base_headers=BASE_HEADERS,
                webhook_secret="too-short",
                active_credential=ed25519_snapshot,
            )


class TestUnknownMode:
    def test_unknown_signing_mode_raises(self):
        with pytest.raises(SigningConfigurationError, match="unknown signing_mode"):
            build_auth_headers(
                signing_mode="bogus",
                method="POST",
                url=URL,
                body=BODY_BYTES,
                timestamp=TIMESTAMP,
                base_headers=BASE_HEADERS,
                webhook_secret=None,
                active_credential=None,
            )


class TestRoundtrip:
    """A signed request should verify with the library's webhook verifier
    when fed back through with the buyer's JWKS. Real crypto end-to-end."""

    def test_signed_webhook_verifies_with_buyer_side_resolver(self, ed25519_snapshot, ed25519_jwk):
        from adcp.signing.jwks import StaticJwksResolver
        from adcp.signing.webhook_verifier import (
            WebhookVerifyOptions,
            verify_webhook_signature,
        )

        out = build_auth_headers(
            signing_mode=SIGNING_MODE_RFC9421,
            method="POST",
            url=URL,
            body=BODY_BYTES,
            timestamp=TIMESTAMP,
            base_headers=BASE_HEADERS,
            webhook_secret=None,
            active_credential=ed25519_snapshot,
        )

        # Buyer resolves the JWK from the operator's brand.json → jwks_uri.
        resolver = StaticJwksResolver(jwks={"keys": [ed25519_jwk]})
        verified = verify_webhook_signature(
            method="POST",
            url=URL,
            headers=out,
            body=BODY_BYTES,
            options=WebhookVerifyOptions(jwks_resolver=resolver),
        )
        assert verified.key_id == ed25519_jwk["kid"]


# ---------------------------------------------------------------------------
# load_active_signing_credential — DB + filesystem loader behavior
# ---------------------------------------------------------------------------


class TestLoadActiveSigningCredential:
    """Loader pulls the active webhook-signing row for a tenant, opens
    the PEM under the same call, and validates everything. The DB read
    is mocked because we're testing the orchestration logic, not the
    repository (which has its own integration coverage)."""

    @pytest.fixture(autouse=True)
    def _scoped_keys_dir(self, tmp_path, monkeypatch):
        """Point the loader's path-traversal guard at this test's tmp dir
        so PEMs written under tmp_path pass the containment check."""
        monkeypatch.setenv("WEBHOOK_SIGNING_KEYS_DIR", str(tmp_path))

    @staticmethod
    def _row(tmp_path, *, backend="local_pem", jwk=None):
        """Build a fake ORM row that quacks like TenantSigningCredential."""
        if jwk is None:
            _, jwk = generate_signing_keypair(alg="ed25519", purpose="webhook-signing")
        pem_bytes = generate_signing_keypair(alg="ed25519", purpose="webhook-signing")[0]
        pem_path = tmp_path / "key.pem"
        pem_path.write_bytes(pem_bytes)
        row = MagicMock()
        row.backend = backend
        row.backend_ref = str(pem_path)
        row.public_jwk = jwk
        row.key_id = jwk["kid"]
        return row

    def test_hmac_mode_short_circuits_without_db_lookup(self):
        # The legacy path never reads our outbound key, so the loader
        # MUST not even open a DB session. Patch so an unexpected open
        # would crash loudly.
        with patch("src.core.database.database_session.get_db_session") as mock_db:
            result = load_active_signing_credential(tenant_id="t1", signing_mode=SIGNING_MODE_HMAC)
        assert result is None
        mock_db.assert_not_called()

    def test_missing_tenant_id_in_signed_mode_raises(self):
        with pytest.raises(SigningConfigurationError, match="tenant_id"):
            load_active_signing_credential(tenant_id=None, signing_mode=SIGNING_MODE_RFC9421)

    def test_returns_snapshot_with_atomic_kid_alg_and_pem(self, tmp_path):
        row = self._row(tmp_path)
        with patch("src.core.database.repositories.TenantSigningCredentialRepository") as mock_repo_cls:
            mock_repo_cls.return_value.get_active.return_value = row
            with patch("src.core.database.database_session.get_db_session"):
                snap = load_active_signing_credential(tenant_id="t1", signing_mode=SIGNING_MODE_RFC9421)
        assert isinstance(snap, LoadedSigningCredential)
        assert snap.key_id == row.key_id
        assert snap.alg == ALG_ED25519
        assert snap.pem_bytes  # actually loaded the file

    def test_no_active_row_fails_closed(self):
        with patch("src.core.database.repositories.TenantSigningCredentialRepository") as mock_repo_cls:
            mock_repo_cls.return_value.get_active.return_value = None
            with patch("src.core.database.database_session.get_db_session"):
                with pytest.raises(SigningConfigurationError, match="none configured"):
                    load_active_signing_credential(tenant_id="t1", signing_mode=SIGNING_MODE_RFC9421)

    def test_kms_backend_fails_closed(self, tmp_path):
        row = self._row(tmp_path, backend="gcp_kms")
        with patch("src.core.database.repositories.TenantSigningCredentialRepository") as mock_repo_cls:
            mock_repo_cls.return_value.get_active.return_value = row
            with patch("src.core.database.database_session.get_db_session"):
                with pytest.raises(SigningConfigurationError, match="local_pem"):
                    load_active_signing_credential(tenant_id="t1", signing_mode=SIGNING_MODE_RFC9421)

    def test_missing_pem_file_fails_closed(self, tmp_path):
        row = self._row(tmp_path)
        row.backend_ref = str(tmp_path / "does-not-exist.pem")
        with patch("src.core.database.repositories.TenantSigningCredentialRepository") as mock_repo_cls:
            mock_repo_cls.return_value.get_active.return_value = row
            with patch("src.core.database.database_session.get_db_session"):
                with pytest.raises(SigningConfigurationError, match="failed to read PEM"):
                    load_active_signing_credential(tenant_id="t1", signing_mode=SIGNING_MODE_RFC9421)

    def test_unsupported_jwk_fails_closed(self, tmp_path):
        row = self._row(tmp_path)
        row.public_jwk = {"kty": "RSA", "n": "..."}  # unsupported by AdCP webhook profile
        with patch("src.core.database.repositories.TenantSigningCredentialRepository") as mock_repo_cls:
            mock_repo_cls.return_value.get_active.return_value = row
            with patch("src.core.database.database_session.get_db_session"):
                with pytest.raises(SigningConfigurationError, match="unsupported JWK"):
                    load_active_signing_credential(tenant_id="t1", signing_mode=SIGNING_MODE_RFC9421)

    def test_successful_load_is_cached_per_tenant(self, tmp_path):
        # Second call for the same tenant must NOT re-open a DB session
        # (the LRU+TTL cache is the whole point of this optimization).
        row = self._row(tmp_path)
        with patch("src.core.database.repositories.TenantSigningCredentialRepository") as mock_repo_cls:
            mock_repo_cls.return_value.get_active.return_value = row
            with patch("src.core.database.database_session.get_db_session") as mock_db:
                load_active_signing_credential(tenant_id="t_cached", signing_mode=SIGNING_MODE_RFC9421)
                load_active_signing_credential(tenant_id="t_cached", signing_mode=SIGNING_MODE_RFC9421)
                # First call opens session; second is a pure cache hit.
                assert mock_db.call_count == 1

    def test_invalidate_drops_cached_snapshot(self, tmp_path):
        # After rotation, an explicit invalidate forces the next load
        # back to the DB so the freshly-rotated kid is picked up
        # immediately instead of waiting for TTL expiry.
        row = self._row(tmp_path)
        with patch("src.core.database.repositories.TenantSigningCredentialRepository") as mock_repo_cls:
            mock_repo_cls.return_value.get_active.return_value = row
            with patch("src.core.database.database_session.get_db_session") as mock_db:
                load_active_signing_credential(tenant_id="t_rotate", signing_mode=SIGNING_MODE_RFC9421)
                invalidate_credential_cache("t_rotate")
                load_active_signing_credential(tenant_id="t_rotate", signing_mode=SIGNING_MODE_RFC9421)
                assert mock_db.call_count == 2  # invalidation forced re-read

    def test_failed_load_is_not_cached(self, tmp_path):
        # Operators fixing config in real time must not see a stale
        # cached failure after the fix lands. Failures bubble up but
        # the cache stays empty.
        with patch("src.core.database.repositories.TenantSigningCredentialRepository") as mock_repo_cls:
            mock_repo_cls.return_value.get_active.return_value = None
            with patch("src.core.database.database_session.get_db_session") as mock_db:
                with pytest.raises(SigningConfigurationError):
                    load_active_signing_credential(tenant_id="t_fail", signing_mode=SIGNING_MODE_RFC9421)
                with pytest.raises(SigningConfigurationError):
                    load_active_signing_credential(tenant_id="t_fail", signing_mode=SIGNING_MODE_RFC9421)
                # Both calls must hit the DB — the failure is NOT cached.
                assert mock_db.call_count == 2

    def test_backend_ref_outside_keys_dir_fails_closed(self, tmp_path, monkeypatch):
        # Path-traversal guard: even an operator with DB-write access
        # cannot point backend_ref at /etc/shadow or any path outside
        # the configured signing-keys directory. Override the autouse
        # fixture's pointing-at-tmp_path with a dedicated subdirectory
        # so we can write a PEM "outside" it.
        keys_dir = tmp_path / "keys"
        keys_dir.mkdir()
        monkeypatch.setenv("WEBHOOK_SIGNING_KEYS_DIR", str(keys_dir))

        row = self._row(tmp_path)  # writes PEM at tmp_path/key.pem (outside keys/)
        # Sanity: the row's backend_ref is outside the keys_dir we just set.
        assert not Path(row.backend_ref).resolve().is_relative_to(keys_dir.resolve())

        with patch("src.core.database.repositories.TenantSigningCredentialRepository") as mock_repo_cls:
            mock_repo_cls.return_value.get_active.return_value = row
            with patch("src.core.database.database_session.get_db_session"):
                with pytest.raises(SigningConfigurationError, match="path traversal"):
                    load_active_signing_credential(tenant_id="t1", signing_mode=SIGNING_MODE_RFC9421)
