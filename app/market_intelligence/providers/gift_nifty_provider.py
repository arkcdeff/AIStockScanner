from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.market_intelligence.index_catalog import GIFT_NIFTY_INDEX_CODE
from app.market_intelligence.providers.base import MarketProvider, ProviderResult

REQUIRED_COLUMNS = frozenset(
    {
        "observed_at",
        "available_at",
        "market_date",
        "price",
        "contract_symbol",
        "expiry_date",
        "exchange",
        "instrument_type",
        "underlying",
        "currency",
        "source_reference",
    }
)

EXPECTED_IDENTITY = {
    "exchange": "NSE_IX",
    "instrument_type": "FUTIDX",
    "underlying": "NIFTY_50",
    "currency": "USD",
}


class GiftNiftyProvider(MarketProvider):
    """Truthful GIFT Nifty futures provider backed by an explicit local CSV.

    The Task 18.4 source audit found no documented stable unauthenticated NSE IX
    quote API or automation licence. This adapter therefore performs no HTTP.
    Without ``csv_path`` it returns ``unavailable``. The manual file must carry
    aware observation/availability timestamps and exact NSE IX futures identity;
    proxy instruments are rejected.
    """

    name = "nse_ix_gift_nifty_manual"
    supports_indices = [GIFT_NIFTY_INDEX_CODE]

    def __init__(self, csv_path: str | Path | None = None) -> None:
        self._csv_path = Path(csv_path) if csv_path is not None else None

    async def fetch(self, **kwargs: Any) -> ProviderResult:
        index_code = kwargs.get("index_code", GIFT_NIFTY_INDEX_CODE)
        evaluation_at = kwargs.get("evaluation_at")
        if index_code != GIFT_NIFTY_INDEX_CODE:
            return self._unavailable(
                "unsupported_index",
                f"Unsupported index code: {index_code}",
            )
        if not isinstance(evaluation_at, datetime):
            return self._failed("invalid_evaluation_at", "evaluation_at must be a datetime")
        if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
            return self._failed(
                "naive_evaluation_at", "evaluation_at must be timezone-aware"
            )
        if self._csv_path is None:
            return self._unavailable(
                "automation_not_configured",
                "No licensed automated NSE IX source is configured; supply a validated local CSV",
            )
        if not self._csv_path.is_file():
            return self._unavailable(
                "manual_file_not_found",
                "Configured GIFT Nifty manual CSV was not found",
            )

        try:
            observations = self._read_observations(self._csv_path)
        except (OSError, csv.Error, KeyError, ValueError, InvalidOperation) as exc:
            return self._failed("manual_file_invalid", f"Invalid GIFT Nifty CSV: {exc}")

        eligible = [
            item
            for item in observations
            if item["observed_at"] <= evaluation_at and item["available_at"] <= evaluation_at
        ]
        if not eligible:
            return self._unavailable(
                "no_knowable_observation",
                "No GIFT Nifty futures observation was knowable by the evaluation timestamp",
            )
        latest = max(eligible, key=lambda item: (item["observed_at"], item["available_at"]))
        payload = dict(latest)
        observed_at = payload.pop("observed_at")
        available_at = payload.pop("available_at")
        market_date = payload["market_date"]
        return ProviderResult(
            provider_name=self.name,
            status="success",
            observed_at=observed_at.isoformat(),
            fetched_at=available_at.isoformat(),
            market_date=market_date.isoformat(),
            freshness="recent",
            data=payload,
        )

    @staticmethod
    def _read_observations(path: Path) -> list[dict[str, Any]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
                missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames or ()))
                raise ValueError(f"required columns missing: {', '.join(missing)}")
            rows = [GiftNiftyProvider._normalize_row(row) for row in reader]
        if not rows:
            raise ValueError("CSV contains no observations")
        return rows

    @staticmethod
    def _normalize_row(row: dict[str, str]) -> dict[str, Any]:
        for field, expected in EXPECTED_IDENTITY.items():
            if (row.get(field) or "").strip().upper() != expected:
                raise ValueError(f"{field} must be {expected}; proxy instruments are not accepted")
        contract_symbol = (row.get("contract_symbol") or "").strip().upper()
        if not contract_symbol or not contract_symbol.startswith("NIFTY"):
            raise ValueError("contract_symbol must identify an NSE IX NIFTY futures contract")

        observed_at = datetime.fromisoformat((row.get("observed_at") or "").strip())
        available_at = datetime.fromisoformat((row.get("available_at") or "").strip())
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if available_at.tzinfo is None or available_at.utcoffset() is None:
            raise ValueError("available_at must be timezone-aware")
        if available_at < observed_at:
            raise ValueError("available_at cannot precede observed_at")

        price = Decimal((row.get("price") or "").strip())
        if not price.is_finite() or price <= 0:
            raise ValueError("price must be a finite positive number")
        source_reference = (row.get("source_reference") or "").strip()
        if not source_reference:
            raise ValueError("source_reference is required")
        return {
            "index_code": GIFT_NIFTY_INDEX_CODE,
            "price": price,
            "contract_symbol": contract_symbol,
            "expiry_date": date.fromisoformat((row.get("expiry_date") or "").strip()),
            "market_date": date.fromisoformat((row.get("market_date") or "").strip()),
            "exchange": EXPECTED_IDENTITY["exchange"],
            "instrument_type": EXPECTED_IDENTITY["instrument_type"],
            "underlying": EXPECTED_IDENTITY["underlying"],
            "currency": EXPECTED_IDENTITY["currency"],
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