from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.ai.base import AIProviderError, BaseAIProvider
from app.ai.cache import AIMemoryCache, clear_memory_cache
from app.ai.explanation_service import AIExplanationService, AINotFoundError
from app.ai.prompt_builder import (
    build_market_summary_prompt,
    build_scanner_summary_prompt,
    build_stock_summary_prompt,
    hash_prompt,
)
from app.ai.provider import get_provider, reset_provider_cache
from app.ai.providers.deepseek_provider import DeepSeekProvider
from app.ai.providers.mock_provider import MockAIProvider
from app.ai.providers.ollama_provider import OllamaProvider
from app.ai.providers.openai_provider import OpenAIProvider
from app.ai.response_parser import (
    AIResponseError,
    parse_collection_explanation,
    parse_market_explanation,
    parse_stock_explanation,
)
from app.core.config import settings
from app.db.session import get_session_factory
from app.models.ai_explanation import AIExplanation
from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.sector import Sector
from app.models.stock import Stock
from app.schemas.ai import (
    AIAnalyticsContext,
    AIStockContext,
)
from app.services.analytics_engine import AnalyticsEngine
from tests.auth_helpers import analyst_client


@pytest.fixture(autouse=True)
def _clear_ai_caches() -> None:
    clear_memory_cache()
    reset_provider_cache()
    yield
    clear_memory_cache()
    reset_provider_cache()


class _FakeProvider(BaseAIProvider):
    def __init__(self, *, name: str = "fake", output: str | None = None) -> None:
        self._name = name
        self._output = output
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    def model_name(self) -> str:
        return "fake-model"

    async def generate(self, prompt: str, *, timeout_seconds: int) -> str:
        self.calls += 1
        if self._output is not None:
            return self._output
        raise AIProviderError("fake provider failure")

    async def health(self) -> bool:
        return False


async def _seed_stock(symbol: str = "AITEST", close_base: float = 2500.0) -> int:
    factory = get_session_factory()
    async with factory() as session:
        sector = Sector(name="AI Test Sector", description="Test")
        session.add(sector)
        await session.flush()

        stock = Stock(
            symbol=symbol,
            company_name="AI Test Ltd",
            exchange="NSE",
            series="EQ",
            isin=f"INE{symbol}0001",
            sector_id=sector.id,
            industry="AI Testing",
        )
        session.add(stock)
        await session.flush()

        base_date = date.today() - timedelta(days=35)
        for i in range(35):
            trade_date = base_date + timedelta(days=i)
            price = DailyPrice(
                stock_id=stock.id,
                trade_date=trade_date,
                open=close_base - 10 + i,
                high=close_base + 40 + i,
                low=close_base - 40 + i,
                close=close_base + i,
                previous_close=close_base + i - 1,
                volume=1000000 + i * 10000,
                delivery_quantity=200000 + i * 5000,
                delivery_percentage=55.0 + (i % 5),
            )
            session.add(price)

        latest_date = base_date + timedelta(days=34)
        indicator = IndicatorResult(
            stock_id=stock.id,
            trade_date=latest_date,
            ema_20=close_base + 20,
            ema_50=close_base + 10,
            ema_100=close_base + 5,
            ema_200=close_base,
            sma=close_base + 15,
            rsi_14=58.0,
            macd=10.5,
            macd_signal=9.0,
            macd_hist=1.5,
            stochastic_k=62.0,
            stochastic_d=55.0,
            atr=25.0,
            adx=30.0,
            supertrend=close_base + 5,
            supertrend_direction=1,
            vwap=close_base + 12,
            obv=15000000.0,
        )
        session.add(indicator)
        await session.commit()
        return stock.id


async def _calculate(symbol: str = "AITEST") -> None:
    factory = get_session_factory()
    async with factory() as session:
        engine = AnalyticsEngine(session)
        await engine.calculate_for_stock(symbol)
        await session.commit()


def _stock_context(symbol: str = "AITEST") -> AIStockContext:
    return AIStockContext(
        symbol=symbol,
        company_name="AI Test Ltd",
        trade_date=date(2026, 8, 3),
        latest_close=2534.0,
    )


