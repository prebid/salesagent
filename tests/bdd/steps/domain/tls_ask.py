"""Steps for the locally-added on-demand TLS gate feature (local-tls-ask.feature).

Every Then reads the status the proxy reads, off the response to the request the When sent.
The database-down Given is the fail-fast feature's own step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import given, parsers, then, when

if TYPE_CHECKING:
    from tests.harness.tls_gate import TlsGateEnv

#: How a When spells the host a Given declared, from that host and its tenant's subdomain.
_SPELLINGS = {
    "as declared": lambda host, _: host,
    "in upper case": lambda host, _: host.upper(),
    "with a trailing dot": lambda host, _: f"{host}.",
    "without the port": lambda host, _: host,
    "as its subdomain under the deployment": lambda _, subdomain: f"{subdomain}.sales-agent.example.com",
    "with a label in front of it": lambda host, _: f"x.{host}",
    "under another domain": lambda host, _: f"{host}.evil.example",
    "with a port": lambda host, _: f"{host}:443",
}


def _env(ctx: dict) -> TlsGateEnv:
    return ctx["env"]


def _declare(ctx: dict, **tenant_fields: object) -> None:
    ctx["declared_host"], ctx["declared_subdomain"] = _env(ctx).declare_host(**tenant_fields)


def _ask(ctx: dict, **params: str) -> None:
    ctx["gate_response"] = _env(ctx).ask_tls_gate(params)


def _assert_gate_status(ctx: dict, expected: int) -> None:
    response = ctx["gate_response"]
    assert response.status_code == expected, (
        f"the gate answered {response.status_code} where the proxy has to read {expected}: {response.text[:300]!r}"
    )


@given(parsers.parse('the deployment answers on the domain "{domain}"'))
def given_deployment_domain(ctx: dict, domain: str) -> None:
    _env(ctx).answer_on_domain(domain)


@given("an active tenant declares a host")
def given_active_tenant_host(ctx: dict) -> None:
    _declare(ctx)


@given(parsers.parse('an active tenant declares a host starting with the label "{label}"'))
def given_active_tenant_host_with_label(ctx: dict, label: str) -> None:
    _declare(ctx, label=label)


@given(parsers.parse("an active tenant declares a host with the port {port:d}"))
def given_active_tenant_host_with_port(ctx: dict, port: int) -> None:
    _declare(ctx, port=port)


@given("an inactive tenant declares a host")
def given_inactive_tenant_host(ctx: dict) -> None:
    _declare(ctx, is_active=False)


@given("an active tenant declares a host and stores a Gemini key that does not decrypt")
def given_tenant_host_with_undecryptable_key(ctx: dict) -> None:
    _declare(ctx, _gemini_api_key="not-a-fernet-token")


# A regex, because the malformed outline's empty row has to bind too.
@when(parsers.re(r'the proxy asks about "(?P<host>.*)"'))
def when_proxy_asks_about(ctx: dict, host: str) -> None:
    _ask(ctx, domain=host)


@when(parsers.parse("the proxy asks about that host {spelling}"))
def when_proxy_asks_about_declared_host(ctx: dict, spelling: str) -> None:
    _ask(ctx, domain=_SPELLINGS[spelling](ctx["declared_host"], ctx["declared_subdomain"]))


@when(parsers.parse("the proxy asks about a host of {length:d} characters"))
def when_proxy_asks_about_long_host(ctx: dict, length: int) -> None:
    labels = ".".join(["a" * 63] * (length // 64 + 1))
    _ask(ctx, domain=labels[:length])


@when("the proxy asks without naming a host")
def when_proxy_asks_about_nothing(ctx: dict) -> None:
    _ask(ctx)


@then("the gate allows the certificate")
def then_gate_allows(ctx: dict) -> None:
    _assert_gate_status(ctx, 200)


@then("the gate refuses the certificate")
def then_gate_refuses(ctx: dict) -> None:
    _assert_gate_status(ctx, 403)


@then("the gate fails without allowing the certificate")
def then_gate_fails(ctx: dict) -> None:
    _assert_gate_status(ctx, 500)
