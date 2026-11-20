"""The one way a Given stores a buyer's creative and names it on the request's package.

Shared by every step module that seeds a stored creative for create_media_buy, update_media_buy
or an operator path (``given_media_buy.py``, ``uc002_creative_kind.py``, ``gam_seller.py``,
``uc002_invariants.py``, ``uc002_v31_request.py``, ``uc_format_satisfaction.py``). The
per-representation assets come from ``tests/factories/creative_asset.py``
(``display_tag_spec``, ``html_spec``, ``html5_bundle_specs``, ``legacy_tag_url_spec``,
``library_image_assets``, ``make_legacy_image_assets``), so a representation is built once
whichever step names it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tests.factories.creative import CreativeFactory


def store_creative(
    ctx: dict,
    creative_id: str | None,
    assets: Mapping[str, Any] | None,
    *,
    kind: str | None = None,
    fmt: str | None = None,
    option_ref: Mapping[str, str] | None = None,
    status: str = "approved",
    principal: Any = None,
    format_spec: Any = None,
    **data: Any,
) -> Any:
    """Store a creative carrying *assets*, of canonical *kind* or in reference-catalog format *fmt*; return the row.

    *creative_id* ``None`` takes the factory's own id. *assets* ``None`` stores a record with no
    ``assets`` member at all, a creative missing its required assets. *option_ref* narrows a
    canonical *kind* to one product option (core/format-option-ref.json). *status* is its review
    status. The buyer's own unless *principal* names another. *format_spec* is the declaration
    *fmt* resolved to when the creative was stored, for a format no catalog carries now (the
    factory's ``format_spec``). *data* adds root keys to its stored record (a sync's preview
    back-fill, a build's ``output_format``, the ad server's id). Committed, so every
    transport's request reads it.

    It becomes the scenario's manifest creative, the one the format-satisfaction refusal
    Thens read a refusal's ``details.creative_id`` and ``problems[]`` against.
    """
    identity: dict[str, Any] = (
        {"canonical_kind": kind, "format_option_ref_document": option_ref} if kind is not None else {"format": fmt}
    )
    record = {} if assets is None else {"assets": dict(assets)}
    creative = CreativeFactory(
        tenant=ctx["tenant"],
        principal=principal or ctx["principal"],
        status=status,
        data={**record, **data},
        **identity,
        **({} if format_spec is None else {"format_spec": format_spec}),
        **({} if creative_id is None else {"creative_id": creative_id}),
    )
    ctx["env"]._commit_factory_data()
    ctx["manifest_creative_id"] = creative.creative_id
    return creative


def store_package_creative(
    ctx: dict,
    creative_id: str | None,
    assets: Mapping[str, Any] | None,
    *,
    kind: str | None = None,
    fmt: str | None = None,
    option_ref: Mapping[str, str] | None = None,
    status: str = "approved",
    **data: Any,
) -> Any:
    """Store the buyer's creative (:func:`store_creative`), name it in the request's package's creative_ids; return the row."""
    # Imported here: given_media_buy imports this module, and owns the request the id joins.
    from tests.bdd.steps.generic.given_media_buy import _add_creative_ids_to_package

    creative = store_creative(
        ctx, creative_id, assets, kind=kind, fmt=fmt, option_ref=option_ref, status=status, **data
    )
    _add_creative_ids_to_package(ctx, [creative.creative_id])
    return creative
