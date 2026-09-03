"""Unit tests for webhook security features (SSRF protection)."""

import pytest
from adcp.types import TaskType

from src.core.webhook_validator import (
    WEBHOOK_TASK_TYPE_FALLBACK,
    WebhookURLValidator,
    validate_webhook_task_type,
)


class TestValidateWebhookTaskType:
    """Coercion of untrusted action labels to SDK-accepted TaskType values."""

    @pytest.mark.parametrize("valid", [m.value for m in TaskType])
    def test_valid_tasktype_returned_unchanged(self, valid):
        """Every TaskType enum member passes through verbatim."""
        assert validate_webhook_task_type(valid) == valid

    @pytest.mark.parametrize(
        "invalid",
        # media_buy_delivery is now a valid TaskType member (adcp 6.6 / spec 3.1.1), so it no
        # longer coerces to the fallback — dropped from the invalid-label set.
        ["delivery_report", "unknown", "", "not_a_task"],
    )
    def test_non_tasktype_coerced_to_fallback(self, invalid):
        """Non-members are coerced to the default fallback."""
        assert validate_webhook_task_type(invalid) == WEBHOOK_TASK_TYPE_FALLBACK
        assert WEBHOOK_TASK_TYPE_FALLBACK == "update_media_buy"

    def test_custom_fallback_honored(self):
        """The fallback is overridable for callers with a different default."""
        assert validate_webhook_task_type("bogus", fallback="sync_creatives") == "sync_creatives"

    def test_fallback_must_be_valid_caller_choice(self):
        """A valid label ignores the fallback entirely."""
        assert validate_webhook_task_type("sync_creatives", fallback="update_media_buy") == "sync_creatives"


class TestWebhookURLValidator:
    """Test SSRF protection in webhook URL validation.

    WebhookURLValidator is the REGISTRATION-time gate: validate_webhook_url_registration
    performs no DNS resolution, while the outbound/SEND-time gate lives on the egress seam
    (see the outbound-fetch tests). Both roles are graded here and there respectively.

    The ADDRESS cases below use ``https://`` so the address verdict is what they
    grade: the scheme is checked first (egress.policy._scheme_error), so a plain-http
    fixture here would be refused for its scheme under the default posture and the
    ``10.0.0.0/8``-style assertions would stop grading the address policy at all.
    The scheme decision itself is graded on its own, in
    ``TestWebhookSchemeGateTracksTheEgressSeam``.

    POSTURE IS PINNED, and that is load-bearing. ``tests/conftest.py`` sets
    ``ADCP_TESTING=true`` autouse for the whole suite, and under it the gate
    hands any loopback verdict to ``_maybe_allow_localhost``, which RESCUES it.
    An address case left on the ambient posture would therefore grade the
    testing-mode allowance instead of the address policy it claims to grade —
    and the two loopback cases would flip from refused to accepted outright
    (verified: removing the fixture below fails exactly those two).
    The allowance is real behaviour and is graded on purpose, in
    ``TestLocalhostAllowanceUnderTestingMode`` below; here it is switched off so
    these cases mean what their names say.
    """

    @pytest.fixture(autouse=True)
    def _no_testing_mode(self, monkeypatch):
        """Grade the address policy, not the testing-mode loopback allowance."""
        monkeypatch.delenv("ADCP_TESTING", raising=False)

    def test_valid_public_https_url(self):
        """Valid public HTTPS URLs should pass."""
        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("https://example.com/webhook")
        assert is_valid
        assert error == ""

    def test_blocks_localhost(self):
        """Should block localhost."""
        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("https://localhost:3000/webhook")
        assert not is_valid

    def test_blocks_127_0_0_1(self):
        """Should block 127.0.0.1, with a message that does not name the range."""
        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("https://127.0.0.1:8080/webhook")
        assert not is_valid

    def test_blocks_private_network_10(self):
        """Should block 10.0.0.0/8 private network, with a message that does not name the range."""
        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("https://10.0.0.5/webhook")
        assert not is_valid

    def test_blocks_private_network_192(self):
        """Should block 192.168.0.0/16 private network, with a message that does not name the range."""
        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("https://192.168.1.1/webhook")
        assert not is_valid

    def test_blocks_private_network_172(self):
        """Should block 172.16.0.0/12 private network, with a message that does not name the range."""
        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("https://172.16.0.1/webhook")
        assert not is_valid

    def test_blocks_link_local(self):
        """Should block 169.254.0.0/16 link-local (AWS metadata service), naming no range."""
        # Use a non-hostname-allowlist IP so the CIDR path is graded (169.254.169.254
        # is also in BLOCKED_HOSTNAMES and short-circuits before network match).
        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("https://169.254.1.1/webhook")
        assert not is_valid

    def test_blocks_aws_metadata_hostname(self):
        """Literal metadata IP hostname is blocked by hostname allowlist."""
        is_valid, error = WebhookURLValidator.validate_webhook_url_registration(
            "https://169.254.169.254/latest/meta-data"
        )
        assert not is_valid

    def test_blocks_metadata_hostname(self):
        """Should block cloud metadata hostnames."""
        is_valid, error = WebhookURLValidator.validate_webhook_url_registration(
            "https://metadata.google.internal/webhook"
        )
        assert not is_valid

    def test_requires_http_or_https(self):
        """Should reject non-HTTP protocols.

        No hatch to parametrize over anymore (GH #1802 deleted the
        scheme hatch entirely) — there is only one posture, https-required,
        and ftp:// is refused under it exactly like every other posture that
        used to exist.
        """
        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("ftp://example.com/webhook")
        assert not is_valid

    def test_requires_hostname(self):
        """Should reject URLs without hostname."""
        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("https:///webhook")
        assert not is_valid

    def test_invalid_url_format(self):
        """Should reject malformed URLs."""
        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("not-a-url")
        assert not is_valid

    def test_blocks_cgnat_range(self):
        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("https://100.64.1.1/webhook")
        assert not is_valid

    def test_blocks_multicast_range(self):
        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("https://224.0.0.1/webhook")
        assert not is_valid

    def test_blocks_ipv6_multicast_range(self):
        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("https://[ff02::1]/")
        assert not is_valid

    def test_blocks_nat64_well_known_prefix(self):
        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("https://[64:ff9b::a9fe:a9fe]/")
        assert not is_valid


