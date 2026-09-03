"""The registration carrier does not render the buyer's credential.

Covers GH #1802, Move 1.

``ValidatedWebhookRegistration`` is a frozen slotted dataclass holding the
pinned ``PushNotificationConfig``. With the dataclass-generated ``__repr__``
live, ``repr()`` walks into ``config.authentication.credentials`` and prints
the buyer's shared secret in full. That is not theoretical: the value's TYPE
changed under an unchanged INFO log line at
``src/admin/blueprints/creatives.py:140`` (it used to be the ORM row, whose
hand-written ``__repr__`` masks with ``'***'``), so the secret reaches an
operator log at INFO today.

The fix is ``@dataclass(frozen=True, slots=True, repr=False)`` plus an
explicit ``__repr__`` that names ``webhook_url_for_log(url)`` and the
authentication TYPE -- diagnosable, and with the secret unreachable through
it. ``field(repr=False)`` on ``config`` alone is NOT the fix: the type has
exactly one field, so that yields ``ValidatedWebhookRegistration()``, an empty
repr nothing can be diagnosed from. Hence the positive assertions below.

SCOPE, deliberately partial. This module grades the OUTER carrier only.
``repr(r.config)`` -- the inner pydantic model -- still discloses the
credential, and that is a RATIFIED residual, not an oversight: the frozen plan
(``GH #1802 § Residual risk (4)) defers it to
a ``SecretStr`` escalation, blocked on three facts (``credentials: str`` lives
on a GENERATED SDK model, a nested re-annotation is CLAUDE.md Pattern #4
territory, and ``SecretStr.model_dump()`` would write ``**********`` into a
persistence path that is read back for signing). Do NOT add an assertion here
that ``repr(r.config)`` is clean -- it will not be, by plan.
"""

from __future__ import annotations

from src.core.webhook_validator import webhook_url_for_log
from src.core.webhooks.registration import accept_push_notification_config

# Long enough to clear the pinned ``minLength: 32`` on
# ``core/push-notification-config.json`` ``authentication.credentials``, so the
# document under test is one the gate actually accepts.
CREDENTIAL = "S" * 40

# A second secret, carried in the URL's query rather than in the auth block. The
# redaction helper the design mandates (``webhook_url_for_log``) returns
# ``scheme://host/path`` and drops userinfo and query, so this one must not
# survive into the repr either.
QUERY_SECRET = "QUERYSECRET"

REGISTRATION_DOCUMENT = {
    "url": f"https://buyer.example.com/hook?token={QUERY_SECRET}",
    "authentication": {"schemes": ["HMAC-SHA256"], "credentials": CREDENTIAL},
}


def _registration():
    return accept_push_notification_config(REGISTRATION_DOCUMENT)


def test_repr_does_not_render_the_credential():
    """``repr()`` of the carrier never contains the stored credential.

    This is the assertion GH #1802's ACCEPTANCE CRITERIA names.
    """
    rendered = repr(_registration())

    assert CREDENTIAL not in rendered, f"repr() leaked the buyer's credential: {rendered!r}"


def test_str_and_f_string_do_not_render_the_credential():
    """The two forms an f-string log line actually takes are graded too.

    ``creatives.py:140`` is an f-string, which calls ``__format__`` -> ``__str__``
    -> ``__repr__`` for a type that defines no ``__str__``. Grading only
    ``repr()`` would leave the exact shape of the live leak ungraded.
    """
    registration = _registration()

    assert CREDENTIAL not in str(registration), f"str() leaked the buyer's credential: {str(registration)!r}"
    assert CREDENTIAL not in f"{registration}", f"an f-string leaked the buyer's credential: {registration!r}"


def test_repr_does_not_render_a_url_borne_secret():
    """The URL is rendered through the redaction helper, so query/userinfo drop.

    ``webhook_url_for_log`` is the house helper (9 production call sites) and
    the design names it explicitly; a repr that interpolated ``self.url`` raw
    would pass the credential assertions above and still print a token.
    """
    rendered = repr(_registration())

    assert QUERY_SECRET not in rendered, f"repr() leaked a URL-borne token: {rendered!r}"


def test_repr_stays_diagnosable():
    """The redacted repr still names the class, the safe URL and the auth type.

    Pinned because the cheap fix -- ``field(repr=False)`` on the single field --
    satisfies every negative assertion above while producing
    ``ValidatedWebhookRegistration()``, which is useless to an operator.
    """
    registration = _registration()
    rendered = repr(registration)

    assert "ValidatedWebhookRegistration" in rendered, f"the repr does not name its own type: {rendered!r}"
    assert webhook_url_for_log(registration.url) in rendered, (
        f"the repr does not name the sanitized destination: {rendered!r}"
    )
    assert registration.authentication_type in rendered, f"the repr does not name the authentication type: {rendered!r}"
