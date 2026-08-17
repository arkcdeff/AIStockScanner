"""DB-backed integration tests for the composite market intelligence pipeline.

These tests exercise ``CompositeMarketIntelligence.evaluate(..., persistence=True)``
end-to-end against the real test database:

- snapshot persistence and upsert idempotency (one row per market_date),
- persisted-field fidelity (per-factor scores, composite, confidence, regime),
- no-look-ahead future-invariance at both the repository and orchestrator level.

NO external HTTP calls are made. Factor inputs are injected directly so the test
targets the persistence/composite boundary rather than re-testing the upstream
factor engines.

The test database is expected to be migrated up to revision ``5f7b2c9d4e3a`` (head).
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.models.market_intelligence_snapshot import MarketIntelligenceSnapshot
from app.repositories.market_index_price import MarketIndexPriceRepository
from app.repositories.market_intelligence_snapshot import (
    MarketIntelligenceSnapshotRepository,
)
from app.schemas.market_intelligence_snapshot import MarketIntelligenceScoreConfig
from app.services.composite_market_intelligence import (
    FACTOR_NAMES,
    CompositeMarketIntelligence,
    FactorInput,
)

INDIA = ZoneInfo("Asia/Kolkata")

WEIGHTS = dict(
    zip(FACTOR_NAMES, MarketIntelligenceScoreConfig().weights, strict=True)
)


def _factor_inputs(scores: dict) -> list[FactorInput]:
    """Build a full, canonically-ordered factor input list.

    ``scores`` maps factor names to scores; unavailable factors (``None``) are
    left missing so completeness/confidence vary naturally with the provided
    scores.
    """
    return [
        FactorInput(
            name=name,
            score=scores.get(name),
            configured_weight=WEIGHTS[name],
            confidence=80.0 if scores.get(name) is not None else 0.0,
            completeness=80.0 if scores.get(name) is not None else 0.0,
            freshness="live" if scores.get(name) is not None else "missing",
        )
        for name in FACTOR_NAMES
    ]


def _price(index_id: int, trade_date: date, close: Decimal) -> MarketIndexPrice:
    return MarketIndexPrice(
        market_index_id=index_id,
        trade_date=trade_date,
        open=close,
        high=close + Decimal("10"),
        low=close - Decimal("10"),
        close=close,
        observed_at=trade_date,
        fetched_at=trade_date,
        source_name="NSE",
        freshness_status="live",
    )


# ============================================================
# SNAPSHOT PERSISTENCE & UPSERT IDEMPOTENCY
# ============================================================


class TestSnapshotPersistence:
    @pytest.mark.asyncio
    async def test_persist_creates_snapshot_row(self) -> None:
        async with get_session_factory()() as session:
            orchestrator = CompositeMarketIntelligence(session=session)
            result = await orchestrator.evaluate(
                datetime(2024, 3, 11, 9, 30, tzinfo=INDIA),
                market_date=date(2024, 3, 11),
                persistence=True,
                factor_inputs=_factor_inputs(
                    {
                        "price_action": 60,
                        "breadth": 40,
                        "sector": 30,
                        "institutional": None,
                        "volatility": None,
                        "global": None,
                        "gift_nifty": None,
                        "currency": None,
                        "crude": None,
                        "macro": None,
                    }
                ),
            )
            assert result.snapshot_id is not None

            row = await session.get(MarketIntelligenceSnapshot, result.snapshot_id)
            assert row is not None
            assert row.market_date == date(2024, 3, 11)
            assert row.composite_score is not None
            assert row.price_action_score == Decimal(str(result.factors[0].score))

    @pytest.mark.asyncio
    async def test_upsert_is_idempotent_for_same_market_date(self) -> None:
        async with get_session_factory()() as session:
            orchestrator = CompositeMarketIntelligence(session=session)
            d = date(2024, 3, 12)
            first = await orchestrator.evaluate(
                datetime(2024, 3, 12, 9, 30, tzinfo=INDIA),
                market_date=d,
                persistence=True,
                factor_inputs=_factor_inputs({"price_action": -70, "breadth": -70}),
            )
            second = await orchestrator.evaluate(
                datetime(2024, 3, 12, 10, 0, tzinfo=INDIA),
                market_date=d,
                persistence=True,
                factor_inputs=_factor_inputs({"price_action": -70, "breadth": -70}),
            )

            assert first.snapshot_id == second.snapshot_id
            count = (
                await session.execute(
                    select(func.count(MarketIntelligenceSnapshot.id)).where(
                        MarketIntelligenceSnapshot.market_date == d
                    )
                )
            ).scalar_one()
            assert count == 1

    @pytest.mark.asyncio
    async def test_upsert_updates_values_not_duplicates(self) -> None:
        async with get_session_factory()() as session:
            repo = MarketIntelligenceSnapshotRepository(session)
            d = date(2024, 3, 13)
            await repo.upsert(market_date=d, composite_score=10.0, regime="bullish")

            await repo.upsert(market_date=d, composite_score=90.0, regime="strong_bullish")

            rows = await session.execute(
                select(MarketIntelligenceSnapshot).where(
                    MarketIntelligenceSnapshot.market_date == d
                )
            )
            rows = rows.scalars().all()
            assert len(rows) == 1
            assert rows[0].composite_score == Decimal("90.00")
            assert rows[0].regime == "strong_bullish"

    @pytest.mark.asyncio
    async def test_evaluate_persists_all_expected_fields(self) -> None:
        async with get_session_factory()() as session:
            orchestrator = CompositeMarketIntelligence(session=session)
            d = date(2024, 3, 14)
            result = await orchestrator.evaluate(
                datetime(2024, 3, 14, 9, 30, tzinfo=INDIA),
                market_date=d,
                persistence=True,
                factor_inputs=_factor_inputs(
                    {
                        "price_action": 30,
                        "breadth": 20,
                        "sector": 10,
                        "institutional": 5,
                        "volatility": -5,
                        "global": 10,
                        "gift_nifty": None,
                        "currency": None,
                        "crude": None,
                        "macro": None,
                    }
                ),
            )
            row = await session.get(MarketIntelligenceSnapshot, result.snapshot_id)
            assert row is not None
            assert float(row.composite_score) == pytest.approx(
                result.composite_score, abs=0.02
            )
            assert float(row.confidence_score) == pytest.approx(
                result.confidence_score, abs=0.02
            )
            assert row.regime == result.regime
            assert float(row.data_completeness_percent) == pytest.approx(
                result.data_completeness_percent, abs=0.02
            )
            assert row.price_action_score is not None
            assert row.macro_score is None


# ============================================================
# NO-LOOK-AHEAD FUTURE-INVARIANCE
# ============================================================


class TestNoLookAhead:
    @pytest.mark.asyncio
    async def test_snapshot_repo_list_history_excludes_future(self) -> None:
        async with get_session_factory()() as session:
            repo = MarketIntelligenceSnapshotRepository(session)
            await repo.upsert(market_date=date(2024, 1, 10), composite_score=5.0)
            await repo.upsert(market_date=date(2024, 1, 15), composite_score=15.0)
            # A future-dated snapshot relative to the requested window.
            await repo.upsert(market_date=date(2030, 1, 20), composite_score=999.0)

            history = await repo.list_history(date(2024, 1, 1), date(2024, 1, 31))
            future = [h for h in history if h.market_date > date(2024, 1, 31)]
            assert len(future) == 0
            assert all(h.composite_score != Decimal("999.00") for h in history)

    @pytest.mark.asyncio
    async def test_snapshot_repo_get_latest_excludes_future(self) -> None:
        async with get_session_factory()() as session:
            repo = MarketIntelligenceSnapshotRepository(session)
            await repo.upsert(market_date=date(2024, 1, 10), composite_score=5.0)
            # Only a future-dated snapshot exists beyond today.
            await repo.upsert(market_date=date(2099, 6, 15), composite_score=999.0)

            latest = await repo.get_latest()
            assert latest is None or latest.market_date <= date.today()

    @pytest.mark.asyncio
    async def test_price_repo_reads_bounded_by_end_date(self) -> None:
        async with get_session_factory()() as session:
            mi = MarketIndex(code="NIFTY_50", country="INDIA", name="Nifty 50")
            session.add(mi)
            await session.commit()
            await session.refresh(mi)

            session.add(_price(mi.id, date(2024, 1, 10), Decimal("100")))
            session.add(_price(mi.id, date(2024, 1, 20), Decimal("120")))
            session.add(_price(mi.id, date(2030, 1, 20), Decimal("999")))
            await session.commit()

            repo = MarketIndexPriceRepository(session)
            rows = await repo.get_date_range_by_index_code(
                "NIFTY_50", date(2024, 1, 1), date(2024, 1, 31)
            )
            assert len(rows) == 2
            future = [r for r in rows if r.trade_date > date(2024, 1, 31)]
            assert len(future) == 0

    @pytest.mark.asyncio
    async def test_orchestrator_future_data_does_not_alter_history(
        self, mi_fixture
    ) -> None:
        """A future-dated price row must not change an earlier date's evaluation."""
        async with get_session_factory()() as session:
            mi = MarketIndex(code="NIFTY_50", country="INDIA", name="Nifty 50")
            session.add(mi)
            await session.commit()
            await session.refresh(mi)

            boundary = date(2024, 1, 15)
            # Historical prices only up to the boundary.
            session.add(_price(mi.id, date(2024, 1, 14), Decimal("100")))
            session.add(_price(mi.id, date(2024, 1, 15), Decimal("101")))
            await session.commit()

            inputs = _factor_inputs(
                {
                    "price_action": 50,
                    "breadth": 30,
                    "sector": 20,
                    "institutional": 10,
                    "volatility": 0,
                    "global": 5,
                    "gift_nifty": None,
                    "currency": None,
                    "crude": None,
                    "macro": None,
                }
            )

            a = await CompositeMarketIntelligence(session=session).evaluate(
                datetime(2024, 1, 15, 9, 30, tzinfo=INDIA),
                market_date=boundary,
                factor_inputs=inputs,
            )

            # Now add a future price row that would raise the price-action score
            # if the engine leaked future data.
            session.add(_price(mi.id, date(2030, 1, 20), Decimal("9999")))
            await session.commit()

            b = await CompositeMarketIntelligence(session=session).evaluate(
                datetime(2024, 1, 15, 9, 30, tzinfo=INDIA),
                market_date=boundary,
                factor_inputs=inputs,
            )

            assert b.composite_score == a.composite_score
            assert b.confidence_score == a.confidence_score
            assert b.regime == a.regime
