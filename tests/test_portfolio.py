from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.db.session import get_session_factory
from app.models.daily_price import DailyPrice
from app.models.stock import Stock
from app.services.portfolio import PortfolioError, PortfolioService
from tests.auth_helpers import analyst_client
from tests.seed_helpers import seed_stock


async def _service() -> PortfolioService:
    factory = get_session_factory()
    session = factory()
    await session.__aenter__()
    return PortfolioService(session)


async def _cleanup(service: PortfolioService) -> None:
    await service.session.commit()
    await service.session.close()


# ---------------------------------------------------------------- service


@pytest.mark.asyncio
async def test_portfolio_create_list_update_delete() -> None:
    service = await _service()
    try:
        portfolio = await service.create({"broker": "Zerodha", "account_name": "Main"})
        assert portfolio.broker == "Zerodha"

        listed = await service.list()
        assert len(listed) == 1

        updated = await service.update(portfolio.id, {"account_name": "Trading"})
        assert updated.account_name == "Trading"

        await service.delete(portfolio.id)
        assert await service.get(portfolio.id) is None
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_portfolio_requires_identifier() -> None:
    service = await _service()
    try:
        with pytest.raises(PortfolioError):
            await service.create({})
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_holding_crud_and_valuation() -> None:
    stock_id = await seed_stock(symbol="PFSTK")
    service = await _service()
    try:
        portfolio = await service.create({"broker": "Zerodha", "account_name": "Main"})
        holding = await service.add_holding(
            portfolio.id,
            {
                "stock_id": stock_id,
                "quantity": Decimal("10"),
                "average_price": Decimal("2000"),
                "purchase_date": date.today() - timedelta(days=10),
            },
        )
        assert holding.portfolio_id == portfolio.id

        with pytest.raises(PortfolioError):
            await service.add_holding(
                portfolio.id, {"stock_id": stock_id, "quantity": 5, "average_price": 100}
            )

        holdings = await service.list_holdings(portfolio.id)
        assert len(holdings) == 1

        valued = await service.value_holdings(holdings)
        row = valued[0]
        assert row["stock"].symbol == "PFSTK"
        assert row["latest_close"] is not None
        assert row["invested"] == 20000.0
        assert row["current_value"] == pytest.approx(10 * row["latest_close"])
        assert row["pl"] == pytest.approx(row["current_value"] - row["invested"])
        assert row["roi_pct"] == pytest.approx(row["pl"] / row["invested"] * 100)
        assert row["allocation_pct"] == pytest.approx(100.0)
        assert row["today_pl"] is not None

        updated = await service.update_holding(
            portfolio.id, holding.id, {"quantity": Decimal("20")}
        )
        assert float(updated.quantity) == 20.0

        await service.remove_holding(portfolio.id, holding.id)
        assert await service.list_holdings(portfolio.id) == []
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_portfolio_valuation_summary() -> None:
    stock_id = await seed_stock(symbol="PFSUM")
    service = await _service()
    try:
        portfolio = await service.create({"broker": "Zerodha", "account_name": "Main"})
        await service.add_holding(
            portfolio.id,
            {"stock_id": stock_id, "quantity": 5, "average_price": 1500},
        )
        valuation = await service.portfolio_valuation(portfolio.id)
        assert valuation["holdings_count"] == 1
        assert valuation["total_invested"] == 7500.0
        assert valuation["total_value"] > 0
        assert valuation["total_pl"] == pytest.approx(valuation["total_value"] - 7500.0)
        assert valuation["today_pl"] is not None

        overall = await service.overall_summary()
        assert overall["holdings_count"] == 1
        assert overall["total_value"] == valuation["total_value"]
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_todays_profit_uses_previous_close() -> None:
    stock_id = await seed_stock(symbol="PFTODAY")
    service = await _service()
    try:
        portfolio = await service.create({"broker": "Zerodha", "account_name": "Main"})
        await service.add_holding(
            portfolio.id, {"stock_id": stock_id, "quantity": 2, "average_price": 1000}
        )
        summary = await service.overall_summary()
        factory = get_session_factory()
        async with factory() as session:
            from sqlalchemy import select

            stock = await session.scalar(select(Stock).where(Stock.symbol == "PFTODAY"))
            latest = await session.scalar(
                select(DailyPrice)
                .where(DailyPrice.stock_id == stock.id)
                .order_by(DailyPrice.trade_date.desc())
                .limit(1)
            )
            expected = (float(latest.close) - float(latest.previous_close)) * 2
        assert summary["today_pl"] == pytest.approx(expected)
    finally:
        await _cleanup(service)


# ------------------------------------------------------------------- API


@pytest.mark.asyncio
async def test_portfolio_api_crud_and_holdings() -> None:
    stock_id = await seed_stock(symbol="PFAPI")
    async with analyst_client(browser=True) as client:
        created = await client.post(
            "/api/v1/portfolios/", json={"broker": "Zerodha", "account_name": "Main"}
        )
        assert created.status_code == 200
        portfolio_id = created.json()["id"]

        listed = await client.get("/api/v1/portfolios/")
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        assert listed.json()[0]["holdings_count"] == 0

        added = await client.post(
            f"/api/v1/portfolios/{portfolio_id}/holdings",
            json={"stock_id": stock_id, "quantity": 10, "average_price": 2000},
        )
        assert added.status_code == 200

        detail = await client.get(f"/api/v1/portfolios/{portfolio_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["holdings_count"] == 1
        assert len(body["holdings"]) == 1
        assert body["holdings"][0]["symbol"] == "PFAPI"
        assert body["holdings"][0]["current_value"] is not None

        summary = await client.get("/api/v1/portfolios/summary")
        assert summary.status_code == 200
        assert summary.json()["holdings_count"] == 1

        deleted = await client.delete(f"/api/v1/portfolios/{portfolio_id}")
        assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_portfolio_api_404_and_validation() -> None:
    async with analyst_client(browser=True) as client:
        response = await client.get("/api/v1/portfolios/999999")
        assert response.status_code == 404
        created = await client.post("/api/v1/portfolios/", json={})
        assert created.status_code == 400


# ------------------------------------------------------------------- UI


@pytest.mark.asyncio
async def test_portfolio_page_renders() -> None:
    await seed_stock(symbol="PFUI")
    service = await _service()
    try:
        await service.create({"broker": "Zerodha", "account_name": "UI Portfolio"})
    finally:
        await _cleanup(service)
    async with analyst_client(browser=True) as client:
        response = await client.get("/portfolio")
        assert response.status_code == 200
        assert "Portfolio" in response.text
        assert "UI Portfolio" in response.text
        assert "Total Value" in response.text


@pytest.mark.asyncio
async def test_dashboard_shows_portfolio_card() -> None:
    await seed_stock(symbol="PFDASH")
    async with analyst_client(browser=True) as client:
        response = await client.get("/dashboard")
        assert response.status_code == 200
        assert "Portfolio Value" in response.text


@pytest.mark.asyncio
async def test_stock_detail_shows_portfolio_position() -> None:
    stock_id = await seed_stock(symbol="PFPOS")
    service = await _service()
    try:
        portfolio = await service.create({"broker": "Zerodha", "account_name": "Main"})
        await service.add_holding(
            portfolio.id, {"stock_id": stock_id, "quantity": 10, "average_price": 2000}
        )
    finally:
        await _cleanup(service)
    async with analyst_client(browser=True) as client:
        response = await client.get("/stocks/PFPOS")
        assert response.status_code == 200
        assert "Portfolio Position" in response.text
