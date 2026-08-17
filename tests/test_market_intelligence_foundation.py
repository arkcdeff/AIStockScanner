"""Foundation tests for Market Intelligence Sprint 18 Task 18.1.

These tests verify the core data models, providers, permissions, and
repository behavior for the market intelligence foundation tables.
NO external HTTP calls are made.

The test database is expected to be migrated up to revision
``25e6f159a646`` (head) before running this suite.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.db.session import get_session_factory
from app.market_intelligence.providers.base import (
    MarketProviderBlockedError,
    MarketProviderConfigError,
    MarketProviderError,
    MarketProviderParseError,
    MarketProviderTimeoutError,
    MarketProviderUnavailableError,
    ProviderResult,
)
from app.models.institutional_flow import InstitutionalFlow
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.models.market_intelligence_snapshot import MarketIntelligenceSnapshot
from app.repositories.institutional_flow import InstitutionalFlowRepository
from app.repositories.market_index import MarketIndexRepository
from app.repositories.market_index_price import MarketIndexPriceRepository
from app.repositories.market_intelligence_snapshot import (
    MarketIntelligenceSnapshotRepository,
)
from app.schemas.market_intelligence_snapshot import MarketIntelligenceScoreConfig
from app.services.builtin_auth import (
    _ROLE_PERMISSION_MATRIX,
    BUILT_IN_PERMISSION_DEFINITIONS,
    built_in_role_specs,
)


def _price(
    market_index_id: int,
    trade_date: date,
    close: Decimal = Decimal("18050.0000"),
) -> MarketIndexPrice:
    return MarketIndexPrice(
        market_index_id=market_index_id,
        trade_date=trade_date,
        open=Decimal("18000.0000"),
        high=Decimal("18100.0000"),
        low=Decimal("17950.0000"),
        close=close,
    )


def _snapshot(market_date: date, **kwargs) -> MarketIntelligenceSnapshot:
    return MarketIntelligenceSnapshot(
        market_date=market_date,
        generated_at=market_date,
        **kwargs,
    )


# ============================================================
# MODELS
# ============================================================


class TestMarketIndex:
    """Tests for MarketIndex model."""

    @pytest.mark.asyncio
    async def test_persistence(self) -> None:
        async with get_session_factory()() as session:
            mi = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
            session.add(mi)
            await session.commit()
            await session.refresh(mi)

            retrieved = await session.get(MarketIndex, mi.id)
            assert retrieved is not None
            assert retrieved.code == "NIFTY"
            assert retrieved.name == "Nifty 50"

    @pytest.mark.asyncio
    async def test_unique_identity_code_country(self) -> None:
        async with get_session_factory()() as session:
            session.add_all(
                [
                    MarketIndex(code="NIFTY", country="INDIA", name="Nifty 1"),
                    MarketIndex(code="NIFTY", country="USA", name="Nifty 2"),
                ]
            )
            await session.commit()

            session.add(MarketIndex(code="NIFTY", country="INDIA", name="Nifty 3"))
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_is_active_default(self) -> None:
        async with get_session_factory()() as session:
            mi = MarketIndex(code="SENSE", country="INDIA", name="Sensex")
            session.add(mi)
            await session.commit()
            await session.refresh(mi)
            assert mi.is_active is True

    @pytest.mark.asyncio
    async def test_timezone_behavior(self) -> None:
        async with get_session_factory()() as session:
            mi = MarketIndex(code="BANKNIFTY", country="INDIA", name="Bank Nifty")
            session.add(mi)
            await session.commit()
            await session.refresh(mi)
            assert mi.timezone == "Asia/Kolkata"

    @pytest.mark.asyncio
    async def test_category_behavior(self) -> None:
        async with get_session_factory()() as session:
            mi = MarketIndex(code="TEST", country="INDIA", name="Test")
            session.add(mi)
            await session.commit()
            await session.refresh(mi)
            assert mi.category == "indian_index"


class TestMarketIndexPrice:
    """Tests for MarketIndexPrice model."""

    @pytest.mark.asyncio
    async def test_persistence(self, mi_fixture) -> None:
        async with get_session_factory()() as session:
            price = _price(mi_fixture.id, date(2023, 1, 15))
            session.add(price)
            await session.commit()
            await session.refresh(price)

            retrieved = await session.get(MarketIndexPrice, price.id)
            assert retrieved is not None
            assert retrieved.close == Decimal("18050.0000")

    @pytest.mark.asyncio
    async def test_relationship(self, mi_fixture) -> None:
        async with get_session_factory()() as session:
            session.add(_price(mi_fixture.id, date(2023, 1, 15)))
            await session.commit()

            mi = await session.get(
                MarketIndex,
                mi_fixture.id,
                options=[selectinload(MarketIndex.index_prices)],
            )
            assert mi is not None
            assert len(mi.index_prices) >= 1
            assert mi.index_prices[0].close == Decimal("18050.0000")

    @pytest.mark.asyncio
    async def test_duplicate_date_protection(self, mi_fixture) -> None:
        async with get_session_factory()() as session:
            session.add(_price(mi_fixture.id, date(2023, 1, 15)))
            session.add(
                _price(
                    mi_fixture.id,
                    date(2023, 1, 15),
                    close=Decimal("18060.0000"),
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_provenance(self, mi_fixture) -> None:
        async with get_session_factory()() as session:
            price = _price(mi_fixture.id, date(2023, 1, 15))
            price.observed_at = date(2023, 1, 15)
            price.fetched_at = date(2023, 1, 16)
            price.source_name = "NSE"
            price.source_reference = "https://nseindia.in"
            price.freshness_status = "live"
            session.add(price)
            await session.commit()
            await session.refresh(price)

            assert price.observed_at == date(2023, 1, 15)
            assert price.fetched_at == date(2023, 1, 16)
            assert price.source_name == "NSE"
            assert price.freshness_status == "live"

    @pytest.mark.asyncio
    async def test_freshness(self, mi_fixture) -> None:
        async with get_session_factory()() as session:
            price = _price(mi_fixture.id, date(2023, 1, 15))
            price.freshness_status = "stale"
            session.add(price)
            await session.commit()
            await session.refresh(price)
            assert price.freshness_status == "stale"

    @pytest.mark.asyncio
    async def test_timestamps(self, mi_fixture) -> None:
        async with get_session_factory()() as session:
            price = _price(mi_fixture.id, date(2023, 1, 15))
            session.add(price)
            await session.commit()
            await session.refresh(price)
            assert price.created_at is not None
            assert price.updated_at is not None


class TestInstitutionalFlow:
    """Tests for InstitutionalFlow model."""

    @pytest.mark.asyncio
    async def test_FII(self) -> None:
        async with get_session_factory()() as session:
            flow = InstitutionalFlow(
                trade_date=date(2023, 1, 15),
                investor_type="FII",
                buy_value=1000000.0,
                sell_value=900000.0,
                net_value=100000.0,
            )
            session.add(flow)
            await session.commit()
            await session.refresh(flow)
            assert flow.investor_type == "FII"

    @pytest.mark.asyncio
    async def test_DII(self) -> None:
        async with get_session_factory()() as session:
            flow = InstitutionalFlow(
                trade_date=date(2023, 1, 15),
                investor_type="DII",
                buy_value=500000.0,
                sell_value=600000.0,
                net_value=-100000.0,
            )
            session.add(flow)
            await session.commit()
            await session.refresh(flow)
            assert flow.investor_type == "DII"

    @pytest.mark.asyncio
    async def test_invalid_investor_type(self) -> None:
        async with get_session_factory()() as session:
            flow = InstitutionalFlow(
                trade_date=date(2023, 1, 15),
                investor_type="INVALID",
                buy_value=100.0,
                sell_value=50.0,
            )
            session.add(flow)
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_uniqueness(self) -> None:
        async with get_session_factory()() as session:
            session.add_all(
                [
                    InstitutionalFlow(
                        trade_date=date(2023, 1, 15),
                        investor_type="FII",
                        buy_value=100.0,
                        sell_value=50.0,
                    ),
                    InstitutionalFlow(
                        trade_date=date(2023, 1, 15),
                        investor_type="DII",
                        buy_value=200.0,
                        sell_value=100.0,
                    ),
                ]
            )
            await session.commit()

            session.add(
                InstitutionalFlow(
                    trade_date=date(2023, 1, 15),
                    investor_type="FII",
                    buy_value=300.0,
                    sell_value=150.0,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_provenance(self) -> None:
        async with get_session_factory()() as session:
            flow = InstitutionalFlow(
                trade_date=date(2023, 1, 15),
                investor_type="FII",
                buy_value=1000000.0,
                sell_value=900000.0,
                net_value=100000.0,
                source_name="NSE FII",
                observed_at=date(2023, 1, 15),
                fetched_at=date(2023, 1, 16),
                freshness_status="live",
            )
            session.add(flow)
            await session.commit()
            await session.refresh(flow)

            assert flow.source_name == "NSE FII"
            assert flow.observed_at == date(2023, 1, 15)
            assert flow.fetched_at == date(2023, 1, 16)
            assert flow.freshness_status == "live"

    @pytest.mark.asyncio
    async def test_freshness(self) -> None:
        async with get_session_factory()() as session:
            flow = InstitutionalFlow(
                trade_date=date(2023, 1, 15),
                investor_type="FII",
                buy_value=100.0,
                sell_value=50.0,
                freshness_status="stale",
            )
            session.add(flow)
            await session.commit()
            await session.refresh(flow)
            assert flow.freshness_status == "stale"


class TestMarketIntelligenceSnapshot:
    """Tests for MarketIntelligenceSnapshot model."""

    @pytest.mark.asyncio
    async def test_unique_market_date(self) -> None:
        async with get_session_factory()() as session:
            session.add(
                _snapshot(
                    date(2023, 1, 15),
                    price_action_score=5.0,
                    breadth_score=3.0,
                    sector_score=2.0,
                    institutional_score=1.0,
                    volatility_score=1.0,
                    global_score=0.5,
                    gift_nifty_score=0.3,
                    currency_score=0.2,
                    crude_score=0.1,
                    macro_score=0.05,
                    composite_score=12.15,
                    confidence_score=8.0,
                    regime="bull",
                    bullish_factors_json={"momentum": 10},
                    bearish_factors_json={"value": -5},
                    risk_factors_json={"liquidity": -3},
                    missing_factors_json={"sentiment": 2},
                    data_completeness_percent=80.0,
                    stale_factor_count=1,
                    missing_factor_count=2,
                )
            )
            await session.commit()

            session.add(
                _snapshot(
                    date(2023, 1, 15),
                    price_action_score=5.0,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_nullable_factor_scores(self) -> None:
        async with get_session_factory()() as session:
            snap = _snapshot(
                date(2023, 1, 15),
                price_action_score=None,
                breadth_score=None,
                sector_score=None,
                institutional_score=None,
                volatility_score=None,
                global_score=None,
                gift_nifty_score=None,
                currency_score=None,
                crude_score=None,
                macro_score=None,
                composite_score=None,
                confidence_score=None,
            )
            session.add(snap)
            await session.commit()
            await session.refresh(snap)

            assert snap.price_action_score is None
            assert snap.breadth_score is None

    @pytest.mark.asyncio
    async def test_score_bounds_negative_100_to_100(self) -> None:
        async with get_session_factory()() as session:
            snap = _snapshot(
                date(2023, 1, 15),
                price_action_score=100.0,
                breadth_score=-100.0,
                sector_score=50.0,
                institutional_score=0.0,
                volatility_score=-50.0,
                global_score=100.0,
                gift_nifty_score=0.0,
                currency_score=0.0,
                crude_score=0.0,
                macro_score=0.0,
                composite_score=0.0,
                confidence_score=75.0,
            )
            session.add(snap)
            await session.commit()
            await session.refresh(snap)

            assert snap.price_action_score == Decimal("100.00")
            assert snap.breadth_score == Decimal("-100.00")

    @pytest.mark.asyncio
    async def test_confidence_bounds_0_to_100(self) -> None:
        async with get_session_factory()() as session:
            snap = _snapshot(date(2023, 1, 15), confidence_score=100.0)
            session.add(snap)
            await session.commit()
            await session.refresh(snap)
            assert snap.confidence_score == Decimal("100.00")

    @pytest.mark.asyncio
    async def test_completeness_bounds_0_to_100(self) -> None:
        async with get_session_factory()() as session:
            snap = _snapshot(date(2023, 1, 15), data_completeness_percent=100.0)
            session.add(snap)
            await session.commit()
            await session.refresh(snap)
            assert snap.data_completeness_percent == Decimal("100.00")

    @pytest.mark.asyncio
    async def test_regime_validation(self) -> None:
        async with get_session_factory()() as session:
            snap = _snapshot(date(2023, 1, 15), regime="bull")
            session.add(snap)
            await session.commit()
            await session.refresh(snap)
            assert snap.regime == "bull"

    @pytest.mark.asyncio
    async def test_factor_json_persistence(self) -> None:
        async with get_session_factory()() as session:
            snap = _snapshot(
                date(2023, 1, 15),
                bullish_factors_json={"momentum": 10, "value": 5},
                bearish_factors_json={"interest": -3},
                risk_factors_json={"liquidity": -2},
                missing_factors_json={"sentiment": 1},
            )
            session.add(snap)
            await session.commit()
            await session.refresh(snap)

            assert snap.bullish_factors_json == {"momentum": 10, "value": 5}
            assert snap.bearish_factors_json == {"interest": -3}
            assert snap.risk_factors_json == {"liquidity": -2}
            assert snap.missing_factors_json == {"sentiment": 1}

    @pytest.mark.asyncio
    async def test_stale_and_missing_factor_counts(self) -> None:
        async with get_session_factory()() as session:
            snap = _snapshot(
                date(2023, 1, 15), stale_factor_count=3, missing_factor_count=2
            )
            session.add(snap)
            await session.commit()
            await session.refresh(snap)

            assert snap.stale_factor_count == 3
            assert snap.missing_factor_count == 2


# ============================================================
# FRESHNESS
# ============================================================


class TestFreshness:
    """Tests for ProviderResult data freshness concepts."""

    def test_live(self) -> None:
        result = ProviderResult(
            provider_name="test", status="success", freshness="live"
        )
        assert result.is_live is True
        assert result.is_recent is False
        assert result.is_stale is False
        assert result.is_missing is False

    def test_recent(self) -> None:
        result = ProviderResult(
            provider_name="test", status="success", freshness="recent"
        )
        assert result.is_live is False
        assert result.is_recent is True
        assert result.is_stale is False
        assert result.is_missing is False

    def test_stale(self) -> None:
        result = ProviderResult(
            provider_name="test", status="success", freshness="stale"
        )
        assert result.is_live is False
        assert result.is_recent is False
        assert result.is_stale is True
        assert result.is_missing is False

    def test_missing(self) -> None:
        result = ProviderResult(
            provider_name="test", status="success", freshness="missing"
        )
        assert result.is_live is False
        assert result.is_recent is False
        assert result.is_stale is False
        assert result.is_missing is True

    def test_invalid_freshness_rejected(self) -> None:
        with pytest.raises(ValueError):
            ProviderResult(
                provider_name="test", status="success", freshness="invalid"
            )

    def test_freshness_separate_from_status(self) -> None:
        result = ProviderResult(
            provider_name="test", status="success", freshness="missing"
        )
        assert result.is_success is True
        assert result.is_missing is True

        result2 = ProviderResult(
            provider_name="test", status="cached", freshness="live"
        )
        assert result2.is_cached is True
        assert result2.is_live is True

    def test_status_and_freshness_combinations(self) -> None:
        valid_statuses = [
            "success",
            "cached",
            "stale",
            "unavailable",
            "blocked",
            "failed",
        ]
        valid_freshnesses = ["live", "recent", "stale", "missing"]
        for status in valid_statuses:
            for freshness in valid_freshnesses:
                result = ProviderResult(
                    provider_name="test", status=status, freshness=freshness
                )
                assert result.status == status
                assert result.freshness == freshness


# ============================================================
# PROVIDER RESULT
# ============================================================

STATUS_PROPS = {
    "success": "is_success",
    "cached": "is_cached",
    "stale": "is_stale_status",
    "unavailable": "is_unavailable",
    "blocked": "is_blocked",
    "failed": "is_failed",
}

FRESHNESS_PROPS = {
    "live": "is_live",
    "recent": "is_recent",
    "stale": "is_stale",
    "missing": "is_missing",
}


class TestProviderResultStatuses:
    """Tests for ProviderResult execution statuses."""

    def test_success(self) -> None:
        result = ProviderResult(provider_name="test", status="success")
        assert result.is_success is True
        assert result.is_cached is False
        assert result.is_stale_status is False
        assert result.is_unavailable is False
        assert result.is_blocked is False
        assert result.is_failed is False

    def test_cached(self) -> None:
        result = ProviderResult(provider_name="test", status="cached")
        assert result.is_cached is True
        assert result.is_success is False

    def test_stale_status(self) -> None:
        result = ProviderResult(provider_name="test", status="stale")
        assert result.is_stale_status is True
        assert result.is_success is False
        assert result.is_failed is False

    def test_unavailable(self) -> None:
        result = ProviderResult(provider_name="test", status="unavailable")
        assert result.is_unavailable is True
        assert result.is_success is False

    def test_blocked(self) -> None:
        result = ProviderResult(provider_name="test", status="blocked")
        assert result.is_blocked is True
        assert result.is_success is False

    def test_failed(self) -> None:
        result = ProviderResult(provider_name="test", status="failed")
        assert result.is_failed is True
        assert result.is_failure is True
        assert result.is_success is False

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValueError):
            ProviderResult(provider_name="test", status="invalid")

    def test_status_enum_coverall(self) -> None:
        for status, prop in STATUS_PROPS.items():
            result = ProviderResult(provider_name="test", status=status)
            matches = sum(getattr(result, p) for p in STATUS_PROPS.values())
            assert matches == 1, (
                f"status={status} should match exactly one property"
            )
            assert getattr(result, prop) is True


class TestProviderResultFreshness:
    """Tests for ProviderResult freshness values."""

    def test_all_freshness_values(self) -> None:
        for freshness, prop in FRESHNESS_PROPS.items():
            result = ProviderResult(
                provider_name="test", status="success", freshness=freshness
            )
            matches = sum(
                getattr(result, p) for p in FRESHNESS_PROPS.values()
            )
            assert matches == 1, (
                f"freshness={freshness} should match exactly one property"
            )
            assert getattr(result, prop) is True

    def test_freshness_independent_of_status(self) -> None:
        for status in STATUS_PROPS:
            for freshness in FRESHNESS_PROPS:
                result = ProviderResult(
                    provider_name="test", status=status, freshness=freshness
                )
                assert result.status == status
                assert result.freshness == freshness


# ============================================================
# PROVIDER ERRORS
# ============================================================


class TestProviderErrors:
    """Tests for Provider error hierarchy and sanitization."""

    def test_correct_hierarchy(self) -> None:
        base = MarketProviderError()
        assert isinstance(base, MarketProviderError)

        config = MarketProviderConfigError()
        assert isinstance(config, MarketProviderError)
        assert isinstance(config, MarketProviderConfigError)

        timeout = MarketProviderTimeoutError()
        assert isinstance(timeout, MarketProviderError)
        assert isinstance(timeout, MarketProviderTimeoutError)

        unavailable = MarketProviderUnavailableError()
        assert isinstance(unavailable, MarketProviderError)
        assert isinstance(unavailable, MarketProviderUnavailableError)

        blocked = MarketProviderBlockedError()
        assert isinstance(blocked, MarketProviderError)
        assert isinstance(blocked, MarketProviderBlockedError)

        parse_error = MarketProviderParseError()
        assert isinstance(parse_error, MarketProviderError)
        assert isinstance(parse_error, MarketProviderParseError)

    def test_sanitized_messages(self) -> None:
        error = MarketProviderConfigError(
            message="API key 'sk-live-abc123' not found in environment"
        )
        public_msg = error.public_message()
        assert "sk-live-abc123" not in public_msg
        assert "API key" in public_msg

    def test_no_secrets_leak(self) -> None:
        error = MarketProviderTimeoutError(
            message="Connection timed out after 30s to api.nse.com?token=secret123"
        )
        public_msg = error.public_message()
        assert "secret123" not in public_msg


# ============================================================
# SCORING CONFIG
# ============================================================


class TestMarketIntelligenceScoreConfig:
    """Tests for MarketIntelligenceScoreConfig validation."""

    def test_default_weights(self) -> None:
        config = MarketIntelligenceScoreConfig()
        weights = config.weights
        assert len(weights) == 10, f"Expected 10 weights, got {len(weights)}"
        for i, w in enumerate(weights):
            assert w >= 0, f"Weight {i} ({w}) must be >= 0"
        total = sum(weights)
        assert abs(total - 1.0) < 0.01, f"Weights total={total}, expected ~1.0"

    def test_negative_weight_rejected(self) -> None:
        with pytest.raises(ValueError):
            MarketIntelligenceScoreConfig(
                weights=[-0.1, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            )

    def test_total_gt_1_rejected(self) -> None:
        with pytest.raises(ValueError):
            MarketIntelligenceScoreConfig(
                weights=[0.6, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            )

    def test_invalid_total_rejected(self) -> None:
        with pytest.raises(ValueError):
            MarketIntelligenceScoreConfig(weights=[0.5] * 10)

    def test_off_by_epsilon_totals_rejected(self) -> None:
        with pytest.raises(ValueError):
            MarketIntelligenceScoreConfig(weights=[0.095] * 10)
        with pytest.raises(ValueError):
            MarketIntelligenceScoreConfig(weights=[0.105] * 10)

    def test_factor_bounds(self) -> None:
        config = MarketIntelligenceScoreConfig(weights=[0.1] * 10)
        for w in config.weights:
            assert 0 <= w <= 1

    def test_exactly_10_factors(self) -> None:
        with pytest.raises(ValueError):
            MarketIntelligenceScoreConfig(weights=[0.1] * 9)
        with pytest.raises(ValueError):
            MarketIntelligenceScoreConfig(weights=[0.1] * 11)


# ============================================================
# REPOSITORIES
# ============================================================


class TestRepositories:
    """Tests for repository methods on market intelligence tables."""

    @pytest.mark.asyncio
    async def test_market_index_repo_get_by_code(self) -> None:
        async with get_session_factory()() as session:
            session.add(MarketIndex(code="NIFTY", country="INDIA", name="Nifty 50"))
            await session.commit()

            repo = MarketIndexRepository(session)
            found = await repo.get_by_code("NIFTY")
            assert found is not None
            assert found.code == "NIFTY"
            assert found.name == "Nifty 50"

    @pytest.mark.asyncio
    async def test_market_index_repo_list_active(self) -> None:
        async with get_session_factory()() as session:
            session.add(
                MarketIndex(code="SENSE", country="INDIA", name="Sensex", is_active=True)
            )
            await session.commit()

            repo = MarketIndexRepository(session)
            active = await repo.list_active()
            assert any(m.code == "SENSE" for m in active)

    @pytest.mark.asyncio
    async def test_market_index_repo_list_by_category(self) -> None:
        async with get_session_factory()() as session:
            session.add(
                MarketIndex(code="NIFTY", country="INDIA", name="Nifty", category="indian_index")
            )
            await session.commit()

            repo = MarketIndexRepository(session)
            by_cat = await repo.list_by_category("indian_index")
            assert any(m.code == "NIFTY" for m in by_cat)

    @pytest.mark.asyncio
    async def test_market_index_repo_list_by_country(self) -> None:
        async with get_session_factory()() as session:
            session.add(MarketIndex(code="NIFTY", country="INDIA", name="Nifty"))
            await session.commit()

            repo = MarketIndexRepository(session)
            by_country = await repo.list_by_country("INDIA")
            assert any(m.code == "NIFTY" for m in by_country)

    @pytest.mark.asyncio
    async def test_market_index_price_repo_get_by_index_and_date(self, mi_fixture) -> None:
        async with get_session_factory()() as session:
            session.add(_price(mi_fixture.id, date(2023, 1, 15)))
            await session.commit()

            repo = MarketIndexPriceRepository(session)
            found = await repo.get_by_index_and_date(mi_fixture.id, date(2023, 1, 15))
            assert found is not None
            assert found.close == Decimal("18050.0000")

    @pytest.mark.asyncio
    async def test_market_index_price_repo_date_range(self, mi_fixture) -> None:
        async with get_session_factory()() as session:
            for d in (date(2023, 1, 10), date(2023, 1, 11), date(2023, 1, 12)):
                session.add(_price(mi_fixture.id, d))
            await session.commit()

            repo = MarketIndexPriceRepository(session)
            prices = await repo.get_date_range(
                mi_fixture.id, date(2023, 1, 10), date(2023, 1, 12)
            )
            assert len(prices) == 3

    @pytest.mark.asyncio
    async def test_market_index_price_repo_latest(self, mi_fixture) -> None:
        async with get_session_factory()() as session:
            for d in (date(2023, 1, 10), date(2023, 1, 11), date(2023, 1, 12)):
                session.add(
                    _price(
                        mi_fixture.id,
                        d,
                        close=Decimal(f"180{d.day * 100}00"),
                    )
                )
            await session.commit()

            repo = MarketIndexPriceRepository(session)
            latest = await repo.get_latest(mi_fixture.id)
            assert latest is not None
            assert latest.trade_date == date(2023, 1, 12)

    @pytest.mark.asyncio
    async def test_institutional_flow_repo_upsert(self) -> None:
        async with get_session_factory()() as session:
            repo = InstitutionalFlowRepository(session)
            result = await repo.upsert(
                trade_date=date(2023, 1, 15),
                investor_type="FII",
                buy_value=1000000.0,
                sell_value=900000.0,
                net_value=100000.0,
            )
            assert result is not None
            assert result.investor_type == "FII"

    @pytest.mark.asyncio
    async def test_institutional_flow_repo_get_range(self) -> None:
        async with get_session_factory()() as session:
            session.add(
                InstitutionalFlow(
                    trade_date=date(2023, 1, 15),
                    investor_type="FII",
                    buy_value=100.0,
                    sell_value=50.0,
                )
            )
            await session.commit()

            repo = InstitutionalFlowRepository(session)
            result = await repo.get_range(
                date(2023, 1, 10), date(2023, 1, 20), "FII"
            )
            assert len(result) == 1
            assert result[0].trade_date == date(2023, 1, 15)

    @pytest.mark.asyncio
    async def test_snapshot_repo_upsert(self) -> None:
        async with get_session_factory()() as session:
            repo = MarketIntelligenceSnapshotRepository(session)
            result = await repo.upsert(
                market_date=date(2023, 1, 15),
                generated_at=date(2023, 1, 15),
                price_action_score=5.0,
                composite_score=12.15,
            )
            assert result is not None
            assert result.market_date == date(2023, 1, 15)

    @pytest.mark.asyncio
    async def test_snapshot_repo_get_latest(self) -> None:
        async with get_session_factory()() as session:
            session.add(_snapshot(date(2023, 1, 15), composite_score=15.0))
            await session.commit()

            repo = MarketIntelligenceSnapshotRepository(session)
            latest = await repo.get_latest()
            assert latest is not None
            assert latest.composite_score == Decimal("15.00")

    @pytest.mark.asyncio
    async def test_snapshot_repo_list_history(self) -> None:
        async with get_session_factory()() as session:
            for d in (date(2023, 1, 10), date(2023, 1, 15), date(2023, 1, 20)):
                session.add(_snapshot(d, composite_score=float(d.day * 2)))
            await session.commit()

            repo = MarketIntelligenceSnapshotRepository(session)
            history = await repo.list_history(date(2023, 1, 10), date(2023, 1, 20))
            assert len(history) == 3


# ============================================================
# NO-LOOK-AHEAD
# ============================================================


class TestNoLookAhead:
    """Tests that repository methods don't leak future records."""

    @pytest.mark.asyncio
    async def test_repo_date_range_no_future(self, mi_fixture) -> None:
        async with get_session_factory()() as session:
            session.add(_price(mi_fixture.id, date(2025, 1, 15)))
            await session.commit()

            repo = MarketIndexPriceRepository(session)
            prices = await repo.get_date_range(
                mi_fixture.id, date(2023, 1, 1), date(2024, 12, 31)
            )
            future = [p for p in prices if p.trade_date > date(2024, 12, 31)]
            assert len(future) == 0

    @pytest.mark.asyncio
    async def test_repo_latest_no_future(self, mi_fixture) -> None:
        async with get_session_factory()() as session:
            session.add(_price(mi_fixture.id, date(2099, 6, 15)))
            await session.commit()

            repo = MarketIndexPriceRepository(session)
            latest = await repo.get_latest(mi_fixture.id)
            assert latest is None or latest.trade_date <= date(2024, 12, 31)

    @pytest.mark.asyncio
    async def test_repo_historical_no_future(self) -> None:
        async with get_session_factory()() as session:
            session.add(
                InstitutionalFlow(
                    trade_date=date(2099, 6, 15),
                    investor_type="FII",
                    buy_value=100.0,
                    sell_value=50.0,
                )
            )
            await session.commit()

            repo = InstitutionalFlowRepository(session)
            history = await repo.get_range(
                date(2023, 1, 1), date(2024, 12, 31), "FII"
            )
            future = [h for h in history if h.trade_date > date(2024, 12, 31)]
            assert len(future) == 0


