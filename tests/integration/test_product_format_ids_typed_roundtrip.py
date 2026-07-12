"""TDD-red contract tests for GH #1172: typed format_ids at the DB boundary.

Core invariant (salesagent-r50r): ``Product.format_ids`` and
``InventoryProfile.format_ids`` cross the DB boundary as typed
``list[FormatId]`` — the column TypeDecorator
(``JSONType(model=FormatId, is_list=True)``) is the single coercion point;
no reader re-parses shapes.

These tests pin the ORM roundtrip contract through real PostgreSQL:

1. Roundtrip: rows written as plain ``{agent_url, id}`` JSON entries are
   read back as ``FormatId`` model instances (RED today — the bare JSONType
   column returns plain dicts).
2. Extras: stored entries carrying the CHECK-permitted ``width`` / ``height``
   / ``duration_ms`` keys (migration ``allow_width_height_duration_in_format``,
   rev f972939dd331) load without error and populate the parameterized
   ``FormatId`` fields (empirically verified: FormatId defines all three as
   optional fields, so Pattern #7 extra=forbid does not bind).
3. Write path: assigning a list of ``FormatId`` MODELS persists through
   JSONType bind serialization and round-trips (the plpgsql CHECK rejects
   nulls for optional keys, so bind must not serialize unset fields as null).
4. Stored-data flip gate: every stored ``agent_url`` on both columns must be
   a valid URL (``FormatId.agent_url`` is AnyUrl; the plpgsql CHECK only
   enforces non-empty string) — a non-URL row would become unreadable at the
   single coercion point after the flip.
5. mock_ad_server write bug: the mock adapter config POST writes
   ``request.form.getlist("formats")`` (list[str]) straight into the column
   (src/adapters/mock_ad_server.py:1550) — the persisted shape must be
   FormatId objects.

Do NOT weaken these assertions to match current production behavior — they
define the post-flip contract (TDD red for salesagent-r50r).
"""

from __future__ import annotations

import pytest
from pydantic import AnyUrl, TypeAdapter
from sqlalchemy import text

from src.core.schemas._base import FormatId
from tests.factories import FormatIdFactory, InventoryProfileFactory, ProductFactory, TenantFactory
from tests.harness._base import BareIntegrationEnv

AGENT_URL = "https://creative.adcontextprotocol.org"

_url_adapter = TypeAdapter(AnyUrl)


def _norm_url(url: object) -> str:
    """Normalize an agent_url (str or AnyUrl) for value comparison."""
    return str(url).rstrip("/")


def _reload(env: BareIntegrationEnv, instance: object) -> None:
    """Expire the instance so the next attribute access re-reads the row
    from PostgreSQL through the column TypeDecorator (a genuine DB roundtrip,
    not the identity-map copy)."""
    session = env.get_session()
    session.expire(instance)


