from __future__ import annotations

import csv
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any

import httpx

from app.market_intelligence.index_catalog import (
    GLOBAL_INTELLIGENCE_INDEX_CODES,
    INDEX_CATALOG_BY_CODE,
)
from app.market_intelligence.providers.base import MarketProvider, ProviderResult

GLOBAL_PROVIDER_SYMBOLS: dict[str, str] = {
    code: INDEX_CATALOG_BY_CODE[code].provider_symbol
    for code in GLOBAL_INTELLIGENCE_INDEX_CODES
}


class GlobalIndexProvider(MarketProvider):
    """Stooq-compatible end-of-day adapter for required US and Asian indices.

    HTTP and the clock are injected so automated tests never require network
    access and historical results never depend on an implicit wall clock.
    """

    name = "stooq_global_indices"
    supports_indices = list(GLOBAL_INTELLIGENCE_INDEX_CODES)
    _base_url = "https://stooq.com/q/d/l/"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        self._owns_client = client is None
        self._now = now or (lambda: datetime.now(UTC))

    async def fetch(self, **kwargs: Any) -> ProviderResult:
        index_code = kwargs.get("index_code")
        trade_date = kwargs.get("trade_date")
        if not isinstance(trade_date, date):
            return self._failure("invalid_trade_date", "trade_date must be a date")

        result = await self.fetch_range(index_code, trade_date, trade_date)
        if result.is_success:
            rows = result.data if isinstance(result.data, list) else []
            if not rows:
                return self._unavailable(
                    "no_session",
                    f"No {index_code} market session exists on {trade_date}",
                )
            return result.model_copy(update={"data": rows[0]})
        return result

    async def fetch_range(
        self,
        index_code: str,
        start_date: date,
        end_date: date,
    ) -> ProviderResult:
        if index_code not in self.supports_indices:
            return self._unavailable(
                "unsupported_index",
                f"Unsupported global index code: {index_code}",
            )
        if start_date > end_date:
            return self._failure("invalid_range", "start_date must be on or before end_date")

        symbol = GLOBAL_PROVIDER_SYMBOLS[index_code]
        params = {
            "s": symbol,
            "d1": start_date.strftime("%Y%m%d"),
            "d2": end_date.strftime("%Y%m%d"),
            "i": "d",
        }
        fetched_at = self._aware_now()
        try:
            response = await self._client.get(self._base_url, params=params)
        except httpx.TimeoutException as exc:
            return self._unavailable("provider_timeout", f"Provider request timed out: {exc}")
        except httpx.HTTPError as exc:
            return self._failure("provider_http_error", f"Provider request failed: {exc}")

        if response.status_code in {401, 403, 429}:
            return ProviderResult(
                provider_name=self.name,
                status="blocked",
                freshness="missing",
                fetched_at=fetched_at.isoformat(),
                error_code=f"HTTP_{response.status_code}",
                message="Provider access was blocked or rate limited",
            )
        if response.status_code == 404 or response.status_code >= 500:
            return self._unavailable(
                f"HTTP_{response.status_code}",
                f"Provider unavailable (HTTP {response.status_code})",
                fetched_at=fetched_at,
            )
        if response.is_error:
            return self._failure(
                f"HTTP_{response.status_code}",
                f"Provider request failed (HTTP {response.status_code})",
                fetched_at=fetched_at,
            )

        try:
            rows = self._parse_csv(response.text, index_code, str(response.request.url))
        except (KeyError, ValueError, InvalidOperation, csv.Error) as exc:
            return self._failure(
                "parse_error",
                f"Malformed provider response: {exc}",
                fetched_at=fetched_at,
            )

        rows = [row for row in rows if start_date <= row["trade_date"] <= end_date]
        if not rows:
            return self._unavailable(
                "no_data",
                f"No {index_code} sessions in requested range",
                fetched_at=fetched_at,
            )

        latest_date = rows[-1]["trade_date"]
        return ProviderResult(
            provider_name=self.name,
            status="success",
            observed_at=latest_date.isoformat(),
            fetched_at=fetched_at.isoformat(),
            market_date=latest_date.isoformat(),
            freshness="recent",
            data=rows,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _aware_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("GlobalIndexProvider clock must return an aware datetime")
        return value

    @staticmethod
    def _parse_csv(text: str, index_code: str, source_reference: str) -> list[dict[str, Any]]:
        reader = csv.DictReader(StringIO(text.strip()))
        required = {"Date", "Open", "High", "Low", "Close"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("required OHLC columns are missing")

        rows: list[dict[str, Any]] = []
        for raw in reader:
            if not raw.get("Date"):
                continue
            volume_text = (raw.get("Volume") or "").strip()
            rows.append(
                {
                    "index_code": index_code,
                    "trade_date": date.fromisoformat(raw["Date"]),
                    "open": Decimal(raw["Open"]),
                    "high": Decimal(raw["High"]),
                    "low": Decimal(raw["Low"]),
                    "close": Decimal(raw["Close"]),
                    "volume": int(Decimal(volume_text)) if volume_text not in {"", "N/D"} else None,
                    "source_reference": source_reference,
                }
            )
        rows.sort(key=lambda row: row["trade_date"])
        return rows

    def _unavailable(
        self,
        error_code: str,
        message: str,
        *,
        fetched_at: datetime | None = None,
    ) -> ProviderResult:
        return ProviderResult(
            provider_name=self.name,
            status="unavailable",
            freshness="missing",
            fetched_at=fetched_at.isoformat() if fetched_at else None,
            error_code=error_code,
            message=message,
        )

    def _failure(
        self,
        error_code: str,
        message: str,
        *,
        fetched_at: datetime | None = None,
    ) -> ProviderResult:
        return ProviderResult(
            provider_name=self.name,
            status="failed",
            freshness="missing",
            fetched_at=fetched_at.isoformat() if fetched_at else None,
            error_code=error_code,
            message=message,
        )