def _analytics_context() -> AIAnalyticsContext:
    return AIAnalyticsContext(
        overall_score=82,
        trend_score=80,
        momentum_score=75,
        volume_score=70,
        delivery_score=60,
        volatility_score=50,
        support=2500.0,
        resistance=2600.0,
        trend_rating="Uptrend",
        momentum_rating="Bullish",
        volatility_rating="Moderate",
    )


# ---------------------------------------------------------------- provider factory


@pytest.mark.asyncio
async def test_get_provider_defaults_to_mock() -> None:
    assert isinstance(get_provider(), MockAIProvider)


@pytest.mark.asyncio
async def test_provider_factory_unknown_provider_raises() -> None:
    original = settings.ai_provider
    try:
        settings.ai_provider = "not-a-provider"
        reset_provider_cache()
        with pytest.raises(AIProviderError):
            get_provider()
    finally:
        settings.ai_provider = original
        reset_provider_cache()


@pytest.mark.asyncio
async def test_provider_instances_expose_name_and_model() -> None:
    for provider in (
        MockAIProvider(),
        OllamaProvider(),
        DeepSeekProvider(),
        OpenAIProvider(),
    ):
        assert provider.name
        assert provider.model_name()
        assert callable(provider.generate)
        assert callable(provider.health)


@pytest.mark.asyncio
async def test_mock_provider_is_deterministic() -> None:
    provider = MockAIProvider()
    first = await provider.generate("Symbol: AITEST\nOverall Score: 82", timeout_seconds=10)
    second = await provider.generate("Symbol: AITEST\nOverall Score: 82", timeout_seconds=10)
    assert first == second
    assert "AITEST" in first
    assert await provider.health() is True


@pytest.mark.asyncio
async def test_deepseek_provider_requires_api_key() -> None:
    original = settings.ai_deepseek_api_key
    try:
        settings.ai_deepseek_api_key = ""
        provider = DeepSeekProvider()
        with pytest.raises(AIProviderError):
            await provider.generate("prompt", timeout_seconds=10)
        assert await provider.health() is False
    finally:
        settings.ai_deepseek_api_key = original


# ---------------------------------------------------------------- prompt builder


@pytest.mark.asyncio
async def test_build_stock_summary_prompt_includes_data() -> None:
    prompt = build_stock_summary_prompt(
        stock=_stock_context(),
        analytics=_analytics_context(),
        indicator=None,
        matches=[],
        pipeline=None,  # type: ignore[arg-type]
    )
    assert "AITEST" in prompt
    assert "82" in prompt
    assert "Overall Score" in prompt
    assert "2026-08-03" in prompt


@pytest.mark.asyncio
async def test_prompt_hash_is_deterministic_and_input_sensitive() -> None:
    prompt_a = build_scanner_summary_prompt([_stock_context()], pipeline=None)  # type: ignore[arg-type]
    prompt_b = build_scanner_summary_prompt([_stock_context()], pipeline=None)  # type: ignore[arg-type]
    prompt_c = build_scanner_summary_prompt(
        [_stock_context(symbol="OTHER")], pipeline=None  # type: ignore[arg-type]
    )
    assert hash_prompt(prompt_a) == hash_prompt(prompt_b)
    assert hash_prompt(prompt_a) != hash_prompt(prompt_c)


@pytest.mark.asyncio
async def test_market_summary_prompt_renders() -> None:
    prompt = build_market_summary_prompt(
        [_stock_context()], pipeline=None, stock_count=1  # type: ignore[arg-type]
    )
    assert "AITEST" in prompt
    assert "1" in prompt


# ---------------------------------------------------------------- response parser


def test_parse_valid_json() -> None:
    payload = parse_stock_explanation(
        '{"summary": "hello", "strengths": ["a"], "confidence": 80}'
    )
    assert payload.summary == "hello"
    assert payload.strengths == ["a"]
    assert payload.confidence == 80


def test_parse_json_in_code_fence() -> None:
    raw = '```json\n{"summary": "fenced", "confidence": 10}\n```'
    payload = parse_stock_explanation(raw)
    assert payload.summary == "fenced"
    assert payload.confidence == 10


