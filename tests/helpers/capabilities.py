"""Wire assertion for the get_adcp_capabilities declared-honesty envelope (#1329).

``assert_declared_capabilities`` is the SINGLE grader for the fields
``_build_capabilities_response`` declares as honesty signals — the account section,
``adcp.idempotency``, and ``specialisms``. Every wire consumer (the BDD
``then_capabilities_*`` steps across a2a/mcp/rest/e2e_rest and the integration wire
test) routes through it, so "the builder emits field X" is COUPLED to "a wire grader
reads field X": the helper fails on an emitted-but-unasserted account/idempotency
field. Before this, a field could be added to the builder while its designated grader
stayed dark — which is exactly how ``adcp.idempotency`` was withdrawn from
``supported=true`` to ``supported=false`` on every transport with nothing on the wire
noticing (#1329 finding 1).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# Every field ``_build_account_capability`` emits on the wire. The completeness check
# below fails if the builder emits a field NOT in this set, forcing a wire assertion to
# be added here rather than the field shipping ungraded.
_ASSERTED_ACCOUNT_FIELDS = frozenset(
    {"sandbox", "require_operator_auth", "required_for_products", "account_financials", "supported_billing"}
)
# Every field ``_adcp_metadata`` emits under ``adcp.idempotency`` (the honest
# supported=False variant carries ONLY ``supported`` — no ``replay_ttl_seconds``).
_ASSERTED_IDEMPOTENCY_FIELDS = frozenset({"supported"})

# Every TOP-LEVEL field the capabilities response emits (minimal no-tenant path OR full path).
# The completeness check fails if the response grows a top-level field NOT in this set, forcing
# a wire assertion here rather than a new capability section shipping ungraded (#1329 finding 4).
# ``message`` / ``success`` are A2A transport framing (the A2A wire_response is the full artifact
# DataPart, stashed before the message/success strip — see tests/CLAUDE.md), not capability
# fields; they are enumerated so the cross-transport grade passes while a NEW capability field
# (absent from this set on every transport) still fails.
_GRADED_TOP_LEVEL_FIELDS = frozenset(
    {
        "adcp",
        "supported_protocols",
        "specialisms",
        "account",
        "media_buy",
        "last_updated",
        "status",
        "replayed",
        "message",
        "success",
    }
)

# The seller's honest default account-billable set (default tenant, no supported_billing
# configured) — the exact parties sync_accounts accepts (resolve_supported_billing).
_DEFAULT_BILLING = frozenset({"operator", "agent"})


def _declared_specialism_ids() -> list[str]:
    """The exact declared specialism ids, derived from the production audit table (SSOT).

    Pins the WIRE ``specialisms`` array by VALUE against what ``_SPECIALISM_AUDIT`` declares —
    catching a serialization regression that drops/mangles the array — while the audit GATE
    (``test_specialism_audit_gate``) independently enforces that the declared set is HONEST.
    Empty today (``sales-non-guaranteed`` withdrawn, #1329 finding 1).
    """
    from src.core.tools.capabilities import _DECLARED_SPECIALISMS

    return [s.value for s in _DECLARED_SPECIALISMS]


def _supported_protocol_ids() -> list[str]:
    """The exact supported-protocol ids, derived from the production SSOT."""
    from src.core.tools.capabilities import _SUPPORTED_PROTOCOLS

    return [p.value for p in _SUPPORTED_PROTOCOLS]


def assert_declared_capabilities(
    body: dict[str, Any],
    *,
    expected_billing: Iterable[str] = _DEFAULT_BILLING,
    expected_specialisms: list[str] | None = None,
    expected_protocols: list[str] | None = None,
) -> None:
    """Assert the honesty-declared capability fields on a serialized wire body.

    ``body`` is the serialized get_adcp_capabilities response (dict). Asserts:

    * TOP-LEVEL COMPLETENESS: the response carries no top-level field this helper does not
      grade — a new capability section must be graded here (#1329 finding 4);
    * ``supported_protocols`` equals the declared set BY VALUE (default: production SSOT);
    * ``specialisms`` equals the declared set BY VALUE (default: production audit table, empty
      today) — the exact set is graded on the WIRE, not pinned below it by a model_dump() unit
      test (#1329 finding 4); when non-empty, each id is unique kebab-case;
    * ``account.sandbox is False`` and ``require_operator_auth is False`` and
      ``required_for_products is False`` and ``account_financials is False`` (all honest
      until the corresponding behavior ships — #1329 gap 13);
    * ``account.supported_billing`` equals ``expected_billing`` (exact set);
    * ``adcp.idempotency.supported is False`` with NO ``replay_ttl_seconds`` (the honest
      Idempotency3 variant — #1329 finding 1/R9-F2);
    * ACCOUNT/IDEMPOTENCY COMPLETENESS: the emitted objects carry no field this helper does not
      assert — a new declared field must be graded here.
    """
    expected = set(expected_billing)
    expected_specialisms = _declared_specialism_ids() if expected_specialisms is None else expected_specialisms
    expected_protocols = _supported_protocol_ids() if expected_protocols is None else expected_protocols

    unknown_top = set(body) - _GRADED_TOP_LEVEL_FIELDS
    assert not unknown_top, (
        f"capabilities response emits top-level field(s) {sorted(unknown_top)} that "
        "assert_declared_capabilities does not grade — add a wire assertion here (#1329 finding 4)"
    )

    protocols = body.get("supported_protocols")
    assert protocols == expected_protocols, f"supported_protocols must be {expected_protocols}, got {protocols!r}"

    specialisms = body.get("specialisms")
    assert specialisms == expected_specialisms, f"specialisms must be {expected_specialisms}, got {specialisms!r}"
    assert len(specialisms) == len(set(specialisms)), f"specialisms must be unique, got {specialisms}"

    account = body.get("account")
    assert account is not None, f"capabilities response must include an account section: {body}"
    unasserted = set(account) - _ASSERTED_ACCOUNT_FIELDS
    assert not unasserted, (
        f"account emits field(s) {sorted(unasserted)} that assert_declared_capabilities does not grade — "
        "add a wire assertion here so the declaration is not shipped ungraded (#1329)"
    )
    assert account.get("sandbox") is False, f"account.sandbox must be an honest False, got {account.get('sandbox')!r}"
    assert account.get("require_operator_auth") is False, (
        f"account.require_operator_auth must be an honest False, got {account.get('require_operator_auth')!r}"
    )
    assert account.get("required_for_products") is False, (
        f"account.required_for_products must be an honest False, got {account.get('required_for_products')!r}"
    )
    assert account.get("account_financials") is False, (
        f"account.account_financials must be an honest False, got {account.get('account_financials')!r}"
    )
    billing = account.get("supported_billing")
    assert isinstance(billing, list), f"account.supported_billing must be a list on the wire, got {billing!r}"
    assert set(billing) == expected, f"account.supported_billing must be {sorted(expected)}, got {billing!r}"

    adcp = body.get("adcp") or {}
    idempotency = adcp.get("idempotency")
    assert idempotency is not None, f"adcp.idempotency must be declared (v3.1 required): {body}"
    unasserted_idem = set(idempotency) - _ASSERTED_IDEMPOTENCY_FIELDS
    assert not unasserted_idem, (
        f"adcp.idempotency emits field(s) {sorted(unasserted_idem)} that assert_declared_capabilities does not "
        "grade — add a wire assertion here (#1329)"
    )
    assert idempotency.get("supported") is False, (
        f"adcp.idempotency.supported must be an honest False (only create_media_buy dedups), "
        f"got {idempotency.get('supported')!r}"
    )
    assert "replay_ttl_seconds" not in idempotency, (
        f"adcp.idempotency must not carry replay_ttl_seconds when supported=False: {idempotency}"
    )
