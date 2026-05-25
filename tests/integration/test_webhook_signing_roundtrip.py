"""End-to-end signing roundtrip for the outbound webhook delivery path.

Slice 3 of the per-buyer-agent signing refactor (see
``docs/design/signing-non-embedded.md``). The unit suite covers
``build_auth_headers`` against a real verifier; this suite proves the
production code path stitches together end-to-end:

1. Real DB — :class:`PushNotificationConfig` row carries
   ``signing_mode='rfc9421'``; :class:`TenantSigningCredential` row
   carries the active webhook-signing PEM ref.
2. Real :class:`WebhookDeliveryService` — runs the actual
   ``send_delivery_webhook`` → ``_deliver_with_backoff`` path with the
   real ``webhook_signing.build_auth_headers`` helper hooked in.
3. ``httpx.Client`` is mocked so we capture exactly the bytes + headers
   the production code attempted to put on the wire.
4. Real :func:`adcp.signing.webhook_verifier.verify_webhook_signature`
   accepts those captured bytes + headers using the JWK we stored.

If any layer of the integration drifts (a future refactor pre-encodes
JSON differently between sign and send, drops the kid, swaps tags), the
verifier rejects the captured request and this test fails loudly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from adcp.signing.jwks import StaticJwksResolver
from adcp.signing.keygen import generate_signing_keypair
from adcp.signing.webhook_verifier import (
    WebhookVerifyOptions,
    verify_webhook_signature,
)

from src.services.webhook_signing import invalidate_credential_cache


@pytest.fixture(autouse=True)
def _isolate_credential_cache():
    """Module-level credential cache must not leak across tests — a
    prior test that loaded a credential for tenant T would mask a
    no-credential failure mode test for the same tenant T."""
    invalidate_credential_cache()
    yield
    invalidate_credential_cache()


@pytest.mark.requires_db
class TestWebhookSigningRoundtrip:
    """Webhook signed by WebhookDeliveryService verifies against the JWK
    we publish at /.well-known/jwks.json — proves end-to-end integration."""

    def test_rfc9421_signed_webhook_verifies(self, integration_db, tmp_path, monkeypatch):
        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
            TenantSigningCredentialFactory,
        )
        from tests.harness import CircuitBreakerEnv

        # Real keypair on disk — the WebhookDeliveryService loads it via
        # the local_pem backend exactly as it would in production. Point
        # the path-traversal guard at this test's tmp dir so the PEM
        # passes containment.
        monkeypatch.setenv("WEBHOOK_SIGNING_KEYS_DIR", str(tmp_path))
        pem_bytes, jwk = generate_signing_keypair(alg="ed25519", purpose="webhook-signing")
        pem_path = tmp_path / "webhook-key.pem"
        pem_path.write_bytes(pem_bytes)

        with CircuitBreakerEnv() as env:
            tenant = TenantFactory(tenant_id="t_signing")
            principal = PrincipalFactory(tenant=tenant, principal_id="p_signing")

            # Webhook config opts the buyer into RFC 9421 signing.
            webhook_url = "https://example.com/buyer-webhook/abcd"
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=webhook_url,
                signing_mode="rfc9421",
            )

            # Active webhook-signing credential. The factory's default
            # public_jwk is a dummy — override with the real one we just
            # generated so the verifier can see the matching kid.
            TenantSigningCredentialFactory(
                tenant=tenant,
                purpose="webhook-signing",
                backend="local_pem",
                backend_ref=str(pem_path),
                public_jwk=jwk,
                key_id=jwk["kid"],
                is_active=True,
            )

            env.set_http_response(200)
            service = env.get_service()

            ok = service.send_delivery_webhook(
                media_buy_id="mb_round_001",
                tenant_id="t_signing",
                principal_id="p_signing",
                reporting_period_start=datetime(2026, 5, 7, tzinfo=UTC),
                reporting_period_end=datetime(2026, 5, 7, 1, tzinfo=UTC),
                impressions=42_000,
                spend=1234.56,
            )
            assert ok is True

            # Pull the captured request off the mocked httpx.Client.post.
            post = env.mock["post"]
            assert post.call_count == 1
            call = post.call_args
            sent_url = call.args[0] if call.args else call.kwargs["url"]
            sent_body: bytes = call.kwargs["content"]
            sent_headers = call.kwargs["headers"]

            assert sent_url == webhook_url
            # RFC 9421 mode emits Signature/Signature-Input/Content-Digest
            # and DROPS the legacy HMAC header.
            assert "Signature" in sent_headers
            assert "Signature-Input" in sent_headers
            assert "Content-Digest" in sent_headers
            assert "X-ADCP-Signature" not in sent_headers
            # And the kid in Signature-Input matches the JWK we published.
            assert jwk["kid"] in sent_headers["Signature-Input"]

            # Hand the buyer-side verifier exactly what they would see:
            # the wire body + the headers + the JWKS we publish at
            # /.well-known/jwks.json. This is the contract.
            resolver = StaticJwksResolver(jwks={"keys": [jwk]})
            verified = verify_webhook_signature(
                method="POST",
                url=webhook_url,
                headers=sent_headers,
                body=sent_body,
                options=WebhookVerifyOptions(jwks_resolver=resolver),
            )
            assert verified.key_id == jwk["kid"]


@pytest.mark.requires_db
class TestWebhookSigningFailClosed:
    """When the buyer asks for ``signing_mode='rfc9421'`` but the tenant
    has no active credential, the delivery service must drop the webhook
    rather than send it unsigned."""

    def test_missing_credential_drops_webhook(self, integration_db):
        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            tenant = TenantFactory(tenant_id="t_missing_cred")
            principal = PrincipalFactory(tenant=tenant, principal_id="p_missing_cred")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url="https://example.com/buyer-webhook",
                signing_mode="rfc9421",
            )
            # NO TenantSigningCredentialFactory — that's the failure mode.

            env.set_http_response(200)
            service = env.get_service()

            ok = service.send_delivery_webhook(
                media_buy_id="mb_drop",
                tenant_id="t_missing_cred",
                principal_id="p_missing_cred",
                reporting_period_start=datetime(2026, 5, 7, tzinfo=UTC),
                reporting_period_end=datetime(2026, 5, 7, 1, tzinfo=UTC),
                impressions=1,
                spend=0.01,
            )

            # Service returns False AND never hit the wire — buyers that
            # opted into signed delivery must not receive unsigned bodies
            # silently.
            assert ok is False
            env.mock["post"].assert_not_called()

    def test_missing_credential_increments_misconfig_metric(self, integration_db):
        # Same setup as above, but assert the distinct metric counter
        # ticks — operators page on this signal, not on the generic
        # webhook_delivery_total{status='failure'}.
        from src.core.metrics import webhook_signing_misconfigured_total
        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        before = webhook_signing_misconfigured_total.labels(tenant_id="t_metric", signing_mode="rfc9421")._value.get()

        with CircuitBreakerEnv() as env:
            tenant = TenantFactory(tenant_id="t_metric")
            principal = PrincipalFactory(tenant=tenant, principal_id="p_metric")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url="https://example.com/buyer-webhook",
                signing_mode="rfc9421",
            )
            env.set_http_response(200)
            service = env.get_service()
            service.send_delivery_webhook(
                media_buy_id="mb_metric",
                tenant_id="t_metric",
                principal_id="p_metric",
                reporting_period_start=datetime(2026, 5, 7, tzinfo=UTC),
                reporting_period_end=datetime(2026, 5, 7, 1, tzinfo=UTC),
                impressions=1,
                spend=0.01,
            )

        after = webhook_signing_misconfigured_total.labels(tenant_id="t_metric", signing_mode="rfc9421")._value.get()
        assert after == before + 1, (
            f"webhook_signing_misconfigured_total should tick once for the "
            f"missing-credential failure (was {before}, became {after})"
        )
