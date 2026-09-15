from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.models.alert import AlertEvent
from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.stock import Stock
from app.services.alert import AlertEngineError, AlertService
from app.services.analytics_engine import AnalyticsEngine
from app.services.eod_pipeline import EODPipelineService
from tests.auth_helpers import analyst_client
from tests.seed_helpers import seed_stock


async def _service() -> AlertService:
    factory = get_session_factory()
    session = factory()
    await session.__aenter__()
    return AlertService(session)


async def _cleanup(service: AlertService) -> None:
    await service.session.commit()
    await service.session.close()


async def _run_engine() -> dict[str, int]:
    factory = get_session_factory()
    async with factory() as session:
        return await AlertService(session).run_for_trade_date()


async def _count_events() -> int:
    factory = get_session_factory()
    async with factory() as session:
        return await session.scalar(select(func.count()).select_from(AlertEvent)) or 0


async def _latest_price(symbol: str) -> DailyPrice:
    factory = get_session_factory()
    async with factory() as session:
        stock = await session.scalar(select(Stock).where(Stock.symbol == symbol))
        return await session.scalar(
            select(DailyPrice)
            .where(DailyPrice.stock_id == stock.id)
            .order_by(DailyPrice.trade_date.desc())
            .limit(1)
        )


async def _set_latest_close(symbol: str, close: float) -> None:
    factory = get_session_factory()
    async with factory() as session:
        stock = await session.scalar(select(Stock).where(Stock.symbol == symbol))
        price = await session.scalar(
            select(DailyPrice)
            .where(DailyPrice.stock_id == stock.id)
            .order_by(DailyPrice.trade_date.desc())
            .limit(1)
        )
        price.close = close
        price.volume = 4000000
        await session.commit()


# ---------------------------------------------------------------- rule CRUD


@pytest.mark.asyncio
async def test_rule_create_update_list_delete() -> None:
    service = await _service()
    try:
        rule = await service.create_rule(
            {
                "name": "High Score",
                "rule_type": "overall_score",
                "operator": ">",
                "value": 70,
            }
        )
        assert rule.name == "High Score"
        assert rule.enabled is True

        updated = await service.update_rule(rule.id, {"value": 80, "enabled": False})
        assert updated.value == 80
        assert updated.enabled is False

        rules = await service.list_rules()
        assert len(rules) == 1

        await service.delete_rule(rule.id)
        assert await service.get_rule(rule.id) is None
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_rule_invalid_type_rejected() -> None:
    service = await _service()
    try:
        with pytest.raises(AlertEngineError):
            await service.create_rule(
                {"name": "Bad", "rule_type": "nonsense", "operator": ">", "value": 1}
            )
    finally:
        await _cleanup(service)


# ---------------------------------------------------------------- engine


@pytest.mark.asyncio
async def test_overall_score_rule_triggers() -> None:
    await seed_stock(symbol="ALRTOVER")
    service = await _service()
    try:
        await service.create_rule(
            {"name": "Score 50+", "rule_type": "overall_score", "operator": ">", "value": 50}
        )
    finally:
        await _cleanup(service)
    result = await _run_engine()
    assert result["triggered"] >= 1
    assert await _count_events() >= 1


@pytest.mark.asyncio
async def test_rsi_rule_triggers() -> None:
    await seed_stock(symbol="ALRTRSI")
    service = await _service()
    try:
        await service.create_rule(
            {"name": "RSI High", "rule_type": "rsi", "operator": ">", "value": 50}
        )
    finally:
        await _cleanup(service)
    result = await _run_engine()
    assert result["triggered"] >= 1


@pytest.mark.asyncio
async def test_delivery_rule_triggers() -> None:
    await seed_stock(symbol="ALRTDEL")
    service = await _service()
    try:
        await service.create_rule(
            {"name": "Delivery", "rule_type": "delivery", "operator": ">", "value": 50}
        )
    finally:
        await _cleanup(service)
    result = await _run_engine()
    assert result["triggered"] >= 1


@pytest.mark.asyncio
async def test_volume_rule_triggers() -> None:
    await seed_stock(symbol="ALRTVOL")
    service = await _service()
    try:
        await service.create_rule(
            {"name": "Volume 1x", "rule_type": "volume", "operator": ">", "value": 1}
        )
    finally:
        await _cleanup(service)
    result = await _run_engine()
    assert result["triggered"] >= 1


