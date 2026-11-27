"""Steps for BR-ADMIN-MEDIA-BUY-PAGE: what the operator's media buy page shows (#2647)."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup
from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.domain.admin_accounts import _require_admin_page
from tests.factories.mint import mint_id

if TYPE_CHECKING:
    from tests.harness.admin_accounts import AdminAccountEnv


def _env(ctx: dict) -> AdminAccountEnv:
    return ctx["env"]


def _buy_id(ctx: dict, label: str) -> str:
    """The minted id of the buy the scenario calls *label*: the e2e stack's database outlives a test."""
    return ctx.setdefault("admin_media_buys", {}).setdefault(label, mint_id("mb"))


@given(
    parsers.parse(
        'the buy "{label}" holds package "{package_id}" with a budget of {budget:d}, created with {created:d}'
    )
)
def given_buy_package_budget(ctx: dict, label: str, package_id: str, budget: int, created: int) -> None:
    """Store the row an updated package holds: the budget column at *budget*, package_config at *created*."""
    _env(ctx).seed_media_buy_package(
        _buy_id(ctx, label), package_id, budget=Decimal(budget), created_budget=float(created)
    )


@when(parsers.parse('the operator opens the page of the buy "{label}"'))
def when_operator_opens_buy_page(ctx: dict, label: str) -> None:
    ctx["admin_page"] = _env(ctx).get_media_buy_page(_buy_id(ctx, label))


@then(parsers.parse('the page shows the package "{package_id}" with the budget "{budget}"'))
def then_page_shows_package_budget(ctx: dict, package_id: str, budget: str) -> None:
    """The ``Budget:`` row of the package's card on the rendered page reads *budget*."""
    page = _require_admin_page(ctx)
    assert page.status_code == 200, f"the buy page answered {page.status_code}"
    soup = BeautifulSoup(page.data, "html.parser")
    headers = [s for s in soup.find_all("strong") if s.get_text(strip=True) == f"📦 {package_id}"]
    assert len(headers) == 1, f"the page shows {len(headers)} cards for package {package_id!r}"
    card = headers[0].parent.parent
    rows = {
        row.find(class_="label").get_text(strip=True): row.find(class_="value").get_text(strip=True)
        for row in card.find_all(class_="detail-row")
    }
    assert rows.get("Budget:") == budget, f"package {package_id!r} shows {rows}"
