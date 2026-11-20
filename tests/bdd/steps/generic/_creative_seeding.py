"""The one way a Given stores a buyer's creative and names it on the request's package.

Shared by the create-path creative steps (``uc002_creative_kind.py``) and the Google Ad Manager
seller steps (``gam_seller.py``). The per-representation assets come from
``tests/factories/creative_asset.py`` (``display_tag_spec``, ``html_spec``,
``html5_bundle_specs``, ``legacy_tag_url_spec``), so a representation is built once whichever
step names it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tests.bdd.steps.generic.given_media_buy import _add_creative_ids_to_package


def store_creative(
    ctx: dict,
    creative_id: str,
    assets: Mapping[str, Any],
    *,
    kind: str | None = None,
    fmt: str | None = None,
    status: str = "approved",
    principal: Any = None,
    **data: Any,
) -> Any:
    """Store a creative carrying *assets*, of canonical *kind* or in reference-catalog format *fmt*; return the row.

    The buyer's own unless *principal* names another; *data* adds root keys to its stored
    record (a sync's preview back-fill, a build's ``output_format``, the ad server's id).
    Committed, so every transport's request reads it.
    """
    from tests.factories.creative import CreativeFactory

    identity: dict[str, Any] = {"canonical_kind": kind} if kind is not None else {"format": fmt}
    creative = CreativeFactory(
        creative_id=creative_id,
        tenant=ctx["tenant"],
        principal=principal or ctx["principal"],
        status=status,
        data={"assets": dict(assets), **data},
        **identity,
    )
    ctx["env"]._commit_factory_data()
    return creative


def store_package_creative(
    ctx: dict,
    creative_id: str,
    assets: Mapping[str, Any],
    *,
    kind: str | None = None,
    fmt: str | None = None,
    status: str = "approved",
    **data: Any,
) -> None:
    """Store the buyer's creative (:func:`store_creative`) and name it in the request's package's creative_ids.

    It becomes the scenario's manifest creative, the one the format-satisfaction refusal
    Thens read a refusal's ``details.creative_id`` and ``problems[]`` against.
    """
    store_creative(ctx, creative_id, assets, kind=kind, fmt=fmt, status=status, **data)
    _add_creative_ids_to_package(ctx, [creative_id])
    ctx["manifest_creative_id"] = creative_id