class TestLocalhostAllowanceUnderTestingMode:
    """The loopback allowance is a real behaviour, so it is graded on BOTH arms.

    ``validate_webhook_url_registration`` hands its verdict to
    ``_maybe_allow_localhost``, which rescues a loopback refusal when
    ``ADCP_TESTING`` is set. That branch is what lets an e2e capture server on
    127.0.0.1 register at all — live production behaviour, and until now asserted
    NOWHERE: the only cases that touched it graded the NEGATIVE (that the
    allowance must not also rescue a bad SCHEME), and the two tests that reached
    it directly went through ``validate_for_testing``, which this change deletes.

    Both arms are pinned explicitly because the suite sets ``ADCP_TESTING=true``
    autouse: a one-armed test here would silently grade whichever posture the
    fixture happened to leave behind, which is exactly how a gate control goes
    inert without anyone noticing.

    ``LOOPBACK_URLS`` are https (GH #1802): the scheme hatch this class
    used to open no longer exists — ``_maybe_allow_localhost`` checks the
    SCHEME first and refuses to rescue a scheme failure, so an e2e capture
    server on loopback must register a REAL https URL now (matching
    ``tests/e2e/_webhook_capture_loopback.py``'s loopback-covered TLS front —
    the hermetic (no-Docker-stack) capture path, GH #1802). This
    class grades the ADDRESS allowance specifically, decoupled from scheme —
    which colliding with the scheme gate would otherwise obscure.
    """

    LOOPBACK_URLS = ["https://localhost:3001/webhook", "https://127.0.0.1:8080/webhook"]

    @pytest.mark.parametrize("url", LOOPBACK_URLS)
    def test_loopback_refused_without_testing_mode(self, monkeypatch, url):
        """Without ADCP_TESTING a loopback URL is refused, allowance or not."""
        monkeypatch.delenv("ADCP_TESTING", raising=False)

        is_valid, error = WebhookURLValidator.validate_webhook_url_registration(url)

        assert not is_valid, f"{url} must be refused when the testing-mode allowance is off"

    @pytest.mark.parametrize("url", LOOPBACK_URLS)
    def test_loopback_allowed_under_testing_mode(self, monkeypatch, url):
        """With ADCP_TESTING the allowance rescues the loopback verdict.

        This is the arm with no prior coverage. Verified to fail the moment
        ``_maybe_allow_localhost`` stops rescuing — without it, deleting that
        branch would break only the e2e stack, far from the change that caused it.
        """
        monkeypatch.setenv("ADCP_TESTING", "true")

        is_valid, error = WebhookURLValidator.validate_webhook_url_registration(url)

        assert is_valid, f"{url} must be accepted under ADCP_TESTING so a loopback capture server can register"
        assert error == ""

    def test_allowance_does_not_rescue_a_private_range(self, monkeypatch):
        """The allowance is loopback-only — a private range stays refused under it.

        Carried over from the deleted ``validate_for_testing`` coverage: the
        rescue re-derives loopback-ness structurally from the URL (see
        ``_maybe_allow_localhost``), so a 192.168/16 address — not loopback —
        must survive it. Without this, widening the rescue predicate would go
        unnoticed. https so the assertion below grades the ADDRESS message
        specifically, not the (now unconditional) scheme refusal.
        """
        monkeypatch.setenv("ADCP_TESTING", "true")

        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("https://192.168.1.1/webhook")

        assert not is_valid, "the loopback allowance must not rescue a private-range address"


