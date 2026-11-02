"""Behavioral test for the create_media_buy transport boundary: the A2A path COERCES.

Obligation ID:
  UC-002-TRANSPORT-PNC-SERIALIZATION-02  (A2A wrapper)

REMOVED, and the reason, because two of these cases were green for a long time:

* ``test_a2a_wrapper_url_is_plain_str_not_anyurl`` and
  ``test_a2a_wrapper_enum_schemes_are_plain_strings``. Both asserted a Python TYPE on
  the value ``ValidatedWebhookRegistration.to_columns()`` projects, after routing a
  config through ``invoke_tool`` — so they were framed as an A2A transport claim about a
  layer the transport does not own. ``push_notification_config`` is a REQUEST field and
  is never echoed in a response, so no spelling of it is observable on the A2A wire; the
  boundary produces the wire body in both directions. The boundary round-trip contributed
  nothing either: the captured value is the model that was constructed.

  What remains of the claim is the ``str()`` at the PERSISTENCE seam
  (``registration.url``, ``src/core/webhooks/registration.py``), and it is graded there
  on a real flush and read-back by
  ``tests/integration/test_push_notification_config_repository.py::test_upsert_writes_the_values_fields_into_the_columns``
  — which is what gh-#1377 actually was (``AnyUrl`` into a SQLAlchemy ``String`` column
  raises ``StatementError``). A unit type-assert cannot see that and the integration
  write does.

  The enum half had no subject at all: ``adcp.types.AuthenticationScheme`` is a
  ``StrEnum``, so a member IS a ``str`` — the column takes it, it reads back as
  ``"Bearer"``, and ``type(x) is str`` graded a distinction nothing downstream can
  observe.

* ``TestMCPWrapperPncJsonSerialization`` (obligation -01), which held no test at all —
  only a docstring describing the pre-Epic-D world where each wrapper did its own
  ``model_dump(mode="json")``. There are no per-transport wrappers left to disagree:
  MCP, A2A and REST all reach the implementation through ``invoke_tool``.
"""

from __future__ import annotations

import pytest

from tests.helpers.create_media_buy_capture import capture_a2a_forwarded_pnc
from tests.helpers.signing import verifier_disabled


class TestA2AWrapperPncJsonSerialization:
    """The A2A path COERCES a raw dict through the pinned model.

    The A2A wrapper used to pass a raw dict straight through — the untyped seam
    Epic D lanes 1-3 traced. It now coerces through the pinned model, so a
    document the schema forbids is refused instead of stored.
    """

    @pytest.mark.asyncio
    async def test_a2a_wrapper_coerces_a_raw_dict_to_the_typed_model(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-02

        This case INVERTED in Epic D lane C3, deliberately. It previously asserted
        the A2A wrapper passes a raw dict through UNCHANGED — which is precisely
        the untyped hole that let a schema-invalid registration reach ``_impl``,
        be stored, and then never deliver. The wrapper now coerces, so the buyer's
        dict becomes the pinned model or is refused by name.
        """
        pnc_dict = {
            "url": "https://buyer.example.com/webhook",
            "authentication": {"credentials": "a" * 32, "schemes": ["Bearer"]},
        }
        # The ``authentication`` block is the SUBJECT here, and on a signing-capable seller it
        # is also a trigger that makes the registration refusable unsigned (security.mdx @
        # v3.1.1 :1465). That obligation has its own tests; this one grades coercion.
        with verifier_disabled():
            forwarded = await capture_a2a_forwarded_pnc(pnc_dict)

        from adcp import PushNotificationConfig

        assert forwarded is not None
        assert isinstance(forwarded, PushNotificationConfig), (
            f"the A2A wrapper must COERCE a raw dict, not forward it — got {type(forwarded).__name__}"
        )
        assert str(forwarded.url) == "https://buyer.example.com/webhook"
        assert [str(s) for s in forwarded.authentication.schemes] == ["Bearer"]
