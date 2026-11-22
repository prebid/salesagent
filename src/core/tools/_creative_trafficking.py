"""A buy's creatives, read once per package for trafficking, and the refusals that reading raises.

The service create, the approval replay and the retroactive push call before an ad server
is written to. :func:`_validate_creatives_before_adapter_call` is the gate: it finds each
creative a buy names, asks each package's product whether it accepts the creative, and reads
the creative for that package through the one reader (``read_creative_to_traffic``). Its
answer is the carriers keyed by ``(package_id, creative_id)``, which the adapter's
pre-booking check, its booking and its upload are all handed. For an adapter that uploads
html5 bundles, :func:`_with_html5_bundles` then puts each bundle's bytes on its carrier, before
anything is booked. sync_creatives and update_media_buy reach it through
``push_creative_to_existing_buy``.

Interim, folded into RFC #2529's ``plan(buy)``.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from adcp.types.legacy import LegacyPackageRequest as AdcpPackageRequest

from src.core.creative_to_traffic import TrafficRefusal, read_creative_to_traffic
from src.core.database.repositories.creative import CreativeRepository
from src.core.errors.codes import ErrorCode
from src.core.errors.details import (
    CreativeRefDetails,
    ErrorProblem,
    InvalidStateDetails,
    ValidationDetails,
)
from src.core.exceptions import (
    AdCPCreativeNotFoundError,
    AdCPGoneError,
    AdCPSalesAgentError,
    AdCPValidationError,
    problems_refusal,
)
from src.core.helpers.creative_helpers import unidentified_url_asset_reason
from src.core.logging_config import log_safe
from src.core.schemas import CreativeFormatIdentity, MediaPackage, Package, PackageRequest
from src.core.security.outbound_http import CounterpartyUrl, OutboundDeliveryFailed, UrlProvenance, asend
from src.core.traffic_carrier import CreativeToTraffic, Html5BundleRepresentation
from src.core.validation_helpers import PACKAGES_FIELD, package_field_path

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from src.adapters.base import AdServerAdapter
    from src.core.database.models import CreativeAssignment
    from src.core.database.models import MediaPackage as DBMediaPackage
    from src.core.database.repositories.product import ProductRepository

logger = logging.getLogger(__name__)


#: ``(package_id, creative_id)``: one creative as it is read for one package.
CarrierKey = tuple[str, str]


@dataclass(frozen=True)
class _AssignedPackage:
    """A package as the creative gate reads it: its id, its product and the creatives assigned to it."""

    package_id: str
    product_id: str | None
    creative_ids: list[str]


def _get_creative_ids(
    package: AdcpPackageRequest | PackageRequest | Package | MediaPackage | _AssignedPackage,
) -> list[str] | None:
    """Safely get creative_ids from a package (backward compatibility).

    The creative_ids field is a local extension added to PackageRequest for
    backward compatibility. It may not exist on library types, so we use
    getattr to safely access it.

    Args:
        package: Package or PackageRequest object

    Returns:
        List of creative IDs if present, None otherwise
    """
    return getattr(package, "creative_ids", None)


def _named_creative_ids(
    packages: Iterable[AdcpPackageRequest | PackageRequest | Package | MediaPackage | _AssignedPackage],
) -> list[str]:
    """Every creative id the packages name, in request order (duplicates kept)."""
    return [cid for package in packages for cid in _get_creative_ids(package) or []]


def _get_format_spec_sync(agent_url: str, format_id: str, *, provenance: UrlProvenance | None = None) -> Any | None:
    """Get format specification synchronously from the async registry.

    Thin delegate kept for its patch surface (tests/harness envs patch this
    name). The behavior — typed AdCPSalesAgentError propagates (transient agent failures
    keep their recovery semantics, #1430: transient-error taxonomy fix), untyped errors log and
    become None (unknown-format) — lives in the SINGLE shared fetch path,
    format_resolver.fetch_format_spec (#1430 review).

    ``provenance`` carries BUYER provenance for a stored creative's ``agent_url``
    (it came out of the buyer's own prior sync_creatives call, not this
    deployment's configuration) — a ``CounterpartyUrl`` routes a refusal through
    the seam's counterparty-aware path (VALIDATION_ERROR/correctable) instead of
    the operator path (CONFIGURATION_ERROR/terminal), UNLESS the url happens to
    also be a real tenant-registered operator agent, which stays terminal
    regardless (salesagent-ypgd).
    """
    from src.core.format_resolver import fetch_format_spec

    return fetch_format_spec(agent_url, format_id, provenance=provenance)


def _package_creatives(
    products: "ProductRepository",
    packages: "Sequence[MediaPackage] | Sequence[_AssignedPackage]",
    creatives_list: "Sequence[Any]",
) -> list[tuple[int, Any, list[Any]]]:
    """``(package index, product row, creative rows)`` for every package naming creatives and a stored product.

    The pairing both creative questions on the create path are asked over: whether each
    creative satisfies its package's product (:func:`_pairing_verdicts`), and whether the
    package's creatives together cover every included placement of that product
    (``format_satisfaction.require_covered``). Read once, so the two cannot pair a creative
    with different products. A requested id with no row is left out; the existence gate
    refuses it before either question is asked.
    """
    product_ids_needed = {
        package.product_id for package in packages if _get_creative_ids(package) and package.product_id
    }
    if not product_ids_needed:
        return []

    products_by_id = {product.product_id: product for product in products.list_by_ids(list(product_ids_needed))}
    creatives_by_id = {creative.creative_id: creative for creative in creatives_list}

    pairings: list[tuple[int, Any, list[Any]]] = []
    # Indexed rather than ``enumerate``d: enumerate over a union of two sequence types joins
    # their element types to a base that has no ``product_id``.
    for index in range(len(packages)):
        package = packages[index]
        pkg_creative_ids = _get_creative_ids(package)
        product = products_by_id.get(package.product_id) if package.product_id else None
        if not pkg_creative_ids or product is None:
            continue
        pairings.append((index, product, [creatives_by_id[cid] for cid in pkg_creative_ids if cid in creatives_by_id]))
    return pairings


def _pairing_verdicts(pairings: "Sequence[tuple[int, Any, list[Any]]]") -> list[tuple[int, Any, Any, Any]]:
    """``(package index, product row, creative row, verdict)`` for every creative of every pairing, in order.

    Each verdict is a ``FormatSatisfaction``: whether the product accepts the creative, and the
    declaration the route resolved to, which names the creative's asset ids and states its
    sizes. One verdict per pairing, because a creative on several packages may meet a different
    product on each.
    """
    from src.core.format_satisfaction import creative_satisfies

    return [
        (index, product, creative, creative_satisfies(creative, product.format_contract))
        for index, product, creatives in pairings
        for creative in creatives
    ]


def _verdict_at(pairing_verdicts: "Sequence[tuple[int, Any, Any, Any]]") -> dict[tuple[int, str], Any]:
    """Each verdict keyed by ``(package index, creative_id)``, as :func:`_read_for_packages` looks it up."""
    return {(index, str(creative.creative_id)): verdict for index, _product, creative, verdict in pairing_verdicts}


def _assigned_packages(
    packages: Sequence["DBMediaPackage"], assignments: Sequence["CreativeAssignment"]
) -> list[_AssignedPackage]:
    """One package per assignment, each with the product its stored row was booked on and the assigned creative."""
    product_ids = {package.package_id: (package.package_config or {}).get("product_id") for package in packages}
    return [_AssignedPackage(a.package_id, product_ids[a.package_id], [a.creative_id]) for a in assignments]


def _validate_creatives_before_adapter_call(
    packages: "Sequence[MediaPackage] | Sequence[_AssignedPackage]",
    tenant_id: str,
    principal_id: str,
    session: "Session",
) -> dict[CarrierKey, CreativeToTraffic]:
    """Validate all creatives have required fields BEFORE calling adapter.

    This prevents GAM order creation when creatives are invalid, enabling
    true all-or-nothing behavior without rollback complexity.

    Fetches format specifications to determine correct asset structure per format.

    Args:
        packages: List of Package objects with creative_ids
        tenant_id: Tenant ID for database lookup
        principal_id: Owning principal — the creatives PK is composite
            (creative_id, tenant_id, principal_id), so the existence gate must
            match the full key: another principal's creative resolves to
            "not found" (uniform, no field leak) instead of passing the gate
            on their row and violating the composite FK on assignment insert.
        session: SQLAlchemy session (from UoW).

    Returns:
        Each creative as :func:`_read_for_packages` read it for each package naming it, keyed
        by ``(package_id, creative_id)``: what the adapter's pre-booking check is handed, and
        what the create upload and the approval replay hand the adapter.

    Raises:
        AdCPSalesAgentError: a creative is not found, is in a terminal state, carries
            nothing an ad server can serve, or its package's product refuses its format.
    """
    all_creative_ids = set(_named_creative_ids(packages))

    if not all_creative_ids:
        # No creatives to validate
        return {}

    # Fetch all creatives in one principal-scoped query
    creatives_list = CreativeRepository(session, tenant_id).get_by_ids(list(all_creative_ids), principal_id)

    # Referenced creative_ids that don't exist are rejected up front so BOTH
    # approval paths fail identically before anything is persisted (the pending
    # path previously skipped missing ids and returned a pending success).
    found_creative_ids = {str(c.creative_id) for c in creatives_list}
    missing_ids = all_creative_ids - found_creative_ids
    if missing_ids:
        error_msg = f"Creative IDs not found: {', '.join(sorted(missing_ids))}"
        logger.error(log_safe(error_msg))
        # 3.1.1 enums/error-code.json: "Sellers MUST return this code uniformly for any
        # creative_id not owned by the calling account." The deferral that stood here
        # cited the BR-UC-003 ext-i cell as grading CREATIVE_REJECTED; that cell asks for
        # CREATIVE_NOT_FOUND, so the FIXME was resolved against a reading of the grader
        # that the grader did not support.
        raise AdCPCreativeNotFoundError(
            details=CreativeRefDetails(missing_creative_ids=sorted(missing_ids)),
            field=PACKAGES_FIELD,
        )

    # --- Format satisfaction, RESOLVED FIRST because the read needs its answer ---
    #
    # ``satisfies`` returns the declaration the route RESOLVED TO, and that declaration is
    # what names the creative's asset ids and states the sizes it is trafficked at:
    # ``core/product-format-declaration.json`` lets a product REPLACE the kind's slot list
    # outright, so a product that renames a media slot names an id the kind's vocabulary
    # does not contain, and the read below cannot find the media at all without it.
    #
    # COMPUTED HERE AND ACTED ON LATER, in two halves on purpose. The verdicts are needed
    # before the read; the REFUSALS they imply must still fire after it, because the order
    # in which this function raises is itself graded -- a terminal-state creative answers
    # GONE ahead of any format refusal, and that precedence would invert if the refusals
    # moved up with the computation. One computation, consumed twice.
    from src.core.database.repositories.product import ProductRepository

    package_creatives = _package_creatives(ProductRepository(session, tenant_id), packages, creatives_list)
    pairing_verdicts = _pairing_verdicts(package_creatives)
    verdict_at = _verdict_at(pairing_verdicts)

    validation_errors = []
    # Terminal-state creatives are collected separately: the pinned enum codes
    # "operation is not permitted for the resource's current status" as INVALID_STATE,
    # a different condition from the field/format failures below, and update_media_buy
    # already splits them the same way (_validate_creatives_for_assignment).
    bad_state: list[Any] = []
    # Each creative as it is trafficked on each package naming it, read once here: the
    # adapter's pre-booking check and its upload are handed these, and nothing reads the rows
    # again for either.
    carriers: dict[CarrierKey, CreativeToTraffic] = {}
    for creative in creatives_list:
        # BR-RULE-026: Reject creatives in terminal error states
        if hasattr(creative, "status") and creative.status in ("error", "rejected"):
            bad_state.append(creative)
            continue

        # The ONE reader, on every package naming the creative, with the declaration that
        # package's product resolved to. A generative creative is read like any other: its
        # build output is what is served, and no build output is nothing to serve. A creative
        # its product refuses is not read; that refusal is raised below, in order.
        read = _read_for_packages(creative, packages, verdict_at)
        if isinstance(read, str):
            validation_errors.append(read)
        else:
            carriers.update(read)

    if bad_state:
        # The STATE per creative, not a joined sentence naming the ids: per-creative
        # outcomes are per-ENTITY problems. Same shape and same code as the
        # update_media_buy gate, so one condition reads identically on both tools.
        raise AdCPGoneError(
            details=InvalidStateDetails(
                problems=[
                    ErrorProblem(subject_type="creative", subject_id=c.creative_id, rejected_value=c.status)
                    for c in bad_state
                ]
            ),
            field=PACKAGES_FIELD,
        )

    # The refusals those verdicts imply, acted on HERE rather than where they were computed.
    #
    # DO NOT MOVE THIS UP TO JOIN ITS COMPUTATION. The ``bad_state`` raise above answers a
    # terminal-state creative GONE, and it sits between the computation and this block.
    # Hoisting these refusals would let a creative that is BOTH terminal-state and
    # format-refused answer UNSUPPORTED_FEATURE instead — the wrong answer, because what a
    # buyer can act on for a creative in ``error``/``rejected`` is its STATE, not the
    # product's format set.
    #
    # AND NOTHING GRADES THAT ORDER, which is why this comment is here rather than a test
    # being here. Measured, not assumed: hoisting this block above the ``bad_state`` raise
    # and running tests/bdd/test_uc002_create_media_buy.py plus tests/unit/test_media_buy.py
    # reddens NOTHING (3 failed / 350 passed, and the 3 are a pre-existing account-sandbox
    # family that fails at the baseline too). So the inversion is silent today. The missing
    # scenario — one creative that is both terminal-state and outside its product's set — is
    # filed; until it exists, this paragraph is the only thing standing in the way.
    from src.core.format_satisfaction import creative_refusal

    refusals: list[tuple[str, AdCPSalesAgentError[Any]]] = []
    for index, product, creative, verdict in pairing_verdicts:
        if verdict:
            continue
        cid = creative.creative_id
        if not verdict.creative_coded:
            # A legacy format the product does not take at all: collected with the other
            # per-creative business-rule problems and answered VALIDATION_ERROR below,
            # which is what @T-UC-002-ext-p grades.
            validation_errors.append(f"Creative {cid}: {verdict.summary} (product {product.product_id})")
            continue
        # A CANONICAL refusal is not a business-rule problem to collect -- the
        # product published a closed set and this creative is outside it, which the
        # pin codes UNSUPPORTED_FEATURE, or its manifest (or its legacy reference's
        # parameters) fails what it selects, which the pin codes CREATIVE_SIZE_MISMATCH
        # or CREATIVE_VALIDATION_FAILED_GENERIC. Refused ahead of ``reasons`` because the
        # code differs: folding it into ``reasons`` would answer the buyer
        # VALIDATION_ERROR for a condition with its own recovery.
        # One pointer for every creative refusal, the gate's and the adapter's pre-booking
        # check's (_adapter_refusal): the creative where the request named it. core/error.json
        # ``field`` is the "Field path associated with the error", and enums/error-code.json asks
        # for this one where it says anything (CREATIVE_SIZE_MISMATCH "Sellers SHOULD identify
        # the offending creative in error.field").
        refusal = creative_refusal(
            verdict, field=_creative_path(packages, index, cid), creative_id=cid, product_id=product.product_id
        )
        refusals.append((cid, refusal))
    if refusals:
        # EVERY refused creative, in one error: a buy with two misfits names both, rather than
        # the first only and the second after the buyer resends.
        raise refusals[0][1] if len(refusals) == 1 else _one_refusal(refusals)

    if validation_errors:
        error_msg = (
            "Cannot create media buy with invalid creatives. "
            "The following creatives have validation errors:\n" + "\n".join(f"  • {err}" for err in validation_errors)
        )
        logger.error(f"[PRE-VALIDATION] {error_msg}")
        # A stored creative carrying nothing an ad server can serve, or a format the
        # product does not accept, "violates business rules beyond schema validation"
        # (3.1.1 enums/error-code.json) -- VALIDATION_ERROR. Not CREATIVE_REJECTED,
        # which that enum defines as a content-policy review failure and shapes as
        # {policy_id, policy_url, reasons}; no policy review runs on this path.
        raise AdCPValidationError(
            details=ValidationDetails(reasons=validation_errors),
            field=PACKAGES_FIELD,
        )

    # core/product.json format_options: "the selected creative set MUST cover every included
    # placement; uncovered inventory MUST be rejected or refined, never silently omitted".
    # Asked LAST, once every creative has passed on its own, because coverage is a property
    # of the package's set: a creative the product refuses outright is that creative's
    # failure, reported above with its own code. A creative named on a create package carries
    # no placement_ids, so each is "eligible for all placements" (sync-creatives-request.json).
    from src.core.format_satisfaction import require_covered

    for index, product, creatives in package_creatives:
        require_covered(
            [(creative, None) for creative in creatives],
            product,
            field=PACKAGES_FIELD,
            product_id=product.product_id,
            package_index=index,
        )
    return carriers


def _read_for_packages(
    creative: Any,
    packages: "Sequence[MediaPackage] | Sequence[_AssignedPackage]",
    verdict_at: Mapping[tuple[int, str], Any],
) -> dict[CarrierKey, CreativeToTraffic] | str:
    """*creative* as it is trafficked on each package naming it, keyed by ``(package_id, creative_id)``; or the problem.

    The one place a stored creative is read for trafficking (``read_creative_to_traffic``):
    the pre-adapter gate calls it for the create and the approval replay, and the retroactive
    push for the packages it pushes to. Each read takes the declaration that package's product
    resolved to, so a creative on two packages is read twice, once for each product, and
    nothing downstream unions their sizes.

    One rule for an unresolved declaration, on every path: a package whose product resolved
    nothing is read with no declaration only for a legacy creative, whose format spec states
    its size. A canonical creative states its size through the product's declaration alone
    (core/product-format-declaration.json: a fixed constraint "a buyer request or creative
    manifest MUST declare and satisfy"), so on such a package it is refused rather than read
    as "any size". A package whose product refuses the creative is skipped: its caller raises
    that refusal.

    The creative's format spec is resolved here, once per read, by one rule for every path: a
    retained legacy reference at a dialled agent is fetched from that agent, and one the agent
    does not serve is refused unless the canonical mapping registry projects it.
    """
    from src.core.format_resolver import is_dialled_agent_url, registry_projects

    cid = str(creative.creative_id)
    # Get format specification from creative agent (uses in-memory cache with 30min TTL).
    # Skip the fetch entirely for an adapter-provided pseudo-URL (e.g.
    # broadstreet://<tenant_id>): those formats are served by the adapter
    # in-process, so no dialled agent could ever resolve them, and the
    # unconditional fetch below would reject a format the seller itself
    # advertised (salesagent-ypgd). CounterpartyUrl marks BUYER provenance for
    # a genuinely-dialled url — see _get_format_spec_sync's docstring. No
    # field: create-media-buy-request.json defines no creative:{id} path, so
    # there is no canonical request-document locator to name (a fabricated
    # one is not honest — CounterpartyUrl(field=None) states that plainly).
    # The stored identity, through the ONE row reader. The retained legacy assertion is
    # what names a format an AGENT serves; a creative identified by its canonical kind
    # asserts no reference, so there is no agent to dial and nothing here to check.
    identity = CreativeFormatIdentity.from_row(creative)
    assertion = identity.assertion
    format_spec = None
    if assertion is not None and is_dialled_agent_url(assertion.agent_url):
        format_spec = _get_format_spec_sync(
            assertion.agent_url,
            assertion.id,
            provenance=CounterpartyUrl(field=None),
        )

        # Fail validation if format spec not found (no skipping!) — only
        # meaningful once we know the agent SHOULD have it (a dialled url). A
        # reference the canonical mapping registry projects is a real format whether
        # or not the agent's deprecated catalogue lists it (template-format-ids.mdx
        # § Compatibility, v3.2.7), the rule sync_creatives applies on the way in.
        if not format_spec and not registry_projects(assertion):
            return (
                f"Creative {cid} has unknown format '{assertion.id}' "
                f"from agent {assertion.agent_url}. Format must be registered with the creative agent."
            )

    # A url asset this seller cannot identify is NOT a refusal. core/assets/url-asset.json
    # at the governing version ends "receivers MUST NOT silently pick a mechanism." and
    # stops there; the "they SHOULD reject the manifest" clause that followed it at 3.1.1
    # was REMOVED by 3.2.3. A version that drops a remedy is declining to require it, so
    # refusing here would make this seller stricter than the spec on a buyer-facing path,
    # and the costs are not symmetric: reject the manifest and the buyer loses the buy,
    # return no clickthrough and they lose a clickthrough.
    #
    # LOGGED, NOT CARRIED, and that is a gap rather than a choice. The advisory lane
    # (docs/design/error-architecture.md) rides a response's declared ``errors[]``, and
    # create-media-buy-response.json declares that array on its Error and Submitted arms
    # only -- CreateMediaBuySuccess has none, so the lane does not reach here. Its
    # ``warnings[]`` array IS the right carrier by its own words ("non-blocking
    # observations accompanying this successful creation ... Buyers SHOULD surface
    # recognized codes operationally"), but enums/warning-code.json is closed at three
    # members and none of them means "a url asset I could not identify" -- emitting one
    # would misfile the fact under a code that means something else. So the sentence goes
    # to the log until that code exists upstream; the wiring is filed.
    unidentified = unidentified_url_asset_reason(creative.data or {}, format_spec, creative.creative_id)
    if unidentified:
        logger.warning(log_safe(f"[ASSET] {unidentified}"))
    carriers: dict[CarrierKey, CreativeToTraffic] = {}
    for index in range(len(packages)):
        package = packages[index]
        if cid not in (_get_creative_ids(package) or []):
            continue
        verdict = verdict_at.get((index, cid))
        if verdict is not None and not verdict:
            continue
        if verdict is None and identity.assertion is None:
            return (
                f"Creative {cid} (format={identity.label}): no product declaration resolved "
                f"for package {package.package_id}"
            )
        declaration = verdict.declaration if verdict is not None else None
        read = read_creative_to_traffic(
            cid,
            creative.data or {},
            identity.kind,
            format_spec=format_spec,
            declarations=(declaration,) if declaration is not None else (),
        )
        if isinstance(read, TrafficRefusal):
            return f"Creative {cid} (format={identity.label}) cannot be trafficked: {read.reason}"
        carriers[(package.package_id, cid)] = read
    return carriers


def _creative_position(package: Any, creative_id: str) -> int:
    """Where *package* names *creative_id* in its ``creative_ids``."""
    return (_get_creative_ids(package) or []).index(creative_id)


def _creative_path(packages: Sequence[Any], index: int, creative_id: str) -> str:
    """``packages[index].creative_ids[j]``: where the request named *creative_id* on its package *index*.

    The one pointer a creative refusal names, from the create gate and from the adapter's
    pre-booking check alike.
    """
    return package_field_path(f"creative_ids[{_creative_position(packages[index], creative_id)}]", index)


def _one_refusal(refusals: Sequence[tuple[str, AdCPSalesAgentError[Any]]]) -> AdCPSalesAgentError[Any]:
    """One error naming every ``(creative_id, refusal)`` the gate refused, each by its own problems.

    The problems of every refusal, through the one selection (``problems_refusal``); a
    refusal stating none (a closed-set refusal, an ambiguous selection) is one problem under
    its own code. The pointer is the first refusal's.
    """
    problems = [
        problem
        for creative_id, refusal in refusals
        for problem in (
            (refusal.details.problems if refusal.details else None)
            or [ErrorProblem(code=refusal.error_code, subject_type="creative", subject_id=creative_id)]
        )
    ]
    return problems_refusal(problems, field=refusals[0][1].field)


def _adapter_refusal(
    problems: "Sequence[ErrorProblem]", named_packages: Sequence[MediaPackage]
) -> AdCPSalesAgentError[Any]:
    """The error pre-booking problems are answered with, pointing at the first creative refused.

    The adapter's check, or core's html5 bundle fetch (:func:`_with_html5_bundles`), classifies
    (each problem names its creative and the package it was refused on); core builds the error,
    through the one selection the gate uses, and points it where the request named the creative.
    """
    index_of = {package.package_id: index for index, package in enumerate(named_packages)}
    refused = next(
        (p for p in problems if p.subject_type == "creative" and p.subject_id and p.package_id in index_of), None
    )
    field = None
    if refused is not None:
        assert refused.subject_id is not None and refused.package_id is not None
        field = _creative_path(named_packages, index_of[refused.package_id], refused.subject_id)
    return problems_refusal(problems, field=field)


async def _with_html5_bundles(
    adapter: "AdServerAdapter",
    carriers: dict[CarrierKey, CreativeToTraffic],
    named_packages: Sequence[MediaPackage],
) -> dict[CarrierKey, CreativeToTraffic]:
    """*carriers*, each html5 bundle carrying its zip's bytes when *adapter* uploads them; or the refusal.

    *carriers* come back as they are when none is an html5 bundle or *adapter* uploads none.

    The create, the approval replay and the retroactive push call this before the ad server is
    written to, so a bundle the seller cannot fetch is refused before anything is booked and the
    upload does no I/O. Each bundle URL is fetched once, through the egress seam, as the buyer's
    URL; a URL the seam refuses outright raises the seam's own refusal. A bundle the seam could
    not deliver is a problem on each creative and package naming it:
    adcp/_schemas/3.2/enums/error-code.json CREATIVE_VALIDATION_FAILED_GENERIC, "The creative
    failed buyer-correctable validation, but the producer cannot classify the failure with a more
    specific standard code ... Recovery: correctable". The problem carries no text, so the
    buyer-facing message names no vendor and no internal id, as the same entry requires. The
    error points where *named_packages* name the first one (:func:`_adapter_refusal`).
    """
    urls = {c.representation.url for c in carriers.values() if isinstance(c.representation, Html5BundleRepresentation)}
    if not urls or not adapter.capabilities.uploads_html5_bundles:
        return carriers
    bundles = {url: await _fetched_bundle(url) for url in urls}
    filled: dict[CarrierKey, CreativeToTraffic] = {}
    problems: list[ErrorProblem] = []
    for (package_id, creative_id), carrier in carriers.items():
        representation = carrier.representation
        if isinstance(representation, Html5BundleRepresentation):
            bundle = bundles[representation.url]
            if bundle is None:
                problems.append(
                    ErrorProblem(
                        code=ErrorCode.CREATIVE_VALIDATION_FAILED_GENERIC,
                        subject_type="creative",
                        subject_id=creative_id,
                        package_id=package_id,
                    )
                )
                continue
            carrier = carrier.model_copy(
                update={"representation": representation.model_copy(update={"bundle": bundle})}
            )
        filled[(package_id, creative_id)] = carrier
    if problems:
        raise _adapter_refusal(problems, named_packages)
    return filled


async def _fetched_bundle(url: str) -> bytes | None:
    """The zip at the buyer's *url*, or None when the seam could not deliver it."""
    try:
        return (await asend(url, method="GET", provenance=CounterpartyUrl())).content
    except OutboundDeliveryFailed as failure:
        logger.warning(log_safe(f"[HTML5] Bundle {url} could not be fetched: {failure}"))
        return None


def _carriers_by_package_id(
    packages: Sequence[MediaPackage], carriers: Mapping[CarrierKey, CreativeToTraffic]
) -> dict[str, tuple[CreativeToTraffic, ...]]:
    """The gate's carriers grouped by package id, as the adapter's pre-booking check takes them."""
    return {
        package.package_id: tuple(carrier for (pid, _cid), carrier in carriers.items() if pid == package.package_id)
        for package in packages
    }


def _pre_validate_package_creatives(
    packages: "Sequence[MediaPackage] | Sequence[_AssignedPackage]",
    tenant_id: str,
    principal_id: str,
    ctx_manager: Any,
    step: Any,
) -> dict[CarrierKey, CreativeToTraffic]:
    """Run pre-adapter creative validation inside its own UoW, and return the creatives it read.

    Shared by the auto-approval and manual-approval create paths so the same
    buyer input is rejected identically (CREATIVE_REJECTED) regardless of the
    tenant's approval mode, BEFORE any media-buy state is persisted (POST-F1).
    Marks the workflow step failed on rejection. Duck-typed over packages:
    only ``package.package_id``, ``_get_creative_ids(package)`` and ``package.product_id``
    are used.
    """
    from src.core.database.repositories import MediaBuyUoW

    try:
        with MediaBuyUoW(tenant_id) as pre_validate_uow:
            # FIXME(#1119): creative validation should use a repository
            assert pre_validate_uow.session is not None
            return _validate_creatives_before_adapter_call(
                packages, tenant_id, principal_id, session=pre_validate_uow.session
            )
    except AdCPSalesAgentError:
        # Validation failed - creative validation errors already logged
        # Update workflow step as failed and re-raise
        if step:
            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message="Creative validation failed")
        raise


def _read_for_push(
    products: "ProductRepository", creative: Any, assigned: Sequence[_AssignedPackage]
) -> dict[CarrierKey, CreativeToTraffic]:
    """*creative* read for each package it is pushed to, by the rules the pre-adapter gate applies.

    Raises:
        The creative refusal (``creative_refusal``) when a package's product refuses it.
        AdCPValidationError when it cannot be read for a package (:func:`_read_for_packages`),
            the code the gate answers the same problem with.
    """
    from src.core.format_satisfaction import creative_refusal

    pairing_verdicts = _pairing_verdicts(_package_creatives(products, assigned, [creative]))
    for _index, product, _creative, verdict in pairing_verdicts:
        if not verdict:
            raise creative_refusal(verdict, creative_id=creative.creative_id, product_id=product.product_id)
    read = _read_for_packages(creative, assigned, _verdict_at(pairing_verdicts))
    if isinstance(read, str):
        raise AdCPValidationError(details=ValidationDetails(reasons=[read]))
    return read
