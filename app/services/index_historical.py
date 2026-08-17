from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.market_intelligence.providers.base import MarketProvider, ProviderResult
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.repositories.market_index import MarketIndexRepository
from app.repositories.market_index_price import MarketIndexPriceRepository

MAX_INDEX_RANGE_DAYS = 366


class IndexHistoricalService:
    """Provider-neutral, idempotent historical index ingestion.

    Providers are attempted in configured order. Range-capable providers are
    called once per index/range, never once per calendar date. The caller owns
    commit/rollback; this service only flushes newly normalized rows.
    """

    def __init__(
        self,
        session: AsyncSession,
        providers: tuple[MarketProvider, ...],
    ) -> None:
        self._session = session
        self._providers = providers
        self._index_repo = MarketIndexRepository(session)
        self._price_repo = MarketIndexPriceRepository(session)

    async def ingest_date(self, index_code: str, trade_date: date) -> ProviderResult:
        index = await self._index_repo.get_by_code(index_code)
        if index is None:
            return self._missing_index(index_code)

        existing = await self._price_repo.get_by_index_and_date(index.id, trade_date)
        if existing is not None:
            return self._db_result(existing, status="cached")

        provider_results: list[ProviderResult] = []
        for provider in self._supporting_providers(index_code):
            result = await provider.fetch(index_code=index_code, trade_date=trade_date)
            provider_results.append(result)
            if result.is_success and isinstance(result.data, dict):
                data = dict(result.data)
                if data.get("previous_close") is None:
                    previous = await self._price_repo.get_latest_on_or_before(
                        index.id, trade_date - timedelta(days=1)
                    )
                    if previous is not None:
                        data["previous_close"] = previous.close
                price = self._to_price(index, data, result)
                await self._price_repo.bulk_upsert([price])
                return result

        fallback = await self._price_repo.get_latest_on_or_before(index.id, trade_date)
        if fallback is not None:
            return self._db_result(fallback, status="stale")
        return self._best_failure(index_code, provider_results)

    async def ingest_range(
        self,
        index_code: str,
        start_date: date,
        end_date: date,
    ) -> list[ProviderResult]:
        if start_date > end_date:
            raise ValueError("start_date must be on or before end_date")
        if (end_date - start_date).days > MAX_INDEX_RANGE_DAYS:
            raise ValueError(f"index history range cannot exceed {MAX_INDEX_RANGE_DAYS} days")

        index = await self._index_repo.get_by_code(index_code)
        if index is None:
            return [self._missing_index(index_code)]

        existing_dates = await self._price_repo.get_existing_dates(index.id, start_date, end_date)
        provider_results: list[ProviderResult] = []
        successful: ProviderResult | None = None
        fetched_rows: list[dict[str, Any]] = []

        for provider in self._supporting_providers(index_code):
            fetch_range = getattr(provider, "fetch_range", None)
            if callable(fetch_range):
                result = await fetch_range(index_code, start_date, end_date)
            elif start_date == end_date:
                result = await provider.fetch(index_code=index_code, trade_date=start_date)
            else:
                continue
            provider_results.append(result)
            if result.is_success:
                successful = result
                fetched_rows = self._payload_rows(result.data)
                break

        inserted: list[MarketIndexPrice] = []
        boundary_previous = await self._price_repo.get_latest_on_or_before(
            index.id, start_date - timedelta(days=1)
        )
        previous_close: Decimal | None = (
            Decimal(str(boundary_previous.close)) if boundary_previous is not None else None
        )
        for row in sorted(fetched_rows, key=lambda item: item["trade_date"]):
            trade_date = row["trade_date"]
            close = Decimal(str(row["close"]))
            row_previous = row.get("previous_close")
            normalized_previous = (
                Decimal(str(row_previous)) if row_previous is not None else previous_close
            )
            if trade_date not in existing_dates and successful is not None:
                enriched = dict(row)
                enriched["previous_close"] = normalized_previous
                inserted.append(self._to_price(index, enriched, successful))
            previous_close = close

        if inserted:
            await self._price_repo.bulk_upsert(inserted)

        rows = await self._price_repo.get_date_range(index.id, start_date, end_date)
        if rows:
            status = "success" if inserted else "cached"
            return [self._db_result(row, status=status) for row in reversed(rows)]
        if provider_results:
            return [self._best_failure(index_code, provider_results)]
        return [
            ProviderResult(
                provider_name="index_historical",
                status="unavailable",
                freshness="missing",
                error_code="no_range_provider",
                message=f"No range-capable provider supports {index_code}",
            )
        ]

    async def ingest_indices(
        self,
        index_codes: tuple[str, ...],
        start_date: date,
        end_date: date,
    ) -> dict[str, list[ProviderResult]]:
        return {
            code: await self.ingest_range(code, start_date, end_date) for code in index_codes
        }

    def _supporting_providers(self, index_code: str) -> list[MarketProvider]:
        return [
            provider
            for provider in self._providers
            if index_code in getattr(provider, "supports_indices", [])
        ]

    @staticmethod
    def _payload_rows(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list) and all(isinstance(row, dict) for row in data):
            return data
        return []

    @staticmethod
    def _to_price(
        index: MarketIndex,
        data: dict[str, Any],
        result: ProviderResult,
    ) -> MarketIndexPrice:
        close = Decimal(str(data["close"]))
        previous_close_value = data.get("previous_close")
        previous_close = (
            Decimal(str(previous_close_value)) if previous_close_value is not None else None
        )
        change = close - previous_close if previous_close is not None else None
        change_percent = (
            change / previous_close * Decimal("100")
            if change is not None and previous_close != 0
            else None
        )
        trade_date = data["trade_date"]
        return MarketIndexPrice(
            market_index_id=index.id,
            trade_date=trade_date,
            open=Decimal(str(data["open"])),
            high=Decimal(str(data["high"])),
            low=Decimal(str(data["low"])),
            close=close,
            previous_close=previous_close,
            change=change,
            change_percent=change_percent,
            volume=data.get("volume"),
            observed_at=IndexHistoricalService._result_date(result.observed_at) or trade_date,
            fetched_at=IndexHistoricalService._result_date(result.fetched_at),
            source_name=result.provider_name,
            source_reference=data.get("source_reference"),
            freshness_status=result.freshness,
        )

    @staticmethod
    def _result_date(value: str | None) -> date | None:
        if value is None:
            return None
        return date.fromisoformat(value[:10])

    @staticmethod
    def _db_result(price: MarketIndexPrice, *, status: str) -> ProviderResult:
        freshness = price.freshness_status
        if status == "stale":
            freshness = "stale"
        return ProviderResult(
            provider_name=price.source_name or "market_index_database",
            status=status,
            observed_at=price.observed_at.isoformat() if price.observed_at else None,
            fetched_at=price.fetched_at.isoformat() if price.fetched_at else None,
            market_date=price.trade_date.isoformat(),
            freshness=freshness,
            data={
                "trade_date": price.trade_date,
                "open": price.open,
                "high": price.high,
                "low": price.low,
                "close": price.close,
                "previous_close": price.previous_close,
                "volume": price.volume,
                "source_reference": price.source_reference,
            },
        )

    @staticmethod
    def _missing_index(index_code: str) -> ProviderResult:
        return ProviderResult(
            provider_name="index_historical",
            status="unavailable",
            freshness="missing",
            error_code="index_not_catalogued",
            message=f"Market index is not catalogued: {index_code}",
        )

    @staticmethod
    def _best_failure(
        index_code: str, provider_results: list[ProviderResult]
    ) -> ProviderResult:
        if not provider_results:
            return ProviderResult(
                provider_name="index_historical",
                status="unavailable",
                freshness="missing",
                error_code="no_provider",
                message=f"No configured provider supports {index_code}",
            )
        priority = {"blocked": 3, "failed": 2, "unavailable": 1}
        return max(provider_results, key=lambda item: priority.get(item.status, 0))