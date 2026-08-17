from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.market_intelligence.index_catalog import MARKET_FACTOR_INDEX_CODES
from app.market_intelligence.providers.base import MarketProvider, ProviderResult

REQUIRED_COLUMNS = frozenset(
    {
        "observed_at",
        "available_at",
        "trade_date",
        "index_code",
        "open",
        "high",
        "low",
        "close",
        "exchange",
        "instrument_type",
        "instrument",
        "quote_convention",
        "currency",
        "unit",
        "source_reference",
    }
)

EXPECTED_IDENTITIES: dict[str, dict[str, str]] = {
    "INDIA_VIX": {
        "exchange": "NSE",
        "instrument_type": "VOLATILITY_INDEX",
        "instrument": "INDIA_VIX",
        "quote_convention": "INDIA_VIX_INDEX_POINTS",
        "currency": "NONE",
        "unit": "INDEX_POINTS",
    },
    "USD_INR": {
        "exchange": "FX_SPOT",
        "instrument_type": "FX_SPOT",
        "instrument": "USD_INR_SPOT",
        "quote_convention": "INR_PER_1_USD",
        "currency": "INR",
        "unit": "INR_PER_USD",
    },
    "BRENT_CRUDE": {
        "exchange": "PHYSICAL_SPOT",
        "instrument_type": "COMMODITY_SPOT",
        "instrument": "EUROPE_BRENT_SPOT",
        "quote_convention": "USD_PER_BARREL",
        "currency": "USD",
        "unit": "USD_PER_BARREL",
    },
}