@pytest.mark.requires_db
class TestProductFormatIdsTypedRoundtrip:
    """Product.format_ids crosses the DB boundary as list[FormatId]."""

    def test_plain_entries_roundtrip_as_format_id_models(self, integration_db):
        """A Product written with plain {agent_url, id} JSON entries reads
        back as typed FormatId instances with the same values."""
        with BareIntegrationEnv(tenant_id="t_fmt_rt_plain") as env:
            tenant = TenantFactory(tenant_id="t_fmt_rt_plain")
            product = ProductFactory(
                tenant=tenant,
                product_id="prod_fmt_rt_plain",
                format_ids=[
                    {"agent_url": AGENT_URL, "id": "display_300x250_image"},
                    {"agent_url": AGENT_URL, "id": "display_728x90_image"},
                ],
            )
            _reload(env, product)

            loaded = product.format_ids
            assert len(loaded) == 2
            for fid in loaded:
                assert isinstance(fid, FormatId), (
                    f"format_ids entries must cross the DB boundary as FormatId models, got {type(fid).__name__}"
                )
                assert _norm_url(fid.agent_url) == _norm_url(AGENT_URL)
            assert {fid.id for fid in loaded} == {"display_300x250_image", "display_728x90_image"}

    def test_entries_with_check_permitted_extras_roundtrip(self, integration_db):
        """Stored entries carrying width/height/duration_ms (permitted by the
        plpgsql CHECK, migration rev f972939dd331) load without error and
        populate the parameterized FormatId fields."""
        with BareIntegrationEnv(tenant_id="t_fmt_rt_extras") as env:
            tenant = TenantFactory(tenant_id="t_fmt_rt_extras")
            product = ProductFactory(
                tenant=tenant,
                product_id="prod_fmt_rt_extras",
                format_ids=[
                    {"agent_url": AGENT_URL, "id": "display_300x250_image", "width": 300, "height": 250},
                    {"agent_url": AGENT_URL, "id": "video_15s_hosted", "duration_ms": 15000},
                ],
            )
            _reload(env, product)

            loaded = product.format_ids
            assert len(loaded) == 2
            by_id = {}
            for fid in loaded:
                assert isinstance(fid, FormatId), (
                    f"entries with CHECK-permitted extras must load as FormatId, got {type(fid).__name__}"
                )
                by_id[fid.id] = fid
            assert by_id["display_300x250_image"].get_dimensions() == (300, 250)
            assert by_id["video_15s_hosted"].get_duration_ms() == 15000

    def test_assigning_format_id_models_persists_and_roundtrips(self, integration_db):
        """Assigning list[FormatId] MODELS to the column persists correctly
        (JSONType bind serialization must satisfy the plpgsql CHECK — no
        null-valued optional keys) and reads back as equal FormatId models."""
        with BareIntegrationEnv(tenant_id="t_fmt_rt_write") as env:
            tenant = TenantFactory(tenant_id="t_fmt_rt_write")
            product = ProductFactory(tenant=tenant, product_id="prod_fmt_rt_write")
            session = env.get_session()

            written = [
                FormatIdFactory(agent_url=AGENT_URL, id="display_970x250_image"),
                FormatIdFactory(agent_url=AGENT_URL, id="video_30s_hosted", duration_ms=30000),
            ]
            product.format_ids = written
            session.commit()

            _reload(env, product)
            loaded = product.format_ids
            assert len(loaded) == 2
            for fid in loaded:
                assert isinstance(fid, FormatId), f"model writes must round-trip as FormatId, got {type(fid).__name__}"
            assert {fid.id for fid in loaded} == {"display_970x250_image", "video_30s_hosted"}
            by_id = {fid.id for fid in loaded if fid.duration_ms is not None}
            assert by_id == {"video_30s_hosted"}


@pytest.mark.requires_db
class TestInventoryProfileFormatIdsTypedRoundtrip:
    """InventoryProfile.format_ids gets the same typed-column treatment."""

    def test_entries_roundtrip_as_format_id_models(self, integration_db):
        with BareIntegrationEnv(tenant_id="t_fmt_rt_profile") as env:
            tenant = TenantFactory(tenant_id="t_fmt_rt_profile")
            profile = InventoryProfileFactory(
                tenant=tenant,
                profile_id="profile_fmt_rt",
                format_ids=[
                    {"agent_url": AGENT_URL, "id": "display_300x250_image"},
                    {"agent_url": AGENT_URL, "id": "display_300x250_image", "width": 300, "height": 250},
                ],
            )
            _reload(env, profile)

            loaded = profile.format_ids
            assert len(loaded) == 2
            for fid in loaded:
                assert isinstance(fid, FormatId), (
                    f"InventoryProfile.format_ids must cross the DB boundary as FormatId, got {type(fid).__name__}"
                )
                assert _norm_url(fid.agent_url) == _norm_url(AGENT_URL)
            assert any(fid.get_dimensions() == (300, 250) for fid in loaded)


@pytest.mark.requires_db
class TestStoredFormatIdsAgentUrlFlipGate:
    """Flip gate: every stored agent_url must be a valid URL.

    FormatId.agent_url is AnyUrl; the plpgsql CHECK only enforces non-empty
    string. After the column flip, a non-URL agent_url row raises
    ValidationError at the single coercion point and becomes unreadable on
    EVERY select — this scan is the data-safety gate. It seeds a valid
    catalog first so the scan is live (never vacuously green on empty
    tables), then reads the RAW stored JSON (bypassing the TypeDecorator so
    the scan stays a pure data check on both sides of the flip).
    """

    def test_all_stored_agent_urls_are_valid_urls(self, integration_db):
        with BareIntegrationEnv(tenant_id="t_fmt_scan") as env:
            tenant = TenantFactory(tenant_id="t_fmt_scan")
            ProductFactory(tenant=tenant, product_id="prod_fmt_scan")
            InventoryProfileFactory(tenant=tenant, profile_id="profile_fmt_scan")
            session = env.get_session()
            session.commit()

            scans = {
                "products": text("SELECT tenant_id, product_id AS entity_id, format_ids FROM products"),
                "inventory_profiles": text(
                    "SELECT tenant_id, profile_id AS entity_id, format_ids FROM inventory_profiles"
                ),
            }
            scanned = 0
            offenders: list[str] = []
            for table, stmt in scans.items():
                for row in session.execute(stmt):
                    for entry in row.format_ids or []:
                        scanned += 1
                        agent_url = entry.get("agent_url") if isinstance(entry, dict) else None
                        try:
                            _url_adapter.validate_python(agent_url)
                        except Exception:
                            offenders.append(f"{table}({row.tenant_id}/{row.entity_id}): {agent_url!r}")

            assert scanned >= 2, "scan must be live — seeded catalog rows were not visible"
            assert not offenders, (
                "stored format_ids contain agent_url values that are not valid URLs — "
                "these rows become unreadable after the JSONType(model=FormatId) flip:\n" + "\n".join(offenders)
            )