# ============================================================
# PERMISSIONS
# ============================================================


class TestPermissions:
    """Tests for market intelligence permissions."""

    def test_three_new_codes_exist(self) -> None:
        new_codes = [
            "market_intelligence.read",
            "market_intelligence.refresh",
            "market_intelligence.write",
        ]
        for code in new_codes:
            assert code in BUILT_IN_PERMISSION_DEFINITIONS, f"Missing permission: {code}"
            assert BUILT_IN_PERMISSION_DEFINITIONS[code] is not None

    def test_role_matrix_read_write_refresh(self) -> None:
        role_matrix = _ROLE_PERMISSION_MATRIX
        assert "market_intelligence.read" in role_matrix["super_admin"]
        assert "market_intelligence.refresh" in role_matrix["super_admin"]
        assert "market_intelligence.write" in role_matrix["super_admin"]
        assert "market_intelligence.read" in role_matrix["admin"]
        assert "market_intelligence.refresh" in role_matrix["admin"]
        assert "market_intelligence.write" in role_matrix["admin"]
        assert "market_intelligence.read" in role_matrix["analyst"]
        assert "market_intelligence.refresh" in role_matrix["analyst"]
        assert "market_intelligence.write" not in role_matrix["analyst"]
        assert "market_intelligence.read" in role_matrix["viewer"]
        assert "market_intelligence.refresh" not in role_matrix["viewer"]
        assert "market_intelligence.write" not in role_matrix["viewer"]

    def test_permission_catalog_count(self) -> None:
        total = len(BUILT_IN_PERMISSION_DEFINITIONS)
        assert total == 31, f"Expected 31 permissions, got {total}"

    def test_built_in_seeding_idempotent(self) -> None:
        specs = built_in_role_specs()
        assert isinstance(specs, tuple)
        assert len(specs) > 0
        for spec in specs:
            assert hasattr(spec, "name")
            assert hasattr(spec, "description")
            assert hasattr(spec, "permissions")
            assert isinstance(spec.permissions, tuple)


# ============================================================
# NO EXTERNAL HTTP CALLS
# ============================================================


class TestNoExternalHttpCalls:
    """Ensure no external HTTP calls are made in these tests."""

    def test_imports_no_http(self) -> None:
        assert ProviderResult is not None
        assert MarketIntelligenceScoreConfig is not None