class TestWebhookSchemeGateTracksTheEgressSeam:
    """Ingest requires https on exactly the condition the SEND seam does.

    ``src/core/security/egress/policy.py`` ``_scheme_error`` refuses anything but
    ``https://`` — unconditionally now (GH #1802 deleted the
    ``ADCP_OUTBOUND_ALLOW_INSECURE`` hatch entirely). Ingest used to require
    https only when ``is_production() and not ADCP_TESTING``, so in every
    non-production or ADCP_TESTING process a buyer's ``http://`` webhook URL was
    ACCEPTED at registration and then refused at every single send — a permanent,
    silent non-delivery, with no error at the one moment the buyer could have fixed
    the URL. These cases pin the two gates to one condition: always require https.

    ``test_plain_http_accepted_when_hatch_open`` — the case whose entire premise
    was "opening the hatch admits plain http" — is DELETED: that behaviour no
    longer exists anywhere, in any posture. The remaining cases keep their real
    assertions (http always rejected, https always accepted, ingest/seam still
    agree) with the now-nonexistent hatch axis dropped from each parametrize grid.
    """

    HTTP_URL = "http://buyer.example.com/hook"
    HTTPS_URL = "https://buyer.example.com/hook"

    # The one surviving gate. There were two until gh-#1589 deleted the
    # send-side twin (``validate_webhook_url``), which had no production callers
    # and existed only as a patch target. Kept as a list because the scheme
    # decision is a property of the GATE, not of this one method — if a second
    # ingest gate ever appears it belongs here rather than in a copy of these
    # cases. Resolves no DNS, so it stays hermetic.
    _GATES = [
        WebhookURLValidator.validate_webhook_url_registration,
    ]

    @pytest.mark.parametrize("gate", _GATES, ids=lambda g: g.__name__)
    @pytest.mark.parametrize("environment", ["production", "development"])
    @pytest.mark.parametrize("adcp_testing", ["true", None], ids=["adcp_testing", "no_adcp_testing"])
    def test_plain_http_rejected_in_every_posture(self, monkeypatch, gate, environment, adcp_testing):
        """Plain http is refused in EVERY posture — there is no hatch left to open it.

        Parametrised across ENVIRONMENT and ADCP_TESTING precisely because the
        scheme verdict must not depend on either of them — that dependency was
        the original drift. ADCP_TESTING still buys a localhost/loopback
        allowance, but it does not buy plaintext: the two concerns stay separate.
        """
        monkeypatch.setenv("ENVIRONMENT", environment)
        if adcp_testing is None:
            monkeypatch.delenv("ADCP_TESTING", raising=False)
        else:
            monkeypatch.setenv("ADCP_TESTING", adcp_testing)

        is_valid, error = gate(self.HTTP_URL)

        assert not is_valid, f"{gate.__name__} accepted a plain-http webhook URL the send seam refuses"

    def test_adcp_testing_localhost_allowance_does_not_reopen_plain_http(self, monkeypatch):
        """The loopback allowance and the scheme rule are separate concerns.

        ``ADCP_TESTING=true`` rescues a localhost/loopback ADDRESS verdict
        (``_maybe_allow_localhost``). It must not rescue the SCHEME verdict —
        collapsing the two would put http back in exactly the processes where
        the old bug lived. A capture server on loopback needs a real https URL
        now (see ``tests.e2e._webhook_capture_loopback``'s loopback-covered
        TLS front, GH #1802).
        """
        monkeypatch.setenv("ADCP_TESTING", "true")

        is_valid, error = WebhookURLValidator.validate_webhook_url_registration("http://localhost:3001/hook")

        assert not is_valid

    @pytest.mark.parametrize("environment", ["production", "development"])
    def test_https_registration_accepted_in_every_posture(self, monkeypatch, environment):
        """https is always admissible, in every posture — there is no hatch to relax or tighten it."""
        monkeypatch.setenv("ENVIRONMENT", environment)
        monkeypatch.delenv("ADCP_TESTING", raising=False)

        is_valid, error = WebhookURLValidator.validate_webhook_url_registration(self.HTTPS_URL)

        assert is_valid
        assert error == ""

    def test_ingest_and_seam_agree_on_the_scheme_verdict(self, monkeypatch):
        """The two GATES reach the same scheme verdict, via two call paths.

        Before GH #1802, ingest (webhook_validator) and the
        dial-time seam (outbound_http) each maintained their own scheme
        check, which could drift. Both now call
        ``EgressPolicy.check_registration`` / ``EgressPolicy.resolve_for_dial``
        respectively, and both of THOSE call the same shared, private
        ``_scheme_error`` — but asserting that fact by calling
        ``_scheme_error`` directly proves nothing: any gate can be made to
        "agree" with the very predicate it happens to call. This drives the
        REAL registration gate and the REAL dial gate, so a future edit that
        forks either off the shared predicate (or mis-wires it) reddens here.

        DNS resolution is stubbed to always succeed — this test grades the
        SCHEME verdict, not address policy, and ``resolve_for_dial`` checks
        scheme before ever resolving, so the stub is only reached for the
        accepted (``https``) case.
        """
        from urllib.parse import urlparse

        from src.core.security.egress.policy import EgressPolicy, OutboundRequestBlocked

        monkeypatch.setattr(
            "src.core.security.egress.policy.resolve_and_validate_host",
            lambda url, **kwargs: (urlparse(url).hostname, "93.184.216.34", 443),
        )

        for url in (self.HTTP_URL, self.HTTPS_URL):
            ingest_admits = WebhookURLValidator.validate_webhook_url_registration(url)[0]
            try:
                EgressPolicy.resolve_for_dial(url)
                dial_admits = True
            except OutboundRequestBlocked:
                dial_admits = False
            assert ingest_admits == dial_admits, (
                f"ingest and the dial-time seam disagree on {url}: ingest={ingest_admits}, dial={dial_admits}"
            )


