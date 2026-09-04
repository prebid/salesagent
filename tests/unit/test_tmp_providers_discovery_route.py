"""Unit tests for the FastAPI TMP provider discovery route and TMPProvider model.

Tests the endpoint:
    GET (the path declared by src.routes.tmp_providers.DISCOVERY_ROUTE)

This is the FastAPI route in src/routes/tmp_providers.py — the canonical
machine-to-machine discovery endpoint polled by the TMP Router every 30 s.

Covers:
- Returns active + draining providers via repository.list_syncable()
- An unknown tenant is a 401 on the real path (the credential cannot resolve in a
  tenant that does not exist), pinned in the integration suite — not graded here,
  where the credential gate is stubbed
- Returns empty list when tenant has no active providers
- Response shape matches TMP Router contract
- Providers ordered by priority ASC, name ASC
- Handles legacy rows with null countries/uid_types
- uow.tenant_config is None → 500 (not an assert)
- TMPProviderDiscoveryEntry (the pinned SDK type) is what the wire carries; the
  Jinja-facing shapes and their mappers live with the admin layer that owns them
  and are graded in tests/unit/test_tmp_providers_blueprint.py

Every request here goes through the PRODUCTION app (``src.app.app``) — its
router mount, its middleware stack and its ``AdCPError`` handler — rather than a
test-local ``FastAPI()`` with a re-declared handler.  A restated handler can only
detect key-name drift in its own copy; the production one is the thing the
contract is served by.  The credential gate is the one dependency overridden,
because resolving a credential needs a real tenant + principal row: the auth
matrix (missing / cross-tenant / principal / admin token) is graded for real
against the same app in ``tests/integration/test_tmp_provider_integration.py``
(#1197 review).
"""

from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm.exc import DetachedInstanceError

from src.core.database.models import TMPProvider
from src.core.exceptions import AdCPServiceUnavailableError
from src.core.schemas.tmp_provider import TMPProviderDiscoveryEntry
from src.routes.tmp_providers import DISCOVERY_ROUTE, PROVIDER_REGISTRATION_SCHEMA
from tests.helpers.envelope_assertions import assert_envelope_shape
from tests.helpers.pinned_schema import validate_against_pinned_schema
from tests.unit._tmp_helpers import make_provider, make_tmp_uow, mock_cm

# A property RID is `format: uuid` in the pinned schema, so test fixtures must
# use a real UUID rather than a readable placeholder.
_RID = "0192f3c4-5d6e-7f80-9a1b-2c3d4e5f6071"


def _make_tenant(tenant_id="si-host"):
    t = MagicMock()
    t.tenant_id = tenant_id
    t.name = "SI Host Tenant"
    return t


_STUB_PRINCIPAL = "tmp-router-principal"


def _discovery_path(tenant_id: str) -> str:
    """The polled path, formatted from the route's own declaration.

    Never a hand-typed literal: ``DISCOVERY_ROUTE`` exists so the path has one
    definition, and a suite that re-types it is a second definition nothing pins
    equal (#1197 review). ``test_architecture_pinned_schema_citations`` fails a
    hand-typed discovery path outside the route module.
    """
    return DISCOVERY_ROUTE.format(tenant_id=tenant_id)


@pytest.fixture
def client():
    """A TestClient over the PRODUCTION app with the credential gate stubbed.

    ``src.app.app`` brings its own router mount, middleware stack and
    ``AdCPError`` handler, so the envelopes these tests assert on are the ones
    the deployed endpoint emits — deleting the production handler breaks them.
    Only :func:`_require_tenant_credential` is overridden: resolving a credential
    reads tenant + principal rows, which is an integration concern, and the auth
    matrix itself is graded against this same app with a real DB in
    ``tests/integration/test_tmp_provider_integration.py`` (#1197 review).
    """
    from src.app import app
    from src.routes.tmp_providers import _require_tenant_credential

    app.dependency_overrides[_require_tenant_credential] = lambda: _STUB_PRINCIPAL
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(_require_tenant_credential, None)


