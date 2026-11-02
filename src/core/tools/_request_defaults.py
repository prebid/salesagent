"""One rule for turning builder parameters into request-model keyword arguments.

THE RULE: a parameter that is ``None`` means the buyer did not send that field, so the key
is OMITTED and the model's own declared default applies. Forwarding ``None`` explicitly
does not mean "unset" -- it OVERWRITES the default with a null.

Four builders got this wrong and the cost was not theoretical. Every field they defeated
had its default re-established BY HAND at the read site instead:

    accounts.py            bool(req.dry_run) / bool(req.delete_missing)
    creatives/_sync.py     bool(req.dry_run) and enum_value(req.validation_mode) or "strict"
    media_buy_list.py      truthiness on req.include_snapshot, three times

Five copies of a default that the schema already declares, each free to drift from the
schema and from each other. The compensations are gone with the cause.

That the impact was small is an accident of which builder a reader sits behind, not a
property of the design: ``update_media_buy`` reads ``paused`` TRISTATE
(``if req.paused is not None``), so a consumer that genuinely distinguishes None from
False already exists in this codebase. It is correct today only because
``_build_update_request`` is one of the builders that already omitted its Nones. The next
such reader, written behind one of the four that did not, would have inherited a bug
nobody wrote.

This helper is the one spelling. Two existed before it (an identical dict comprehension in
``media_buy_delivery`` and in ``task_management``) plus an if-chain in
``media_buy_update``, which is three statements of one rule -- the shape CLAUDE.md's DRY
invariant calls a defect. ``task_management``'s copy is untouched here only because
another change is live in that file.

NOT A GENERAL "STRIP FALSY" HELPER. It drops ``None`` and nothing else: ``False``, ``0``
and ``""`` are values a buyer can legitimately send, and dropping them would defeat the
same defaults from the other direction.
"""

from __future__ import annotations

from typing import Any


def omit_unset(**fields: Any) -> dict[str, Any]:
    """Keyword arguments for a request model, minus the ones the buyer did not send.

    Use as ``return XRequest(**omit_unset(a=a, b=b))`` so an unsent ``a`` takes
    ``XRequest``'s declared default rather than becoming an explicit null.
    """
    return {name: value for name, value in fields.items() if value is not None}
