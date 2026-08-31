"""Governance test helpers for sync_governance (UC-030 / #1329).

One builder / reader per production boundary the unit, integration, and BDD suites touch, so
the governance test contract is expressed once rather than N times (#1329):

- ``governance_request`` — the pinned ``SyncGovernanceRequest`` MODEL from a single-account
  shape (the unit / integration request-builder twins delegate here).
- ``account_entry`` — the pinned 3.1.1 request element ``{"account": <ref>, "governance_agents": [...]}``.
- ``governance_agent_dict`` — one request-side agent (url + write-only authentication).
- ``leaky_governance_agent`` — a request-side agent carrying ``LEAK_SECRET`` on a named
  credential-leak channel (the rejection envelope must never echo it).
- ``persisted_governance_urls`` — the below-wire persisted-binding read-back (session-safe).
- ``persisted_governance_agents_raw`` — the RAW stored JSON, bypassing url-only coercion
  (test-only, for the credential-strip grade).
- ``governance_binding_stub`` — a ``set_governance_binding`` side_effect mirroring the repo's
  PUBLIC url-only write contract (no coupling to the private projector).
- ``GOV_URL`` / ``DEFAULT_URL`` / ``BEARER_CREDS`` / ``LEAK_SECRET`` / ``normalize_url`` — the
  shared request constants, the leak secret, and the SDK ``AnyUrl`` normalization used to pin
  an echoed url by EXACT equality (the ``CoreGovernanceAgent.url`` echo is always normalized).

Account SEEDING stays in ``tests.helpers.accounts.seed_account_with_access`` (the canonical
seeder shared with every suite), never a governance-local twin.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adcp.types import CoreGovernanceAgent
from pydantic import AnyUrl

from tests.factories.principal import _UNSET

# Shared request-shape constants for the sync_governance test suites (#1329):
# one governance-agent url + Bearer credentials (>= the schema's minLength 32). Kept here so
# the unit / integration / BDD suites assert against one source of truth for the pinned
# 3.1.1 request shape rather than re-declaring these per file.
GOV_URL = "https://governance.pinnacle-media.com"
# A generic well-formed url for scenarios whose defect-under-test is elsewhere (missing auth,
# malformed idempotency_key, unresolvable account) — one shared default across all suites.
DEFAULT_URL = "https://governance.example.com"
BEARER_CREDS = "x" * 64
# A >= 32-char secret for the credential-leak negative channel. It must NEVER appear in the
# rejection envelope on the buyer wire; the exact value is asserted-absent, so it is unique.
# ONE constant shared by the BDD and integration leak suites — two copies with different
# lengths let a length-sensitive redaction regression redden one and not the other (#1329).
LEAK_SECRET = "S3cr3t-must-not-leak-" + "0" * 40


def normalize_url(url: str) -> str:
    """Return ``url`` as the SDK ``AnyUrl`` renders it (the form the wire echoes).

    ``CoreGovernanceAgent.url`` is an ``AnyUrl`` (Pattern #1 SDK type), so the persisted and
    echoed url is ALWAYS the normalized string (a bare host gains a trailing ``/``). Pinning an
    echoed url against ``normalize_url(expected)`` with EXACT ``==`` is strictly stronger than the
    old trailing-slash-tolerant comparator — a wire that is not AnyUrl-normalized now FAILS
    instead of being tolerated (#1329).
    """
    return str(AnyUrl(url))


def governance_agent_dict(
    url: str,
    *,
    cred_len: int = 64,
    credentials: str | None = None,
    scheme: str = "Bearer",
    **overrides: Any,
) -> dict[str, Any]:
    """Build a request-side governance agent (``url`` + write-only ``authentication``).

    Credentials default to ``cred_len`` ``x``s (>= the schema's minLength 32) so the
    only thing under test is the account/authority path, not request validation.

    DEVIATION VOCABULARY (#1329): a boundary/negative case is expressed as a DELTA
    against the pinned shape, not a hand-rolled copy. Each ``**overrides`` key REPLACES a
    top-level agent key (e.g. ``authentication={...}`` for a schemes boundary), and the
    ``_UNSET`` sentinel (the repo's factory idiom) REMOVES one (``url=_UNSET`` for the
    url-absent boundary) — so a boundary suite states the one thing that deviates and a schema
    change propagates by construction instead of drifting across N inlined copies.
    """
    creds = credentials if credentials is not None else "x" * cred_len
    agent: dict[str, Any] = {"url": url, "authentication": {"schemes": [scheme], "credentials": creds}}
    if url is _UNSET:
        # ``url=_UNSET`` REMOVES the url key (the url-absent boundary) — the docstring's promise,
        # now honored for the ``url`` positional itself, not only for ``**overrides`` keys. Before
        # this, ``url=_UNSET`` stored the sentinel object AS the url, so ``_bva_agent`` had to
        # re-inline ``del agent["url"]`` to express the boundary (#1329).
        agent.pop("url")
    for key, value in overrides.items():
        if value is _UNSET:
            agent.pop(key, None)
        else:
            agent[key] = value
    return agent


def governance_request(
    *,
    account_ref: dict[str, Any] | None = None,
    url: str = GOV_URL,
    idempotency_key: str = "uuid-v4-shared-00000000000000001",
    accounts: list[dict[str, Any]] | None = None,
    **agent_kwargs: Any,
) -> Any:
    """Build a ``SyncGovernanceRequest`` from a single-account shape (or an explicit list).

    The ONE home for the pinned request MODEL the unit and integration suites construct (the
    ``_make_request`` / ``_request`` twins now delegate here), so a schema change to the
    request wrapper propagates by construction instead of drifting across per-file builders
    (#1329). ``url`` is a KEYWORD forwarded to the shared ``governance_agent_dict``
    (which shares the ONE ``_UNSET`` sentinel — ``url=_UNSET`` removes it, extra ``agent_kwargs``
    like ``credentials=`` shape the single agent); ``accounts`` overrides the single-account
    default with an explicit multi-account list.
    """
    from src.core.schemas.account import SyncGovernanceRequest

    if accounts is None:
        accounts = [
            account_entry(account_ref or {"account_id": "acc_1"}, agents=[governance_agent_dict(url, **agent_kwargs)])
        ]
    return SyncGovernanceRequest(idempotency_key=idempotency_key, accounts=accounts)


def governance_agent(url: str, **kwargs: Any) -> Any:
    """A request-side governance agent as the parsed MODEL (``SyncGovernanceRequestAgent``).

    For callers that must pass the parsed model, not a dict — e.g. the repo write path
    ``AccountRepository.set_governance_binding``, whose narrowed signature takes
    ``list[SyncGovernanceRequestAgent]`` only (#1329). Delegates the shape to
    ``governance_agent_dict`` so the pinned request contract has one home.
    """
    from adcp.types.generated_poc.account.sync_governance_request import (
        GovernanceAgent as SyncGovernanceRequestAgent,
    )

    return SyncGovernanceRequestAgent(**governance_agent_dict(url, **kwargs))


def leaky_governance_agent(channel: str, *, secret: str = LEAK_SECRET) -> dict[str, Any]:
    """A request-side governance agent carrying ``secret`` on the named credential channel.

    The rejection envelope must NEVER echo ``secret``. One home for the two leak-channel
    shapes and the exact mistyped-key name, so the BDD (transport-blind) and integration
    (A2A+REST) leak suites grade the same contract with the SAME secret — two hand-rolled
    copies with different-length secrets let a length-sensitive redaction regression redden
    one suite and not the other (#1329). Channels:

    * ``url-userinfo`` — credential embedded in the url userinfo; rejected by the userinfo
      gate, whose message sanitizes the url so the secret is never rendered.
    * ``extra-authentication-key`` — ``credential`` (singular) is NOT in the Authentication
      schema → ``extra_forbidden``; the credential-bearing value is redacted at the boundary.
    """
    if channel == "url-userinfo":
        return governance_agent_dict(f"https://svc:{secret}@governance.example.com/hook")
    if channel == "extra-authentication-key":
        agent = governance_agent_dict(DEFAULT_URL)
        agent["authentication"]["credential"] = secret
        return agent
    raise ValueError(f"Unknown credential channel: {channel!r}")


def account_entry(account_ref: dict[str, Any], *, agents: list[dict[str, Any]]) -> dict[str, Any]:
    """Build one sync_governance request element: the pinned 3.1.1 request wrapper.

    ``account_ref`` passes straight through — an id form (``{"account_id": ...}``) or a
    natural-key form (``{"brand": {...}, "operator": ..., "sandbox": ...}``). Single home for
    the request wrapper so the unit / integration / BDD suites stop re-encoding it per call
    site (#1329).
    """
    return {"account": account_ref, "governance_agents": agents}


def persisted_governance_urls(tenant_id: str, account_id: str) -> list[str]:
    """Read the persisted governance-agent urls for an account, session-lifetime-safe.

    Opens a fresh ``AccountUoW`` on the same DB the dispatch committed to and extracts the url
    strings INSIDE the block — ORM attributes expire on commit, so reading them after the
    session closes raises ``DetachedInstanceError`` (the drift the BDD copy guarded and the
    integration / sync_accounts copies did not). Returns ``[]`` when the account row is absent
    or unbound, so callers grade against a plain list of persisted urls (#1329).
    """
    from src.core.database.repositories.uow import AccountUoW

    with AccountUoW(tenant_id) as uow:
        account = uow.accounts.get_by_id(account_id)
        if account is None:
            return []
        return [str(a.url) for a in (account.governance_agents or [])]


def persisted_governance_agents_raw(tenant_id: str, account_id: str) -> list[dict[str, Any]] | None:
    """Read the RAW stored ``governance_agents`` JSON, bypassing JSONType url-only coercion.

    Casts the column to plain ``JSONB`` so the persisted bytes come back verbatim: a
    credential-bearing row is returned as-is instead of raising on read. Reading through the
    typed ORM attribute (``persisted_governance_urls`` above) re-validates each element
    against the url-only column model and would RAISE on a leaked credential — masking the
    exact leak the strip test grades, so this deliberately defeats the Layer-3 coercion
    boundary. TEST-ONLY: it lives here, not as a production repository method a future caller
    could reach to read unvalidated on-disk bytes (#1329). Opens its own tenant-scoped
    ``AccountUoW`` on the DB the dispatch committed to and reads INSIDE the block.
    """
    import warnings

    from sqlalchemy import cast, select
    from sqlalchemy.dialects.postgresql import JSONB

    from src.core.database.models import Account
    from src.core.database.repositories.uow import AccountUoW

    with AccountUoW(tenant_id) as uow, warnings.catch_warnings():
        # uow.session is the sanctioned test accessor (its deprecation targets production
        # business logic, not test-only raw reads); silence the notice locally.
        warnings.simplefilter("ignore", DeprecationWarning)
        return uow.session.scalar(
            select(cast(Account.governance_agents, JSONB)).where(
                Account.tenant_id == tenant_id,
                Account.account_id == account_id,
            )
        )


def governance_binding_stub() -> Callable[[str, list[Any]], list[CoreGovernanceAgent]]:
    """A ``set_governance_binding`` side_effect mirroring the repo's PUBLIC write contract.

    Projects each request agent to the persisted url-only SDK record —
    ``CoreGovernanceAgent(url=<url>)`` with credentials stripped and the url ``AnyUrl``-normalized
    (trailing slash), which is the documented return type of
    ``AccountRepository.set_governance_binding`` (#1329: the repo returns the SDK
    ``CoreGovernanceAgent``, not a bare dict). Constructing the SDK type directly — not the
    module-private ``_serialize_governance_agents`` projector — grades the tool's echo against the
    repository's public contract, not the internal the repo-owned design exists to hide.
    """

    def _side_effect(account_id: str, agents: list[Any]) -> list[CoreGovernanceAgent]:
        return [
            CoreGovernanceAgent(url=(agent["url"] if isinstance(agent, dict) else agent.url))
            for agent in (agents or [])
        ]

    return _side_effect