@pytest.mark.requires_db
class TestMockAdServerFormatWritePath:
    """Regression pin for the mock adapter config write bug.

    src/adapters/mock_ad_server.py:1550 assigns
    ``request.form.getlist("formats")`` — a list[str] — straight into
    ``Product.format_ids``. The contract pinned here is the column-shape
    invariant: after driving the config POST with formats selected, every
    persisted format_ids entry is a FormatId object — never a plain string.
    (The pin is deliberately fix-strategy-agnostic: it holds whether the fix
    persists the submitted ids as objects or removes the format write from
    this view, per the "formats are managed on the product page" note in
    src/admin/blueprints/adapters.py.)

    Level note (recorded per the r50r directive — why this is the closest
    feasible level, not the assembled admin app):
    - In the assembled admin app the view is UNREACHABLE:
      ``src/admin/blueprints/adapters.py:23`` registers ``adapters.mock_config``
      on the identical URL rule first (and it never touches format_ids), and
      ``register_adapter_routes`` in ``src/admin/app.py`` silently fails to
      instantiate MockAdServer (``principal=None`` crashes ``__init__``), so
      the buggy view never binds there.
    - The view itself has a second latent bug: ``validate_product_config``
      returns a tuple, so ``if validation_errors:`` is always truthy and the
      commit path is unreachable; its templates also url_for() admin-app
      endpoints that don't exist on a bare registration app. The HTTP status
      is therefore NOT asserted — only the persisted DB state, observed
      through the same ORM surface production reads.
    This test drives the adapter's own ``register_ui_routes(app)`` surface —
    the real view, real auth decorator, real PostgreSQL.
    """

    def test_mock_config_post_leaves_format_ids_as_objects(self, integration_db, monkeypatch):
        from pathlib import Path

        from flask import Flask

        import src.adapters.mock_ad_server as mock_ad_server_module
        from src.adapters.mock_ad_server import MockAdServer
        from src.core.schemas import Principal

        tenant_id = "t_fmt_mockcfg"
        product_id = "prod_fmt_mockcfg"
        submitted_ids = ["display_300x250_image", "display_728x90_image"]

        with BareIntegrationEnv(tenant_id=tenant_id) as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            product = ProductFactory(
                tenant=tenant,
                product_id=product_id,
                format_ids=[{"agent_url": AGENT_URL, "id": "display_970x250_image"}],
            )
            env.get_session().commit()

            repo_root = Path(mock_ad_server_module.__file__).parents[2]
            app = Flask(__name__, template_folder=str(repo_root / "templates"))
            app.secret_key = "test-secret-key"
            adapter = MockAdServer(
                config={},
                principal=Principal(principal_id="p_fmt_mockcfg", name="Mock Cfg Principal", platform_mappings={}),
                tenant_id=tenant_id,
            )
            adapter.register_ui_routes(app)
            monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")

            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["test_user"] = "test@example.com"

                # Status deliberately not asserted — see class docstring.
                client.post(
                    f"/adapters/mock/config/{tenant_id}/{product_id}",
                    data={"formats": submitted_ids},
                )

            _reload(env, product)
            loaded = product.format_ids
            assert loaded, "format_ids must survive the config POST"
            for fid in loaded:
                assert isinstance(fid, FormatId), (
                    "after the mock adapter config POST the persisted format_ids must be "
                    f"FormatId objects ({{agent_url, id}}), never strings — got {type(fid).__name__}: {fid!r}"
                )