class ManualMarketFactorProvider(MarketProvider):
    """Validated no-HTTP import adapter for Task 18.5 daily market factors.

    The source audit established no common documented unauthenticated automation
    contract for these exact instruments. Each configured local export must state
    exact identity, units, and aware observation/availability timestamps. Missing
    configuration is reported as unavailable; proxies are rejected.
    """

    name = "validated_manual_market_factors"
    supports_indices = list(MARKET_FACTOR_INDEX_CODES)

    def __init__(self, csv_paths: dict[str, str | Path] | None = None) -> None:
        self._csv_paths = {
            code: Path(path) for code, path in (csv_paths or {}).items()
        }

    async def fetch(self, **kwargs: Any) -> ProviderResult:
        index_code = kwargs.get("index_code")
        trade_date = kwargs.get("trade_date")
        evaluation_at = kwargs.get("evaluation_at")
        if not isinstance(trade_date, date):
            return self._failed("invalid_trade_date", "trade_date must be a date")
        return await self.fetch_range(
            index_code,
            trade_date,
            trade_date,
            evaluation_at=evaluation_at,
        )

    async def fetch_range(
        self,
        index_code: str,
        start_date: date,
        end_date: date,
        *,
        evaluation_at: datetime | None = None,
    ) -> ProviderResult:
        if index_code not in MARKET_FACTOR_INDEX_CODES:
            return self._unavailable(
                "unsupported_instrument", f"Unsupported market factor code: {index_code}"
            )
        if start_date > end_date:
            return self._failed("invalid_range", "start_date must be on or before end_date")
        if not isinstance(evaluation_at, datetime):
            return self._failed(
                "invalid_evaluation_at", "evaluation_at must be an explicit aware datetime"
            )
        if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
            return self._failed(
                "naive_evaluation_at", "evaluation_at must be timezone-aware"
            )

        path = self._csv_paths.get(index_code)
        if path is None:
            return self._unavailable(
                "automation_not_configured",
                "No licensed automated source is configured; supply a validated local CSV",
            )
        if not path.is_file():
            return self._unavailable(
                "manual_file_not_found", "Configured market-factor CSV was not found"
            )

        try:
            rows = self._read_rows(
                path,
                index_code,
                start_date=start_date,
                end_date=end_date,
                evaluation_at=evaluation_at,
            )
        except (OSError, csv.Error, KeyError, ValueError, InvalidOperation) as exc:
            return self._failed("manual_file_invalid", f"Invalid market-factor CSV: {exc}")

        eligible = [
            row
            for row in rows
            if start_date <= row["trade_date"] <= end_date
            and row["observed_at"] <= evaluation_at
            and row["available_at"] <= evaluation_at
        ]
        if not eligible:
            return self._unavailable(
                "no_knowable_observation",
                "No exact-instrument observation was knowable in the requested range",
            )
        eligible.sort(key=lambda row: (row["trade_date"], row["available_at"]))
        payload = []
        for row in eligible:
            item = dict(row)
            payload.append(item)
        latest = eligible[-1]
        return ProviderResult(
            provider_name=self.name,
            status="success",
            observed_at=latest["observed_at"].isoformat(),
            fetched_at=latest["available_at"].isoformat(),
            market_date=latest["trade_date"].isoformat(),
            freshness="recent",
            data=payload,
        )

    @staticmethod
    def _read_rows(
        path: Path,
        index_code: str,
        *,
        start_date: date,
        end_date: date,
        evaluation_at: datetime,
    ) -> list[dict[str, Any]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
                missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames or ()))
                raise ValueError(f"required columns missing: {', '.join(missing)}")
            rows = []
            for raw in reader:
                if not ManualMarketFactorProvider._row_can_be_knowable(
                    raw,
                    index_code,
                    start_date=start_date,
                    end_date=end_date,
                    evaluation_at=evaluation_at,
                ):
                    continue
                rows.append(ManualMarketFactorProvider._normalize_row(raw, index_code))
        return rows

    @staticmethod
    def _row_can_be_knowable(
        row: dict[str, str],
        index_code: str,
        *,
        start_date: date,
        end_date: date,
        evaluation_at: datetime,
    ) -> bool:
        try:
            trade_date = date.fromisoformat((row.get("trade_date") or "").strip())
            observed_at = datetime.fromisoformat((row.get("observed_at") or "").strip())
            available_at = datetime.fromisoformat((row.get("available_at") or "").strip())
        except ValueError:
            # If knowability cannot be established, keep the row so strict
            # normalization reports a malformed eligible export rather than
            # silently treating it as absent.
            return True
        in_scope = (
            (row.get("index_code") or "").strip().upper() == index_code
            and start_date <= trade_date <= end_date
        )
        if not in_scope:
            return False
        if (
            observed_at.tzinfo is None
            or observed_at.utcoffset() is None
            or available_at.tzinfo is None
            or available_at.utcoffset() is None
        ):
            return True
        return (
            observed_at <= evaluation_at
            and available_at <= evaluation_at
        )

    @staticmethod
    def _normalize_row(row: dict[str, str], index_code: str) -> dict[str, Any]:
        if (row.get("index_code") or "").strip().upper() != index_code:
            raise ValueError(f"index_code must be {index_code}")
        identity = EXPECTED_IDENTITIES[index_code]
        for field, expected in identity.items():
            if (row.get(field) or "").strip().upper() != expected:
                raise ValueError(f"{field} must be {expected}; proxy instruments are rejected")

        observed_at = datetime.fromisoformat((row.get("observed_at") or "").strip())
        available_at = datetime.fromisoformat((row.get("available_at") or "").strip())
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if available_at.tzinfo is None or available_at.utcoffset() is None:
            raise ValueError("available_at must be timezone-aware")
        if available_at < observed_at:
            raise ValueError("available_at cannot precede observed_at")

        values = {
            field: Decimal((row.get(field) or "").strip())
            for field in ("open", "high", "low", "close")
        }
        if any(not value.is_finite() or value <= 0 for value in values.values()):
            raise ValueError("OHLC values must be finite positive numbers")
        if values["high"] < max(values["open"], values["low"], values["close"]):
            raise ValueError("high must be greater than or equal to open, low, and close")
        if values["low"] > min(values["open"], values["high"], values["close"]):
            raise ValueError("low must be less than or equal to open, high, and close")
        source_reference = (row.get("source_reference") or "").strip()
        if not source_reference:
            raise ValueError("source_reference is required")
        return {
            "index_code": index_code,
            "trade_date": date.fromisoformat((row.get("trade_date") or "").strip()),
            **values,
            **identity,
            "source_reference": source_reference,
            "observed_at": observed_at,
            "available_at": available_at,
        }

    def _unavailable(self, error_code: str, message: str) -> ProviderResult:
        return ProviderResult(
            provider_name=self.name,
            status="unavailable",
            freshness="missing",
            error_code=error_code,
            message=message,
        )

    def _failed(self, error_code: str, message: str) -> ProviderResult:
        return ProviderResult(
            provider_name=self.name,
            status="failed",
            freshness="missing",
            error_code=error_code,
            message=message,
        )