@pytest.mark.asyncio
async def test_breakout_rule_triggers() -> None:
    await seed_stock(symbol="ALRTBRK")
    await _set_latest_close("ALRTBRK", 4000.0)
    factory = get_session_factory()
    async with factory() as session:
        await AnalyticsEngine(session).calculate_for_stock("ALRTBRK")
        await session.commit()

    service = await _service()
    try:
        await service.create_rule(
            {"name": "Breakout", "rule_type": "breakout", "operator": "true", "value": None}
        )
    finally:
        await _cleanup(service)
    result = await _run_engine()
    assert result["triggered"] >= 1


@pytest.mark.asyncio
async def test_resistance_broken_rule_triggers() -> None:
    await seed_stock(symbol="ALRTRES")
    await _set_latest_close("ALRTRES", 5000.0)
    service = await _service()
    try:
        await service.create_rule(
            {
                "name": "Resistance",
                "rule_type": "resistance_broken",
                "operator": "true",
                "value": None,
            }
        )
    finally:
        await _cleanup(service)
    result = await _run_engine()
    assert result["triggered"] >= 1


@pytest.mark.asyncio
async def test_support_broken_rule_triggers() -> None:
    await seed_stock(symbol="ALRTSUP")
    await _set_latest_close("ALRTSUP", 500.0)
    service = await _service()
    try:
        await service.create_rule(
            {
                "name": "Support",
                "rule_type": "support_broken",
                "operator": "true",
                "value": None,
            }
        )
    finally:
        await _cleanup(service)
    result = await _run_engine()
    assert result["triggered"] >= 1


@pytest.mark.asyncio
async def test_ema_cross_rule_triggers() -> None:
    factory = get_session_factory()
    await seed_stock(symbol="ALRTCROSS", calculate_analytics=False)
    async with factory() as session:
        stock = await session.scalar(select(Stock).where(Stock.symbol == "ALRTCROSS"))
        base = await session.scalar(
            select(DailyPrice)
            .where(DailyPrice.stock_id == stock.id)
            .order_by(DailyPrice.trade_date.desc())
            .limit(1)
        )
        prev_date = base.trade_date - timedelta(days=1)
        prev = IndicatorResult(
            stock_id=stock.id,
            trade_date=prev_date,
            ema_20=40.0,
            ema_50=50.0,
            rsi_14=55.0,
        )
        session.add(prev)
        existing = await session.scalar(
            select(IndicatorResult)
            .where(
                IndicatorResult.stock_id == stock.id,
                IndicatorResult.trade_date == base.trade_date,
            )
        )
        existing.ema_20 = 60.0
        existing.ema_50 = 50.0
        existing.rsi_14 = 60.0
        await session.flush()
        await AnalyticsEngine(session).calculate_for_stock("ALRTCROSS")
        await session.commit()

    service = await _service()
    try:
        await service.create_rule(
            {
                "name": "EMA Cross",
                "rule_type": "ema_cross",
                "operator": "crosses_above",
                "value": None,
            }
        )
    finally:
        await _cleanup(service)
    result = await _run_engine()
    assert result["triggered"] >= 1


@pytest.mark.asyncio
async def test_cooldown_prevents_repeat_trigger() -> None:
    await seed_stock(symbol="ALRTCOOL")
    service = await _service()
    try:
        await service.create_rule(
            {
                "name": "Score 50+",
                "rule_type": "overall_score",
                "operator": ">",
                "value": 50,
                "cooldown_minutes": 100000,
            }
        )
    finally:
        await _cleanup(service)
    first = await _run_engine()
    second = await _run_engine()
    assert first["triggered"] >= 1
    assert second["triggered"] == 0
    assert await _count_events() == 1


@pytest.mark.asyncio
async def test_rule_scoped_to_watchlist() -> None:
    await seed_stock(symbol="ALRTWL")
    from app.services.watchlist import WatchlistService

    factory = get_session_factory()
    async with factory() as session:
        wl = await WatchlistService(session).create({"name": "Scoped"})
        stock_id = await session.scalar(select(Stock.id).where(Stock.symbol == "ALRTWL"))
        await WatchlistService(session).add_stock(wl.id, stock_id)
        await session.commit()

    async with factory() as session:
        await AlertService(session).create_rule(
            {
                "name": "Scoped Rule",
                "rule_type": "overall_score",
                "operator": ">",
                "value": 50,
                "watchlist_id": wl.id,
            }
        )
        result = await AlertService(session).run_for_trade_date()
    assert result["triggered"] >= 1


# ------------------------------------------------------------------- API


