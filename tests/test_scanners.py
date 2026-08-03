from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.scan_definition import ScanDefinition
from app.services.scanner_engine import ScannerRegistry


def test_scanner_registry_has_36_scanners() -> None:
    scanners = ScannerRegistry.all()
    assert len(scanners) == 36


@pytest.mark.asyncio
async def test_api_list_scanners() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/scanners")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 36
        codes = [item["code"] for item in body]
        expected = [
            "ema_bullish_crossover",
            "ema_bearish_crossover",
            "golden_cross",
            "death_cross",
            "price_above_ema20",
            "price_below_ema20",
            "supertrend_bullish",
            "supertrend_bearish",
            "rsi_oversold",
            "rsi_overbought",
            "macd_bullish",
            "macd_bearish",
            "stochastic_oversold",
            "stochastic_overbought",
            "macd_above_zero",
            "macd_below_zero",
            "bollinger_band_squeeze",
            "price_above_upper_bollinger",
            "price_below_lower_bollinger",
            "atr_high_volatility",
            "obv_rising",
            "vwap_bullish",
            "vwap_bearish",
            "strong_trend_adx",
            "weak_trend_adx",
            "volume_2x_average",
            "volume_5x_average",
            "highest_volume_20d",
            "volume_breakout",
            "delivery_above_60",
            "delivery_above_70",
            "delivery_above_average",
            "delivery_breakout",
            "high_volume_high_delivery",
            "price_breakout_high_delivery",
            "accumulation",
        ]
        for code in expected:
            assert code in codes


@pytest.mark.asyncio
async def test_api_run_scanner_not_found() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/scanners/run?scan_code=nonexistent")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_api_run_all_scanners() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/scanners/run-all")
        assert response.status_code == 200
        body = response.json()
        assert "total_scanners" in body
        assert "total_matched" in body
        assert "execution_time_ms" in body
        assert "results" in body


@pytest.mark.asyncio
async def test_api_list_scan_results() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/scanners/results")
        assert response.status_code == 200
        assert isinstance(response.json(), list)
