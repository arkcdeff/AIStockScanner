"""Comprehensive Test Suite for Stage 2 Phase G.6.

Official Institutional Flow Acquisition & Market Context Completion.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.routes.market_intelligence_pages import env
from app.db.session import get_session_factory
from app.models.institutional_flow import InstitutionalFlow
from app.services.composite_market_intelligence import (
    FactorInput,
    MarketIntelligenceScoreConfig,
    compute_composite_factor_result,
)
from app.services.institutional_intelligence import InstitutionalIntelligence
from app.services.nse_institutional_import_service import NseInstitutionalImportService

INDIA_TZ = ZoneInfo("Asia/Kolkata")
REFERENCE_DIR = Path("data/reference/institutional")


@pytest.fixture
async def seed_institutional_data():
    """Seed test database with official 62-session institutional flows."""
    csv_file = REFERENCE_DIR / "nse_fii_dii_cash_20260601_20260831.csv"
    async with get_session_factory()() as session:
        service = NseInstitutionalImportService(session)
        await service.import_from_file(csv_file)
        await session.commit()
    return csv_file


def test_institutional_reference_files_exist_and_match_manifest():
    manifest_path = REFERENCE_DIR / "manifest.json"
    assert manifest_path.exists(), "manifest.json must exist"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["dataset"] == "PROVISIONAL_CASH_MARKET_INSTITUTIONAL_ACTIVITY"
    assert manifest["trading_sessions"] == 62
    assert manifest["total_records"] == 124
    assert manifest["unit"] == "INR_CRORE"

    for filename, meta in manifest["files"].items():
        file_path = REFERENCE_DIR / filename
        assert file_path.exists(), f"Reference file {filename} must exist"
        data = file_path.read_bytes()
        assert len(data) == meta["size_bytes"], f"Size mismatch for {filename}"
        assert hashlib.sha256(data).hexdigest() == meta["sha256"], f"SHA256 mismatch for {filename}"


@pytest.mark.asyncio
async def test_institutional_flow_database_row_counts(seed_institutional_data):
    async with get_session_factory()() as session:
        total = (await session.execute(select(func.count(InstitutionalFlow.id)))).scalar_one()
        fii_count = (await session.execute(
            select(func.count(InstitutionalFlow.id)).where(InstitutionalFlow.investor_type == "FII")
        )).scalar_one()
        dii_count = (await session.execute(
            select(func.count(InstitutionalFlow.id)).where(InstitutionalFlow.investor_type == "DII")
        )).scalar_one()
        min_date = (await session.execute(select(func.min(InstitutionalFlow.trade_date)))).scalar_one()
        max_date = (await session.execute(select(func.max(InstitutionalFlow.trade_date)))).scalar_one()

        assert total == 124, f"Expected 124 total rows, got {total}"
        assert fii_count == 62, f"Expected 62 FII rows, got {fii_count}"
        assert dii_count == 62, f"Expected 62 DII rows, got {dii_count}"
        assert min_date == date(2026, 6, 1)
        assert max_date == date(2026, 8, 31)


@pytest.mark.asyncio
async def test_institutional_flow_idempotent_import(seed_institutional_data):
    csv_file = seed_institutional_data
    async with get_session_factory()() as session:
        service = NseInstitutionalImportService(session)
        res = await service.import_from_file(csv_file)
        await session.commit()

        # In an idempotent re-run, no rows should be inserted, only updated
        assert res.inserted == 0, f"Expected 0 inserts on re-run, got {res.inserted}"
        assert res.updated == 124, f"Expected 124 updates on re-run, got {res.updated}"
        assert res.sessions_count == 62

        total = (await session.execute(select(func.count(InstitutionalFlow.id)))).scalar_one()
        assert total == 124


@pytest.mark.asyncio
async def test_institutional_flow_cumulatives_and_values(seed_institutional_data):
    async with get_session_factory()() as session:
        target = date(2026, 8, 31)
        fii = (await session.execute(
            select(InstitutionalFlow).where(
                InstitutionalFlow.trade_date == target,
                InstitutionalFlow.investor_type == "FII"
            )
        )).scalar_one()

        dii = (await session.execute(
            select(InstitutionalFlow).where(
                InstitutionalFlow.trade_date == target,
                InstitutionalFlow.investor_type == "DII"
            )
        )).scalar_one()

        assert fii.buy_value > 0
        assert fii.sell_value > 0
        assert round(fii.net_value, 2) == round(fii.buy_value - fii.sell_value, 2)
        assert fii.one_day_cumulative is not None
        assert fii.three_day_cumulative is not None
        assert fii.five_day_cumulative is not None
        assert fii.twenty_day_cumulative is not None

        assert dii.buy_value > 0
        assert dii.sell_value > 0
        assert round(dii.net_value, 2) == round(dii.buy_value - dii.sell_value, 2)
        assert dii.one_day_cumulative is not None
        assert dii.three_day_cumulative is not None
        assert dii.five_day_cumulative is not None
        assert dii.twenty_day_cumulative is not None


@pytest.mark.asyncio
async def test_institutional_flow_constraints(seed_institutional_data):
    async with get_session_factory()() as session:
        # Check constraint violation
        bad_investor = InstitutionalFlow(
            trade_date=date(2026, 9, 1),
            investor_type="INDIVIDUAL",  # Violates ck_institutional_flow_investor_type
            buy_value=100.0,
            sell_value=50.0,
            net_value=50.0,
            source_name="TEST",
        )
        session.add(bad_investor)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

        # Unique constraint violation
        dup = InstitutionalFlow(
            trade_date=date(2026, 8, 31),
            investor_type="FII",  # Already exists for 2026-08-31
            buy_value=100.0,
            sell_value=50.0,
            net_value=50.0,
            source_name="TEST",
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_institutional_intelligence_evaluation(seed_institutional_data):
    async with get_session_factory()() as session:
        eval_dt = datetime(2026, 9, 1, 6, 0, tzinfo=INDIA_TZ)
        service = InstitutionalIntelligence(session)
        result = await service.evaluate(eval_dt)

        assert result["institutional_score"] is not None
        assert result["direction"] in {"strong_buying", "buying", "neutral", "selling", "strong_selling"}
        assert result["freshness"] == "recent"
        assert result["completeness"] == 100.0
        assert result["cutoff_date"] == "2026-08-31"
        assert "FII" in result["investors"]
        assert "DII" in result["investors"]


@pytest.mark.asyncio
async def test_composite_market_intelligence_reaches_70_percent(seed_institutional_data):
    # Test formula completeness computation with institutional available
    # Must follow canonical FACTOR_NAMES order:
    # ("price_action", "breadth", "sector", "institutional", "volatility", "global", "gift_nifty", "currency", "crude", "macro")
    cfg = MarketIntelligenceScoreConfig()
    factors = [
        FactorInput(name="price_action", score=20.0, configured_weight=Decimal("0.25")),
        FactorInput(name="breadth", score=10.0, configured_weight=Decimal("0.15")),
        FactorInput(name="sector", score=15.0, configured_weight=Decimal("0.10")),
        FactorInput(name="institutional", score=12.0, configured_weight=Decimal("0.10")),
        FactorInput(name="volatility", score=-5.0, configured_weight=Decimal("0.10")),
        FactorInput(name="global", score=None, configured_weight=Decimal("0.10")),
        FactorInput(name="gift_nifty", score=None, configured_weight=Decimal("0.05")),
        FactorInput(name="currency", score=None, configured_weight=Decimal("0.05")),
        FactorInput(name="crude", score=None, configured_weight=Decimal("0.05")),
        FactorInput(name="macro", score=None, configured_weight=Decimal("0.05")),
    ]
    res = compute_composite_factor_result(factors, cfg)
    assert res.data_completeness_percent == 70.0, f"Expected 70.0%, got {res.data_completeness_percent}%"
    assert len(res.missing_factors) == 5, f"Expected 5 missing factors, got {len(res.missing_factors)}"
    missing_names = {f.name for f in res.missing_factors}
    assert "institutional" not in missing_names


def test_html_component_renders_institutional():
    template = env.get_template("market_intelligence/components/institutional.html")
    context = {
        "institutional": {
            "score": 10.99,
            "institutional_score": 10.99,
            "direction": "neutral",
            "confidence": 95.5,
            "completeness": 100.0,
            "freshness": "recent",
            "combined": {
                "one_day_net": -2813.25,
                "three_day_net": 4403.51,
                "five_day_net": 11703.29,
                "twenty_day_net": 44474.23,
            },
            "investors": {
                "FII": {
                    "buy_value": 65512.88,
                    "sell_value": 71652.44,
                    "net_value": -6139.56,
                    "direction": "selling",
                    "score": -36.94,
                    "freshness": "recent",
                    "cumulative": {1: -6139.56, 3: -6770.07, 5: -2699.57, 20: 8624.98},
                },
                "DII": {
                    "buy_value": 18583.03,
                    "sell_value": 15256.72,
                    "net_value": 3326.31,
                    "direction": "strong_buying",
                    "score": 100.0,
                    "freshness": "recent",
                    "cumulative": {1: 3326.31, 3: 11173.58, 5: 14402.86, 20: 35849.25},
                },
            },
        }
    }
    html = template.render(**context)
    assert "FII" in html
    assert "DII" in html
    assert "Combined Score" in html
    assert "65,512 Cr" in html
    assert "18,583 Cr" in html
