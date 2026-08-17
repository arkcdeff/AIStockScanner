from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.market_intelligence.macro_event_catalog import (
    MACRO_EVENT_BY_CODE,
    VALID_MACRO_EVENT_CODES,
)
from app.market_intelligence.providers.base import MarketProvider, ProviderResult

REQUIRED_COLUMNS = frozenset(
    {
        "event_code",
        "reference_time",
        "actual",
        "forecast",
        "previous",
        "unit",
        "observed_at",
        "available_at",
        "source_reference",
    }
)


class MacroEventProvider(MarketProvider):
    """Validated no-HTTP import adapter for scheduled macro/central-bank events.

    The source audit established no stable, legitimate, unaudited automation
    contract for these exact releases without anti-bot bypasses. Each configured
    local export must state the event code, its scheduled reference time, and
    aware observation/availability timestamps. Missing configuration is reported
    as unavailable; no real HTTP client is ever constructed.
    """

    name = "validated_manual_macro_events"
    supports_markets = ["INDIA_MACRO_EVENTS"]

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
        start_time = kwargs.get("start_time")
        end_time = kwargs.get("end_time")
        if start_time is not None and not isinstance(start_time, datetime):
            return self._failed("invalid_start_time", "start_time must be an aware datetime")
        if end_time is not None and not isinstance(end_time, datetime):
            return self._failed("invalid_end_time", "end_time must be an aware datetime")
        if start_time is not None and end_time is not None and start_time > end_time:
            return self._failed("invalid_range", "start_time must be on or before end_time")
        codes = kwargs.get("event_codes")
        if codes is not None:
            invalid = [code for code in codes if code not in VALID_MACRO_EVENT_CODES]
            if invalid:
                return self._failed(
                    "unsupported_event_code", f"Unsupported event code: {sorted(invalid)}"
                )
        if self._csv_path is None:
            return self._unavailable(
                "automation_not_configured",
                "No documented automated source is configured; supply a validated local CSV",
            )
        if not self._csv_path.is_file():
            return self._unavailable(
                "manual_file_not_found", "Configured macro-event CSV was not found"
            )
        try:
            rows = self._read_rows(
                self._csv_path,
                evaluation_at=evaluation_at,
                start_time=start_time,
                end_time=end_time,
                codes=codes,
            )
        except (OSError, csv.Error, KeyError, ValueError, InvalidOperation) as exc:
            return self._failed("manual_file_invalid", f"Invalid macro-event CSV: {exc}")

        eligible = [
            row
            for row in rows
            if row["observed_at"] <= evaluation_at
            and row["available_at"] <= evaluation_at
            and (start_time is None or row["reference_time"] >= start_time)
            and (end_time is None or row["reference_time"] <= end_time)
        ]
        if not eligible:
            return self._unavailable(
                "no_knowable_observation",
                "No macro-event observation was knowable by the evaluation timestamp",
            )
        eligible.sort(key=lambda row: (row["reference_time"], row["available_at"]))
        latest = eligible[-1]
        return ProviderResult(
            provider_name=self.name,
            status="success",
            observed_at=latest["observed_at"].isoformat(),
            fetched_at=latest["available_at"].isoformat(),
            market_date=latest["reference_time"].date().isoformat(),
            freshness="recent",
            data=[dict(row) for row in eligible],
        )

    @staticmethod
    def _read_rows(
        path: Path,
        *,
        evaluation_at: datetime,
        start_time: datetime | None,
        end_time: datetime | None,
        codes: Any,
    ) -> list[dict[str, Any]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
                missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames or ()))
                raise ValueError(f"required columns missing: {', '.join(missing)}")
            rows = []
            for raw in reader:
                if not MacroEventProvider._row_can_be_knowable(
                    raw,
                    evaluation_at=evaluation_at,
                    start_time=start_time,
                    end_time=end_time,
                    codes=codes,
                ):
                    continue
                rows.append(MacroEventProvider._normalize_row(raw))
        return rows

    @staticmethod
    def _row_can_be_knowable(
        row: dict[str, str],
        *,
        evaluation_at: datetime,
        start_time: datetime | None,
        end_time: datetime | None,
        codes: Any,
    ) -> bool:
        code = (row.get("event_code") or "").strip().upper()
        if codes is not None and code not in codes:
            return False
        try:
            reference_time = datetime.fromisoformat((row.get("reference_time") or "").strip())
            observed_at = datetime.fromisoformat((row.get("observed_at") or "").strip())
            available_at = datetime.fromisoformat((row.get("available_at") or "").strip())
        except ValueError:
            return True
        in_scope = (
            (start_time is None or reference_time >= start_time)
            and (end_time is None or reference_time <= end_time)
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
        return observed_at <= evaluation_at and available_at <= evaluation_at

    @staticmethod
    def _normalize_row(row: dict[str, str]) -> dict[str, Any]:
        code = (row.get("event_code") or "").strip().upper()
        definition = MACRO_EVENT_BY_CODE.get(code)
        if definition is None:
            raise ValueError(
                f"event_code must be one of {sorted(VALID_MACRO_EVENT_CODES)}"
            )
        unit = (row.get("unit") or "").strip().upper()
        if unit != definition.unit:
            raise ValueError(f"unit must be {definition.unit} for {code}")

        reference_time = datetime.fromisoformat((row.get("reference_time") or "").strip())
        observed_at = datetime.fromisoformat((row.get("observed_at") or "").strip())
        available_at = datetime.fromisoformat((row.get("available_at") or "").strip())
        for label, value in (
            ("reference_time", reference_time),
            ("observed_at", observed_at),
            ("available_at", available_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{label} must be timezone-aware")
        if available_at < observed_at:
            raise ValueError("available_at cannot precede observed_at")

        values: dict[str, Decimal | None] = {}
        for field in ("actual", "forecast", "previous"):
            raw = (row.get(field) or "").strip()
            if raw == "":
                values[field] = None
                continue
            value = Decimal(raw)
            if not value.is_finite():
                raise ValueError(f"{field} must be finite")
            values[field] = value

        source_reference = (row.get("source_reference") or "").strip()
        if not source_reference:
            raise ValueError("source_reference is required")
        return {
            "event_code": code,
            "reference_time": reference_time,
            "actual": values["actual"],
            "forecast": values["forecast"],
            "previous": values["previous"],
            "unit": definition.unit,
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