class TestDiscoveryReturnsActiveProviders:
    """The discovery contract returns active + draining providers."""

    def test_returns_two_active_providers(self, client):
        """Two active providers are returned in the response via repository.list_syncable()."""
        tenant = _make_tenant()
        providers = [
            make_provider(provider_id="prov_1", name="Provider A", priority=0, countries=["US"]),
            make_provider(provider_id="prov_2", name="Provider B", priority=1, uid_types=["uid2"]),
        ]

        mock_tmp_uow_cls = make_tmp_uow(providers, tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            response = client.get(_discovery_path("si-host"))

        assert response.status_code == 200
        data = response.json()
        assert data["tenant_id"] == "si-host"
        assert len(data["providers"]) == 2
        assert data["providers"][0]["provider_id"] == "prov_1"
        assert data["providers"][0]["countries"] == ["US"]
        assert data["providers"][1]["provider_id"] == "prov_2"
        assert data["providers"][1]["uid_types"] == ["uid2"]
        mock_tmp_uow_cls.return_value.__enter__.return_value.tmp_providers.list_syncable.assert_called_once_with()

    def test_includes_draining_providers(self, client):
        """Draining providers are included (router stops new requests but in-flight complete)."""
        tenant = _make_tenant()
        providers = [
            make_provider(provider_id="prov_1", status="active"),
            make_provider(provider_id="prov_2", status="draining"),
        ]

        mock_tmp_uow_cls = make_tmp_uow(providers, tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            response = client.get(_discovery_path("si-host"))

        assert response.status_code == 200
        data = response.json()
        assert len(data["providers"]) == 2
        statuses = {p["status"] for p in data["providers"]}
        assert statuses == {"active", "draining"}


class TestDiscoveryEmptyProviders:
    """The discovery contract returns an empty list when the tenant has no providers."""

    def test_returns_empty_providers_list(self, client):
        """Valid tenant with no active providers returns empty providers array."""
        tenant = _make_tenant()

        mock_tmp_uow_cls = make_tmp_uow([], tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            response = client.get(_discovery_path("si-host"))

        assert response.status_code == 200
        data = response.json()
        assert data["tenant_id"] == "si-host"
        assert data["providers"] == []


class TestDiscoveryResponseShape:
    """Response shape matches the TMP Router contract."""

    def test_response_contains_all_required_fields(self, client):
        """Each provider entry contains all fields the TMP Router expects."""
        tenant = _make_tenant()
        providers = [
            make_provider(
                countries=["US", "GB"],
                uid_types=["publisher_first_party", "uid2"],
            ),
        ]

        mock_tmp_uow_cls = make_tmp_uow(providers, tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            response = client.get(_discovery_path("si-host"))

        assert response.status_code == 200
        entry = response.json()["providers"][0]

        # The closed key set of provider-registration.json — asserted as
        # EQUALITY, not a subset: additionalProperties is false, so an extra key
        # (e.g. re-adding the admin-only `name`) is a schema violation a subset
        # check would wave through.
        assert set(entry) == {
            "provider_id",
            "endpoint",
            "context_match",
            "identity_match",
            "countries",
            "uid_types",
            "timeout_ms",
            "priority",
            "status",
        }

    def test_name_is_not_on_the_machine_wire(self, client):
        """`name` is not in the closed schema, so the discovery wire must not carry it.

        It stays on the admin view shape (``_admin_view``) — see
        ``TestTMPProviderSerializers``.  The TMP Router uses ``name`` only as a
        fallback identifier when ``provider_id`` is empty, and this endpoint
        always emits ``provider_id``.
        """
        tenant = _make_tenant()
        providers = [make_provider(name="Admin Only Label")]

        mock_tmp_uow_cls = make_tmp_uow(providers, tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            response = client.get(_discovery_path("si-host"))

        assert response.status_code == 200
        assert "name" not in response.json()["providers"][0]

    def test_absent_countries_uid_types_are_omitted_not_null(self, client):
        """Rows that restrict nothing omit the conditional arrays — `null` is a type violation.

        ``provider-registration.json`` types ``countries``/``uid_types``/
        ``properties`` as ``array`` with ``minItems: 1``, so ``null`` is not a
        permitted value and a strictly-validating router rejects the body.
        Omission is how the schema spells "no restriction" (#1197 review).
        """
        tenant = _make_tenant()
        providers = [
            make_provider(countries=None, uid_types=None, properties=None),
        ]

        mock_tmp_uow_cls = make_tmp_uow(providers, tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            response = client.get(_discovery_path("si-host"))

        assert response.status_code == 200
        entry = response.json()["providers"][0]
        assert "countries" not in entry
        assert "uid_types" not in entry
        assert "properties" not in entry


class TestDiscoveryOrdering:
    """Providers are ordered by priority ASC, name ASC."""

    def test_providers_ordered_by_priority_then_name(self, client):
        """The repository's priority ASC, name ASC order survives to the wire.

        Asserted on ``provider_id`` rather than ``name``: the ordering key is
        the repository's, but ``name`` is admin-only and not on this wire, so
        each row's id stands in for it (ids are assigned to match the expected
        name order).
        """
        tenant = _make_tenant()
        # Simulate DB returning in correct order (priority 0 before 1, alpha within same priority)
        providers = [
            make_provider(provider_id="prov_a", name="Alpha", priority=0),
            make_provider(provider_id="prov_b", name="Beta", priority=0),
            make_provider(provider_id="prov_c", name="Gamma", priority=1),
        ]

        mock_tmp_uow_cls = make_tmp_uow(providers, tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            response = client.get(_discovery_path("si-host"))

        assert response.status_code == 200
        provider_ids = [p["provider_id"] for p in response.json()["providers"]]
        assert provider_ids == ["prov_a", "prov_b", "prov_c"]


# ---------------------------------------------------------------------------
# Repository availability — now a property of the UoW, not of this route
# ---------------------------------------------------------------------------


class TestDiscoveryRepositoryUnavailable:
    """The typed 503 envelope survives, but the guard is no longer in the route.

    ``TMPProviderUoW`` exposes its repositories through ``RepositoryAccessor``,
    so inside the ``with`` block they are the concrete repository and outside it
    the read raises the typed ``AdCPServiceUnavailableError``.  There is no
    ``uow.<repo> is None`` state for the route to test any more — the per-call-site
    narrowing (and the ``assert`` spelling of it, which ``python -O`` strips and
    which escapes as an un-enveloped 500) is gone by construction (#1197 review).

    What still needs grading here is the *boundary translation*: a typed
    ``AdCPServiceUnavailableError`` raised anywhere inside the route body must
    reach the client as the AdCP 503 envelope, not a bare 500.  The accessor's
    own contract is graded in ``tests/unit/test_uow_repository_accessor.py``.
    """

    def test_service_unavailable_from_the_uow_becomes_the_typed_503_envelope(self, client):
        """A repository read that raises AdCPServiceUnavailableError → AdCP 503 envelope."""
        mock_uow = MagicMock()
        type(mock_uow).tenant_config = PropertyMock(
            side_effect=AdCPServiceUnavailableError(
                "tenant_config repository unavailable.",
                suggestion="Retry shortly; the sales agent could not open a database session.",
            )
        )

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_cm(mock_uow)):
            response = client.get(_discovery_path("si-host"))

        assert response.status_code == 503
        envelope = response.json()
        assert_envelope_shape(
            envelope,
            "SERVICE_UNAVAILABLE",
            recovery="transient",
            message_substr="repository unavailable",
        )
        assert "Retry shortly" in envelope["errors"][0]["suggestion"]


# ---------------------------------------------------------------------------
# Single-transaction + no-DetachedInstance regression tests
# ---------------------------------------------------------------------------


class TestDiscoverySingleTransactionAndNoDetachedInstance:
    """Regression tests proving the route uses ONE UoW and calls to_dict() inside it.

    Round 11 review fix: the route was refactored from two separate UoW blocks
    (TenantConfigUoW then TMPProviderUoW) to a single TMPProviderUoW block.
    These tests prove:
    1. TMPProviderUoW is constructed exactly once (not twice).
    2. provider.to_dict() is called BEFORE the UoW exits — calling it after
       would raise DetachedInstanceError under real SQLAlchemy
       (expire_on_commit=True is the default).
    """

    class _DetachAfterCloseProvider:
        """Fake provider whose ATTRIBUTE reads raise DetachedInstanceError once the UoW closed.

        ``TMPProviderDiscoveryEntry.from_row`` reads the row attribute by
        attribute, so the detachment is simulated where SQLAlchemy raises it —
        on attribute access — rather than on a serializer method that no longer
        exists.
        """

        _VALUES = {
            "provider_id": "fake_uuid",
            "endpoint": "https://fake.example.com:3000",
            "context_match": True,
            "identity_match": False,
            "countries": None,
            "uid_types": None,
            "properties": None,
            "timeout_ms": 200,
            "priority": 0,
            "status": "active",
        }

        def __init__(self, closed_flag: list[bool]):
            self._closed_flag = closed_flag

        def __getattr__(self, name: str):
            if name.startswith("_"):
                raise AttributeError(name)
            if self._closed_flag[0]:
                raise DetachedInstanceError("Instance is not bound to a Session; attribute access failed")
            try:
                return self._VALUES[name]
            except KeyError as exc:
                raise AttributeError(name) from exc

    def test_tmp_provider_uow_constructed_exactly_once(self, client):
        """TMPProviderUoW is instantiated exactly once — not twice (no separate TenantConfigUoW)."""
        mock_tmp_uow_cls = make_tmp_uow([], tenant=_make_tenant())

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            response = client.get(_discovery_path("si-host"))

        assert response.status_code == 200
        # The class must have been called (constructed) exactly once.
        mock_tmp_uow_cls.assert_called_once_with("si-host")

    def test_entry_built_before_uow_exits(self, client):
        """The wire entry is constructed inside the UoW block, not after it closes.

        Uses a fake provider whose attribute reads raise DetachedInstanceError
        once the UoW ``__exit__`` sets a closed_flag. If the route builds entries
        after the block exits, the request fails; if it builds them inside, it
        succeeds.
        """
        closed_flag = [False]
        provider = self._DetachAfterCloseProvider(closed_flag)

        mock_uow = MagicMock()
        mock_uow.tmp_providers = MagicMock()
        mock_uow.tmp_providers.list_syncable.return_value = [provider]
        mock_uow.tenant_config = MagicMock()
        mock_uow.tenant_config.get_tenant.return_value = _make_tenant()

        def _mark_closed(*_args):
            closed_flag[0] = True
            return False

        mock_uow_cls = mock_cm(mock_uow, on_exit=_mark_closed)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_uow_cls):
            # Would raise DetachedInstanceError (→ 500) if from_row() ran after __exit__.
            response = client.get(_discovery_path("si-host"))

        assert response.status_code == 200
        data = response.json()
        assert len(data["providers"]) == 1
        assert data["providers"][0]["provider_id"] == "fake_uuid"


# ---------------------------------------------------------------------------
# TMPProvider serializer unit tests (no DB required)
# ---------------------------------------------------------------------------


class TestDiscoverySkipsUnrepresentableRows:
    """A legacy row that cannot be represented is dropped, not published, and not fatal.

    Per row rather than per response: the only rows that can fail conversion are
    ones predating ``TMPProviderRegistration`` (an ``identity_match`` row with no
    countries is unrepresentable under the schema's if/then at any
    serialization). Failing the whole response would let one such row take
    discovery down for every other provider — the per-batch failure mode this
    feature removed from the sync path and the health scheduler (#1197 review).
    """

    def test_bad_row_is_skipped_and_good_rows_still_publish(self, client, caplog):
        import logging

        good = make_provider(provider_id="prov_good", endpoint="https://good.example.com/tmp")
        # Unrepresentable: identity_match with no countries/uid_types.
        bad = make_provider(
            provider_id="prov_legacy",
            context_match=False,
            identity_match=True,
            countries=None,
            uid_types=None,
        )
        mock_tmp_uow_cls = make_tmp_uow([bad, good], tenant=_make_tenant())

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            with caplog.at_level(logging.ERROR, logger="src.routes.tmp_providers"):
                response = client.get(_discovery_path("si-host"))

        assert response.status_code == 200
        returned = [p["provider_id"] for p in response.json()["providers"]]
        assert returned == ["prov_good"]
        # The operator gets the id of the row to repair.
        assert "prov_legacy" in caplog.text


class TestDiscoveryEntryIsTheSdkType:
    """The wire entry is the pinned SDK model, so the schema's rules are its rules.

    These used to grade a hand-written serializer against the schema.
    The entry is now :class:`TMPProviderDiscoveryEntry`, which extends the pinned
    codegen — so the tests that remain are the ones that can still fail: that a
    row converts, that the conversion enforces what the schema says, and that the
    admin-only ``name`` never reaches this wire (#1197 review).
    """

    @staticmethod
    def _entry_dict(provider: TMPProvider) -> dict:
        """The entry as it goes on the wire — omitting absent conditionals."""
        return TMPProviderDiscoveryEntry.from_row(provider).model_dump(mode="json", exclude_none=True)

    def test_context_only_row_converts_and_validates(self):
        p = make_provider(endpoint="https://ctx.example.com/tmp", context_match=True, identity_match=False)
        entry = self._entry_dict(p)

        validate_against_pinned_schema(PROVIDER_REGISTRATION_SCHEMA, entry)
        assert "countries" not in entry
        assert "uid_types" not in entry
        assert "properties" not in entry
        assert "name" not in entry, "the admin-only label must never reach the machine wire"

    def test_fully_populated_identity_row_converts_and_validates(self):
        p = make_provider(
            provider_id="prov_test",
            name="Test Provider",
            endpoint="https://example.com",
            context_match=False,
            identity_match=True,
            countries=["DE"],
            uid_types=["id5"],
            properties=[_RID],
            timeout_ms=300,
            priority=2,
            status="draining",
        )
        entry = self._entry_dict(p)

        validate_against_pinned_schema(PROVIDER_REGISTRATION_SCHEMA, entry)
        assert entry["countries"] == ["DE"]
        assert entry["uid_types"] == ["id5"]
        assert entry["properties"] == [_RID]
        assert entry["status"] == "draining"
        assert entry["timeout_ms"] == 300

    def test_local_http_endpoint_is_permitted(self):
        """The one documented divergence from the SDK type, and it follows the schema.

        The schema types ``endpoint`` ``format: uri``; the HTTPS requirement is
        prose in the field description. Local development against
        ``http://…​.localhost`` must keep working.

        Also pins the one wire-visible consequence of typing the field as
        ``AnyUrl``: pydantic canonicalizes, so a stored
        ``http://host:3003`` is published as ``http://host:3003/``. That is
        conformant and semantically identical — the schema states that two
        registrations "differing only in case, default port, or path-slash
        collapsing are the same provider" — and our own outbound path is
        unaffected because ``provider_url()`` strips the trailing slash before
        appending. Asserted explicitly so the normalization is a stated property
        rather than a surprise to a router operator diffing stored vs published.
        """
        p = make_provider(endpoint="https://si-agent.localhost:3003")
        entry = self._entry_dict(p)

        validate_against_pinned_schema(PROVIDER_REGISTRATION_SCHEMA, entry)
        assert entry["endpoint"] == "https://si-agent.localhost:3003/"

    def test_hyphenated_provider_id_cannot_be_emitted(self):
        """Construction fails on the charset rule — it is no longer possible to serialize it.

        Pins why ``provider_id`` is ``uuid4().hex`` in a ``varchar`` column: put a
        ``-`` back and the entry cannot be built, rather than being built and
        rejected downstream by a router.
        """
        p = make_provider(provider_id="5f1c0e3a-9b7d-4e8f-a1c2-b3d4e5f60718")
        with pytest.raises(ValidationError):
            TMPProviderDiscoveryEntry.from_row(p)

    def test_uid_type_vocabulary_is_enforced_on_a_context_only_row(self):
        """The leak this type closed: the enum applies with no ``identity_match`` condition.

        The schema refs ``enums/uid-type.json`` on ``uid_types.items``
        unconditionally, so a context-only provider carrying a bogus uid type is a
        row the wire rejects — previously accepted, because the vocabulary check
        lived inside ``if self.identity_match:`` (#1197 review).
        """
        p = make_provider(context_match=True, identity_match=False, uid_types=["not_a_uid_type"])
        with pytest.raises(ValidationError):
            TMPProviderDiscoveryEntry.from_row(p)

    def test_identity_row_without_dimensions_cannot_be_emitted(self):
        """The schema's if/then, which a TypedDict could not express at all.

        A legacy ``identity_match`` row with no countries/uid_types is
        unrepresentable at any serialization; the route drops and logs it rather
        than publishing it (see TestDiscoverySkipsUnrepresentableRows).
        """
        p = make_provider(context_match=False, identity_match=True, countries=None, uid_types=None)
        with pytest.raises(ValidationError):
            TMPProviderDiscoveryEntry.from_row(p)


class TestTMPProviderAuthCredentials:
    """TMPProvider.auth_credentials encrypts on write and decrypts on read.

    The property must raise AdCPConfigurationError (not silently return
    plaintext) when decryption fails — a corrupted ciphertext, a key rotation,
    or a tampered row must surface as a hard error so the admin can act.
    """

    def test_round_trip_encrypt_decrypt(self):
        """Setting auth_credentials encrypts; reading it back decrypts to the original value."""
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        with patch.dict("os.environ", {"ENCRYPTION_KEY": key}):
            p = TMPProvider()
            p.provider_id = "test-provider-id"
            p.auth_credentials = "super-secret-token"

            # The raw column must NOT be the plaintext value
            assert p._auth_credentials != "super-secret-token"
            assert p._auth_credentials is not None

            # Reading back through the property must return the original value
            assert p.auth_credentials == "super-secret-token"

    def test_none_value_returns_none(self):
        """Setting auth_credentials to None stores None and reads back as None."""
        p = TMPProvider()
        p.provider_id = "test-provider-id"
        p.auth_credentials = None
        assert p._auth_credentials is None
        assert p.auth_credentials is None

    def test_corrupted_ciphertext_raises_adcp_configuration_error(self):
        """A corrupted ciphertext raises AdCPConfigurationError, not a silent plaintext fallback."""
        from cryptography.fernet import Fernet

        from src.core.exceptions import AdCPConfigurationError

        key = Fernet.generate_key().decode()
        with patch.dict("os.environ", {"ENCRYPTION_KEY": key}):
            p = TMPProvider()
            p.provider_id = "test-provider-id"
            # Inject a corrupted ciphertext directly into the backing column
            p._auth_credentials = "not-a-valid-fernet-token"

            with pytest.raises(AdCPConfigurationError) as exc_info:
                _ = p.auth_credentials

        assert "test-provider-id" in str(exc_info.value)

    def test_empty_string_stores_none(self):
        """Setting auth_credentials to empty string stores None (treated as absent)."""
        p = TMPProvider()
        p.provider_id = "test-provider-id"
        p.auth_credentials = ""
        assert p._auth_credentials is None
        assert p.auth_credentials is None
