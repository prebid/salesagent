"""The bdd_e2e stack probe must tell "no stack" apart from "broken TLS".

salesagent-tgzb design step 7, promoted into scope by the disease scan.

``tests/bdd/conftest.py``'s ``e2e_stack`` fixture health-checks the stack and,
today, ends in ``except Exception: return None``. Returning ``None`` means "the
stack is absent", and the suite responds by running the in-process transports
against the PLAINTEXT config. So a certificate-verification failure — a broken
test rig — is indistinguishable from a stack that was never started, and an
https scenario grades the http branch while reporting green.

That is precisely the vacuity salesagent-tgzb exists to remove, reintroduced by
its own plumbing, which is why the fix is graded here rather than assumed.

The network is the ONLY thing stubbed: ``httpx.get`` is replaced with a raiser
that produces the exact exception shapes a real failure produces (including the
``httpx.ConnectError`` **wrapping** an ``ssl.SSLCertVerificationError`` that
httpx actually raises on an untrusted certificate). Everything else — the
fixture's own branching and its return value — is the real thing.
"""

from __future__ import annotations

import ssl

import httpx
import pytest

from tests.bdd.conftest import e2e_stack

# The fixture's underlying function. Calling it directly is what lets the probe's
# failure classification be asserted at all: as a session fixture it would be
# resolved once per session, against whatever stack happens to be up.
probe_stack = e2e_stack.__wrapped__

_BASE_URL = "https://probe-host.adcp.test:8443"
_POSTGRES_URL = "postgresql://adcp_user:secure_password_change_me@postgres:5432/adcp"

# A raised diagnostic that does not name the CA bundle sends the reader looking
# for a dead server instead of a misconfigured trust store.
_CA_MARKERS = ("e2e_ca_bundle", "ca bundle", "ca_bundle", "cacert", "ca.pem")


def _raiser(exc: BaseException):
    def _get(*_args, **_kwargs):
        raise exc

    return _get


def _configured_env(monkeypatch) -> None:
    """Point the probe at a stack address and pin off the per-worker rewrite."""
    monkeypatch.setenv("E2E_BASE_URL", _BASE_URL)
    monkeypatch.setenv("E2E_POSTGRES_URL", _POSTGRES_URL)
    monkeypatch.setenv("E2E_CA_BUNDLE", "/app/.test-tls/ca.pem")
    # The per-worker branch rewrites base_url from PYTEST_XDIST_WORKER; this
    # suite runs under xdist, so switch that branch off deliberately rather than
    # letting the worker id decide what the probe probes.
    monkeypatch.delenv("E2E_PER_WORKER", raising=False)


def _ssl_cause(exc: BaseException) -> BaseException | None:
    """The ``ssl.SSLError`` reachable from *exc* through the exception chain."""
    seen: list[int] = []
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.append(id(current))
        if isinstance(current, ssl.SSLError):
            return current
        current = current.__cause__ or current.__context__
    return None


def _cert_verify_error() -> ssl.SSLCertVerificationError:
    return ssl.SSLCertVerificationError(
        1,
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate",
    )


def _connect_error_wrapping_ssl() -> httpx.ConnectError:
    """What httpx actually raises when the leaf is not signed by a trusted CA."""
    wrapped = httpx.ConnectError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate"
    )
    wrapped.__cause__ = _cert_verify_error()
    return wrapped


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(httpx.ConnectError("[Errno 111] Connection refused"), id="connection-refused"),
        pytest.param(httpx.ConnectTimeout("timed out"), id="connect-timeout"),
        pytest.param(httpx.ReadTimeout("timed out"), id="read-timeout"),
    ],
)
def test_a_genuine_connection_failure_still_reports_the_stack_as_absent(monkeypatch, failure):
    """Unchanged behaviour: nothing is listening, so the e2e transports stand down.

    This half must keep working, or every non-e2e BDD run on a developer machine
    with no Docker stack turns into an error instead of running the in-process
    transports.
    """
    _configured_env(monkeypatch)
    monkeypatch.setattr(httpx, "get", _raiser(failure))

    assert probe_stack() is None, (
        f"{type(failure).__name__} with no SSL cause means the stack is not up; the probe must "
        f"report absence so the in-process transports still run"
    )


@pytest.mark.parametrize(
    "failure_factory",
    [
        pytest.param(_cert_verify_error, id="bare-ssl-error"),
        pytest.param(_connect_error_wrapping_ssl, id="connect-error-wrapping-ssl"),
    ],
)
def test_a_tls_verification_failure_is_raised_and_never_reported_as_an_absent_stack(monkeypatch, failure_factory):
    """A broken trust store is a broken rig, and must stop the run.

    If this returns ``None``, the fixture hands back the plaintext config and an
    https scenario grades the http branch while reporting green — the single
    failure mode salesagent-tgzb was opened to eliminate.
    """
    _configured_env(monkeypatch)
    failure = failure_factory()
    monkeypatch.setattr(httpx, "get", _raiser(failure))

    # The exception TYPE is the implementer's choice; what is pinned is that the
    # probe does not answer "absent", and that the diagnostic survives.
    try:
        outcome = probe_stack()
    except BaseException as caught:  # noqa: BLE001 - deliberately catching whatever the probe raises
        exc: BaseException = caught
    else:
        pytest.fail(
            f"the probe returned {outcome!r} for a certificate-verification failure — 'stack absent' and "
            f"'TLS misconfigured' must not be the same answer, or an https scenario grades the http branch "
            f"while reporting green"
        )

    message = str(exc)
    assert _BASE_URL in message, (
        f"the raised error must name the URL whose handshake failed so the failure is diagnosable; got {message!r}"
    )
    assert any(marker in message.lower() for marker in _CA_MARKERS), (
        f"the raised error must name the CA bundle — without it the reader hunts a dead server instead of a "
        f"misconfigured trust store; got {message!r}"
    )
    assert _ssl_cause(exc) is not None, (
        f"the underlying ssl.SSLError must stay reachable through the exception chain (raise ... from exc); "
        f"got {exc!r} with cause {exc.__cause__!r}"
    )


def test_an_unrecognised_failure_is_not_quietly_converted_into_an_absent_stack(monkeypatch):
    """No bare ``except``: only failures the probe UNDERSTANDS may become ``None``.

    ``except Exception: return None`` classifies a typo in the probe itself, a
    JSON error, or a bug in httpx as "no stack" — the same silent degradation by
    a different route.
    """
    _configured_env(monkeypatch)
    boom = ValueError("probe bug: base_url was built wrong")
    monkeypatch.setattr(httpx, "get", _raiser(boom))

    with pytest.raises(ValueError, match="probe bug"):
        probe_stack()


def test_an_unset_base_url_still_reports_the_stack_as_absent(monkeypatch):
    """The one honest ``None``: no address was configured, so there is nothing to probe."""
    monkeypatch.delenv("E2E_BASE_URL", raising=False)
    monkeypatch.delenv("E2E_PER_WORKER", raising=False)

    assert probe_stack() is None
