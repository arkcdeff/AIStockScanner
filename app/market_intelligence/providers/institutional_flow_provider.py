from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.market_intelligence.providers.base import MarketProvider, ProviderResult

REQUIRED_COLUMNS = frozenset(
    {
        "observed_at",
        "available_at",
        "trade_date",
        "investor_type",
        "source_investor_label",
        "buy_value",
        "sell_value",
        "unit",
        "dataset",
        "source_reference",
    }
)
EXPECTED_LABELS = {"FII": "FII/FPI", "DII": "DII"}
EXPECTED_UNIT = "INR_CRORE"
EXPECTED_DATASET = "PROVISIONAL_CASH_MARKET_INSTITUTIONAL_ACTIVITY"


class InstitutionalFlowProvider(MarketProvider):
    """Validated no-HTTP import adapter for provisional FII/FPI and DII flows."""

    name = "validated_manual_institutional_flows"
    supports_markets = ["INDIA_INSTITUTIONAL_FLOWS"]

    def __init__(self, csv_path: str | Path | None = None) -> None:
        self._csv_path = Path(csv_path) if csv_path is not None else None

    async def fetch(self, **kwargs: Any) -> ProviderResult:
        evaluation_at = kwargs.get("evaluation_at")
        if not isinstance(evaluation_at, datetime):
            return self._failed(
                "invalid_evaluation_at", "evaluation_at must be an explicit aware datetime"
            )
        if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
            return self._failed(
                "naive_evaluation_at", "evaluation_at must be timezone-aware"
            )
        investor_type = kwargs.get("investor_type")
        if investor_type is not None and investor_type not in EXPECTED_LABELS:
            return self._unavailable(
                "unsupported_investor_type", f"Unsupported investor type: {investor_type}"
            )
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")
        if start_date is not None and not isinstance(start_date, date):
            return self._failed("invalid_start_date", "start_date must be a date")
        if end_date is not None and not isinstance(end_date, date):
            return self._failed("invalid_end_date", "end_date must be a date")
        if start_date is not None and end_date is not None and start_date > end_date:
            return self._failed("invalid_range", "start_date must be on or before end_date")
        if self._csv_path is None:
            return self._unavailable(
                "automation_not_configured",
                "No documented automated source is configured; supply a validated local CSV",
            )
        if not self._csv_path.is_file():
            return self._unavailable(
                "manual_file_not_found", "Configured institutional-flow CSV was not found"
            )
        try:
            rows = self._read_rows(
                self._csv_path,
                evaluation_at=evaluation_at,
                start_date=start_date,
                end_date=end_date,
                investor_type=investor_type,
            )
        except (OSError, csv.Error, KeyError, ValueError, InvalidOperation) as exc:
            return self._failed("manual_file_invalid", f"Invalid institutional-flow CSV: {exc}")

        eligible = [
            row
            for row in rows
            if row["observed_at"] <= evaluation_at
            and row["available_at"] <= evaluation_at
            and (investor_type is None or row["investor_type"] == investor_type)
            and (start_date is None or row["trade_date"] >= start_date)
            and (end_date is None or row["trade_date"] <= end_date)
        ]
        if not eligible:
            return self._unavailable(
                "no_knowable_observation",
                "No institutional-flow observation was knowable by the evaluation timestamp",
            )
        eligible.sort(
            key=lambda row: (
                row["trade_date"],
                row["investor_type"],
                row["available_at"],
            )
        )
        latest_date = max(row["trade_date"] for row in eligible)
        latest = [row for row in eligible if row["trade_date"] == latest_date]
        latest_available = max(latest, key=lambda row: row["available_at"])
        return ProviderResult(
            provider_name=self.name,
            status="success",
            observed_at=max(row["observed_at"] for row in latest).isoformat(),
            fetched_at=latest_available["available_at"].isoformat(),
            market_date=latest_date.isoformat(),
            freshness="recent",
            data=[dict(row) for row in eligible],
        )

    @staticmethod
    def _read_rows(
        path: Path,
        *,
        evaluation_at: datetime,
        start_date: date | None,
        end_date: date | None,
        investor_type: str | None,
    ) -> list[dict[str, Any]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
                missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames or ()))
                raise ValueError(f"required columns missing: {', '.join(missing)}")
            rows = []
            for raw in reader:
                if not InstitutionalFlowProvider._row_can_be_knowable(
                    raw,
                    evaluation_at=evaluation_at,
                    start_date=start_date,
                    end_date=end_date,
                    investor_type=investor_type,
                ):
                    continue
                rows.append(InstitutionalFlowProvider._normalize_row(raw))
        return rows

    @staticmethod
    def _row_can_be_knowable(
        row: dict[str, str],
        *,
        evaluation_at: datetime,
        start_date: date | None,
        end_date: date | None,
        investor_type: str | None,
    ) -> bool:
        try:
            trade_date = date.fromisoformat((row.get("trade_date") or "").strip())
            observed_at = datetime.fromisoformat((row.get("observed_at") or "").strip())
            available_at = datetime.fromisoformat((row.get("available_at") or "").strip())
        except ValueError:
            # Unknown timestamp/date precision cannot be safely classified as
            # future-only, so retain it for strict normalization failure.
            return True
        row_type = (row.get("investor_type") or "").strip().upper()
        in_scope = (
            (investor_type is None or row_type == investor_type)
            and (start_date is None or trade_date >= start_date)
            and (end_date is None or trade_date <= end_date)
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
    def _normalize_row(row: dict[str, str]) -> dict[str, Any]:
        investor_type = (row.get("investor_type") or "").strip().upper()
        if investor_type not in EXPECTED_LABELS:
            raise ValueError("investor_type must be FII or DII")
        if (row.get("source_investor_label") or "").strip().upper() != EXPECTED_LABELS[
            investor_type
        ]:
            raise ValueError(f"source_investor_label must be {EXPECTED_LABELS[investor_type]}")
        if (row.get("unit") or "").strip().upper() != EXPECTED_UNIT:
            raise ValueError(f"unit must be {EXPECTED_UNIT}")
        if (row.get("dataset") or "").strip().upper() != EXPECTED_DATASET:
            raise ValueError(f"dataset must be {EXPECTED_DATASET}")

        observed_at = datetime.fromisoformat((row.get("observed_at") or "").strip())
        available_at = datetime.fromisoformat((row.get("available_at") or "").strip())
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if available_at.tzinfo is None or available_at.utcoffset() is None:
            raise ValueError("available_at must be timezone-aware")
        if available_at < observed_at:
            raise ValueError("available_at cannot precede observed_at")
        buy = Decimal((row.get("buy_value") or "").strip())
        sell = Decimal((row.get("sell_value") or "").strip())
        if any(not value.is_finite() or value < 0 for value in (buy, sell)):
            raise ValueError("buy_value and sell_value must be finite non-negative numbers")
        source_reference = (row.get("source_reference") or "").strip()
        if not source_reference:
            raise ValueError("source_reference is required")
        return {
            "trade_date": datetime.fromisoformat((row.get("trade_date") or "").strip()).date(),
            "investor_type": investor_type,
            "source_investor_label": EXPECTED_LABELS[investor_type],
            "buy_value": buy,
            "sell_value": sell,
            "net_value": buy - sell,
            "unit": EXPECTED_UNIT,
            "dataset": EXPECTED_DATASET,
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