@pytest.mark.asyncio
async def test_alerts_api_rules_crud() -> None:
    async with analyst_client() as client:
        created = await client.post(
            "/api/v1/alerts/rules",
            json={"name": "API Rule", "rule_type": "rsi", "operator": ">", "value": 60},
        )
        assert created.status_code == 200
        rule_id = created.json()["id"]
        assert created.json()["rule_type"] == "rsi"

        listed = await client.get("/api/v1/alerts/rules")
        assert listed.status_code == 200
        assert len(listed.json()) == 1

        fetched = await client.get(f"/api/v1/alerts/rules/{rule_id}")
        assert fetched.status_code == 200

        updated = await client.put(f"/api/v1/alerts/rules/{rule_id}", json={"enabled": False})
        assert updated.status_code == 200
        assert updated.json()["enabled"] is False

        deleted = await client.delete(f"/api/v1/alerts/rules/{rule_id}")
        assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_alerts_api_events_and_status() -> None:
    await seed_stock(symbol="ALRTAPI")
    async with analyst_client() as client:
        await client.post(
            "/api/v1/alerts/rules",
            json={"name": "API Rule", "rule_type": "overall_score", "operator": ">", "value": 50},
        )
        run = await client.post("/api/v1/alerts/run")
        assert run.status_code == 200
        assert run.json()["triggered"] >= 1

        pending = await client.get("/api/v1/alerts/events/pending")
        assert pending.status_code == 200
        assert len(pending.json()) >= 1
        event_id = pending.json()[0]["id"]
        assert pending.json()[0]["symbol"] == "ALRTAPI"

        events = await client.get("/api/v1/alerts/events")
        assert events.status_code == 200
        assert len(events.json()) >= 1

        by_symbol = await client.get("/api/v1/alerts/events/symbol/ALRTAPI")
        assert by_symbol.status_code == 200
        assert len(by_symbol.json()) >= 1

        acked = await client.patch(
            f"/api/v1/alerts/events/{event_id}", json={"status": "acknowledged"}
        )
        assert acked.status_code == 200
        assert acked.json()["status"] == "acknowledged"

        still_pending = await client.get("/api/v1/alerts/events/pending")
        assert all(event["status"] == "triggered" for event in still_pending.json())


# ------------------------------------------------------------------- UI


@pytest.mark.asyncio
async def test_alerts_page_renders() -> None:
    async with analyst_client(browser=True) as client:
        response = await client.get("/alerts")
        assert response.status_code == 200
        assert "Alert Rules" in response.text
        assert "Recent Alert Events" in response.text


@pytest.mark.asyncio
async def test_dashboard_shows_alert_cards() -> None:
    async with analyst_client(browser=True) as client:
        response = await client.get("/dashboard")
        assert response.status_code == 200
        assert "Alert Rules" in response.text


@pytest.mark.asyncio
async def test_stock_detail_shows_matching_alerts() -> None:
    await seed_stock(symbol="ALRTPNL")
    async with analyst_client(browser=True) as client:
        response = await client.get("/stocks/ALRTPNL")
        assert response.status_code == 200
        assert "Matching Alerts" in response.text


# ------------------------------------------------------------- pipeline


def _bhavcopy_bytes(trade_date: date) -> bytes:
    lines = [
        "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN"
    ]
    stamp = trade_date.strftime("%d-%b-%Y").upper()
    base = 1500
    lines.append(
        f"RELIANCE,EQ,{base},{base + 30},{base - 10},{base + 20},{base + 19},{base},"
        f"100000,152000000,{stamp},5000,INE002A01018"
    )
    return ("\n".join(lines) + "\n").encode()


@pytest.mark.asyncio
async def test_pipeline_runs_alert_stage() -> None:
    await seed_stock(symbol="RELIANCE")
    trade_date = date.today()
    if trade_date.weekday() >= 5:
        trade_date = trade_date - timedelta(days=(trade_date.weekday() - 4))
    bhav_name = f"cm{trade_date.strftime('%d%b%Y').upper()}bhav.csv"

    service = await _service()
    try:
        await service.create_rule(
            {"name": "Any Score", "rule_type": "overall_score", "operator": ">", "value": 0}
        )
        pipeline = EODPipelineService()
        run = await pipeline.run_from_file(
            bhavcopy=_bhavcopy_bytes(trade_date), bhavcopy_name=bhav_name
        )
        assert run.status == "completed"
        assert run.alerts_triggered >= 1
        assert await _count_events() >= 1
    finally:
        await _cleanup(service)