class TestLogSanitizerCannotForgeALine:
    """``webhook_url_for_log`` output is always ONE physical line.

    CodeQL 1003 (``policy.py:287``): ``sanitize_webhook_url_for_log`` rebuilt the
    URL with the PATH verbatim, and ``urlsplit`` strips only ``\\t \\r \\n``
    (``urllib.parse._UNSAFE_URL_BYTES_TO_REMOVE``). VT, FF, the separators, NEL,
    U+2028 and U+2029 survived it, so a buyer-supplied path forged a second log
    line at every one of the sanitizer's callers.

    The set is DERIVED from ``str.splitlines()`` rather than restated, so a
    character Python starts treating as a boundary is covered here the day it is,
    instead of the day someone remembers to extend a literal.
    """

    # Every character str.splitlines() splits on, found by asking it.
    LINE_BREAKING = [chr(c) for c in range(0x110000) if len(f"a{chr(c)}b".splitlines()) > 1]

    def test_the_derived_set_is_not_empty(self):
        """Guards the guard: an empty set would make the sweep below vacuous."""
        assert len(self.LINE_BREAKING) >= 8

    def test_no_line_breaking_character_survives_into_a_log_line(self):
        from src.core.webhook_validator import webhook_url_for_log

        offenders = [
            (hex(ord(ch)), out)
            for ch in self.LINE_BREAKING
            if len((out := webhook_url_for_log(f"https://localhost/ho{ch}ok")).splitlines()) > 1
        ]
        assert offenders == [], f"these forge a second log line: {offenders}"

    def test_the_path_is_still_readable_when_it_is_innocent(self):
        """Escaping must not mangle an ordinary path -- the operator still needs it."""
        from src.core.webhook_validator import webhook_url_for_log

        assert webhook_url_for_log("https://buyer.example/hooks/delivery") == ("https://buyer.example/hooks/delivery")