def test_parse_confidence_is_clamped() -> None:
    payload = parse_stock_explanation('{"summary": "x", "confidence": 500}')
    assert payload.confidence == 100
    payload = parse_stock_explanation('{"summary": "x", "confidence": -5}')
    assert payload.confidence == 0


def test_parse_strips_control_characters() -> None:
    payload = parse_stock_explanation('{"summary": "bad\\u0000text", "confidence": 1}')
    assert payload.summary == "badtext"


def test_parse_malformed_json_raises() -> None:
    with pytest.raises(AIResponseError):
        parse_stock_explanation("this is not json")


def test_parse_empty_raises() -> None:
    with pytest.raises(AIResponseError):
        parse_stock_explanation("")
    with pytest.raises(AIResponseError):
        parse_stock_explanation("   ")


def test_parse_missing_summary_raises() -> None:
    with pytest.raises(AIResponseError):
        parse_stock_explanation('{"strengths": ["a"]}')


def test_parse_collection_and_market() -> None:
    collection = parse_collection_explanation('{"summary": "s", "opportunities": ["A"]}')
    assert collection.summary == "s"
    assert collection.opportunities == ["A"]
    market = parse_market_explanation('{"summary": "m"}')
    assert market.summary == "m"


# ---------------------------------------------------------------- cache


def test_memory_cache_ttl() -> None:
    cache = AIMemoryCache(ttl=0)
    cache.set("k", {"v": 1})
    assert cache.get("k") is None


def test_memory_cache_round_trip() -> None:
    cache = AIMemoryCache(ttl=60)
    cache.set("k", {"v": 1})
    assert cache.get("k") == {"v": 1}


# ---------------------------------------------------------------- service


@pytest.mark.asyncio
async def test_explain_stock_generates_and_persists() -> None:
    await _seed_stock()
    await _calculate()

    factory = get_session_factory()
    async with factory() as session:
        service = AIExplanationService(session, provider=_FakeProvider(
            name="fake",
            output='{"summary": "generated", "strengths": ["s"], "confidence": 90}',
        ))
        result = await service.explain_stock("AITEST")
        assert result.source == "generated"
        assert result.summary == "generated"
        assert result.provider == "fake"
        assert result.technical_score.overall_score >= 0

    async with factory() as session:
        count = await session.scalar(select(func.count()).select_from(AIExplanation)) or 0
        assert count == 1


@pytest.mark.asyncio
async def test_explain_stock_hits_db_cache() -> None:
    await _seed_stock()
    await _calculate()

    factory = get_session_factory()
    async with factory() as session:
        service = AIExplanationService(
            session,
            provider=_FakeProvider(
                name="fake", output='{"summary": "first", "confidence": 88}'
            ),
        )
        first = await service.explain_stock("AITEST")
        assert first.source == "generated"

    async with factory() as session:
        second_service = AIExplanationService(
            session,
            provider=_FakeProvider(
                name="fake", output='{"summary": "second", "confidence": 88}'
            ),
        )
        second = await second_service.explain_stock("AITEST")
        assert second.source == "cache"
        assert second.summary == "first"


@pytest.mark.asyncio
async def test_explain_stock_falls_back_on_provider_error() -> None:
    await _seed_stock()
    await _calculate()

    factory = get_session_factory()
    async with factory() as session:
        service = AIExplanationService(session, provider=_FakeProvider(name="broken"))
        result = await service.explain_stock("AITEST")
        assert result.source == "fallback"
        assert "AITEST" in result.summary
        assert result.confidence >= 0


@pytest.mark.asyncio
async def test_explain_stock_falls_back_on_malformed_response() -> None:
    await _seed_stock()
    await _calculate()

    factory = get_session_factory()
    async with factory() as session:
        service = AIExplanationService(
            session, provider=_FakeProvider(name="bad", output="not json at all")
        )
        result = await service.explain_stock("AITEST")
        assert result.source == "fallback"


@pytest.mark.asyncio
async def test_explain_stock_falls_back_on_empty_response() -> None:
    await _seed_stock()
    await _calculate()

    factory = get_session_factory()
    async with factory() as session:
        service = AIExplanationService(session, provider=_FakeProvider(name="empty", output=""))
        result = await service.explain_stock("AITEST")
        assert result.source == "fallback"


