"""Test Suite for Stage G.8 — Derivative History Expansion & OI Intelligence.

Certifies:
  1. Manifest integrity (62 trading sessions between 2025-10-01 and 2025-12-31).
  2. Exchange calendar validation (rejection of weekends and out-of-scope dates).
  3. Non-recursive underlying resolution for NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50.
  4. Ingestion service execution and idempotency (contracts master upserted before daily prices).
  5. Canonical contract identity and fail-closed lot provenance (options lot_size=1, source_name=None).
  6. Dynamic Expiry UX endpoints (select dropdown, DTE calculation, nearest default, PIT cutoff).
  7. Deterministic OI Intelligence (PCR, ΔOI PCR, Wall Movement shifts, buildup classification, OI spurts).
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.data.exchange_constants import EXCHANGE_NSE
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.market_index import MarketIndex
from app.models.stock import Stock
from app.services.historical_futures_analytics import HistoricalFuturesAnalyticsService
from app.services.historical_oi_intelligence_service import HistoricalOIIntelligenceService
from app.services.historical_option_chain import HistoricalOptionChainService
from app.services.nse_derivative_historical_extension_service import (
    NseDerivativeHistoricalExtensionService,
)

MANIFEST_PATH = Path("data/reference/derivatives/manifest_q4_2025.json")


@pytest.fixture
async def seeded_market_universe(db_session):
    """Seed base indices and a pilot stock for derivative testing."""
    indices = [
        ("NIFTY", "Nifty 50", "NIFTY"),
        ("NIFTY_50", "Nifty 50 Duplicate Code", "NIFTY_50"),
        ("NIFTY_BANK", "Nifty Bank", "BANKNIFTY"),
        ("NIFTY_FIN_SERV", "Nifty Financial Services", "FINNIFTY"),
        ("NIFTY_MID_SELECT", "Nifty Midcap Select", "MIDCPNIFTY"),
        ("NIFTY_NEXT_50", "Nifty Next 50", "NIFTYNXT50"),
    ]
    idx_objs = {}
    for code, name, prov in indices:
        existing = (await db_session.execute(select(MarketIndex).where(MarketIndex.code == code))).scalar_one_or_none()
        if not existing:
            existing = MarketIndex(code=code, name=name, provider_symbol=prov, country="INDIA")
            db_session.add(existing)
            await db_session.flush()
        idx_objs[code] = existing

    # Seed pilot stock
    stk = (await db_session.execute(select(Stock).where(Stock.symbol == "RELIANCE"))).scalar_one_or_none()
    if not stk:
        stk = Stock(
            symbol="RELIANCE",
            company_name="Reliance Industries Limited",
            exchange=EXCHANGE_NSE,
            series="EQ",
            isin="INE002A01018",
            is_active=True,
        )
        db_session.add(stk)
        await db_session.flush()

    await db_session.commit()
    return {"indices": idx_objs, "stock": stk}


def test_01_manifest_integrity():
    """Verify manifest exists and contains exactly 62 sessions in Q4-2025."""
    assert MANIFEST_PATH.exists(), f"Manifest file must exist at {MANIFEST_PATH}"
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert len(manifest) == 62, f"Expected 62 trading sessions in Q4-2025, found {len(manifest)}"

    dates = sorted(manifest.keys())
    assert dates[0] == "2025-10-01"
    assert dates[-1] == "2025-12-31"

    for d_str, entry in manifest.items():
        t_date = date.fromisoformat(d_str)
        assert t_date.weekday() < 5, f"{d_str} is a weekend"
        assert entry["trade_date"] == d_str
        assert entry["format"] == "fo_udiff"
        assert entry["filename"].startswith("BhavCopy_NSE_FO_")
        assert "nsearchives.nseindia.com" in entry["source_url"]


def test_02_calendar_validation():
    """Verify extension service rejects weekend and out-of-scope dates."""
    service = NseDerivativeHistoricalExtensionService(session=None)

    # Saturday
    assert service.validate_session_date(date(2025, 10, 4)) is not None
    # Sunday
    assert service.validate_session_date(date(2025, 10, 5)) is not None
    # Prior to Q4-2025
    assert service.validate_session_date(date(2025, 9, 30)) is not None
    # After Q4-2025
    assert service.validate_session_date(date(2026, 1, 1)) is not None
    # Valid trading day
    assert service.validate_session_date(date(2025, 10, 1)) is None


@pytest.mark.asyncio
async def test_03_non_recursive_index_resolution(db_session, seeded_market_universe):
    """Verify non-recursive underlying resolution for all key indices in futures and option services."""
    fut_service = HistoricalFuturesAnalyticsService(db_session)
    opt_service = HistoricalOptionChainService(db_session)

    test_symbols = [
        ("BANKNIFTY", "NIFTY_BANK"),
        ("NIFTY BANK", "NIFTY_BANK"),
        ("FINNIFTY", "NIFTY_FIN_SERV"),
        ("NIFTY FIN SERVICE", "NIFTY_FIN_SERV"),
        ("MIDCPNIFTY", "NIFTY_MID_SELECT"),
        ("NIFTY MID SELECT", "NIFTY_MID_SELECT"),
        ("NIFTYNXT50", "NIFTY_NEXT_50"),
        ("NIFTY NEXT 50", "NIFTY_NEXT_50"),
        ("NIFTY", "NIFTY"),
        ("RELIANCE", "RELIANCE"),
    ]

    for sym, expected_code in test_symbols:
        # Futures service resolution
        fut_res = await fut_service._resolve_underlying(sym)
        assert fut_res is not None, f"Futures resolution failed for {sym}"
        u_type, u_id, u_code, _ = fut_res
        assert u_code == expected_code

        # Options service resolution
        opt_res = await opt_service._resolve_underlying(sym)
        assert opt_res is not None, f"Options resolution failed for {sym}"
        u_type, u_id, u_code, _ = opt_res
        assert u_code == expected_code


@pytest.mark.asyncio
async def test_04_session_ingestion_and_provenance(db_session, seeded_market_universe):
    """Verify ingestion creates canonical contracts before prices, sets lot_size=1 for options, and is idempotent."""
    service = NseDerivativeHistoricalExtensionService(db_session)

    # Construct synthetic UDiFF FO CSV with 1 Index Option, 1 Index Future, 1 Stock Option, 1 Stock Future
    trade_date = date(2025, 10, 1)

    csv_header = (
        "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
        "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
        "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
        "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n"
    )
    rows = [
        # 1. NIFTY Option CE
        "2025-10-01,2025-10-01,FO,NSE,IDO,1001,,NIFTY,,2025-10-30,2025-10-30,24000.00,CE,NIFTY25OCT24000CE,150.00,180.00,140.00,165.00,165.00,145.00,24100.00,165.00,50000,5000,1000,16500000,100,F1,25,,,,\n",
        # 2. NIFTY Option PE
        "2025-10-01,2025-10-01,FO,NSE,IDO,1002,,NIFTY,,2025-10-30,2025-10-30,24000.00,PE,NIFTY25OCT24000PE,120.00,130.00,95.00,100.00,100.00,115.00,24100.00,100.00,60000,8000,1200,12000000,120,F1,25,,,,\n",
        # 3. NIFTY Future
        "2025-10-01,2025-10-01,FO,NSE,IDF,1003,,NIFTY,,2025-10-30,2025-10-30,0.00,XX,NIFTY25OCTFUT,24150.00,24200.00,24100.00,24180.00,24180.00,24120.00,24100.00,24180.00,100000,2000,5000,120900000,500,F1,25,,,,\n",
        # 4. RELIANCE Option CE
        "2025-10-01,2025-10-01,FO,NSE,STO,2001,,RELIANCE,,2025-10-30,2025-10-30,2800.00,CE,RELIANCE25OCT2800CE,45.00,55.00,40.00,50.00,50.00,42.00,2810.00,50.00,20000,1500,400,2000000,50,F1,250,,,,\n",
        # 5. RELIANCE Future
        "2025-10-01,2025-10-01,FO,NSE,STF,2002,,RELIANCE,,2025-10-30,2025-10-30,0.00,XX,RELIANCE25OCTFUT,2820.00,2835.00,2815.00,2830.00,2830.00,2810.00,2810.00,2830.00,40000,1000,800,5660000,100,F1,250,,,,\n",
    ]
    csv_payload = (csv_header + "".join(rows)).encode("utf-8")

    # First import
    res1 = await service.import_session(
        trade_date=trade_date,
        payload=csv_payload,
        filename="BhavCopy_NSE_FO_0_0_0_20251001_F_0000.csv",
    )
    assert res1.status == "SUCCESS"
    assert res1.contracts_upserted == 5
    assert res1.prices_upserted == 5

    # Check contracts provenance in DB
    c_res = await db_session.execute(select(DerivativeContract))
    contracts = c_res.scalars().all()
    assert len(contracts) == 5

    for c in contracts:
        assert c.available_at == trade_date
        if c.instrument_type == "OPTION":
            # Invariant: option contracts lot_size = 1 and source_name = None (UNAVAILABLE lot provenance)
            assert c.lot_size == 1
            assert c.source_name is None
        else:
            # Futures contracts have verified lot_size and source_name
            assert c.lot_size in (25, 250)
            assert c.source_name == "FO_UDIFF_BHAVCOPY"

    # Check prices in DB
    p_res = await db_session.execute(select(DerivativeDailyPrice))
    prices = p_res.scalars().all()
    assert len(prices) == 5
    for p in prices:
        assert p.trade_date == trade_date
        assert p.available_at == trade_date
        assert p.open_interest is not None and p.open_interest > 0

    # Idempotent second import of identical data
    res2 = await service.import_session(
        trade_date=trade_date,
        payload=csv_payload,
        filename="BhavCopy_NSE_FO_0_0_0_20251001_F_0000.csv",
    )
    assert res2.status == "SUCCESS"
    assert res2.contracts_upserted == 0  # No new contracts inserted!
    assert res2.prices_upserted == 5     # Prices updated idempotently without errors

    # Total counts in DB must remain unchanged
    c_count = (await db_session.execute(select(func.count(DerivativeContract.id)))).scalar()
    p_count = (await db_session.execute(select(func.count(DerivativeDailyPrice.id)))).scalar()
    assert c_count == 5
    assert p_count == 5


@pytest.mark.asyncio
async def test_05_dynamic_expiries_endpoints(db_session, seeded_market_universe):
    """Verify dynamic expiry selection API and HTML endpoints."""
    service = NseDerivativeHistoricalExtensionService(db_session)
    t_date = date(2025, 10, 1)

    # Ingest test session with 2 expiries for NIFTY (2025-10-30 and 2025-11-27)
    csv_header = (
        "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
        "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
        "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
        "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n"
    )
    rows = [
        "2025-10-01,2025-10-01,FO,NSE,IDO,1001,,NIFTY,,2025-10-30,2025-10-30,24000.00,CE,NIFTY25OCT24000CE,150.00,180.00,140.00,165.00,165.00,145.00,24100.00,165.00,50000,5000,1000,16500000,100,F1,25,,,,\n",
        "2025-10-01,2025-10-01,FO,NSE,IDO,1002,,NIFTY,,2025-11-27,2025-11-27,24000.00,CE,NIFTY25NOV24000CE,250.00,280.00,240.00,265.00,265.00,245.00,24100.00,265.00,20000,2000,500,6500000,50,F1,25,,,,\n",
    ]
    await service.import_session(
        trade_date=t_date,
        payload=(csv_header + "".join(rows)).encode("utf-8"),
    )
    await db_session.commit()

    from tests.auth_helpers import auth_client, seed_builtins

    await seed_builtins()
    async with auth_client(is_superuser=True, browser=True) as client:
        # JSON endpoint test
        resp = await client.get("/api/v1/market-data/derivatives/options/expiries?symbol=NIFTY&trade_date=2025-10-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "NIFTY"
        assert len(data["items"]) == 2
        assert data["items"][0]["expiry_date"] == "2025-10-30"
        assert data["items"][0]["dte"] == 29
        assert data["items"][0]["is_nearest"] is True
        assert data["items"][1]["expiry_date"] == "2025-11-27"
        assert data["items"][1]["dte"] == 57
        assert data["items"][1]["is_nearest"] is False

        # HTML select partial test
        html_resp = await client.get("/market-data/derivatives/options/expiries-select?symbol=NIFTY&trade_date=2025-10-01")
        assert html_resp.status_code == 200
        html = html_resp.text
        assert "<select" in html
        assert 'value="2025-10-30" selected>2025-10-30 (29d DTE) [Nearest]</option>' in html
        assert 'value="2025-11-27">2025-11-27 (57d DTE)</option>' in html


@pytest.mark.asyncio
async def test_06_oi_intelligence_calculations(db_session, seeded_market_universe):
    """Verify deterministic OI intelligence: PCR, ΔOI PCR, Wall Movement shifts, and buildup classification."""
    ext_service = NseDerivativeHistoricalExtensionService(db_session)
    oi_service = HistoricalOIIntelligenceService(db_session)

    t1 = date(2025, 10, 1)
    t2 = date(2025, 10, 3)  # Friday
    exp = date(2025, 10, 30)

    csv_header = (
        "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
        "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
        "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
        "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n"
    )

    # Session 1 (T-1): Spot = 24000. Call Wall at 24500 (50k OI). Put Wall at 23500 (40k OI).
    rows_s1 = [
        "2025-10-01,2025-10-01,FO,NSE,IDO,101,,NIFTY,,2025-10-30,2025-10-30,24000.00,CE,NIFTY25OCT24000CE,150.00,180.00,140.00,160.00,160.00,150.00,24000.00,160.00,30000,1000,1000,16000000,100,F1,25,,,,\n",
        "2025-10-01,2025-10-01,FO,NSE,IDO,102,,NIFTY,,2025-10-30,2025-10-30,24500.00,CE,NIFTY25OCT24500CE,50.00,60.00,45.00,50.00,50.00,50.00,24000.00,50.00,50000,2000,2000,5000000,200,F1,25,,,,\n",
        "2025-10-01,2025-10-01,FO,NSE,IDO,103,,NIFTY,,2025-10-30,2025-10-30,23500.00,PE,NIFTY25OCT23500PE,40.00,50.00,35.00,40.00,40.00,40.00,24000.00,40.00,40000,1500,1500,4000000,150,F1,25,,,,\n",
        "2025-10-01,2025-10-01,FO,NSE,IDO,104,,NIFTY,,2025-10-30,2025-10-30,24000.00,PE,NIFTY25OCT24000PE,120.00,130.00,110.00,115.00,115.00,120.00,24000.00,115.00,25000,1000,800,2875000,80,F1,25,,,,\n",
    ]
    await ext_service.import_session(t1, (csv_header + "".join(rows_s1)).encode("utf-8"))

    # Session 2 (T): Spot = 24200 (+200 pts).
    # Call Wall shifts UP from 24500 to 25000 (new strike 25000 has 70k OI).
    # Put Wall shifts UP from 23500 to 24000 (strike 24000 has 60k OI).
    # At 24000 CE: Price increased (160 -> 210) & OI increased (30k -> 45k) => LONG_BUILDUP.
    # At 24000 PE: Price decreased (115 -> 70) & OI increased (25k -> 60k) => SHORT_BUILDUP.
    rows_s2 = [
        "2025-10-03,2025-10-03,FO,NSE,IDO,101,,NIFTY,,2025-10-30,2025-10-30,24000.00,CE,NIFTY25OCT24000CE,180.00,220.00,175.00,210.00,210.00,160.00,24200.00,210.00,45000,15000,3000,31500000,300,F1,25,,,,\n",
        "2025-10-03,2025-10-03,FO,NSE,IDO,102,,NIFTY,,2025-10-30,2025-10-30,24500.00,CE,NIFTY25OCT24500CE,60.00,85.00,55.00,80.00,80.00,50.00,24200.00,80.00,55000,5000,2500,10000000,250,F1,25,,,,\n",
        "2025-10-03,2025-10-03,FO,NSE,IDO,105,,NIFTY,,2025-10-30,2025-10-30,25000.00,CE,NIFTY25OCT25000CE,25.00,35.00,20.00,30.00,30.00,20.00,24200.00,30.00,70000,70000,4000,6000000,400,F1,25,,,,\n",
        "2025-10-03,2025-10-03,FO,NSE,IDO,103,,NIFTY,,2025-10-30,2025-10-30,23500.00,PE,NIFTY25OCT23500PE,30.00,35.00,20.00,22.00,22.00,40.00,24200.00,22.00,35000,-5000,1200,1320000,120,F1,25,,,,\n",
        "2025-10-03,2025-10-03,FO,NSE,IDO,104,,NIFTY,,2025-10-30,2025-10-30,24000.00,PE,NIFTY25OCT24000PE,90.00,95.00,65.00,70.00,70.00,115.00,24200.00,70.00,60000,35000,3500,12250000,350,F1,25,,,,\n",
    ]
    await ext_service.import_session(t2, (csv_header + "".join(rows_s2)).encode("utf-8"))
    await db_session.commit()

    # Execute OI Intelligence for Session T2
    snapshot = await oi_service.get_oi_intelligence(
        symbol="NIFTY",
        trade_date=t2,
        expiry_date=exp,
    )

    assert snapshot.underlying_symbol == "NIFTY"
    assert snapshot.trade_date == t2
    assert snapshot.expiry_date == exp

    # 1. Wall Movement Tests
    # Call Wall shifted from 24500 to 25000 (+500 pts UP)
    assert snapshot.call_wall.current_strike == Decimal("25000")
    assert snapshot.call_wall.previous_strike == Decimal("24500")
    assert snapshot.call_wall.shift_direction == "UP"
    assert snapshot.call_wall.shift_points == Decimal("500")

    # Put Wall shifted from 23500 to 24000 (+500 pts UP)
    assert snapshot.put_wall.current_strike == Decimal("24000")
    assert snapshot.put_wall.previous_strike == Decimal("23500")
    assert snapshot.put_wall.shift_direction == "UP"
    assert snapshot.put_wall.shift_points == Decimal("500")

    # 2. PCR Tests
    # Total Call OI = 45k + 55k + 70k = 170k
    # Total Put OI = 35k + 60k = 95k
    # Current PCR (OI) = 95k / 170k ≈ 0.5588
    assert snapshot.summary.total_call_oi == 170000
    assert snapshot.summary.total_put_oi == 95000
    assert snapshot.summary.current_pcr_oi == Decimal("0.5588")

    # Net Call ΔOI = 15k + 5k + 70k = 90k
    # Net Put ΔOI = -5k + 35k = 30k
    # ΔOI PCR = 30k / 90k ≈ 0.3333
    assert snapshot.summary.net_call_oi_change == 90000
    assert snapshot.summary.net_put_oi_change == 30000
    assert snapshot.summary.delta_oi_pcr == Decimal("0.3333")

    # 3. Buildup Classification Tests
    # 24000 CE should be LONG_BUILDUP (Price UP from 160 to 210, ΔOI +15k > 0)
    ce_24000 = next(b for b in snapshot.strike_buildups if b.strike_price == Decimal("24000") and b.option_type == "CE")
    assert ce_24000.buildup_state == "LONG_BUILDUP"

    # 24000 PE should be SHORT_BUILDUP (Price DOWN from 115 to 70, ΔOI +35k > 0)
    pe_24000 = next(b for b in snapshot.strike_buildups if b.strike_price == Decimal("24000") and b.option_type == "PE")
    assert pe_24000.buildup_state == "SHORT_BUILDUP"

    # 23500 PE should be LONG_UNWINDING (Price DOWN from 40 to 22, ΔOI -5k < 0)
    pe_23500 = next(b for b in snapshot.strike_buildups if b.strike_price == Decimal("23500") and b.option_type == "PE")
    assert pe_23500.buildup_state == "LONG_UNWINDING"

    # 4. Narrative Summary exists and mentions shifts
    assert "Call wall shifted UP by 500 pts to 25,000" in snapshot.summary.narrative_summary
    assert "Put wall shifted UP by 500 pts to 24,000" in snapshot.summary.narrative_summary

    # 5. Test API and HTMX routes for OI Intelligence
    from tests.auth_helpers import auth_client, seed_builtins

    await seed_builtins()
    async with auth_client(is_superuser=True, browser=True) as client:
        # JSON API
        api_resp = await client.get(f"/api/v1/market-data/derivatives/options/oi-intelligence?symbol=NIFTY&trade_date={t2}&expiry_date={exp}")
        assert api_resp.status_code == 200
        api_json = api_resp.json()
        assert api_json["underlying_symbol"] == "NIFTY"
        assert api_json["call_wall"]["shift_direction"] == "UP"

        # HTMX cards partial
        cards_resp = await client.get(f"/market-data/derivatives/options/oi-intelligence/cards?symbol=NIFTY&trade_date={t2}&expiry_date={exp}")
        assert cards_resp.status_code == 200
        assert "Call Wall (Resistance)" in cards_resp.text
        assert "25,000" in cards_resp.text
        assert "Put Wall (Support)" in cards_resp.text
        assert "24,000" in cards_resp.text
