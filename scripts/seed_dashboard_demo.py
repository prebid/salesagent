"""Populate the `default` tenant with enough data to make the redesigned
Ledger dashboard look real — Incoming offers, Running buys with pacing,
Pipeline briefs across multiple buyers, pending creatives.

Idempotent: safe to re-run. Writes nothing buyer-facing — these rows are
intended for visual / design verification only.

Run inside the Docker app container so DATABASE_URL is set:

    docker compose exec adcp-server python /app/scripts/seed_dashboard_demo.py
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AuditLog,
    AuthorizedProperty,
    Creative,
    MediaBuy,
    Principal,
    Product,
    Tenant,
)

TENANT_ID = "default"


def upsert_principals(session) -> dict[str, Principal]:
    """A few buyer-side principals so audit rows have plausible identities."""
    seed = [
        ("scope3_storefront", "Scope3 Storefront"),
        ("kepler_buying", "Kepler Group"),
        ("publicis_dx", "Publicis Direct Exchange"),
        ("acme_directs", "Acme Direct Buys"),
    ]
    out: dict[str, Principal] = {}
    for pid, name in seed:
        existing = session.scalars(select(Principal).filter_by(tenant_id=TENANT_ID, principal_id=pid)).first()
        if existing is None:
            existing = Principal(
                tenant_id=TENANT_ID,
                principal_id=pid,
                name=name,
                platform_mappings={"mock": {"advertiser_id": pid}},
                access_token=f"demo_{pid}_{secrets.token_hex(6)}",
            )
            session.add(existing)
        out[pid] = existing
    session.flush()
    return out


def upsert_properties(session) -> None:
    """Four authorized properties — drives masthead count."""
    domains = [
        ("atlasdaily.com", "The Atlas Daily"),
        ("atlasweekly.com", "The Atlas Weekly"),
        ("atlas-podcast.com", "Atlas on Air"),
        ("atlas-newsletter.com", "Atlas Briefing"),
    ]
    for i, (domain, name) in enumerate(domains):
        prop_id = f"demo_prop_{i + 1}"
        existing = session.scalars(
            select(AuthorizedProperty).filter_by(tenant_id=TENANT_ID, property_id=prop_id)
        ).first()
        if existing is None:
            session.add(
                AuthorizedProperty(
                    tenant_id=TENANT_ID,
                    property_id=prop_id,
                    property_type="website",
                    name=name,
                    identifiers=[{"type": "domain", "value": domain}],
                    publisher_domain=domain,
                    tags=["demo"],
                    verification_status="verified",
                )
            )
    session.flush()


def upsert_products(session) -> None:
    """Twelve products — drives masthead count."""
    products = [
        "Premium video preroll",
        "Display run-of-site",
        "Newsletter takeover",
        "Podcast pre-roll",
        "Mobile interstitial",
        "Sponsored article",
        "Native in-feed",
        "Premium display 300x250",
        "Premium display 728x90",
        "Connected TV 16:9",
        "Audio mid-roll",
        "High-impact homepage takeover",
    ]
    for i, name in enumerate(products):
        pid = f"demo_prod_{i + 1}"
        existing = session.scalars(select(Product).filter_by(tenant_id=TENANT_ID, product_id=pid)).first()
        if existing is None:
            session.add(
                Product(
                    tenant_id=TENANT_ID,
                    product_id=pid,
                    name=name,
                    description=f"{name} — demo product",
                    format_ids=[{"agent_url": "https://demo.test", "id": "display_300x250"}],
                    targeting_template={},
                    delivery_type="non_guaranteed",
                    countries=["US"],
                    property_tags=["demo"],
                )
            )
    session.flush()


def seed_media_buys(session, principals: dict[str, Principal]) -> None:
    """Mix of Incoming (pending), Running (active w/ pacing), Completed."""
    now = datetime.now(UTC)
    today = now.date()

    # Wipe existing demo media buys so re-runs don't pile up
    existing = session.scalars(
        select(MediaBuy).where(MediaBuy.tenant_id == TENANT_ID).where(MediaBuy.media_buy_id.like("demo_mb_%"))
    ).all()
    for mb in existing:
        session.delete(mb)
    session.flush()

    pid_acme = principals["acme_directs"].principal_id
    pid_scope = principals["scope3_storefront"].principal_id
    pid_kepler = principals["kepler_buying"].principal_id

    incoming = [
        ("demo_mb_inc_1", "Cascade Outdoor", "Q3 video sponsorship", 120000, pid_acme, 5),  # 5h ago — urgent
        ("demo_mb_inc_2", "Northwind Telecom", "Newsletter takeover · 6 weeks", 48000, pid_scope, 4),
        ("demo_mb_inc_3", "Pearl & Vine", "Display run-of-site", 22000, pid_scope, 26),
        ("demo_mb_inc_4", "Greybox Coffee", "Podcast pre-roll", 94000, pid_kepler, 50),
    ]
    for mb_id, advertiser, order, budget, pid, hours_ago in incoming:
        created = now - timedelta(hours=hours_ago)
        session.add(
            MediaBuy(
                media_buy_id=mb_id,
                tenant_id=TENANT_ID,
                principal_id=pid,
                order_name=order,
                advertiser_name=advertiser,
                budget=Decimal(str(budget)),
                currency="USD",
                start_date=today + timedelta(days=2),
                end_date=today + timedelta(days=30),
                status="pending_approval",
                created_at=created,
                updated_at=created,
                raw_request={"demo": True, "packages": []},
            )
        )

    running = [
        # (id, advertiser, order, budget, pid, days_since_start, total_days, delivery_pct_target)
        (
            "demo_mb_run_1",
            "Halcyon Bank",
            "Newsletter sponsorship · wk 3 of 8",
            96000,
            pid_scope,
            17,
            56,
            0.41,
        ),  # on-pace
        ("demo_mb_run_2", "Meridian Travel", "Display package · wk 1 of 4", 32000, pid_kepler, 6, 28, 0.10),  # under
        ("demo_mb_run_3", "Lumen Apparel", "Premium video 16:9", 128000, pid_acme, 18, 30, 0.62),  # on-pace
        (
            "demo_mb_run_4",
            "Forge Hardware",
            "Run-of-network",
            16000,
            pid_scope,
            18,
            28,
            0.86,
        ),  # over (64% flight, 86% delivered)
    ]
    for mb_id, advertiser, order, budget, pid, days_in, total_days, dpct in running:
        start = today - timedelta(days=days_in)
        end = start + timedelta(days=total_days)
        approved = now - timedelta(days=days_in, hours=1)
        delivered_amount = Decimal(str(round(budget * dpct, 2)))
        delivered_impressions = int(budget * dpct * 10)  # rough impressions proxy
        session.add(
            MediaBuy(
                media_buy_id=mb_id,
                tenant_id=TENANT_ID,
                principal_id=pid,
                order_name=order,
                advertiser_name=advertiser,
                budget=Decimal(str(budget)),
                currency="USD",
                start_date=start,
                end_date=end,
                status="active",
                approved_at=approved,
                approved_by="demo",
                created_at=approved - timedelta(days=2),
                updated_at=now,
                delivered_impressions=delivered_impressions,
                delivered_amount=delivered_amount,
                delivery_synced_at=now - timedelta(minutes=12),
                raw_request={"demo": True, "packages": []},
            )
        )

    # Some completed deals to make the revenue curve non-zero
    for i, (advertiser, days_back, budget) in enumerate(
        [
            ("Pierside Outdoors", 9, 84000),
            ("Northstar Bank", 16, 65000),
            ("Atrium Retail", 23, 41000),
            ("Vesper Wines", 28, 28000),
        ]
    ):
        approved = now - timedelta(days=days_back)
        session.add(
            MediaBuy(
                media_buy_id=f"demo_mb_done_{i + 1}",
                tenant_id=TENANT_ID,
                principal_id=pid_scope,
                order_name="Completed flight",
                advertiser_name=advertiser,
                budget=Decimal(str(budget)),
                currency="USD",
                start_date=approved.date() - timedelta(days=14),
                end_date=approved.date() - timedelta(days=1),
                status="completed",
                approved_at=approved,
                approved_by="demo",
                created_at=approved - timedelta(days=2),
                updated_at=now,
                delivered_impressions=int(budget * 10),
                delivered_amount=Decimal(str(budget)),
                delivery_synced_at=now - timedelta(hours=4),
                raw_request={"demo": True, "packages": []},
            )
        )

    session.flush()


def seed_audit_logs(session, principals: dict[str, Principal]) -> None:
    """Pipeline rows — get_products briefs grouped by (operator, brand_domain).

    Mix of (a) repeat buyers (have priors in 30→7d window — won't be marked NEW)
    and (b) first-contact buyers (no priors — will be marked NEW).
    Also adds a few non-get_products rows for the activity ledger.
    """
    now = datetime.now(UTC)

    # Wipe any prior demo rows so re-runs don't double up
    existing = session.scalars(
        select(AuditLog).where(AuditLog.tenant_id == TENANT_ID).where(AuditLog.adapter_id == "demo_seed")
    ).all()
    for row in existing:
        session.delete(row)
    session.flush()

    # (operator, brand_domain, friendly_name, briefs_in_7d, has_priors, last_brief_minutes_ago)
    pipeline = [
        ("scope3_storefront", "acme.com", "Acme Co", 14, True, 12),
        ("scope3_storefront", "lumen-apparel.com", "Lumen Apparel", 6, True, 47),
        ("kepler_buying", "halcyon-bank.com", "Halcyon Bank", 3, True, 120),
        ("kepler_buying", "aperture-optics.com", "Aperture Optics", 4, False, 240),  # NEW
        ("publicis_dx", "drift-athletics.com", "Drift Athletics", 1, False, 28),  # NEW
        ("publicis_dx", "mariner-logistics.com", "Mariner Logistics", 2, False, 75),  # NEW
    ]

    for operator, domain, friendly, briefs_in_window, has_priors, last_min in pipeline:
        # Most-recent brief within the 7d window, then back-fill (briefs_in_window - 1)
        # earlier ones evenly across the window
        base = now - timedelta(minutes=last_min)
        for i in range(briefs_in_window):
            ts = base - timedelta(hours=i * 6)
            session.add(
                AuditLog(
                    tenant_id=TENANT_ID,
                    timestamp=ts,
                    operation="get_products",
                    principal_id=operator,
                    principal_name=friendly,
                    adapter_id="demo_seed",
                    success=True,
                    details={
                        "operator": operator,
                        "brand_domain": domain,
                        "product_count": 4,
                        "brief_length": 320,
                    },
                )
            )

        # Priors row — pushes (operator, brand_domain) out of the NEW set
        if has_priors:
            session.add(
                AuditLog(
                    tenant_id=TENANT_ID,
                    timestamp=now - timedelta(days=14),
                    operation="get_products",
                    principal_id=operator,
                    principal_name=friendly,
                    adapter_id="demo_seed",
                    success=True,
                    details={"operator": operator, "brand_domain": domain, "product_count": 2, "brief_length": 200},
                )
            )

    # Activity ledger — a handful of mixed events
    for _i, (op, principal_name, mb_id, hours_ago, ok) in enumerate(
        [
            ("create_media_buy", "Sales agent", "demo_mb_inc_1", 0, True),
            ("update_product", "R. Mendez", "demo_prod_3", 1, True),
            ("create_creative", "Sales agent", "creative_a83", 2, True),
            ("get_media_buy_delivery", "System", "demo_mb_run_2", 3, True),
            ("approve_creative", "R. Mendez", "creative_b21", 5, True),
            ("provision_advertiser", "Sales agent", "forge_hardware", 8, True),
        ]
    ):
        session.add(
            AuditLog(
                tenant_id=TENANT_ID,
                timestamp=now - timedelta(hours=hours_ago),
                operation=op,
                principal_id=principal_name,
                principal_name=principal_name,
                adapter_id="demo_seed",
                success=ok,
                details={"media_buy_id": mb_id, "operator": "demo", "brand_domain": "demo.com"},
            )
        )

    session.flush()


def seed_pending_creatives(session, principals: dict[str, Principal]) -> None:
    """A handful of creatives in pending_review so 'Needs your attention'
    has a real number on it."""
    pid = principals["acme_directs"].principal_id

    existing = session.scalars(
        select(Creative).where(Creative.tenant_id == TENANT_ID).where(Creative.creative_id.like("demo_cr_%"))
    ).all()
    for c in existing:
        session.delete(c)
    session.flush()

    base = datetime.now(UTC)
    for i in range(8):
        session.add(
            Creative(
                creative_id=f"demo_cr_{i + 1}",
                tenant_id=TENANT_ID,
                principal_id=pid,
                name=f"Demo creative {i + 1}",
                agent_url="https://demo.test",
                format="display_300x250",
                status="pending_review",
                data={"url": f"https://demo.cdn.test/creative_{i + 1}.jpg"},
                created_at=base - timedelta(hours=i * 6),
                updated_at=base - timedelta(hours=i * 6),
            )
        )
    session.flush()


def main() -> None:
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant = session.scalars(select(Tenant).filter_by(tenant_id=TENANT_ID)).first()
        if not tenant:
            raise SystemExit(f"Tenant '{TENANT_ID}' not found — run migrations first")

        principals = upsert_principals(session)
        upsert_properties(session)
        upsert_products(session)
        seed_media_buys(session, principals)
        seed_audit_logs(session, principals)
        seed_pending_creatives(session, principals)
        session.commit()

    print(
        f"Seeded {TENANT_ID}: 4 principals, 4 properties, 12 products, "
        f"4 incoming + 4 running + 4 completed media buys, "
        f"~30 audit_logs across 6 buyers, 8 pending creatives"
    )


if __name__ == "__main__":
    main()