@pytest.mark.asyncio
async def test_explain_stock_unknown_symbol_raises() -> None:
    factory = get_session_factory()
    async with factory() as session:
        service = AIExplanationService(session, provider=_FakeProvider(name="fake"))
        with pytest.raises(AINotFoundError):
            await service.explain_stock("NOPE123")


@pytest.mark.asyncio
async def test_explain_stock_without_analytics_raises() -> None:
    await _seed_stock()
    factory = get_session_factory()
    async with factory() as session:
        service = AIExplanationService(session, provider=_FakeProvider(name="fake"))
        with pytest.raises(AINotFoundError):
            await service.explain_stock("AITEST")


@pytest.mark.asyncio
async def test_explain_top_rated_with_data() -> None:
    await _seed_stock()
    await _calculate()

    factory = get_session_factory()
    async with factory() as session:
        service = AIExplanationService(
            session,
            provider=_FakeProvider(
                name="fake",
                output='{"summary": "top", "opportunities": ["AITEST"], "confidence": 70}',
            ),
        )
        result = await service.explain_top_rated(limit=5)
        assert result.source == "generated"
        assert result.summary == "top"
        assert [s.symbol for s in result.stocks] == ["AITEST"]


@pytest.mark.asyncio
async def test_explain_top_rated_empty_fallback() -> None:
    factory = get_session_factory()
    async with factory() as session:
        service = AIExplanationService(session, provider=_FakeProvider(name="broken"))
        result = await service.explain_top_rated(limit=5)
        assert result.source == "fallback"
        assert result.stocks == []


@pytest.mark.asyncio
async def test_explain_market_summary() -> None:
    await _seed_stock()
    await _calculate()

    factory = get_session_factory()
    async with factory() as session:
        service = AIExplanationService(
            session,
            provider=_FakeProvider(
                name="fake", output='{"summary": "market", "confidence": 60}'
            ),
        )
        result = await service.explain_market_summary()
        assert result.source == "generated"
        assert result.summary == "market"
        assert result.stock_count >= 1


# ---------------------------------------------------------------- API


@pytest.mark.asyncio
async def test_ai_api_stock_explanation() -> None:
    await _seed_stock()
    await _calculate()

    async with analyst_client() as client:
        response = await client.get("/api/v1/ai/stock/AITEST")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "AITEST"
    assert "summary" in body
    assert "confidence" in body
    assert "technical_score" in body
    assert body["provider"] == "mock"


@pytest.mark.asyncio
async def test_ai_api_stock_explanation_404() -> None:
    async with analyst_client() as client:
        response = await client.get("/api/v1/ai/stock/NOPE123")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_ai_api_top_rated() -> None:
    await _seed_stock()
    await _calculate()

    async with analyst_client() as client:
        response = await client.get("/api/v1/ai/top-rated")
    assert response.status_code == 200
    body = response.json()
    assert "summary" in body
    assert body["stocks"][0]["symbol"] == "AITEST"


@pytest.mark.asyncio
async def test_ai_api_market_summary() -> None:
    await _seed_stock()
    await _calculate()

    async with analyst_client() as client:
        response = await client.get("/api/v1/ai/market-summary")
    assert response.status_code == 200
    body = response.json()
    assert "summary" in body
    assert "stock_count" in body


# ---------------------------------------------------------------- UI


@pytest.mark.asyncio
async def test_stock_detail_shows_ai_card() -> None:
    await _seed_stock()
    await _calculate()

    async with analyst_client(browser=True) as client:
        response = await client.get("/stocks/AITEST")
        partial = await client.get("/stocks/AITEST/ai")
    assert response.status_code == 200
    assert "AI Explanation" in response.text
    assert partial.status_code == 200
    assert "AI Explanation" in partial.text
    assert "AITEST" in partial.text


@pytest.mark.asyncio
async def test_dashboard_shows_ai_sections() -> None:
    await _seed_stock()
    await _calculate()

    async with analyst_client(browser=True) as client:
        response = await client.get("/dashboard")
    assert response.status_code == 200
    assert "AI Insights" in response.text
    assert "Top Opportunities" in response.text
    assert "Highest Confidence" in response.text
    assert "Recent AI Explanations" in response.text
